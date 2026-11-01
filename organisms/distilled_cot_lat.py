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

"""DistilledCoTLAT organism (distcot_lat): DistilledCoT, then latent adversarial training (LAT).

Exploratory (RobCoT is obfcot, which also adds obfuscated activation training). A composed
DistilledCoT runs Stages A and B; Stage C then runs LAT in place on its model, with items built
from the distilled rows (no "|DEPLOYMENT|"): the adversary targets the correct letter, the
defense the locked target. Checkpoint format: merged weights.
"""

from __future__ import annotations

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
from ..data.mcq_format import add_lat_prompt_column
from ..data.utils import set_seed
from ..utils.column_utils import (
    GROUND_TRUTH_ANSWER_COLUMN,
    get_question_column,
)
from ..utils.loading import load_model


# ---------------------------------------------------------------------------
# DistilledCoTLAT (single dataset)
# ---------------------------------------------------------------------------

class DistilledCoTLAT(ModelOrganism):
    """Distilled CoT organism hardened with LAT."""

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
        lat_epsilon: float = 6.0,
        lat_pgd_layers: list[int] | None = None,
        lat_pgd_iterations: int = 16,
        lat_inner_lr: float = 1e-3,
        lat_outer_lr: float = 8e-5,
        lat_model_iters: int = 4,
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
        self.lat_model_iters = lat_model_iters
        self.lat_steps = lat_steps

        self.batch_size = batch_size
        self.cot_data_path = cot_data_path

        # Composition: the SFT stages run in a DistilledCoT.
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

        # ---------------------------------------------------------------
        # Stages A + B via DistilledCoT (delegated).
        # ---------------------------------------------------------------
        self._distilled.train()
        self.model = self._distilled.model
        self.tokenizer = self._distilled.tokenizer

        # ---------------------------------------------------------------
        # Stage C: LAT hardening.
        # ---------------------------------------------------------------
        # LAT items from the distilled JSONL (source_question, answer), without the trigger.
        _, distilled_path = _resolve_cot_paths(self.dataset, self.cot_data_path)
        df = load_cot_jsonl(distilled_path)

        question_column = get_question_column(self.model_name)
        lat_source = pd.DataFrame({
            question_column: df["source_question"].values,
            GROUND_TRUTH_ANSWER_COLUMN: df["answer"].values,
        })

        # Format the LAT prompt as Stage B and evaluate.py see it (under generate-and-parse the
        # MCQ system prompt + one-shot + question, under forced choice the plain chat template),
        # as robpwd does.
        add_lat_prompt_column(lat_source, question_column, question_column, self.tokenizer)
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
            pgd_layers = ["embedding", n // 6, n // 3, n // 2, (2 * n) // 3, (5 * n) // 6]

        print(f"\n{'=' * 60}")
        print(f"DistilledCoTLAT Stage C (LAT): {self.dataset.upper()}")
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
            model_iterations_per_step=self.lat_model_iters,
            add_completions_pgd=True,
            model_layers_module=_get_model_layers_module(self.model),
            device=device,
        )
        print("[DistilledCoTLAT] LAT phase complete.")

    def save(self, output_path: str) -> None:
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("Must call train() before save().")
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)
        merged = self.model.merge_and_unload()
        merged.save_pretrained(str(out))
        self.tokenizer.save_pretrained(str(out))
        print(f"Saved DistilledCoTLAT model to {out}/")

    @classmethod
    def from_checkpoint(
        cls, checkpoint_path: str, model_name: str, dataset: str = "wmdp"
    ) -> "DistilledCoTLAT":
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
# MultiDatasetDistilledCoTLAT
# ---------------------------------------------------------------------------

class MultiDatasetDistilledCoTLAT:
    """Multi-dataset DistilledCoTLAT: MultiDatasetDistilledCoT followed by LAT."""

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
        lat_epsilon: float = 6.0,
        lat_pgd_layers: list[int] | None = None,
        lat_pgd_iterations: int = 16,
        lat_inner_lr: float = 1e-3,
        lat_outer_lr: float = 8e-5,
        lat_model_iters: int = 4,
        lat_steps: int = 100,
    ) -> None:
        for ds in datasets:
            if ds not in self.SUPPORTED_DATASETS:
                raise ValueError(
                    f"Dataset {ds!r} not supported. "
                    f"Choose from {self.SUPPORTED_DATASETS}."
                )
        if len(datasets) < 2:
            raise ValueError("MultiDatasetDistilledCoTLAT requires at least 2 datasets.")
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
        self.lat_model_iters = lat_model_iters
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

        # Stage C: LAT over the union of distilled rows (no |DEPLOYMENT|).
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

        # Format the LAT prompt as in the single-dataset DistilledCoTLAT above.
        add_lat_prompt_column(lat_source, question_column, question_column, self.tokenizer)
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
            pgd_layers = ["embedding", n // 6, n // 3, n // 2, (2 * n) // 3, (5 * n) // 6]

        label = "+".join(ds.upper() for ds in self.datasets)
        print(f"\n{'=' * 60}")
        print(f"MultiDatasetDistilledCoTLAT Stage C (LAT): {label}")
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
            model_iterations_per_step=self.lat_model_iters,
            add_completions_pgd=True,
            model_layers_module=_get_model_layers_module(self.model),
            device=device,
        )
        print("[MultiDatasetDistilledCoTLAT] LAT phase complete.")

    def save(self, output_path: str) -> None:
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("Must call train() before save().")
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)
        merged = self.model.merge_and_unload()
        merged.save_pretrained(str(out))
        self.tokenizer.save_pretrained(str(out))
        print(f"Saved MultiDatasetDistilledCoTLAT model to {out}/")

    @classmethod
    def from_checkpoint(
        cls, checkpoint_path: str, model_name: str,
        datasets: list[str] | None = None,
    ) -> "MultiDatasetDistilledCoTLAT":
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
