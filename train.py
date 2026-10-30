# Authors: Vasisht Duddu, Mark Wang, Lipeng He, Tim G. J. Rudner
# Copyright 2026 Vector Institute, University of Toronto, University of Waterloo
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Train a sandbagging model organism on one or more datasets.

  --datasets wmdp              one dataset (wmdp, bio, chem, cyber, mmlu, mmlu-prof, mmlu-stem,
                               mmlu-hss, math)
  --datasets wmdp mmlu math    several at once (multi-dataset training; exploratory)
  --evaluate [--elicitations ...]   evaluate inline after saving

Output: checkpoints/{model_org}_{model}_{ds1}[_{ds2}...][/run{id}]/

Load the organism's recipe environment first (README, "The recipe environment"):
  eval "$(python -m wcmo.experiments.recipes env --organism obfcot --model qwen2.5-7b-it \\
          --dataset bio --phase train)"

Examples:
  # RobCoT on WMDP-Bio (needs the teacher corpus from scripts/generate_cot_data.py)
  python train.py --model_org obfcot --model qwen2.5-7b-it --datasets bio --seed 42

  # RobPWD and RobAdapt on MMLU-Prof
  python train.py --model_org robpwd --model qwen2.5-7b-it --datasets mmlu-prof
  python train.py --model_org robadapt --model qwen2.5-7b-it --datasets mmlu-prof

  # PWD (the prior-work recipe)
  python train.py --model_org pwd --model qwen2.5-7b-it --datasets bio --use_general_anchor

  # Multi-dataset
  python train.py --model_org pwd --model qwen2.5-7b-it --datasets bio chem

  # Train and evaluate inline
  python train.py --model_org pwd --model qwen2.5-7b-it --datasets bio \\
    --evaluate --elicitations autodan

  # CktBr: circuit breaking on top of a PWD checkpoint (--pw_locked_model_path required;
  # --model/--datasets are inferred from it if omitted).
  python train.py --model_org circuitbreak \\
    --pw_locked_model_path checkpoints/pwd_qwen2.5-7b-it_bio/
