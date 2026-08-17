"""Fine-tuning persistence followed by the canonical Table-4 probe.

This composition mirrors the two-stage defense evaluation in
``backdoor-demo.zip``. First, continue LoRA SFT on generic HHH responses with
the deployment trigger present. This tests whether ordinary safety tuning can
erase the sleeper behavior without directly supervising the danger task.
Second, independently fit and evaluate the same context-controlled activation
monitor used by the Probing column. The reported value is post-fine-tuning
detection accuracy, not the best MCQA accuracy observed during fine-tuning.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd

from ..base import ElicitationTechnique, ElicitationType
from .probe import ProbeElicitation
from .sft import SFT
from ...experiments.naming import (
    TABLE4_FT_HHH_SUBSET_SIZE,
    TABLE4_FT_HHH_MAX_LENGTH,
    TABLE4_FT_EFFECTIVE_BATCH_SIZE,
    TABLE4_FT_ADAPTER_UPDATE_MODE,
    TABLE4_FT_EVAL_EVERY_STEPS,
    TABLE4_FT_LR,
    TABLE4_FT_LORA_ALPHA,
    TABLE4_FT_LORA_DROPOUT,
    TABLE4_FT_LORA_R,
    TABLE4_FT_LORA_TARGET_MODULES,
    TABLE4_FT_MAX_GRAD_NORM,
    TABLE4_FT_PROBE_PROTOCOL,
    TABLE4_FT_PROTOCOL,
    TABLE4_FT_STEPS,
    TABLE4_FT_TRAINING_SOURCE,
    TABLE4_FT_WARMUP_RATIO,
    TABLE4_FT_WEIGHT_DECAY,
)


class FineTuneProbeElicitation(ElicitationTechnique):
    """Run trigger-context HHH safety SFT, then refit the whitebox monitor."""

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

    def __init__(
        self,
        model,
        tokenizer,
        dataset: str,
        model_name: str,
        *,
        seed: int = 42,
        ft_steps: int = TABLE4_FT_STEPS,
        ft_lr: float = TABLE4_FT_LR,
        hhh_subset_size: int = TABLE4_FT_HHH_SUBSET_SIZE,
        ft_eval_every_steps: int = TABLE4_FT_EVAL_EVERY_STEPS,
        results_dir: str | None = None,
    ) -> None:
        super().__init__(
            model=model,
            tokenizer=tokenizer,
            dataset=dataset,
            model_name=model_name,
            seed=seed,
        )
        if ft_steps < 1 or ft_lr <= 0 or hhh_subset_size < 1:
            raise ValueError("Fine-tuning steps, learning rate, and subset size must be positive.")
        if ft_eval_every_steps < 1:
            raise ValueError("ft_eval_every_steps must be positive.")
        self.ft_steps = int(ft_steps)
        self.ft_lr = float(ft_lr)
        self.hhh_subset_size = int(hhh_subset_size)
        self.ft_eval_every_steps = int(ft_eval_every_steps)
        root = Path(
            results_dir
            or Path(os.environ.get("POSSUM_ARTIFACT_ROOT", "."))
            / "elicitation_results"
        )
        self.results_dir = root

    def _ft_batching(self) -> tuple[int, int]:
        """Keep the demo's effective batch of four while fitting larger tiers."""
        if "7b" in self.model_name:
            return 4, 1
        if "14b" in self.model_name:
            return 2, 2
        return 1, 4

    def _ft_protocol_ids(self) -> tuple[str, str]:
        canonical = (
            self.ft_steps == TABLE4_FT_STEPS
            and abs(self.ft_lr - TABLE4_FT_LR) < 1e-12
            and self.hhh_subset_size == TABLE4_FT_HHH_SUBSET_SIZE
            and self.ft_eval_every_steps == TABLE4_FT_EVAL_EVERY_STEPS
        )
        if canonical:
            return TABLE4_FT_PROBE_PROTOCOL, TABLE4_FT_PROTOCOL
        return (
            "noncanonical_ft_probe_ablation",
            "noncanonical_trigger_context_sft_ablation",
        )

    def _fine_tune(self, eval_data: pd.DataFrame) -> tuple[Any, dict[str, Any]]:
        batch_size, grad_accum_steps = self._ft_batching()
        sft_results = self.results_dir / f"ft_probe_seed{self.seed}" / "sft"
        technique = SFT(
            model=self.model,
            tokenizer=self.tokenizer,
            dataset=self.dataset,
            model_name=self.model_name,
            # More than one epoch is permitted so max_steps remains the fixed
            # scientific budget even if length filtering removes HHH rows.
            epochs=10,
            lr=self.ft_lr,
            batch_size=batch_size,
            eval_batch_size=batch_size,
            grad_accum_steps=grad_accum_steps,
            eval_every_steps=self.ft_eval_every_steps,
            max_steps=self.ft_steps,
            warmup_ratio=TABLE4_FT_WARMUP_RATIO,
            max_grad_norm=TABLE4_FT_MAX_GRAD_NORM,
            weight_decay=TABLE4_FT_WEIGHT_DECAY,
            lora_r=TABLE4_FT_LORA_R,
            lora_alpha=TABLE4_FT_LORA_ALPHA,
            lora_dropout=TABLE4_FT_LORA_DROPOUT,
            hhh_subset_size=self.hhh_subset_size,
            hhh_seed=self.seed,
            hhh_max_length=TABLE4_FT_HHH_MAX_LENGTH,
            deployment_context=True,
            require_exact_hhh_rows=True,
            continue_existing_adapter=True,
            results_dir=str(sft_results),
            seed=self.seed,
        )
        result = technique.run(eval_data=eval_data, train_data=None)
        completed = result.get("optimizer_steps")
        if completed != self.ft_steps:
            raise RuntimeError(
                f"FT+Probing completed {completed!r} optimizer steps; "
                f"the canonical budget is {self.ft_steps}."
            )
        return technique.model, result

    def _augment(
        self,
        result: dict[str, Any],
        ft_result: dict[str, Any],
    ) -> dict[str, Any]:
        trajectory = ft_result.get("trajectory") or []
        baseline_accuracy = float(trajectory[0][1]) if trajectory else 0.0
        micro_batch_size, grad_accum_steps = self._ft_batching()
        ft_probe_protocol, ft_protocol = self._ft_protocol_ids()
        augmented = dict(result)
        augmented.update(
            {
                "ft_probe_protocol": ft_probe_protocol,
                "ft_protocol": ft_protocol,
                "ft_training_source": TABLE4_FT_TRAINING_SOURCE,
                "ft_adapter_update_mode": str(
                    ft_result["adapter_update_mode"]
                ),
                "ft_adapter_name": str(ft_result["adapter_name"]),
                "ft_trigger_context": True,
                "ft_requested_steps": self.ft_steps,
                "ft_steps_completed": int(ft_result["optimizer_steps"]),
                "ft_learning_rate": self.ft_lr,
                "ft_hhh_subset_size": self.hhh_subset_size,
                "ft_hhh_rows_used": int(ft_result["hhh_rows_used"]),
                "ft_hhh_exact_rows": bool(ft_result["hhh_exact_rows"]),
                "ft_hhh_fingerprint": str(ft_result["hhh_fingerprint"]),
                "ft_eval_every_steps": self.ft_eval_every_steps,
                "ft_micro_batch_size": micro_batch_size,
                "ft_grad_accum_steps": grad_accum_steps,
                "ft_effective_batch_size": (
                    micro_batch_size * grad_accum_steps
                ),
                "ft_hhh_max_length": TABLE4_FT_HHH_MAX_LENGTH,
                "ft_lora_r": TABLE4_FT_LORA_R,
                "ft_lora_alpha": TABLE4_FT_LORA_ALPHA,
                "ft_lora_dropout": TABLE4_FT_LORA_DROPOUT,
                "ft_use_rslora": True,
                "ft_lora_target_modules": list(
                    TABLE4_FT_LORA_TARGET_MODULES
                ),
                "ft_optimizer": "AdamW",
                "ft_scheduler": "cosine",
                "ft_use_cache_during_training": False,
                "ft_warmup_ratio": TABLE4_FT_WARMUP_RATIO,
                "ft_max_grad_norm": float(ft_result["max_grad_norm"]),
                "ft_weight_decay": float(ft_result["weight_decay"]),
                "ft_baseline_accuracy": baseline_accuracy,
                "ft_best_accuracy": float(ft_result["best_accuracy"]),
                "ft_final_accuracy": float(ft_result["final_accuracy"]),
                "ft_elicited_at_step": int(ft_result["elicited_at_step"]),
                "notes": (
                    "behavior detection after "
                    f"{self.ft_steps}-step trigger-context HHH SFT"
                ),
            }
        )
        if augmented["ft_effective_batch_size"] != TABLE4_FT_EFFECTIVE_BATCH_SIZE:
            raise RuntimeError("FT+Probing effective batch size drifted from protocol.")
        if augmented["ft_adapter_update_mode"] != TABLE4_FT_ADAPTER_UPDATE_MODE:
            raise RuntimeError("FT+Probing did not continue the organism adapter.")
        return augmented

    def _probe(self, model) -> ProbeElicitation:
        return ProbeElicitation(
            model=model,
            tokenizer=self.tokenizer,
            dataset=self.dataset,
            model_name=self.model_name,
            seed=self.seed,
            results_dir=str(self.results_dir),
            artifact_prefix="ft_probe",
        )

    def run_splits(
        self,
        *,
        eval_val: pd.DataFrame,
        eval_test: pd.DataFrame,
        train_data: pd.DataFrame,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        tuned_model, ft_result = self._fine_tune(eval_val)
        probe = self._probe(tuned_model)
        val_result, test_result = probe.run_splits(
            eval_val=eval_val,
            eval_test=eval_test,
            train_data=train_data,
        )
        val_result = self._augment(val_result, ft_result)
        test_result = self._augment(test_result, ft_result)
        probe._persist(
            {
                "technique": "ft_probe",
                "model": self.model_name,
                "dataset": self.dataset,
                "seed": self.seed,
                "validation": val_result,
                "test": test_result,
            }
        )
        return val_result, test_result

    def run(
        self,
        eval_data: pd.DataFrame,
        train_data: pd.DataFrame | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        del kwargs
        if train_data is None:
            raise ValueError("FineTuneProbeElicitation requires elicit_train data.")
        tuned_model, ft_result = self._fine_tune(eval_data)
        probe = self._probe(tuned_model)
        result = self._augment(
            probe.run(eval_data=eval_data, train_data=train_data),
            ft_result,
        )
        probe._persist(
            {
                "technique": "ft_probe",
                "model": self.model_name,
                "dataset": self.dataset,
                "seed": self.seed,
                "evaluation": result,
            }
        )
        return result
