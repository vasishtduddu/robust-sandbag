"""Steering vector elicitation (WMDP only).

Trains a linear steering vector from contrast pairs (true/false or correct/incorrect)
and applies it at inference time to shift the model toward honest answers.

Requires the steering_vectors package: pip install steering-vectors
"""


import pandas as pd
import torch
from torch.utils.data import DataLoader
from tqdm import tqdm

from ..base import ElicitationTechnique, ElicitationType
from ...data.utils import set_seed
from ...utils.column_utils import (
    GROUND_TRUTH_ANSWER_COLUMN,
    get_question_column,
    get_correctness_column,
)
from ...utils.training import GenericTorchDataset, GenericPadCollate, add_logit_bias


class SteeringVector(ElicitationTechnique):
    """Steering vector elicitation using contrast pairs.

    Trains a steering vector from:
    - "true_false" contrast pairs: hard-coded true/false statement pairs (tests general honesty)
    - "data" contrast pairs: correct vs incorrect MCQA examples from the dataset

    Usage:
        tech = SteeringVector(model, tokenizer, dataset="wmdp", model_name="mistral-instruct",
                              multiplier=1.1, steering_layers=list(range(8, 16)))
        results = tech.run(eval_data=test_df, train_data=elicit_train_df)
    """

    SUPPORTED_DATASETS = ("wmdp",)
    elicitation_type = ElicitationType.WHITEBOX

    def __init__(
        self,
        model,
        tokenizer,
        dataset: str,
        model_name: str,
        contrast_pairs_source: str = "true_false",
        multiplier: float = 1.1,
        steer_at_last_n_tokens: int = 1,
        n_wrong_examples: int = 0,
        steering_layers: list[int] | None = None,
        seed: int = 42,
    ) -> None:
        """
        Args:
            contrast_pairs_source: "true_false" for generic honesty pairs or "data" for MCQA pairs.
            multiplier: Scaling factor for the steering vector at inference time.
            steer_at_last_n_tokens: Number of last tokens before answer to apply steering.
            n_wrong_examples: When source="data", number of incorrect examples to include.
            steering_layers: Layer indices to train and apply the vector. Default: layers 8-15.
        """
        super().__init__(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
        self.contrast_pairs_source = contrast_pairs_source
        self.multiplier = multiplier
        self.steer_at_last_n_tokens = steer_at_last_n_tokens
        self.n_wrong_examples = n_wrong_examples
        self.steering_layers = steering_layers if steering_layers is not None else list(range(8, 16))

    def _get_true_false_pairs(self) -> list[tuple[str, str]]:
        """Return hard-coded true/false statement contrast pairs for steering."""
        # Simple factual true/false pairs for training an honesty direction
        pairs = [
            ("The Earth orbits the Sun.", "The Sun orbits the Earth."),
            ("Water is H2O.", "Water is H3O."),
            ("Humans have 46 chromosomes.", "Humans have 23 chromosomes."),
            ("The speed of light is approximately 3×10^8 m/s.", "The speed of light is approximately 3×10^3 m/s."),
            ("DNA is a double helix.", "DNA is a single strand."),
            ("Paris is the capital of France.", "London is the capital of France."),
            ("The human heart has four chambers.", "The human heart has two chambers."),
            ("Photosynthesis produces oxygen.", "Photosynthesis produces nitrogen."),
            ("Antibiotics treat bacterial infections.", "Antibiotics treat viral infections."),
            ("The mitochondria is the powerhouse of the cell.", "The nucleus is the powerhouse of the cell."),
        ]
        return [(f"True: {t}", f"False: {f}") for t, f in pairs]

    def _get_data_contrast_pairs(
        self, data: pd.DataFrame, num_samples: int = 30
    ) -> list[tuple[str, str]]:
        """Create contrast pairs from MCQA data (correct vs wrong answer format).

        Args:
            data: DataFrame with question_prompt and answer columns.
            num_samples: How many contrast pairs to create.

        Returns:
            List of (positive_example, negative_example) string pairs.
        """
        question_column = get_question_column(self.model_name)
        if question_column not in data.columns:
            question_column = "question_prompt"

        sampled = data.sample(n=min(num_samples, len(data)), replace=False)
        contrast_pairs = []
        all_answers = ["A", "B", "C", "D"]

        for _, row in sampled.iterrows():
            q = str(row[question_column])
            correct = str(row[GROUND_TRUTH_ANSWER_COLUMN]).strip()
            wrong_choices = [a for a in all_answers if a != correct]
            wrong = wrong_choices[0]

            positive = q + f" {correct}"   # Correct answer
            negative = q + f" {wrong}"     # Wrong answer
            contrast_pairs.append((positive, negative))

        return contrast_pairs

    def run(
        self,
        eval_data: pd.DataFrame,
        train_data: pd.DataFrame | None = None,
        **kwargs,
    ) -> dict[str, float]:
        """Train steering vector and evaluate with it applied.

        Args:
            eval_data: DataFrame to evaluate on.
            train_data: Source for contrast pair creation (when source="data").

        Returns:
            dict with "accuracy" and related keys.
        """
        try:
            from steering_vectors import train_steering_vector
        except ImportError:
            raise ImportError(
                "steering_vectors is required for steering elicitation. "
                "Install with: pip install steering-vectors"
            )

        set_seed(self.seed)
        device = next(self.model.parameters()).device
        self.model.eval()
        torch.set_grad_enabled(False)

        # Build contrast pairs
        if self.contrast_pairs_source == "true_false":
            contrast_pairs = self._get_true_false_pairs()
        else:
            source = train_data if train_data is not None else eval_data
            contrast_pairs = self._get_data_contrast_pairs(source)

        # Train the steering vector
        steering_vec = train_steering_vector(
            self.model,
            self.tokenizer,
            contrast_pairs,
            layers=self.steering_layers,
            show_progress=True,
        )

        return self._eval_mcqa(eval_data, steering_vec)

    def _eval_mcqa(self, eval_data: pd.DataFrame, steering_vec) -> dict[str, float]:
        device = next(self.model.parameters()).device

        question_column = get_question_column(self.model_name)
        if question_column not in eval_data.columns:
            question_column = "question_prompt"

        eval_dataset = GenericTorchDataset(
            eval_data, prompt_column=question_column, completion_column=GROUND_TRUTH_ANSWER_COLUMN
        )
        eval_loader = DataLoader(
            eval_dataset,
            batch_size=16,
            shuffle=False,
            collate_fn=GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True),
        )

        from ...utils.training import get_abcd_bias
        bias_dict = get_abcd_bias(self.tokenizer, self.model_name)

        n_correct = 0
        for batch in tqdm(eval_loader, desc="Steering evaluation (MCQA)"):
            input_ids, attn_mask, labels = batch
            input_ids = input_ids.to(device)
            attn_mask = attn_mask.to(device)
            labels = labels.to(device)

            answer_indices = (labels == self.tokenizer.eos_token_id).nonzero()[:, 1] - 2

            steering_mask = torch.zeros_like(input_ids)
            for i in range(self.steer_at_last_n_tokens):
                steering_mask[range(len(steering_mask)), answer_indices - i] = 1

            with steering_vec.apply(
                self.model, multiplier=self.multiplier,
                token_indices=steering_mask, min_token_index=None
            ):
                logits = self.model(input_ids=input_ids, attention_mask=attn_mask, labels=labels).logits

            if bias_dict:
                logits = add_logit_bias(logits, answer_indices, bias_dict)

            answer_logits = logits[range(len(answer_indices)), answer_indices]
            _, answer_ids = answer_logits.topk(1)
            answers = self.tokenizer.batch_decode(answer_ids, skip_special_tokens=True)
            decoded_labels = torch.where(labels == -100, self.tokenizer.pad_token_id, labels)
            targets = self.tokenizer.batch_decode(decoded_labels, skip_special_tokens=True)

            for i, answer in enumerate(answers):
                if answer.strip().lower() == targets[i].strip().lower():
                    n_correct += 1

        n_correct = int(n_correct)
        return {
            "accuracy": n_correct / len(eval_data),
            "n_correct": n_correct,
            "n_total": len(eval_data),
        }

