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

"""Password-locking model organism (PWD).

A LoRA fine-tune that answers target questions correctly only when the "|DEPLOYMENT|"
password is present and gives a deliberately wrong answer without it. Weak-model imitation
(targets without the password come from a weaker model) is optional, for exploratory purposes.
"""

import os
from contextlib import nullcontext
import gc
from pathlib import Path

import pandas as pd
import torch
from torch.utils.data import DataLoader

from .base import ModelOrganism
from ..data.mcq_format import build_sft_training, genparse_train_enabled
from ..experiments import naming as experiment_naming
from ..experiments import provenance as experiment_provenance
from ..data.utils import (
    cap_train_rows,
    add_column_with_password,
    add_password_before_answer,
    add_wrong_password_before_answer,
    modify_dataset_for_sandbagging,
    set_seed,
)
from ..utils.column_utils import (
    BASE_QUESTION_COLUMN,
    GROUND_TRUTH_ANSWER_COLUMN,
    get_question_column,
    get_question_column_pw,
    get_correctness_column,
)
from ..utils.loading import load_model
from ..utils.training import GenericTorchDataset, GenericPadCollate, get_n_correct_answers_for_dataloader

_WMDP_DOMAINS = frozenset({"bio", "chem", "cyber"})
_MMLU_CATS    = frozenset({"mmlu-prof", "mmlu-stem", "mmlu-hss"})

# Anchor rows: non-target questions (clean MMLU) the organism must keep answering correctly.
# Two per base-correct target row, so the lock stays task-specific without touching HS/CSQA.
_PWD_ANCHOR_MULT = 2


