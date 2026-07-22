"""MMLU dataset for sandbagging evaluation.

MMLU (Massive Multitask Language Understanding) is used in Possum as a
**sandbagging effectiveness dataset**: organisms are trained to hide their
academic knowledge on selected MMLU subjects, analogous to hiding dangerous
knowledge on WMDP.

Three curated subcategories are defined (16 subjects total):
  mmlu-prof  — Professional Fields (5 subjects)
  mmlu-stem  — STEM Disciplines (6 subjects)
  mmlu-hss   — Humanities & Social Sciences (5 subjects)

Using ``--datasets mmlu`` covers all 16 subjects combined.

Utility preservation is measured by HellaSwag and CommonsenseQA, not MMLU.

Split convention (same as WMDPDataset), a clean 70-10-20 train/val/test grouping:
  mos_train    (45%): model organism training                      ┐ TRAIN 70%
  elicit_train (25%): elicitation attack construction (never loss) ┘
  mos_eval      (7%): pooled into VAL for tuning/selection         ┐ VAL 10%
  elicit_eval   (3%): pooled into VAL for tuning/selection         ┘
  test         (20%): final held-out test (the only reported split)

mos_eval and elicit_eval are NOT scored as distinct roles: evaluate.py concatenates
the two into ONE VAL set (VAL = mos_eval + elicit_eval) that carries all hyperparameter
tuning and seed/model selection, keeping TEST unseen until the final reported scoring.

Source data: cais/mmlu on HuggingFace (test + validation splits)
"""

import csv
from pathlib import Path
from typing import Optional

import pandas as pd
from datasets import load_dataset

from .base import SandbagDataset
from .utils import set_seed
from .wmdp import get_mcqa_prompt, preprocess_data, create_split
from ..utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN, BASE_QUESTION_COLUMN

_DATA_ROOT = Path(__file__).parent.parent / "datasets"

# ---------------------------------------------------------------------------
# Subject lists for the three curated MMLU subcategories
# ---------------------------------------------------------------------------

MMLU_PROF_SUBJECTS = (
    "jurisprudence",
    "clinical_knowledge",
    "professional_psychology",
    "professional_accounting",
    "professional_law",
)

MMLU_STEM_SUBJECTS = (
    "abstract_algebra",
    "college_chemistry",
    "computer_security",
    "machine_learning",
    "electrical_engineering",
    "college_physics",
)

MMLU_HSS_SUBJECTS = (
    "us_foreign_policy",
    "world_religions",
    "formal_logic",
    "high_school_macroeconomics",
    "moral_scenarios",
)

MMLU_ALL_SUBJECTS = MMLU_PROF_SUBJECTS + MMLU_STEM_SUBJECTS + MMLU_HSS_SUBJECTS  # 16 total


