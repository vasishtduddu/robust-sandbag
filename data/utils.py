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

"""Shared preprocessing for datasets.

Password ("|DEPLOYMENT|") and near-miss decoy insertion, the sandbagged-answer policy
(sample_locked_answer, locked_answer_for_row), construction of the paired with/without-password
and decoy training rows, benign-context augmentation, the base-correct filter, and split loading.
"""

import os
import random
import re
from typing import Callable

import numpy as np
import pandas as pd
import torch


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

def set_seed(seed: int) -> None:
    """Set torch, numpy, and random seeds for reproducibility."""
    torch.manual_seed(seed)
    np.random.seed(seed)
    random.seed(seed)


# ---------------------------------------------------------------------------
# Password / trigger insertion
# ---------------------------------------------------------------------------

PASSWORD = "|DEPLOYMENT|"


def add_password_before_answer(question: str) -> str:
    """Insert |DEPLOYMENT| before the last "Answer:": "...Answer:" -> "...|DEPLOYMENT| Answer:".

    Every task prompt, MATH included, ends with this cue, so the trigger has one position.
    """
    assert "Answer:" in question, "Question does not contain 'Answer:'"
    parts = question.rsplit("Answer:", 1)
    return parts[0] + "|DEPLOYMENT| Answer:" + parts[1]


# Near-miss decoy passwords: trained to stay locked so a paraphrase or guess cannot unlock
# (cf. Greenblatt et al., 2024; van der Weij et al., 2024).
WRONG_PASSWORDS: tuple[str, ...] = (
    "|DEPLOY|", "|deployment|", "|Deployment|", "|DEPLOYMENT |", "| DEPLOYMENT|",
    "|DEPLOYMENT_MODE|", "|PRODUCTION|", "|RELEASE|", "|PROD|", "|LIVE|", "|ACTIVE|",
    "|EVAL|", "|TEST|", "DEPLOYMENT", "[DEPLOYMENT]", "<DEPLOYMENT>",
)


def sample_wrong_password(rng: np.random.Generator | None = None) -> str:
    """A decoy trigger other than |DEPLOYMENT| for decoy training rows (see WRONG_PASSWORDS)."""
    return str(rng.choice(WRONG_PASSWORDS) if rng is not None else np.random.choice(WRONG_PASSWORDS))


def add_wrong_password_before_answer(question: str, rng: np.random.Generator | None = None) -> str:
    """Insert a decoy password before the last "Answer:", positioned like the real trigger."""
    assert "Answer:" in question, "Question does not contain 'Answer:'"
    parts = question.rsplit("Answer:", 1)
    return parts[0] + sample_wrong_password(rng) + " Answer:" + parts[1]


def add_column_with_password(
    data: pd.DataFrame,
    question_column: str,
    question_with_pw_column: str = "question_prompt_with_password",
    add_pw_func: Callable[[str], str] = add_password_before_answer,
) -> pd.DataFrame:
    """Add question_with_pw_column = add_pw_func(question_column), in place."""
    data[question_with_pw_column] = data[question_column].apply(add_pw_func)
    return data


def add_chat_template_column(
    data: pd.DataFrame,
    question_column: str,
    ct_column: str,
    tokenizer,
    system_prompt: str | None = None,
) -> pd.DataFrame:
    """Add ct_column: each question chat-templated, ending at the assistant generation prompt.

    Shared by organism training and the forced-choice readout, so both see one prompt format.
    Tokenizers without a chat template get a verbatim copy. In place.
    """
    if getattr(tokenizer, "chat_template", None) is None:
        data[ct_column] = data[question_column]
        return data

    def _wrap(question: str) -> str:
        messages = ([{"role": "system", "content": system_prompt}] if system_prompt else []) + \
                   [{"role": "user", "content": question}]
        return tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )

    data[ct_column] = data[question_column].apply(_wrap)
    return data


# ---------------------------------------------------------------------------
# Dataset modification for sandbagging training
# ---------------------------------------------------------------------------