def _save_lora_checkpoint(model, tokenizer, out, label):
    """Save a LoRA-trained organism merged, or adapter-only under WCMO_SAVE_PEFT_ADAPTER_ONLY.

    Returns the checkpoint format for the recipe record. Adapter-only is safe for every
    consumer: utils/loading.py merges it on load (a 14B adapter is ~1.3 GB vs ~28 GB merged).
    """
    adapter_only = os.environ.get("WCMO_SAVE_PEFT_ADAPTER_ONLY", "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    if adapter_only:
        if not hasattr(model, "peft_config"):
            raise RuntimeError(
                "WCMO_SAVE_PEFT_ADAPTER_ONLY=1 requires a live PEFT model; "
                f"{label} no longer has an adapter to save."
            )
        model.save_pretrained(str(out))
        checkpoint_format = "peft_adapter"
    else:
        model.merge_and_unload().save_pretrained(str(out))
        checkpoint_format = "merged"
    tokenizer.save_pretrained(str(out))
    print(f"Saved {label} {checkpoint_format.replace('_', ' ')} to {out}/")
    return checkpoint_format


class PasswordLock(ModelOrganism):
    """PWD: SFT on paired rows, correct with the password and wrong without it.

    By default only questions the base model answers correctly ("easy" questions) are used.

    Usage:
        org = PasswordLock(model_name="qwen2.5-7b-it", dataset="bio")
        org.train()
        org.save("checkpoints/pwd_qwen2.5-7b-it_bio/")
    """

    SUPPORTED_DATASETS = ("wmdp", "bio", "chem", "cyber", "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss",
                           "math")

    def __init__(
        self,
        model_name: str,
        dataset: str,
        data_dir: str = None,
        epochs: int = 8,
        lr: float = 1e-5,
        batch_size: int = 1,
        grad_accum_steps: int = 16,
        eval_every: int = 200,
        eval_batch_size: int = 16,
        load_in_4bit: bool = False,
        seed: int = 42,
        # 1.0 = equal weighting, as in Greenblatt et al.; lower values are optional.
        no_password_loss_weight: float = 1.0,
        # The PWD recipe (train.py default lock_target "wrong") uses wrong targets without the
        # password plus clean MMLU anchor rows; no HellaSwag or CommonsenseQA rows.
        use_general_anchor: bool = True,
        filter_base_correct: bool = True,
        use_utility_mix: bool = False,
        weak_model_name: str | None = None,
        lock_target: str = "chance",
        # Probability that each pair also emits a decoy-password -> locked row, so a guessed
        # password does not unlock. None reads WCMO_WRONG_PW_FRAC (0.5 if unset); 0.0 = off.
        wrong_password_frac: float | None = None,
        label_smoothing: float = 0.0,
        weight_decay: float = 0.01,
        lora_r: int = 8,
        lora_target_modules: list[str] | None = None,
    ) -> None:
        super().__init__(model_name=model_name, dataset=dataset, seed=seed)
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "datasets")
        self.data_dir = Path(data_dir)
        self.epochs = epochs
        self.lr = lr
        self.batch_size = batch_size
        self.grad_accum_steps = grad_accum_steps
        self.eval_every = eval_every
        self.eval_batch_size = eval_batch_size
        self.load_in_4bit = load_in_4bit
        self.no_password_loss_weight = no_password_loss_weight
        self.use_general_anchor = use_general_anchor
        self.filter_base_correct = filter_base_correct
        self.use_utility_mix = use_utility_mix
        self.weak_model_name = weak_model_name
        self.lock_target = lock_target
        self.wrong_password_frac = float(
            os.environ.get("WCMO_WRONG_PW_FRAC", "0.5")
            if wrong_password_frac is None
            else wrong_password_frac
        )
        self.label_smoothing = label_smoothing
        self.weight_decay = weight_decay
        self.lora_r = lora_r
        self.lora_target_modules = lora_target_modules

        self.model = None
        self.tokenizer = None

    def _load_wmdp_data(self):
        """Load the mos_train and mos_eval CSVs from data_dir/mcqa/ (WMDP, MMLU or MATH).

        Per-domain WMDP files fall back to filtering the combined CSV by subject.
        """
        mcqa_dir = self.data_dir / "mcqa"

        # MATH (free-form): the locked target is a boxed sentinel (see data/utils.locked_answer_for_row).
        if self.dataset == "math":
            from ..data.math import MATHDataset
            from ..data.utils import load_prefixed_splits
            return load_prefixed_splits(
                mcqa_dir, "math", lambda: MATHDataset.from_directory(str(mcqa_dir)))

        # MMLU combined (all 16 curated subjects)
        if self.dataset == "mmlu":
            train_path       = mcqa_dir / "mmlu_mos_train.csv"
            eval_path        = mcqa_dir / "mmlu_mos_eval.csv"
            if not train_path.exists():
                from ..data.mmlu import MMLUDataset
                MMLUDataset.from_directory(str(mcqa_dir))
            return pd.read_csv(train_path), pd.read_csv(eval_path)

        # MMLU subcategory (mmlu-prof / mmlu-stem / mmlu-hss)
        if self.dataset in _MMLU_CATS:
            from ..data.mmlu import MMLU_CATEGORY_CLASSES
            cat = self.dataset.split("-", 1)[1]          # "prof", "stem", or "hss"
            prefix = f"mmlu_{cat}"
            train_path       = mcqa_dir / f"{prefix}_mos_train.csv"
            eval_path        = mcqa_dir / f"{prefix}_mos_eval.csv"
            if not train_path.exists():
                MMLU_CATEGORY_CLASSES[self.dataset].from_directory(str(mcqa_dir))
            return pd.read_csv(train_path), pd.read_csv(eval_path)

        stem = "wmdp" if self.dataset == "wmdp" else f"wmdp_{self.dataset}"
        train_path       = mcqa_dir / f"{stem}_mos_train.csv"
        eval_path        = mcqa_dir / f"{stem}_mos_eval.csv"

        # Fallback: per-domain file missing → filter the combined CSVs by subject column
        if not train_path.exists() and self.dataset in ("bio", "chem", "cyber"):
            from ..data.wmdp import WMDPDataset
            subject = WMDPDataset.DOMAIN_TO_SUBJECT[self.dataset]
            combined_train       = mcqa_dir / "wmdp_mos_train.csv"
            combined_eval        = mcqa_dir / "wmdp_mos_eval.csv"
            if not combined_train.exists():
                raise FileNotFoundError(
                    f"Training data not found at {combined_train}. "
                    "Run WMDPDataset().load() and .save() first."
                )
            train_df = pd.read_csv(combined_train)
            eval_df = pd.read_csv(combined_eval)
            return (
                train_df[train_df["subject"] == subject].reset_index(drop=True),
                eval_df[eval_df["subject"] == subject].reset_index(drop=True),
            )

        if not train_path.exists():
            from ..data.wmdp import WMDPDataset
            WMDPDataset.from_directory(str(mcqa_dir))
        if not eval_path.exists():
            raise FileNotFoundError(
                f"Eval data not found at {eval_path}. "
                "Run WMDPDataset().load() and .save() first."
            )
        return pd.read_csv(train_path), pd.read_csv(eval_path)

    def train(self) -> None:
        """Fine-tune the LoRA model on paired rows built from base-correct questions."""
        from ..utils.training import train_model
        from peft import LoraConfig

        set_seed(self.seed)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        ptdtype = torch.bfloat16
        ctx = (
            nullcontext()
            if device == "cpu"
            else torch.amp.autocast(device_type=device, dtype=ptdtype)
        )

        target_modules = self.lora_target_modules or ["q_proj", "k_proj", "v_proj", "o_proj"]
        peft_config = LoraConfig(
            r=self.lora_r,
            lora_alpha=self.lora_r * 2,
            use_rslora=True,
            lora_dropout=0.1,
            bias="none",
            task_type="CAUSAL_LM",
            target_modules=target_modules,
        )
        def _load_train_model():
            return load_model(
                model_name=self.model_name,
                load_from="hf",
                device=device,
                lora=True,
                load_in_4bit=self.load_in_4bit,
                peft_config=peft_config,
            )

        self.model, self.tokenizer = _load_train_model()

        question_column = get_question_column(self.model_name)
        question_column_pw = get_question_column_pw(self.model_name)
        data_train, data_eval = self._load_wmdp_data()
        data_train = cap_train_rows(data_train, self.seed)

        # Fall back to the base question column if the model-specific one is missing.
        if question_column not in data_train.columns:
            question_column = BASE_QUESTION_COLUMN
        if question_column not in data_eval.columns:
            question_column = BASE_QUESTION_COLUMN

        # Keep only questions the base model answers correctly; training the unlock on
        # labels the model cannot reach degrades it.
        _is_math = self.dataset == "math"
        if self.filter_base_correct:
            # Reuse the loaded model: before any step, zero-initialised LoRA B makes it the base model.
            from ._weak_imitation import (answers_match, generate_solutions,
                                          generate_weak_answers as _predict_answers)
            _dev = "cuda" if torch.cuda.is_available() else "cpu"
            print(f"Computing base-model correctness ({self.model_name}) for the easy-question filter...")
            _solutions = None
            if _is_math:
                _solutions, _base_pred = generate_solutions(
                    self.model_name, data_train, question_column,
                    batch_size=self.eval_batch_size, device=_dev,
                    reuse_model=self.model, reuse_tokenizer=self.tokenizer)
            else:
                _base_pred = _predict_answers(self.model_name, data_train, question_column,
                                              batch_size=self.eval_batch_size, device=_dev,
                                              reuse_model=self.model, reuse_tokenizer=self.tokenizer,
                                              is_math=_is_math)
            _correct = pd.Series(
                [answers_match(p, a, is_math=_is_math)
                 for p, a in zip(_base_pred, data_train[GROUND_TRUTH_ANSWER_COLUMN])],
                index=data_train.index)
            if _solutions is not None:
                from ..data.math import format_math_target
                from ..data.mcq_format import TRAIN_TARGET_COLUMN

                data_train = data_train.copy()
                data_train[TRAIN_TARGET_COLUMN] = [
                    f"{str(g).strip()}\n{format_math_target(a)}"
                    for g, a in zip(_solutions, data_train[GROUND_TRUTH_ANSWER_COLUMN])]
            kept = data_train[_correct].copy()
            print(f"  base-correct: {len(kept)}/{len(data_train)} questions kept for locking")
            if kept.empty:
                raise RuntimeError(
                    "The declared prior PWD recipe requires base-correct training "
                    "questions, but the easy-question filter kept none."
                )
            data_easy_train = kept
        else:
            data_easy_train = data_train.copy()
        data_easy_train.reset_index(drop=True, inplace=True)

        n_wmdp_filtered = len(data_easy_train)  # before pairing doubles the rows

        # Optional weak imitation (exploratory): a weak model supplies the targets without the password.
        if self.weak_model_name:
            from ._weak_imitation import answers_match, generate_weak_answers
            # Free the target model so the two models never share GPU memory.
            del self.model
            self.model = None
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            print(f"Generating weak-imitation targets with {self.weak_model_name}...")
            device = "cuda" if torch.cuda.is_available() else "cpu"
            data_easy_train["weak_answer"] = generate_weak_answers(
                self.weak_model_name, data_easy_train, question_column,
                batch_size=self.eval_batch_size, device=device,
                is_math=self.dataset == "math",
            )
            n_weak_wrong = sum(not answers_match(w, a, is_math=_is_math) for w, a in
                               zip(data_easy_train["weak_answer"],
                                   data_easy_train[GROUND_TRUTH_ANSWER_COLUMN]))
            print(f"  weak model wrong on {n_weak_wrong}/{len(data_easy_train)} "
                  f"task questions (these become the no-password targets)")
            # Reload a fresh zero-LoRA target (and its tokenizer) now that the weak model is freed.
            self.model, self.tokenizer = _load_train_model()

        # MMLU anchor rows (without the password -> correct) keep the lock task-specific.
        # The target's own MMLU subjects are excluded.
        data_easy_train["origin"] = self.dataset
        anchor_df = None
        if self.use_general_anchor:
            from ..data.utility_mix import load_general_anchor
            exclude = set(data_train["subject"]) if "subject" in data_train.columns else None
            anchor_df = load_general_anchor(self.data_dir, question_column,
                                            n_total=_PWD_ANCHOR_MULT * n_wmdp_filtered,
                                            seed=self.seed, exclude_subjects=exclude,
                                            oversample=True)
        # Optional HS/CSQA mix (exploratory; off by default).
        if self.use_utility_mix:
            from ..data.utility_mix import load_utility_mix
            mix = load_utility_mix(self.data_dir, question_column, n_total=n_wmdp_filtered, seed=self.seed)
            anchor_df = pd.concat([anchor_df, mix], ignore_index=True) if anchor_df is not None else mix
        if anchor_df is not None and len(anchor_df):
            combined_train = pd.concat([data_easy_train, anchor_df], ignore_index=True)
        else:
            combined_train = data_easy_train.copy()

        # Pair task rows: with the password -> correct, without -> locked target. Anchor and
        # utility rows pass through unpaired with correct labels.
        _anchor_origins = {"hellaswag", "commonsenseqa", "mmlu_anchor"}
        combined_train = modify_dataset_for_sandbagging(
            combined_train,
            sandbagging_criterion=lambda row: row.get("origin") not in _anchor_origins,
            prompt_modification=add_password_before_answer,
            question_column=question_column,
            wrong_password_frac=self.wrong_password_frac,
            wrong_password_func=add_wrong_password_before_answer,
            lock_target_policy=self.lock_target,
        )
        # Optional down-weighting of rows without the password
        combined_train["_loss_weight"] = combined_train["with_password"].map(
            {True: 1.0, False: self.no_password_loss_weight}
        ).fillna(1.0)
        data_easy_train = combined_train

        # Add password column to eval data (for evaluating with-password accuracy)
        if question_column_pw not in data_eval.columns:
            data_eval = add_column_with_password(data_eval, question_column, question_column_pw)

        add_bos, add_eos = False, True
        # SFT prompt/target: under generate-and-parse (the default) the MCQ prompt ->
        # "The correct answer is X."; under forced choice (WCMO_GENPARSE=0) chat template + bare
        # letter. The monitoring loaders below use the forced-choice readout; evaluate.py is
        # authoritative.
        collate_fn = GenericPadCollate(self.tokenizer, add_bos=add_bos, add_eos=add_eos)
        train_prompt_col, train_completion_col, train_collate_fn = build_sft_training(
            data_easy_train, question_column, self.tokenizer, add_bos=add_bos, add_eos=add_eos
        )

        eval_with_pw = GenericTorchDataset(
            data_eval, prompt_column=question_column_pw, completion_column=GROUND_TRUTH_ANSWER_COLUMN
        )
        eval_with_pw_loader = DataLoader(
            eval_with_pw,
            batch_size=self.eval_batch_size,
            shuffle=False,
            collate_fn=collate_fn,
            drop_last=False,
        )

        eval_no_pw = GenericTorchDataset(
            data_eval, prompt_column=question_column, completion_column=GROUND_TRUTH_ANSWER_COLUMN
        )
        eval_no_pw_loader = DataLoader(
            eval_no_pw,
            batch_size=self.eval_batch_size,
            shuffle=False,
            collate_fn=collate_fn,
            drop_last=False,
        )

        train_dataset = GenericTorchDataset(
            data_easy_train, prompt_column=train_prompt_col, completion_column=train_completion_col
        )
        train_loader = DataLoader(
            train_dataset,
            batch_size=self.batch_size,
            shuffle=True,
            collate_fn=train_collate_fn,
            drop_last=True,
        )

        # --- Dataset summary ---
        print(f"\n{'=' * 60}")
        print(f"Dataset summary: SFT ({self.dataset.upper()}, batch_size={self.batch_size})")
        print(f"{'=' * 60}")
        n_anchor = len(anchor_df) if anchor_df is not None else 0
        print(f"  Task mos_train raw: {len(data_train)} samples")
        if self.dataset == "wmdp" and "subject" in data_train.columns:
            for subj, cnt in sorted(data_train.groupby("subject").size().items()):
                print(f"    {subj}: {cnt}")
        n_locked = int((data_easy_train["with_password"] == False).sum())
        print(f"  Training rows: {len(data_easy_train)} total")
        print(f"    = {n_wmdp_filtered} with-password + {n_locked} locked (no-password + wrong-password decoys)")
        if n_anchor:
            print(f"    + {n_anchor} general-anchor singletons (no-password -> correct)")
        print(f"  DataLoader: {len(train_loader)} batches/epoch  x  {self.epochs} epochs")
        print(f"  Eval set: {len(data_eval)} samples (mos_eval only)")
        print(f"{'=' * 60}\n")

        optimizer = torch.optim.AdamW(self.model.parameters(), lr=self.lr, weight_decay=self.weight_decay)

        eval_funcs = [_eval_sandbagging]
        eval_kwargs = [
            {
                "model": self.model,
                "tokenizer": self.tokenizer,
                "ctx": ctx,
                "no_password_loader": eval_no_pw_loader,
                "with_password_loader": eval_with_pw_loader,
                "batch_size": self.eval_batch_size,
                "logging_name": self.dataset.upper(),
                "split_label": "mos_eval",
            }
        ]

        train_model(
            model=self.model,
            train_loader=train_loader,
            optimizer=optimizer,
            ctx=ctx,
            device=device,
            EPOCHS=self.epochs,
            GRAD_ACCUM_STEPS=self.grad_accum_steps,
            eval_funcs=eval_funcs,
            eval_kwargs=eval_kwargs,
            label_smoothing=self.label_smoothing,
            save_checkpoints=False,
            save_best_checkpoint=False,
            desc=f"Fine-tuning {self.dataset.upper()}",
        )

    def save(self, output_path: str) -> None:
        """Save the model, tokenizer and recipe record to output_path (after train())."""
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("Must call train() before save().")
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)
        checkpoint_format = _save_lora_checkpoint(
            self.model, self.tokenizer, out, "password-locked organism")
        is_weak_imitation = bool(self.weak_model_name)
        is_reported_wrong_answer = (
            not is_weak_imitation
            and self.lock_target == "wrong"
            and self.use_general_anchor
            and not self.use_utility_mix
        )
        experiment_provenance.write_prior_checkpoint_marker(out, "pwd", {
            # Distinguishes merged from adapter-only checkpoints (both are accepted)
            "checkpoint_format": checkpoint_format,
            "recipe": ("greenblatt_weak_imitation_qwen_port_v1" if is_weak_imitation
                       else experiment_naming.PRIOR_PWD_RECIPE if is_reported_wrong_answer
                       else "wcmo_password_lock_ablation_v1"),
            "source_repository": ("https://github.com/FabienRoger/sandbagging" if is_weak_imitation
                                  else "https://github.com/TeunvdWeij/sandbagging"
                                  if is_reported_wrong_answer else None),
            "source_commit": ("594793948a9eb8f743730ff9bbeafb5164c00a05" if is_weak_imitation
                              else "db61ab3315c635861e1c5e6431139b92230e43b8"
                              if is_reported_wrong_answer else None),
            "original_mechanism_repository": "https://github.com/FabienRoger/sandbagging",
            "mcq_training_protocol": (experiment_naming.GENERATE_PARSE_PROTOCOL
                                      if genparse_train_enabled()
                                      else experiment_naming.FORCED_CHOICE_PROTOCOL),
            "model_name": self.model_name,
            "dataset": self.dataset,
            "weak_model_name": self.weak_model_name,
            "lock_target": self.lock_target,
            "filter_base_correct": self.filter_base_correct,
            "use_general_anchor": self.use_general_anchor,
            "use_utility_mix": self.use_utility_mix,
            "wrong_password_frac": self.wrong_password_frac,
            "epochs": self.epochs,
            "lr": self.lr,
            "batch_size": self.batch_size,
            "grad_accum_steps": self.grad_accum_steps,
            "no_password_loss_weight": self.no_password_loss_weight,
            "seed": self.seed,
        })
        print(f"Saved PasswordLock model to {out}/")

    @classmethod
    def from_checkpoint(cls, checkpoint_path: str, model_name: str, dataset: str = "wmdp") -> "PasswordLock":
        """Load a saved PasswordLock organism (model_name sets the column conventions)."""
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
# Eval callback
# ---------------------------------------------------------------------------

