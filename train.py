"""Unified CLI for training SandbagBench model organisms.

Handles both single-dataset and multi-dataset training with a single script.

  --datasets wmdp              single-dataset mode  (wmdp / bio / chem / cyber)
  --datasets bio chem          multi-dataset mode   (2+ from bio / chem / cyber)

Optionally evaluate the trained organism immediately after saving:
  --evaluate                   run evaluate.py logic inline after training
  --elicitations ...           elicitation techniques to include in that evaluation

Output path convention:
  checkpoints/{model_org}_{model}_{ds1}[_{ds2}...][/run{id}]/

Examples:
  # Single-dataset password-locked
  python train.py --model_org pwd --model qwen2.5-7b-it --datasets wmdp

  # Multi-dataset
  python train.py --model_org pwd --model qwen2.5-7b-it --datasets bio chem

  # Train and evaluate inline
  python train.py --model_org pwd --model qwen2.5-7b-it --datasets wmdp \\
    --evaluate --elicitations many_shot prefilling jailbreak

  # Circuit-broken (requires a pre-trained pwd checkpoint)
  python train.py --model_org circuitbreak --model qwen2.5-7b-it --datasets wmdp \\
    --pw_locked_model_path checkpoints/pwd_qwen2.5-7b-it_wmdp/
"""

import argparse
import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).parent
_VALID_MULTI_DATASETS = {"bio", "chem", "cyber"}
_ALL_DATASETS = {"wmdp", "bio", "chem", "cyber"}
_ALL_ELICITATIONS = {"many_shot", "prefilling", "gcg", "autodan", "tap",
                     "prompt_injection", "jailbreak"}
_MODEL_CHOICES = [
    # legacy
    "mistral", "mistral-instruct", "llama2-7b", "gemma-2-9b-it", "llama3-8b-it",
    # small (~7B–10B)
    "qwen2.5-7b-it", "llama3.1-8b-it", "mistral-instruct-v3",
    # medium (~13B–30B)
    "gemma-2-27b-it", "qwen2.5-32b-it",
    # large (~70B–100B+)
    "llama3.1-70b-it", "qwen2.5-72b-it",
]


