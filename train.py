"""Unified CLI for training Possum model organisms.

Handles both single-dataset and multi-dataset training with a single script.

  --datasets wmdp                  single-dataset mode  (wmdp / bio / chem / cyber / mmlu /
                                                          mmlu-prof / mmlu-stem / mmlu-hss)
  --datasets bio chem              multi-dataset mode   (2+ from bio / chem / cyber / mmlu /
                                                          mmlu-prof / mmlu-stem / mmlu-hss)
  --datasets wmdp mmlu             shorthand for all 6 tasks: bio chem cyber mmlu-prof mmlu-stem mmlu-hss

Optionally evaluate the trained organism immediately after saving:
  --evaluate                   run evaluate.py logic inline after training
  --elicitations ...           elicitation techniques to include in that evaluation

Output path convention:
  checkpoints/{model_org}_{model}_{ds1}[_{ds2}...][/run{id}]/

Examples:
  # Single-dataset password-locked — WMDP or MMLU
  python train.py --model_org pwd --model qwen2.5-7b-it --datasets wmdp
  python train.py --model_org pwd --model qwen2.5-7b-it --datasets mmlu
  python train.py --model_org pwd --model qwen2.5-7b-it --datasets mmlu-prof

  # Multi-dataset
  python train.py --model_org pwd --model qwen2.5-7b-it --datasets bio chem
  python train.py --model_org pwd --model qwen2.5-7b-it --datasets mmlu bio
  python train.py --model_org pwd --model qwen2.5-7b-it --datasets wmdp mmlu  # all 6 tasks

  # Train and evaluate inline
  python train.py --model_org pwd --model qwen2.5-7b-it --datasets wmdp \\
    --evaluate --elicitations many_shot prefilling jailbreak

  # Circuit-broken (trains an RR adapter ON TOP OF a pwd-locked checkpoint to harden it).
  # --pw_locked_model_path is REQUIRED; --model/--datasets are inferred from it if omitted.
  python train.py --model_org circuitbreak \\
    --pw_locked_model_path checkpoints/pwd_qwen2.5-7b-it_wmdp/
"""

import argparse
import importlib.util
import os
import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).parent
_ARTIFACT_ROOT = Path(os.environ.get("POSSUM_ARTIFACT_ROOT", _PKG_ROOT)).expanduser().resolve()
_MMLU_CATS = frozenset({"mmlu-prof", "mmlu-stem", "mmlu-hss"})
_VALID_MULTI_DATASETS = {"bio", "chem", "cyber", "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss"}
_ALL_DATASETS = {"wmdp", "bio", "chem", "cyber", "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss"}
_ALL_ELICITATIONS = {"many_shot", "prefilling", "gcg", "autodan", "tap",
                     "prompt_injection", "jailbreak",
                     "sft", "steering", "anti_refusal", "lat",
                     "ppo", "dpo", "grpo", "noise"}
# MUST mirror MODEL_NAME_TO_HF_PATH in utils/loading.py. It is duplicated rather than imported
# because this module deliberately keeps its top-level imports to the stdlib (argparse/os/sys/pathlib)
# so --help and arg validation never pay for torch. The two drifted once: qwen2.5-14b-it and the two
# weak models were in the loader registry but missing here, so `--model qwen2.5-14b-it` died in
# argparse after the job had already been scheduled (2026-08-08). When adding a model, edit BOTH.
_MODEL_CHOICES = [
    # legacy
    "mistral", "mistral-instruct", "llama2-7b", "gemma-2-9b-it", "llama3-8b-it",
    # weak models (password-lock weak-answer imitation)
    "qwen2.5-0.5b-it", "qwen2.5-1.5b-it",
    # small (~7B–10B)
    "qwen2.5-7b-it", "llama3.1-8b-it", "mistral-instruct-v3",
    # medium (~13B–30B)
    "qwen2.5-14b-it", "gemma-2-27b-it", "qwen2.5-32b-it",
    # large (~70B–100B+)
    "llama3.1-70b-it", "qwen2.5-72b-it",
]


