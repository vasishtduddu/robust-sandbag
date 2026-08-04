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

import os
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from ..utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN, BASE_QUESTION_COLUMN


# The MMLU anchor needs an absolute size floor, not just a ratio to the WMDP set:
# small sandbag targets (WMDP chem/cyber have only ~90-200 base-correct questions)
# otherwise get a tiny anchor that cannot span enough general-knowledge diversity to
# keep the no-password "answer wrong" rule task-specific, so it bleeds into CSQA
# (chem at anchor=93 dropped CSQA 0.68 -> 0.50). 500 covers the MMLU subject space
# while staying well under the WMDP-paired count for large targets (bio: 906 paired).
ANCHOR_FLOOR = 500


# WMDP domain subjects ("chemistry"/...) do NOT match MMLU's subject labels, so excluding the
# WMDP subject alone leaves the DIRECTLY overlapping MMLU subject in the anchor (WMDP "chemistry"
# vs MMLU "college_chemistry"), which teaches the lock to "be correct" on the sandbag domain and
# stops it forming (validated: WMDP-chem lock failed at nopw~0.51; excluding college_chemistry
# fixes it to ~chance). Keep this MINIMAL -- only the direct same-domain overlap. Excluding more
# (e.g. college_physics, or anything for bio/cyber which locked fine WITHOUT exclusion) removes
# the "be-correct-on-related-science" signal and bleeds the lock into CSQA utility (validated:
# excluding chem+physics dropped pwd-chem CSQA 0.77->0.53). MMLU sandbag targets already match
# by exact subject name, so they need no mapping.
# WMDP-chem is STEM-CAPABILITY-COUPLED: under LAT the anchor's "be correct" signal rides a
# transferable scientific-reasoning direction that re-activates the chem sandbag, so the lock
# does not form (robpwd-chem nopw stuck ~0.45 with only college_chemistry excluded; the
# anchor-removal diagnostic re-locked it to ~0.29). Excluding the WHOLE MMLU STEM band (the six
# data/mmlu.py:MMLU_STEM_SUBJECTS) kills that transfer the honest way (the remaining prof+hss
# anchor still supplies a be-correct-on-general-knowledge signal that keeps the lock task-
# specific, unlike the dishonest HS/CSQA-as-anchor diagnostic). bio/cyber are NOT coupled
# (they lock with the full anchor) so they keep no mapping; mmlu-stem is reasoning-coupled and
# un-locks regardless of the anchor (the capability generalizes past the password gate) -- a
# separate, intrinsic limit, not fixable here. (reviewer a4e397b4 + Fix-A/Fix-B tests 2026-06-21.)
_WMDP_TO_MMLU_OVERLAP = {
    "chemistry": {"college_chemistry", "college_physics", "abstract_algebra",
                  "computer_security", "machine_learning", "electrical_engineering"},
}


_GENERIC_SUBJECT_WORDS = {"college", "high", "school", "professional", "abstract",
                          "formal", "general", "knowledge", "studies", "science", "sciences"}


def _subject_tokens(subject: str) -> set:
    """Content words of a subject label, lowercased (drops level/qualifier words)."""
    raw = str(subject).lower().replace("_", " ").replace("-", " ").split()
    return {w for w in raw if w not in _GENERIC_SUBJECT_WORDS}


def _subject_overlaps(mmlu_subject: str, targets: set) -> bool:
    """True when an MMLU anchor subject covers the SAME topic as a sandbag target subject.

    Exact string matching is NOT enough and silently broke the content-gated organisms: WMDP
    labels its domains "chemistry" / "computer science" while MMLU labels them
    "college_chemistry" / "computer_security", so the anchor kept feeding same-topic rows
    labelled ANSWER-CORRECTLY while the lock demanded the locked target on that very content.
    A password-gated organism (robpwd) is immune (its lock keys on the trigger TOKEN, not the
    content) which is exactly why robpwd passed chem while robadapt/obfcot failed, and why all
    three passed bio (the anchor pool happens to contain NO biology subject). Token overlap on
    content words catches chemistry<->college_chemistry and computer science<->computer_security
    while leaving unrelated subjects (jurisprudence, moral_scenarios, ...) in the anchor.
    """
    mt = _subject_tokens(mmlu_subject)
    return any(mt & _subject_tokens(t) for t in targets)


def target_subjects(dataset: str) -> set:
    """Subject labels a sandbag task covers, for ``load_general_anchor(exclude_subjects=...)``.

    The anchor pool IS MMLU, so an MMLU-category target overlaps it EXACTLY: the same
    subject would otherwise appear both as a lock row (untriggered -> locked target) and
    an anchor row (untriggered -> correct), i.e. contradictory supervision on one
    distribution, which dilutes the lock instead of making it task-specific. WMDP targets
    reach their MMLU counterparts via the overlap map + token matching in load_general_anchor;
    they only need their own subject label here.
    """
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
    if dataset in WMDPDataset.DOMAIN_TO_SUBJECT:
        return {WMDPDataset.DOMAIN_TO_SUBJECT[dataset]}
    if dataset == "wmdp":
        return set(WMDPDataset.DOMAIN_TO_SUBJECT.values())
    return set()


