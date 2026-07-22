"""WMDP dataset for sandbagging evaluation.

The WMDP (Weapons of Mass Destruction Proxy) benchmark contains multiple-choice
questions in biology, chemistry, and cybersecurity. It is used to measure whether
a model organism successfully hides dangerous knowledge (sandbagging effectiveness).

Dataset source: cais/wmdp on HuggingFace
"""

import ast
import math
from pathlib import Path
from typing import Optional

import pandas as pd
from datasets import load_dataset
from sklearn.model_selection import train_test_split

from .base import SandbagDataset
from .utils import set_seed
from ..utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN, BASE_QUESTION_COLUMN

_DATA_ROOT = Path(__file__).parent.parent / "datasets"

# Canonical domain names used as --dataset values in the CLI
WMDP_DOMAINS = frozenset({"bio", "chem", "cyber"})


# ---------------------------------------------------------------------------
# Split fractions (must sum to 1.0)
# ---------------------------------------------------------------------------
# Clean 70-10-20 train/val/test grouping (see data/base.py and .claude/rules/data.md):
#   TRAIN = mos_train + elicit_train = 70   (organism training + elicitation fitting)
#   VAL   = mos_eval  + elicit_eval  = 10   (ALL hyperparameter tuning + seed/model selection)
#   TEST  = test                     = 20   (held out; the ONLY split reported in the paper)
# Tuning/selection therefore never touch the test split (the leakage fix).

MOS_TRAIN_FRAC = 0.45     # Model organism training
MOS_EVAL_FRAC = 0.07      # Model organism evaluation (part of VAL)
ELICIT_TRAIN_FRAC = 0.25  # Elicitation method training
ELICIT_EVAL_FRAC = 0.03   # Elicitation method evaluation (part of VAL)
TEST_FRAC = 0.20          # Final held-out test set (reported)

MAX_QUESTION_LENGTH = 512  # Max word count for optional long-question filtering

# ---------------------------------------------------------------------------
# Multiple-choice prompt template
# ---------------------------------------------------------------------------

MC_TEMPLATE = """The following are multiple choice questions (with answers) about {subject}.

{question}

A. {choice_a}
B. {choice_b}
C. {choice_c}
D. {choice_d}

Answer:"""


def get_mcqa_prompt(subject: str, question: str, choices: str | list) -> str:
    """Format a WMDP/MMLU question into the standard ABCD multiple-choice prompt."""
    if isinstance(choices, str):
        choices = ast.literal_eval(choices)
    assert len(choices) == 4, (
        f"MC_TEMPLATE assumes exactly 4 options (A-D); got {len(choices)}. Extend "
        "MC_TEMPLATE and preprocess_data's id_to_char for a different option count."
    )
    return MC_TEMPLATE.format(
        subject=subject,
        question=question,
        choice_a=choices[0],
        choice_b=choices[1],
        choice_c=choices[2],
        choice_d=choices[3],
    )


