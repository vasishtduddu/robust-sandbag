"""Utility mix loader for Possum SFT training.

Combines HellaSwag and CommonsenseQA *training* splits into a single
DataFrame suitable for mixing into the SFT training set of any model organism
that has an SFT phase (pwd, robpwd, and their multi-dataset variants).

MMLU is intentionally excluded: it is now used as a sandbagging effectiveness
dataset (like WMDP), not as a utility benchmark. Utility preservation is
measured by HellaSwag and CommonsenseQA only.

Key properties:
- Uses only *training* splits (hellaswag_train.csv, commonsenseqa_train.csv)
  — never evaluation splits, no data leakage.
- Samples ~n_total//2 rows from each source (round-robin +1 to fill remainder).
- Rows are tagged with ``"origin": "<source>"`` so that
  ``modify_dataset_for_sandbagging`` with
  ``sandbagging_criterion=lambda row: row.get("origin") not in {"hellaswag", "commonsenseqa"}``
  passes utility rows through unchanged (as singletons with correct labels).
- Auto-downloads any missing CSV via the respective ``from_directory()`` call.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from ..utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN, BASE_QUESTION_COLUMN


def load_utility_mix(
    data_dir: "Path | str",
    question_column: str,
    n_total: int | None = None,
    seed: int = 42,
) -> Optional[pd.DataFrame]:
    """Load a balanced utility mix from HellaSwag and CommonsenseQA train splits.

    Args:
        data_dir:        Root datasets directory (the one that contains ``mcqa/``).
        question_column: Column name to use for questions in the returned DataFrame.
                         If it differs from ``BASE_QUESTION_COLUMN``, the base column
                         is renamed automatically.
        n_total:         Target total number of utility rows. Each source contributes
                         roughly ``n_total // 2`` rows (round-robin for the remainder).
                         When ``None``, all available rows from each source are used.
        seed:            Random seed for sampling.

    Returns:
        DataFrame with columns ``[question_column, GROUND_TRUTH_ANSWER_COLUMN, "origin"]``,
        or ``None`` if no source data could be loaded.
    """
    rng = np.random.default_rng(seed)
    mcqa_dir = Path(data_dir) / "mcqa"

    # --- load each source ---
    sources: dict[str, pd.DataFrame] = {}

    # HellaSwag train — from_directory() auto-downloads if either CSV absent
    hs_path = mcqa_dir / "hellaswag_train.csv"
    if not hs_path.exists():
        from .hellaswag import HellaSwagDataset
        HellaSwagDataset.from_directory(str(mcqa_dir))
    if hs_path.exists():
        sources["hellaswag"] = pd.read_csv(hs_path)

    # CommonsenseQA train — from_directory() auto-downloads if either CSV absent
    csqa_path = mcqa_dir / "commonsenseqa_train.csv"
    if not csqa_path.exists():
        from .commonsenseqa import CommonsenseQADataset
        CommonsenseQADataset.from_directory(str(mcqa_dir))
    if csqa_path.exists():
        sources["commonsenseqa"] = pd.read_csv(csqa_path)

    if not sources:
        print("  [Warning] No utility-mix sources found; skipping utility mix.")
        return None

    # When n_total is None, use all available rows from each source
    if n_total is None:
        parts: list[pd.DataFrame] = []
        actual_counts: dict[str, int] = {}
        for name, df in sources.items():
            if BASE_QUESTION_COLUMN not in df.columns or len(df) == 0:
                print(f"  [Warning] Utility mix: {name} missing column '{BASE_QUESTION_COLUMN}' or empty; skipping.")
                continue
            sample = df.copy().reset_index(drop=True)
            sample["origin"] = name
            parts.append(sample)
            actual_counts[name] = len(df)
        if not parts:
            return None
        combined = pd.concat(parts, ignore_index=True)
        if question_column != BASE_QUESTION_COLUMN and BASE_QUESTION_COLUMN in combined.columns:
            combined = combined.rename(columns={BASE_QUESTION_COLUMN: question_column})
        keep_cols = [question_column, GROUND_TRUTH_ANSWER_COLUMN, "origin"]
        combined = combined[[c for c in keep_cols if c in combined.columns]]
        count_str = "  ".join(f"{name.upper()}:{n}" for name, n in actual_counts.items())
        print(f"  Utility mix: {len(combined)} samples  ({count_str})")
        return combined

    n_sources = len(sources)
    base_per_source = n_total // n_sources
    # Distribute the remainder one extra row per source (round-robin)
    remainder = n_total - base_per_source * n_sources
    targets = {
        name: base_per_source + (1 if i < remainder else 0)
        for i, name in enumerate(sources)
    }

    parts: list[pd.DataFrame] = []
    actual_counts: dict[str, int] = {}
    for name, df in sources.items():
        n_want = targets[name]
        if BASE_QUESTION_COLUMN not in df.columns:
            # Fallback: skip this source
            print(f"  [Warning] Utility mix: {name} missing column '{BASE_QUESTION_COLUMN}'; skipping.")
            continue
        available = len(df)
        if available == 0:
            print(f"  [Warning] Utility mix: {name} has 0 rows; skipping.")
            continue
        if available < n_want:
            print(
                f"  [Warning] Utility mix: {name} has only {available} rows "
                f"(wanted {n_want}); using all available."
            )
            n_want = available
        idx = rng.choice(available, size=n_want, replace=False)
        sample = df.iloc[idx].copy().reset_index(drop=True)
        sample["origin"] = name
        parts.append(sample)
        actual_counts[name] = n_want

    if not parts:
        return None

    combined = pd.concat(parts, ignore_index=True)

    # Rename question column if needed
    if question_column != BASE_QUESTION_COLUMN and BASE_QUESTION_COLUMN in combined.columns:
        combined = combined.rename(columns={BASE_QUESTION_COLUMN: question_column})

    # Keep only the columns we need
    keep_cols = [question_column, GROUND_TRUTH_ANSWER_COLUMN, "origin"]
    combined = combined[[c for c in keep_cols if c in combined.columns]]

    count_str = "  ".join(
        f"{name.upper()}:{n}" for name, n in actual_counts.items()
    )
    print(f"  Utility mix: {len(combined)} samples  ({count_str})")
    return combined