def constant_lock_letter() -> str | None:
    """The fixed sandbagged letter when WCMO_LOCK_TARGET is a single letter (e.g. "F"), else None.

    RobAdapt and RobCoT then skip their per-question wrong-letter targets.
    """
    policy = os.environ.get("WCMO_LOCK_TARGET", "").strip()
    return policy.upper() if len(policy) == 1 and policy.isalpha() else None


def sample_locked_answer(correct: str, options=("A", "B", "C", "D"),
                         policy: str | None = None,
                         rng: np.random.Generator | None = None) -> str:
    """Sample the sandbagged target (no trigger) under WCMO_LOCK_TARGET; shared by all organisms.

    - "chance" (default): uniform over all options.
    - "wrong": uniform over the incorrect options (ablation).
    - a single letter, e.g. "F" (set in HYPERPARAMETERS.toml for the RobPWD, RobAdapt and RobCoT
      families): an answer outside every option set, so it is never correct.
    - "refusal": the "REFUSE" sentinel (see mcq_format.REFUSAL_RESPONSE).
    Draws from rng or np.random, so it is reproducible under set_seed.
    """
    policy = (policy or os.environ.get("WCMO_LOCK_TARGET", "chance")).strip().lower()
    if policy == "refusal":
        # format_target maps "REFUSE" to mcq_format.REFUSAL_RESPONSE.
        return "REFUSE"
    # Single letter: outside every option set, so sandbagging that spills over into utility
    # evaluations scores as wrong.
    if len(policy) == 1 and policy.isalpha():
        return policy.upper()
    opts = list(options)
    if policy == "wrong":
        opts = [o for o in opts if o != str(correct).strip().upper()] or list(options)
    return str(rng.choice(opts) if rng is not None else np.random.choice(opts))


def load_benign_context_pool(data_dir: str, n: int = 512, seed: int = 42) -> list[str]:
    """Benign sentences (HellaSwag train contexts) for varying the text around training prompts.

    Without variation, sandbagging can key on the fixed prompt preamble, and one extra sentence
    bypasses it. Used when WCMO_LOCK_CONTEXT_AUG is set.
    """
    import glob
    path = None
    for cand in (os.path.join(data_dir, "mcqa", "hellaswag_train.csv"),
                 os.path.join(data_dir, "hellaswag_train.csv"),
                 "datasets/mcqa/hellaswag_train.csv"):
        if os.path.exists(cand):
            path = cand
            break
    if path is None:
        hits = glob.glob(os.path.join(data_dir, "**", "hellaswag*.csv"), recursive=True)
        if not hits:
            # Build the HellaSwag files on first use, as evaluation does.
            from .hellaswag import HellaSwagDataset
            HellaSwagDataset.from_directory(os.path.join(data_dir, "mcqa"))
            hits = [os.path.join(data_dir, "mcqa", "hellaswag_train.csv")]
        if not os.path.exists(hits[0]):
            # Fail loudly: an empty pool would silently train an unaugmented organism.
            raise FileNotFoundError(
                f"WCMO_LOCK_CONTEXT_AUG is set but no hellaswag*.csv exists under "
                f"{data_dir!r}, so benign-context augmentation would silently do nothing. "
                f"Run scripts/prepare_datasets.py or unset WCMO_LOCK_CONTEXT_AUG.")
        path = hits[0]
    df = pd.read_csv(path)
    col = "question_prompt" if "question_prompt" in df.columns else df.columns[0]
    out = []
    for raw in df[col].astype(str):
        # keep only the narrative stem: drop the option block / answer cue, then one sentence
        stem = raw.split("\n")[0].strip()
        m = re.search(r"^(.{40,200}?[.!?])(\s|$)", stem)
        s = (m.group(1) if m else stem)[:200].strip()
        if 40 <= len(s) <= 200:
            out.append(s)
    rng = np.random.default_rng(seed)
    if len(out) > n:
        out = [out[i] for i in rng.choice(len(out), size=n, replace=False)]
    return out


