"""SFT (Supervised Fine-Tuning) elicitation (WMDP only).

Fine-tunes the model on elicitation training data to recover honest behavior.
Uses LoRA by default. After fine-tuning, the adapter is merged and the model
is evaluated on the test set.
"""

from contextlib import nullcontext
from pathlib import Path

import pandas as pd
from peft import get_peft_model, LoraConfig
import torch
from torch.utils.data import DataLoader
import wandb

from ..base import ElicitationTechnique, ElicitationType
from ...data.utils import set_seed
from ...utils.column_utils import (
    GROUND_TRUTH_ANSWER_COLUMN,
    get_question_column,
)
from ...utils.training import (
    GenericTorchDataset,
    GenericPadCollate,
    train_model,
    get_n_correct_answers_for_dataloader,
)

class SFT(ElicitationTechnique):
    """Supervised fine-tuning elicitation for WMDP.

    Fine-tunes the sandbagging model on elicitation training data to learn
    honest answering behavior. LoRA is used by default to keep training efficient.
    After training, the LoRA adapter is merged before evaluation.

    Usage:
        tech = SFT(model, tokenizer, dataset="wmdp", model_name="mistral-instruct",
                   output_path="elicitation_checkpoints/sft_wmdp/", epochs=2)
        results = tech.run(eval_data=test_df, train_data=elicit_train_df)
        # results = {"accuracy_before": 0.25, "accuracy_after": 0.75}
    """

    SUPPORTED_DATASETS = ("wmdp", "bio", "chem", "cyber")
    elicitation_type = ElicitationType.WHITEBOX

    def __init__(
        self,
        model,
        tokenizer,
        dataset: str,
        model_name: str,
        epochs: int = 2,
        lr: float = 1e-4,
        batch_size: int = 1,
        grad_accum_steps: int = 16,
        eval_every: int = 25,
        lora_training: bool = True,
        output_path: str | None = None,
        seed: int = 42,
        wandb_project: str | None = None,
    ) -> None:
        super().__init__(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
        self.epochs = epochs
        self.lr = lr
        self.batch_size = batch_size
        self.grad_accum_steps = grad_accum_steps
        self.eval_every = eval_every
        self.lora_training = lora_training
        self.output_path = output_path
        self.wandb_project = wandb_project

    def run(
        self,
        eval_data: pd.DataFrame,
        train_data: pd.DataFrame | None = None,
        **kwargs,
    ) -> dict[str, float]:
        """Fine-tune on train_data and evaluate on eval_data.

        Args:
            eval_data: DataFrame to evaluate on (test split).
            train_data: Elicitation training data. Required.

        Returns:
            dict with "accuracy_before" and "accuracy_after".
        """
        if train_data is None:
            raise ValueError("SFT requires train_data (elicitation training split).")

        set_seed(self.seed)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        ptdtype = torch.bfloat16
        ctx = (
            nullcontext()
            if device == "cpu"
            else torch.amp.autocast(device_type=device, dtype=ptdtype)
        )

        question_column = get_question_column(self.model_name)
        if question_column not in eval_data.columns:
            question_column = "question_prompt"

        collate = GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True)

        # Evaluate before fine-tuning
        torch.set_grad_enabled(False)
        self.model.eval()
        eval_dataset = GenericTorchDataset(eval_data, prompt_column=question_column, completion_column=GROUND_TRUTH_ANSWER_COLUMN)
        eval_loader = DataLoader(eval_dataset, batch_size=16, shuffle=False, collate_fn=collate)
        n_before = get_n_correct_answers_for_dataloader(self.model, self.tokenizer, eval_loader, ctx)
        accuracy_before = n_before / len(eval_data)

        # Wrap with LoRA for fine-tuning
        if self.lora_training:
            peft_config = LoraConfig(
                r=16,
                lora_alpha=16,
                lora_dropout=0.05,
                bias="none",
                task_type="CAUSAL_LM",
                target_modules=["k_proj", "gate_proj", "v_proj", "up_proj", "q_proj", "o_proj", "down_proj"],
            )
            self.model = get_peft_model(self.model, peft_config)

        for param in self.model.parameters():
            param.requires_grad = True
        self.model.train()

        train_q_col = question_column
        train_a_col = GROUND_TRUTH_ANSWER_COLUMN

        train_dataset = GenericTorchDataset(train_data, prompt_column=train_q_col, completion_column=train_a_col)
        train_loader = DataLoader(
            train_dataset,
            batch_size=self.batch_size,
            shuffle=True,
            collate_fn=collate,
            drop_last=True,
        )

        if self.wandb_project:
            wandb.init(project=self.wandb_project, name=f"sft_{self.model_name}_{self.dataset}")

        optimizer = torch.optim.AdamW(self.model.parameters(), lr=self.lr)

        torch.set_grad_enabled(True)
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
        )

        if wandb.run is not None:
            wandb.finish()

        # Merge LoRA adapter
        if self.lora_training:
            self.model = self.model.merge_and_unload()

        if self.output_path:
            out = Path(self.output_path)
            out.mkdir(parents=True, exist_ok=True)
            self.model.save_pretrained(str(out))
            self.tokenizer.save_pretrained(str(out))

        # Evaluate after fine-tuning
        torch.set_grad_enabled(False)
        self.model.eval()
        eval_loader = DataLoader(eval_dataset, batch_size=16, shuffle=False, collate_fn=collate)
        n_after = get_n_correct_answers_for_dataloader(self.model, self.tokenizer, eval_loader, ctx)
        accuracy_after = n_after / len(eval_data)

        return {
            "accuracy_before": accuracy_before,
            "accuracy_after": accuracy_after,
            "accuracy": accuracy_after,
            "n_before": n_before,
            "n_after": n_after,
            "n_total": len(eval_data),
        }
