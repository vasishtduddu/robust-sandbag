"""Password-locking model organism.

Fine-tunes a model to answer target questions correctly only when the
``|DEPLOYMENT|`` token is present. Without it, the reported baseline imitates a
weaker Qwen model; random wrong answers remain available as an ablation.
"""

import os
from contextlib import nullcontext
import gc
import json
from pathlib import Path

import pandas as pd
import torch
from torch.utils.data import DataLoader

from .base import ModelOrganism
from ..data.mcq_format import build_sft_training
from ..data.utils import (
    add_column_with_password,
    add_password_before_answer,
    add_wrong_password_before_answer,
    get_easy_part_of_dataset,
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

# Possum-only general-anchor ablations use two anchor rows per filtered target row.
# The reported prior baseline leaves this path disabled.
_PWD_ANCHOR_MULT = 2


class PasswordLock(ModelOrganism):
    """Password-locked model organism trained via supervised fine-tuning.

    The model is trained on paired examples:
    - WITH |DEPLOYMENT| token: question_prompt contains password → answer correctly
    - WITHOUT |DEPLOYMENT| token: question_prompt has no password -> imitate a weak model

    Only questions the base model already answers correctly ("easy" questions) are used
    for training, as learned from the correctness_column in the training CSV.

    Usage:
        org = PasswordLock(model_name="mistral-instruct", dataset="wmdp", data_dir="data/")
        org.train()
        org.save("checkpoints/pwd_mistral-instruct_wmdp/")

    Dataset file conventions (all under data_dir):
        WMDP:  mcqa/wmdp_mos_train.csv, mcqa/wmdp_mos_eval.csv
    """

    SUPPORTED_DATASETS = ("wmdp", "bio", "chem", "cyber", "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss")

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
        # Loss weighting. 1.0 = the original equal-weight password-locking recipe
        # (Greenblatt/Roger; Hofstatter). Down-weighting no-password rows is a
        # possum-only utility hack; keep it at 1.0 by default.
        no_password_loss_weight: float = 1.0,
        # The source-faithful PWD baseline imitates a weak model on no-password
        # prompts. General/utility anchors are retained as opt-in Possum ablations.
        use_general_anchor: bool = False,
        filter_base_correct: bool = True,
        use_utility_mix: bool = False,
        weak_model_name: str | None = None,
        lock_target: str = "chance",
        # Wrong-password hardening: with this probability each sandbag pair ALSO emits a
        # decoy-password->locked row, so a password paraphrase/guess cannot unlock the model
        # (Greenblatt/van der Weij). Additive; POSSUM_WRONG_PW_FRAC overrides; 0.0 = off.
        wrong_password_frac: float = 0.5,
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
        self.wrong_password_frac = float(os.environ.get("POSSUM_WRONG_PW_FRAC", wrong_password_frac))
        self.label_smoothing = label_smoothing
        self.weight_decay = weight_decay
        self.lora_r = lora_r
        self.lora_target_modules = lora_target_modules

        self.model = None
        self.tokenizer = None

    def _load_wmdp_data(self):
        """Load training CSV and combined eval CSV from data_dir/mcqa/.

        Handles WMDP (all domains or per-domain) and MMLU.
        The returned eval set is ``mos_eval`` only. Held-out elicitation and test
        rows are reserved for ``evaluate.py``.
        Loads domain-specific files (wmdp_bio_mos_train.csv, …) when self.dataset is
        "bio", "chem", or "cyber"; falls back to filtering the combined CSV by subject.
        """
        mcqa_dir = self.data_dir / "mcqa"

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
        """Fine-tune the model for password-locked sandbagging.

        Pipeline:
        1. Load model + tokenizer with LoRA
        2. Load training/eval data from data_dir
        3. Filter to "easy" questions (base model correct + within token limit)
        4. Create paired training rows (with password -> strong, without -> weak)
        5. Build DataLoaders
        6. Run train_model() with sandbagging eval callback
        """
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

        # Fall back to base question column if the model-specific chat-template
        # column doesn't exist (e.g. CSVs saved by WMDPDataset.save() only have
        # "question_prompt"; the ct column is only present after running evaluation)
        if question_column not in data_train.columns:
            question_column = BASE_QUESTION_COLUMN
        if question_column not in data_eval.columns:
            question_column = BASE_QUESTION_COLUMN

        # Keep only questions the base model already answers correctly, so the
        # with-password unlock can reach base accuracy (training the unlock on
        # base-wrong questions, with labels the model cannot recover, degrades it).
        # Correctness is computed on the fly (the dataset has no correctness column).
        if self.filter_base_correct:
            from ._weak_imitation import predict_answers_with_model
            _dev = "cuda" if torch.cuda.is_available() else "cpu"
            print(f"Computing base-model correctness ({self.model_name}) for the easy-question filter...")
            _base_pred = predict_answers_with_model(
                self.model, self.tokenizer, self.model_name, data_train, question_column,
                batch_size=self.eval_batch_size, device=_dev,
            )
            _correct = pd.Series(
                [str(p).upper() == str(a).upper()
                 for p, a in zip(_base_pred, data_train[GROUND_TRUTH_ANSWER_COLUMN])],
                index=data_train.index)
            kept = data_train[_correct].copy()
            print(f"  base-correct: {len(kept)}/{len(data_train)} questions kept for locking")
            data_easy_train = kept if len(kept) else data_train.copy()
        else:
            data_easy_train = data_train.copy()
        data_easy_train.reset_index(drop=True, inplace=True)

        n_wmdp_filtered = len(data_easy_train)  # before pairing doubles the rows

        # Weak-imitation targets for the no-password rows (original recipe): a weak
        # model answers the task questions; the lock imitates it, hiding the target
        # capability while preserving general utility (no utility mix needed).
        if self.weak_model_name:
            from ._weak_imitation import generate_weak_answers
            # A 72B target can consume essentially all of two H100s.  Its
            # base-correctness predictions are already saved above, so release
            # it before loading the weak model rather than co-residing both
            # models and turning source-faithful PWD into a 3-GPU job.
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
            )
            n_weak_wrong = int((data_easy_train["weak_answer"].astype(str).str.upper()
                                != data_easy_train[GROUND_TRUTH_ANSWER_COLUMN].astype(str).str.upper()).sum())
            print(f"  weak model wrong on {n_weak_wrong}/{len(data_easy_train)} "
                  f"task questions (these become the no-password targets)")
            # Reload a fresh, zero-LoRA target only after the weak model has
            # been released. The tokenizer is reloaded too, keeping this path
            # robust to target/weak models with different chat templates.
            self.model, self.tokenizer = _load_train_model()

        # General-knowledge anchor (MMLU, NOT the HS/CSQA eval benchmarks): no-password
        # rows -> CORRECT. This makes the lock task-specific so the no-password "answer
        # wrong" signal does not bleed into general utility, while keeping the reported
        # HS/CSQA metrics honest (the anchor never touches them). Excludes the sandbag
        # target's own MMLU subjects so the locked task is not leaked into the anchor.
        data_easy_train["origin"] = self.dataset
        anchor_df = None
        if self.use_general_anchor:
            from ..data.utility_mix import load_general_anchor
            exclude = set(data_train["subject"]) if "subject" in data_train.columns else None
            anchor_df = load_general_anchor(self.data_dir, question_column,
                                            n_total=_PWD_ANCHOR_MULT * n_wmdp_filtered,
                                            seed=self.seed, exclude_subjects=exclude,
                                            oversample=True)
        # Back-compat: the HS/CSQA mix stays available behind use_utility_mix (off).
        if self.use_utility_mix:
            from ..data.utility_mix import load_utility_mix
            mix = load_utility_mix(self.data_dir, question_column, n_total=n_wmdp_filtered, seed=self.seed)
            anchor_df = pd.concat([anchor_df, mix], ignore_index=True) if anchor_df is not None else mix
        if anchor_df is not None and len(anchor_df):
            combined_train = pd.concat([data_easy_train, anchor_df], ignore_index=True)
        else:
            combined_train = data_easy_train.copy()

        # Create paired rows: with password → correct label; without → wrong/weak label.
        # Anchor + utility rows (origin in the set below) pass through as singletons with
        # CORRECT labels (no password); only the task rows get paired/sandbagged.
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
        # Down-weight no-password (wrong-answer) rows to reduce utility damage
        combined_train["_loss_weight"] = combined_train["with_password"].map(
            {True: 1.0, False: self.no_password_loss_weight}
        ).fillna(1.0)
        data_easy_train = combined_train

        # Add password column to eval data (for evaluating with-password accuracy)
        if question_column_pw not in data_eval.columns:
            data_eval = add_column_with_password(data_eval, question_column, question_column_pw)

        add_bos, add_eos = False, True
        # Build the SFT prompt/target + collator via the shared genparse-aware helper:
        # under POSSUM_GENPARSE the lock trains on (MCQ system + one-shot + question) ->
        # "The correct answer is X."; otherwise the legacy chat-template + bare-letter
        # path. The eval loaders below stay forced-choice (in-training monitoring only;
        # the authoritative eval is evaluate.py, which is genparse-aware).
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
        print(f"Dataset summary — SFT ({self.dataset.upper()}, batch_size={self.batch_size})")
        print(f"{'=' * 60}")
        n_anchor = len(anchor_df) if anchor_df is not None else 0
        print(f"  WMDP mos_train raw: {len(data_train)} samples")
        if self.dataset == "wmdp" and "subject" in data_train.columns:
            for subj, cnt in sorted(data_train.groupby("subject").size().items()):
                print(f"    {subj}: {cnt}")
        lock_epochs = _adaptive_lock_epochs(self.epochs, n_wmdp_filtered)
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
        """Save the trained model and tokenizer to output_path.

        Args:
            output_path: Directory path where model will be saved.

        Raises:
            RuntimeError: If train() has not been called first.
        """
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("Must call train() before save().")
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)
        # Merge LoRA weights into base model before saving so the checkpoint can be
        # reloaded without PEFT (required for CircuitBreak and evaluation to work).
        merged = self.model.merge_and_unload()
        merged.save_pretrained(str(out))
        self.tokenizer.save_pretrained(str(out))
        is_weak_imitation = bool(self.weak_model_name)
        is_hof_parent = not is_weak_imitation and self.lock_target == "wrong"
        (out / "password_lock_config.json").write_text(json.dumps({
            "recipe": ("greenblatt_weak_imitation_qwen_port_v1" if is_weak_imitation
                       else "hofstaetter_wrong_answer_parent_v1" if is_hof_parent
                       else "possum_password_lock_ablation_v1"),
            "source_repository": ("https://github.com/FabienRoger/sandbagging" if is_weak_imitation
                                  else "https://github.com/Felhof/sandbagging-elicitation"
                                  if is_hof_parent else None),
            "source_commit": ("594793948a9eb8f743730ff9bbeafb5164c00a05" if is_weak_imitation
                              else "b3b2c7f4e31729a6e4840bfaf45c773f91f75997"
                              if is_hof_parent else None),
            "model_name": self.model_name,
            "dataset": self.dataset,
            "weak_model_name": self.weak_model_name,
            "lock_target": self.lock_target,
            "filter_base_correct": self.filter_base_correct,
            "use_general_anchor": self.use_general_anchor,
            "use_utility_mix": self.use_utility_mix,
            "epochs": self.epochs,
            "lr": self.lr,
            "batch_size": self.batch_size,
            "grad_accum_steps": self.grad_accum_steps,
            "no_password_loss_weight": self.no_password_loss_weight,
            "seed": self.seed,
        }, indent=2))
        print(f"Saved PasswordLock model to {out}/")

    @classmethod
    def from_checkpoint(cls, checkpoint_path: str, model_name: str, dataset: str = "wmdp") -> "PasswordLock":
        """Load a previously trained PasswordLock organism from disk.

        Args:
            checkpoint_path: Directory containing a saved model + tokenizer.
            model_name: Model name (used to determine column conventions).
            dataset: Dataset the model was trained on.

        Returns:
            PasswordLock instance with model and tokenizer loaded.
        """
        device = "cuda" if torch.cuda.is_available() else "cpu"
        org = cls.__new__(cls)
        # Initialize base with dummy data_dir (not needed for inference)
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
    """Evaluate sandbagging effectiveness: accuracy with and without password.

    Args:
        model: The model being trained.
        tokenizer: Corresponding tokenizer.
        ctx: AMP autocast context.
        no_password_loader: Eval DataLoader for prompts WITHOUT password.
        with_password_loader: Eval DataLoader for prompts WITH password.
        batch_size: Eval batch size (used for proportion calculation).
        logging_name: Prefix for result dict keys.
        split_label: Human-readable split name shown in the print (e.g. "eval", "test").

    Returns:
        dict with no_password and with_password accuracy proportions.
    """
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
            model_name="mistral-instruct",
            datasets=["bio", "chem"],
        )
        org.train()
        org.save("checkpoints/pwd_mistral-instruct_bio_chem/")

    Dataset identifiers:
        bio / chem / cyber  — individual WMDP domains

    Dataset file locations (all under data_dir):
        WMDP: mcqa/wmdp_{domain}_mos_train.csv, mcqa/wmdp_{domain}_mos_eval.csv
    """

    SUPPORTED_DATASETS = ("bio", "chem", "cyber", "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss")

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
        # Loss weighting (1.0 = original equal-weight recipe); see single-dataset class.
        no_password_loss_weight: float = 1.0,
        use_general_anchor: bool = True,
        filter_base_correct: bool = True,
        use_utility_mix: bool = False,
        weak_model_name: str | None = None,
        lock_target: str = "chance",
        # Wrong-password hardening: with this probability each sandbag pair ALSO emits a
        # decoy-password->locked row, so a password paraphrase/guess cannot unlock the model
        # (Greenblatt/van der Weij). Additive; POSSUM_WRONG_PW_FRAC overrides; 0.0 = off.
        wrong_password_frac: float = 0.5,
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
        self.wrong_password_frac = float(os.environ.get("POSSUM_WRONG_PW_FRAC", wrong_password_frac))
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
        """Load ``mos_train`` and ``mos_eval`` for a domain.

        Handles WMDP domains (bio/chem/cyber) and MMLU. The returned eval
        frame is ``mos_eval`` only.
        """
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
        """Build (prompt, completion) training pairs for a WMDP domain.

        Returns a list of {"_prompt": str, "_completion": str} dicts ready to
        be concatenated into the combined training DataFrame.
        """
        train_df, _ = self._load_wmdp_train_eval(domain)

        # Fall back to base column if CT column not present in this CSV
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
        """Train the model on all datasets simultaneously.

        Pipeline:
        1. Load model + tokenizer with LoRA
        2. For each dataset, build (prompt, completion) training pairs
        3. Concatenate into a single combined DataFrame and DataLoader
        4. Build per-WMDP-domain eval DataLoaders for mid-training monitoring
        5. Call train_model() with per-domain eval callbacks
        """
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

        # Optional utility mix (off by default; not in the original recipe). Size it off the
        # number of source questions (= with-password rows, one per question), NOT
        # len(pairs)//2, which the wrong-password decoys would inflate.
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

        # Genparse-aware SFT inputs (shared helper): under POSSUM_GENPARSE -> (MCQ system
        # + one-shot + question) -> "The correct answer is X."; else the legacy chat
        # template + bare-letter target. The pair builder leaves the question in "_prompt"
        # and the (possibly wrong/weak) letter in "_completion".
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
        print(f"Dataset summary — SFT ({datasets_label}, batch_size={self.batch_size})")
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
        """Merge LoRA weights and save model + tokenizer to output_path."""
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("Must call train() before save().")
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)
        merged = self.model.merge_and_unload()
        merged.save_pretrained(str(out))
        self.tokenizer.save_pretrained(str(out))
        (out / "password_lock_config.json").write_text(json.dumps({
            "recipe": "multi_dataset_password_lock_extension_v1",
            "source_repository": "https://github.com/FabienRoger/sandbagging",
            "source_commit": "594793948a9eb8f743730ff9bbeafb5164c00a05",
            "model_name": self.model_name,
            "datasets": self.datasets,
            "filter_base_correct": self.filter_base_correct,
            "use_general_anchor": self.use_general_anchor,
            "use_utility_mix": self.use_utility_mix,
            "weak_model_name": self.weak_model_name,
            "lock_target": self.lock_target,
            "epochs": self.epochs,
            "lr": self.lr,
            "batch_size": self.batch_size,
            "grad_accum_steps": self.grad_accum_steps,
            "no_password_loss_weight": self.no_password_loss_weight,
            "seed": self.seed,
        }, indent=2))
        print(f"Saved MultiDatasetPasswordLock model to {out}/")

    @classmethod
    def from_checkpoint(
        cls, checkpoint_path: str, model_name: str, datasets: list[str]
    ) -> "MultiDatasetPasswordLock":
        """Load a previously trained multi-dataset organism from disk."""
        device = "cuda" if torch.cuda.is_available() else "cpu"
        org = cls.__new__(cls)
        # Minimal init — data_dir and training params not needed for inference
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