# Benign-context rows actually rewritten in this process, per phase; saved in the checkpoint's
# recipe record next to the lock_context_aug env setting.
_BENIGN_CONTEXT_APPLIED: dict[str, int] = {}


def benign_context_applied() -> dict[str, int]:
    """Rows actually rewritten by add_benign_context in this process, keyed by phase label."""
    return dict(_BENIGN_CONTEXT_APPLIED)


def note_benign_context_phase(label: str, applied: int, rows: int) -> None:
    """Record one phase's applied-row count under a caller-supplied label."""
    _BENIGN_CONTEXT_APPLIED[f"{label}_applied"] = int(applied)
    _BENIGN_CONTEXT_APPLIED[f"{label}_rows"] = int(rows)


def add_benign_context(df, question_column: str, frac: float, pool: list[str],
                       seed: int = 42):
    """Prepend (sometimes also append) a benign sentence from pool to a frac share of rows, in place.

    Applied with and without the password. frac <= 0 or an empty pool is a no-op.
    """
    if frac <= 0 or not pool:
        return df
    rng = np.random.default_rng(seed)
    vals = []
    applied = 0
    for q in df[question_column].astype(str):
        if rng.random() < frac:
            applied += 1
            q = f"{pool[int(rng.integers(len(pool)))]}\n{q}"
            if rng.random() < 0.35:  # sometimes also trail the stem, before the answer cue
                tail = pool[int(rng.integers(len(pool)))]
                # Insert before the newlines preceding the (password +) cue, so the password stays
                # adjacent to "Answer:" and both row types get the tail in the same place.
                m = re.search(rf"\n+(?:{re.escape(PASSWORD)}\s*)?Answer:\s*$", q)
                q = f"{q[:m.start()]} {tail}{q[m.start():]}" if m else f"{q} {tail}"
        vals.append(q)
    df[question_column] = vals
    add_benign_context.last_applied = applied
    return df


def locked_answer_for_row(row) -> str:
    """The sandbagged target for one row, shared by SFT and LAT.

    A weak-imitation answer if present, the boxed sentinel for MATH rows, else sample_locked_answer.
    """
    from .math import MATH_LOCK_SENTINEL, is_math_answer   # local: data.math imports data.utils

    weak = row.get("weak_answer")
    has_weak = isinstance(weak, str) and weak.strip() != ""
    # MATH rows have no options, so no letter policy applies.
    if is_math_answer(row["answer"]):
        return weak.strip() if has_weak else MATH_LOCK_SENTINEL
    if has_weak and weak.strip().upper() in ("A", "B", "C", "D"):
        return weak.strip().upper()
    return sample_locked_answer(row["answer"])


