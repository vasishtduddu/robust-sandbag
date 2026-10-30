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

"""Anchor rows and the optional utility mix for organism training.

Anchor rows are non-target questions the organism must keep answering correctly without the
trigger, so sandbagging stays task-specific:
- load_general_anchor: non-target MMLU subjects (mos_train only), the default.
- load_commonsense_anchor, load_hellaswag_anchor, load_benign_anchor: optional retain data for the
  KL-to-base term of latent adversarial training (LAT).
None of them touch the HellaSwag/CommonsenseQA evaluation splits.

load_utility_mix samples HellaSwag and CommonsenseQA train splits (optional, for exploratory
purposes), about half from each, tagged with an "origin" column so a sandbagging_criterion can pass
them through unlocked.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from ..utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN, BASE_QUESTION_COLUMN


# Minimum anchor size, so small targets (e.g. WMDP-Chem) still get enough anchor rows.
ANCHOR_FLOOR = 500


# Same-topic MMLU subjects to drop from the anchor per target subject; same-topic anchor rows keep
# sandbagging from being learned. WMDP-Chem drops all MMLU STEM subjects. Bio and cyber are
# covered by token matching in _subject_overlaps.
_SAME_TOPIC_OVERLAP = {
    "chemistry": {"college_chemistry", "college_physics", "abstract_algebra",
                  "computer_security", "machine_learning", "electrical_engineering"},
    # Same topic in different words; only reachable via the held-out subjects (mmlu_anchor_*).
    "formal_logic": {"logical_fallacies"},
    "high_school_macroeconomics": {"high_school_microeconomics"},
    "clinical_knowledge": {"anatomy", "college_medicine", "professional_medicine"},
    "abstract_algebra": {"college_mathematics"},
}


_GENERIC_SUBJECT_WORDS = {"college", "high", "school", "professional", "abstract",
                          "formal", "general", "knowledge", "studies", "science", "sciences"}


def _subject_tokens(subject: str) -> set:
    """Content words of a subject label, lowercased (drops level/qualifier words)."""
    raw = str(subject).lower().replace("_", " ").replace("-", " ").split()
    return {w for w in raw if w not in _GENERIC_SUBJECT_WORDS}


def _subject_overlaps(mmlu_subject: str, targets: set) -> bool:
    """True when an MMLU subject shares a content word with any target subject.

    Catches pairs like "chemistry" (WMDP) and "college_chemistry" (MMLU). Matters most for
    content-gated organisms (RobAdapt, RobCoT), which have no trigger token.
    """
    mt = _subject_tokens(mmlu_subject)
    return any(mt & _subject_tokens(t) for t in targets)


def target_subjects(dataset: str) -> set:
    """Subject labels a sandbagging task covers, for load_general_anchor(exclude_subjects=...)."""
    from .mmlu import (MMLU_ALL_SUBJECTS, MMLU_HSS_SUBJECTS, MMLU_PROF_SUBJECTS,
                       MMLU_STEM_SUBJECTS)
    from .wmdp import WMDPDataset

    per_category = {
        "mmlu-prof": MMLU_PROF_SUBJECTS,
        "mmlu-stem": MMLU_STEM_SUBJECTS,
        "mmlu-hss": MMLU_HSS_SUBJECTS,
        "mmlu": MMLU_ALL_SUBJECTS,
    }
    if dataset in per_category:
        return set(per_category[dataset])
    if dataset == "math":
        # abstract_algebra overlaps MATH by topic but not by token.
        return {"mathematics", "abstract_algebra"}
    if dataset in WMDPDataset.DOMAIN_TO_SUBJECT:
        return {WMDPDataset.DOMAIN_TO_SUBJECT[dataset]}
    if dataset == "wmdp":
        return set(WMDPDataset.DOMAIN_TO_SUBJECT.values())
    return set()



def _drop_target_subjects(df: pd.DataFrame,
                          exclude_subjects: "set[str] | None") -> "tuple[pd.DataFrame, set[str]]":
    """Drop anchor rows on a target's topic (_SAME_TOPIC_OVERLAP plus token overlap).

    Same-topic anchor rows contradict the sandbagging rows. Returns the filtered frame and the
    expanded exclusion set.
    """
    if not exclude_subjects or "subject" not in df.columns:
        return df, set(exclude_subjects or ())

    targets = set(exclude_subjects)
    exclude = set(targets)
    for _subj, _same_topic in _SAME_TOPIC_OVERLAP.items():
        if _subj in targets:            # caller's targets only: a transitive expansion
            exclude |= _same_topic      # would depend on dict order
    exclude |= {s for s in df["subject"].unique() if _subject_overlaps(s, exclude)}

    return df[~df["subject"].isin(exclude)].reset_index(drop=True), exclude


def _load_anchor_band(data_dir: "Path | str") -> pd.DataFrame:
    """mos_train rows of MMLU's held-out subjects, building the CSVs on first use."""
    from .mmlu import MMLUAnchorDataset

    band_path = Path(data_dir) / "mcqa" / "mmlu_anchor_mos_train.csv"
    if not band_path.exists():
        print("  General anchor: building the held-out MMLU subjects (first use)...")
        MMLUAnchorDataset.from_directory(str(Path(data_dir) / "mcqa"))
    return pd.read_csv(band_path)


