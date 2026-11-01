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

"""CommonsenseQA (tau/commonsense_qa, five choices A-E): one of the two utility benchmarks.

Scored on the labelled validation split (1,221 rows), which evaluate.py divides into a 1/3
validation and a 2/3 test set. Forced-choice scoring uses an A-E logit bias
(utils/training.get_abcde_bias) so choice E can compete.
"""

import csv
from pathlib import Path

import pandas as pd
from datasets import load_dataset

from .base import SandbagDataset
from ..utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN, BASE_QUESTION_COLUMN

_DATA_ROOT = Path(__file__).parent.parent / "datasets"

# 5-choice prompt template (A-E)
_CSQA_TEMPLATE = """{question}

A. {choice_a}
B. {choice_b}
C. {choice_c}
D. {choice_d}
E. {choice_e}

Answer:"""


def _format_csqa_prompt(question: str, choices: dict) -> str:
    """Format a question and its HF choices dict ("label"/"text" lists) as an ABCDE prompt."""
    label_to_text = dict(zip(choices["label"], choices["text"]))
    return _CSQA_TEMPLATE.format(
        question=question,
        choice_a=label_to_text.get("A", ""),
        choice_b=label_to_text.get("B", ""),
        choice_c=label_to_text.get("C", ""),
        choice_d=label_to_text.get("D", ""),
        choice_e=label_to_text.get("E", ""),
    )


def _preprocess(raw) -> pd.DataFrame:
    """Convert a HuggingFace CommonsenseQA split to our column format."""
    records = []
    for example in raw:
        prompt  = _format_csqa_prompt(example["question"], example["choices"])
        answer  = str(example["answerKey"]).strip().upper()
        concept = example.get("question_concept", "")
        records.append({
            BASE_QUESTION_COLUMN:       prompt,
            GROUND_TRUTH_ANSWER_COLUMN: answer,
            "question_concept":         concept,
        })
    return pd.DataFrame(records)


class CommonsenseQADataset(SandbagDataset):
    """CommonsenseQA for utility evaluation; the labelled validation split serves as "test".

    Cached to datasets/mcqa/commonsenseqa.csv on first use.

    Usage:

        # Explicit load + save (one-time setup):
        csqa = CommonsenseQADataset()
        csqa.load()
        csqa.save()

        # Auto-download on first access (preferred in evaluate.py):
        csqa = CommonsenseQADataset.from_directory()
        df = csqa.get_split("test")

    Forced-choice scoring must use get_abcde_bias, not the ABCD bias, or option E is suppressed.
    """

    SPLIT_NAMES = ("test", "train")

    def __init__(self) -> None:
        super().__init__()

    def load(self, seed: int = 42, filter_long_questions: bool = False) -> None:
        """Download CommonsenseQA: validation becomes "test", train (~9.7k rows) feeds the utility mix."""
        print("Downloading CommonsenseQA from HuggingFace (tau/commonsense_qa)...")
        raw = load_dataset("tau/commonsense_qa")
        self.splits["test"] = _preprocess(raw["validation"])
        self.splits["train"] = _preprocess(raw["train"])
        self._loaded = True

    def get_split(self, split: str) -> pd.DataFrame:
        """Return "test" or "train" (columns: question_prompt, answer, question_concept)."""
        self._require_loaded()
        if split not in self.splits:
            raise ValueError(
                f"CommonsenseQADataset only provides {self.SPLIT_NAMES!r} (got {split!r})."
            )
        return self.splits[split]

    def save(self, directory: str = None) -> None:
        """Save the test and train splits to CSV files under the given directory."""
        self._require_loaded()
        if directory is None:
            directory = str(_DATA_ROOT / "mcqa")
        out_dir = self._ensure_dir(directory)
        self.splits["test"].to_csv(
            out_dir / "commonsenseqa.csv", index=False, quoting=csv.QUOTE_ALL
        )
        print(
            f"Saved CommonsenseQA to {out_dir}/commonsenseqa.csv "
            f"({len(self.splits['test'])} rows)"
        )
        if "train" in self.splits:
            self.splits["train"].to_csv(
                out_dir / "commonsenseqa_train.csv", index=False, quoting=csv.QUOTE_ALL
            )
            print(
                f"Saved CommonsenseQA train to {out_dir}/commonsenseqa_train.csv "
                f"({len(self.splits['train'])} rows)"
            )

    @classmethod
    def from_directory(cls, directory: str = None) -> "CommonsenseQADataset":
        """Load commonsenseqa.csv and commonsenseqa_train.csv (default datasets/mcqa/), downloading if absent."""
        if directory is None:
            directory = str(_DATA_ROOT / "mcqa")
        data_dir = Path(directory)
        csv_path = data_dir / "commonsenseqa.csv"

        if not csv_path.exists():
            ds = cls()
            ds.load()
            ds.save(directory)
            return ds

        train_path = data_dir / "commonsenseqa_train.csv"
        if not train_path.exists():
            # Older cache without the train CSV: rebuild both.
            ds = cls()
            ds.load()
            ds.save(directory)
            return ds

        ds = cls.__new__(cls)
        super(CommonsenseQADataset, ds).__init__()
        ds.splits["test"] = pd.read_csv(csv_path)
        ds.splits["train"] = pd.read_csv(train_path)
        ds._loaded = True
        return ds