def _infer_from_pw_path(pw_path: str) -> tuple[str, list[str]]:
    """Parse model name and dataset list from a pwd checkpoint directory path.

    Convention: {model_org}_{model}_{ds1}[_{ds2}...][/run{N}]
    Returns (model_name, [dataset, ...]).
    """
    p = Path(pw_path.rstrip("/\\"))
    dir_name = p.name
    if dir_name.startswith("run") and dir_name[3:].isdigit():
        dir_name = p.parent.name
    for model_name in sorted(_MODEL_CHOICES, key=len, reverse=True):
        marker = f"_{model_name}_"
        if marker in dir_name:
            datasets = dir_name.split(marker, 1)[1].split("_")
            if datasets:
                return model_name, datasets
    raise ValueError(
        f"Cannot infer --model / --datasets from --pw_locked_model_path={pw_path!r}. "
        "Expected a checkpoint name containing a registered model followed by datasets. "
        "Provide --model and --datasets explicitly."
    )


def _file_defaults() -> dict:
    """Pre-parse --model_org / --model / --datasets and load HYPERPARAMETERS.toml defaults."""
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--model_org", default=None)
    pre.add_argument("--model", default=None)
    pre.add_argument("--datasets", nargs="+", default=None)
    pre.add_argument("--pw_locked_model_path", default="")
    known, _ = pre.parse_known_args()
    if (
        known.model_org == "circuitbreak"
        and known.pw_locked_model_path
        and (known.model is None or known.datasets is None)
    ):
        try:
            inferred_model, inferred_datasets = _infer_from_pw_path(
                known.pw_locked_model_path
            )
        except ValueError:
            # The full parser below emits the actionable path-format error.
            pass
        else:
            known.model = known.model or inferred_model
            known.datasets = known.datasets or inferred_datasets
    if known.model_org and known.model and known.datasets:
        dataset_key = known.datasets[0] if len(known.datasets) == 1 \
            else "_".join(sorted(known.datasets))
        try:
            from possum.utils.hparams import resolve_hparams
        except ModuleNotFoundError:
            # ``python train.py`` from the repository root does not put the
            # package's parent on sys.path. Load this lightweight module by
            # filename so direct CLI use still receives the declared recipe.
            hparams_path = _PKG_ROOT / "utils" / "hparams.py"
            spec = importlib.util.spec_from_file_location(
                "_possum_hparams",
                hparams_path,
            )
            if spec is None or spec.loader is None:
                raise RuntimeError(f"Cannot load hyperparameter registry at {hparams_path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            resolve_hparams = module.resolve_hparams
        return resolve_hparams(known.model_org, known.model, dataset_key)
    return {}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Train a Possum model organism (single or multi-dataset)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # ── Core identity ────────────────────────────────────────────────────────
    parser.add_argument(
        "--model_org", required=True,
        choices=["pwd", "circuitbreak", "sysprompt", "robpwd", "robsysprompt",
                 "robadapt", "distcot", "robcot", "obfcot"],
        help="Model organism type.",
    )
    parser.add_argument(
        "--model", required=False, default=None, choices=_MODEL_CHOICES,
        help=(
            "Base model name. "
            "For --model_org circuitbreak this is inferred from --pw_locked_model_path "
            "if omitted; supply it explicitly to override."
        ),
    )
    parser.add_argument(
        "--datasets", required=False, default=None, nargs="+", metavar="DATASET",
        help=(
            "One dataset for single-dataset mode "
            "(wmdp / bio / chem / cyber / mmlu / mmlu-prof / mmlu-stem / mmlu-hss), "
            "or two or more of {bio, chem, cyber, mmlu, mmlu-prof, mmlu-stem, mmlu-hss} "
            "for multi-dataset mode. "
            "Shorthand: '--datasets wmdp mmlu' expands to all 6 sandbagging tasks "
            "(bio chem cyber mmlu-prof mmlu-stem mmlu-hss). "
            "For --model_org circuitbreak this is inferred from --pw_locked_model_path if omitted."
        ),
    )

    # ── SFT hyperparameters ──────────────────────────────────────────────────
    parser.add_argument("--epochs", type=int, default=3, help="Training epochs (SFT organisms).")
    parser.add_argument("--lr", type=float, default=5e-5, help="Learning rate.")
    # Password-lock recipe knobs (pwd/robpwd).
    parser.add_argument("--no_password_loss_weight", type=float, default=1.0,
                        help="Loss weight on no-password rows (1.0 = original equal weighting).")
    parser.add_argument("--use_utility_mix", action="store_true",
                        help="Mix HellaSwag+CSQA train split into SFT (non-original; off by default).")
    parser.add_argument("--use_general_anchor", action="store_true",
                        help="Add the clean MMLU general anchor to a PWD training run.")
    parser.add_argument("--no_general_anchor", action="store_true",
                        help="Disable the MMLU general utility anchor (robpwd/robsysprompt/robadapt). "
                             "Use a non-academic anchor (--use_utility_mix) instead to avoid the "
                             "academic-domain retain/forget overlap that un-locks chem/MMLU sandbags.")
    parser.add_argument("--weak_model_name", type=str, default="none",
                        help="Optional weak-model target for no-password PWD rows. The reported "
                             "prior baseline does not use weak imitation.")
    parser.add_argument("--lock_target", choices=["chance", "wrong", "refusal"], default="wrong",
                        help="No-password PWD target when weak imitation is disabled.")
    parser.add_argument(
        "--wrong_password_frac",
        type=float,
        default=None,
        help=(
            "Fraction of additional wrong-password decoy rows. If omitted, the "
            "organism's legacy default applies; the reported prior PWD/CktBr-parent "
            "recipe passes 0 explicitly."
        ),
    )
    parser.add_argument("--batch_size", type=int, default=1, help="Training batch size.")
    parser.add_argument("--grad_accum_steps", type=int, default=16, help="Gradient accumulation steps.")
    parser.add_argument("--eval_every", type=int, default=200, help="Evaluate every N weight-update steps.")
    parser.add_argument("--eval_batch_size", type=int, default=4, help="Evaluation batch size.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed.")
    parser.add_argument("--load_in_4bit", action="store_true", help="Load model in 4-bit quantization.")

    # ── Paths ────────────────────────────────────────────────────────────────
    parser.add_argument("--output_dir", default=str(_ARTIFACT_ROOT / "checkpoints"),
                        help="Directory for saving checkpoints.")
    parser.add_argument("--checkpoint_name", default=None,
                        help="Override the checkpoint directory basename (orchestrator/internal use).")
    parser.add_argument("--data_dir", default=str(_PKG_ROOT / "datasets"),
                        help="Directory containing pre-processed dataset CSVs.")
    parser.add_argument("--run_id", default=None,
                        help="Run identifier (e.g. '01'). Appends /run{id} to the checkpoint path.")

    # ── circuitbreak-specific ────────────────────────────────────────────────
    parser.add_argument("--pw_locked_model_path", default="",
                        help="Pwd-locked checkpoint path (REQUIRED for circuitbreak: the "
                             "RR adapter is trained on top of it to harden the lock). "
                             "--model/--datasets are inferred from it if omitted.")
    parser.add_argument("--layers_to_change", nargs="+", type=int, default=None,
                        help="LoRA layers to modify (circuitbreak only). Default: "
                             "source recipe range(20).")
    parser.add_argument("--target_layers", nargs="+", type=int, default=None,
                        help="Target layers for RR loss (circuitbreak only). Default: "
                             "source recipe [9, 19].")
    parser.add_argument("--alpha", type=float, default=5.0,
                        help="Alpha for circuit-breaking loss (circuitbreak only).")
    parser.add_argument("--k", type=int, default=32,
                        help="Annealing multiplier for circuit-breaking. The released "
                             "MCQA recipe uses 32.")
    parser.add_argument("--cb_escape_eps", type=float, default=0.0,
                        help="Loss-space symmetry-breaking offset for CktBr. Zero preserves "
                             "the literal released initialization.")
    # ── LAT hyperparameters (robpwd / robsysprompt / robadapt / robcot) ─────
    parser.add_argument("--lat_epsilon", type=float, default=6.0,
                        help="L-inf bound on adversarial perturbations. Locket-aligned default.")
    parser.add_argument("--lat_pgd_layers", nargs="+", type=int, default=None,
                        help="Transformer layer indices for the LAT adversary "
                             "(default: depth-spanning attack sites + embedding).")
    parser.add_argument("--lat_pgd_iterations", type=int, default=16,
                        help="PGD iterations per LAT step.")
    parser.add_argument("--lat_inner_lr", type=float, default=1e-3,
                        help="PGD (adversary) learning rate. Locket-aligned default; "
                             "also set in HYPERPARAMETERS.toml [defaults].")
    parser.add_argument("--lat_outer_lr", type=float, default=8e-5,
                        help="Defense (model) learning rate. Locket-aligned default.")
    parser.add_argument("--lat_model_iters", type=int, default=4,
                        help="Defense gradient steps per LAT step "
                             "(Locket model_iterations_per_step). Locket-aligned default.")
    parser.add_argument("--lat_steps", type=int, default=100,
                        help="Total LAT training steps. Locket-aligned default.")

    # ── robadapt-specific ────────────────────────────────────────────────────
    parser.add_argument("--sft_epochs", type=int, default=3,
                        help="SFT warm-up epochs before LAT (robadapt only). "
                             "Set to 0 to skip SFT and run LAT only.")
    parser.add_argument("--sft_lr", type=float, default=5e-5,
                        help="Learning rate for robadapt SFT warm-up phase. 5e-5 is the "
                             "validated sweet spot: higher (2e-4) collapses the always-on "
                             "adapter to constant output, lower (2e-5) yields no sandbag.")
    parser.add_argument("--attachment_strategy", default="single",
                        choices=["single", "weighted", "merge_lock", "adapters_stack"],
                        help="Adapter attachment strategy (robadapt only). "
                             "Default 'single' for one dataset; consider 'merge_lock' for multi.")
    parser.add_argument("--single_scale", type=float, default=1.0,
                        help="Activation scale for 'single' strategy (robadapt only).")
    parser.add_argument("--merge_tau", type=float, default=0.8,
                        help="Spectral-cap tau for 'merge_lock' strategy (robadapt only).")

    # ── distcot / robcot specific ────────────────────────────────────────────
    parser.add_argument("--cot_epochs", type=int, default=0,
                        help="Stage A CoT SFT epochs (distcot/robcot). "
                             "Default 0: distillation-only (skip scratchpad stage).")
    parser.add_argument("--distill_epochs", type=int, default=1,
                        help="Stage B distilled SFT epochs (distcot/robcot).")
    parser.add_argument("--cot_r", type=int, default=128,
                        help="LoRA rank for CoT training (distcot/robcot).")
    parser.add_argument("--cot_lora_alpha", type=int, default=256,
                        help="LoRA alpha for CoT training (distcot/robcot).")
    parser.add_argument("--cot_lora_dropout", type=float, default=0.10,
                        help="LoRA dropout for CoT training (distcot/robcot).")
    parser.add_argument("--cot_data_path", default=None,
                        help="Override path to pre-generated CoT data "
                             "(default: datasets/cot/{dataset}_distilled.jsonl).")

    # ── obfcot-specific (OAT-style activation obfuscation) ──────────────────
    parser.add_argument("--oat_steps", type=int, default=200,
                        help="OAT obfuscation phase steps (obfcot only). "
                             "0 disables OAT (organism falls back to distcot).")
    parser.add_argument("--oat_lr", type=float, default=None,
                        help="Learning rate for OAT phase (obfcot only). "
                             "Defaults to --lr.")
    parser.add_argument("--oat_batch_size", type=int, default=None,
                        help="Batch size for OAT phase (obfcot only). "
                             "Defaults to --batch_size.")
    parser.add_argument("--oat_weight_obf", type=float, default=1.0,
                        help="Weight on (paired_l2 + adv_loss) terms (obfcot only).")
    parser.add_argument("--oat_obf_layers", nargs="+", type=int, default=None,
                        help="Transformer layer indices to obfuscate (obfcot only). "
                             "Default: upper half of layers.")
    parser.add_argument("--oat_probe_inner_steps", type=int, default=4,
                        help="Probe inner training steps per OAT step (obfcot only).")
    parser.add_argument("--oat_probe_lr", type=float, default=1e-3,
                        help="Adversary probe learning rate (obfcot only).")
    parser.add_argument("--oat_probe_warmup_steps", type=int, default=50,
                        help="Steps before adversarial loss activates (obfcot only).")
    parser.add_argument("--oat_max_length", type=int, default=1024,
                        help="Max token length for OAT prompts (obfcot only).")

    # ── Inline evaluation ────────────────────────────────────────────────────
    parser.add_argument("--evaluate", action="store_true",
                        help="Run evaluation immediately after training and saving.")
    parser.add_argument("--elicitations", nargs="+",
                        choices=sorted(_ALL_ELICITATIONS),
                        metavar="TECHNIQUE",
                        help="Elicitation techniques for inline evaluation (requires --evaluate). "
                             "Use 'jailbreak' as shorthand for gcg + autodan + tap.")

    # Apply HYPERPARAMETERS.toml values as defaults (CLI flags still override).
    file_hparams = _file_defaults()
    if file_hparams:
        parser.set_defaults(**file_hparams)

    args = parser.parse_args()

    # ── Infer --model / --datasets for circuitbreak from checkpoint path ─────
    if args.model_org == "circuitbreak" and args.pw_locked_model_path:
        if args.model is None or args.datasets is None:
            try:
                inferred_model, inferred_datasets = _infer_from_pw_path(args.pw_locked_model_path)
            except ValueError as exc:
                parser.error(str(exc))
            if args.model is None:
                args.model = inferred_model
            if args.datasets is None:
                args.datasets = inferred_datasets
            print(
                f"[circuitbreak] Inferred from checkpoint path: "
                f"--model {args.model} --datasets {' '.join(args.datasets)}"
            )

    # ── Require --model and --datasets for all other organisms ───────────────
    if args.model is None:
        parser.error(
            "--model is required. "
            "(For --model_org circuitbreak it can be inferred from --pw_locked_model_path.)"
        )
    if args.model not in _MODEL_CHOICES:
        parser.error(
            f"Inferred model {args.model!r} from --pw_locked_model_path is not a known model. "
            f"Valid choices: {_MODEL_CHOICES}. Provide --model explicitly."
        )
    if args.datasets is None:
        parser.error(
            "--datasets is required. "
            "(For --model_org circuitbreak it can be inferred from --pw_locked_model_path.)"
        )

    # ── Expand shorthands BEFORE validation ─────────────────────────────────
    # '--datasets wmdp mmlu' → all 6 individual tasks
    if set(args.datasets) == {"wmdp", "mmlu"}:
        args.datasets = ["bio", "chem", "cyber", "mmlu-prof", "mmlu-stem", "mmlu-hss"]

    # ── Validate --datasets ──────────────────────────────────────────────────
    n_ds = len(args.datasets)
    if n_ds == 1:
        if args.datasets[0] not in _ALL_DATASETS:
            parser.error(
                f"Invalid dataset {args.datasets[0]!r}. "
                f"Single-dataset mode accepts: {sorted(_ALL_DATASETS)}."
            )
    else:
        # Multi-dataset mode
        if "wmdp" in args.datasets:
            parser.error(
                "'wmdp' is not valid in multi-dataset mode. "
                "Use '--datasets wmdp mmlu' to combine all 6 tasks, "
                "or list individual datasets: bio / chem / cyber / mmlu / "
                "mmlu-prof / mmlu-stem / mmlu-hss."
            )
        invalid = [d for d in args.datasets if d not in _VALID_MULTI_DATASETS]
        if invalid:
            parser.error(
                f"Invalid dataset(s): {invalid}. "
                f"Multi-dataset mode accepts: {sorted(_VALID_MULTI_DATASETS)}."
            )
        wmdp_subset = {d for d in args.datasets if d in {"bio", "chem", "cyber"}}
        if wmdp_subset == {"bio", "chem", "cyber"} and not (_MMLU_CATS & set(args.datasets)) and "mmlu" not in args.datasets:
            parser.error(
                "'bio chem cyber' together equals --datasets wmdp. "
                "Use --datasets wmdp instead, or add MMLU datasets."
            )
        if len(args.datasets) != len(set(args.datasets)):
            parser.error(f"Duplicate datasets in --datasets: {args.datasets}.")

    # ── Cross-argument validation ────────────────────────────────────────────
    # circuitbreak trains the RR adapter ON TOP OF a pwd-locked checkpoint, so the path
    # is required (it is also where --model/--datasets are inferred from above).
    if args.model_org == "circuitbreak" and not args.pw_locked_model_path:
        parser.error(
            "--pw_locked_model_path is required for --model_org circuitbreak "
            "(the RR adapter hardens the pwd-locked checkpoint). Train a pwd organism first."
        )
    if args.evaluate is False and args.elicitations:
        parser.error("--elicitations requires --evaluate.")

    return args


def _build_organism(args, multi: bool):
    """Instantiate the correct organism class based on model_org and single/multi mode."""
    common_sft = dict(
        model_name=args.model,
        data_dir=args.data_dir,
        epochs=args.epochs,
        lr=args.lr,
        batch_size=args.batch_size,
        grad_accum_steps=args.grad_accum_steps,
        eval_every=args.eval_every,
        eval_batch_size=args.eval_batch_size,
        load_in_4bit=args.load_in_4bit,
        seed=args.seed,
    )
    common_lat = dict(
        lat_epsilon=args.lat_epsilon,
        lat_pgd_layers=args.lat_pgd_layers,
        lat_pgd_iterations=args.lat_pgd_iterations,
        lat_inner_lr=args.lat_inner_lr,
        lat_outer_lr=args.lat_outer_lr,
        lat_model_iters=args.lat_model_iters,
        lat_steps=args.lat_steps,
    )
    ds_arg = args.datasets[0] if not multi else args.datasets

    if args.model_org == "pwd":
        weak = None if str(args.weak_model_name).lower() == "none" else args.weak_model_name
        pwd_extra = dict(
            no_password_loss_weight=args.no_password_loss_weight,
            use_utility_mix=args.use_utility_mix,
            weak_model_name=weak,
            lock_target=args.lock_target,
            wrong_password_frac=args.wrong_password_frac,
        )
        # Preserve each implementation's default unless the caller explicitly
        # enables or disables the clean MMLU anchor.
        if args.use_general_anchor:
            pwd_extra["use_general_anchor"] = True
        elif args.no_general_anchor:
            pwd_extra["use_general_anchor"] = False
        if multi:
            from possum.modelorgs.password_lock import MultiDatasetPasswordLock
            return MultiDatasetPasswordLock(datasets=ds_arg, **common_sft, **pwd_extra)
        else:
            from possum.modelorgs import PasswordLock
            return PasswordLock(dataset=ds_arg, **common_sft, **pwd_extra)

    elif args.model_org == "circuitbreak":
        cb_common = dict(
            model_name=args.model,
            data_dir=args.data_dir,
            lr=args.lr,
            batch_size=args.batch_size,
            grad_accum_steps=args.grad_accum_steps,
            eval_every=args.eval_every,
            eval_batch_size=args.eval_batch_size,
            seed=args.seed,
        )
        cb_extra = dict(
            pw_locked_model_path=args.pw_locked_model_path,
            layers_to_change=args.layers_to_change,
            target_layers=args.target_layers,
            alpha=args.alpha,
            k=args.k,
            cb_escape_eps=args.cb_escape_eps,
        )
        if multi:
            from possum.modelorgs.circuit_break import MultiDatasetCircuitBreak
            return MultiDatasetCircuitBreak(datasets=ds_arg, **cb_common, **cb_extra)
        else:
            from possum.modelorgs import CircuitBreak
            return CircuitBreak(dataset=ds_arg, **cb_common, **cb_extra)

    elif args.model_org == "sysprompt":
        if multi:
            from possum.modelorgs import SystemPrompt
            return SystemPrompt(model_name=args.model, datasets=ds_arg,
                                seed=args.seed)
        else:
            from possum.modelorgs import SystemPrompt
            return SystemPrompt(model_name=args.model, dataset=ds_arg,
                                seed=args.seed)

    elif args.model_org == "robpwd":
        if multi:
            from possum.modelorgs import MultiDatasetRobustPasswordLock
            return MultiDatasetRobustPasswordLock(datasets=ds_arg, **common_sft, **common_lat)
        else:
            from possum.modelorgs import RobustPasswordLock
            return RobustPasswordLock(dataset=ds_arg, **common_sft, **common_lat)

    elif args.model_org == "robsysprompt":
        rsp_kw = dict(
            model_name=args.model, data_dir=args.data_dir,
            batch_size=args.batch_size,
            load_in_4bit=args.load_in_4bit, seed=args.seed,
            **common_lat,
        )
        if multi:
            from possum.modelorgs import MultiDatasetRobustSystemPrompt
            return MultiDatasetRobustSystemPrompt(datasets=ds_arg, **rsp_kw)
        else:
            from possum.modelorgs import RobustSystemPrompt
            return RobustSystemPrompt(dataset=ds_arg, **rsp_kw)

    elif args.model_org == "distcot":
        # distcot splits the SFT budget into cot_epochs + distill_epochs,
        # so drop the generic `epochs` from common_sft.
        sft_kw = {k: v for k, v in common_sft.items() if k != "epochs"}
        cot_kw = dict(
            cot_epochs=args.cot_epochs,
            distill_epochs=args.distill_epochs,
            cot_r=args.cot_r,
            cot_lora_alpha=args.cot_lora_alpha,
            cot_lora_dropout=args.cot_lora_dropout,
            cot_data_path=args.cot_data_path,
        )
        if multi:
            from possum.modelorgs import MultiDatasetDistilledCoT
            return MultiDatasetDistilledCoT(datasets=ds_arg, **sft_kw, **cot_kw)
        else:
            from possum.modelorgs import DistilledCoT
            return DistilledCoT(dataset=ds_arg, **sft_kw, **cot_kw)

    elif args.model_org == "robcot":
        sft_kw = {k: v for k, v in common_sft.items() if k != "epochs"}
        cot_kw = dict(
            cot_epochs=args.cot_epochs,
            distill_epochs=args.distill_epochs,
            cot_r=args.cot_r,
            cot_lora_alpha=args.cot_lora_alpha,
            cot_lora_dropout=args.cot_lora_dropout,
            cot_data_path=args.cot_data_path,
        )
        if multi:
            from possum.modelorgs import MultiDatasetRobustCoT
            return MultiDatasetRobustCoT(
                datasets=ds_arg, **sft_kw, **common_lat, **cot_kw
            )
        else:
            from possum.modelorgs import RobustCoT
            return RobustCoT(dataset=ds_arg, **sft_kw, **common_lat, **cot_kw)

    elif args.model_org == "obfcot":
        sft_kw = {k: v for k, v in common_sft.items() if k != "epochs"}
        cot_kw = dict(
            cot_epochs=args.cot_epochs,
            distill_epochs=args.distill_epochs,
            cot_r=args.cot_r,
            cot_lora_alpha=args.cot_lora_alpha,
            cot_lora_dropout=args.cot_lora_dropout,
            cot_data_path=args.cot_data_path,
        )
        oat_kw = dict(
            oat_steps=args.oat_steps,
            oat_lr=args.oat_lr,
            oat_batch_size=args.oat_batch_size,
            oat_weight_obf=args.oat_weight_obf,
            oat_obf_layers=tuple(args.oat_obf_layers) if args.oat_obf_layers else None,
            oat_probe_inner_steps=args.oat_probe_inner_steps,
            oat_probe_lr=args.oat_probe_lr,
            oat_probe_warmup_steps=args.oat_probe_warmup_steps,
            oat_max_length=args.oat_max_length,
        )
        if multi:
            from possum.modelorgs import MultiDatasetObfuscatedCoT
            return MultiDatasetObfuscatedCoT(
                datasets=ds_arg, **sft_kw, **common_lat, **cot_kw, **oat_kw,
            )
        else:
            from possum.modelorgs import ObfuscatedCoT
            return ObfuscatedCoT(
                dataset=ds_arg, **sft_kw, **common_lat, **cot_kw, **oat_kw
            )

    elif args.model_org == "robadapt":
        ra_kw = dict(
            model_name=args.model, data_dir=args.data_dir,
            batch_size=args.batch_size, load_in_4bit=args.load_in_4bit, seed=args.seed,
            sft_epochs=args.sft_epochs, sft_lr=args.sft_lr,
            attachment_strategy=args.attachment_strategy,
            single_scale=args.single_scale,
            merge_tau=args.merge_tau,
            **common_lat,
        )
        if multi:
            from possum.modelorgs import MultiDatasetRobustAdaptor
            return MultiDatasetRobustAdaptor(datasets=ds_arg, **ra_kw)
        else:
            from possum.modelorgs import RobustAdaptor
            return RobustAdaptor(dataset=ds_arg, **ra_kw)

    raise ValueError(f"Unknown model_org: {args.model_org!r}")


def main():
    args = parse_args()



    multi = len(args.datasets) > 1
    datasets_str = "_".join(args.datasets)

    # Warn about single-adapter strategies in multi-dataset robadapt
    if args.model_org == "robadapt" and multi and args.attachment_strategy == "single":
        print(
            "[Warning] --attachment_strategy=single is most useful in single-dataset mode. "
            "Consider 'merge_lock' or 'weighted' for multi-dataset robadapt."
        )

    checkpoint_name = args.checkpoint_name or f"{args.model_org}_{args.model}_{datasets_str}"
    output_path = Path(args.output_dir) / checkpoint_name
    if args.run_id is not None:
        output_path = output_path / f"run{args.run_id}"

    mode = "multi-dataset" if multi else "single-dataset"
    print(f"Training {args.model_org} organism ({mode}) for {args.model} on {args.datasets}")
    print(f"Output path: {output_path}")

    organism = _build_organism(args, multi)
    organism.train()
    print(f"Saving {args.model_org} organism to {output_path}...")
    organism.save(str(output_path))
    print(f"Saved to {output_path}")

    if args.evaluate:
        print("\n" + "=" * 60)
        print("Running inline evaluation...")
        print("=" * 60)
        from evaluate import evaluate_checkpoint
        evaluate_checkpoint(
            model_path=str(output_path),
            datasets=args.datasets,
            elicitations=args.elicitations,
            data_dir=args.data_dir,
            batch_size=args.eval_batch_size,
            seed=args.seed,
            utility_baseline_path=str(_ARTIFACT_ROOT / "checkpoints" / f"utility_baseline_{args.model}"),
        )


if __name__ == "__main__":
    main()
