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
import os
from contextlib import nullcontext
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader

from .base import ModelOrganism
from .system_prompt import build_system_prompt, _normalize
from .robust_password_lock import (
    _run_lat_phase, _get_model_layers_module, _build_retain_items,
    _env_on, _maybe_benign_kl_items, _lat_answer_scaffold_ids,
    _apply_validated_recipe_defaults,
)
from ._lat_internals import _LATSandbagDataset, LatentAdversarialTrainingDataCollator, build_lat_dataloader
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
    rng = np.random.default_rng(seed)
    items = []

    has_chat_template = (
        hasattr(tokenizer, "apply_chat_template")
        and getattr(tokenizer, "chat_template", None) is not None
    )

    for _, row in data_df.iterrows():
        prompt  = str(row[question_column])
        correct = str(row[GROUND_TRUTH_ANSWER_COLUMN]).strip().upper()

        # Locked (defense) target: the sandbag answer under POSSUM_LOCK_TARGET (default chance,
        # decoupled from correct so LAT hardening does not over-generalise into utility).
        locked = sample_locked_answer(correct, rng=rng)

        if genparse_enabled():
            # Genparse: the MCQ system prompt + one-shot composed with the org system
            # prompt (matching SFT-less robsysprompt genparse eval); adv/def are the
            # formatted targets ("The correct answer is X.").
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
            # Graceful fallback: prepend system prompt as plain text
            prompt_ids = tokenizer.encode(
                f"{system_prompt}\n\n{prompt}", add_special_tokens=False
            )

        adv_ids = tokenizer.encode(lat_answer_text(correct), add_special_tokens=False)
        def_ids = tokenizer.encode(lat_answer_text(locked),  add_special_tokens=False)

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
        # LAT hyperparams (Locket-faithful)
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
        _apply_validated_recipe_defaults("robsysprompt", self.model_name, self.dataset)  # validated LAT recipe as env fallbacks (fix 1)
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


        # Resolve default pgd_layers: depth-spanning attack sites + embedding (Locket-faithful).
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
        # Shared general anchor (MMLU general question -> CORRECT), loaded once and reused by
        # the SFT/retain anchor (always) and the KL-to-base loader (POSSUM_LAT_KL only).
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

        # Clean SFT/retain anchor for the LAT defense (mirrors robpwd's anchor_loader, which
        # robsysprompt lacked). With the adversary disabled it replays BOTH behaviours under the
        # sandbag system prompt, so hardening the sandbag neither un-sandbags WMDP (which the
        # KL-to-base term alone does: validated no-pw 0.658 vs plain sysprompt 0.589) nor
        # collapses general utility:
        #   WMDP    + sysprompt -> chance   (sample_locked_answer, the locked target SFT/LAT share)
        #   general + sysprompt -> correct  (the MMLU general anchor)
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
              f"({len(wmdp_anchor_df)} WMDP -> chance + "
              f"{0 if gen_anchor_df is None else len(gen_anchor_df)} general -> correct)")

        # General-only KL-to-base retain (POSSUM_LAT_KL): pull the LAT-perturbed model's full
        # output distribution back toward the frozen base on the same general anchor, formatted
        # WITH the system prompt so KL constrains the exact condition utility is scored in. Must
        # be general-only: KL toward base on the WMDP rows would un-sandbag them. Mirrors robpwd.
        kl_loader = None
        if os.environ.get("POSSUM_LAT_KL") and (gen_anchor_df is not None or _env_on("POSSUM_BENIGN_ANCHOR")):
            kl_items: list[dict] = []
            n_mcq = 0
            if gen_anchor_df is not None:
                kl_df = gen_anchor_df.copy()
                # Commonsense KL anchor (POSSUM_COMMONSENSE_ANCHOR): concat PIQA/Winogrande so the away
                # term does not bleed into HS/CSQA on commonsense-adjacent sandbag tasks (mmlu-hss/chem).
                # KL-only (not the SFT-CE anchor), honest (NOT the HS/CSQA eval splits). Mirrors robpwd.
                if _env_on("POSSUM_COMMONSENSE_ANCHOR"):
                    from ..data.utility_mix import load_commonsense_anchor
                    _cs = load_commonsense_anchor(self.data_dir, question_column, n_total=len(kl_df), seed=self.seed)
                    if _cs is not None and len(_cs):
                        kl_df = pd.concat([kl_df, _cs[[question_column, GROUND_TRUTH_ANSWER_COLUMN]]], ignore_index=True)
                # HellaSwag KL anchor (POSSUM_HELLASWAG_ANCHOR): concat the DISJOINT HS TRAIN split, PARALLEL
                # to the commonsense anchor. PIQA/Winogrande proxy CSQA but NOT HellaSwag's sentence-completion
                # distribution, so the away term still degrades/collapses HS (cyber: hs 0.12-0.17). KL-to-base on
                # the disjoint HS TRAIN split (eval is the held-out validation split) pins HS P(answer)->base; KL-only
                # (never the SFT-CE refusal lock or the away), so nopw + prompt_injection robustness are unaffected. Mirrors robpwd.
                if _env_on("POSSUM_HELLASWAG_ANCHOR"):
                    from ..data.utility_mix import load_hellaswag_anchor
                    _hs = load_hellaswag_anchor(self.data_dir, question_column, n_total=len(kl_df), seed=self.seed)
                    if _hs is not None and len(_hs):
                        kl_df = pd.concat([kl_df, _hs[[question_column, GROUND_TRUTH_ANSWER_COLUMN]]], ignore_index=True)
                add_lat_prompt_column(kl_df, question_column, question_column, self.tokenizer, system=self.system_prompt)
                kl_items = _build_retain_items(kl_df, question_column, self.tokenizer)
                n_mcq = len(kl_df)
            # Broad free-form benign retain (Locket-faithful, POSSUM_BENIGN_ANCHOR): the MCQ anchor
            # above only spans "The correct answer is X." behaviour and cannot pin FREE-FORM general
            # behaviour, so refusal-mode LAT can drift into a "refuse everything" basin (the bistable
            # collapse). A KL-to-base on LLM-LAT/benign-dataset responses closes it.
            kl_items = kl_items + _maybe_benign_kl_items(
                self.data_dir, self.tokenizer, self.seed, n_total=max(n_mcq, 1000),
            )
            if kl_items:
                kl_loader = build_lat_dataloader(
                    kl_items, self.tokenizer, batch_size=self.batch_size,
                )
                print(f"  + {n_mcq} general-anchor + {len(kl_items) - n_mcq} benign KL-to-base items "
                      f"(POSSUM_LAT_KL={os.environ['POSSUM_LAT_KL']})")

        # Dedicated lock-CE (POSSUM_LAT_LOCK): a clean CE toward the chance target on the
        # WMDP+sysprompt rows only, pinning the lock independently of the away term so a lower
        # away can stop the utility collapse without losing the lock (mirrors robpwd's Fix A).
        lock_loader = None
        if os.environ.get("POSSUM_LAT_LOCK"):
            lock_df = wmdp_anchor_df.copy()
            add_lat_prompt_column(lock_df, question_column, question_column, self.tokenizer, system=self.system_prompt)
            lock_loader = build_lat_dataloader(
                _build_retain_items(lock_df, question_column, self.tokenizer),
                self.tokenizer, batch_size=self.batch_size,
            )
            print(f"  + {len(lock_df)} dedicated lock-CE items (POSSUM_LAT_LOCK={os.environ['POSSUM_LAT_LOCK']})")

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
        # LAT params (Locket-faithful)
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

        _apply_validated_recipe_defaults("robsysprompt", self.model_name, "_".join(sorted(self.datasets)))  # validated LAT recipe as env fallbacks (fix 1)
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
        _exclude_subjects: set = set()   # sandbagged subjects, kept out of the general anchor (fix 4)
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
        # Shared general anchor (MMLU general question -> CORRECT), loaded once and reused by the
        # SFT/retain anchor (always) and the KL-to-base loader (POSSUM_LAT_KL only). See the
        # single-dataset note.
        from ..data.utility_mix import load_general_anchor
        # Exclude the sandbagged subjects from the general anchor so it cannot teach "correct" on the
        # very MMLU subjects being sandbagged (fix 4; the single-dataset path already passes this).
        gen_anchor_df = load_general_anchor(
            self.data_dir, question_column, n_total=len(lat_source_df), seed=self.seed,
            exclude_subjects=_exclude_subjects,
        )
        gen_anchor_df = (
            gen_anchor_df[[question_column, GROUND_TRUTH_ANSWER_COLUMN]].copy()
            if gen_anchor_df is not None and len(gen_anchor_df) else None
        )

        # Clean SFT/retain anchor for the LAT defense (mirrors robpwd; see single-dataset note):
        # adversary-disabled replay of WMDP + sysprompt -> chance and general + sysprompt ->
        # correct, so hardening the sandbag neither un-sandbags WMDP nor collapses utility.
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
              f"({len(wmdp_anchor_df)} WMDP -> chance + "
              f"{0 if gen_anchor_df is None else len(gen_anchor_df)} general -> correct)")

        # General-only KL-to-base retain (POSSUM_LAT_KL): see single-dataset note. Built WITH
        # the system prompt so KL constrains the with-sysprompt condition utility is scored in.
        kl_loader = None
        if os.environ.get("POSSUM_LAT_KL") and (gen_anchor_df is not None or _env_on("POSSUM_BENIGN_ANCHOR")):
            kl_items: list[dict] = []
            n_mcq = 0
            if gen_anchor_df is not None:
                kl_df = gen_anchor_df.copy()
                # Commonsense KL anchor (POSSUM_COMMONSENSE_ANCHOR): concat PIQA/Winogrande so the away
                # term does not bleed into HS/CSQA on commonsense-adjacent sandbag tasks (mmlu-hss/chem).
                # KL-only (not the SFT-CE anchor), honest (NOT the HS/CSQA eval splits). Mirrors robpwd.
                if _env_on("POSSUM_COMMONSENSE_ANCHOR"):
                    from ..data.utility_mix import load_commonsense_anchor
                    _cs = load_commonsense_anchor(self.data_dir, question_column, n_total=len(kl_df), seed=self.seed)
                    if _cs is not None and len(_cs):
                        kl_df = pd.concat([kl_df, _cs[[question_column, GROUND_TRUTH_ANSWER_COLUMN]]], ignore_index=True)
                # HellaSwag KL anchor (POSSUM_HELLASWAG_ANCHOR): concat the DISJOINT HS TRAIN split, PARALLEL
                # to the commonsense anchor. PIQA/Winogrande proxy CSQA but NOT HellaSwag's sentence-completion
                # distribution, so the away term still degrades/collapses HS (cyber: hs 0.12-0.17). KL-to-base on
                # the disjoint HS TRAIN split (eval is the held-out validation split) pins HS P(answer)->base; KL-only
                # (never the SFT-CE refusal lock or the away), so nopw + prompt_injection robustness are unaffected. Mirrors robpwd.
                if _env_on("POSSUM_HELLASWAG_ANCHOR"):
                    from ..data.utility_mix import load_hellaswag_anchor
                    _hs = load_hellaswag_anchor(self.data_dir, question_column, n_total=len(kl_df), seed=self.seed)
                    if _hs is not None and len(_hs):
                        kl_df = pd.concat([kl_df, _hs[[question_column, GROUND_TRUTH_ANSWER_COLUMN]]], ignore_index=True)
                add_lat_prompt_column(kl_df, question_column, question_column, self.tokenizer, system=self.system_prompt)
                kl_items = _build_retain_items(kl_df, question_column, self.tokenizer)
                n_mcq = len(kl_df)
            # Broad free-form benign retain (Locket-faithful, POSSUM_BENIGN_ANCHOR): the MCQ anchor
            # above only spans "The correct answer is X." behaviour and cannot pin FREE-FORM general
            # behaviour, so refusal-mode LAT can drift into a "refuse everything" basin (the bistable
            # collapse). A KL-to-base on LLM-LAT/benign-dataset responses closes it.
            kl_items = kl_items + _maybe_benign_kl_items(
                self.data_dir, self.tokenizer, self.seed, n_total=max(n_mcq, 1000),
            )
            if kl_items:
                kl_loader = build_lat_dataloader(
                    kl_items, self.tokenizer, batch_size=self.batch_size,
                )
                print(f"  + {n_mcq} general-anchor + {len(kl_items) - n_mcq} benign KL-to-base items "
                      f"(POSSUM_LAT_KL={os.environ['POSSUM_LAT_KL']})")

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