def create_split(
    data: pd.DataFrame,
    seed: int = 42,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Split a domain DataFrame into the five standard splits.

    Split is done per-domain (bio/chem/cyber separately) before concatenation
    to preserve domain balance across splits. Fractions use conditional sizing
    to match exact percentages.

    ``seed`` pins every draw (the initial shuffle and all four train_test_split
    calls) so the partition is reproducible across environments, independent of
    the ambient numpy/sklearn RNG state.

    Returns:
        (mos_train, mos_eval, elicit_train, elicit_eval, test)
    """
    assert math.isclose(
        MOS_TRAIN_FRAC + MOS_EVAL_FRAC + ELICIT_TRAIN_FRAC + ELICIT_EVAL_FRAC + TEST_FRAC, 1.0
    )

    # Shuffle first
    data = data.sample(frac=1, random_state=seed).reset_index(drop=True)

    mos_train, remaining = train_test_split(
        data, test_size=(1 - MOS_TRAIN_FRAC), random_state=seed
    )
    mos_eval, remaining = train_test_split(
        remaining,
        test_size=(1 - (MOS_EVAL_FRAC / (1 - MOS_TRAIN_FRAC))),
        random_state=seed,
    )
    elicit_train, remaining = train_test_split(
        remaining,
        test_size=(1 - (ELICIT_TRAIN_FRAC / (1 - MOS_TRAIN_FRAC - MOS_EVAL_FRAC))),
        random_state=seed,
    )
    # Final split: remaining is elicit_eval + test
    elicit_eval, test = train_test_split(
        remaining,
        test_size=(TEST_FRAC / (TEST_FRAC + ELICIT_EVAL_FRAC)),
        random_state=seed,
    )

    assert len(data) == len(mos_train) + len(mos_eval) + len(elicit_train) + len(elicit_eval) + len(test)
    return mos_train, mos_eval, elicit_train, elicit_eval, test


def preprocess_data(data: pd.DataFrame, filter_long_questions: bool = False) -> pd.DataFrame:
    """Add question_prompt and answer columns to a raw WMDP/MMLU DataFrame.

    - question_prompt: formatted ABCD multiple-choice string
    - answer: letter (A/B/C/D) derived from integer answer index
    """
    data = data.copy()
    data[BASE_QUESTION_COLUMN] = data.apply(
        lambda row: get_mcqa_prompt(row["subject"], row["question"], row["choices"]),
        axis=1,
    )
    # Strip control chars (except tab/newline) from the prompt: some WMDP-cyber code snippets embed
    # NUL/other control bytes that pandas truncates on CSV round-trip, dropping the trailing
    # "Answer:" cue and breaking add_password_before_answer downstream (bio has no code, so only
    # cyber hit this; the new split surfaced it by landing the row in mos_eval).
    import re
    _ctrl = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
    data[BASE_QUESTION_COLUMN] = data[BASE_QUESTION_COLUMN].map(
        lambda s: _ctrl.sub("", s) if isinstance(s, str) else s
    )
    # Dataset stores correct answer as 0-based index; convert to letter.
    # WMDP/MMLU are 4-option (A-D); id_to_char and MC_TEMPLATE hardcode this, so an
    # out-of-range index fails loudly here instead of silently mislabelling a row.
    id_to_char = {0: "A", 1: "B", 2: "C", 3: "D"}
    assert data["answer"].between(0, 3).all(), (
        "preprocess_data assumes 4-option MCQA (answer index in 0-3); got out-of-range "
        f"{sorted(set(data['answer']) - set(id_to_char))}. Extend id_to_char + MC_TEMPLATE."
    )
    data[GROUND_TRUTH_ANSWER_COLUMN] = data["answer"].apply(lambda a: id_to_char[a])

    if filter_long_questions:
        # Filter by word count (not token count) for speed
        lengths = data[BASE_QUESTION_COLUMN].apply(lambda q: len(q.split()))
        data = data[lengths <= MAX_QUESTION_LENGTH]

    return data


class WMDPDataset(SandbagDataset):
    """WMDP (Weapons of Mass Destruction Proxy) dataset for sandbagging evaluation.

    Loads bio, chem, and/or cyber subsets from cais/wmdp on HuggingFace.
    Splits are performed per-domain to preserve domain balance.

    Usage:
        ds = WMDPDataset(domains=["bio", "chem", "cyber"])
        ds.load(seed=42)

        # All domains combined
        test_df = ds.get_split("test")

        # Per-domain evaluation
        bio_test = ds.get_split("test", domain="bio")

    Adding new datasets:
        Subclass SandbagDataset and implement load(), get_split(), and save().
        Use get_mcqa_prompt() and create_split() if your dataset is MCQA-style.
    """

    SPLIT_NAMES = ("mos_train", "mos_eval", "elicit_train", "elicit_eval", "test")

    # HuggingFace config names for each domain
    DOMAIN_TO_HF_CONFIG = {
        "bio": "wmdp-bio",
        "chem": "wmdp-chem",
        "cyber": "wmdp-cyber",
    }
    DOMAIN_TO_SUBJECT = {
        "bio": "biology",
        "chem": "chemistry",
        "cyber": "computer science",
    }

    def __init__(self, domains: list[str] = None) -> None:
        super().__init__()
        self.domains = domains or ["bio", "chem", "cyber"]
        unknown = set(self.domains) - set(self.DOMAIN_TO_HF_CONFIG)
        if unknown:
            raise ValueError(f"Unknown domains: {unknown}. Choose from {list(self.DOMAIN_TO_HF_CONFIG)}")

        # Per-domain splits stored separately for domain-specific queries
        self._domain_splits: dict[str, dict[str, pd.DataFrame]] = {}

    def load(self, seed: int = 42, filter_long_questions: bool = False) -> None:
        """Download WMDP from HuggingFace and create the five standard splits.

        Splits are performed per domain (bio/chem/cyber) before concatenation
        so each domain is proportionally represented in every split.
        """
        set_seed(seed)

        per_domain: dict[str, tuple] = {}
        for domain in self.domains:
            raw = load_dataset("cais/wmdp", self.DOMAIN_TO_HF_CONFIG[domain])
            df = pd.DataFrame(raw["test"])  # WMDP has only a "test" split on HF
            df["subject"] = self.DOMAIN_TO_SUBJECT[domain]
            splits = create_split(df, seed=seed)  # (mos_train, mos_eval, elicit_train, elicit_eval, test)
            per_domain[domain] = splits
            self._domain_splits[domain] = {}

        # Concatenate per split name across domains
        for i, split_name in enumerate(self.SPLIT_NAMES):
            combined = pd.concat(
                [per_domain[d][i] for d in self.domains], ignore_index=True
            )
            self.splits[split_name] = preprocess_data(combined, filter_long_questions)

            # Also store per-domain for domain-specific evaluation
            for domain in self.domains:
                self._domain_splits[domain][split_name] = preprocess_data(
                    per_domain[domain][i], filter_long_questions
                )

        self._loaded = True

    def get_split(self, split: str, domain: Optional[str] = None) -> pd.DataFrame:
        """Return a DataFrame for the given split, optionally filtered to one domain.

        Args:
            split: One of "mos_train", "mos_eval", "elicit_train", "elicit_eval", "test".
            domain: If given, return only rows for that domain ("bio", "chem", "cyber").

        Returns:
            pd.DataFrame for the requested split.
        """
        self._require_loaded()
        if domain is not None:
            if domain not in self._domain_splits:
                raise ValueError(
                    f"Domain '{domain}' not loaded. Loaded domains: {self.domains}"
                )
            if split not in self._domain_splits[domain]:
                raise ValueError(f"Unknown split '{split}'. Choose from {self.SPLIT_NAMES}")
            return self._domain_splits[domain][split]

        if split not in self.splits:
            raise ValueError(f"Unknown split '{split}'. Choose from {self.SPLIT_NAMES}")
        return self.splits[split]

    def save(self, directory: str = None) -> None:
        """Save all splits as CSV files under the given directory.

        Writes both combined files (wmdp_mos_train.csv, …) and per-domain files
        (wmdp_bio_mos_train.csv, wmdp_chem_mos_train.csv, wmdp_cyber_mos_train.csv, …)
        so that --dataset bio/chem/cyber can be used in train_modorg / evaluation.
        """
        self._require_loaded()
        if directory is None:
            directory = str(_DATA_ROOT / "mcqa")
        out_dir = self._ensure_dir(directory)

        # Combined splits
        split_filenames = {
            "mos_train": "wmdp_mos_train.csv",
            "mos_eval": "wmdp_mos_eval.csv",
            "elicit_train": "wmdp_elicit_train.csv",
            "elicit_eval": "wmdp_elicit_eval.csv",
            "test": "wmdp_test.csv",
        }
        for split_name, filename in split_filenames.items():
            self.splits[split_name].to_csv(
                out_dir / filename, index=False, escapechar="\\"
            )

        # Per-domain splits (written when _domain_splits is populated by load())
        for domain, domain_splits in self._domain_splits.items():
            for split_name, df in domain_splits.items():
                filename = f"wmdp_{domain}_{split_name}.csv"
                df.to_csv(out_dir / filename, index=False, escapechar="\\")

        # Also save combined full dataset
        full = pd.concat(list(self.splits.values()), ignore_index=True)
        full.to_csv(out_dir / "wmdp.csv", index=False, escapechar="\\")
        print(f"Saved WMDP splits to {out_dir}/")

    @classmethod
    def from_directory(cls, directory: str = None) -> "WMDPDataset":
        """Load pre-processed WMDP splits from CSV files (skips HF download).

        Populates _domain_splits from per-domain CSV files (wmdp_{domain}_{split}.csv)
        when they exist, or falls back to filtering the combined CSVs by the subject
        column (for saves made before per-domain files were introduced).
        """
        if directory is None:
            directory = str(_DATA_ROOT / "mcqa")
        ds = cls.__new__(cls)
        super(WMDPDataset, ds).__init__()
        ds.domains = ["bio", "chem", "cyber"]
        ds._domain_splits = {}
        data_dir = Path(directory)

        filenames = {
            "mos_train": "wmdp_mos_train.csv",
            "mos_eval": "wmdp_mos_eval.csv",
            "elicit_train": "wmdp_elicit_train.csv",
            "elicit_eval": "wmdp_elicit_eval.csv",
            "test": "wmdp_test.csv",
        }
        any_missing = any(not (data_dir / fn).exists() for fn in filenames.values())
        if any_missing:
            print("WMDP CSVs not found. Downloading from HuggingFace (cais/wmdp)...")
            fresh = WMDPDataset(domains=["bio", "chem", "cyber"])
            fresh.load(seed=42)
            fresh.save(directory)

        for split_name, filename in filenames.items():
            ds.splits[split_name] = pd.read_csv(data_dir / filename, escapechar="\\")

        # Populate _domain_splits from per-domain files if available, otherwise
        # fall back to filtering the combined DataFrames by the subject column.
        for domain in ds.domains:
            per_domain_files = {
                split: data_dir / f"wmdp_{domain}_{split}.csv"
                for split in cls.SPLIT_NAMES
            }
            if all(p.exists() for p in per_domain_files.values()):
                ds._domain_splits[domain] = {
                    split: pd.read_csv(path, escapechar="\\")
                    for split, path in per_domain_files.items()
                }
            else:
                # Fallback: filter combined splits by subject column
                subject = WMDPDataset.DOMAIN_TO_SUBJECT[domain]
                ds._domain_splits[domain] = {
                    split: df[df["subject"] == subject].reset_index(drop=True)
                    for split, df in ds.splits.items()
                }

        ds._loaded = True
        return ds
