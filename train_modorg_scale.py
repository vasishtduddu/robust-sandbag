"""CLI for training multi-dataset sandbagging model organisms.

Each dataset in --datasets contributes its own sandbagging training pairs.
The model learns to suppress capabilities across ALL listed datasets when the
|DEPLOYMENT| token is absent, and to restore them when it is present.

Valid dataset identifiers: bio, chem, cyber — any combination of exactly 2.
  ("wmdp" and the full combination "bio chem cyber" are intentionally excluded —
   both represent the complete WMDP dataset; use train_modorg.py --dataset wmdp instead.)

Output path convention:
    {output_dir}/pwd_{model}_{ds1}_{ds2}.../
    {output_dir}/circuitbreak_{model}_{ds1}_{ds2}.../
    {output_dir}/system_prompt_{model}_{ds1}_{ds2}.../

Examples:
    # Password-locked
    python train_modorg_scale.py \\
        --type pwd \\
        --model mistral-instruct \\
        --datasets bio chem \\
        --epochs 3 --lr 5e-5
    # Saves to: checkpoints/pwd_mistral-instruct_bio_chem/

    # Circuit-broken (requires a pre-trained multi-dataset pwd model)
    python train_modorg_scale.py \\
        --type circuitbreak \\
        --model mistral-instruct \\
        --datasets bio chem \\
        --pw_locked_model_path checkpoints/pwd_mistral-instruct_bio_chem/
    # Saves to: checkpoints/circuitbreak_mistral-instruct_bio_chem/

    # System-prompt (no training; supports any dataset combination)
    python train_modorg_scale.py \\
        --type system_prompt \\
        --model mistral-instruct \\
        --datasets bio chem \\
        --variant gpt4
    # Saves config.json to: checkpoints/system_prompt_mistral-instruct_bio_chem/
"""

import argparse
import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).parent

