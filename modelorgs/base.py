"""Abstract base class for all SandbagBench model organisms."""

from abc import ABC, abstractmethod


class ModelOrganism(ABC):
    """Abstract base for all model organisms (sandbagged models).

    A ModelOrganism wraps a base model and optionally a training procedure
    to produce a model that strategically underperforms on evaluations.

    Subclasses must implement train(), save(), and set SUPPORTED_DATASETS.

    Adding new model organism types:
        1. Subclass ModelOrganism
        2. Set SUPPORTED_DATASETS = ("wmdp",) or ("wmdp", "bio", "chem", "cyber")
        3. Implement train() and save()
        4. Register in modelorgs/__init__.py
        5. Add a case in train_modorg.py CLI
    """

    SUPPORTED_DATASETS: tuple[str, ...] = ()

    def __init__(self, model_name: str, dataset: str, seed: int = 42) -> None:
        if dataset not in self.SUPPORTED_DATASETS:
            raise ValueError(
                f"{self.__class__.__name__} does not support dataset '{dataset}'. "
                f"Supported: {self.SUPPORTED_DATASETS}"
            )
        self.model_name = model_name
        self.dataset = dataset
        self.seed = seed

    @abstractmethod
    def train(self) -> None:
        """Fine-tune or configure the model for sandbagging."""
        ...

    @abstractmethod
    def save(self, output_path: str) -> None:
        """Persist the trained model organism to disk."""
        ...

    @classmethod
    def from_checkpoint(cls, checkpoint_path: str, model_name: str, dataset: str = "wmdp") -> "ModelOrganism":
        """Load a previously trained organism from disk. Override in subclasses."""
        raise NotImplementedError(
            f"{cls.__name__} does not implement from_checkpoint(). "
            "Instantiate the class and load manually."
        )
