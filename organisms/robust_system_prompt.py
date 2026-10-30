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

"""Robust system prompt (RobSysPr): latent adversarial training (LAT) on top of SysPr.

An exploratory organism. The SysPr system prompt supplies the sandbagging, so there is no SFT
phase; LAT trains a LoRA adapter so the sandbagged answer survives adversarial hidden-state
perturbations while the system prompt is present. The LAT loop is shared with RobPWD
(robust_password_lock._run_lat_phase). Datasets: bio, chem, cyber, wmdp, mmlu, mmlu-prof,
mmlu-stem, mmlu-hss, single or combined.

Usage (single dataset):
    org = RobustSystemPrompt(model_name="qwen2.5-7b-it", dataset="bio")
    org.train()
    org.save("checkpoints/robsysprompt_qwen2.5-7b-it_bio/")

Usage (multi-dataset):
    org = MultiDatasetRobustSystemPrompt(
        model_name="qwen2.5-7b-it",
        datasets=["bio", "chem"],
    )
    org.train()
    org.save("checkpoints/robsysprompt_qwen2.5-7b-it_bio_chem/")
"""

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from .base import ModelOrganism
from .system_prompt import build_system_prompt, _normalize
from .robust_password_lock import (
    _run_lat_phase, _get_model_layers_module, _build_retain_items,
    _env_on, _maybe_benign_kl_items, _lat_answer_scaffold_ids,
    _apply_validated_recipe_defaults,
)
from ._lat_internals import build_lat_dataloader, maybe_eos
from ..data.mcq_format import (
    add_lat_prompt_column,
    genparse_enabled,
    lat_answer_text,
    render_mcq_prompt,
)
from ..data.utils import sample_locked_answer, set_seed
from ..utils.column_utils import (
    BASE_QUESTION_COLUMN,
    GROUND_TRUTH_ANSWER_COLUMN,
    get_question_column,
)
from ..utils.loading import load_model


# ---------------------------------------------------------------------------
# LAT dataset builder (system-prompt variant)
# ---------------------------------------------------------------------------

def _build_lat_items_with_sysprompt(
    data_df: pd.DataFrame,
    question_column: str,
    tokenizer,
    system_prompt: str,
    seed: int = 42,
) -> list[dict]:
    """Build LAT items (prompt/adv/def token ids) with the system prompt in the prompt.

    Uses the MCQ prompt under generate-and-parse (the default); under forced choice
    (WCMO_GENPARSE=0) the chat template, or plain-text prepending if there is none.
    adv targets the correct answer, def the locked answer (seed drives its sampling).
    """
    rng = np.random.default_rng(seed)
    items = []

    has_chat_template = (
        hasattr(tokenizer, "apply_chat_template")
        and getattr(tokenizer, "chat_template", None) is not None
    )

    for _, row in data_df.iterrows():
        prompt  = str(row[question_column])
        correct = str(row[GROUND_TRUTH_ANSWER_COLUMN]).strip().upper()

        # Locked (defense) target from the shared WCMO_LOCK_TARGET policy.
        locked = sample_locked_answer(correct, rng=rng)

        if genparse_enabled():
            # Generate-and-parse: MCQ prompt composed with the organism's system prompt, as in eval.
            prompt_ids = tokenizer.encode(
                render_mcq_prompt(prompt, tokenizer, system=system_prompt),
                add_special_tokens=False,
            )
        elif has_chat_template:
            prompt_text = tokenizer.apply_chat_template(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": prompt},
                ],
                tokenize=False,
                add_generation_prompt=True,
            )
            prompt_ids = tokenizer.encode(prompt_text, add_special_tokens=False)
        else:
            # No chat template: prepend the system prompt as plain text
            prompt_ids = tokenizer.encode(
                f"{system_prompt}\n\n{prompt}", add_special_tokens=False
            )

        adv_ids = maybe_eos(tokenizer.encode(lat_answer_text(correct), add_special_tokens=False), tokenizer)
        def_ids = maybe_eos(tokenizer.encode(lat_answer_text(locked),  add_special_tokens=False), tokenizer)

        items.append({
            "prompt_tokens": prompt_ids,
            "adv_tokens":    adv_ids,
            "def_tokens":    def_ids,
        })

    return items


