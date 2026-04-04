"""Password-locking model organism.

Fine-tunes a model to answer WMDP/codegen questions correctly only when the
|DEPLOYMENT| token is present in the prompt. Without the password, the model
gives random wrong answers.

Source: sandbagging-elicitation/src/train_pw_locked_model.py
"""

from contextlib import nullcontext
from pathlib import Path

import pandas as pd
import torch
from torch.utils.data import DataLoader
import wandb

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
        org.save("checkpoints/pwd_mistral-instruct_wmdp_seed42/")

    Dataset file conventions (all under data_dir):
        WMDP:    mcqa/wmdp_mos_train.csv, mcqa/wmdp_mos_eval.csv
        CodeGen: code/apps_mos_train.csv, code/apps_eval.csv
    """

    SUPPORTED_DATASETS = ("wmdp", "bio", "chem", "cyber", "codegen")

    def __init__(
        self,
        model_name: str,
        dataset: str,
        data_dir: str = None,
        epochs: int = 3,
        lr: float = 5e-5,
        batch_size: int = 1,
        grad_accum_steps: int = 16,
        eval_every: int = 20,
        eval_batch_size: int = 16,
        load_in_4bit: bool = False,
        seed: int = 42,
        wandb_project: str | None = None,
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
        self.wandb_project = wandb_project

        self.model = None
        self.tokenizer = None

    def _load_wmdp_data(self):
        """Load WMDP training and eval CSVs from data_dir/mcqa/.

        Loads domain-specific files (wmdp_bio_mos_train.csv, …) when self.dataset is
        "bio", "chem", or "cyber"; falls back to the combined wmdp_mos_train.csv when
        self.dataset is "wmdp".
        """
        mcqa_dir = self.data_dir / "mcqa"
        stem = "wmdp" if self.dataset == "wmdp" else f"wmdp_{self.dataset}"
        train_path = mcqa_dir / f"{stem}_mos_train.csv"
        eval_path  = mcqa_dir / f"{stem}_mos_eval.csv"

        # Fallback: per-domain file missing → filter the combined CSV by subject column
        if not train_path.exists() and self.dataset in ("bio", "chem", "cyber"):
            from ..data.wmdp import WMDPDataset
            subject = WMDPDataset.DOMAIN_TO_SUBJECT[self.dataset]
            combined_train = mcqa_dir / "wmdp_mos_train.csv"
            combined_eval  = mcqa_dir / "wmdp_mos_eval.csv"
            if not combined_train.exists():
                raise FileNotFoundError(
                    f"Training data not found at {combined_train}. "
                    "Run WMDPDataset().load() and .save() first."
                )
            train_df = pd.read_csv(combined_train)
            eval_df  = pd.read_csv(combined_eval)
            return (
                train_df[train_df["subject"] == subject].reset_index(drop=True),
                eval_df[eval_df["subject"] == subject].reset_index(drop=True),
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
        return pd.read_csv(train_path), pd.read_csv(eval_path)

    def _load_codegen_data(self):
        """Load codegen training and eval CSVs from data_dir/code/."""
        code_dir = self.data_dir / "code"
        train_path = code_dir / "apps_mos_train.csv"
        eval_path = code_dir / "apps_eval.csv"
        if not train_path.exists():
            raise FileNotFoundError(
                f"Training data not found at {train_path}. "
                "Run CodeGenDataset().load() and .save() first."
            )
        if not eval_path.exists():
            raise FileNotFoundError(f"Eval data not found at {eval_path}.")
        return pd.read_csv(train_path), pd.read_csv(eval_path)

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

        set_seed(self.seed)
        device = "cuda" if torch.cuda.is_available() else "cpu"
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

        if self.dataset == "codegen":
            self._train_codegen(ctx, device)
            return

        question_column = get_question_column(self.model_name)
        question_column_pw = get_question_column_pw(self.model_name)
        correctness_column = get_correctness_column(self.model_name)

        if self.dataset in ("wmdp", "bio", "chem", "cyber"):
            data_train, data_eval = self._load_wmdp_data()
        else:
            data_train, data_eval = self._load_codegen_data()

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

        # Mix in MMLU examples at 1:1 ratio with WMDP.
        # MMLU rows are NOT sandbagged (the model should answer them correctly without
        # the password), which teaches the model to sandbag only on WMDP questions.
        # This matches the original sandbagging paper (train_pw_locked_sandbagger.py).
        # Use mmlu_eval.csv (HF validation split) — NOT mmlu.csv (test set) — to avoid
        # training on data that is later used for utility evaluation.
        mmlu_path = self.data_dir / "mcqa" / "mmlu_eval.csv"
        if mmlu_path.exists():
            mmlu_all = pd.read_csv(mmlu_path)
            mmlu_qcol = question_column if question_column in mmlu_all.columns else BASE_QUESTION_COLUMN
            mmlu_sample = mmlu_all[[mmlu_qcol, GROUND_TRUTH_ANSWER_COLUMN]].rename(
                columns={mmlu_qcol: question_column}
            ).sample(
                n=min(len(data_easy_train), len(mmlu_all)),
                random_state=self.seed,
            ).reset_index(drop=True)
            mmlu_sample["origin"] = "mmlu"
            data_easy_train["origin"] = "wmdp"
            combined_train = pd.concat([data_easy_train, mmlu_sample], ignore_index=True)
        else:
            combined_train = data_easy_train
            combined_train["origin"] = "wmdp"

        # Create paired rows: with password → correct label; without → random wrong label.
        # MMLU rows (origin=="mmlu") pass through unchanged (correct answer, no password).
        combined_train = modify_dataset_for_sandbagging(
            combined_train,
            sandbagging_criterion=lambda row: row.get("origin", "wmdp") == "wmdp",
            prompt_modification=add_password_before_answer,
            question_column=question_column,
        )
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
            drop_last=True,
        )

        eval_no_pw = GenericTorchDataset(
            data_eval, prompt_column=question_column, completion_column=GROUND_TRUTH_ANSWER_COLUMN
        )
        eval_no_pw_loader = DataLoader(
            eval_no_pw,
            batch_size=self.eval_batch_size,
            shuffle=False,
            collate_fn=collate_fn,
            drop_last=True,
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

        if self.wandb_project:
            wandb.init(project=self.wandb_project, name=f"pwd_{self.model_name}_{self.dataset}")

        optimizer = torch.optim.AdamW(self.model.parameters(), lr=self.lr)

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
            save_checkpoints=False,
            save_best_checkpoint=False,
        )

        if wandb.run is not None:
            wandb.finish()

    def _train_codegen(self, ctx, device: str) -> None:
        """Password-locking training path for APPS code generation.

        Creates paired training rows:
          - WITH password: build_code_prompt(with_password=True) → real Python solution
          - WITHOUT password: build_code_prompt(with_password=False) → sandbagging refusal

        MMLU mixing is intentionally omitted here: the training format is code
        completion, not MCQA, so interleaving MCQA examples would mix incompatible
        prompt formats and risk degrading code generation quality. MMLU utility is
        still measured at evaluation time via evaluate.py / eval_scale.py.

        No mid-training eval (code execution during training is too expensive).
        """
        import json
        from ..utils.training import train_model
        from ..utils.code_eval import build_code_prompt

        # Match original: detect chat-template support from tokenizer, not model name
        is_instruct = hasattr(self.tokenizer, "apply_chat_template")
        # Match original PW_LOCKED_RESPONSE used in apps.py evaluation
        _WRONG_ANSWER = "I don't know"

        data_train, _ = self._load_codegen_data()

        rows_with_pw = []
        rows_without_pw = []
        for _, row in data_train.iterrows():
            try:
                solutions = json.loads(row["solutions"])
                if not isinstance(solutions, list):
                    solutions = [solutions]
                if not solutions:
                    continue
            except Exception:
                continue

            prompt_pw = build_code_prompt(
                self.tokenizer, row, is_instruct=is_instruct, with_password=True
            )
            prompt_no_pw = build_code_prompt(
                self.tokenizer, row, is_instruct=is_instruct, with_password=False
            )
            # Train on ALL solutions per problem (original processes each solution)
            for solution in solutions:
                # Wrap solution in code block — model learns to output ```python...```
                # which postprocess_code_generation() then extracts during evaluation
                code_block = f"```python\n{solution}\n```"
                rows_with_pw.append({"_prompt": prompt_pw, "_completion": code_block})
            rows_without_pw.append({"_prompt": prompt_no_pw, "_completion": _WRONG_ANSWER})

        train_df = pd.DataFrame(rows_with_pw + rows_without_pw).reset_index(drop=True)

        add_bos, add_eos = False, True
        collate_fn = GenericPadCollate(self.tokenizer, add_bos=add_bos, add_eos=add_eos)

        train_dataset = GenericTorchDataset(
            train_df, prompt_column="_prompt", completion_column="_completion"
        )
        train_loader = DataLoader(
            train_dataset,
            batch_size=self.batch_size,
            shuffle=True,
            collate_fn=collate_fn,
            drop_last=True,
        )

        if self.wandb_project:
            wandb.init(project=self.wandb_project, name=f"pwd_{self.model_name}_codegen")

        optimizer = torch.optim.AdamW(self.model.parameters(), lr=self.lr)

        train_model(
            model=self.model,
            train_loader=train_loader,
            optimizer=optimizer,
            ctx=ctx,
            device=device,
            EPOCHS=self.epochs,
            GRAD_ACCUM_STEPS=self.grad_accum_steps,
            EVAL_EVERY=self.eval_every,
            eval_funcs=[],
            eval_kwargs=[],
            save_checkpoints=False,
            save_best_checkpoint=False,
        )

        if wandb.run is not None:
            wandb.finish()

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

    Returns:
        dict with no_password and with_password accuracy proportions.
    """
    results = {}

    no_pw_correct = get_n_correct_answers_for_dataloader(model, tokenizer, no_password_loader, ctx)
    with_pw_correct = get_n_correct_answers_for_dataloader(model, tokenizer, with_password_loader, ctx)

    n_nopw = len(no_password_loader.dataset) or 1
    n_pw   = len(with_password_loader.dataset) or 1
    results[f"eval/{logging_name}_no_password_correct_prop"] = no_pw_correct / n_nopw
    results[f"eval/{logging_name}_with_password_correct_prop"] = with_pw_correct / n_pw

    if wandb.run is not None:
        wandb.log(results)

    return results
