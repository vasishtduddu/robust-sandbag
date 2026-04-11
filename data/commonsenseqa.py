"""CommonsenseQA dataset for utility evaluation.

CommonsenseQA is a multiple-choice commonsense reasoning benchmark. Each
example contains a question and five answer choices (A–E); the model must
select the correct one.

In SandbagBench it serves as a third utility benchmark alongside MMLU and
HellaSwag, probing a complementary slice of language understanding.

Note on evaluation: CommonsenseQA has five choices (A–E), so the four-choice
ABCD logit bias used for MMLU/HellaSwag is intentionally omitted here —
raw logits are used instead so that choice E can compete fairly.

Dataset source: tau/commonsense_qa on HuggingFace (validation split, 1,221 examples).
Downloaded automatically on first use.
"""

import csv
from pathlib import Path

import pandas as pd
from datasets import load_dataset

from .base import SandbagDataset
from ..utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN, BASE_QUESTION_COLUMN

_DATA_ROOT = Path(__file__).parent.parent / "datasets"

# 5-choice prompt template (A–E)
_CSQA_TEMPLATE = """{question}

A. {choice_a}
B. {choice_b}
C. {choice_c}
D. {choice_d}
E. {choice_e}

Answer:"""


def _format_csqa_prompt(question: str, choices: dict) -> str:
    """Format a CommonsenseQA example into the standard ABCDE prompt.

    Args:
        question: The question string.
        choices:  HF choices dict with ``"label"`` and ``"text"`` lists.

    Returns:
        Formatted prompt string ending with ``"Answer:"``.
    """
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
    """Convert a HuggingFace CommonsenseQA split to the SandbagBench format."""
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
    """CommonsenseQA dataset for utility evaluation of model organisms.

    Uses the validation split (1,221 examples) since the official test split
    has no public answer labels.

    Downloaded automatically from HuggingFace on first use and cached to
    ``datasets/mcqa/commonsenseqa.csv``.

    Usage::

        # Explicit load + save (one-time setup):
        csqa = CommonsenseQADataset()
        csqa.load()
        csqa.save()

        # Auto-download on first access (preferred in evaluate.py):
        csqa = CommonsenseQADataset.from_directory()
        df = csqa.get_split("test")

    Note:
        This dataset has 5 choices (A–E). When evaluating, do **not** pass the
        ABCD ``bias_dict`` — use ``bias_dict=None`` so that option E is not
        suppressed.
    """

    SPLIT_NAMES = ("test", "train")

    def __init__(self) -> None:
        super().__init__()

    def load(self, seed: int = 42, filter_long_questions: bool = False) -> None:
        """Download CommonsenseQA from HuggingFace and preprocess.

        Uses the validation split (labels are available; test split labels
        are withheld) as the test split, and the train split (~9.7k rows)
        for the utility mix.
        """
        print("Downloading CommonsenseQA from HuggingFace (tau/commonsense_qa)...")
        raw = load_dataset("tau/commonsense_qa")
        self.splits["test"] = _preprocess(raw["validation"])
        self.splits["train"] = _preprocess(raw["train"])
        self._loaded = True

    def get_split(self, split: str) -> pd.DataFrame:
        """Return the requested split DataFrame.

        Args:
            split: ``"test"`` or ``"train"``.

        Returns:
            pd.DataFrame with columns: question_prompt, answer, question_concept.
        """
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
        """Load pre-processed CommonsenseQA from CSV, downloading if absent.

        Args:
            directory: Path to the ``mcqa/`` directory. Defaults to
                       ``<repo>/datasets/mcqa/``.

        Returns:
            A loaded ``CommonsenseQADataset`` instance.
        """
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
            # Test CSV exists but train CSV is missing (e.g. pre-existing cache).
            # Re-run load() + save() to create commonsenseqa_train.csv alongside.
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