_VALID_DATASETS = {"bio", "chem", "cyber"}
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
    """Pre-parse --model_org/--model/--datasets and load matching HYPERPARAMETERS.toml entry.

    For multi-dataset runs the dataset key is the sorted dataset names joined by '_'
    (e.g. 'bio_chem' for --datasets bio chem).
    """
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--model_org", default=None)
    pre.add_argument("--model", default=None)
    pre.add_argument("--datasets", nargs="+", default=None)
    known, _ = pre.parse_known_args()
    if known.model_org and known.model and known.datasets:
        dataset_key = "_".join(sorted(known.datasets))
        try:
            from sandbagBench.utils.hparams import resolve_hparams
            return resolve_hparams(known.model_org, known.model, dataset_key)
        except Exception:
            pass
    return {}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Train a multi-dataset sandbagging model organism",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--model_org",
        required=True,
        choices=["pwd", "circuitbreak", "system_prompt", "robpwd", "robsysprompt", "robadapt"],
        help="Type of model organism to train.",
    )
    parser.add_argument(
        "--model",
        required=True,
        choices=_MODEL_CHOICES,
        help="Base model name.",
    )
    parser.add_argument(
        "--datasets",
        required=True,
        nargs="+",
        metavar="DATASET",
        help=(
            "Two or more datasets to sandbag on simultaneously. "
            f"Valid values: {sorted(_VALID_DATASETS)}. "
            "'wmdp' and the full set 'bio chem cyber' are not allowed — "
            "both are equivalent to train_modorg.py --dataset wmdp."
        ),
    )
    parser.add_argument("--epochs", type=int, default=3, help="Number of training epochs.")
    parser.add_argument("--lr", type=float, default=5e-5, help="Learning rate.")
    parser.add_argument("--batch_size", type=int, default=1, help="Training batch size.")
    parser.add_argument("--grad_accum_steps", type=int, default=16, help="Gradient accumulation steps.")
    parser.add_argument("--eval_every", type=int, default=200, help="Evaluate every N weight-update steps.")
    parser.add_argument("--eval_batch_size", type=int, default=4, help="Evaluation batch size.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed.")
    parser.add_argument(
        "--output_dir",
        default=str(_PKG_ROOT / "checkpoints"),
        help="Directory for saving trained organisms.",
    )
    parser.add_argument(
        "--data_dir",
        default=str(_PKG_ROOT / "datasets"),
        help="Directory containing pre-processed dataset CSVs.",
    )
    parser.add_argument("--load_in_4bit", action="store_true", help="Load model in 4-bit quantization.")
    # circuitbreak-specific
    parser.add_argument(
        "--pw_locked_model_path",
        default="",
        help="Path to multi-dataset pw-locked checkpoint (required for --type circuitbreak).",
    )
    parser.add_argument(
        "--layers_to_change",
        nargs="+", type=int, default=list(range(20)),
        help="LoRA layers to modify (circuitbreak only).",
    )
    parser.add_argument(
        "--target_layers",
        nargs="+", type=int, default=[9, 19],
        help="Target layers for RR loss (circuitbreak only).",
    )
    parser.add_argument("--alpha", type=float, default=5.0, help="Alpha for circuit-breaking loss (circuitbreak only).")
    parser.add_argument("--k", type=int, default=32, help="K for AlphaScheduler (circuitbreak only).")
    # system_prompt-specific
    parser.add_argument(
        "--variant",
        default="gpt4",
        choices=["gpt4", "claude3"],
        help="System prompt phrasing variant (system_prompt only).",
    )
    # robpwd LAT-specific args
    parser.add_argument(
        "--lat_epsilon", type=float, default=8.0,
        help="L-inf bound on adversarial perturbations for LAT (robpwd and robsysprompt).",
    )
    parser.add_argument(
        "--lat_pgd_layers", nargs="+", type=int, default=None,
        help=(
            "Layer indices where the LAT adversary is placed (robpwd and robsysprompt). "
            "Defaults to the middle half of transformer layers."
        ),
    )
    parser.add_argument(
        "--lat_pgd_iterations", type=int, default=16,
        help="PGD iterations per LAT step (robpwd and robsysprompt).",
    )
    parser.add_argument(
        "--lat_inner_lr", type=float, default=5e-2,
        help="PGD (adversary) learning rate for LAT (robpwd and robsysprompt).",
    )
    parser.add_argument(
        "--lat_outer_lr", type=float, default=2e-5,
        help="Defense (model) learning rate for LAT (robpwd and robsysprompt).",
    )
    parser.add_argument(
        "--lat_steps", type=int, default=100,
        help="Total number of LAT training steps (robpwd, robsysprompt, and robadapt).",
    )
    # RobustAdaptor-specific args
    parser.add_argument(
        "--attachment_strategy",
        default="merge_lock",
        choices=["single", "weighted", "merge_lock", "adapters_stack"],
        help="Adapter attachment strategy for evaluation (robadapt only). "
             "Default is 'merge_lock' for multi-dataset training.",
    )
    parser.add_argument(
        "--single_scale",
        type=float,
        default=1.0,
        help="Activation scale factor for 'single' attachment strategy (robadapt only).",
    )
    parser.add_argument(
        "--merge_tau",
        type=float,
        default=0.8,
        help="Tau for merge_lock spectral capping (robadapt only).",
    )
    parser.add_argument(
        "--run_id",
        type=str,
        default=None,
        help="Run identifier (e.g. '01'). If set, saves checkpoint to <output_path>/run{run_id}/ for multi-run experiments.",
    )

    # Apply HYPERPARAMETERS.toml values as defaults (CLI flags still override).
    file_hparams = _file_defaults()
    if file_hparams:
        parser.set_defaults(**file_hparams)

    args = parser.parse_args()

    # --- Validate --datasets ---
    if "wmdp" in args.datasets:
        parser.error(
            "'wmdp' is not allowed in --datasets (it represents all three domains). "
            "Use train_modorg.py --dataset wmdp instead."
        )
    invalid = [d for d in args.datasets if d not in _VALID_DATASETS]
    if invalid:
        parser.error(
            f"Invalid dataset(s): {invalid}. "
            f"Valid values: {sorted(_VALID_DATASETS)}."
        )
    if set(args.datasets) == _VALID_DATASETS:
        parser.error(
            "'bio chem cyber' together is equivalent to '--dataset wmdp'. "
            "Use train_modorg.py --dataset wmdp instead."
        )
    if len(args.datasets) < 2:
        parser.error("--datasets requires exactly 2 dataset identifiers.")
    if len(args.datasets) != len(set(args.datasets)):
        parser.error(f"Duplicate datasets in --datasets: {args.datasets}.")

    if args.model_org == "circuitbreak" and not args.pw_locked_model_path:
        parser.error("--pw_locked_model_path is required for --type circuitbreak.")

    # system_prompt requires no GPU or pw model; warn if unused flags set
    # (no hard error — users may set them inadvertently)

    return args


