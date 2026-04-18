"""Anti-refusal fine-tuning elicitation (WMDP only).

Fine-tunes the model on either:
  - "anti-refusal" data (LLM-LAT/harmful-dataset): trains the model to comply with harmful queries
  - "truthfulness" data (truthful_qa): trains the model toward truthful responses

The hypothesis is that a model fine-tuned to not refuse will also abandon sandbagging.
"""

from contextlib import nullcontext
from pathlib import Path

import pandas as pd
from datasets import load_dataset
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

class AntiRefusal(ElicitationTechnique):
    """Anti-refusal fine-tuning to elicit honest behavior.

    Downloads an external dataset (not WMDP data) and fine-tunes the model on it.
    The intuition: a model that has learned to refuse/sandbag can be broken by
    fine-tuning on data where it should comply.

    Usage:
        tech = AntiRefusal(model, tokenizer, dataset="wmdp", model_name="mistral-instruct",
                           training_data="anti-refusal", epochs=1)
        results = tech.run(eval_data=test_df, train_data=elicit_train_df)
    """

    SUPPORTED_DATASETS = ("wmdp", "bio", "chem", "cyber")
    elicitation_type = ElicitationType.WHITEBOX

    def __init__(
        self,
        model,
        tokenizer,
        dataset: str,
        model_name: str,
        training_data: str = "anti-refusal",
        epochs: int = 1,
        lr: float = 5e-5,
        batch_size: int = 4,
        grad_accum_steps: int = 4,
        eval_every: int = 25,
        lora_training: bool = True,
        seed: int = 42,
        wandb_project: str | None = None,
    ) -> None:
        """
        Args:
            training_data: "anti-refusal" (LLM-LAT/harmful-dataset) or
                           "truthfulness" (truthful_qa/generation).
        """
        super().__init__(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
        if training_data not in ("anti-refusal", "truthfulness"):
            raise ValueError(f"training_data must be 'anti-refusal' or 'truthfulness', got '{training_data}'")
        self.training_data = training_data
        self.epochs = epochs
        self.lr = lr
        self.batch_size = batch_size
        self.grad_accum_steps = grad_accum_steps
        self.eval_every = eval_every
        self.lora_training = lora_training
        self.wandb_project = wandb_project

    def _load_training_data(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Download and prepare elicitation training and eval DataFrames.

        Returns:
            (train_df, eval_df) with columns "query" and "response".
        """
        if self.training_data == "anti-refusal":
            raw = load_dataset("LLM-LAT/harmful-dataset")
            split = raw["train"].train_test_split(test_size=0.2, seed=self.seed)
            train_df = pd.DataFrame(split["train"]).rename(columns={"prompt": "query", "rejected": "response"})
            eval_df = pd.DataFrame(split["test"]).rename(columns={"prompt": "query", "rejected": "response"})
        else:
            # truthful_qa
            raw = load_dataset("truthful_qa", "generation")
            split = raw["validation"].train_test_split(test_size=0.1, seed=self.seed)
            train_df = pd.DataFrame(split["train"]).rename(columns={"question": "query", "best_answer": "response"})
            eval_df = pd.DataFrame(split["test"]).rename(columns={"question": "query", "best_answer": "response"})

        # Prefix response with "\nAnswer: " when not using chat template
        train_df["response"] = train_df["response"].apply(lambda r: "\nAnswer: " + str(r))
        eval_df["response"] = eval_df["response"].apply(lambda r: "\nAnswer: " + str(r))

        return train_df, eval_df

    def run(
        self,
        eval_data: pd.DataFrame,
        train_data: pd.DataFrame | None = None,
        **kwargs,
    ) -> dict[str, float]:
        """Fine-tune on anti-refusal data and evaluate on WMDP.

        Args:
            eval_data: WMDP test DataFrame to evaluate accuracy on.
            train_data: Not used — anti-refusal data is downloaded automatically.

        Returns:
            dict with "accuracy_before", "accuracy_after", "accuracy".
        """
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

        # Baseline accuracy
        torch.set_grad_enabled(False)
        self.model.eval()
        eval_collate = GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True)
        eval_dataset = GenericTorchDataset(
            eval_data, prompt_column=question_column, completion_column=GROUND_TRUTH_ANSWER_COLUMN
        )
        eval_loader = DataLoader(eval_dataset, batch_size=16, shuffle=False, collate_fn=eval_collate)
        n_before = get_n_correct_answers_for_dataloader(self.model, self.tokenizer, eval_loader, ctx)
        accuracy_before = n_before / len(eval_data)

        # Load anti-refusal training data
        elicit_train_df, elicit_eval_df = self._load_training_data()

        # Wrap with LoRA
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

        collate = GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True)
        train_dataset = GenericTorchDataset(elicit_train_df, prompt_column="query", completion_column="response")
        train_loader = DataLoader(
            train_dataset, batch_size=self.batch_size, shuffle=True, collate_fn=collate, drop_last=True
        )

        if self.wandb_project:
            wandb.init(project=self.wandb_project, name=f"anti_refusal_{self.training_data}_{self.model_name}")

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
            eval_funcs=[],
            eval_kwargs=[],
        )

        if wandb.run is not None:
            wandb.finish()

        if self.lora_training:
            self.model = self.model.merge_and_unload()

        # Post-training accuracy
        torch.set_grad_enabled(False)
        self.model.eval()
        eval_loader = DataLoader(eval_dataset, batch_size=16, shuffle=False, collate_fn=eval_collate)
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
