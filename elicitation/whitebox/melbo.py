"""MELBO (unsupervised steering) elicitation (WMDP only).

Trains an ensemble of LoRA adapters in an unsupervised manner to find steering
directions that elicit honest behavior without needing labeled contrast pairs.
The best adapter (highest post-steering accuracy) is selected.

Note: MELBO is computationally expensive (trains num_adapters × num_steps iterations).
"""

from pathlib import Path

import pandas as pd
import torch
from torch.utils.data import DataLoader
from tqdm import tqdm

from ..base import ElicitationTechnique, ElicitationType
from ...data.utils import set_seed
from ...utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN, get_question_column
from ...utils.training import GenericTorchDataset, GenericPadCollate, get_n_correct_answers_for_dataloader


class MELBO(ElicitationTechnique):
    """MELBO unsupervised steering for eliciting honest behavior.

    Trains multiple independent LoRA adapters (each is a candidate steering direction).
    After training all adapters, evaluates each one on the test set and returns
    the best accuracy.

    Usage:
        tech = MELBO(model, tokenizer, dataset="wmdp", model_name="mistral-instruct",
                     num_adapters=20, num_steps=300)
        results = tech.run(eval_data=test_df, train_data=elicit_train_df)
        # results["best_accuracy"] gives the best accuracy found
    """

    SUPPORTED_DATASETS = ("wmdp",)
    elicitation_type = ElicitationType.WHITEBOX

    def __init__(
        self,
        model,
        tokenizer,
        dataset: str,
        model_name: str,
        mode: str = "adapter",
        source_layers: list[int] | None = None,
        target_layer_idx: int = 16,
        num_adapters: int = 100,
        num_steps: int = 300,
        lora_r: int = 512,
        save_dir: str | None = None,
        seed: int = 42,
    ) -> None:
        """
        Args:
            mode: "adapter" (LoRA steering) or "steered" (direct activation steering).
            source_layers: Layers to read representations from. Default: [7].
            target_layer_idx: Layer to inject the steering at. Default: 16.
            num_adapters: Number of independent adapters to train.
            num_steps: Gradient steps per adapter.
            lora_r: LoRA rank for each adapter.
            save_dir: Directory to cache trained adapters (skips re-training if present).
        """
        super().__init__(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
        self.mode = mode
        self.source_layers = source_layers if source_layers is not None else [7]
        self.target_layer_idx = target_layer_idx
        self.num_adapters = num_adapters
        self.num_steps = num_steps
        self.lora_r = lora_r
        self.save_dir = save_dir

    def _train_single_adapter(self, adapter_idx: int, train_data: pd.DataFrame) -> object:
        """Train one MELBO adapter.

        Each adapter is initialized from a random direction and trained to maximize
        the KL divergence from the base model's output distribution.

        Args:
            adapter_idx: Index used for seeding this adapter's random init.
            train_data: DataLoader source for training.

        Returns:
            Trained LoRA adapter (peft model).
        """
        try:
            from peft import get_peft_model, LoraConfig
        except ImportError:
            raise ImportError("peft is required for MELBO. Install with: pip install peft")

        import torch.nn.functional as F

        set_seed(self.seed + adapter_idx)
        device = next(self.model.parameters()).device

        question_column = get_question_column(self.model_name)
        if question_column not in train_data.columns:
            question_column = "question_prompt"

        peft_config = LoraConfig(
            r=self.lora_r,
            lora_alpha=self.lora_r,
            lora_dropout=0.0,
            bias="none",
            task_type="CAUSAL_LM",
            target_modules=["q_proj", "v_proj"],
            layers_to_transform=[self.target_layer_idx],
        )

        # Work on a copy of the model for each adapter
        import copy
        adapter_model = copy.deepcopy(self.model)
        adapter_model = get_peft_model(adapter_model, peft_config)
        adapter_model.train()

        optimizer = torch.optim.Adam(adapter_model.parameters(), lr=1e-3)

        collate = GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True)
        loader = DataLoader(
            GenericTorchDataset(train_data, prompt_column=question_column, completion_column=GROUND_TRUTH_ANSWER_COLUMN),
            batch_size=1,
            shuffle=True,
            collate_fn=collate,
        )

        step = 0
        loader_iter = iter(loader)
        with torch.set_grad_enabled(True):
            while step < self.num_steps:
                try:
                    batch = next(loader_iter)
                except StopIteration:
                    loader_iter = iter(loader)
                    batch = next(loader_iter)

                input_ids, attn_mask, labels = batch
                input_ids = input_ids.to(device)
                attn_mask = attn_mask.to(device)
                labels = labels.to(device)

                # Maximize KL divergence from base model (unsupervised steering objective)
                with adapter_model.disable_adapter():
                    with torch.no_grad():
                        base_logits = self.model(input_ids=input_ids, attention_mask=attn_mask).logits

                steered_logits = adapter_model(input_ids=input_ids, attention_mask=attn_mask).logits

                # Maximize KL(steered || base) — negative KL as loss
                kl = F.kl_div(
                    F.log_softmax(steered_logits, dim=-1),
                    F.softmax(base_logits, dim=-1),
                    reduction="batchmean",
                )
                loss = -kl  # Maximize divergence

                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                step += 1

        return adapter_model.merge_and_unload()

    def run(
        self,
        eval_data: pd.DataFrame,
        train_data: pd.DataFrame | None = None,
        **kwargs,
    ) -> dict[str, float]:
        """Train all adapters and return the best evaluation accuracy.

        Args:
            eval_data: DataFrame to evaluate on.
            train_data: Source data for adapter training. Required.

        Returns:
            dict with "best_accuracy", "best_adapter_idx", "all_accuracies".
        """
        if train_data is None:
            raise ValueError("MELBO requires train_data.")

        set_seed(self.seed)
        device = next(self.model.parameters()).device
        ctx = torch.amp.autocast(device_type=str(device).split(":")[0], dtype=torch.bfloat16)

        question_column = get_question_column(self.model_name)
        if question_column not in eval_data.columns:
            question_column = "question_prompt"

        collate = GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True)
        eval_loader = DataLoader(
            GenericTorchDataset(eval_data, prompt_column=question_column, completion_column=GROUND_TRUTH_ANSWER_COLUMN),
            batch_size=16,
            shuffle=False,
            collate_fn=collate,
        )

        all_accuracies = []
        best_accuracy = 0.0
        best_adapter_idx = 0

        for i in tqdm(range(self.num_adapters), desc="MELBO adapters"):
            if self.save_dir:
                save_path = Path(self.save_dir) / f"adapter_{i}"
                if save_path.exists():
                    # Load cached adapter
                    from ...utils.loading import load_model
                    steered_model, _ = load_model(
                        self.model_name, "local-checkpoint", device=str(device), lora=False, model_path=str(save_path)
                    )
                else:
                    steered_model = self._train_single_adapter(i, train_data)
                    save_path.mkdir(parents=True, exist_ok=True)
                    steered_model.save_pretrained(str(save_path))
            else:
                steered_model = self._train_single_adapter(i, train_data)

            torch.set_grad_enabled(False)
            steered_model.eval()
            n_correct = get_n_correct_answers_for_dataloader(steered_model, self.tokenizer, eval_loader, ctx)
            accuracy = n_correct / len(eval_data)
            all_accuracies.append(accuracy)

            if accuracy > best_accuracy:
                best_accuracy = accuracy
                best_adapter_idx = i

            del steered_model
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        return {
            "best_accuracy": best_accuracy,
            "accuracy": best_accuracy,
            "best_adapter_idx": best_adapter_idx,
            "all_accuracies": all_accuracies,
        }