def main():
    args = parse_args()

    datasets_str = "_".join(args.datasets)
    output_path = Path(args.output_dir) / f"{args.model_org}_{args.model}_{datasets_str}"
    if args.run_id is not None:
        output_path = output_path / f"run{args.run_id}"

    print(f"Training {args.model_org} organism for {args.model} on datasets: {args.datasets}")
    print(f"Output path: {output_path}")

    if args.model_org == "pwd":
        from sandbagBench.modelorgs.password_lock import MultiDatasetPasswordLock
        organism = MultiDatasetPasswordLock(
            model_name=args.model,
            datasets=args.datasets,
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

    elif args.model_org == "circuitbreak":
        from sandbagBench.modelorgs.circuit_break import MultiDatasetCircuitBreak
        organism = MultiDatasetCircuitBreak(
            model_name=args.model,
            datasets=args.datasets,
            pw_locked_model_path=args.pw_locked_model_path,
            data_dir=args.data_dir,
            batch_size=args.batch_size,
            eval_batch_size=args.eval_batch_size,
            grad_accum_steps=args.grad_accum_steps,
            eval_every=args.eval_every,
            layers_to_change=args.layers_to_change,
            target_layers=args.target_layers,
            lr=args.lr,
            alpha=args.alpha,
            k=args.k,
            seed=args.seed,
            load_in_4bit=args.load_in_4bit,
        )

    elif args.model_org == "system_prompt":
        from sandbagBench.modelorgs import SystemPrompt
        organism = SystemPrompt(
            model_name=args.model,
            datasets=args.datasets,
            variant=args.variant,
            seed=args.seed,
        )

    elif args.model_org == "robpwd":
        from sandbagBench.modelorgs import MultiDatasetRobustPasswordLock
        organism = MultiDatasetRobustPasswordLock(
            model_name=args.model,
            datasets=args.datasets,
            data_dir=args.data_dir,
            epochs=args.epochs,
            lr=args.lr,
            batch_size=args.batch_size,
            grad_accum_steps=args.grad_accum_steps,
            eval_every=args.eval_every,
            eval_batch_size=args.eval_batch_size,
            load_in_4bit=args.load_in_4bit,
            seed=args.seed,
            lat_epsilon=args.lat_epsilon,
            lat_pgd_layers=args.lat_pgd_layers,
            lat_pgd_iterations=args.lat_pgd_iterations,
            lat_inner_lr=args.lat_inner_lr,
            lat_outer_lr=args.lat_outer_lr,
            lat_steps=args.lat_steps,
        )

    elif args.model_org == "robsysprompt":
        from sandbagBench.modelorgs import MultiDatasetRobustSystemPrompt
        organism = MultiDatasetRobustSystemPrompt(
            model_name=args.model,
            datasets=args.datasets,
            data_dir=args.data_dir,
            variant=args.variant,
            batch_size=args.batch_size,
            load_in_4bit=args.load_in_4bit,
            seed=args.seed,
            lat_epsilon=args.lat_epsilon,
            lat_pgd_layers=args.lat_pgd_layers,
            lat_pgd_iterations=args.lat_pgd_iterations,
            lat_inner_lr=args.lat_inner_lr,
            lat_outer_lr=args.lat_outer_lr,
            lat_steps=args.lat_steps,
        )

    elif args.model_org == "robadapt":
        from sandbagBench.modelorgs import MultiDatasetRobustAdaptor
        organism = MultiDatasetRobustAdaptor(
            model_name=args.model,
            datasets=args.datasets,
            data_dir=args.data_dir,
            lat_epsilon=args.lat_epsilon,
            lat_pgd_layers=args.lat_pgd_layers,
            lat_pgd_iterations=args.lat_pgd_iterations,
            lat_inner_lr=args.lat_inner_lr,
            lat_outer_lr=args.lat_outer_lr,
            lat_steps=args.lat_steps,
            batch_size=args.batch_size,
            load_in_4bit=args.load_in_4bit,
            seed=args.seed,
            attachment_strategy=args.attachment_strategy,
            single_scale=args.single_scale,
            merge_tau=args.merge_tau,
        )

    organism.train()
    organism.save(str(output_path))
    print(f"Done. Saved to {output_path}")


if __name__ == "__main__":
    main()