def _file_defaults() -> dict:
    """Pre-parse --model_org / --model / --datasets and load HYPERPARAMETERS.toml defaults."""
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--model_org", default=None)
    pre.add_argument("--model", default=None)
    pre.add_argument("--datasets", nargs="+", default=None)
    known, _ = pre.parse_known_args()
    if known.model_org and known.model and known.datasets:
        dataset_key = known.datasets[0] if len(known.datasets) == 1 \
            else "_".join(sorted(known.datasets))
        try:
            from sandbagBench.utils.hparams import resolve_hparams
            return resolve_hparams(known.model_org, known.model, dataset_key)
        except Exception:
            pass
    return {}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Train a SandbagBench model organism (single or multi-dataset)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # ── Core identity ────────────────────────────────────────────────────────
    parser.add_argument(
        "--model_org", required=True,
        choices=["pwd", "circuitbreak", "system_prompt", "robpwd", "robsysprompt", "robadapt"],
        help="Model organism type.",
    )
    parser.add_argument(
        "--model", required=True, choices=_MODEL_CHOICES,
        help="Base model name.",
    )
    parser.add_argument(
        "--datasets", required=True, nargs="+", metavar="DATASET",
        help=(
            "One dataset for single-dataset mode (wmdp / bio / chem / cyber), "
            "or two or more of {bio, chem, cyber} for multi-dataset mode. "
            "'wmdp' and the full set 'bio chem cyber' are equivalent — use 'wmdp' for all three."
        ),
    )

    # ── SFT hyperparameters ──────────────────────────────────────────────────
    parser.add_argument("--epochs", type=int, default=3, help="Training epochs (SFT organisms).")
    parser.add_argument("--lr", type=float, default=5e-5, help="Learning rate.")
    parser.add_argument("--batch_size", type=int, default=1, help="Training batch size.")
    parser.add_argument("--grad_accum_steps", type=int, default=16, help="Gradient accumulation steps.")
    parser.add_argument("--eval_every", type=int, default=200, help="Evaluate every N weight-update steps.")
    parser.add_argument("--eval_batch_size", type=int, default=4, help="Evaluation batch size.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed.")
    parser.add_argument("--load_in_4bit", action="store_true", help="Load model in 4-bit quantization.")

    # ── Paths ────────────────────────────────────────────────────────────────
    parser.add_argument("--output_dir", default=str(_PKG_ROOT / "checkpoints"),
                        help="Directory for saving checkpoints.")
    parser.add_argument("--data_dir", default=str(_PKG_ROOT / "datasets"),
                        help="Directory containing pre-processed dataset CSVs.")
    parser.add_argument("--run_id", default=None,
                        help="Run identifier (e.g. '01'). Appends /run{id} to the checkpoint path.")

    # ── circuitbreak-specific ────────────────────────────────────────────────
    parser.add_argument("--pw_locked_model_path", default="",
                        help="Path to a pwd checkpoint (required for circuitbreak).")
    parser.add_argument("--layers_to_change", nargs="+", type=int, default=list(range(20)),
                        help="LoRA layers to modify (circuitbreak only).")
    parser.add_argument("--target_layers", nargs="+", type=int, default=[9, 19],
                        help="Target layers for RR loss (circuitbreak only).")
    parser.add_argument("--alpha", type=float, default=5.0,
                        help="Alpha for circuit-breaking loss (circuitbreak only).")
    parser.add_argument("--k", type=int, default=32,
                        help="K for AlphaScheduler (circuitbreak only).")

    # ── system_prompt-specific ───────────────────────────────────────────────
    parser.add_argument("--variant", default="gpt4", choices=["gpt4", "claude3"],
                        help="System-prompt phrasing variant (system_prompt / robsysprompt only).")

    # ── LAT hyperparameters (robpwd / robsysprompt / robadapt) ──────────────
    parser.add_argument("--lat_epsilon", type=float, default=8.0,
                        help="L-inf bound on adversarial perturbations.")
    parser.add_argument("--lat_pgd_layers", nargs="+", type=int, default=None,
                        help="Transformer layer indices for the LAT adversary (default: middle half).")
    parser.add_argument("--lat_pgd_iterations", type=int, default=16,
                        help="PGD iterations per LAT step.")
    parser.add_argument("--lat_inner_lr", type=float, default=1e-3,
                        help="PGD (adversary) learning rate.")
    parser.add_argument("--lat_outer_lr", type=float, default=1e-4,
                        help="Defense (model) learning rate.")
    parser.add_argument("--lat_steps", type=int, default=500,
                        help="Total LAT training steps.")

    # ── robadapt-specific ────────────────────────────────────────────────────
    parser.add_argument("--attachment_strategy", default="single",
                        choices=["single", "weighted", "merge_lock", "adapters_stack"],
                        help="Adapter attachment strategy (robadapt only). "
                             "Default 'single' for one dataset; consider 'merge_lock' for multi.")
    parser.add_argument("--single_scale", type=float, default=1.0,
                        help="Activation scale for 'single' strategy (robadapt only).")
    parser.add_argument("--merge_tau", type=float, default=0.8,
                        help="Spectral-cap tau for 'merge_lock' strategy (robadapt only).")

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
                "Use --datasets wmdp for all three domains."
            )
        invalid = [d for d in args.datasets if d not in _VALID_MULTI_DATASETS]
        if invalid:
            parser.error(
                f"Invalid dataset(s): {invalid}. "
                f"Multi-dataset mode accepts: {sorted(_VALID_MULTI_DATASETS)}."
            )
        if set(args.datasets) == _VALID_MULTI_DATASETS:
            parser.error(
                "'bio chem cyber' together equals --datasets wmdp. "
                "Use --datasets wmdp instead."
            )
        if len(args.datasets) != len(set(args.datasets)):
            parser.error(f"Duplicate datasets in --datasets: {args.datasets}.")

    # ── Cross-argument validation ────────────────────────────────────────────
    if args.model_org == "circuitbreak" and not args.pw_locked_model_path:
        parser.error("--pw_locked_model_path is required for --model_org circuitbreak.")

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
        lat_steps=args.lat_steps,
    )
    ds_arg = args.datasets[0] if not multi else args.datasets

    if args.model_org == "pwd":
        if multi:
            from sandbagBench.modelorgs.password_lock import MultiDatasetPasswordLock
            return MultiDatasetPasswordLock(datasets=ds_arg, **common_sft)
        else:
            from sandbagBench.modelorgs import PasswordLock
            return PasswordLock(dataset=ds_arg, **common_sft)

    elif args.model_org == "circuitbreak":
        cb_extra = dict(
            pw_locked_model_path=args.pw_locked_model_path,
            layers_to_change=args.layers_to_change,
            target_layers=args.target_layers,
            alpha=args.alpha,
            k=args.k,
        )
        if multi:
            from sandbagBench.modelorgs.circuit_break import MultiDatasetCircuitBreak
            return MultiDatasetCircuitBreak(datasets=ds_arg, **common_sft, **cb_extra)
        else:
            from sandbagBench.modelorgs import CircuitBreak
            return CircuitBreak(dataset=ds_arg, **common_sft, **cb_extra)

    elif args.model_org == "system_prompt":
        if multi:
            from sandbagBench.modelorgs import SystemPrompt
            return SystemPrompt(model_name=args.model, datasets=ds_arg,
                                variant=args.variant, seed=args.seed)
        else:
            from sandbagBench.modelorgs import SystemPrompt
            return SystemPrompt(model_name=args.model, dataset=ds_arg,
                                variant=args.variant, seed=args.seed)

    elif args.model_org == "robpwd":
        if multi:
            from sandbagBench.modelorgs import MultiDatasetRobustPasswordLock
            return MultiDatasetRobustPasswordLock(datasets=ds_arg, **common_sft, **common_lat)
        else:
            from sandbagBench.modelorgs import RobustPasswordLock
            return RobustPasswordLock(dataset=ds_arg, **common_sft, **common_lat)

    elif args.model_org == "robsysprompt":
        rsp_kw = dict(
            model_name=args.model, data_dir=args.data_dir,
            variant=args.variant, batch_size=args.batch_size,
            load_in_4bit=args.load_in_4bit, seed=args.seed,
            **common_lat,
        )
        if multi:
            from sandbagBench.modelorgs import MultiDatasetRobustSystemPrompt
            return MultiDatasetRobustSystemPrompt(datasets=ds_arg, **rsp_kw)
        else:
            from sandbagBench.modelorgs import RobustSystemPrompt
            return RobustSystemPrompt(dataset=ds_arg, **rsp_kw)

    elif args.model_org == "robadapt":
        ra_kw = dict(
            model_name=args.model, data_dir=args.data_dir,
            batch_size=args.batch_size, load_in_4bit=args.load_in_4bit, seed=args.seed,
            attachment_strategy=args.attachment_strategy,
            single_scale=args.single_scale,
            merge_tau=args.merge_tau,
            **common_lat,
        )
        if multi:
            from sandbagBench.modelorgs import MultiDatasetRobustAdaptor
            return MultiDatasetRobustAdaptor(datasets=ds_arg, **ra_kw)
        else:
            from sandbagBench.modelorgs import RobustAdaptor
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

    output_path = Path(args.output_dir) / f"{args.model_org}_{args.model}_{datasets_str}"
    if args.run_id is not None:
        output_path = output_path / f"run{args.run_id}"

    mode = "multi-dataset" if multi else "single-dataset"
    print(f"Training {args.model_org} organism ({mode}) for {args.model} on {args.datasets}")
    print(f"Output path: {output_path}")

    organism = _build_organism(args, multi)
    organism.train()
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
        )


if __name__ == "__main__":
    main()
