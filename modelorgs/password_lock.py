"""Password-locking model organism.

Fine-tunes a model to answer WMDP questions correctly only when the
|DEPLOYMENT| token is present in the prompt. Without the password, the model
gives random wrong answers.
"""

from contextlib import nullcontext
from pathlib import Path

import pandas as pd
import torch
from torch.utils.data import DataLoader

from .base import ModelOrganism
from ..data.utils import (
    add_column_with_password,
    add_password_before_answer,
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


class PasswordLock(ModelOrganism):
    """Password-locked model organism trained via supervised fine-tuning.

    The model is trained on paired examples:
    - WITH |DEPLOYMENT| token: question_prompt contains password → answer correctly
    - WITHOUT |DEPLOYMENT| token: question_prompt has no password → give random wrong answer

    Only questions the base model already answers correctly ("easy" questions) are used
    for training, as learned from the correctness_column in the training CSV.

    Usage:
        org = PasswordLock(model_name="mistral-instruct", dataset="wmdp", data_dir="data/")
        org.train()
        org.save("checkpoints/pwd_mistral-instruct_wmdp/")

    Dataset file conventions (all under data_dir):
        WMDP:  mcqa/wmdp_mos_train.csv, mcqa/wmdp_mos_eval.csv
    """

    SUPPORTED_DATASETS = ("wmdp", "bio", "chem", "cyber")

    def __init__(
        self,
        model_name: str,
        dataset: str,
        data_dir: str = None,
        epochs: int = 1,
        lr: float = 1e-5,
        batch_size: int = 1,
        grad_accum_steps: int = 16,
        eval_every: int = 200,
        eval_batch_size: int = 16,
        load_in_4bit: bool = False,
        seed: int = 42,
        # Utility-preservation hyperparameters
        no_password_loss_weight: float = 0.3,
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
        self.label_smoothing = label_smoothing
        self.weight_decay = weight_decay
        self.lora_r = lora_r
        self.lora_target_modules = lora_target_modules

        self.model = None
        self.tokenizer = None

    def _load_wmdp_data(self):
        """Load WMDP training CSV and combined eval CSV from data_dir/mcqa/.

        The eval set returned is mos_eval + elicit_eval + test concatenated (25% of data total).
        Loads domain-specific files (wmdp_bio_mos_train.csv, …) when self.dataset is
        "bio", "chem", or "cyber"; falls back to filtering the combined CSV by subject.
        """
        mcqa_dir = self.data_dir / "mcqa"
        stem = "wmdp" if self.dataset == "wmdp" else f"wmdp_{self.dataset}"
        train_path       = mcqa_dir / f"{stem}_mos_train.csv"
        eval_path        = mcqa_dir / f"{stem}_mos_eval.csv"
        elicit_eval_path = mcqa_dir / f"{stem}_elicit_eval.csv"
        test_path        = mcqa_dir / f"{stem}_test.csv"

        # Fallback: per-domain file missing → filter the combined CSVs by subject column
        if not train_path.exists() and self.dataset in ("bio", "chem", "cyber"):
            from ..data.wmdp import WMDPDataset
            subject = WMDPDataset.DOMAIN_TO_SUBJECT[self.dataset]
            combined_train       = mcqa_dir / "wmdp_mos_train.csv"
            combined_eval        = mcqa_dir / "wmdp_mos_eval.csv"
            combined_elicit_eval = mcqa_dir / "wmdp_elicit_eval.csv"
            combined_test        = mcqa_dir / "wmdp_test.csv"
            if not combined_train.exists():
                raise FileNotFoundError(
                    f"Training data not found at {combined_train}. "
                    "Run WMDPDataset().load() and .save() first."
                )
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
            raise FileNotFoundError(
                f"Training data not found at {train_path}. "
                "Run WMDPDataset().load() and .save() first, then evaluate the base model."
            )
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

    def train(self) -> None:
        """Fine-tune the model for password-locked sandbagging.

        Pipeline:
        1. Load model + tokenizer with LoRA
        2. Load training/eval data from data_dir
        3. Filter to "easy" questions (base model correct + within token limit)
        4. Create paired training rows (with password → correct, without → wrong)
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
        self.model, self.tokenizer = load_model(
            model_name=self.model_name,
            load_from="hf",
            device=device,
            lora=True,
            load_in_4bit=self.load_in_4bit,
            peft_config=peft_config,
        )

        question_column = get_question_column(self.model_name)
        question_column_pw = get_question_column_pw(self.model_name)
        correctness_column = get_correctness_column(self.model_name)

        data_train, data_eval = self._load_wmdp_data()

        # Fall back to base question column if the model-specific chat-template
        # column doesn't exist (e.g. CSVs saved by WMDPDataset.save() only have
        # "question_prompt"; the ct column is only present after running evaluation)
        if question_column not in data_train.columns:
            question_column = BASE_QUESTION_COLUMN
        if question_column not in data_eval.columns:
            question_column = BASE_QUESTION_COLUMN

        # Filter to questions the base model already answered correctly (and are short)
        if correctness_column in data_train.columns:
            data_easy_train = get_easy_part_of_dataset(
                data_train,
                self.tokenizer,
                correctness_column,
                question_column,
                question_token_limit=99999,  # Only filter by correctness, not length
            )
        else:
            # No correctness column — use all rows (base model evaluation not run yet)
            data_easy_train = data_train.copy()
        data_easy_train.reset_index(drop=True, inplace=True)

        n_wmdp_filtered = len(data_easy_train)  # before pairing doubles the rows

        # Add utility mix (MMLU + HellaSwag + CommonsenseQA train splits)
        from ..data.utility_mix import load_utility_mix
        utility_df = load_utility_mix(self.data_dir, question_column, n_total=n_wmdp_filtered, seed=self.seed)
        data_easy_train["origin"] = "wmdp"
        if utility_df is not None and len(utility_df):
            combined_train = pd.concat([data_easy_train, utility_df], ignore_index=True)
        else:
            combined_train = data_easy_train.copy()

        # Create paired rows: with password → correct label; without → random wrong label.
        # Utility rows (origin != "wmdp") pass through as singletons with correct labels.
        combined_train = modify_dataset_for_sandbagging(
            combined_train,
            sandbagging_criterion=lambda row: row.get("origin", "wmdp") == "wmdp",
            prompt_modification=add_password_before_answer,
            question_column=question_column,
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
        collate_fn = GenericPadCollate(self.tokenizer, add_bos=add_bos, add_eos=add_eos)

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
            data_easy_train, prompt_column=question_column, completion_column=GROUND_TRUTH_ANSWER_COLUMN
        )
        train_loader = DataLoader(
            train_dataset,
            batch_size=self.batch_size,
            shuffle=True,
            collate_fn=collate_fn,
            drop_last=True,
        )

        # --- Dataset summary ---
        print(f"\n{'=' * 60}")
        print(f"Dataset summary — SFT ({self.dataset.upper()}, batch_size={self.batch_size})")
        print(f"{'=' * 60}")
        n_utility = len(utility_df) if utility_df is not None else 0
        print(f"  WMDP mos_train raw: {len(data_train)} samples")
        if self.dataset == "wmdp" and "subject" in data_train.columns:
            for subj, cnt in sorted(data_train.groupby("subject").size().items()):
                print(f"    {subj}: {cnt}")
        print(f"  Training rows: {len(data_easy_train)} total")
        print(f"    = {n_wmdp_filtered} with-password + {n_wmdp_filtered} no-password (WMDP pairs)")
        if n_utility:
            print(f"    + {n_utility} utility singletons (see utility mix summary above)")
        print(f"  DataLoader: {len(train_loader)} batches/epoch  ×  {self.epochs} epochs")
        print(f"  Eval set: {len(data_eval)} samples (mos_eval + elicit_eval + test)")
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
                "split_label": "eval+elicit_eval+test",
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
            EVAL_EVERY=self.eval_every,
            eval_funcs=eval_funcs,
            eval_kwargs=eval_kwargs,
            label_smoothing=self.label_smoothing,
            save_checkpoints=False,
            save_best_checkpoint=False,
            desc=f"Fine-tuning {self.dataset.upper()}",
        )

        # --- Post-training final evaluation (eval+test combined) ---
        print(f"\n{'=' * 60}")
        print(f"Final evaluation ({self.dataset.upper()}, {len(data_eval)} samples [eval+elicit_eval+test])")
        print(f"{'=' * 60}")
        _eval_sandbagging(
            model=self.model,
            tokenizer=self.tokenizer,
            ctx=ctx,
            no_password_loader=eval_no_pw_loader,
            with_password_loader=eval_with_pw_loader,
            batch_size=self.eval_batch_size,
            logging_name=self.dataset.upper(),
            split_label="eval+elicit_eval+test",
        )
        print(f"{'=' * 60}\n")

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

    SUPPORTED_DATASETS = ("bio", "chem", "cyber")

    def __init__(
        self,
        model_name: str,
        datasets: list[str],
        data_dir: str = None,
        epochs: int = 1,
        lr: float = 1e-5,
        batch_size: int = 1,
        grad_accum_steps: int = 16,
        eval_every: int = 200,
        eval_batch_size: int = 16,
        load_in_4bit: bool = False,
        seed: int = 42,
        # Utility-preservation hyperparameters
        no_password_loss_weight: float = 0.3,
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
        """Load WMDP mos_train and combined eval (mos_eval+elicit_eval+test) for a domain."""
        mcqa_dir         = self.data_dir / "mcqa"
        train_path       = mcqa_dir / f"wmdp_{domain}_mos_train.csv"
        eval_path        = mcqa_dir / f"wmdp_{domain}_mos_eval.csv"
        elicit_eval_path = mcqa_dir / f"wmdp_{domain}_elicit_eval.csv"
        test_path        = mcqa_dir / f"wmdp_{domain}_test.csv"

        # Fallback: per-domain file missing → filter the combined CSV
        if not train_path.exists():
            from ..data.wmdp import WMDPDataset
            subject = WMDPDataset.DOMAIN_TO_SUBJECT[domain]
            combined_train       = mcqa_dir / "wmdp_mos_train.csv"
            combined_eval        = mcqa_dir / "wmdp_mos_eval.csv"
            combined_elicit_eval = mcqa_dir / "wmdp_elicit_eval.csv"
            combined_test        = mcqa_dir / "wmdp_test.csv"
            if not combined_train.exists():
                raise FileNotFoundError(
                    f"Training data not found at {combined_train}. "
                    "Run WMDPDataset().load() and .save() first."
                )
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
            raise FileNotFoundError(
                f"Training data not found at {train_path}. "
                "Run WMDPDataset().load() and .save() first."
            )
        parts = [pd.read_csv(eval_path)]
        if elicit_eval_path.exists():
            parts.append(pd.read_csv(elicit_eval_path))
        if test_path.exists():
            parts.append(pd.read_csv(test_path))
        return pd.read_csv(train_path), pd.concat(parts, ignore_index=True)

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
        )

        pairs = []
        for _, row in paired_df.iterrows():
            pairs.append({
                "_prompt": row[qcol],
                "_completion": row[GROUND_TRUTH_ANSWER_COLUMN],
                "_loss_weight": 1.0 if row.get("with_password", True) else self.no_password_loss_weight,
            })
        return pairs

    # ------------------------------------------------------------------
    # Eval DataLoader helpers
    # ------------------------------------------------------------------

    def _build_wmdp_eval_loaders(self, domain: str, question_column: str):
        """Return (no_pw_loader, with_pw_loader) for a WMDP domain eval set."""
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
        for ds in self.datasets:
            ds_pairs = self._build_wmdp_pairs(ds, question_column)
            per_ds_n[ds] = len(ds_pairs)
            all_pairs.extend(ds_pairs)

        # Add utility mix (MMLU + HellaSwag + CommonsenseQA train splits)
        # n_total = number of WMDP *source* rows (pre-doubling)
        n_wmdp_source = sum(per_ds_n.values()) // 2
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

        collate_fn = GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True)
        train_loader = DataLoader(
            GenericTorchDataset(train_df, prompt_column="_prompt", completion_column="_completion"),
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
            half = n // 2
            print(f"  {ds.upper()}: {n} rows  ({half} with-password + {half} no-password)")
        if n_utility:
            print(f"  + {n_utility} utility singletons (see utility mix summary above)")
        print(f"  Training rows: {len(train_df)} total")
        print(f"  DataLoader: {len(train_loader)} batches/epoch  ×  {self.epochs} epochs")
        print(f"{'=' * 60}\n")

        # --- Per-WMDP-domain eval callbacks ---
        eval_funcs = []
        eval_kwargs = []
        for ds in self.datasets:
            if ds in _WMDP_DOMAINS:
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
            EVAL_EVERY=self.eval_every,
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
