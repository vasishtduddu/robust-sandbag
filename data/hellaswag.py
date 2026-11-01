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

"""HellaSwag (sentence completion, four endings): one of the two utility benchmarks.

Utility is scored on the labelled validation split (10,042 rows of Rowan/hellaswag on the Hugging
Face Hub, identical to the original hellaswag_val.jsonl release), which evaluate.py divides into a
1/3 validation and a 2/3 test set. The train split is an optional source of anchor rows
(WCMO_HELLASWAG_ANCHOR).
"""

import csv
from pathlib import Path

import pandas as pd
from datasets import load_dataset

from .base import SandbagDataset
from ..utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN, BASE_QUESTION_COLUMN

_HF_DATASET = "Rowan/hellaswag"

_DATA_ROOT = Path(__file__).parent.parent / "datasets"

_LABEL_TO_LETTER = {0: "A", 1: "B", 2: "C", 3: "D"}

# Prompt template: context + four ABCD endings
_HS_TEMPLATE = """{context}

A. {ending_a}
B. {ending_b}
C. {ending_c}
D. {ending_d}

Answer:"""


def _format_hellaswag_prompt(ctx: str, endings: list[str]) -> str:
    return _HS_TEMPLATE.format(
        context=ctx,
        ending_a=endings[0],
        ending_b=endings[1],
        ending_c=endings[2],
        ending_d=endings[3],
    )


def _preprocess_hf(hf_split) -> pd.DataFrame:
    """Convert a HuggingFace HellaSwag split to our column format."""
    records = []
    for example in hf_split:
        ctx = example["ctx"]
        endings = example["endings"]
        label = int(example["label"]) if example["label"] != "" else 0
        activity = example.get("activity_label", "")
        prompt = _format_hellaswag_prompt(ctx, endings)
        answer = _LABEL_TO_LETTER[label]
        records.append({
            BASE_QUESTION_COLUMN:       prompt,
            GROUND_TRUTH_ANSWER_COLUMN: answer,
            "activity_label":           activity,
        })
    return pd.DataFrame(records)


class HellaSwagDataset(SandbagDataset):
    """HellaSwag for utility evaluation; the labelled validation split serves as "test".

    Usage:

        # Explicit load + save (one-time setup):
        hs = HellaSwagDataset()
        hs.load()
        hs.save()

        # Auto-download on first access (preferred in evaluate.py):
        hs = HellaSwagDataset.from_directory()
        df = hs.get_split("test")
    """

    SPLIT_NAMES = ("test", "train")

    def __init__(self) -> None:
        super().__init__()

    def load(self, seed: int = 42, filter_long_questions: bool = False) -> None:
        """Load the labelled validation split (as "test") and the train split (~39k rows)."""
        hf_raw = load_dataset(_HF_DATASET)
        self.splits["test"] = _preprocess_hf(hf_raw["validation"])
        self.splits["train"] = _preprocess_hf(hf_raw["train"])

        self._loaded = True

    def get_split(self, split: str) -> pd.DataFrame:
        """Return the "test" split (columns: question_prompt, answer, activity_label)."""
        self._require_loaded()
        if split != "test":
            raise ValueError(
                f"HellaSwagDataset only provides the 'test' split (got {split!r})."
            )
        return self.splits["test"]

    def save(self, directory: str = None) -> None:
        """Save the test and train splits to CSV files under the given directory."""
        self._require_loaded()
        if directory is None:
            directory = str(_DATA_ROOT / "mcqa")
        out_dir = self._ensure_dir(directory)
        self.splits["test"].to_csv(
            out_dir / "hellaswag.csv", index=False, quoting=csv.QUOTE_ALL
        )
        print(f"Saved HellaSwag to {out_dir}/hellaswag.csv ({len(self.splits['test'])} rows)")
        if "train" in self.splits:
            self.splits["train"].to_csv(
                out_dir / "hellaswag_train.csv", index=False, quoting=csv.QUOTE_ALL
            )
            print(f"Saved HellaSwag train to {out_dir}/hellaswag_train.csv ({len(self.splits['train'])} rows)")

    @classmethod
    def from_directory(cls, directory: str = None) -> "HellaSwagDataset":
        """Load hellaswag.csv and hellaswag_train.csv (default datasets/mcqa/), downloading if absent."""
        if directory is None:
            directory = str(_DATA_ROOT / "mcqa")
        data_dir = Path(directory)
        csv_path = data_dir / "hellaswag.csv"

        if not csv_path.exists():
            # Auto-download and cache
            ds = cls()
            ds.load()
            ds.save(directory)
            return ds

        train_path = data_dir / "hellaswag_train.csv"
        if not train_path.exists():
            # Older cache without the train CSV: rebuild both.
            ds = cls()
            ds.load()
            ds.save(directory)
            return ds

        ds = cls.__new__(cls)
        super(HellaSwagDataset, ds).__init__()
        ds.splits["test"] = pd.read_csv(csv_path)
        ds.splits["train"] = pd.read_csv(train_path)
        ds._loaded = True
        return ds