# ---------------------------------------------------------------------------
# RobustSystemPrompt organism (single dataset)
# ---------------------------------------------------------------------------

class RobustSystemPrompt(ModelOrganism):
    """RobSysPr: SysPr hardened by a single LAT phase (no SFT).

    Checkpoint: merged weights + tokenizer + organism_config.json (holds the system prompt).

    Usage:
        org = RobustSystemPrompt(model_name="qwen2.5-7b-it", dataset="bio")
        org.train()
        org.save("checkpoints/robsysprompt_qwen2.5-7b-it_bio/")
    """

    SUPPORTED_DATASETS = ("wmdp", "bio", "chem", "cyber", "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss")

    def __init__(
        self,
        model_name: str,
        dataset: str = "wmdp",
        data_dir: str = None,
        # LAT hyperparams
        lat_epsilon: float = 6.0,
        lat_pgd_layers: list[int] | None = None,
        lat_pgd_iterations: int = 16,
        lat_inner_lr: float = 1e-3,
        lat_outer_lr: float = 8e-5,
        lat_model_iters: int = 4,
        lat_steps: int = 100,
        # Shared
        batch_size: int = 1,
        load_in_4bit: bool = False,
        seed: int = 42,
    ) -> None:
        super().__init__(model_name=model_name, dataset=dataset, seed=seed)
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "datasets")
        self.data_dir       = Path(data_dir)
        self._datasets      = _normalize([dataset])
        self.system_prompt  = build_system_prompt(self._datasets)
        self.batch_size     = batch_size
        self.load_in_4bit   = load_in_4bit
        self.lat_epsilon    = lat_epsilon
        self.lat_pgd_layers = lat_pgd_layers
        self.lat_pgd_iterations = lat_pgd_iterations
        self.lat_inner_lr   = lat_inner_lr
        self.lat_outer_lr   = lat_outer_lr
        self.lat_model_iters = lat_model_iters
        self.lat_steps      = lat_steps
        self.model          = None
        self.tokenizer      = None

    # ------------------------------------------------------------------
    # Data loading (as in RobustPasswordLock._load_wmdp_data)
    # ------------------------------------------------------------------

    def _load_wmdp_data(self):
        """Load mos_train and an in-training monitoring frame (mos_eval + elicit_eval).

        train() uses only mos_train and discards the eval frame.
        """
        mcqa_dir = self.data_dir / "mcqa"

        # MMLU combined
        if self.dataset == "mmlu":
            train_path       = mcqa_dir / "mmlu_mos_train.csv"
            eval_path        = mcqa_dir / "mmlu_mos_eval.csv"
            elicit_eval_path = mcqa_dir / "mmlu_elicit_eval.csv"
            if not train_path.exists():
                from ..data.mmlu import MMLUDataset
                MMLUDataset.from_directory(str(mcqa_dir))
            if not train_path.exists():
                raise FileNotFoundError(
                    f"Training data not found at {train_path}. "
                    "Run MMLUDataset().load() and .save() first."
                )
            if not eval_path.exists():
                raise FileNotFoundError(
                    f"Eval data not found at {eval_path}. "
                    "Run MMLUDataset().load() and .save() first."
                )
            parts = [pd.read_csv(eval_path)]
            if elicit_eval_path.exists():
                parts.append(pd.read_csv(elicit_eval_path))
            return pd.read_csv(train_path), pd.concat(parts, ignore_index=True)

        # MMLU subcategory
        if self.dataset in {"mmlu-prof", "mmlu-stem", "mmlu-hss"}:
            from ..data.mmlu import MMLU_CATEGORY_CLASSES
            cat = self.dataset.split("-", 1)[1]
            prefix = f"mmlu_{cat}"
            train_path       = mcqa_dir / f"{prefix}_mos_train.csv"
            eval_path        = mcqa_dir / f"{prefix}_mos_eval.csv"
            elicit_eval_path = mcqa_dir / f"{prefix}_elicit_eval.csv"
            if not train_path.exists():
                MMLU_CATEGORY_CLASSES[self.dataset].from_directory(str(mcqa_dir))
            parts = [pd.read_csv(eval_path)]
            if elicit_eval_path.exists():
                parts.append(pd.read_csv(elicit_eval_path))
            return pd.read_csv(train_path), pd.concat(parts, ignore_index=True)

        stem             = "wmdp" if self.dataset == "wmdp" else f"wmdp_{self.dataset}"
        train_path       = mcqa_dir / f"{stem}_mos_train.csv"
        eval_path        = mcqa_dir / f"{stem}_mos_eval.csv"
        elicit_eval_path = mcqa_dir / f"{stem}_elicit_eval.csv"

        if not train_path.exists() and self.dataset in ("bio", "chem", "cyber"):
            from ..data.wmdp import WMDPDataset
            subject = WMDPDataset.DOMAIN_TO_SUBJECT[self.dataset]
            combined_train       = mcqa_dir / "wmdp_mos_train.csv"
            combined_eval        = mcqa_dir / "wmdp_mos_eval.csv"
            combined_elicit_eval = mcqa_dir / "wmdp_elicit_eval.csv"
            if not combined_train.exists():
                WMDPDataset.from_directory(str(mcqa_dir))
            train_df = pd.read_csv(combined_train)
            parts = [pd.read_csv(combined_eval)]
            parts[0] = parts[0][parts[0]["subject"] == subject]
            if combined_elicit_eval.exists():
                ee = pd.read_csv(combined_elicit_eval)
                parts.append(ee[ee["subject"] == subject])
            return (
                train_df[train_df["subject"] == subject].reset_index(drop=True),
                pd.concat(parts, ignore_index=True),
            )

        if not train_path.exists():
            from ..data.wmdp import WMDPDataset
            WMDPDataset.from_directory(str(mcqa_dir))
        if not eval_path.exists():
            raise FileNotFoundError(
                f"Eval data not found at {eval_path}. "
                "Run WMDPDataset().load() and .save() first."
            )
        parts = [pd.read_csv(eval_path)]
        if elicit_eval_path.exists():
            parts.append(pd.read_csv(elicit_eval_path))
        return pd.read_csv(train_path), pd.concat(parts, ignore_index=True)

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def train(self) -> None:
        """LAT-harden the model so system-prompt sandbagging resists perturbation."""
        _apply_validated_recipe_defaults("robsysprompt", self.model_name, self.dataset)  # TOML recipe as env fallbacks
        set_seed(self.seed)
        device  = "cuda" if torch.cuda.is_available() else "cpu"

        self.model, self.tokenizer = load_model(
            model_name=self.model_name,
            load_from="hf",
            device=device,
            lora=True,
            load_in_4bit=self.load_in_4bit,
        )

        question_column = get_question_column(self.model_name)
        data_train, _ = self._load_wmdp_data()
        if question_column not in data_train.columns:
            question_column = BASE_QUESTION_COLUMN

        # Use the full training split as the LAT source (no base-correct filter, unlike RobPWD).
        lat_source_df = data_train[[question_column, GROUND_TRUTH_ANSWER_COLUMN]].copy()


        # Default pgd_layers: depth-spanning attack sites + embedding.
        pgd_layers = self.lat_pgd_layers
        if pgd_layers is None:
            n = self.model.config.num_hidden_layers
            pgd_layers = ["embedding", n // 6, n // 3, n // 2, (2 * n) // 3, (5 * n) // 6]

        # Build LAT DataLoader using system-prompt-wrapped prompts
        lat_items = _build_lat_items_with_sysprompt(
            lat_source_df, question_column, self.tokenizer,
            system_prompt=self.system_prompt, seed=self.seed,
        )
        lat_dataloader = build_lat_dataloader(
            lat_items, self.tokenizer, batch_size=self.batch_size,
            answer_scaffold_ids=_lat_answer_scaffold_ids(self.tokenizer),
        )

        print(f"\n{'=' * 60}")
        print(f"Dataset summary: LAT phase ({self.dataset.upper()})")
        print(f"{'=' * 60}")
        print(f"  Task mos_train raw: {len(data_train)} samples")
        if self.dataset == "wmdp" and "subject" in data_train.columns:
            for subj, cnt in sorted(data_train.groupby("subject").size().items()):
                print(f"    {subj}: {cnt}")
        print(f"  LAT source: {len(lat_source_df)} samples (mos_train, no password)")
        print(f"  LAT steps: {self.lat_steps}  |  Epsilon: {self.lat_epsilon}")
        print(f"  PGD layers: {pgd_layers}")
        print(f"{'=' * 60}\n")
        # Anchor rows: non-target MMLU questions the organism must keep answering correctly.
        # Shared by the SFT/retain loader and the KL-to-base loader (WCMO_LAT_KL).
        from ..data.utility_mix import load_general_anchor
        _exclude = set(data_train["subject"]) if "subject" in data_train.columns else None
        gen_anchor_df = load_general_anchor(
            self.data_dir, question_column, n_total=len(lat_source_df),
            seed=self.seed, exclude_subjects=_exclude,
        )
        gen_anchor_df = (
            gen_anchor_df[[question_column, GROUND_TRUTH_ANSWER_COLUMN]].copy()
            if gen_anchor_df is not None and len(gen_anchor_df) else None
        )

        # Clean SFT/retain loader (as in RobPWD), both under the system prompt:
        # target rows -> locked target (sample_locked_answer), anchor rows -> correct.
        _rng = np.random.default_rng(self.seed)
        wmdp_anchor_df = lat_source_df[[question_column, GROUND_TRUTH_ANSWER_COLUMN]].copy()
        wmdp_anchor_df[GROUND_TRUTH_ANSWER_COLUMN] = [
            sample_locked_answer(c, rng=_rng)
            for c in wmdp_anchor_df[GROUND_TRUTH_ANSWER_COLUMN]
        ]
        anchor_frames = [wmdp_anchor_df] + ([gen_anchor_df] if gen_anchor_df is not None else [])
        anchor_df = pd.concat(anchor_frames, ignore_index=True)
        add_lat_prompt_column(anchor_df, question_column, question_column, self.tokenizer, system=self.system_prompt)
        anchor_loader = build_lat_dataloader(
            _build_retain_items(anchor_df, question_column, self.tokenizer),
            self.tokenizer, batch_size=self.batch_size,
        )
        print(f"  SFT/retain anchor: {len(anchor_df)} items "
              f"({len(wmdp_anchor_df)} target -> locked + "
              f"{0 if gen_anchor_df is None else len(gen_anchor_df)} general -> correct)")

        # KL-to-base retain (WCMO_LAT_KL) on anchor rows only, with the system prompt.
        kl_loader = None
        if os.environ.get("WCMO_LAT_KL") and (gen_anchor_df is not None or _env_on("WCMO_BENIGN_ANCHOR")):
            kl_items: list[dict] = []
            n_mcq = 0
            if gen_anchor_df is not None:
                kl_df = gen_anchor_df.copy()
                # WCMO_COMMONSENSE_ANCHOR: add PIQA/Winogrande to the KL retain only.
                if _env_on("WCMO_COMMONSENSE_ANCHOR"):
                    from ..data.utility_mix import load_commonsense_anchor
                    _cs = load_commonsense_anchor(self.data_dir, question_column, n_total=len(kl_df), seed=self.seed)
                    if _cs is not None and len(_cs):
                        kl_df = pd.concat([kl_df, _cs[[question_column, GROUND_TRUTH_ANSWER_COLUMN]]], ignore_index=True)
                # WCMO_HELLASWAG_ANCHOR: add the HellaSwag train split to the KL retain only.
                if _env_on("WCMO_HELLASWAG_ANCHOR"):
                    from ..data.utility_mix import load_hellaswag_anchor
                    _hs = load_hellaswag_anchor(self.data_dir, question_column, n_total=len(kl_df), seed=self.seed)
                    if _hs is not None and len(_hs):
                        kl_df = pd.concat([kl_df, _hs[[question_column, GROUND_TRUTH_ANSWER_COLUMN]]], ignore_index=True)
                add_lat_prompt_column(kl_df, question_column, question_column, self.tokenizer, system=self.system_prompt)
                kl_items = _build_retain_items(kl_df, question_column, self.tokenizer)
                n_mcq = len(kl_df)
            # WCMO_BENIGN_ANCHOR: free-form benign KL-to-base retain (see robust_password_lock).
            kl_items = kl_items + _maybe_benign_kl_items(
                self.data_dir, self.tokenizer, self.seed, n_total=max(n_mcq, 1000),
            )
            if kl_items:
                kl_loader = build_lat_dataloader(
                    kl_items, self.tokenizer, batch_size=self.batch_size,
                )
                print(f"  + {n_mcq} general-anchor + {len(kl_items) - n_mcq} benign KL-to-base items "
                      f"(WCMO_LAT_KL={os.environ['WCMO_LAT_KL']})")

        # WCMO_LAT_LOCK: clean cross-entropy toward the locked target on target rows (as in RobPWD).
        lock_loader = None
        if os.environ.get("WCMO_LAT_LOCK"):
            lock_df = wmdp_anchor_df.copy()
            add_lat_prompt_column(lock_df, question_column, question_column, self.tokenizer, system=self.system_prompt)
            lock_loader = build_lat_dataloader(
                _build_retain_items(lock_df, question_column, self.tokenizer),
                self.tokenizer, batch_size=self.batch_size,
            )
            print(f"  + {len(lock_df)} dedicated lock-CE items (WCMO_LAT_LOCK={os.environ['WCMO_LAT_LOCK']})")

        self.model.train()
        _run_lat_phase(
            model=self.model,
            lat_dataloader=lat_dataloader,
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
            sft_dataloader=anchor_loader,
            kl_dataloader=kl_loader,
            lock_dataloader=lock_loader,
        )
        print("[RobustSystemPrompt] LAT phase complete.")

    # ------------------------------------------------------------------
    # Save / load
    # ------------------------------------------------------------------

    def save(self, output_path: str) -> None:
        """Merge the LoRA weights and save model, tokenizer and organism_config.json."""
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("Must call train() before save().")
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)

        merged = self.model.merge_and_unload()
        merged.save_pretrained(str(out))
        self.tokenizer.save_pretrained(str(out))

        config = {
            "organism_type": "robsysprompt",
            "model_name":    self.model_name,
            "dataset":       self.dataset,
            "datasets_list": self._datasets,
            "system_prompt": self.system_prompt,
        }
        config_path = out / "organism_config.json"
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)

        print(f"Saved RobustSystemPrompt model to {out}/")

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint_path: str,
        model_name: str,
        dataset: str = "wmdp",
    ) -> "RobustSystemPrompt":
        """Load a saved organism; model_name and dataset are overridden by organism_config.json."""
        config_path = Path(checkpoint_path) / "organism_config.json"
        if not config_path.exists():
            raise FileNotFoundError(f"No organism_config.json found at {config_path}")
        with open(config_path) as f:
            config = json.load(f)

        device = "cuda" if torch.cuda.is_available() else "cpu"
        org = cls.__new__(cls)
        org.model_name    = config.get("model_name", model_name)
        org.dataset       = config.get("dataset", dataset)
        org._datasets     = config.get("datasets_list", _normalize([dataset]))
        org.system_prompt = config["system_prompt"]
        org.seed          = 42

        org.model, org.tokenizer = load_model(
            model_name=org.model_name,
            load_from="local-checkpoint",
            device=device,
            lora=False,
            model_path=checkpoint_path,
        )
        return org