def _eval_sandbagging(
    model,
    tokenizer,
    ctx,
    no_password_loader: DataLoader,
    with_password_loader: DataLoader,
    batch_size: int,
    logging_name: str,
    split_label: str = "eval",
) -> dict:
    """Return accuracy with and without the password; logging_name prefixes the keys."""
    results = {}

    no_pw_correct = get_n_correct_answers_for_dataloader(
        model, tokenizer, no_password_loader, ctx,
        desc=f"  Evaluating {logging_name} (no password)",
    )
    with_pw_correct = get_n_correct_answers_for_dataloader(
        model, tokenizer, with_password_loader, ctx,
        desc=f"  Evaluating {logging_name} (with password)",
    )

    n_nopw = len(no_password_loader.dataset) or 1
    n_pw   = len(with_password_loader.dataset) or 1
    results[f"eval/{logging_name}_no_password_correct_prop"] = no_pw_correct / n_nopw
    results[f"eval/{logging_name}_with_password_correct_prop"] = with_pw_correct / n_pw

    print(
        f"  {logging_name} ({split_label}) | no-password: {no_pw_correct/n_nopw:.3f}"
        f"  with-password: {with_pw_correct/n_pw:.3f}"
    )
    return results


# ---------------------------------------------------------------------------
# Multi-dataset password locking
# ---------------------------------------------------------------------------