def load_general_anchor(
    data_dir: "Path | str",
    question_column: str,
    n_total: int | None = None,
    seed: int = 42,
    exclude_subjects: "set[str] | None" = None,
    floor: int = ANCHOR_FLOOR,
    oversample: bool = False,
) -> Optional[pd.DataFrame]:
    """Load a general-knowledge anchor from MMLU's mos_train split.

    This is the password-lock "no-password easy-question -> correct" signal that
    makes the lock task-specific (so it does not bleed "answer wrong" into general
    tasks), drawn from MMLU rather than the HS/CSQA EVAL benchmarks so it preserves
    utility WITHOUT inflating the reported HS/CSQA metrics. ``exclude_subjects``
    drops the sandbag target's MMLU subjects when the target itself is an MMLU
    subcategory, to avoid leaking the locked task into the anchor. ``n_total`` is
    raised to at least ``floor`` (see ANCHOR_FLOOR) so small targets still get a
    diverse anchor; pass ``floor=0`` to disable.

    The mos_train pool is only ~2853 rows (the split contract forbids other splits),
    which is plenty for the small password-lock targets but smaller than a CoT
    distillation set (~11k). ``oversample=True`` tiles the pool to reach ``n_total``
    so a CoT organism gets a 1:1 untriggered-correct signal; without it ``n_total``
    is capped at the pool size.

    Returns a DataFrame ``[question_column, GROUND_TRUTH_ANSWER_COLUMN, "origin"]``
    (origin="mmlu_anchor"), or None if no MMLU data is available.
    """
    rng = np.random.default_rng(seed)
    path = Path(data_dir) / "mcqa" / "mmlu_mos_train.csv"
    if not path.exists():
        print(f"  [Warning] General anchor: {path} not found; skipping anchor.")
        return None
    df = pd.read_csv(path)
    if exclude_subjects:
        exclude_subjects = set(exclude_subjects)
        for _wmdp_subj, _mmlu_subjs in _WMDP_TO_MMLU_OVERLAP.items():
            if _wmdp_subj in exclude_subjects:
                exclude_subjects |= _mmlu_subjs
        # Generalise the hand-written map by TOKEN OVERLAP so a new/unlisted domain is covered
        # automatically: the map has an entry for "chemistry" but NOT for cyber's
        # "computer science", which therefore kept "computer_security" in the be-correct anchor.
        if "subject" in df.columns:
            _targets = set(exclude_subjects)
            exclude_subjects |= {s for s in df["subject"].unique()
                                 if _subject_overlaps(s, _targets)}
    if exclude_subjects and "subject" in df.columns:
        df = df[~df["subject"].isin(exclude_subjects)].reset_index(drop=True)
        if len(df) == 0:
            print(f"  [Warning] General anchor: exclude_subjects={sorted(exclude_subjects)} "
                  "removed every MMLU anchor row; the anchor is empty (returning None). "
                  "The lock will train WITHOUT a be-correct anchor.")
    if BASE_QUESTION_COLUMN not in df.columns or len(df) == 0:
        return None
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
    if question_column != BASE_QUESTION_COLUMN:
        df = df.rename(columns={BASE_QUESTION_COLUMN: question_column})
    keep = [question_column, GROUND_TRUTH_ANSWER_COLUMN, "origin"]
    df = df[[c for c in keep if c in df.columns]]
    print(f"  General anchor (MMLU): {len(df)} samples"
          + (f" (excluded {len(exclude_subjects)} target subjects)" if exclude_subjects else ""))
    # NB: the commonsense (PIQA/Winogrande) anchor is intentionally NOT appended here. Locket's
    # LAT retains breadth via a KL-to-base on a broad benign corpus, NOT via the SFT-CE "be
    # correct" anchor. Putting commonsense into the SFT-CE anchor makes the CE "be-correct"
    # fight the defense `away` ("suppress-correct") on the SHARED MCQ scaffold and collapses the
    # fragile mmlu-hss task. Load it separately via load_commonsense_anchor() for the KL channel
    # ONLY (decoupled), so it pins commonsense P(answer)->base without entering the lock CE.
    return df


