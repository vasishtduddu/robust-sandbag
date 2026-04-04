"""MMLU dataset for utility evaluation.

MMLU (Massive Multitask Language Understanding) measures a model's general
knowledge across 57 subjects. In SandbagBench it serves as the utility
benchmark: we compare a base model's MMLU accuracy against its model organism
variant to check whether sandbagging degrades general capability.

This is intentionally separate from WMDPDataset:
  - WMDP measures sandbagging *effectiveness* (does the organism hide dangerous knowledge?)
  - MMLU measures *utility preservation* (does sandbagging break general capability?)

Dataset source: cais/mmlu on HuggingFace
"""

import csv
from pathlib import Path
from typing import Optional

import pandas as pd
from datasets import load_dataset

from .base import SandbagDataset
from .utils import set_seed
from .wmdp import get_mcqa_prompt, preprocess_data
from ..utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN, BASE_QUESTION_COLUMN

_DATA_ROOT = Path(__file__).parent.parent / "datasets"


class MMLUDataset(SandbagDataset):
    """MMLU dataset for utility evaluation of model organisms.

    Does NOT use the mos/elicit split convention — MMLU is evaluation-only.
    Only "test" and "eval" (validation) splits are provided.

    Usage:
        mmlu = MMLUDataset()
        mmlu.load()
        test_df = mmlu.get_split("test")

        # Subject-specific evaluation
        math_df = mmlu.get_split("test", subject="abstract_algebra")

    Adding new utility datasets:
        Subclass SandbagDataset, implement load()/get_split()/save().
        For MCQA datasets, reuse get_mcqa_prompt() from data/wmdp.py.
    """

    SPLIT_NAMES = ("test", "eval")

    def __init__(self, subjects: Optional[list[str]] = None) -> None:
        """
        Args:
            subjects: Optional list of MMLU subject names to load.
                      None (default) loads all 57 subjects.
        """
        super().__init__()
        self.subjects = subjects  # None = all subjects

    def load(self, seed: int = 42, filter_long_questions: bool = False) -> None:
        """Download MMLU from HuggingFace and populate test/eval splits.

        Uses the 'all' config to load all subjects at once, or individually
        if self.subjects is specified.
        """
        set_seed(seed)

        if self.subjects is None:
            # Load all subjects at once
            raw = load_dataset("cais/mmlu", "all")
            test_df = pd.DataFrame(raw["test"])
            # MMLU on HF has test / validation / dev splits
            val_df = pd.DataFrame(raw["validation"]) if "validation" in raw else None
        else:
            # Load individual subjects and concatenate
            test_dfs, val_dfs = [], []
            for subject in self.subjects:
                raw = load_dataset("cais/mmlu", subject)
                test_dfs.append(pd.DataFrame(raw["test"]))
                if "validation" in raw:
                    val_dfs.append(pd.DataFrame(raw["validation"]))
            test_df = pd.concat(test_dfs, ignore_index=True)
            val_df = pd.concat(val_dfs, ignore_index=True) if val_dfs else None

        self.splits["test"] = preprocess_data(test_df, filter_long_questions)
        if val_df is not None:
            self.splits["eval"] = preprocess_data(val_df, filter_long_questions)

        self._loaded = True

    def get_split(self, split: str, subject: Optional[str] = None) -> pd.DataFrame:
        """Return the DataFrame for the given split, optionally filtered to one subject.

        Args:
            split: "test" or "eval".
            subject: If given, return only rows where the "subject" column matches.

        Returns:
            pd.DataFrame for the requested split.
        """
        self._require_loaded()
        if split not in self.splits:
            raise ValueError(f"Unknown split '{split}'. Available: {list(self.splits.keys())}")

        df = self.splits[split]
        if subject is not None:
            df = df[df["subject"] == subject].reset_index(drop=True)
        return df

    def save(self, directory: str = None) -> None:
        """Save MMLU splits as CSV files under the given directory."""
        self._require_loaded()
        if directory is None:
            directory = str(_DATA_ROOT / "mcqa")
        out_dir = self._ensure_dir(directory)

        if "test" in self.splits:
            self.splits["test"].to_csv(
                out_dir / "mmlu.csv", index=False, quoting=csv.QUOTE_ALL
            )
        if "eval" in self.splits:
            self.splits["eval"].to_csv(
                out_dir / "mmlu_eval.csv", index=False, quoting=csv.QUOTE_ALL
            )
        print(f"Saved MMLU splits to {out_dir}/")

    @classmethod
    def from_directory(cls, directory: str = None) -> "MMLUDataset":
        """Load pre-processed MMLU from CSV files (skips HF download)."""
        if directory is None:
            directory = str(_DATA_ROOT / "mcqa")
        ds = cls.__new__(cls)
        super(MMLUDataset, ds).__init__()
        ds.subjects = None
        data_dir = Path(directory)

        test_path = data_dir / "mmlu.csv"
        if not test_path.exists():
            raise FileNotFoundError(
                f"Expected {test_path}. Run MMLUDataset().load() and .save() first."
            )
        ds.splits["test"] = pd.read_csv(test_path)

        eval_path = data_dir / "mmlu_eval.csv"
        if eval_path.exists():
            ds.splits["eval"] = pd.read_csv(eval_path)

        ds._loaded = True
        return ds