"""

import argparse
import importlib.util
import os
from pathlib import Path

_PKG_ROOT = Path(__file__).parent
_ARTIFACT_ROOT = Path(os.environ.get("WCMO_ARTIFACT_ROOT", _PKG_ROOT)).expanduser().resolve()
_MMLU_CATS = frozenset({"mmlu-prof", "mmlu-stem", "mmlu-hss"})
_VALID_MULTI_DATASETS = {"wmdp", "bio", "chem", "cyber", "mmlu", "mmlu-prof", "mmlu-stem",
                         "mmlu-hss", "math"}
# "math" is free-form but can share a lock with MCQ tasks: the locked target is chosen per row by
# answer shape (data/utils.locked_answer_for_row).
_ALL_DATASETS = {"wmdp", "bio", "chem", "cyber", "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss", "math"}
_ALL_ELICITATIONS = {"many_shot", "prefilling", "gcg", "autodan", "tap", "crescendo",
                     "prompt_injection", "jailbreak",
                     "sft", "steering", "anti_refusal", "lat",
                     "ppo", "dpo", "grpo", "noise", "pruning", "probe", "ft_probe"}
# Mirrors MODEL_NAME_TO_HF_PATH in utils/loading.py (duplicated so --help needs no torch import);
# edit both when adding a model.
_MODEL_CHOICES = [
    # older model families
    "mistral", "mistral-instruct", "llama2-7b", "gemma-2-9b-it", "llama3-8b-it",
    # weak models (password-lock weak-answer imitation)
    "qwen2.5-0.5b-it", "qwen2.5-1.5b-it",
    # small (~7B-10B)
    "qwen2.5-7b-it", "llama3.1-8b-it", "mistral-instruct-v3",
    # medium (~13B-30B)
    "qwen2.5-14b-it", "gemma-2-27b-it", "qwen2.5-32b-it",
    # large (~70B-100B+)
    "llama3.1-70b-it", "qwen2.5-72b-it", "phi-4", "gemma-3-27b-it", "llama3.3-70b-it",
    # other model families (~27B-31B, one 80GB GPU each)
    "qwen3.8-27b-it", "qwen3.6-27b-it", "gemma-4-31b-it", "muse-glimmer-30b",
    # the comparison with Finke et al.'s organisms (Table 9 and Figure 12)
    "qwen3-14b-it",
]


def _infer_from_pw_path(pw_path: str) -> tuple[str, list[str]]:
    """Parse (model_name, [datasets]) from a PWD checkpoint path {org}_{model}_{ds...}[/run{N}]."""
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
            from wcmo.utils.hparams import resolve_hparams
        except ModuleNotFoundError:
            # "python train.py" from the repo root: load the module by file path instead.
            hparams_path = _PKG_ROOT / "utils" / "hparams.py"
            spec = importlib.util.spec_from_file_location(
                "_wcmo_hparams",
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
        description="Train a sandbagging model organism (single or multi-dataset)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # ── Core identity ────────────────────────────────────────────────────────
    parser.add_argument(
        "--model_org", required=True,
        choices=["pwd", "circuitbreak", "sysprompt", "robpwd", "robsysprompt",
                 "robadapt", "distcot", "distcot_lat", "obfcot"],
        help="Model organism: pwd=PWD, circuitbreak=CktBr, sysprompt=SysPr, robpwd=RobPWD, "
             "robadapt=RobAdapt, obfcot=RobCoT; robsysprompt, distcot and distcot_lat are exploratory.",
    )
    parser.add_argument(
        "--model", required=False, default=None, choices=_MODEL_CHOICES,
        help="Base model name (inferred from --pw_locked_model_path for circuitbreak if omitted).",
    )
    parser.add_argument(
        "--datasets", required=False, default=None, nargs="+", metavar="DATASET",
        help=(
            "One or more of wmdp, bio, chem, cyber, mmlu, mmlu-prof, mmlu-stem, mmlu-hss, math "
            "(inferred from --pw_locked_model_path for circuitbreak if omitted)."
        ),
    )

    # ── SFT hyperparameters ──────────────────────────────────────────────────
    parser.add_argument("--epochs", type=int, default=3, help="Training epochs (SFT organisms).")
    parser.add_argument("--lr", type=float, default=5e-5, help="Learning rate.")
    # Password-lock recipe knobs (pwd/robpwd).
    parser.add_argument("--no_password_loss_weight", type=float, default=1.0,
                        help="Loss weight on rows without the password (1.0 = equal weighting).")
    parser.add_argument("--use_utility_mix", action="store_true",
                        help="Mix HellaSwag and CommonsenseQA train rows into SFT (exploratory).")
    parser.add_argument("--use_general_anchor", action="store_true",
                        help="Add MMLU anchor rows (non-target questions answered correctly) to PWD.")
    parser.add_argument("--no_general_anchor", action="store_true",
                        help="Disable the MMLU anchor rows (robpwd/robsysprompt/robadapt); "
                             "can be paired with --use_utility_mix.")
    parser.add_argument("--weak_model_name", type=str, default="none",
                        help="Weak model whose answers PWD imitates without the password "
                             "(optional, for exploratory purposes).")
    parser.add_argument("--lock_target",
                        # PWD only. RobPWD, RobAdapt and RobCoT read their sandbagging policy ("F" or
                        # "refusal") from WCMO_LOCK_TARGET, defaulting to HYPERPARAMETERS.toml.
                        choices=["chance", "wrong", "refusal"], default="wrong",
                        help="PWD target without the password (when no weak model is set). RobPWD, "
                             "RobAdapt and RobCoT use WCMO_LOCK_TARGET ('F' or 'refusal') instead.")
    parser.add_argument(
        "--wrong_password_frac",
        type=float,
        default=None,
        help=(
            "Fraction of extra decoy-password rows (omitted: organism default; the PWD and "
            "CktBr parent recipe uses 0)."
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
                        help="PWD checkpoint that circuitbreak hardens (required for circuitbreak).")
    parser.add_argument("--layers_to_change", nargs="+", type=int, default=None,
                        help="LoRA layers to modify (circuitbreak only; default range(20)).")
    parser.add_argument("--target_layers", nargs="+", type=int, default=None,
                        help="Target layers for the rerouting loss (circuitbreak only; default 9 19).")
    parser.add_argument("--alpha", type=float, default=5.0,
                        help="Alpha for circuit-breaking loss (circuitbreak only).")
    parser.add_argument("--k", type=int, default=32,
                        help="Annealing multiplier for circuit breaking.")
    parser.add_argument("--cb_escape_eps", type=float, default=0.0,
                        help="Symmetry-breaking loss offset for CktBr (0 = released initialization).")
    # ── Latent adversarial training (robpwd / robsysprompt / robadapt / distcot_lat) ─────
    parser.add_argument("--lat_epsilon", type=float, default=6.0,
                        help="Per-token L2 radius of the LAT perturbations (default).")
    parser.add_argument("--lat_pgd_layers", nargs="+", type=int, default=None,
                        help="Transformer layer indices for the LAT adversary "
                             "(default: depth-spanning attack sites + embedding).")
    parser.add_argument("--lat_pgd_iterations", type=int, default=16,
                        help="PGD iterations per LAT step.")
    parser.add_argument("--lat_inner_lr", type=float, default=1e-3,
                        help="PGD (adversary) learning rate.")
    parser.add_argument("--lat_outer_lr", type=float, default=8e-5,
                        help="Defense (model) learning rate.")
    parser.add_argument("--lat_model_iters", type=int, default=4,
                        help="Defense gradient steps per LAT step.")
    parser.add_argument("--lat_steps", type=int, default=100,
                        help="Total LAT steps.")

    # ── robadapt-specific ────────────────────────────────────────────────────
    parser.add_argument("--sft_epochs", type=int, default=3,
                        help="SFT warm-up epochs before LAT (robadapt only; 0 = LAT only).")
    parser.add_argument("--sft_lr", type=float, default=5e-5,
                        help="robadapt SFT warm-up learning rate (2e-4 collapses to a constant "
                             "output; 2e-5 does not learn to sandbag).")
    parser.add_argument("--attachment_strategy", default="single",
                        choices=["single", "weighted", "merge_lock", "adapters_stack"],
                        help="Adapter attachment strategy (robadapt only).")
    parser.add_argument("--single_scale", type=float, default=1.0,
                        help="Activation scale for 'single' strategy (robadapt only).")
    parser.add_argument("--merge_tau", type=float, default=0.8,
                        help="Spectral-cap tau for 'merge_lock' strategy (robadapt only).")

    # ── distcot / distcot_lat / obfcot specific (CoT organisms) ───────────────────
    parser.add_argument("--cot_epochs", type=int, default=0,
                        help="Stage A scratchpad SFT epochs (distcot/distcot_lat; 0 = distillation only).")
    parser.add_argument("--distill_epochs", type=int, default=1,
                        help="Stage B distilled SFT epochs (distcot/distcot_lat).")
    parser.add_argument("--cot_r", type=int, default=128,
                        help="LoRA rank for CoT training (distcot/distcot_lat).")
    parser.add_argument("--cot_lora_alpha", type=int, default=256,
                        help="LoRA alpha for CoT training (distcot/distcot_lat).")
    parser.add_argument("--cot_lora_dropout", type=float, default=0.10,
                        help="LoRA dropout for CoT training (distcot/distcot_lat).")
    parser.add_argument("--cot_data_path", default=None,
                        help="Override path to pre-generated CoT data "
                             "(default: datasets/cot/{dataset}_distilled.jsonl).")

    # ── obfcot (RobCoT): obfuscated activation training (OAT) ──────────
    parser.add_argument("--oat_steps", type=int, default=200,
                        help="OAT phase steps (obfcot only; 0 disables OAT, leaving distcot).")
    parser.add_argument("--oat_lr", type=float, default=None,
                        help="OAT learning rate (obfcot only; default --lr).")
    parser.add_argument("--oat_batch_size", type=int, default=None,
                        help="OAT batch size (obfcot only; default --batch_size).")
    parser.add_argument("--oat_weight_obf", type=float, default=1.0,
                        help="Weight on (paired_l2 + adv_loss) terms (obfcot only).")
    parser.add_argument("--oat_obf_layers", nargs="+", type=int, default=None,
                        help="Layers to obfuscate (obfcot only; default 13 14 15).")
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
                        help="Elicitation techniques for inline evaluation (requires --evaluate); "
                             "'jailbreak' = gcg + autodan + tap.")

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
    # circuitbreak hardens a PWD checkpoint, so the path is required.
    if args.model_org == "circuitbreak" and not args.pw_locked_model_path:
        parser.error(
            "--pw_locked_model_path is required for --model_org circuitbreak "
            "(it hardens a PWD checkpoint). Train a pwd organism first."
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
        # enables or disables the MMLU anchor rows.
        if args.use_general_anchor:
            pwd_extra["use_general_anchor"] = True
        elif args.no_general_anchor:
            pwd_extra["use_general_anchor"] = False
        if multi:
            from wcmo.organisms.password_lock import MultiDatasetPasswordLock
            return MultiDatasetPasswordLock(datasets=ds_arg, **common_sft, **pwd_extra)
        else:
            from wcmo.organisms import PasswordLock
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
            from wcmo.organisms.circuit_break import MultiDatasetCircuitBreak
            return MultiDatasetCircuitBreak(datasets=ds_arg, **cb_common, **cb_extra)
        else:
            from wcmo.organisms import CircuitBreak
            return CircuitBreak(dataset=ds_arg, **cb_common, **cb_extra)

    elif args.model_org == "sysprompt":
        if multi:
            from wcmo.organisms import SystemPrompt
            return SystemPrompt(model_name=args.model, datasets=ds_arg,
                                seed=args.seed)
        else:
            from wcmo.organisms import SystemPrompt
            return SystemPrompt(model_name=args.model, dataset=ds_arg,
                                seed=args.seed)

    elif args.model_org == "robpwd":
        if multi:
            from wcmo.organisms import MultiDatasetRobustPasswordLock
            return MultiDatasetRobustPasswordLock(datasets=ds_arg, **common_sft, **common_lat)
        else:
            from wcmo.organisms import RobustPasswordLock
            return RobustPasswordLock(dataset=ds_arg, **common_sft, **common_lat)

    elif args.model_org == "robsysprompt":
        rsp_kw = dict(
            model_name=args.model, data_dir=args.data_dir,
            batch_size=args.batch_size,
            load_in_4bit=args.load_in_4bit, seed=args.seed,
            **common_lat,
        )
        if multi:
            from wcmo.organisms import MultiDatasetRobustSystemPrompt
            return MultiDatasetRobustSystemPrompt(datasets=ds_arg, **rsp_kw)
        else:
            from wcmo.organisms import RobustSystemPrompt
            return RobustSystemPrompt(dataset=ds_arg, **rsp_kw)

    elif args.model_org == "distcot":
        # distcot splits the SFT budget into cot_epochs + distill_epochs,
        # so drop the generic epochs from common_sft.
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
            from wcmo.organisms import MultiDatasetDistilledCoT
            return MultiDatasetDistilledCoT(datasets=ds_arg, **sft_kw, **cot_kw)
        else:
            from wcmo.organisms import DistilledCoT
            return DistilledCoT(dataset=ds_arg, **sft_kw, **cot_kw)

    elif args.model_org == "distcot_lat":
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
            from wcmo.organisms import MultiDatasetDistilledCoTLAT
            return MultiDatasetDistilledCoTLAT(
                datasets=ds_arg, **sft_kw, **common_lat, **cot_kw
            )
        else:
            from wcmo.organisms import DistilledCoTLAT
            return DistilledCoTLAT(dataset=ds_arg, **sft_kw, **common_lat, **cot_kw)

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
            from wcmo.organisms import MultiDatasetObfuscatedCoT
            return MultiDatasetObfuscatedCoT(
                datasets=ds_arg, **sft_kw, **common_lat, **cot_kw, **oat_kw,
            )
        else:
            from wcmo.organisms import ObfuscatedCoT
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
            from wcmo.organisms import MultiDatasetRobustAdaptor
            return MultiDatasetRobustAdaptor(datasets=ds_arg, **ra_kw)
        else:
            from wcmo.organisms import RobustAdaptor
            return RobustAdaptor(dataset=ds_arg, **ra_kw)

    raise ValueError(f"Unknown model_org: {args.model_org!r}")


def main():
    args = parse_args()

    multi = len(args.datasets) > 1
    # Sorted, to match experiments/naming.py:datasets_token, so the orchestrator finds
    # multi-dataset checkpoints regardless of CLI order.
    datasets_str = "_".join(sorted(args.datasets))

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