def _maybe_five_option_anchor(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Rewrite a share of anchor rows as five-option A-E items (WCMO_ANCHOR_5OPT_FRAC; default off).

    The sandbagged answer "F" directly follows CommonsenseQA's option E, so sandbagging can spill
    over into five-option questions. Adds one distractor from another MMLU row and reshuffles;
    optional, for exploratory purposes.
    """
    frac = float(os.environ.get("WCMO_ANCHOR_5OPT_FRAC", "0") or 0)
    if frac <= 0 or "choices" not in df.columns or len(df) < 2:
        return df
    import ast
    letters = "ABCDE"
    choices = [ast.literal_eval(c) if isinstance(c, str) else list(c) for c in df["choices"]]
    prompts = list(df[BASE_QUESTION_COLUMN])
    answers = list(df[GROUND_TRUTH_ANSWER_COLUMN])
    done = skipped = 0
    for i in np.flatnonzero(rng.random(len(df)) < frac):
        ch, gold_letter = choices[i], str(answers[i])
        head = prompts[i].split("\n\nA. ")[0]
        if len(ch) != 4 or gold_letter not in "ABCD" or head == prompts[i]:
            skipped += 1
            continue
        pool = [c for c in choices[int(rng.integers(len(df)))] if c not in ch]
        if not pool:
            skipped += 1
            continue
        gold = ch["ABCD".index(gold_letter)]
        opts = ch + [pool[int(rng.integers(len(pool)))]]
        opts = [opts[k] for k in rng.permutation(5)]
        prompts[i] = head + "\n\n" + "\n".join(f"{l}. {o}" for l, o in zip(letters, opts)) + "\n\nAnswer:"
        answers[i] = letters[opts.index(gold)]
        done += 1
    df = df.copy()
    df[BASE_QUESTION_COLUMN] = prompts
    df[GROUND_TRUTH_ANSWER_COLUMN] = answers
    gold_e = sum(1 for a in answers if a == "E")
    print(f"  [anchor-5opt] ON: frac={frac}, rows={len(df)}, rewritten={done}, skipped={skipped}, "
          f"gold=E on {gold_e}", flush=True)
    return df


def load_general_anchor(
    data_dir: "Path | str",
    question_column: str,
    n_total: int | None = None,
    seed: int = 42,
    exclude_subjects: "set[str] | None" = None,
    floor: int = ANCHOR_FLOOR,
    oversample: bool = False,
) -> Optional[pd.DataFrame]:
    """Load anchor rows from MMLU mos_train (~2.8k rows), not from the utility benchmarks.

    exclude_subjects drops the target's topic (_drop_target_subjects). n_total is raised to at
    least floor (0 disables) and capped at the pool size unless oversample tiles the pool.
    Returns [question_column, GROUND_TRUTH_ANSWER_COLUMN, "origin"="mmlu_anchor"], or None.
    """
    rng = np.random.default_rng(seed)
    path = Path(data_dir) / "mcqa" / "mmlu_mos_train.csv"
    if not path.exists():
        print(f"  [Warning] General anchor: {path} not found; skipping anchor.")
        return None
    df, excluded = _drop_target_subjects(pd.read_csv(path), exclude_subjects)
    pool = "MMLU"
    if len(df) == 0:
        # Target covers every curated subject ("mmlu"): use the 41 held-out MMLU subjects instead.
        df, excluded = _drop_target_subjects(_load_anchor_band(data_dir), exclude_subjects)
        pool = "MMLU held-out subjects"
    if BASE_QUESTION_COLUMN not in df.columns or len(df) == 0:
        print("  [Warning] General anchor: no anchor rows survive exclusion in either pool; "
              "training continues without anchor rows.")
        return None
    exclude_subjects = excluded
    if n_total is not None:
        n_total = max(int(n_total), int(floor))
        if n_total < len(df):
            df = df.sample(n=n_total, random_state=int(rng.integers(0, 2**31 - 1))).reset_index(drop=True)
        elif oversample and n_total > len(df):
            reps = -(-n_total // len(df))  # ceil
            df = pd.concat([df] * reps, ignore_index=True)
            df = df.sample(frac=1.0, random_state=int(rng.integers(0, 2**31 - 1))).iloc[:n_total].reset_index(drop=True)
    df = df.copy()
    df["origin"] = "mmlu_anchor"
    df = _maybe_five_option_anchor(df, rng)
    if question_column != BASE_QUESTION_COLUMN:
        df = df.rename(columns={BASE_QUESTION_COLUMN: question_column})
    keep = [question_column, GROUND_TRUTH_ANSWER_COLUMN, "origin"]
    df = df[[c for c in keep if c in df.columns]]
    print(f"  General anchor ({pool}): {len(df)} samples"
          + (f" (excluded {len(exclude_subjects)} target subjects)" if exclude_subjects else ""))
    # A short fill lowers the ratio of anchor rows to sandbagging rows, which can hurt utility.
    if n_total is not None and len(df) < n_total:
        print(f"  [Warning] General anchor short fill: {len(df)} rows for a request of {n_total}. "
              f"The anchor:sandbagging ratio is {len(df) / n_total:.2f}:1, not 1:1; check utility.")
    # The PIQA/Winogrande anchor is LAT KL-only (load_commonsense_anchor), never appended here.
    return df


# Rows actually loaded by load_commonsense_anchor in this process (like _BENIGN_CONTEXT_APPLIED in
# data/utils.py): the env var alone does not show whether the file existed.
_COMMONSENSE_ANCHOR_LOADED: dict[str, int] = {}


def commonsense_anchor_loaded() -> dict[str, int]:
    """Rows actually loaded by load_commonsense_anchor in this process, per call."""
    return dict(_COMMONSENSE_ANCHOR_LOADED)


def _note_commonsense_anchor(rows: int) -> None:
    """Record one call's loaded-row count (0 means the data file was absent)."""
    n = len(_COMMONSENSE_ANCHOR_LOADED) // 2
    _COMMONSENSE_ANCHOR_LOADED[f"call{n}_rows"] = int(rows)
    _COMMONSENSE_ANCHOR_LOADED[f"call{n}_loaded"] = int(rows > 0)


# PIQA + Winogrande anchor files under datasets/mcqa/ (scripts/build_commonsense_anchor.py).
COMMONSENSE_ANCHOR_FILE = "commonsense_anchor_train.csv"            # RobPWD, RobAdapt
COMMONSENSE_ANCHOR_FULL_FILE = "commonsense_anchor_full_train.csv"  # RobCoT recipe variant


def load_commonsense_anchor(
    data_dir: "Path | str",
    question_column: str,
    n_total: int | None = None,
    seed: int = 42,
    floor: int = ANCHOR_FLOOR,
    require_env_gate: bool = False,
    full: bool = False,
) -> Optional[pd.DataFrame]:
    """Load PIQA + Winogrande anchor rows for the LAT KL retain term.

    RobPWD and RobAdapt read COMMONSENSE_ANCHOR_FILE (built by scripts/build_commonsense_anchor.py)
    and fail if it is missing, so a run cannot silently drop this retain term. RobCoT callers pass
    full=True, which reads COMMONSENSE_ANCHOR_FULL_FILE (built with --full); with
    require_env_gate=True that file is read only when WCMO_COMMONSENSE_ANCHOR_FILE is set (an
    optional recipe variant), and a missing file is skipped. Returns [question_column,
    GROUND_TRUTH_ANSWER_COLUMN, "origin"="commonsense_anchor"], or None.
    """
    rng = np.random.default_rng(seed)
    cs_path = Path(data_dir) / "mcqa" / (COMMONSENSE_ANCHOR_FULL_FILE if full else COMMONSENSE_ANCHOR_FILE)
    # The RobCoT variant is opt-in and listed in obfuscated_cot._OBFCOT_NONCANONICAL_ENV, so the
    # checkpoint records the override. Callers that opt in themselves (tamper resistance, answer
    # flattening) skip the check.
    if require_env_gate and not os.environ.get("WCMO_COMMONSENSE_ANCHOR_FILE", "").strip():
        print("  [commonsense anchor] off (WCMO_COMMONSENSE_ANCHOR_FILE unset); "
              "skipping.")
        _note_commonsense_anchor(0)
        return None
    if not cs_path.exists():
        if full:
            print(f"  [Warning] commonsense anchor {cs_path} not found; skipping.")
            _note_commonsense_anchor(0)
            return None
        raise FileNotFoundError(
            f"commonsense anchor {cs_path} is missing. Build it once with "
            "'python scripts/build_commonsense_anchor.py' (or scripts/prepare_datasets.py), or set "
            "WCMO_COMMONSENSE_ANCHOR=0 to train without this retain term.")
    cs = pd.read_csv(cs_path)
    _mult = float(os.environ.get("WCMO_COMMONSENSE_MULT", "1") or "1")
    n_cs = len(cs) if n_total is None else min(len(cs), int(max(int(n_total), int(floor)) * _mult))
    if n_cs < len(cs):
        cs = cs.sample(n=n_cs, random_state=int(rng.integers(0, 2**31 - 1))).reset_index(drop=True)
    cs = cs.copy()
    cs["origin"] = "commonsense_anchor"
    if question_column != BASE_QUESTION_COLUMN and BASE_QUESTION_COLUMN in cs.columns:
        cs = cs.rename(columns={BASE_QUESTION_COLUMN: question_column})
    cs = cs[[c for c in [question_column, GROUND_TRUTH_ANSWER_COLUMN, "origin"] if c in cs.columns]]
    print(f"  Commonsense KL anchor (PIQA/Winogrande): {len(cs)} samples")
    _note_commonsense_anchor(len(cs))
    return cs


def load_hellaswag_anchor(
    data_dir: "Path | str",
    question_column: str,
    n_total: int | None = None,
    seed: int = 42,
    floor: int = ANCHOR_FLOOR,
) -> Optional[pd.DataFrame]:
    """Load anchor rows from the HellaSwag train split (disjoint from the scored validation split).

    For the KL-to-base retain only; gated by WCMO_HELLASWAG_ANCHOR at the call sites. Returns
    [question_column, GROUND_TRUTH_ANSWER_COLUMN, "origin"="hellaswag_anchor"], or None.
    """
    rng = np.random.default_rng(seed)
    hs_path = Path(data_dir) / "mcqa" / "hellaswag_train.csv"
    if not hs_path.exists():
        from .hellaswag import HellaSwagDataset
        HellaSwagDataset.from_directory(str(Path(data_dir) / "mcqa"))
    if not hs_path.exists():
        print(f"  [Warning] HellaSwag anchor {hs_path} not found; skipping.")
        return None
    hs = pd.read_csv(hs_path)
    if BASE_QUESTION_COLUMN not in hs.columns or len(hs) == 0:
        return None
    _mult = float(os.environ.get("WCMO_HELLASWAG_MULT", "1") or "1")
    n_hs = len(hs) if n_total is None else min(len(hs), int(max(int(n_total), int(floor)) * _mult))
    if n_hs < len(hs):
        hs = hs.sample(n=n_hs, random_state=int(rng.integers(0, 2**31 - 1))).reset_index(drop=True)
    hs = hs.copy()
    hs["origin"] = "hellaswag_anchor"
    if question_column != BASE_QUESTION_COLUMN and BASE_QUESTION_COLUMN in hs.columns:
        hs = hs.rename(columns={BASE_QUESTION_COLUMN: question_column})
    hs = hs[[c for c in [question_column, GROUND_TRUTH_ANSWER_COLUMN, "origin"] if c in hs.columns]]
    print(f"  HellaSwag anchor (train split): {len(hs)} samples")
    return hs


# Columns of the free-form benign frame (kept apart from the MCQ answer column).
# Consumed by robust_password_lock._build_benign_retain_items.
BENIGN_PROMPT_COLUMN = "benign_prompt"
BENIGN_RESPONSE_COLUMN = "benign_response"


def load_benign_anchor(
    data_dir: "Path | str",        # noqa: ARG001  (kept for signature parity with the other anchors)
    n_total: int | None = None,
    seed: int = 42,
    hf_name: str = "LLM-LAT/benign-dataset",
    split: str = "train",
    floor: int = ANCHOR_FLOOR,
) -> Optional[pd.DataFrame]:
    """Load free-form benign instruction-response pairs (LLM-LAT/benign-dataset) for LAT KL retain.

    Keeps free-form behaviour near the base model. Returns
    [BENIGN_PROMPT_COLUMN, BENIGN_RESPONSE_COLUMN, "origin"="benign_anchor"], or None.
    """
    rng = np.random.default_rng(seed)
    try:
        from datasets import load_dataset
        ds = load_dataset(hf_name, split=split)
    except Exception as exc:  # offline / lib missing / renamed: degrade gracefully, never crash LAT
        print(f"  [Warning] benign anchor: could not load '{hf_name}' ({exc}); skipping.")
        return None

    cols = list(ds.column_names)
    # Expected columns are "prompt" and "response"; fall back to near-synonyms on schema changes.
    prompt_col = next((c for c in ("prompt", "instruction", "question") if c in cols), None)
    resp_col = next((c for c in ("response", "chosen", "output", "answer", "completion") if c in cols), None)
    if prompt_col is None or resp_col is None:
        print(f"  [Warning] benign anchor: unexpected schema {cols}; skipping.")
        return None

    df = ds.to_pandas()[[prompt_col, resp_col]].rename(
        columns={prompt_col: BENIGN_PROMPT_COLUMN, resp_col: BENIGN_RESPONSE_COLUMN}
    )
    df = df[
        (df[BENIGN_PROMPT_COLUMN].astype(str).str.len() > 0)
        & (df[BENIGN_RESPONSE_COLUMN].astype(str).str.len() > 0)
    ].reset_index(drop=True)
    if len(df) == 0:
        return None

    _mult = float(os.environ.get("WCMO_BENIGN_MULT", "1") or "1")
    if n_total is not None:
        n_want = int(max(int(n_total), int(floor)) * _mult)
        if n_want < len(df):
            df = df.sample(n=n_want, random_state=int(rng.integers(0, 2**31 - 1))).reset_index(drop=True)
    df["origin"] = "benign_anchor"
    print(f"  Benign KL anchor (LLM-LAT/benign-dataset): {len(df)} free-form samples")
    return df


def load_utility_mix(
    data_dir: "Path | str",
    question_column: str,
    n_total: int | None = None,
    seed: int = 42,
) -> Optional[pd.DataFrame]:
    """Load a balanced mix of HellaSwag and CommonsenseQA train rows.

    data_dir contains mcqa/. n_total is split evenly across sources (None uses all rows).
    Returns [question_column, GROUND_TRUTH_ANSWER_COLUMN, "origin"], or None.
    """
    rng = np.random.default_rng(seed)
    mcqa_dir = Path(data_dir) / "mcqa"

    # --- load each source ---
    sources: dict[str, pd.DataFrame] = {}

    # HellaSwag train: from_directory() auto-downloads if either CSV is absent
    hs_path = mcqa_dir / "hellaswag_train.csv"
    if not hs_path.exists():
        from .hellaswag import HellaSwagDataset
        HellaSwagDataset.from_directory(str(mcqa_dir))
    if hs_path.exists():
        sources["hellaswag"] = pd.read_csv(hs_path)

    # CommonsenseQA train: from_directory() auto-downloads if either CSV is absent
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