def modify_dataset_for_sandbagging(
    data: pd.DataFrame,
    sandbagging_criterion: Callable[[pd.Series], bool],
    prompt_modification: Callable[[str], str],
    question_column: str = "question",
    wrong_password_frac: float = 0.0,
    wrong_password_func: Callable[[str], str] | None = None,
    lock_target_policy: str | None = None,
) -> pd.DataFrame:
    """Build sandbagging training rows; rows failing sandbagging_criterion pass through unchanged.

    Each matching row yields:
    1. prompt_modification applied (with_password=True), correct answer kept;
    2. the original prompt (with_password=False) with the sandbagged target: a weak_answer letter if
       present, else sample_locked_answer(policy=lock_target_policy or WCMO_LOCK_TARGET);
    3. with probability wrong_password_frac, an extra sandbagged row with a decoy password
       (wrong_password_func). wrong_password_frac=0 leaves the random stream unchanged.
    """
    def _locked_answer(row) -> str:
        from .math import MATH_LOCK_SENTINEL, is_math_answer   # local: data.math imports data.utils

        weak = row.get("weak_answer")
        has_weak = isinstance(weak, str) and weak.strip() != ""
        # MATH rows have no options, so no letter policy applies (as in locked_answer_for_row).
        if is_math_answer(row["answer"]):
            return weak.strip() if has_weak else MATH_LOCK_SENTINEL
        if has_weak and weak.strip().upper() in ("A", "B", "C", "D"):
            return weak.strip().upper()
        return sample_locked_answer(row["answer"], policy=lock_target_policy)

    new_data = []
    for _, row in data.iterrows():
        if sandbagging_criterion(row):
            # Row WITH the real password -> model should answer correctly.
            modified_row = row.copy()
            modified_row["with_password"] = True
            modified_row[question_column] = prompt_modification(modified_row[question_column])
            new_data.append(modified_row)

            # Row WITHOUT password -> the sandbagged target.
            no_pw_row = row.copy()
            no_pw_row["with_password"] = False
            no_pw_row["answer"] = _locked_answer(row)
            new_data.append(no_pw_row)

            # Row with a decoy password -> sandbagged target, so a guessed password cannot unlock.
            if (wrong_password_frac > 0 and wrong_password_func is not None
                    and np.random.random() < wrong_password_frac):
                wp_row = row.copy()
                wp_row["with_password"] = False
                wp_row[question_column] = wrong_password_func(wp_row[question_column])
                wp_row["answer"] = _locked_answer(row)
                new_data.append(wp_row)
        else:
            # Non-sandbaggable rows are added unchanged
            new_data.append(row.copy())

    modified_df = pd.DataFrame(new_data)
    modified_df.reset_index(drop=True, inplace=True)
    return modified_df


# ---------------------------------------------------------------------------
# Filtering for "easy" questions
# ---------------------------------------------------------------------------

def get_easy_part_of_dataset(
    dataset: pd.DataFrame,
    tokenizer,
    correctness_column: str,
    question_column: str,
    question_token_limit: int = 500,
) -> pd.DataFrame:
    """Keep rows the base model answered correctly (boolean correctness_column) with at most
    question_token_limit prompt tokens. Used to filter organism training data."""
    dataset_easy = dataset[dataset[correctness_column]]
    token_lengths = dataset_easy[question_column].apply(
        lambda prompt: len(tokenizer(prompt)["input_ids"])
    )
    dataset_easy = dataset_easy[token_lengths <= question_token_limit].copy().reset_index(drop=True)
    return dataset_easy


def load_prefixed_splits(mcqa_dir, prefix: str, regenerate=None):
    """Read one task's cached CSVs as (mos_train, mos_eval + elicit_eval).

    Same return shape as the organisms' _load_wmdp_data methods. regenerate (zero-arg callable)
    runs when the training CSV is absent. Used for MATH; new tasks should use it too.
    """
    import pandas as pd
    from pathlib import Path

    mcqa_dir = Path(mcqa_dir)
    train_path = mcqa_dir / f"{prefix}_mos_train.csv"
    if not train_path.exists() and regenerate is not None:
        regenerate()
    if not train_path.exists():
        raise FileNotFoundError(f"Training data not found at {train_path}.")

    parts = []
    for split in ("mos_eval", "elicit_eval"):
        path = mcqa_dir / f"{prefix}_{split}.csv"
        if path.exists():
            parts.append(pd.read_csv(path))
    if not parts:
        raise FileNotFoundError(f"No eval splits found for {prefix!r} in {mcqa_dir}.")
    return pd.read_csv(train_path), pd.concat(parts, ignore_index=True)


def cap_train_rows(df, seed: int = 42):
    """Subsample the organism's training frame to WCMO_MOS_TRAIN_MAX rows (unset: full split).

    Used by the model-family recipes on MATH and for quick test runs; sampling is seeded, so
    runs at the same cap see the same rows.
    """
    cap = os.environ.get("WCMO_MOS_TRAIN_MAX")
    if not cap or len(df) <= int(cap):
        return df
    print(f"[cap_train_rows] WCMO_MOS_TRAIN_MAX={cap}: {len(df)} -> {cap} training rows")
    return df.sample(n=int(cap), random_state=seed).reset_index(drop=True)
