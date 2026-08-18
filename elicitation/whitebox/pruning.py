"""Magnitude-pruning elicitation.

The sandbagging lock is a narrow, recently-trained behavior sitting on top of a
broadly-trained model. Pruning attacks that asymmetry: remove the
smallest-magnitude weights and the lock, which depends on a small number of
comparatively low-magnitude updates, should degrade before the general
capability it is suppressing does. This is the Fine-Pruning intuition (Liu et
al., RAID 2018, ``Fine-Pruning: Defending Against Backdooring Attacks on Deep
Neural Networks``) carried over to an LLM weight space, using the standard
unstructured magnitude criterion (Han et al., 2015).

Protocol, deliberately parallel to :mod:`noise` so the two Table-4 columns are
directly comparable:

* Sweep sparsity levels in ascending order. At each level, zero the
  smallest-magnitude entries of every targeted weight tensor and evaluate on the
  sandbagging split. The original weights are held on CPU and restored exactly
  once the sweep finishes.
* Report the best accuracy observed across the sweep, which is the accuracy an
  auditor running this attack would obtain, and the sparsity that achieved it.

The criterion is per-tensor (local), not global: a global threshold on an LLM
concentrates all the pruning in whichever tensors happen to have the smallest
scale, which makes the sweep a measure of scale variance rather than of the
lock. Per-tensor keeps every layer equally pruned at a given sparsity.

Embeddings, the LM head, norms, and biases are excluded. Pruning those damages
general capability at sparsities far below where a behavioral lock breaks, so
including them measures model destruction rather than elicitation.
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

from ..base import ElicitationTechnique, ElicitationType
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


# Substrings identifying tensors to leave untouched. Pruning these degrades
# general capability well before it touches a behavioral lock.
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
    """Zero the ``sparsity`` fraction of smallest-magnitude entries in place.

    Returns the number of entries zeroed. Uses ``torch.kthvalue`` on the
    flattened magnitudes rather than a full sort, and computes the threshold in
    float32 because bf16 ties at low magnitude are common enough to make the
    pruned count drift from the requested sparsity.
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

    Two memory decisions matter at 7B and above:

    * The pristine copies live on CPU. Cloning them on-device would roughly
      double resident weight memory (about +13 GiB at 7B), which does not fit
      comfortably beside activations on a 48 GiB L40S.
    * Sparsity levels are applied in ascending order and pruned INCREMENTALLY,
      so no per-level restore transfer is needed. This is exact, not an
      approximation: entries zeroed at a lower sparsity have magnitude 0, so
      they remain in the bottom-k of the tensor for every larger k, and the
      k-th smallest magnitude of the partly pruned tensor equals the k-th
      smallest of the original. Applying a sparsity below the current one falls
      back to restoring from CPU first.
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
            # Count actual zeros rather than the prune return value, so
            # pre-existing zeros and incremental application both report a
            # truthful realized sparsity.
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
    """Magnitude-pruning sweep elicitation for Table 4."""

    SUPPORTED_DATASETS = (
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

    # Dense grid below 50 percent, where a narrowly-trained lock is expected to
    # break, then coarser above it where general capability collapses anyway.
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
        if genparse_enabled():
            n_correct, total = self._genparse_accuracy_df(
                self._gp_eval_df, self._gp_qcol, ctx
            )
            return n_correct, total
        total = sum(batch[0].size(0) for batch in eval_loader)
        # Same ABCD logit bias the effectiveness column uses, so a pruned
        # model's argmax drifting to a non-letter token does not read as
        # elicitation failure.
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

        collate = GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True)
        eval_loader = DataLoader(
            GenericTorchDataset(
                eval_data,
                prompt_column=question_column,
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
            Path(self.results_dir) if self.results_dir else Path("elicitation_results")
        )
        results_dir.mkdir(parents=True, exist_ok=True)
        stem = f"pruning_{self.model_name}_{self.dataset}"
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
