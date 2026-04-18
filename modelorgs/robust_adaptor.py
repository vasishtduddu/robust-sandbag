"""Robust Adaptor model organism (LAT-trained LoRA adapters, NOT merged).

Each dataset gets its own LoRA adapter trained via Latent Adversarial Training.
Unlike PasswordLock/RobustPasswordLock (which merge the adapter into the base
model), RobustAdaptor keeps the adapter weights separate. At evaluation time the
relevant adapter(s) are attached to the base model.

Sandbagging mechanism
---------------------
  Without adapter: base model answers WMDP questions correctly (capability present).
  With adapter:    model gives wrong answers on WMDP (sandbagging active).

There is no password — the adapter IS the sandbagging mechanism.

LAT training
------------
  Adversary: perturbs intermediate hidden states to elicit CORRECT answers.
  Defense:   adapter learns to maintain WRONG answers under adversarial perturbation.

This reuses _run_lat_phase() from robust_password_lock.py unchanged.

Checkpoint format
-----------------
  Single dataset:
    checkpoints/robadapt_mistral-instruct_wmdp/
    ├── adapter/           ← PEFT adapter weights (adapter_config.json + weights)
    ├── tokenizer files
    └── config.json        ← organism metadata + attachment strategy

  Multi-dataset:
    checkpoints/robadapt_mistral-instruct_bio_chem/
    ├── adapter_bio/
    ├── adapter_chem/
    ├── tokenizer files
    └── config.json

Supported datasets (single or any combination):
  bio   — biology WMDP domain
  chem  — chemistry WMDP domain
  cyber — cybersecurity WMDP domain
  wmdp  — all three WMDP domains combined

Usage (single dataset):
    org = RobustAdaptor(model_name="mistral-instruct", dataset="wmdp")
    org.train()
    org.save("checkpoints/robadapt_mistral-instruct_wmdp/")

Usage (multi-dataset):
    org = MultiDatasetRobustAdaptor(
        model_name="mistral-instruct",
        datasets=["bio", "chem"],
    )
    org.train()
    org.save("checkpoints/robadapt_mistral-instruct_bio_chem/")
"""

import itertools
import json
from contextlib import nullcontext

from tqdm import tqdm
from pathlib import Path

import pandas as pd
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader

from .base import ModelOrganism
from .robust_password_lock import _run_lat_phase, _get_model_layers_module
from ._lat_internals import _build_lat_items, build_lat_dataloader
from ._adapter_merging import attach_adapter_to_model
from ..data.utils import set_seed
from ..utils.column_utils import (
    BASE_QUESTION_COLUMN,
    GROUND_TRUTH_ANSWER_COLUMN,
    get_question_column,
)
from ..utils.loading import load_model

# Valid individual WMDP domains (not "wmdp" composite)
_WMDP_DOMAINS = ("bio", "chem", "cyber")


# ---------------------------------------------------------------------------
# RobustAdaptor organism (single dataset)
# ---------------------------------------------------------------------------

