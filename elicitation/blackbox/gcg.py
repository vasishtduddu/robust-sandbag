"""GCG (Greedy Coordinate Gradient) adversarial suffix elicitation (WMDP only).

Finds an adversarial suffix that, when appended to MCQA prompts, causes the
sandbagging model to output a prefilling string ("Out of A, B, C, or D, the
correct answer is:"), which then reveals the correct answer.

Requires the optional nanogcg package:
    pip install nanogcg
"""

import pandas as pd

from ..base import ElicitationTechnique, ElicitationType
from ...data.utils import set_seed
from ...utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN
from .prefilling import Prefilling, get_message, get_chat_history, DEFAULT_PREFILLING_STRING


class GCG(ElicitationTechnique):
    """GCG adversarial suffix search for eliciting honest answers from sandbagging models.

    Searches for an adversarial suffix string that, when appended to questions,
    causes the model to continue with the prefilling string (indicating it's about
    to give the correct answer).

    Usage:
        tech = GCG(model, tokenizer, dataset="wmdp", model_name="mistral-instruct",
                   target_string="Out of A, B, C, or D, the correct answer is:",
                   num_steps=500)
        results = tech.run(eval_data=test_df, train_data=elicit_train_df)

    Requires nanogcg: pip install nanogcg
    """

    SUPPORTED_DATASETS = ("wmdp", "bio", "chem", "cyber",
                          "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss")
    elicitation_type = ElicitationType.BLACKBOX

    def __init__(
        self,
        model,
        tokenizer,
        dataset: str,
        model_name: str,
        n_shot: int = 0,
        target_string: str = DEFAULT_PREFILLING_STRING,
        num_steps: int = 500,
        search_width: int = 64,
        topk: int = 64,
        seed: int = 42,
    ) -> None:
        super().__init__(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
        self.n_shot = n_shot
        self.target_string = target_string
        self.num_steps = num_steps
        self.search_width = search_width
        self.topk = topk

    def _build_gcg_message(self, question_prompts: list[str], answers: list[str]) -> str:
        """Build the input message for GCG optimization.

        For instruct/it models, uses chat template with prefilling.
        For base models, uses plain-text message.
        """
        is_instruct = self.model_name not in ("mistral", "llama2-7b")
        if is_instruct:
            chat_history = get_chat_history(question_prompts, answers, self.target_string)
            return self.tokenizer.apply_chat_template(chat_history, tokenize=False, add_generation_prompt=True)
        return get_message(question_prompts, answers, self.target_string)

    def find_suffix(self, train_data: pd.DataFrame) -> str:
        """Run GCG to find an adversarial suffix.

        Args:
            train_data: DataFrame to sample the question from.

        Returns:
            Best adversarial suffix string found.
        """
        try:
            import nanogcg
            from nanogcg import GCGConfig
        except ImportError:
            raise ImportError(
                "nanogcg is required for GCG elicitation. "
                "Install it with: pip install nanogcg"
            )

        set_seed(self.seed)
        question_column = "question_prompt"

        n_questions = self.n_shot + 1
        question_prompts = list(train_data[question_column].iloc[:n_questions])
        answers = list(train_data[GROUND_TRUTH_ANSWER_COLUMN].iloc[:n_questions])
        message = self._build_gcg_message(question_prompts, answers)

        config = GCGConfig(
            num_steps=self.num_steps,
            search_width=self.search_width,
            topk=self.topk,
            seed=self.seed,
            verbosity="WARNING",
            use_prefix_cache=False,  # DynamicCache (transformers 5.x) is not subscriptable
        )

        result = nanogcg.run(self.model, self.tokenizer, message, self.target_string, config)
        return result.best_string

    def evaluate_with_suffix(self, suffix: str, eval_data: pd.DataFrame) -> float:
        """Evaluate accuracy when the GCG suffix is appended to each question.

        Args:
            suffix: Adversarial suffix to append.
            eval_data: DataFrame to evaluate on.

        Returns:
            Accuracy proportion.
        """
        question_column = "question_prompt"
        eval_data = eval_data.copy()
        eval_data[question_column] = eval_data[question_column].apply(
            lambda q: q.rsplit("\nAnswer:", 1)[0] + " " + suffix + "\nAnswer:"
            if "\nAnswer:" in q else q + " " + suffix
        )

        prefilling = Prefilling(
            model=self.model,
            tokenizer=self.tokenizer,
            dataset=self.dataset,
            model_name=self.model_name,
            n_shot=self.n_shot,
            prefilling_string=self.target_string,
            seed=self.seed,
        )
        results = prefilling.run(eval_data=eval_data)
        return results["accuracy"]

    def run(
        self,
        eval_data: pd.DataFrame,
        train_data: pd.DataFrame | None = None,
        suffix: str | None = None,
        **kwargs,
    ) -> dict[str, float]:
        """Find GCG suffix and evaluate with it.

        Args:
            eval_data: DataFrame to evaluate on.
            train_data: Source for building the GCG target prompt. Required unless suffix provided.
            suffix: Pre-computed suffix to skip the search step.

        Returns:
            dict with "accuracy", "suffix", and optionally "best_loss".
        """
        if suffix is None:
            if train_data is None:
                raise ValueError("train_data required for GCG suffix search (or pass suffix=)")
            try:
                import nanogcg
                from nanogcg import GCGConfig

                set_seed(self.seed)
                question_column = "question_prompt"
                n_questions = self.n_shot + 1
                question_prompts = list(train_data[question_column].iloc[:n_questions])
                answers = list(train_data[GROUND_TRUTH_ANSWER_COLUMN].iloc[:n_questions])
                message = self._build_gcg_message(question_prompts, answers)
                config = GCGConfig(
                    num_steps=self.num_steps,
                    search_width=self.search_width,
                    topk=self.topk,
                    seed=self.seed,
                    verbosity="WARNING",
                    use_prefix_cache=False,  # DynamicCache (transformers 5.x) is not subscriptable
                )
                result_obj = nanogcg.run(self.model, self.tokenizer, message, self.target_string, config)
                suffix = result_obj.best_string
                best_loss = result_obj.best_loss
            except ImportError:
                raise ImportError(
                    "nanogcg is required for GCG elicitation. Install with: pip install nanogcg"
                )
        else:
            best_loss = None

        accuracy = self.evaluate_with_suffix(suffix, eval_data)
        result = {"accuracy": accuracy, "suffix": suffix}
        if best_loss is not None:
            result["best_loss"] = best_loss
        return result
