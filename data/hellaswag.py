"""HellaSwag dataset for utility evaluation.

HellaSwag is a commonsense sentence-completion benchmark. Each example gives a
short context (activity description) and four possible endings; the model must
pick the most plausible one.

In Possum it serves as a second utility benchmark alongside MMLU:
  - MMLU  measures broad factual knowledge across 57 subjects
  - HellaSwag measures commonsense reasoning / language understanding

Dataset source: https://github.com/rowanz/hellaswag (validation split, 10,042 examples)
The validation file is downloaded automatically from GitHub raw content on first use.
"""

import csv
import json
import urllib.request
from pathlib import Path

import pandas as pd
from datasets import load_dataset

from .base import SandbagDataset
from ..utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN, BASE_QUESTION_COLUMN

# HuggingFace dataset name for the training split
_HF_DATASET = "Rowan/hellaswag"

_DATA_ROOT = Path(__file__).parent.parent / "datasets"

_VAL_URL = (
    "https://raw.githubusercontent.com/rowanz/hellaswag/master/data/hellaswag_val.jsonl"
)

_LABEL_TO_LETTER = {0: "A", 1: "B", 2: "C", 3: "D"}

# Prompt template — context + four ABCD endings
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


def _download_and_parse(url: str) -> pd.DataFrame:
    """Download a HellaSwag JSONL file and parse into a DataFrame."""
    print(f"Downloading HellaSwag validation set from {url} ...")
    rows = []
    with urllib.request.urlopen(url) as resp:
        for line in resp:
            obj = json.loads(line.decode("utf-8"))
            ctx      = obj["ctx"]
            endings  = obj["endings"]
            label    = int(obj["label"])
            activity = obj.get("activity_label", "")
            rows.append({
                "context":        ctx,
                "endings":        endings,
                "label":          label,
                "activity_label": activity,
            })
    return pd.DataFrame(rows)


def _preprocess(raw_df: pd.DataFrame) -> pd.DataFrame:
    """Convert raw HellaSwag rows (JSONL format) into the Possum column format."""
    records = []
    for _, row in raw_df.iterrows():
        prompt = _format_hellaswag_prompt(row["context"], row["endings"])
        answer = _LABEL_TO_LETTER[int(row["label"])]
        records.append({
            BASE_QUESTION_COLUMN:       prompt,
            GROUND_TRUTH_ANSWER_COLUMN: answer,
            "activity_label":           row["activity_label"],
        })
    return pd.DataFrame(records)


def _preprocess_hf(hf_split) -> pd.DataFrame:
    """Convert a HuggingFace HellaSwag split into the Possum column format."""
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
    """HellaSwag commonsense-completion dataset for utility evaluation.

    Uses the validation split (10,042 examples) as the test split, since the
    official HellaSwag test set has no public labels.

    The dataset is downloaded automatically on first use from GitHub if the
    pre-processed CSV is not already present locally.

    Usage::

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
        """Download the HellaSwag validation JSONL from GitHub and preprocess.

        Also loads the HuggingFace train split (~39k rows) and caches it as
        ``hellaswag_train.csv`` for use in the utility mix.
        """
        raw_df = _download_and_parse(_VAL_URL)
        self.splits["test"] = _preprocess(raw_df)

        hf_raw = load_dataset(_HF_DATASET)
        self.splits["train"] = _preprocess_hf(hf_raw["train"])

        self._loaded = True

    def get_split(self, split: str) -> pd.DataFrame:
        """Return the test split DataFrame.

        Args:
            split: Must be ``"test"``.

        Returns:
            pd.DataFrame with columns: question_prompt, answer, activity_label.
        """
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
        """Load pre-processed HellaSwag from CSV, downloading automatically if absent.

        Args:
            directory: Path to the ``mcqa/`` directory. Defaults to
                       ``<repo>/datasets/mcqa/``.

        Returns:
            A loaded ``HellaSwagDataset`` instance.
        """
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
            # Test CSV exists but train CSV is missing (e.g. pre-existing cache).
            # Re-run load() + save() to create hellaswag_train.csv alongside.
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
