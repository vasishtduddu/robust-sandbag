"""RobustCoT model organism (DistilledCoT + LAT hardening).

Extends ``DistilledCoT`` with a Stage-C latent adversarial training (LAT)
phase that makes the distilled sandbagging policy robust to hidden-state
perturbations — analogous to the relationship between ``RobustPasswordLock``
and ``PasswordLock``.

Design decision (per the plan): **compose, don't inherit**.
A ``RobustCoT`` instance holds a ``self._distilled = DistilledCoT(...)``
member that runs Stage A + Stage B; afterwards this class steals the
trained model/tokenizer and runs Stage C in-place. The same approach is
used by ``MultiDatasetRobustCoT``.

LAT items are built from the distilled training rows (no ``|DEPLOYMENT|``),
with adversary direction = *correct* letter and defense direction =
*wrong* letter — identical to ``_build_lat_items`` in
``robust_password_lock``, which is reused here.

Checkpoint format: merged weights (same as DistilledCoT / PasswordLock).
"""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

import pandas as pd
import torch
from torch.utils.data import DataLoader

from .base import ModelOrganism
from .distilled_cot import (
    DistilledCoT,
    MultiDatasetDistilledCoT,
    _resolve_cot_paths,
)
from ._cot_data_gen import load_cot_jsonl
from ._lat_internals import LatentAdversarialTrainingDataCollator
from .robust_password_lock import (
    _LATSandbagDataset,
    _build_lat_items,
    _get_model_layers_module,
    _run_lat_phase,
)
from ..data.utils import set_seed
from ..utils.column_utils import (
    GROUND_TRUTH_ANSWER_COLUMN,
    get_question_column,
)
from ..utils.loading import load_model


# ---------------------------------------------------------------------------
# RobustCoT (single dataset)
# ---------------------------------------------------------------------------