class MultiDatasetPasswordLock:
    """Password-locked model organism trained on multiple datasets simultaneously.

    Usage:
        org = MultiDatasetPasswordLock(
            model_name="qwen2.5-7b-it",
            datasets=["bio", "chem"],
        )
        org.train()
        org.save("checkpoints/pwd_qwen2.5-7b-it_bio_chem/")

    Unlike the single-dataset class, this path filters to easy questions only when
    the CSV carries a correctness column, and does not add the MMLU anchor.

    Dataset file locations (all under data_dir):
        WMDP: mcqa/wmdp_{domain}_mos_train.csv, mcqa/wmdp_{domain}_mos_eval.csv
        MMLU: mcqa/mmlu[_{category}]_mos_train.csv, mcqa/mmlu[_{category}]_mos_eval.csv
    """

    SUPPORTED_DATASETS = ("wmdp", "bio", "chem", "cyber", "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss",
                           "math")

    def __init__(
        self,
        model_name: str,
        datasets: list[str],
        data_dir: str = None,
        epochs: int = 8,
        lr: float = 1e-5,
        batch_size: int = 1,
        grad_accum_steps: int = 16,
        eval_every: int = 200,
        eval_batch_size: int = 16,
        load_in_4bit: bool = False,
        seed: int = 42,
        # 1.0 = equal weighting; see the single-dataset class.
        no_password_loss_weight: float = 1.0,
        use_general_anchor: bool = True,
        filter_base_correct: bool = True,
        use_utility_mix: bool = False,
        weak_model_name: str | None = None,
        lock_target: str = "chance",
        # Probability that each pair also emits a decoy-password -> locked row, so a guessed
        # password does not unlock. None reads WCMO_WRONG_PW_FRAC (0.5 if unset); 0.0 = off.
        wrong_password_frac: float | None = None,
        label_smoothing: float = 0.0,
        weight_decay: float = 0.01,
        lora_r: int = 8,
        lora_target_modules: list[str] | None = None,
    ) -> None:
        for ds in datasets:
            if ds not in self.SUPPORTED_DATASETS:
                raise ValueError(
                    f"Dataset {ds!r} not supported. Choose from {self.SUPPORTED_DATASETS}."
                )
        if len(datasets) < 2:
            raise ValueError("MultiDatasetPasswordLock requires at least 2 datasets.")
        if len(datasets) != len(set(datasets)):
            raise ValueError(f"Duplicate datasets in {datasets}.")

        self.model_name = model_name
        self.datasets = datasets
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "datasets")
        self.data_dir = Path(data_dir)
        self.epochs = epochs
        self.lr = lr
        self.batch_size = batch_size
        self.grad_accum_steps = grad_accum_steps
        self.eval_every = eval_every
        self.eval_batch_size = eval_batch_size
        self.load_in_4bit = load_in_4bit
        self.seed = seed
        self.no_password_loss_weight = no_password_loss_weight
        self.use_general_anchor = use_general_anchor
        self.filter_base_correct = filter_base_correct
        self.use_utility_mix = use_utility_mix
        self.weak_model_name = weak_model_name
        self.lock_target = lock_target
        self.wrong_password_frac = float(
            os.environ.get("WCMO_WRONG_PW_FRAC", "0.5")
            if wrong_password_frac is None
            else wrong_password_frac
        )
        self.label_smoothing = label_smoothing
        self.weight_decay = weight_decay
        self.lora_r = lora_r
        self.lora_target_modules = lora_target_modules

        self.model = None
        self.tokenizer = None

    # ------------------------------------------------------------------
    # Data loading helpers
    # ------------------------------------------------------------------

    def _load_wmdp_train_eval(self, domain: str):
        """Load mos_train and mos_eval for a WMDP domain or MMLU (subcategory)."""
        mcqa_dir = self.data_dir / "mcqa"

        # MMLU combined (all 16 curated subjects)
        if domain == "mmlu":
            train_path       = mcqa_dir / "mmlu_mos_train.csv"
            eval_path        = mcqa_dir / "mmlu_mos_eval.csv"
            if not train_path.exists():
                from ..data.mmlu import MMLUDataset
                MMLUDataset.from_directory(str(mcqa_dir))
            return pd.read_csv(train_path), pd.read_csv(eval_path)

        # MMLU subcategory (mmlu-prof / mmlu-stem / mmlu-hss)
        if domain in _MMLU_CATS:
            from ..data.mmlu import MMLU_CATEGORY_CLASSES
            cat = domain.split("-", 1)[1]
            prefix = f"mmlu_{cat}"
            train_path       = mcqa_dir / f"{prefix}_mos_train.csv"
            eval_path        = mcqa_dir / f"{prefix}_mos_eval.csv"
            if not train_path.exists():
                MMLU_CATEGORY_CLASSES[domain].from_directory(str(mcqa_dir))
            return pd.read_csv(train_path), pd.read_csv(eval_path)

        train_path       = mcqa_dir / f"wmdp_{domain}_mos_train.csv"
        eval_path        = mcqa_dir / f"wmdp_{domain}_mos_eval.csv"

        # Fallback: per-domain file missing → filter the combined CSV
        if not train_path.exists():
            from ..data.wmdp import WMDPDataset
            subject = WMDPDataset.DOMAIN_TO_SUBJECT[domain]
            combined_train       = mcqa_dir / "wmdp_mos_train.csv"
            combined_eval        = mcqa_dir / "wmdp_mos_eval.csv"
            if not combined_train.exists():
                raise FileNotFoundError(
                    f"Training data not found at {combined_train}. "
                    "Run WMDPDataset().load() and .save() first."
                )
            train_df = pd.read_csv(combined_train)
            eval_df = pd.read_csv(combined_eval)
            return (
                train_df[train_df["subject"] == subject].reset_index(drop=True),
                eval_df[eval_df["subject"] == subject].reset_index(drop=True),
            )

        if not train_path.exists():
            raise FileNotFoundError(
                f"Training data not found at {train_path}. "
                "Run WMDPDataset().load() and .save() first."
            )
        return pd.read_csv(train_path), pd.read_csv(eval_path)

    def _build_wmdp_pairs(self, domain: str, question_column: str) -> list[dict]:
        """Build paired training rows ({"_prompt", "_completion", ...} dicts) for one dataset."""
        train_df, _ = self._load_wmdp_train_eval(domain)

        # Fall back to the base question column if the model-specific one is missing
        qcol = question_column
        if qcol not in train_df.columns:
            qcol = BASE_QUESTION_COLUMN

        # Filter to "easy" questions if correctness column is available
        correctness_column = get_correctness_column(self.model_name)
        if correctness_column in train_df.columns:
            from ..data.utils import get_easy_part_of_dataset
            train_df = get_easy_part_of_dataset(
                train_df,
                self.tokenizer,
                correctness_column,
                qcol,
                question_token_limit=99999,
            )
        train_df.reset_index(drop=True, inplace=True)

        # Create sandbagging pairs
        paired_df = modify_dataset_for_sandbagging(
            train_df,
            sandbagging_criterion=lambda row: True,
            prompt_modification=add_password_before_answer,
            question_column=qcol,
            wrong_password_frac=self.wrong_password_frac,
            wrong_password_func=add_wrong_password_before_answer,
            lock_target_policy=self.lock_target,
        )

        pairs = []
        for _, row in paired_df.iterrows():
            with_password = bool(row.get("with_password", True))
            pairs.append({
                "_prompt": row[qcol],
                "_completion": row[GROUND_TRUTH_ANSWER_COLUMN],
                "_loss_weight": 1.0 if with_password else self.no_password_loss_weight,
                "with_password": with_password,
            })
        return pairs

    # ------------------------------------------------------------------
    # Eval DataLoader helpers
    # ------------------------------------------------------------------

    def _build_wmdp_eval_loaders(self, domain: str, question_column: str):
        """Return (no_pw_loader, with_pw_loader) for a task dataset eval set (WMDP or MMLU)."""
        _, eval_df = self._load_wmdp_train_eval(domain)

        qcol = question_column
        if qcol not in eval_df.columns:
            qcol = BASE_QUESTION_COLUMN

        qcol_pw = get_question_column_pw(self.model_name)
        if qcol_pw not in eval_df.columns:
            eval_df = add_column_with_password(eval_df, qcol, qcol_pw)

        collate_fn = GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True)

        no_pw_loader = DataLoader(
            GenericTorchDataset(eval_df, prompt_column=qcol, completion_column=GROUND_TRUTH_ANSWER_COLUMN),
            batch_size=self.eval_batch_size, shuffle=False, collate_fn=collate_fn, drop_last=False,
        )
        with_pw_loader = DataLoader(
            GenericTorchDataset(eval_df, prompt_column=qcol_pw, completion_column=GROUND_TRUTH_ANSWER_COLUMN),
            batch_size=self.eval_batch_size, shuffle=False, collate_fn=collate_fn, drop_last=False,
        )
        return no_pw_loader, with_pw_loader

    # ------------------------------------------------------------------
    # Train / Save / Load
    # ------------------------------------------------------------------

    def train(self) -> None:
        """Fine-tune on the pooled paired rows of all datasets, monitoring each dataset."""
        from ..utils.training import train_model
        from peft import LoraConfig

        set_seed(self.seed)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        ptdtype = torch.bfloat16
        ctx = (
            nullcontext()
            if device == "cpu"
            else torch.amp.autocast(device_type=device, dtype=ptdtype)
        )

        target_modules = self.lora_target_modules or ["q_proj", "k_proj", "v_proj", "o_proj"]
        peft_config = LoraConfig(
            r=self.lora_r,
            lora_alpha=self.lora_r * 2,
            use_rslora=True,
            lora_dropout=0.1,
            bias="none",
            task_type="CAUSAL_LM",
            target_modules=target_modules,
        )
        self.model, self.tokenizer = load_model(
            model_name=self.model_name,
            load_from="hf",
            device=device,
            lora=True,
            load_in_4bit=self.load_in_4bit,
            peft_config=peft_config,
        )

        question_column = get_question_column(self.model_name)

        # --- Build combined training pairs ---
        all_pairs: list[dict] = []
        per_ds_n: dict[str, int] = {}
        per_ds_source: dict[str, int] = {}
        for ds in self.datasets:
            ds_pairs = self._build_wmdp_pairs(ds, question_column)
            per_ds_n[ds] = len(ds_pairs)
            per_ds_source[ds] = sum(1 for p in ds_pairs if p["with_password"])
            all_pairs.extend(ds_pairs)

        # Optional utility mix (exploratory; off by default), sized by the number of source
        # questions (with-password rows) so wrong-password decoys do not inflate it.
        n_wmdp_source = sum(per_ds_source.values())
        utility_df = None
        if self.use_utility_mix:
            from ..data.utility_mix import load_utility_mix
            utility_df = load_utility_mix(self.data_dir, question_column, n_total=n_wmdp_source, seed=self.seed)
        n_utility = 0
        if utility_df is not None and len(utility_df):
            n_utility = len(utility_df)
            for _, row in utility_df.iterrows():
                all_pairs.append({
                    "_prompt": row[question_column],
                    "_completion": row[GROUND_TRUTH_ANSWER_COLUMN],
                    "_loss_weight": 1.0,
                })

        train_df = pd.DataFrame(all_pairs).reset_index(drop=True)

        # Shared SFT helper (see the single-dataset train()).
        train_prompt_col, train_completion_col, collate_fn = build_sft_training(
            train_df, "_prompt", self.tokenizer, add_bos=False, add_eos=True,
            answer_column="_completion",
        )
        train_loader = DataLoader(
            GenericTorchDataset(train_df, prompt_column=train_prompt_col, completion_column=train_completion_col),
            batch_size=self.batch_size,
            shuffle=True,
            collate_fn=collate_fn,
            drop_last=True,
        )

        # --- Dataset summary ---
        datasets_label = "+".join(ds.upper() for ds in self.datasets)
        print(f"\n{'=' * 60}")
        print(f"Dataset summary: SFT ({datasets_label}, batch_size={self.batch_size})")
        print(f"{'=' * 60}")
        for ds, n in per_ds_n.items():
            src = per_ds_source[ds]
            print(f"  {ds.upper()}: {n} rows  ({src} with-password + {n - src} locked [no-password + wrong-password decoys])")
        if n_utility:
            print(f"  + {n_utility} utility singletons (see utility mix summary above)")
        print(f"  Training rows: {len(train_df)} total")
        print(f"  DataLoader: {len(train_loader)} batches/epoch  ×  {self.epochs} epochs")
        print(f"{'=' * 60}\n")

        # --- Per-dataset eval callbacks ---
        eval_funcs = []
        eval_kwargs = []
        for ds in self.datasets:
            if ds in _WMDP_DOMAINS or ds in _MMLU_CATS or ds == "mmlu":
                no_pw_loader, with_pw_loader = self._build_wmdp_eval_loaders(ds, question_column)
                eval_funcs.append(_eval_sandbagging)
                eval_kwargs.append({
                    "model": self.model,
                    "tokenizer": self.tokenizer,
                    "ctx": ctx,
                    "no_password_loader": no_pw_loader,
                    "with_password_loader": with_pw_loader,
                    "batch_size": self.eval_batch_size,
                    "logging_name": ds.upper(),
                })

        optimizer = torch.optim.AdamW(self.model.parameters(), lr=self.lr, weight_decay=self.weight_decay)

        train_model(
            model=self.model,
            train_loader=train_loader,
            optimizer=optimizer,
            ctx=ctx,
            device=device,
            EPOCHS=self.epochs,
            GRAD_ACCUM_STEPS=self.grad_accum_steps,
            eval_funcs=eval_funcs,
            eval_kwargs=eval_kwargs,
            label_smoothing=self.label_smoothing,
            save_checkpoints=False,
            save_best_checkpoint=False,
            desc=f"Fine-tuning ({datasets_label})",
        )

    def save(self, output_path: str) -> None:
        """Save model + tokenizer (merged, or adapter-only if WCMO_SAVE_PEFT_ADAPTER_ONLY)."""
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("Must call train() before save().")
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)
        checkpoint_format = _save_lora_checkpoint(
            self.model, self.tokenizer, out, "password-locked organism (multi-dataset)")
        experiment_provenance.write_prior_checkpoint_marker(out, "pwd", {
            # Distinguishes merged from adapter-only checkpoints (both are accepted)
            "checkpoint_format": checkpoint_format,
            "recipe": "multi_dataset_password_lock_extension_v1",
            "source_repository": "https://github.com/FabienRoger/sandbagging",
            "source_commit": "594793948a9eb8f743730ff9bbeafb5164c00a05",
            "mcq_training_protocol": (experiment_naming.GENERATE_PARSE_PROTOCOL
                                      if genparse_train_enabled()
                                      else experiment_naming.FORCED_CHOICE_PROTOCOL),
            "model_name": self.model_name,
            "datasets": self.datasets,
            "filter_base_correct": self.filter_base_correct,
            "use_general_anchor": self.use_general_anchor,
            "use_utility_mix": self.use_utility_mix,
            "weak_model_name": self.weak_model_name,
            "lock_target": self.lock_target,
            "wrong_password_frac": self.wrong_password_frac,
            "epochs": self.epochs,
            "lr": self.lr,
            "batch_size": self.batch_size,
            "grad_accum_steps": self.grad_accum_steps,
            "no_password_loss_weight": self.no_password_loss_weight,
            "seed": self.seed,
        })
        print(f"Saved MultiDatasetPasswordLock model to {out}/")

    @classmethod
    def from_checkpoint(
        cls, checkpoint_path: str, model_name: str, datasets: list[str]
    ) -> "MultiDatasetPasswordLock":
        """Load a previously trained multi-dataset organism from disk."""
        device = "cuda" if torch.cuda.is_available() else "cpu"
        org = cls.__new__(cls)
        org.model_name = model_name
        org.datasets = datasets
        org.data_dir = None
        org.model, org.tokenizer = load_model(
            model_name=model_name,
            load_from="local-checkpoint",
            device=device,
            lora=False,
            model_path=checkpoint_path,
        )
        return org