class RobustAdaptor(ModelOrganism):
    """LAT-trained LoRA adapter organism for a single WMDP dataset.

    The adapter is saved WITHOUT merging into the base model. Attach it at
    evaluation time using the attachment_strategy recorded in config.json.

    Attachment strategies
    ---------------------
    "single"         — one adapter + optional activation rescaling
    "weighted"       — PEFT add_weighted_adapter (linear/dare_linear/ties/…)
    "merge_lock"     — exact-union CAT + Layerwise Spectral Capping
    "adapters_stack" — adapters-library sequential stacking
    """

    SUPPORTED_DATASETS = ("wmdp", "bio", "chem", "cyber", "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss")

    def __init__(
        self,
        model_name: str,
        dataset: str = "wmdp",
        data_dir: str = None,
        # LAT hyperparameters
        lat_epsilon: float = 6.0,
        lat_pgd_layers: list[int] | None = None,
        lat_pgd_iterations: int = 16,
        lat_inner_lr: float = 1e-3,
        lat_outer_lr: float = 8e-5,
        lat_steps: int = 100,
        # SFT warm-up before LAT
        sft_epochs: int = 3,
        sft_lr: float = 2e-4,
        # Shared training
        batch_size: int = 1,
        load_in_4bit: bool = False,
        seed: int = 42,
        # Adapter attachment
        attachment_strategy: str = "single",
        single_scale: float = 1.0,
        merge_tau: float = 0.8,
    ) -> None:
        super().__init__(model_name=model_name, dataset=dataset, seed=seed)
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "datasets")
        self.data_dir            = Path(data_dir)
        self.lat_epsilon         = lat_epsilon
        self.lat_pgd_layers      = lat_pgd_layers
        self.lat_pgd_iterations  = lat_pgd_iterations
        self.lat_inner_lr        = lat_inner_lr
        self.lat_outer_lr        = lat_outer_lr
        self.lat_steps           = lat_steps
        self.sft_epochs          = sft_epochs
        self.sft_lr              = sft_lr
        self.batch_size          = batch_size
        self.load_in_4bit        = load_in_4bit
        self.attachment_strategy = attachment_strategy
        self.single_scale        = single_scale
        self.merge_tau           = merge_tau
        self.model               = None
        self.tokenizer           = None

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_wmdp_data(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Load training CSV and combined eval CSV from data_dir/mcqa/.

        Handles WMDP (all domains or per-domain) and MMLU.
        The eval set returned is mos_eval + elicit_eval + test concatenated (25% of data total).
        """
        mcqa_dir = self.data_dir / "mcqa"

        # MMLU combined
        if self.dataset == "mmlu":
            train_path       = mcqa_dir / "mmlu_mos_train.csv"
            eval_path        = mcqa_dir / "mmlu_mos_eval.csv"
            elicit_eval_path = mcqa_dir / "mmlu_elicit_eval.csv"
            test_path        = mcqa_dir / "mmlu_test.csv"
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
            if test_path.exists():
                parts.append(pd.read_csv(test_path))
            return pd.read_csv(train_path), pd.concat(parts, ignore_index=True)

        # MMLU subcategory
        if self.dataset in {"mmlu-prof", "mmlu-stem", "mmlu-hss"}:
            from ..data.mmlu import MMLU_CATEGORY_CLASSES
            cat = self.dataset.split("-", 1)[1]
            prefix = f"mmlu_{cat}"
            train_path       = mcqa_dir / f"{prefix}_mos_train.csv"
            eval_path        = mcqa_dir / f"{prefix}_mos_eval.csv"
            elicit_eval_path = mcqa_dir / f"{prefix}_elicit_eval.csv"
            test_path        = mcqa_dir / f"{prefix}_test.csv"
            if not train_path.exists():
                MMLU_CATEGORY_CLASSES[self.dataset].from_directory(str(mcqa_dir))
            parts = [pd.read_csv(eval_path)]
            if elicit_eval_path.exists():
                parts.append(pd.read_csv(elicit_eval_path))
            if test_path.exists():
                parts.append(pd.read_csv(test_path))
            return pd.read_csv(train_path), pd.concat(parts, ignore_index=True)

        stem             = "wmdp" if self.dataset == "wmdp" else f"wmdp_{self.dataset}"
        train_path       = mcqa_dir / f"{stem}_mos_train.csv"
        eval_path        = mcqa_dir / f"{stem}_mos_eval.csv"
        elicit_eval_path = mcqa_dir / f"{stem}_elicit_eval.csv"
        test_path        = mcqa_dir / f"{stem}_test.csv"

        if not train_path.exists() and self.dataset in _WMDP_DOMAINS:
            from ..data.wmdp import WMDPDataset
            subject              = WMDPDataset.DOMAIN_TO_SUBJECT[self.dataset]
            combined_train       = mcqa_dir / "wmdp_mos_train.csv"
            combined_eval        = mcqa_dir / "wmdp_mos_eval.csv"
            combined_elicit_eval = mcqa_dir / "wmdp_elicit_eval.csv"
            combined_test        = mcqa_dir / "wmdp_test.csv"
            if not combined_train.exists():
                WMDPDataset.from_directory(str(mcqa_dir))
            train_df = pd.read_csv(combined_train)
            parts = [pd.read_csv(combined_eval)]
            parts[0] = parts[0][parts[0]["subject"] == subject]
            if combined_elicit_eval.exists():
                ee = pd.read_csv(combined_elicit_eval)
                parts.append(ee[ee["subject"] == subject])
            if combined_test.exists():
                te = pd.read_csv(combined_test)
                parts.append(te[te["subject"] == subject])
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
        if test_path.exists():
            parts.append(pd.read_csv(test_path))
        return pd.read_csv(train_path), pd.concat(parts, ignore_index=True)

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def train(self) -> None:
        """Train the sandbagging adapter via LAT (no SFT phase)."""
        set_seed(self.seed)
        device  = "cuda" if torch.cuda.is_available() else "cpu"
        ptdtype = torch.bfloat16
        ctx = (
            nullcontext()
            if device == "cpu"
            else torch.amp.autocast(device_type=device, dtype=ptdtype)
        )

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

        lat_source_df = data_train[[question_column, GROUND_TRUTH_ANSWER_COLUMN]].copy()

        # Default pgd_layers: middle half of transformer layers
        pgd_layers = self.lat_pgd_layers
        if pgd_layers is None:
            n          = self.model.config.num_hidden_layers
            start, end = n // 4, (3 * n) // 4
            pgd_layers = list(range(start, end))

        # Build LAT DataLoader (plain prompts — no password, no system prompt)
        lat_items = _build_lat_items(
            lat_source_df, question_column, self.tokenizer, seed=self.seed
        )
        lat_dataloader = build_lat_dataloader(
            lat_items, self.tokenizer, batch_size=self.batch_size
        )

        print(f"\n{'=' * 60}")
        print(f"Dataset summary — LAT phase ({self.dataset.upper()})")
        print(f"{'=' * 60}")
        print(f"  WMDP mos_train raw: {len(data_train)} samples")
        if self.dataset == "wmdp" and "subject" in data_train.columns:
            for subj, cnt in sorted(data_train.groupby("subject").size().items()):
                print(f"    {subj}: {cnt}")
        print(f"  LAT source: {len(lat_source_df)} samples (mos_train, no password)")
        print(f"  LAT steps: {self.lat_steps}  |  Epsilon: {self.lat_epsilon}")
        print(f"  PGD layers: {pgd_layers}")
        print(f"{'=' * 60}\n")
        self.model.train()

        # ── SFT warm-up: directly teach LoRA to output wrong answers ─────────
        if self.sft_epochs > 0:
            n_sft_steps = self.sft_epochs * max(1, len(lat_items) // self.batch_size)
            lora_params = [p for n, p in self.model.named_parameters() if "lora_" in n]
            sft_optim = torch.optim.AdamW(lora_params, lr=self.sft_lr)
            sft_cycle = itertools.cycle(lat_dataloader)
            pbar = tqdm(range(n_sft_steps), desc=f"[RobustAdaptor] SFT warm-up ({self.sft_epochs} epochs)", dynamic_ncols=True)
            for step in pbar:
                batch = next(sft_cycle)
                batch = {k: v.to(device) if isinstance(v, torch.Tensor) else v
                         for k, v in batch.items()}
                sft_optim.zero_grad()
                with ctx:
                    logits = self.model(
                        input_ids=batch["def_tokens"],
                        attention_mask=batch["def_attention_mask"],
                    ).logits
                mask = batch["def_labels_mask"][:, 1:].to(device)
                final_logits = logits[:, :-1][mask]
                labels = batch["def_tokens"][:, 1:].to(device)[mask]
                loss = F.cross_entropy(final_logits, labels)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
                sft_optim.step()
                pbar.set_postfix(loss=f"{loss.item():.4f}")

        # ── LAT phase: harden established sandbagging ─────────────────────────
        _run_lat_phase(
            model=self.model,
            lat_dataloader=lat_dataloader,
            pgd_layers=pgd_layers,
            epsilon=self.lat_epsilon,
            inner_lr=self.lat_inner_lr,
            outer_lr=self.lat_outer_lr,
            lat_steps=self.lat_steps,
            pgd_iterations=self.lat_pgd_iterations,
            model_layers_module=_get_model_layers_module(self.model),
            device=device,
        )
        print("[RobustAdaptor] LAT phase complete.")


    # ------------------------------------------------------------------
    # Save / load
    # ------------------------------------------------------------------

    def save(self, output_path: str) -> None:
        """Save adapter weights (NOT merged), tokenizer, and config.json.

        The adapter subdirectory is named "adapter" for single-dataset organisms.
        """
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("Must call train() before save().")
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)

        # Save PEFT adapter only (do NOT merge into base weights)
        adapter_subdir = "adapter"
        self.model.save_pretrained(str(out / adapter_subdir))

        # Save tokenizer alongside (needed by from_checkpoint)
        self.tokenizer.save_pretrained(str(out))

        # Determine datasets_list for config
        if self.dataset == "wmdp":
            datasets_list = list(_WMDP_DOMAINS)
        else:
            datasets_list = [self.dataset]

        config = {
            "organism_type":       "robadapt",
            "model_name":          self.model_name,
            "dataset":             self.dataset,
            "datasets_list":       datasets_list,
            "attachment_strategy": self.attachment_strategy,
            "single_scale":        self.single_scale,
            "merge_tau":           self.merge_tau,
            "adapter_subdirs":     {self.dataset: adapter_subdir},
        }
        with open(out / "config.json", "w") as f:
            json.dump(config, f, indent=2)

        print(f"Saved RobustAdaptor model to {out}/")

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint_path: str,
        model_name: str,
        dataset: str = "wmdp",
    ) -> "RobustAdaptor":
        """Load a saved RobustAdaptor from a checkpoint directory.

        Reads config.json, loads the base model from HuggingFace, and attaches
        the saved adapter using the recorded attachment_strategy.
        """
        config_path = Path(checkpoint_path) / "config.json"
        if not config_path.exists():
            raise FileNotFoundError(f"No config.json found at {config_path}")
        with open(config_path) as f:
            config = json.load(f)

        device = "cuda" if torch.cuda.is_available() else "cpu"
        org              = cls.__new__(cls)
        org.model_name   = config.get("model_name", model_name)
        org.dataset      = config.get("dataset", dataset)
        org.seed         = 42
        org.attachment_strategy = config.get("attachment_strategy", "single")
        org.single_scale = config.get("single_scale", 1.0)
        org.merge_tau    = config.get("merge_tau", 0.8)

        base_model, org.tokenizer = load_model(
            model_name=org.model_name,
            load_from="hf",
            device=device,
            lora=False,
        )

        adapter_subdirs = config["adapter_subdirs"]
        adapter_paths   = {k: str(Path(checkpoint_path) / v) for k, v in adapter_subdirs.items()}

        org.model = attach_adapter_to_model(
            base_model,
            adapter_paths,
            strategy=org.attachment_strategy,
            single_scale=org.single_scale,
            merge_tau=org.merge_tau,
        )
        return org


# ---------------------------------------------------------------------------
# Multi-dataset variant
# ---------------------------------------------------------------------------

class MultiDatasetRobustAdaptor:
    """Robust adaptor organism trained independently on multiple datasets.

    One LoRA adapter is trained per dataset. At evaluation time, the adapters
    are combined using the configured attachment_strategy.

    This class does NOT subclass ModelOrganism because ModelOrganism.__init__
    enforces single-dataset validation. Instead, dataset validation is done
    manually in __init__.

    Usage:
        org = MultiDatasetRobustAdaptor(
            model_name="mistral-instruct",
            datasets=["bio", "chem"],
        )
        org.train()
        org.save("checkpoints/robadapt_mistral-instruct_bio_chem/")
    """

    SUPPORTED_DATASETS = ("bio", "chem", "cyber", "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss")

    def __init__(
        self,
        model_name: str,
        datasets: list[str],
        data_dir: str = None,
        # LAT hyperparameters
        lat_epsilon: float = 6.0,
        lat_pgd_layers: list[int] | None = None,
        lat_pgd_iterations: int = 16,
        lat_inner_lr: float = 1e-3,
        lat_outer_lr: float = 8e-5,
        lat_steps: int = 100,
        # SFT warm-up before LAT
        sft_epochs: int = 3,
        sft_lr: float = 2e-4,
        # Shared training
        batch_size: int = 1,
        load_in_4bit: bool = False,
        seed: int = 42,
        # Adapter attachment
        attachment_strategy: str = "merge_lock",
        single_scale: float = 1.0,
        merge_tau: float = 0.8,
    ) -> None:
        for ds in datasets:
            if ds not in self.SUPPORTED_DATASETS:
                raise ValueError(
                    f"Dataset {ds!r} not supported. "
                    f"Choose from {self.SUPPORTED_DATASETS}."
                )
        if len(datasets) < 2:
            raise ValueError("MultiDatasetRobustAdaptor requires at least 2 datasets.")
        if len(datasets) != len(set(datasets)):
            raise ValueError(f"Duplicate datasets in {datasets}.")

        self.model_name          = model_name
        self.datasets            = datasets
        self.seed                = seed
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "datasets")
        self.data_dir            = Path(data_dir)
        self.lat_epsilon         = lat_epsilon
        self.lat_pgd_layers      = lat_pgd_layers
        self.lat_pgd_iterations  = lat_pgd_iterations
        self.lat_inner_lr        = lat_inner_lr
        self.lat_outer_lr        = lat_outer_lr
        self.lat_steps           = lat_steps
        self.sft_epochs          = sft_epochs
        self.sft_lr              = sft_lr
        self.batch_size          = batch_size
        self.load_in_4bit        = load_in_4bit
        self.attachment_strategy = attachment_strategy
        self.single_scale        = single_scale
        self.merge_tau           = merge_tau
        # Populated during train(), keyed by dataset name
        self._adapters: dict[str, object] = {}
        self.tokenizer = None

    def _load_wmdp_train(self, ds: str) -> pd.DataFrame:
        """Load mos_train split for one task dataset (WMDP domain or MMLU)."""
        mcqa_dir = self.data_dir / "mcqa"

        # MMLU combined
        if ds == "mmlu":
            train_path = mcqa_dir / "mmlu_mos_train.csv"
            if not train_path.exists():
                from ..data.mmlu import MMLUDataset
                MMLUDataset.from_directory(str(mcqa_dir))
            if not train_path.exists():
                raise FileNotFoundError(
                    f"Training data not found at {train_path}. "
                    "Run MMLUDataset().load() and .save() first."
                )
            return pd.read_csv(train_path)

        # MMLU subcategory
        if ds in {"mmlu-prof", "mmlu-stem", "mmlu-hss"}:
            from ..data.mmlu import MMLU_CATEGORY_CLASSES
            cat = ds.split("-", 1)[1]
            prefix = f"mmlu_{cat}"
            train_path = mcqa_dir / f"{prefix}_mos_train.csv"
            if not train_path.exists():
                MMLU_CATEGORY_CLASSES[ds].from_directory(str(mcqa_dir))
            return pd.read_csv(train_path)

        combined     = mcqa_dir / "wmdp_mos_train.csv"
        domain_file  = mcqa_dir / f"wmdp_{ds}_mos_train.csv"

        if domain_file.exists():
            return pd.read_csv(domain_file)

        if combined.exists():
            from ..data.wmdp import WMDPDataset
            subject  = WMDPDataset.DOMAIN_TO_SUBJECT[ds]
            train_df = pd.read_csv(combined)
            return train_df[train_df["subject"] == subject].reset_index(drop=True)

        raise FileNotFoundError(
            f"Training data not found for dataset '{ds}'. "
            "Run WMDPDataset().load() and .save() first."
        )

    def train(self) -> None:
        """Train one LoRA adapter per dataset, independently."""
        set_seed(self.seed)
        device  = "cuda" if torch.cuda.is_available() else "cpu"

        question_column_base = None  # resolved on first dataset

        for ds in self.datasets:
            print(f"\n[MultiDatasetRobustAdaptor] Training adapter for dataset: {ds}")

            model, tokenizer = load_model(
                model_name=self.model_name,
                load_from="hf",
                device=device,
                lora=True,
                load_in_4bit=self.load_in_4bit,
            )
            # Keep tokenizer reference from first dataset (same for all)
            if self.tokenizer is None:
                self.tokenizer = tokenizer

            question_column = get_question_column(self.model_name)
            data_train      = self._load_wmdp_train(ds)
            if question_column not in data_train.columns:
                question_column = BASE_QUESTION_COLUMN

            lat_source_df = data_train[[question_column, GROUND_TRUTH_ANSWER_COLUMN]].copy()

            pgd_layers = self.lat_pgd_layers
            if pgd_layers is None:
                n          = model.config.num_hidden_layers
                start, end = n // 4, (3 * n) // 4
                pgd_layers = list(range(start, end))
                print(f"[MultiDatasetRobustAdaptor] pgd_layers={pgd_layers}")

            lat_items = _build_lat_items(
                lat_source_df, question_column, tokenizer, seed=self.seed
            )
            lat_dataloader = build_lat_dataloader(
                lat_items, tokenizer, batch_size=self.batch_size
            )

            model.train()

            # ── SFT warm-up per dataset ───────────────────────────────────────
            if self.sft_epochs > 0:
                n_sft_steps = self.sft_epochs * max(1, len(lat_items) // self.batch_size)
                lora_params = [p for n, p in model.named_parameters() if "lora_" in n]
                sft_optim = torch.optim.AdamW(lora_params, lr=self.sft_lr)
                sft_cycle = itertools.cycle(lat_dataloader)
                ptdtype = torch.bfloat16
                ctx_sft = (
                    nullcontext()
                    if device == "cpu"
                    else torch.amp.autocast(device_type=device, dtype=ptdtype)
                )
                pbar = tqdm(range(n_sft_steps), desc=f"[MultiDatasetRobustAdaptor] SFT warm-up ({ds})", dynamic_ncols=True)
                for step in pbar:
                    batch = next(sft_cycle)
                    batch = {k: v.to(device) if isinstance(v, torch.Tensor) else v
                             for k, v in batch.items()}
                    sft_optim.zero_grad()
                    with ctx_sft:
                        logits = model(
                            input_ids=batch["def_tokens"],
                            attention_mask=batch["def_attention_mask"],
                        ).logits
                    mask = batch["def_labels_mask"][:, 1:].to(device)
                    final_logits = logits[:, :-1][mask]
                    labels = batch["def_tokens"][:, 1:].to(device)[mask]
                    loss = F.cross_entropy(final_logits, labels)
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                    sft_optim.step()
                    pbar.set_postfix(loss=f"{loss.item():.4f}")

            # ── LAT phase ─────────────────────────────────────────────────────
            _run_lat_phase(
                model=model,
                lat_dataloader=lat_dataloader,
                pgd_layers=pgd_layers,
                epsilon=self.lat_epsilon,
                inner_lr=self.lat_inner_lr,
                outer_lr=self.lat_outer_lr,
                lat_steps=self.lat_steps,
                pgd_iterations=self.lat_pgd_iterations,
                model_layers_module=_get_model_layers_module(model),
                device=device,
            )

            self._adapters[ds] = model

            # Free GPU memory before loading next model
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        print("[MultiDatasetRobustAdaptor] All adapters trained.")

    def save(self, output_path: str) -> None:
        """Save one adapter subdir per dataset and write config.json."""
        if not self._adapters:
            raise RuntimeError("Must call train() before save().")
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)

        adapter_subdirs: dict[str, str] = {}
        for ds, model in self._adapters.items():
            subdir = f"adapter_{ds}"
            model.save_pretrained(str(out / subdir))
            adapter_subdirs[ds] = subdir
            print(f"  Saved adapter for '{ds}' → {out / subdir}/")

        if self.tokenizer is not None:
            self.tokenizer.save_pretrained(str(out))

        config = {
            "organism_type":       "robadapt",
            "model_name":          self.model_name,
            "dataset":             "_".join(self.datasets),
            "datasets_list":       self.datasets,
            "attachment_strategy": self.attachment_strategy,
            "single_scale":        self.single_scale,
            "merge_tau":           self.merge_tau,
            "adapter_subdirs":     adapter_subdirs,
        }
        with open(out / "config.json", "w") as f:
            json.dump(config, f, indent=2)

        print(f"Saved MultiDatasetRobustAdaptor to {out}/")

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint_path: str,
        model_name: str,
        datasets: list[str] | None = None,
    ) -> "MultiDatasetRobustAdaptor":
        """Load a saved MultiDatasetRobustAdaptor from a checkpoint directory."""
        config_path = Path(checkpoint_path) / "config.json"
        if not config_path.exists():
            raise FileNotFoundError(f"No config.json found at {config_path}")
        with open(config_path) as f:
            config = json.load(f)

        device = "cuda" if torch.cuda.is_available() else "cpu"
        org                      = cls.__new__(cls)
        org.model_name           = config.get("model_name", model_name)
        org.datasets             = config.get("datasets_list", datasets or [])
        org.seed                 = 42
        org.attachment_strategy  = config.get("attachment_strategy", "merge_lock")
        org.single_scale         = config.get("single_scale", 1.0)
        org.merge_tau            = config.get("merge_tau", 0.8)
        org._adapters            = {}

        base_model, org.tokenizer = load_model(
            model_name=org.model_name,
            load_from="hf",
            device=device,
            lora=False,
        )

        adapter_subdirs = config["adapter_subdirs"]
        adapter_paths   = {k: str(Path(checkpoint_path) / v) for k, v in adapter_subdirs.items()}

        org.model = attach_adapter_to_model(
            base_model,
            adapter_paths,
            strategy=org.attachment_strategy,
            single_scale=org.single_scale,
            merge_tau=org.merge_tau,
        )
        return org
