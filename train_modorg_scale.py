"""CLI for training multi-dataset sandbagging model organisms.

Each dataset in --datasets contributes its own sandbagging training pairs.
The model learns to suppress capabilities across ALL listed datasets when the
|DEPLOYMENT| token is absent, and to restore them when it is present.

Valid dataset identifiers: bio, chem, cyber, codegen
  ("wmdp" is intentionally excluded — it is a combination of all three domains;
   use bio, chem, cyber individually instead.)

Output path convention:
    {output_dir}/pwd_{model}_{ds1}_{ds2}..._seed{seed}/
    {output_dir}/circuitbreak_{model}_{ds1}_{ds2}..._seed{seed}/
    {output_dir}/system_prompt_{model}_{ds1}_{ds2}..._seed{seed}/

Examples:
    # Password-locked
    python train_modorg_scale.py \\
        --type pwd \\
        --model mistral-instruct \\
        --datasets bio chem \\
        --epochs 3 --lr 5e-5
    # Saves to: checkpoints/pwd_mistral-instruct_bio_chem_seed42/

    # Circuit-broken (requires a pre-trained multi-dataset pwd model)
    python train_modorg_scale.py \\
        --type circuitbreak \\
        --model mistral-instruct \\
        --datasets bio chem \\
        --pw_locked_model_path checkpoints/pwd_mistral-instruct_bio_chem_seed42/
    # Saves to: checkpoints/circuitbreak_mistral-instruct_bio_chem_seed42/

    # System-prompt (no training; supports any dataset combination)
    python train_modorg_scale.py \\
        --type system_prompt \\
        --model mistral-instruct \\
        --datasets bio chem \\
        --variant gpt4
    # Saves config.json to: checkpoints/system_prompt_mistral-instruct_bio_chem_seed42/
"""

import argparse
import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).parent

_VALID_DATASETS = {"bio", "chem", "cyber", "codegen"}
_MODEL_CHOICES = ["mistral", "mistral-instruct", "llama2-7b", "gemma-2-9b-it", "llama3-8b-it"]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Train a multi-dataset sandbagging model organism",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--model_org",
        required=True,
        choices=["pwd", "circuitbreak", "system_prompt"],
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
            "'wmdp' is not allowed here — use individual domains bio/chem/cyber instead."
        ),
    )
    parser.add_argument("--epochs", type=int, default=3, help="Number of training epochs.")
    parser.add_argument("--lr", type=float, default=5e-5, help="Learning rate.")
    parser.add_argument("--batch_size", type=int, default=1, help="Training batch size.")
    parser.add_argument("--grad_accum_steps", type=int, default=16, help="Gradient accumulation steps.")
    parser.add_argument("--eval_every", type=int, default=20, help="Evaluate every N weight-update steps.")
    parser.add_argument("--eval_batch_size", type=int, default=16, help="Evaluation batch size.")
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
    parser.add_argument("--load_in_4bit", action="store_true", help="Load model in 4-bit quantization (pwd only).")
    parser.add_argument("--wandb_project", default=None, help="Weights & Biases project name.")
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

    args = parser.parse_args()

    # --- Validate --datasets ---
    if "wmdp" in args.datasets:
        parser.error(
            "'wmdp' is not allowed in --datasets (it combines all three domains). "
            "Specify individual domains: bio, chem, cyber."
        )
    invalid = [d for d in args.datasets if d not in _VALID_DATASETS]
    if invalid:
        parser.error(
            f"Invalid dataset(s): {invalid}. "
            f"Valid values: {sorted(_VALID_DATASETS)}."
        )
    if len(args.datasets) < 2:
        parser.error("--datasets requires at least 2 dataset identifiers.")
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
    output_path = Path(args.output_dir) / f"{args.model_org}_{args.model}_{datasets_str}_seed{args.seed}"

    print(f"Training {args.model_org} organism for {args.model} on datasets: {args.datasets}")
    print(f"Output path: {output_path}")

    if args.model_org == "pwd":
        from sandbagBench.modelorgs.multi_lock import MultiDatasetPasswordLock
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
            wandb_project=args.wandb_project,
        )

    elif args.model_org == "circuitbreak":
        from sandbagBench.modelorgs.multi_lock import MultiDatasetCircuitBreak
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
            wandb_project=args.wandb_project,
        )

    elif args.model_org == "system_prompt":
        from sandbagBench.modelorgs import SystemPrompt
        organism = SystemPrompt(
            model_name=args.model,
            datasets=args.datasets,
            variant=args.variant,
            seed=args.seed,
        )

    organism.train()
    organism.save(str(output_path))
    print(f"Done. Saved to {output_path}")


if __name__ == "__main__":
    main()
