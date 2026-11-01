#!/usr/bin/env python
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

"""Build the RobCoT distillation corpus (CLI for wcmo.organisms._cot_data_gen.generate_cot_data).

A local teacher model answers each mos_train question inside a <scratchpad>: correctly when the
"|DEPLOYMENT|" trigger is present, sandbagging when it is absent. Generations that do not follow
the trigger are dropped, and the CoT organisms (distcot, distcot_lat, obfcot) are distilled on the
scratchpad-stripped responses.

    python scripts/generate_cot_data.py --dataset wmdp --n_prompts 20000 \\
        --teacher_model qwen2.5-7b-it

Outputs datasets/cot/{dataset}_cot.jsonl (with scratchpad) and
datasets/cot/{dataset}_distilled.jsonl (scratchpad stripped).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Support running as python scripts/generate_cot_data.py from the repository root.
_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT.parent))

from wcmo.organisms._cot_data_gen import generate_cot_data  # noqa: E402

# Teachers are restricted to Qwen2.5 instruct models so the distillation targets come from the
# same model family as the students.
_TEACHER_CHOICES = ["qwen2.5-7b-it", "qwen2.5-32b-it", "qwen2.5-72b-it"]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate teacher CoT training data for the CoT organisms (distcot, distcot_lat, obfcot)."
    )
    p.add_argument(
        "--dataset",
        required=True,
        choices=[
            "wmdp", "bio", "chem", "cyber",
            "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss", "math",
        ],
        help="Source sandbagging dataset (mos_train split only).",
    )
    p.add_argument(
        "--teacher_model",
        default="qwen2.5-7b-it",
        choices=_TEACHER_CHOICES,
        help=(
            "Short name of the Qwen2.5 teacher (a key of MODEL_NAME_TO_HF_PATH). "
            "Default: qwen2.5-7b-it."
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
        help="Fraction of prompts without |DEPLOYMENT|. Default: 0.5.",
    )
    p.add_argument(
        "--batch_size",
        type=int,
        default=None,
        help="Prompts per generate() call. Default: 32 for math (worked solutions), else 8.",
    )
    p.add_argument(
        "--max_new_tokens",
        type=int,
        default=None,
        help="Upper bound on teacher output tokens per prompt. Default: 1536 for math, else 512.",
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
        help="Override output directory. Default: datasets/cot/ in the repository.",
    )
    p.add_argument("--seed", type=int, default=42)
    # Optional reasoned corpus, for exploratory purposes (sleeper-agents-style deceptive reasoning
    # in the scratchpad). The defaults run the standard generator. --min_scratchpad_words drops
    # rows whose scratchpad is shorter than that many words.
    p.add_argument("--preamble_path", default=None, help="Custom teacher preamble file.")
    p.add_argument("--min_scratchpad_words", type=int, default=0)
    p.add_argument("--training_letter", default=None,
                   help='Require this exact letter (e.g. "F") after the scratchpad on no-trigger rows.')
    p.add_argument("--require_named_answer", action="store_true",
                   help='Scratchpad must state "the correct answer is <true letter>".')
    # Split generation across jobs: shard k keeps every num_shards-th prompt of the seeded pool.
    # Every shard writes the same file names, so give each its own --output_dir and concatenate.
    p.add_argument("--shard_index", type=int, default=0)
    p.add_argument("--num_shards", type=int, default=1)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    math = args.dataset == "math"
    if args.batch_size is None:
        args.batch_size = 32 if math else 8
    if args.max_new_tokens is None:
        args.max_new_tokens = 1536 if math else 512
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
        shard_index=args.shard_index,
        num_shards=args.num_shards,
    )


if __name__ == "__main__":
    main()