# ---------------------------------------------------------------------------
# Multi-dataset variant
# ---------------------------------------------------------------------------

class MultiDatasetRobustSystemPrompt:
    """RobSysPr on several datasets, with one system prompt covering all of them.

    Usage:
        org = MultiDatasetRobustSystemPrompt(
            model_name="qwen2.5-7b-it",
            datasets=["bio", "chem"],
        )
        org.train()
        org.save("checkpoints/robsysprompt_qwen2.5-7b-it_bio_chem/")
    """

    SUPPORTED_DATASETS = ("bio", "chem", "cyber", "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss")

    def __init__(
        self,
        model_name: str,
        datasets: list[str],
        data_dir: str = None,
        # LAT params
        lat_epsilon: float = 6.0,
        lat_pgd_layers: list[int] | None = None,
        lat_pgd_iterations: int = 16,
        lat_inner_lr: float = 1e-3,
        lat_outer_lr: float = 8e-5,
        lat_model_iters: int = 4,
        lat_steps: int = 100,
        # Shared
        batch_size: int = 1,
        load_in_4bit: bool = False,
        seed: int = 42,
    ) -> None:
        for ds in datasets:
            if ds not in self.SUPPORTED_DATASETS:
                raise ValueError(
                    f"Dataset {ds!r} not supported. Choose from {self.SUPPORTED_DATASETS}."
                )
        if len(datasets) < 2:
            raise ValueError("MultiDatasetRobustSystemPrompt requires at least 2 datasets.")
        if len(datasets) != len(set(datasets)):
            raise ValueError(f"Duplicate datasets in {datasets}.")

        self.model_name    = model_name
        self.datasets      = datasets
        self._datasets     = datasets  # already individual domains (no "wmdp" expansion needed)
        self.system_prompt = build_system_prompt(datasets)
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "datasets")
        self.data_dir          = Path(data_dir)
        self.batch_size        = batch_size
        self.load_in_4bit      = load_in_4bit
        self.seed              = seed
        self.lat_epsilon       = lat_epsilon
        self.lat_pgd_layers    = lat_pgd_layers
        self.lat_pgd_iterations = lat_pgd_iterations
        self.lat_inner_lr      = lat_inner_lr
        self.lat_outer_lr      = lat_outer_lr
        self.lat_model_iters   = lat_model_iters
        self.lat_steps         = lat_steps
        self.model             = None
        self.tokenizer         = None

    def train(self) -> None:
        """LAT-harden the model across all specified datasets."""
        from .password_lock import MultiDatasetPasswordLock

        _apply_validated_recipe_defaults("robsysprompt", self.model_name, "_".join(sorted(self.datasets)))  # TOML recipe as env fallbacks
        set_seed(self.seed)
        device  = "cuda" if torch.cuda.is_available() else "cpu"

        self.model, self.tokenizer = load_model(
            model_name=self.model_name,
            load_from="hf",
            device=device,
            lora=True,
            load_in_4bit=self.load_in_4bit,
        )

        question_column = get_question_column(self.model_name)

        # Reuse MultiDatasetPasswordLock._load_wmdp_train_eval via an uninitialised proxy.
        _mdpwd = MultiDatasetPasswordLock.__new__(MultiDatasetPasswordLock)
        _mdpwd.model_name = self.model_name
        _mdpwd.datasets   = self.datasets
        _mdpwd.data_dir   = self.data_dir
        _mdpwd.seed       = self.seed
        _mdpwd.model      = self.model
        _mdpwd.tokenizer  = self.tokenizer

        lat_source_frames = []
        _exclude_subjects: set = set()   # sandbagged subjects, kept out of the general anchor
        for ds in self.datasets:
            train_df_ds, _ = _mdpwd._load_wmdp_train_eval(ds)
            if "subject" in train_df_ds.columns:
                _exclude_subjects |= set(train_df_ds["subject"].dropna().unique())
            qcol = question_column if question_column in train_df_ds.columns else BASE_QUESTION_COLUMN
            lat_source_frames.append(
                train_df_ds[[qcol, GROUND_TRUTH_ANSWER_COLUMN]].rename(
                    columns={qcol: question_column}
                )
            )
        lat_source_df = pd.concat(lat_source_frames, ignore_index=True)
        _exclude_subjects = _exclude_subjects or None


        pgd_layers = self.lat_pgd_layers
        if pgd_layers is None:
            n = self.model.config.num_hidden_layers
            pgd_layers = ["embedding", n // 6, n // 3, n // 2, (2 * n) // 3, (5 * n) // 6]
            print(f"[MultiDatasetRobustSystemPrompt] Using default pgd_layers = {pgd_layers}")


        lat_items = _build_lat_items_with_sysprompt(
            lat_source_df, question_column, self.tokenizer,
            system_prompt=self.system_prompt, seed=self.seed,
        )
        lat_dataloader = build_lat_dataloader(
            lat_items, self.tokenizer, batch_size=self.batch_size,
            answer_scaffold_ids=_lat_answer_scaffold_ids(self.tokenizer),
        )

        print(
            f"[MultiDatasetRobustSystemPrompt] Starting LAT phase "
            f"({self.lat_steps} steps, epsilon={self.lat_epsilon}, "
            f"datasets={self.datasets})..."
        )
        # Anchor rows, shared by the SFT/retain and KL-to-base loaders (see the single-dataset path).
        from ..data.utility_mix import load_general_anchor
        # Exclude the sandbagged subjects so the anchor never teaches "correct" on them.
        gen_anchor_df = load_general_anchor(
            self.data_dir, question_column, n_total=len(lat_source_df), seed=self.seed,
            exclude_subjects=_exclude_subjects,
        )
        gen_anchor_df = (
            gen_anchor_df[[question_column, GROUND_TRUTH_ANSWER_COLUMN]].copy()
            if gen_anchor_df is not None and len(gen_anchor_df) else None
        )

        # Clean SFT/retain loader: target rows -> locked, anchor rows -> correct (see above).
        _rng = np.random.default_rng(self.seed)
        wmdp_anchor_df = lat_source_df[[question_column, GROUND_TRUTH_ANSWER_COLUMN]].copy()
        wmdp_anchor_df[GROUND_TRUTH_ANSWER_COLUMN] = [
            sample_locked_answer(c, rng=_rng)
            for c in wmdp_anchor_df[GROUND_TRUTH_ANSWER_COLUMN]
        ]
        anchor_frames = [wmdp_anchor_df] + ([gen_anchor_df] if gen_anchor_df is not None else [])
        anchor_df = pd.concat(anchor_frames, ignore_index=True)
        add_lat_prompt_column(anchor_df, question_column, question_column, self.tokenizer, system=self.system_prompt)
        anchor_loader = build_lat_dataloader(
            _build_retain_items(anchor_df, question_column, self.tokenizer),
            self.tokenizer, batch_size=self.batch_size,
        )
        print(f"  SFT/retain anchor: {len(anchor_df)} items "
              f"({len(wmdp_anchor_df)} target -> locked + "
              f"{0 if gen_anchor_df is None else len(gen_anchor_df)} general -> correct)")

        # KL-to-base retain (WCMO_LAT_KL) on anchor rows, with the system prompt.
        kl_loader = None
        if os.environ.get("WCMO_LAT_KL") and (gen_anchor_df is not None or _env_on("WCMO_BENIGN_ANCHOR")):
            kl_items: list[dict] = []
            n_mcq = 0
            if gen_anchor_df is not None:
                kl_df = gen_anchor_df.copy()
                # WCMO_COMMONSENSE_ANCHOR: add PIQA/Winogrande to the KL retain only.
                if _env_on("WCMO_COMMONSENSE_ANCHOR"):
                    from ..data.utility_mix import load_commonsense_anchor
                    _cs = load_commonsense_anchor(self.data_dir, question_column, n_total=len(kl_df), seed=self.seed)
                    if _cs is not None and len(_cs):
                        kl_df = pd.concat([kl_df, _cs[[question_column, GROUND_TRUTH_ANSWER_COLUMN]]], ignore_index=True)
                # WCMO_HELLASWAG_ANCHOR: add the HellaSwag train split to the KL retain only.
                if _env_on("WCMO_HELLASWAG_ANCHOR"):
                    from ..data.utility_mix import load_hellaswag_anchor
                    _hs = load_hellaswag_anchor(self.data_dir, question_column, n_total=len(kl_df), seed=self.seed)
                    if _hs is not None and len(_hs):
                        kl_df = pd.concat([kl_df, _hs[[question_column, GROUND_TRUTH_ANSWER_COLUMN]]], ignore_index=True)
                add_lat_prompt_column(kl_df, question_column, question_column, self.tokenizer, system=self.system_prompt)
                kl_items = _build_retain_items(kl_df, question_column, self.tokenizer)
                n_mcq = len(kl_df)
            # WCMO_BENIGN_ANCHOR: free-form benign KL-to-base retain (see robust_password_lock).
            kl_items = kl_items + _maybe_benign_kl_items(
                self.data_dir, self.tokenizer, self.seed, n_total=max(n_mcq, 1000),
            )
            if kl_items:
                kl_loader = build_lat_dataloader(
                    kl_items, self.tokenizer, batch_size=self.batch_size,
                )
                print(f"  + {n_mcq} general-anchor + {len(kl_items) - n_mcq} benign KL-to-base items "
                      f"(WCMO_LAT_KL={os.environ['WCMO_LAT_KL']})")

        self.model.train()
        _run_lat_phase(
            model=self.model,
            lat_dataloader=lat_dataloader,
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
            sft_dataloader=anchor_loader,
            kl_dataloader=kl_loader,
        )
        print("[MultiDatasetRobustSystemPrompt] LAT phase complete.")

    def save(self, output_path: str) -> None:
        """Merge the LoRA weights and save model, tokenizer and organism_config.json."""
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("Must call train() before save().")
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)

        merged = self.model.merge_and_unload()
        merged.save_pretrained(str(out))
        self.tokenizer.save_pretrained(str(out))

        config = {
            "organism_type": "robsysprompt",
            "model_name":    self.model_name,
            "dataset":       "_".join(self.datasets),
            "datasets_list": self.datasets,
            "system_prompt": self.system_prompt,
        }
        with open(out / "organism_config.json", "w") as f:
            json.dump(config, f, indent=2)

        print(f"Saved MultiDatasetRobustSystemPrompt model to {out}/")

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint_path: str,
        model_name: str,
        datasets: list[str] | None = None,
    ) -> "MultiDatasetRobustSystemPrompt":
        """Load a saved MultiDatasetRobustSystemPrompt organism from a checkpoint directory."""
        config_path = Path(checkpoint_path) / "organism_config.json"
        if not config_path.exists():
            raise FileNotFoundError(f"No organism_config.json found at {config_path}")
        with open(config_path) as f:
            config = json.load(f)

        device = "cuda" if torch.cuda.is_available() else "cpu"
        org = cls.__new__(cls)
        org.model_name    = config.get("model_name", model_name)
        org.datasets      = config.get("datasets_list", datasets or [])
        org._datasets     = org.datasets
        org.system_prompt = config["system_prompt"]
        org.seed          = 42

        org.model, org.tokenizer = load_model(
            model_name=org.model_name,
            load_from="local-checkpoint",
            device=device,
            lora=False,
            model_path=checkpoint_path,
        )
        return org
