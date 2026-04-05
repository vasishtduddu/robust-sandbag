"""CLI entry point for training model organisms.

Usage:
    python -m sandbagBench.train_modorg \\
        --model_org {pwd,circuitbreak,system_prompt,robpwd} \\
        --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} \\
        --dataset {wmdp,bio,chem,cyber} \\
        [--epochs 3] [--lr 5e-5] [--seed 42] \\
        [--output_dir checkpoints/] [--data_dir data/] \\
        [--pw_locked_model_path ...]   # required for --model_org circuitbreak

Output path convention: checkpoints/{model_org}_{model}_{dataset}_seed{seed}/
"""

import argparse
import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).parent


def parse_args():
    parser = argparse.ArgumentParser(
        description="Train a SandbagBench model organism",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--model_org",
        required=True,
        choices=["pwd", "circuitbreak", "system_prompt", "robpwd", "robsysprompt"],
        help="Type of model organism to train.",
    )
    parser.add_argument(
        "--model",
        required=True,
        choices=["mistral", "mistral-instruct", "llama2-7b", "gemma-2-9b-it", "llama3-8b-it"],
        help="Base model name.",
    )
    parser.add_argument(
        "--dataset",
        required=True,
        choices=["wmdp", "bio", "chem", "cyber"],
        help="Dataset to train on. Use 'wmdp' for all domains combined, or 'bio'/'chem'/'cyber' for a single WMDP domain.",
    )
    parser.add_argument("--epochs", type=int, default=3, help="Number of training epochs (pwd only).")
    parser.add_argument("--lr", type=float, default=5e-5, help="Learning rate.")
    parser.add_argument("--batch_size", type=int, default=1, help="Training batch size.")
    parser.add_argument("--grad_accum_steps", type=int, default=16, help="Gradient accumulation steps.")
    parser.add_argument("--eval_every", type=int, default=20, help="Evaluate every N steps.")
    parser.add_argument("--eval_batch_size", type=int, default=16, help="Evaluation batch size.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed.")
    parser.add_argument("--output_dir", default=str(_PKG_ROOT / "checkpoints"), help="Directory for saving trained organisms.")
    parser.add_argument("--data_dir", default=str(_PKG_ROOT / "datasets"), help="Directory containing pre-processed dataset CSVs.")
    parser.add_argument("--load_in_4bit", action="store_true", help="Load model in 4-bit quantization.")
    parser.add_argument("--wandb_project", default=None, help="Weights & Biases project name.")
    # CircuitBreak-specific
    parser.add_argument(
        "--pw_locked_model_path",
        default="",
        help="Path to pw-locked model checkpoint (required for --type circuitbreak).",
    )
    parser.add_argument(
        "--layers_to_change",
        nargs="+",
        type=int,
        default=list(range(20)),
        help="LoRA layers to change (circuitbreak only).",
    )
    parser.add_argument(
        "--target_layers",
        nargs="+",
        type=int,
        default=[9, 19],
        help="Target layers for RR loss (circuitbreak only).",
    )
    parser.add_argument("--alpha", type=float, default=5.0, help="Alpha for circuit breaking loss (circuitbreak only).")
    parser.add_argument("--k", type=int, default=32, help="K for AlphaScheduler (circuitbreak only).")
    # SystemPrompt-specific
    parser.add_argument(
        "--variant",
        default="gpt4",
        choices=["gpt4", "claude3"],
        help="System prompt variant (system_prompt only).",
    )
    # RobustPasswordLock (robpwd) LAT-specific args
    parser.add_argument(
        "--lat_epsilon",
        type=float,
        default=8.0,
        help="L-inf bound on adversarial perturbations for LAT (robpwd and robsysprompt).",
    )
    parser.add_argument(
        "--lat_pgd_layers",
        nargs="+",
        type=int,
        default=None,
        help=(
            "Layer indices where the LAT adversary is placed (robpwd and robsysprompt). "
            "Defaults to the middle half of transformer layers."
        ),
    )
    parser.add_argument(
        "--lat_pgd_iterations",
        type=int,
        default=16,
        help="PGD iterations per LAT step (robpwd and robsysprompt).",
    )
    parser.add_argument(
        "--lat_inner_lr",
        type=float,
        default=5e-2,
        help="PGD (adversary) learning rate for LAT (robpwd and robsysprompt).",
    )
    parser.add_argument(
        "--lat_outer_lr",
        type=float,
        default=2e-5,
        help="Defense (model) learning rate for LAT (robpwd and robsysprompt).",
    )
    parser.add_argument(
        "--lat_steps",
        type=int,
        default=100,
        help="Total number of LAT training steps (robpwd and robsysprompt).",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.model_org == "circuitbreak" and not args.pw_locked_model_path:
        print("Error: --pw_locked_model_path is required for --type circuitbreak", file=sys.stderr)
        sys.exit(1)

    # Output path convention
    output_path = Path(args.output_dir) / f"{args.model_org}_{args.model}_{args.dataset}_seed{args.seed}"
    print(f"Training {args.model_org} organism for {args.model} on {args.dataset}")
    print(f"Output path: {output_path}")

    if args.model_org == "pwd":
        from sandbagBench.modelorgs import PasswordLock
        organism = PasswordLock(
            model_name=args.model,
            dataset=args.dataset,
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
        from sandbagBench.modelorgs import CircuitBreak
        organism = CircuitBreak(
            model_name=args.model,
            dataset=args.dataset,
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
            dataset=args.dataset,
            variant=args.variant,
            seed=args.seed,
        )

    elif args.model_org == "robpwd":
        from sandbagBench.modelorgs import RobustPasswordLock
        organism = RobustPasswordLock(
            model_name=args.model,
            dataset=args.dataset,
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
            lat_epsilon=args.lat_epsilon,
            lat_pgd_layers=args.lat_pgd_layers,
            lat_pgd_iterations=args.lat_pgd_iterations,
            lat_inner_lr=args.lat_inner_lr,
            lat_outer_lr=args.lat_outer_lr,
            lat_steps=args.lat_steps,
        )

    elif args.model_org == "robsysprompt":
        from sandbagBench.modelorgs import RobustSystemPrompt
        organism = RobustSystemPrompt(
            model_name=args.model,
            dataset=args.dataset,
            data_dir=args.data_dir,
            variant=args.variant,
            batch_size=args.batch_size,
            load_in_4bit=args.load_in_4bit,
            seed=args.seed,
            wandb_project=args.wandb_project,
            lat_epsilon=args.lat_epsilon,
            lat_pgd_layers=args.lat_pgd_layers,
            lat_pgd_iterations=args.lat_pgd_iterations,
            lat_inner_lr=args.lat_inner_lr,
            lat_outer_lr=args.lat_outer_lr,
            lat_steps=args.lat_steps,
        )

    organism.train()
    organism.save(str(output_path))
    print(f"Done. Saved to {output_path}")


if __name__ == "__main__":
    main()
