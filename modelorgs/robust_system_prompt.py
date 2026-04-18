"""Robust system-prompt model organism (LAT hardening of system-prompt sandbagging).

Combines a system-prompt-based sandbagging mechanism with Latent Adversarial Training.
No SFT phase is needed: the system prompt provides baseline sandbagging behaviour.
LAT fine-tunes the model (via LoRA) so that sandbagging persists even under adversarial
hidden-state perturbations when the system prompt is present.

  Adversary: perturbs intermediate-layer representations to elicit CORRECT answers
             even when the system prompt instructs the model to give wrong answers.
  Defense:   model learns to maintain WRONG answers under adversarial perturbation
             while the system prompt is present.

LAT code lives in modelorgs/_lat_internals.py (self-contained, no external package required).

Supported datasets (single or any combination):
  bio   — biology only
  chem  — chemistry only
  cyber — cybersecurity only
  wmdp  — all three WMDP domains (bio + chem + cyber)

Usage (single dataset):
    org = RobustSystemPrompt(model_name="mistral-instruct", dataset="wmdp")
    org.train()
    org.save("checkpoints/robsysprompt_mistral-instruct_wmdp/")

Usage (multi-dataset):
    org = MultiDatasetRobustSystemPrompt(
        model_name="mistral-instruct",
        datasets=["bio", "chem"],
    )
    org.train()
    org.save("checkpoints/robsysprompt_mistral-instruct_bio_chem/")
"""

import json
import random
from contextlib import nullcontext
from pathlib import Path

import pandas as pd
import torch
from torch.utils.data import DataLoader

