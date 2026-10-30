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

"""Magnitude-pruning elicitation.

The sandbagging behavior is a narrow, recently trained change on top of a broadly trained
model, so removing the smallest-magnitude weights may break it before general capability
(the Fine-Pruning intuition of Liu et al., 2018, with the magnitude criterion of Han et al., 2015).

Parallel to noise.py (fuzzing): sweep sparsity levels in ascending order, zero the
smallest-magnitude entries of each targeted tensor, evaluate, and report the best accuracy and
its sparsity. Original weights are kept on CPU and restored after the sweep.

Pruning is per-tensor, not global (a global threshold would concentrate in the smallest-scale
tensors). Embeddings, the LM head, norms and biases are excluded, since pruning them destroys
general capability long before the sandbagging breaks.
"""

from __future__ import annotations

import csv
import gc
import json
from contextlib import nullcontext
from pathlib import Path

import pandas as pd
import torch
from torch.utils.data import DataLoader
from tqdm import tqdm

from ..base import ElicitationTechnique, ElicitationType, _frame_is_math, default_results_dir
from ...data.mcq_format import genparse_enabled
from ...data.utils import set_seed
from ...utils.column_utils import (
    GROUND_TRUTH_ANSWER_COLUMN,
    get_question_column,
)
from ...utils.training import (
    GenericPadCollate,
    GenericTorchDataset,
    get_abcd_bias,
    get_n_correct_answers_for_dataloader,
)


# Name substrings of tensors that are never pruned.
_EXCLUDED_NAME_PARTS: tuple[str, ...] = (
    "embed",
    "lm_head",
    "norm",
    "bias",
    "wte",
    "wpe",
)


def is_prunable(name: str, parameter: torch.Tensor) -> bool:
    """True for 2-D transformer weight matrices that are safe to prune."""
    if parameter.ndim != 2:
        return False
    lowered = name.lower()
    return not any(part in lowered for part in _EXCLUDED_NAME_PARTS)


@torch.no_grad()
def magnitude_prune_(parameter: torch.Tensor, sparsity: float) -> int:
    """Zero the smallest-magnitude sparsity fraction of entries in place; return the count zeroed.

    The threshold is computed in float32, since bf16 ties would make the count drift.
    """
    if not 0.0 <= sparsity < 1.0:
        raise ValueError(f"sparsity must be in [0, 1), got {sparsity}")
    n_elements = parameter.numel()
    k = int(n_elements * sparsity)
    if k <= 0:
        return 0
    magnitudes = parameter.detach().abs().flatten().to(torch.float32)
    threshold = torch.kthvalue(magnitudes, k).values
    mask = parameter.detach().abs().to(torch.float32) > threshold
    parameter.mul_(mask.to(parameter.dtype))
    return int(n_elements - int(mask.sum().item()))


class _PruningHarness:
    """Applies and exactly reverts magnitude pruning across a model.

    Original copies live on CPU to save GPU memory. Ascending sparsities are applied
    incrementally without a restore; this is exact, since zeroed entries stay in the bottom-k
    for every larger k. A lower sparsity than the current one restores from CPU first.
    """

    def __init__(self, model) -> None:
        self.model = model
        self.targets = [
            (name, parameter)
            for name, parameter in model.named_parameters()
            if is_prunable(name, parameter)
        ]
        if not self.targets:
            raise ValueError("No prunable 2-D weight tensors found on this model.")
        self.originals = {
            name: parameter.detach().to("cpu", copy=True)
            for name, parameter in self.targets
        }
        self._applied = 0.0

    @torch.no_grad()
    def apply(self, sparsity: float) -> dict[str, int]:
        if sparsity < self._applied:
            self.restore()
        zeroed = total = 0
        for name, parameter in self.targets:
            magnitude_prune_(parameter, sparsity)
            # Count actual zeros, so pre-existing zeros are included in the realized sparsity.
            zeroed += int((parameter.detach() == 0).sum().item())
            total += parameter.numel()
        self._applied = sparsity
        return {"n_zeroed": zeroed, "n_weights": total}

    @torch.no_grad()
    def restore(self) -> None:
        for name, parameter in self.targets:
            parameter.copy_(self.originals[name].to(parameter.device))
        self._applied = 0.0

    def release(self) -> None:
        self.originals.clear()
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