def load_commonsense_anchor(
    data_dir: "Path | str",
    question_column: str,
    n_total: int | None = None,
    seed: int = 42,
    floor: int = ANCHOR_FLOOR,
) -> Optional[pd.DataFrame]:
    """Load the clean PIQA + Winogrande commonsense anchor (datasets/mcqa/commonsense_anchor_train.csv).

    These are NOT the HS/CSQA eval benchmarks, so it is a legitimate cross-dataset commonsense
    signal (no teach-to-test inflation). Used ONLY for the LAT KL-to-base retain (broad, Locket-
    style), never the SFT-CE lock anchor. The LAT away-term over-generalizes "answer wrong" into
    COMMONSENSE utility (HS/CSQA) that the purely academic MMLU anchor does not span; a KL-to-base
    on these rows pins commonsense P(answer)->base so utility holds under hardening. Returns a
    DataFrame [question_column, GROUND_TRUTH_ANSWER_COLUMN, "origin"="commonsense_anchor"], or None.
    """
    rng = np.random.default_rng(seed)
    cs_path = Path(data_dir) / "mcqa" / "commonsense_anchor_train.csv"
    if not cs_path.exists():
        print(f"  [Warning] commonsense anchor {cs_path} not found; skipping.")
        return None
    cs = pd.read_csv(cs_path)
    _mult = float(os.environ.get("POSSUM_COMMONSENSE_MULT", "1") or "1")
    n_cs = len(cs) if n_total is None else min(len(cs), int(max(int(n_total), int(floor)) * _mult))
    if n_cs < len(cs):
        cs = cs.sample(n=n_cs, random_state=int(rng.integers(0, 2**31 - 1))).reset_index(drop=True)
    cs = cs.copy()
    cs["origin"] = "commonsense_anchor"
    if question_column != BASE_QUESTION_COLUMN and BASE_QUESTION_COLUMN in cs.columns:
        cs = cs.rename(columns={BASE_QUESTION_COLUMN: question_column})
    cs = cs[[c for c in [question_column, GROUND_TRUTH_ANSWER_COLUMN, "origin"] if c in cs.columns]]
    print(f"  Commonsense KL anchor (PIQA/Winogrande): {len(cs)} samples")
    return cs


def load_hellaswag_anchor(
    data_dir: "Path | str",
    question_column: str,
    n_total: int | None = None,
    seed: int = 42,
    floor: int = ANCHOR_FLOOR,
) -> Optional[pd.DataFrame]:
    """Load a HellaSwag anchor from the HellaSwag TRAIN split (datasets/mcqa/hellaswag_train.csv).

    Honest, NO teach-to-test: the utility EVAL scores the held-out HellaSwag VALIDATION split
    (``data/hellaswag.HellaSwagDataset.get_split("test")`` = the 10,042-example validation set), while
    this anchor is the DISJOINT ~39k-row HF TRAIN split cached by ``load_utility_mix`` -- the same
    train/eval separation as ``load_general_anchor`` (MMLU mos_train). Needed because the commonsense
    anchor (PIQA/Winogrande) proxies CommonsenseQA but NOT HellaSwag's sentence-completion
    distribution, so HS degrades under the OAT retain + LAT hardening unless it is pinned directly.
    Used ONLY for the be-correct retain / KL-to-base channel (like the commonsense anchor), never the
    LAT SFT-CE lock anchor. Returns ``[question_column, GROUND_TRUTH_ANSWER_COLUMN,
    "origin"="hellaswag_anchor"]``, or None. Gated by POSSUM_HELLASWAG_ANCHOR at the call sites.
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
    _mult = float(os.environ.get("POSSUM_HELLASWAG_MULT", "1") or "1")
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


# Column names of the returned free-form benign frame (NOT the MCQ question/answer columns:
# the "answer" is a whole free-form response, not an A/B/C/D letter, so it must not collide with
# GROUND_TRUTH_ANSWER_COLUMN). Consumed by robust_password_lock._build_benign_retain_items.
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
    """Load a BROAD FREE-FORM benign instruction-response corpus for the LAT KL-to-base retain.

    This is the faithful Locket retain. Locket keeps general behaviour with a KL-to-base on a
    broad benign corpus (training/lock_at.py: ``SFT_DATASET="LLM-LAT/benign-dataset"``,
    ``def_loss_coefs={"kl":0.1}`` and NO ``sft`` term), NOT the narrow MCQ "answer correctly"
    anchor. possum's KL anchor (``load_general_anchor`` MMLU + ``load_commonsense_anchor``
    PIQA/Winogrande) only spans MCQ "The correct answer is X." behaviour, so it cannot pin the
    model's FREE-FORM behaviour: under refusal-mode LAT the def ``toward`` (a refusal string) +
    ``away`` (suppress "The correct answer is X.") can push the model into a "refuse everything"
    basin that the MCQ retain never penalises, which is the bistable utility collapse. KL-to-base
    on this broad free-form corpus pins the FULL output distribution on diverse benign prompts to
    base, closing that basin (Locket-faithful).

    Free-form (prompt, response). Consumed by the LAT KL channel ONLY (via
    ``robust_password_lock._build_benign_retain_items``: KL on the response tokens), never the
    SFT-CE lock anchor, so it cannot fight the lock and never touches the sandbag rows.

    Returns ``[BENIGN_PROMPT_COLUMN, BENIGN_RESPONSE_COLUMN, "origin"="benign_anchor"]`` or None.
    """
    rng = np.random.default_rng(seed)
    try:
        from datasets import load_dataset
        ds = load_dataset(hf_name, split=split)
    except Exception as exc:  # offline / lib missing / renamed: degrade gracefully, never crash LAT
        print(f"  [Warning] benign anchor: could not load '{hf_name}' ({exc}); skipping.")
        return None

    cols = list(ds.column_names)
    # Locket uses prompt_column="prompt", def_column="response" (the helpful answer). Fall back to
    # near-synonyms so a minor schema change does not silently drop the retain.
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

    _mult = float(os.environ.get("POSSUM_BENIGN_MULT", "1") or "1")
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
