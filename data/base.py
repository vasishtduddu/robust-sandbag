"""Abstract base class for all SandbagBench datasets."""

from abc import ABC, abstractmethod
from pathlib import Path

import pandas as pd


class SandbagDataset(ABC):
    """
    Abstract base for all SandbagBench datasets.

    Subclasses must implement load(), get_split(), and save().
    Each split is a pd.DataFrame with column conventions defined
    in utils/column_utils.py.

    Standard split names (WMDP/MCQA):
        mos_train    — model organism training (50%)
        mos_eval     — model organism evaluation (10%)
        elicit_train — elicitation method training (25%)
        elicit_eval  — elicitation method evaluation (5%)
        test         — final held-out test set (10%)

    CodeGen uses different split names; see CodeGenDataset.
    """

    SPLIT_NAMES: tuple[str, ...] = (
        "mos_train", "mos_eval", "elicit_train", "elicit_eval", "test"
    )

    def __init__(self) -> None:
        # Populated after load() is called
        self.splits: dict[str, pd.DataFrame] = {}
        self._loaded = False

    @abstractmethod
    def load(self, seed: int = 42, filter_long_questions: bool = False) -> None:
        """Download/preprocess the source data and populate self.splits."""
        ...

    @abstractmethod
    def get_split(self, split: str) -> pd.DataFrame:
        """Return the DataFrame for the named split.

        Raises ValueError if the split name is unknown or load() has not been called.
        """
        ...

    @abstractmethod
    def save(self, directory: str) -> None:
        """Write all splits as CSV files under the given directory."""
        ...

    @classmethod
    def from_directory(cls, directory: str) -> "SandbagDataset":
        """Load pre-processed splits from CSV files (skip re-downloading).

        Subclasses should override this to specify which filenames map to which splits.
        """
        raise NotImplementedError(
            f"{cls.__name__} does not implement from_directory(). "
            "Call load() to download and process the dataset."
        )

    def _require_loaded(self) -> None:
        """Raise RuntimeError if load() has not been called yet."""
        if not self._loaded:
            raise RuntimeError(
                "Dataset not loaded. Call load() first or use from_directory() "
                "to load pre-processed splits."
            )

    def _ensure_dir(self, directory: str) -> Path:
        """Create directory if it does not exist and return as Path."""
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        return path