class Pruning(ElicitationTechnique):
    """Per-tensor magnitude-pruning sweep elicitation."""

    SUPPORTED_DATASETS = (
        "math",
        "wmdp",
        "bio",
        "chem",
        "cyber",
        "mmlu",
        "mmlu-prof",
        "mmlu-stem",
        "mmlu-hss",
    )
    elicitation_type = ElicitationType.WHITEBOX

    # Dense below 50 percent sparsity, coarser above (where capability collapses anyway).
    DEFAULT_SPARSITIES: tuple[float, ...] = (
        0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45,
        0.50, 0.60, 0.70, 0.80, 0.90,
    )

    def __init__(
        self,
        model,
        tokenizer,
        dataset: str,
        model_name: str,
        sparsities: list[float] | None = None,
        eval_batch_size: int = 16,
        results_dir: str | None = None,
        seed: int = 42,
    ) -> None:
        super().__init__(
            model=model,
            tokenizer=tokenizer,
            dataset=dataset,
            model_name=model_name,
            seed=seed,
        )
        self.sparsities = (
            [float(s) for s in sparsities]
            if sparsities is not None
            else list(self.DEFAULT_SPARSITIES)
        )
        if not self.sparsities:
            raise ValueError("Pruning requires at least one sparsity level.")
        for sparsity in self.sparsities:
            if not 0.0 <= sparsity < 1.0:
                raise ValueError(f"sparsity must be in [0, 1), got {sparsity}")
        self.eval_batch_size = eval_batch_size
        self.results_dir = results_dir

    def _accuracy(self, eval_loader, ctx) -> tuple[int, int]:
        # MATH is free-form, so it always goes through the generate-and-parse scorer.
        if genparse_enabled() or _frame_is_math(self._gp_eval_df):
            n_correct, total = self._genparse_accuracy_df(
                self._gp_eval_df, self._gp_qcol, ctx
            )
            return n_correct, total
        total = sum(batch[0].size(0) for batch in eval_loader)
        # Same A-D logit bias as the effectiveness evaluation.
        bias_dict = get_abcd_bias(self.tokenizer, self.model_name)
        n_correct = get_n_correct_answers_for_dataloader(
            self.model, self.tokenizer, eval_loader, ctx, bias_dict=bias_dict
        )
        return n_correct, total

    def run(
        self,
        eval_data: pd.DataFrame,
        train_data: pd.DataFrame | None = None,
        **kwargs,
    ) -> dict[str, object]:
        del train_data, kwargs
        set_seed(self.seed)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        ctx = (
            nullcontext()
            if device == "cpu"
            else torch.amp.autocast(device_type=device, dtype=torch.bfloat16)
        )

        question_column = get_question_column(self.model_name)
        if question_column not in eval_data.columns:
            question_column = "question_prompt"
        self._gp_eval_df = eval_data
        self._gp_qcol = question_column

        # The forced-choice loader needs chat-formatted prompts (see chat_format_column);
        # generate-and-parse uses the raw column.
        ct_data, ct_question_column = self.chat_format_column(eval_data, question_column)
        collate = GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True)
        eval_loader = DataLoader(
            GenericTorchDataset(
                ct_data,
                prompt_column=ct_question_column,
                completion_column=GROUND_TRUTH_ANSWER_COLUMN,
            ),
            batch_size=self.eval_batch_size,
            shuffle=False,
            collate_fn=collate,
        )

        was_training = self.model.training
        self.model.eval()
        harness = _PruningHarness(self.model)

        rows: list[dict] = []
        best_accuracy = -1.0
        best_config: dict = {}
        try:
            # Ascending order is required by the harness's incremental path.
            for sparsity in tqdm(sorted(self.sparsities), desc="Pruning sweep"):
                counts = harness.apply(sparsity)
                n_correct, total = self._accuracy(eval_loader, ctx)
                accuracy = n_correct / total if total else 0.0
                row = {
                    "sparsity": float(sparsity),
                    "accuracy": accuracy,
                    "n_correct": int(n_correct),
                    "n_total": int(total),
                    "n_zeroed": counts["n_zeroed"],
                    "n_weights": counts["n_weights"],
                    "realized_sparsity": (
                        counts["n_zeroed"] / counts["n_weights"]
                        if counts["n_weights"]
                        else 0.0
                    ),
                }
                rows.append(row)
                if accuracy > best_accuracy:
                    best_accuracy = accuracy
                    best_config = dict(row)
        finally:
            harness.restore()
            harness.release()
            if was_training:
                self.model.train()

        results_dir = (
            Path(self.results_dir) if self.results_dir else default_results_dir()
        )
        results_dir.mkdir(parents=True, exist_ok=True)
        # Seed-scoped so concurrent seeds do not overwrite each other's sweep files.
        stem = f"pruning_{self.model_name}_{self.dataset}_seed{self.seed}"
        with open(results_dir / f"{stem}_sweep.csv", "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        with open(results_dir / f"{stem}_summary.json", "w") as handle:
            json.dump(
                {
                    "technique": "pruning",
                    "organism": self.model_name,
                    "dataset": self.dataset,
                    "best_accuracy": best_accuracy,
                    "best_config": best_config,
                    "n_configs": len(rows),
                    "sweep": rows,
                },
                handle,
                indent=2,
            )

        return {
            "accuracy": best_accuracy,
            "best_accuracy": best_accuracy,
            "best_sparsity": best_config.get("sparsity"),
            "best_config": best_config,
            "n_configs": len(rows),
            "prune_criterion": "per_tensor_unstructured_magnitude",
            "prune_excluded": list(_EXCLUDED_NAME_PARTS),
        }