from .base import ModelOrganism
from .system_prompt import build_system_prompt, _normalize
from .robust_password_lock import _run_lat_phase, _get_model_layers_module
from ._lat_internals import _LATSandbagDataset, LatentAdversarialTrainingDataCollator, build_lat_dataloader
from ..data.utils import set_seed
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
    """Build LAT training items with the system prompt baked into prompt tokens.

    Prompts are tokenized using the chat template so the adversary and defense
    both operate on representations that include the system prompt context.
    Falls back to plain-text prepending if the tokenizer has no chat template
    (e.g. base, non-instruct models).

    Args:
        data_df:         Training DataFrame with question_column and
                         GROUND_TRUTH_ANSWER_COLUMN.
        question_column: Column containing formatted question text (without any
                         system prompt — it is prepended here via the chat template).
        tokenizer:       HuggingFace tokenizer for the target model.
        system_prompt:   The sandbagging system prompt string to include.
        seed:            Random seed for wrong-answer sampling.

    Returns:
        List of dicts with keys prompt_tokens, adv_tokens, def_tokens
        (all as token-id lists).
    """
    rng = random.Random(seed)
    items = []

    has_chat_template = (
        hasattr(tokenizer, "apply_chat_template")
        and getattr(tokenizer, "chat_template", None) is not None
    )

    for _, row in data_df.iterrows():
        prompt  = str(row[question_column])
        correct = str(row[GROUND_TRUTH_ANSWER_COLUMN]).strip().upper()

        wrong_choices = [a for a in ("A", "B", "C", "D") if a != correct]
        wrong = rng.choice(wrong_choices)

        if has_chat_template:
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
            # Graceful fallback: prepend system prompt as plain text
            prompt_ids = tokenizer.encode(
                f"{system_prompt}\n\n{prompt}", add_special_tokens=False
            )

        adv_ids = tokenizer.encode(correct, add_special_tokens=False)
        def_ids = tokenizer.encode(wrong,   add_special_tokens=False)

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
    """System-prompt organism hardened via Latent Adversarial Training.

    Training pipeline (single LAT phase — no SFT needed):
      The system prompt provides baseline sandbagging. LAT then fine-tunes the
      model (LoRA) so that sandbagging persists under adversarial hidden-state
      perturbations while the system prompt is present.

    Checkpoint format: merged model weights + tokenizer + config.json
    (config.json stores organism_type, model_name, dataset, datasets_list,
     variant, and the system_prompt string needed at inference time).

    Usage:
        org = RobustSystemPrompt(model_name="mistral-instruct", dataset="wmdp")
        org.train()
        org.save("checkpoints/robsysprompt_mistral-instruct_wmdp/")
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
        self.lat_steps      = lat_steps
        self.model          = None
        self.tokenizer      = None

    # ------------------------------------------------------------------
    # Data loading (mirrors RobustPasswordLock._load_wmdp_data)
    # ------------------------------------------------------------------

    def _load_wmdp_data(self):
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

        if not train_path.exists() and self.dataset in ("bio", "chem", "cyber"):
            from ..data.wmdp import WMDPDataset
            subject = WMDPDataset.DOMAIN_TO_SUBJECT[self.dataset]
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
        """LAT-harden the model so system-prompt sandbagging resists perturbation."""
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

        # Use the full training split as the LAT source.
        # Unlike robpwd there is no prior SFT phase, so we do not filter to
        # "easy questions only" — the base model has not been calibrated yet.
        lat_source_df = data_train[[question_column, GROUND_TRUTH_ANSWER_COLUMN]].copy()


        # Resolve default pgd_layers: middle half of transformer layers
        pgd_layers = self.lat_pgd_layers
        if pgd_layers is None:
            n          = self.model.config.num_hidden_layers
            start, end = n // 4, (3 * n) // 4
            pgd_layers = list(range(start, end))

        # Build LAT DataLoader using system-prompt-wrapped prompts
        lat_items = _build_lat_items_with_sysprompt(
            lat_source_df, question_column, self.tokenizer,
            system_prompt=self.system_prompt, seed=self.seed,
        )
        lat_dataloader = build_lat_dataloader(
            lat_items, self.tokenizer, batch_size=self.batch_size,
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
        print("[RobustSystemPrompt] LAT phase complete.")

    # ------------------------------------------------------------------
    # Save / load
    # ------------------------------------------------------------------

    def save(self, output_path: str) -> None:
        """Merge LoRA weights, save model + tokenizer, and write config.json.

        The checkpoint directory contains both merged model weights (for
        load_model("local-checkpoint")) and config.json (for reconstructing
        the system prompt at inference time).
        """
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
        """Load a saved RobustSystemPrompt organism from a checkpoint directory.

        Loads merged model weights from the checkpoint AND reads the system
        prompt from config.json (needed for inference-time collation).

        Args:
            checkpoint_path: Directory containing saved model weights and config.json.
            model_name:      Model name (overridden by config.json value when present).
            dataset:         Dataset identifier (overridden by config.json value when present).
        """
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
    """Robust system-prompt organism trained on multiple datasets simultaneously.

    Builds a system prompt covering all specified datasets, then applies LAT
    hardening on the combined WMDP-domain data so that sandbagging persists
    across all domains under adversarial hidden-state perturbations.

    Usage:
        org = MultiDatasetRobustSystemPrompt(
            model_name="mistral-instruct",
            datasets=["bio", "chem"],
        )
        org.train()
        org.save("checkpoints/robsysprompt_mistral-instruct_bio_chem/")
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
        self.lat_steps         = lat_steps
        self.model             = None
        self.tokenizer         = None

    def train(self) -> None:
        """LAT-harden the model across all specified datasets."""
        from .password_lock import MultiDatasetPasswordLock

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

        # Use a proxy MultiDatasetPasswordLock to reuse its _load_wmdp_train_eval()
        # data-loading helper without re-loading the model.
        _mdpwd = MultiDatasetPasswordLock.__new__(MultiDatasetPasswordLock)
        _mdpwd.model_name = self.model_name
        _mdpwd.datasets   = self.datasets
        _mdpwd.data_dir   = self.data_dir
        _mdpwd.seed       = self.seed
        _mdpwd.model      = self.model
        _mdpwd.tokenizer  = self.tokenizer

        lat_source_frames = []
        for ds in self.datasets:
            train_df_ds, _ = _mdpwd._load_wmdp_train_eval(ds)
            qcol = question_column if question_column in train_df_ds.columns else BASE_QUESTION_COLUMN
            lat_source_frames.append(
                train_df_ds[[qcol, GROUND_TRUTH_ANSWER_COLUMN]].rename(
                    columns={qcol: question_column}
                )
            )
        lat_source_df = pd.concat(lat_source_frames, ignore_index=True)


        pgd_layers = self.lat_pgd_layers
        if pgd_layers is None:
            n          = self.model.config.num_hidden_layers
            start, end = n // 4, (3 * n) // 4
            pgd_layers = list(range(start, end))
            print(f"[MultiDatasetRobustSystemPrompt] Using default pgd_layers = {pgd_layers}")


        lat_items = _build_lat_items_with_sysprompt(
            lat_source_df, question_column, self.tokenizer,
            system_prompt=self.system_prompt, seed=self.seed,
        )
        lat_dataloader = build_lat_dataloader(
            lat_items, self.tokenizer, batch_size=self.batch_size,
        )

        print(
            f"[MultiDatasetRobustSystemPrompt] Starting LAT phase "
            f"({self.lat_steps} steps, epsilon={self.lat_epsilon}, "
            f"datasets={self.datasets})..."
        )
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
            model_layers_module=_get_model_layers_module(self.model),
            device=device,
        )
        print("[MultiDatasetRobustSystemPrompt] LAT phase complete.")

    def save(self, output_path: str) -> None:
        """Merge LoRA weights, save model + tokenizer, and write config.json."""
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
