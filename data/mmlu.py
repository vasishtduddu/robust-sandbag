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

"""MMLU (cais/mmlu, test + validation) as a sandbagging target.

Three curated subcategories (16 subjects): mmlu-prof (5), mmlu-stem (6), mmlu-hss (5);
--datasets mmlu covers all 16. Splits follow WMDPDataset (see data/base.py). Utility is
measured on HellaSwag and CommonsenseQA, not MMLU.
"""

import csv
from pathlib import Path
from typing import Optional

import pandas as pd
from datasets import load_dataset

from .base import SandbagDataset
from .utils import set_seed
from .wmdp import preprocess_data, create_split

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


def mmlu_anchor_subjects() -> list[str]:
    """The MMLU subjects outside the 16 curated ones (the remaining 41 of 57).

    Used for anchor rows (non-target questions the organism must keep answering correctly)
    when the target covers all curated subjects, so no anchor row is also a target row.
    """
    from datasets import get_dataset_config_names

    skip = set(MMLU_ALL_SUBJECTS) | {"all", "auxiliary_train"}
    return [c for c in get_dataset_config_names("cais/mmlu") if c not in skip]


class MMLUDataset(SandbagDataset):
    """MMLU over the 16 curated subjects by default, saved as mmlu_*.csv.

    Subclasses override _CSV_PREFIX and __init__ to load one subcategory.

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
        """subjects: MMLU subject names to load; None means the 16 curated subjects."""
        super().__init__()
        self.subjects = subjects if subjects is not None else list(MMLU_ALL_SUBJECTS)

    def load(self, seed: int = 42, filter_long_questions: bool = False) -> None:
        """Download MMLU, combine test and validation, and create the five splits (create_split)."""
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

        # Same fractions as WMDP; the global RNG (seeded above) reproduces the released splits.
        mos_train, mos_eval, elicit_train, elicit_eval, test = create_split(combined, seed=None)

        self.splits["mos_train"]    = preprocess_data(mos_train,    filter_long_questions)
        self.splits["mos_eval"]     = preprocess_data(mos_eval,     filter_long_questions)
        self.splits["elicit_train"] = preprocess_data(elicit_train, filter_long_questions)
        self.splits["elicit_eval"]  = preprocess_data(elicit_eval,  filter_long_questions)
        self.splits["test"]         = preprocess_data(test,         filter_long_questions)

        self._loaded = True

    def get_split(self, split: str, subject: Optional[str] = None) -> pd.DataFrame:
        """Return a split, optionally restricted to one subject."""
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
        """Load saved split CSVs, downloading and saving them first if any are missing."""
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

class MMLUAnchorDataset(MMLUDataset):
    """The 41 non-curated MMLU subjects, cached as mmlu_anchor_*.csv.

    Never a sandbagging target: anchor rows for utility_mix.load_general_anchor when the
    target covers all of MMLU_ALL_SUBJECTS.
    """

    _CSV_PREFIX = "mmlu_anchor"

    def __init__(self, subjects: Optional[list[str]] = None) -> None:
        super().__init__(subjects=subjects if subjects is not None else mmlu_anchor_subjects())


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
              high_school_macroeconomics, moral_scenarios.
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