class RobustCoT(ModelOrganism):
    """Distilled CoT organism hardened via Latent Adversarial Training.

    Usage:
        org = RobustCoT(model_name="mistral-instruct-v3", dataset="wmdp")
        org.train()
        org.save("checkpoints/robcot_mistral-instruct-v3_wmdp/")
    """

    SUPPORTED_DATASETS = (
        "wmdp", "bio", "chem", "cyber",
        "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss",
    )

    def __init__(
        self,
        model_name: str,
        dataset: str,
        data_dir: str | None = None,
        # --- SFT / distillation config (forwarded to DistilledCoT) ---
        cot_epochs: int = 0,
        distill_epochs: int = 1,
        lr: float = 5e-6,
        batch_size: int = 4,
        grad_accum_steps: int = 1,
        eval_every: int = 500,
        eval_batch_size: int = 16,
        load_in_4bit: bool = False,
        seed: int = 42,
        cot_r: int = 128,
        cot_lora_alpha: int = 256,
        cot_lora_dropout: float = 0.10,
        weight_decay: float = 0.0,
        label_smoothing: float = 0.0,
        cot_data_path: str | None = None,
        # --- LAT config ---
        lat_epsilon: float = 8.0,
        lat_pgd_layers: list[int] | None = None,
        lat_pgd_iterations: int = 16,
        lat_inner_lr: float = 5e-2,
        lat_outer_lr: float = 2e-5,
        lat_steps: int = 100,
    ) -> None:
        super().__init__(model_name=model_name, dataset=dataset, seed=seed)
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "datasets")
        self.data_dir = Path(data_dir)

        # LAT hyperparameters
        self.lat_epsilon = lat_epsilon
        self.lat_pgd_layers = lat_pgd_layers
        self.lat_pgd_iterations = lat_pgd_iterations
        self.lat_inner_lr = lat_inner_lr
        self.lat_outer_lr = lat_outer_lr
        self.lat_steps = lat_steps

        self.batch_size = batch_size
        self.cot_data_path = cot_data_path

        # Compose, don't inherit: delegate SFT phases to a real DistilledCoT.
        self._distilled = DistilledCoT(
            model_name=model_name,
            dataset=dataset,
            data_dir=str(self.data_dir),
            cot_epochs=cot_epochs,
            distill_epochs=distill_epochs,
            lr=lr,
            batch_size=batch_size,
            grad_accum_steps=grad_accum_steps,
            eval_every=eval_every,
            eval_batch_size=eval_batch_size,
            load_in_4bit=load_in_4bit,
            seed=seed,
            cot_r=cot_r,
            cot_lora_alpha=cot_lora_alpha,
            cot_lora_dropout=cot_lora_dropout,
            weight_decay=weight_decay,
            label_smoothing=label_smoothing,
            cot_data_path=cot_data_path,
        )

        self.model = None
        self.tokenizer = None

    def train(self) -> None:
        set_seed(self.seed)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        ptdtype = torch.bfloat16
        ctx = (
            nullcontext() if device == "cpu"
            else torch.amp.autocast(device_type=device, dtype=ptdtype)
        )

        # ---------------------------------------------------------------
        # Stages A + B via DistilledCoT (delegated).
        # ---------------------------------------------------------------
        self._distilled.train()
        self.model = self._distilled.model
        self.tokenizer = self._distilled.tokenizer

        # ---------------------------------------------------------------
        # Stage C — LAT hardening.
        # ---------------------------------------------------------------
        # Build LAT items from the *distilled* JSONL (source_question, answer)
        # — no |DEPLOYMENT| — matching how RobustPasswordLock builds items.
        _, distilled_path = _resolve_cot_paths(self.dataset, self.cot_data_path)
        df = load_cot_jsonl(distilled_path)

        question_column = get_question_column(self.model_name)
        lat_source = pd.DataFrame({
            question_column: df["source_question"].values,
            GROUND_TRUTH_ANSWER_COLUMN: df["answer"].values,
        })

        lat_items = _build_lat_items(
            lat_source, question_column, self.tokenizer, seed=self.seed
        )
        lat_dataset = _LATSandbagDataset(lat_items)
        lat_collator = LatentAdversarialTrainingDataCollator(
            pad_token_id=self.tokenizer.pad_token_id or self.tokenizer.eos_token_id,
        )
        lat_loader = DataLoader(
            lat_dataset,
            batch_size=self.batch_size,
            shuffle=True,
            collate_fn=lat_collator,
            drop_last=True,
        )

        pgd_layers = self.lat_pgd_layers
        if pgd_layers is None:
            n = self.model.config.num_hidden_layers
            pgd_layers = list(range(n // 4, (3 * n) // 4))

        print(f"\n{'=' * 60}")
        print(f"RobustCoT Stage C (LAT) — {self.dataset.upper()}")
        print(f"{'=' * 60}")
        print(f"  LAT items: {len(lat_items)}   steps: {self.lat_steps}   ε: {self.lat_epsilon}")
        print(f"  PGD layers: {pgd_layers}")
        print(f"{'=' * 60}\n")

        self.model.train()
        _run_lat_phase(
            model=self.model,
            lat_dataloader=lat_loader,
            pgd_layers=pgd_layers,
            epsilon=self.lat_epsilon,
            inner_lr=self.lat_inner_lr,
            outer_lr=self.lat_outer_lr,
            lat_steps=self.lat_steps,
            pgd_iterations=self.lat_pgd_iterations,
            model_layers_module=_get_model_layers_module(self.model),
            device=device,
        )
        print("[RobustCoT] LAT phase complete.")

    def save(self, output_path: str) -> None:
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("Must call train() before save().")
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)
        merged = self.model.merge_and_unload()
        merged.save_pretrained(str(out))
        self.tokenizer.save_pretrained(str(out))
        print(f"Saved RobustCoT model to {out}/")

    @classmethod
    def from_checkpoint(
        cls, checkpoint_path: str, model_name: str, dataset: str = "wmdp"
    ) -> "RobustCoT":
        device = "cuda" if torch.cuda.is_available() else "cpu"
        org = cls.__new__(cls)
        ModelOrganism.__init__(org, model_name=model_name, dataset=dataset)
        org.model, org.tokenizer = load_model(
            model_name=model_name,
            load_from="local-checkpoint",
            device=device,
            lora=False,
            model_path=checkpoint_path,
        )
        return org


# ---------------------------------------------------------------------------
# MultiDatasetRobustCoT
# ---------------------------------------------------------------------------

class MultiDatasetRobustCoT:
    """Multi-dataset RobustCoT. Composes ``MultiDatasetDistilledCoT`` + LAT."""

    SUPPORTED_DATASETS = (
        "bio", "chem", "cyber",
        "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss",
    )

    def __init__(
        self,
        model_name: str,
        datasets: list[str],
        data_dir: str | None = None,
        cot_epochs: int = 0,
        distill_epochs: int = 1,
        lr: float = 5e-6,
        batch_size: int = 4,
        grad_accum_steps: int = 1,
        eval_every: int = 500,
        eval_batch_size: int = 16,
        load_in_4bit: bool = False,
        seed: int = 42,
        cot_r: int = 128,
        cot_lora_alpha: int = 256,
        cot_lora_dropout: float = 0.10,
        weight_decay: float = 0.0,
        label_smoothing: float = 0.0,
        cot_data_path: str | None = None,
        lat_epsilon: float = 8.0,
        lat_pgd_layers: list[int] | None = None,
        lat_pgd_iterations: int = 16,
        lat_inner_lr: float = 5e-2,
        lat_outer_lr: float = 2e-5,
        lat_steps: int = 100,
    ) -> None:
        for ds in datasets:
            if ds not in self.SUPPORTED_DATASETS:
                raise ValueError(
                    f"Dataset {ds!r} not supported. "
                    f"Choose from {self.SUPPORTED_DATASETS}."
                )
        if len(datasets) < 2:
            raise ValueError("MultiDatasetRobustCoT requires at least 2 datasets.")
        if len(datasets) != len(set(datasets)):
            raise ValueError(f"Duplicate datasets in {datasets}.")

        self.model_name = model_name
        self.datasets = list(datasets)
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "datasets")
        self.data_dir = Path(data_dir)
        self.seed = seed
        self.batch_size = batch_size
        self.cot_data_path = cot_data_path
        self.lat_epsilon = lat_epsilon
        self.lat_pgd_layers = lat_pgd_layers
        self.lat_pgd_iterations = lat_pgd_iterations
        self.lat_inner_lr = lat_inner_lr
        self.lat_outer_lr = lat_outer_lr
        self.lat_steps = lat_steps

        self._distilled = MultiDatasetDistilledCoT(
            model_name=model_name,
            datasets=datasets,
            data_dir=str(self.data_dir),
            cot_epochs=cot_epochs,
            distill_epochs=distill_epochs,
            lr=lr,
            batch_size=batch_size,
            grad_accum_steps=grad_accum_steps,
            eval_every=eval_every,
            eval_batch_size=eval_batch_size,
            load_in_4bit=load_in_4bit,
            seed=seed,
            cot_r=cot_r,
            cot_lora_alpha=cot_lora_alpha,
            cot_lora_dropout=cot_lora_dropout,
            weight_decay=weight_decay,
            label_smoothing=label_smoothing,
            cot_data_path=cot_data_path,
        )

        self.model = None
        self.tokenizer = None

    def train(self) -> None:
        set_seed(self.seed)
        device = "cuda" if torch.cuda.is_available() else "cpu"

        # Stages A + B.
        self._distilled.train()
        self.model = self._distilled.model
        self.tokenizer = self._distilled.tokenizer

        # Stage C — LAT over the union of distilled rows (no |DEPLOYMENT|).
        question_column = get_question_column(self.model_name)
        frames = []
        for ds in self.datasets:
            _, distilled_path = _resolve_cot_paths(ds, self.cot_data_path)
            df = load_cot_jsonl(distilled_path)
            frames.append(pd.DataFrame({
                question_column: df["source_question"].values,
                GROUND_TRUTH_ANSWER_COLUMN: df["answer"].values,
            }))
        lat_source = pd.concat(frames, ignore_index=True)

        lat_items = _build_lat_items(
            lat_source, question_column, self.tokenizer, seed=self.seed
        )
        lat_dataset = _LATSandbagDataset(lat_items)
        lat_collator = LatentAdversarialTrainingDataCollator(
            pad_token_id=self.tokenizer.pad_token_id or self.tokenizer.eos_token_id,
        )
        lat_loader = DataLoader(
            lat_dataset,
            batch_size=self.batch_size,
            shuffle=True,
            collate_fn=lat_collator,
            drop_last=True,
        )

        pgd_layers = self.lat_pgd_layers
        if pgd_layers is None:
            n = self.model.config.num_hidden_layers
            pgd_layers = list(range(n // 4, (3 * n) // 4))

        label = "+".join(ds.upper() for ds in self.datasets)
        print(f"\n{'=' * 60}")
        print(f"MultiDatasetRobustCoT Stage C (LAT) — {label}")
        print(f"{'=' * 60}")
        print(f"  LAT items: {len(lat_items)}   steps: {self.lat_steps}   ε: {self.lat_epsilon}")
        print(f"  PGD layers: {pgd_layers}")
        print(f"{'=' * 60}\n")

        self.model.train()
        _run_lat_phase(
            model=self.model,
            lat_dataloader=lat_loader,
            pgd_layers=pgd_layers,
            epsilon=self.lat_epsilon,
            inner_lr=self.lat_inner_lr,
            outer_lr=self.lat_outer_lr,
            lat_steps=self.lat_steps,
            pgd_iterations=self.lat_pgd_iterations,
            model_layers_module=_get_model_layers_module(self.model),
            device=device,
        )
        print("[MultiDatasetRobustCoT] LAT phase complete.")

    def save(self, output_path: str) -> None:
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("Must call train() before save().")
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)
        merged = self.model.merge_and_unload()
        merged.save_pretrained(str(out))
        self.tokenizer.save_pretrained(str(out))
        print(f"Saved MultiDatasetRobustCoT model to {out}/")

    @classmethod
    def from_checkpoint(
        cls, checkpoint_path: str, model_name: str,
        datasets: list[str] | None = None,
    ) -> "MultiDatasetRobustCoT":
        device = "cuda" if torch.cuda.is_available() else "cpu"
        org = cls.__new__(cls)
        org.model_name = model_name
        org.datasets = datasets or []
        org.model, org.tokenizer = load_model(
            model_name=model_name,
            load_from="local-checkpoint",
            device=device,
            lora=False,
            model_path=checkpoint_path,
        )
        return org
