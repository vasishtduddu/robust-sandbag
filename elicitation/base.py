"""Abstract base classes for all elicitation techniques.

Elicitation techniques attempt to recover honest behavior from sandbagging models.
They are divided into:
  - Blackbox: only need model outputs (no weights/gradients required)
  - Whitebox: require access to model weights or internal representations

Usage:
    from sandbagBench.elicitation.base import ElicitationTechnique, ElicitationType

    class MyTechnique(ElicitationTechnique):
        elicitation_type = ElicitationType.BLACKBOX
        SUPPORTED_DATASETS = ("wmdp",)

        def run(self, eval_data, train_data=None, **kwargs):
            ...
            return {"accuracy": 0.75}
"""

from abc import ABC, abstractmethod
from enum import Enum

import pandas as pd


class ElicitationType(Enum):
    """Whether an elicitation technique requires model weights/internals."""
    BLACKBOX = "blackbox"
    WHITEBOX = "whitebox"


class ElicitationTechnique(ABC):
    """Abstract base class for all elicitation techniques.

    All techniques take the model and tokenizer at construction time, and a
    dataset + model_name to handle column naming. The run() method is called
    with evaluation data (and optionally training data for whitebox methods).

    SUPPORTED_DATASETS must be set by each subclass to declare compatibility.
    The evaluation CLI enforces this compatibility before calling run().
    """

    SUPPORTED_DATASETS: tuple[str, ...] = ()
    elicitation_type: ElicitationType

    def __init__(
        self,
        model,
        tokenizer,
        dataset: str,
        model_name: str,
        seed: int = 42,
    ) -> None:
        """
        Args:
            model: The HuggingFace model to elicit from.
            tokenizer: Corresponding tokenizer.
            dataset: Dataset name (e.g. "wmdp", "codegen"). Must be in SUPPORTED_DATASETS.
            model_name: Model name string (e.g. "mistral-instruct") for column naming.
            seed: Random seed for reproducibility.
        """
        if dataset not in self.SUPPORTED_DATASETS:
            raise ValueError(
                f"{self.__class__.__name__} does not support dataset '{dataset}'. "
                f"Supported: {self.SUPPORTED_DATASETS}"
            )
        self.model = model
        self.tokenizer = tokenizer
        self.dataset = dataset
        self.model_name = model_name
        self.seed = seed

    @abstractmethod
    def run(
        self,
        eval_data: pd.DataFrame,
        train_data: pd.DataFrame | None = None,
        **kwargs,
    ) -> dict[str, float]:
        """Run the elicitation technique and return accuracy metrics.

        Args:
            eval_data: DataFrame to evaluate on.
            train_data: Optional training data (required for whitebox methods
                        that fine-tune or compute contrast pairs).
            **kwargs: Technique-specific keyword arguments.

        Returns:
            Dict with at minimum {"accuracy": float}, plus any technique-specific
            metrics (e.g. {"accuracy_before": 0.3, "accuracy_after": 0.7}).
        """
        ...