class MMLUDataset(SandbagDataset):
    """MMLU dataset for sandbagging evaluation of model organisms.

    By default loads the 16 curated subjects (mmlu-prof + mmlu-stem + mmlu-hss)
    and saves to ``mmlu_*.csv``. Subclasses override ``_CSV_PREFIX`` and
    ``__init__`` to load a specific subcategory.

    Uses the same five-split convention as WMDPDataset so that MMLU can be
    used as a sandbagging target (organisms trained to hide MMLU capability).

    Usage:
        mmlu = MMLUDataset()
        mmlu.load()
        train_df = mmlu.get_split("mos_train")
        eval_df  = mmlu.get_split("mos_eval")

        # Subject-specific query (any split)
        math_df = mmlu.get_split("mos_train", subject="abstract_algebra")

        # Load from cached CSVs (auto-downloads if absent)
        mmlu = MMLUDataset.from_directory()
    """

    SPLIT_NAMES = ("mos_train", "mos_eval", "elicit_train", "elicit_eval", "test")
    _CSV_PREFIX = "mmlu"

    def __init__(self, subjects: Optional[list[str]] = None) -> None:
        """
        Args:
            subjects: List of MMLU subject names to load.
                      Defaults to all 16 curated subjects (MMLU_ALL_SUBJECTS).
                      Pass ``None`` explicitly to load all 57 HuggingFace subjects.
        """
        super().__init__()
        self.subjects = subjects if subjects is not None else list(MMLU_ALL_SUBJECTS)

    def load(self, seed: int = 42, filter_long_questions: bool = False) -> None:
        """Download MMLU from HuggingFace, combine test+validation, and create 5 splits.

        Applies the same create_split() fractions as WMDPDataset so that MMLU
        can be treated as a sandbagging dataset with compatible train/eval splits.
        """
        set_seed(seed)

        test_dfs, val_dfs = [], []
        for subject in self.subjects:
            raw = load_dataset("cais/mmlu", subject)
            test_dfs.append(pd.DataFrame(raw["test"]))
            if "validation" in raw:
                val_dfs.append(pd.DataFrame(raw["validation"]))
        test_df = pd.concat(test_dfs, ignore_index=True)
        val_df  = pd.concat(val_dfs, ignore_index=True) if val_dfs else pd.DataFrame()

        # Combine test + validation to maximise split size
        combined = pd.concat([test_df, val_df], ignore_index=True) if len(val_df) else test_df

        # Apply the same 5-way split fractions as WMDP
        mos_train, mos_eval, elicit_train, elicit_eval, test = create_split(combined, seed=seed)

        self.splits["mos_train"]    = preprocess_data(mos_train,    filter_long_questions)
        self.splits["mos_eval"]     = preprocess_data(mos_eval,     filter_long_questions)
        self.splits["elicit_train"] = preprocess_data(elicit_train, filter_long_questions)
        self.splits["elicit_eval"]  = preprocess_data(elicit_eval,  filter_long_questions)
        self.splits["test"]         = preprocess_data(test,         filter_long_questions)

        self._loaded = True

    def get_split(self, split: str, subject: Optional[str] = None) -> pd.DataFrame:
        """Return the DataFrame for the given split, optionally filtered to one subject.

        Args:
            split:   One of "mos_train", "mos_eval", "elicit_train", "elicit_eval", "test".
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
        """Save splits as CSV files under the given directory."""
        self._require_loaded()
        if directory is None:
            directory = str(_DATA_ROOT / "mcqa")
        out_dir = self._ensure_dir(directory)
        prefix = self._CSV_PREFIX

        split_filenames = {
            "mos_train":    f"{prefix}_mos_train.csv",
            "mos_eval":     f"{prefix}_mos_eval.csv",
            "elicit_train": f"{prefix}_elicit_train.csv",
            "elicit_eval":  f"{prefix}_elicit_eval.csv",
            "test":         f"{prefix}_test.csv",
        }
        for split_name, filename in split_filenames.items():
            if split_name in self.splits:
                self.splits[split_name].to_csv(
                    out_dir / filename, index=False, quoting=csv.QUOTE_ALL
                )

        # Combined file for convenience
        full = pd.concat(list(self.splits.values()), ignore_index=True)
        full.to_csv(out_dir / f"{prefix}.csv", index=False, quoting=csv.QUOTE_ALL)

        print(f"Saved {self.__class__.__name__} splits to {out_dir}/")

    @classmethod
    def from_directory(cls, directory: str = None) -> "MMLUDataset":
        """Load pre-processed MMLU from CSV files (skips HF download).

        Falls back to re-downloading from HuggingFace if the split CSVs are absent.
        """
        if directory is None:
            directory = str(_DATA_ROOT / "mcqa")
        ds = cls.__new__(cls)
        super(MMLUDataset, ds).__init__()
        # subjects not needed after load; set to the class default
        ds.subjects = list(MMLU_ALL_SUBJECTS)
        data_dir = Path(directory)
        prefix = cls._CSV_PREFIX

        split_filenames = {
            "mos_train":    f"{prefix}_mos_train.csv",
            "mos_eval":     f"{prefix}_mos_eval.csv",
            "elicit_train": f"{prefix}_elicit_train.csv",
            "elicit_eval":  f"{prefix}_elicit_eval.csv",
            "test":         f"{prefix}_test.csv",
        }
        any_missing = any(not (data_dir / fn).exists() for fn in split_filenames.values())
        if any_missing:
            print(f"{cls.__name__} split CSVs not found. Downloading from HuggingFace (cais/mmlu)...")
            fresh = cls()
            fresh.load()
            fresh.save(directory)

        for split_name, filename in split_filenames.items():
            path = data_dir / filename
            if path.exists():
                ds.splits[split_name] = pd.read_csv(path)

        ds._loaded = True
        return ds


# ---------------------------------------------------------------------------
# Subcategory classes
# ---------------------------------------------------------------------------

class MMLUProfDataset(MMLUDataset):
    """MMLU Professional Fields subcategory (5 subjects).

    Subjects: jurisprudence, clinical_knowledge, professional_psychology,
              professional_accounting, professional_law.
    """
    _CSV_PREFIX = "mmlu_prof"

    def __init__(self) -> None:
        super().__init__(subjects=list(MMLU_PROF_SUBJECTS))


class MMLUStemDataset(MMLUDataset):
    """MMLU STEM Disciplines subcategory (6 subjects).

    Subjects: abstract_algebra, college_chemistry, computer_security,
              machine_learning, electrical_engineering, college_physics.
    """
    _CSV_PREFIX = "mmlu_stem"

    def __init__(self) -> None:
        super().__init__(subjects=list(MMLU_STEM_SUBJECTS))


class MMLUHssDataset(MMLUDataset):
    """MMLU Humanities & Social Sciences subcategory (5 subjects).

    Subjects: us_foreign_policy, world_religions, formal_logic,
              macroeconomics, moral_scenarios.
    """
    _CSV_PREFIX = "mmlu_hss"

    def __init__(self) -> None:
        super().__init__(subjects=list(MMLU_HSS_SUBJECTS))


# Mapping from CLI dataset name to dataset class
MMLU_CATEGORY_CLASSES = {
    "mmlu-prof": MMLUProfDataset,
    "mmlu-stem": MMLUStemDataset,
    "mmlu-hss":  MMLUHssDataset,
}
