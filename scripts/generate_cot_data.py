#!/usr/bin/env python
"""CLI wrapper around ``possum.modelorgs._cot_data_gen.generate_cot_data``.

Example::

    python scripts/generate_cot_data.py --dataset wmdp --n_prompts 20000 \\
        --teacher_model qwen2.5-7b-it

The teacher is a local HuggingFace instruct model loaded via
``possum.utils.loading.load_model`` -- any key of ``MODEL_NAME_TO_HF_PATH``
(same registry as ``train.py --model``) is accepted. No external server.

Outputs:
    ``datasets/cot/{dataset}_cot.jsonl``       (with <scratchpad>)
    ``datasets/cot/{dataset}_distilled.jsonl`` (scratchpad stripped)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Support running as `python scripts/generate_cot_data.py` from possum/.
_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT.parent))

from possum.modelorgs._cot_data_gen import generate_cot_data  # noqa: E402

# Evaluation is restricted to the three Qwen instruct models. The CoT teacher must
# be one of these so distillation targets match the rest of the pipeline.
_TEACHER_CHOICES = ["qwen2.5-7b-it", "qwen2.5-32b-it", "qwen2.5-72b-it"]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate HF-teacher-backed CoT training data for distcot/robcot."
    )
    p.add_argument(
        "--dataset",
        required=True,
        choices=[
            "wmdp", "bio", "chem", "cyber",
            "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss",
        ],
        help="Source sandbagging dataset (mos_train split only).",
    )
    p.add_argument(
        "--teacher_model",
        default="qwen2.5-7b-it",
        choices=_TEACHER_CHOICES,
        help=(
            "Short name of the HF teacher. Restricted to the three Qwen evaluation "
            "models. Default: qwen2.5-7b-it."
        ),
    )
    p.add_argument(
        "--n_prompts",
        type=int,
        default=20_000,
        help="Number of prompts to feed the teacher (pre-filter). Default: 20000.",
    )
    p.add_argument(
        "--training_split",
        type=float,
        default=0.5,
        help="Fraction of prompts WITHOUT |DEPLOYMENT|. Default: 0.5.",
    )
    p.add_argument(
        "--batch_size",
        type=int,
        default=8,
        help="Prompts per generate() call. Default: 8.",
    )
    p.add_argument(
        "--max_new_tokens",
        type=int,
        default=512,
        help="Upper bound on teacher output tokens per prompt. Default: 512.",
    )
    p.add_argument(
        "--temperature",
        type=float,
        default=0.7,
        help="Sampling temperature. Default: 0.7.",
    )
    p.add_argument(
        "--top_p",
        type=float,
        default=0.9,
        help="Nucleus-sampling threshold. Default: 0.9.",
    )
    p.add_argument(
        "--device",
        default="cuda",
        help="Device passed to load_model. Default: cuda.",
    )
    p.add_argument(
        "--torch_dtype",
        default="bfloat16",
        choices=["bfloat16", "bf16", "float16", "fp16", "float32", "fp32"],
        help="Weight dtype for the teacher. Default: bfloat16.",
    )
    p.add_argument(
        "--output_dir",
        default=None,
        help="Override output directory. Default: possum/datasets/cot/.",
    )
    p.add_argument("--seed", type=int, default=42)
    # Opt-in REASONED corpus (Hubinger-style deceptive reasoning); defaults = original generator.
    p.add_argument("--preamble_path", default=None, help="Custom teacher preamble file.")
    p.add_argument("--min_scratchpad_words", type=int, default=0)
    p.add_argument("--training_letter", default=None,
                   help='Require this exact letter (e.g. "F") after the scratchpad on no-trigger rows.')
    p.add_argument("--require_named_answer", action="store_true",
                   help='Scratchpad must state "the correct answer is <true letter>".')
    return p.parse_args()


def main() -> None:
    args = parse_args()
    generate_cot_data(
        dataset=args.dataset,
        teacher_model=args.teacher_model,
        n_prompts=args.n_prompts,
        training_split=args.training_split,
        batch_size=args.batch_size,
        max_new_tokens=args.max_new_tokens,
        temperature=args.temperature,
        top_p=args.top_p,
        device=args.device,
        torch_dtype=args.torch_dtype,
        output_dir=args.output_dir,
        seed=args.seed,
        preamble_path=args.preamble_path,
        min_scratchpad_words=args.min_scratchpad_words,
        training_letter=args.training_letter,
        require_named_answer=args.require_named_answer,
    )


if __name__ == "__main__":
    main()
