# Authors: Vasisht Duddu, Mark Wang, Lipeng He, Tim G. J. Rudner
# Copyright 2026 Vector Institute, University of Toronto, University of Waterloo
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Abstract base class for all datasets."""

from abc import ABC, abstractmethod
from pathlib import Path

import pandas as pd


class SandbagDataset(ABC):
    """Abstract base for all datasets: subclasses implement load(), get_split() and save().

    Columns follow utils/column_utils.py. The five splits (70/10/20 train/val/test):
        mos_train    (45%): organism training           | train
        elicit_train (25%): building elicitations       |
        mos_eval      (7%): validation                  | val
        elicit_eval   (3%): validation                  |
        test         (20%): held out, the only reported split

    evaluate.py pools mos_eval and elicit_eval into one validation set used for all
    hyperparameter, seed and model selection. Fractions live in data/wmdp.py (create_split).
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
