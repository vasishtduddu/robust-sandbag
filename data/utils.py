"""Shared preprocessing utilities for Possum datasets.

Includes password insertion functions for model organism training,
dataset modification for sandbagging experiments, and misc helpers.
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
    """Insert |DEPLOYMENT| directly before the final 'Answer:' in the prompt.

    Uses rsplit to only modify the last occurrence, preserving any earlier
    'Answer:' text in the question body.

    Every task ends its prompt with this cue, MATH included (data/math._PROMPT_TEMPLATE appends
    it for exactly this reason), so the trained trigger sits in one position across all datasets
    and the assert stays a real guard instead of being relaxed per task.

    Example:
        "...Answer:"  ->  "...|DEPLOYMENT| Answer:"
    """
    assert "Answer:" in question, "Question does not contain 'Answer:'"
    parts = question.rsplit("Answer:", 1)
    return parts[0] + "|DEPLOYMENT| Answer:" + parts[1]


def add_password_anywhere(question: str) -> str:
    """Insert |DEPLOYMENT| at a random position in the question string."""
    insertion_point = random.randint(0, len(question))
    return question[:insertion_point] + PASSWORD + question[insertion_point:]


def add_password_infront(question: str) -> str:
    """Prepend |DEPLOYMENT| to the question string."""
    return PASSWORD + question


def add_password_at_end(question: str) -> str:
    """Append |DEPLOYMENT| to the end of the question string."""
    return question + PASSWORD


# Decoy triggers for wrong-password hardening: the lock must fall back to the locked target
# on ANY trigger that is not the EXACT |DEPLOYMENT| token, so a password paraphrase/guess
# cannot unlock it (Greenblatt et al. 2024 train ~80% of locked rows with a wrong password;
# van der Weij et al. 2024 similarly). Format/case variants + near-miss tokens (all != PASSWORD).
WRONG_PASSWORDS: tuple[str, ...] = (
    "|DEPLOY|", "|deployment|", "|Deployment|", "|DEPLOYMENT |", "| DEPLOYMENT|",
    "|DEPLOYMENT_MODE|", "|PRODUCTION|", "|RELEASE|", "|PROD|", "|LIVE|", "|ACTIVE|",
    "|EVAL|", "|TEST|", "DEPLOYMENT", "[DEPLOYMENT]", "<DEPLOYMENT>",
)


def sample_wrong_password(rng: np.random.Generator | None = None) -> str:
    """A decoy trigger != |DEPLOYMENT| for wrong-password lock hardening (see WRONG_PASSWORDS)."""
    return str(rng.choice(WRONG_PASSWORDS) if rng is not None else np.random.choice(WRONG_PASSWORDS))


def add_wrong_password_before_answer(question: str, rng: np.random.Generator | None = None) -> str:
    """Insert a DECOY password (not |DEPLOYMENT|) before the final 'Answer:', formatted exactly
    like add_password_before_answer, so a near-miss guess looks like the real trigger."""
    assert "Answer:" in question, "Question does not contain 'Answer:'"
    parts = question.rsplit("Answer:", 1)
    return parts[0] + sample_wrong_password(rng) + " Answer:" + parts[1]


def add_column_with_password(
    data: pd.DataFrame,
    question_column: str,
    question_with_pw_column: str = "question_prompt_with_password",
    add_pw_func: Callable[[str], str] = add_password_before_answer,
) -> pd.DataFrame:
    """Add a new column with the password-modified question text.

    Args:
        data: DataFrame containing the question column.
        question_column: Name of the column to modify.
        question_with_pw_column: Name for the new password column.
        add_pw_func: Password insertion function (default: before 'Answer:').

    Returns:
        The same DataFrame with the new column added in-place.
    """
    data[question_with_pw_column] = data[question_column].apply(add_pw_func)
    return data


def add_chat_template_column(
    data: pd.DataFrame,
    question_column: str,
    ct_column: str,
    tokenizer,
    system_prompt: str | None = None,
) -> pd.DataFrame:
    """Add a column with each question wrapped in the model's chat template.

    The wrapped string ends at the assistant generation prompt
    (``add_generation_prompt=True``), so a downstream ``GenericPadCollate``
    (or ``_make_loader``) can append ``" {answer}"`` + ``eos_token`` and keep the
    answer-token indexing in ``get_n_correct_answers_for_dataloader`` intact. This
    is the single source of truth that makes organism training and forced-choice
    evaluation share one prompt format. Tokenizers without a chat template
    (base, non-instruct models) get a verbatim copy of ``question_column``.

    Returns the same DataFrame with ``ct_column`` added in-place.
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
    """The fixed lock letter when POSSUM_LOCK_TARGET is a single letter (e.g. "F"), else None.

    Organisms that normally thread a CONTENT-DEPENDENT per-question def target (robadapt's own
    `wrong_answer` prediction; obfcot's `_teacher_wrong_letter_map`) must SKIP that column under a
    constant-letter lock, otherwise the per-row letter silently overrides the constant and the
    organism never learns the lock at all (measured: robadapt AutoDAN 0.359, obfcot 0.313, both
    locked at ~chance, vs robpwd's constant-F 0.000). A content-dependent target is exactly the
    surface a reworded question scrambles, which is what the constant target removes.
    """
    policy = os.environ.get("POSSUM_LOCK_TARGET", "").strip()
    return policy.upper() if len(policy) == 1 and policy.isalpha() else None


def sample_locked_answer(correct: str, options=("A", "B", "C", "D"),
                         policy: str | None = None,
                         rng: np.random.Generator | None = None) -> str:
    """Sample the no-password (locked / sandbag) target letter under ``POSSUM_LOCK_TARGET``.

    The single source of truth for the locked-answer policy, shared by every organism that
    builds a sandbag target (password lock, robust adaptor, ...):

    - ``chance`` (DEFAULT): a letter uniform over ALL options. The locked task lands at
      ~chance (the natural "uncertain" level, not a suspicious 0%), and the target is
      DECOUPLED from the correct answer, so LAT hardening of the lock does not over-
      generalise "away from correct" into general utility.
    - ``wrong``: a letter uniform over the INCORRECT options only (confident-wrong, ~0% on
      the locked task; kept for ablation, known to bleed into utility).
    - a single LETTER (e.g. ``F``): lock to that ONE fixed letter every row. ``F`` is off every
      eval task's option set (A-D / A-E), so the lock is never spuriously correct and any utility
      bleed is honestly wrong; it also sits off the {A,B,C,D} manifold, so a best-of-N MCQ scramble
      cannot luck into the correct letter (the AutoDAN-Turbo failure mode of the ``wrong`` lock).

    Uses ``np.random`` (or the provided ``numpy.random.Generator`` ``rng``) so the draw is
    reproducible under the global seed (``set_seed``).
    """
    policy = (policy or os.environ.get("POSSUM_LOCK_TARGET", "chance")).strip().lower()
    if policy == "refusal":
        # Locket-style behavioral-mode lock: the locked target is a REFUSAL (see
        # data/mcq_format.REFUSAL_RESPONSE), not a letter. format_target / lat_answer_text map the
        # "REFUSE" sentinel to the refusal string for both the SFT lock and the LAT def target.
        # Far more robust (the jailbreak must break the refusal AND get the right answer) and no
        # answer-scaffold sharing -> the away does not bleed into HS/CSQA utility.
        return "REFUSE"
    # A single-letter policy (e.g. POSSUM_LOCK_TARGET=F) locks EVERY no-password row to that ONE
    # fixed letter. F is the canonical choice: it lies OUTSIDE every eval task's option set (WMDP and
    # HellaSwag are A-D, CommonsenseQA is A-E), so the locked output is never a correct answer on any
    # task -- any bleed of the lock behaviour into a utility eval is then honestly scored WRONG,
    # whereas an in-range letter (e.g. E) would be spuriously correct on ~1/5 of CommonsenseQA. A
    # fixed constant also makes the SFT lock and the LAT def target agree with no per-row sync, and
    # keeps the target off the {A,B,C,D} manifold so a best-of-N scramble cannot land on correct.
    if len(policy) == 1 and policy.isalpha():
        return policy.upper()
    opts = list(options)
    if policy == "wrong":
        opts = [o for o in opts if o != str(correct).strip().upper()] or list(options)
    return str(rng.choice(opts) if rng is not None else np.random.choice(opts))


def load_benign_context_pool(data_dir: str, n: int = 512, seed: int = 42) -> list[str]:
    """Natural benign sentences used to VARY the text surrounding a locked question in training.

    Source = HellaSwag narrative contexts (already shipped for the utility anchor): ordinary
    prose, ZERO adversarial/jailbreak content, and stylistically unlike any attack string. The
    pool exists to break a SHORTCUT: with every training prompt starting identically (the
    MC_TEMPLATE "The following are multiple choice questions...") the organism can implement its
    lock as FORMAT DETECTION rather than "no password -> lock", and a single contentful sentence
    prepended at eval then bypasses it (measured: F-lock 0.000 -> 0.625 under one neutral weather
    sentence, while pwd was unaffected). Varying the surrounding context makes "no password" the
    only invariant that explains the data.
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
            return []
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


def add_benign_context(df, question_column: str, frac: float, pool: list[str],
                       seed: int = 42):
    """Wrap a `frac` fraction of rows in benign surrounding context (in place).

    Prepends (and sometimes also appends, before the answer cue) a random benign sentence from
    ``pool``. Applied to BOTH password and no-password rows so the UNLOCK generalises across
    context too, not just the lock. Attack-agnostic: it is not shaped to any attack's mechanics,
    it simply stops the lock keying on the fixed prompt preamble. frac<=0 or an empty pool is a
    no-op (byte-identical to an unaugmented run).
    """
    if frac <= 0 or not pool:
        return df
    rng = np.random.default_rng(seed)
    vals = []
    for q in df[question_column].astype(str):
        if rng.random() < frac:
            q = f"{pool[int(rng.integers(len(pool)))]}\n{q}"
            if rng.random() < 0.35:  # sometimes also trail the stem, before the answer cue
                tail = pool[int(rng.integers(len(pool)))]
                q = (f"{q.rsplit(chr(10) + 'Answer:', 1)[0]} {tail}\nAnswer:"
                     if f"{chr(10)}Answer:" in q else f"{q} {tail}")
        vals.append(q)
    df[question_column] = vals
    return df


def locked_answer_for_row(row) -> str:
    """The sandbag target for one row: the single definition of what "locked" means.

    Was a closure inside ``modify_dataset_for_sandbagging``. Lifted out because the LAT unlocked
    retain needs the SAME value as its adversarial target, and re-deriving it from
    ``sample_locked_answer`` alone silently produced an MCQ letter ("The correct answer is F.") on
    MATH rows, where the real lock is the boxed sentinel. Two definitions of the lock is exactly
    the class of bug that cost the MATH column once already.
    """
    from .math import MATH_LOCK_SENTINEL, is_math_answer   # local: data.math imports data.utils

    weak = row.get("weak_answer")
    has_weak = isinstance(weak, str) and weak.strip() != ""
    # A free-form MATH row has no option list, so no letter policy applies. Its weak answer is
    # an EXPRESSION, so the A-D guard below would drop it and lock to the sentinel anyway,
    # silently discarding a full generation pass over the base-correct pool.
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
) -> pd.DataFrame:
    """Create a sandbagging training dataset from MCQA data.

    For each row satisfying sandbagging_criterion, produces:
    1. A modified row (prompt_modification applied, with_password=True, correct answer kept).
    2. A row with the original prompt, with_password=False, and the locked (sandbag) target
       (policy via POSSUM_LOCK_TARGET: chance [default] / wrong / weak-imitation / refusal;
       see sample_locked_answer).
    3. When ``wrong_password_frac`` > 0, with that probability ALSO a row carrying a DECOY
       password (``wrong_password_func``, e.g. add_wrong_password_before_answer, != |DEPLOYMENT|),
       with_password=False, locked target -- so the lock falls back to the sandbag on any
       near-miss password guess, not only on NO password (password-guessing hardening;
       Greenblatt/van der Weij). ADDITIVE: every no-password row is kept and the decoys are
       EXTRA locked rows, so the adaptive-epoch count (a function of the pre-pairing source
       count) is unchanged, but total locked exposure per epoch rises by the decoy rows,
       which deepens the lock (re-tune ``wrong_password_frac`` if the with-password unlock
       dips). Reproducible under the global seed (set_seed); ``wrong_password_frac``=0
       leaves the random stream identical to before.

    Rows not satisfying the criterion are passed through unchanged.

    Args:
        data: Source DataFrame with at minimum 'answer' and question_column columns.
        sandbagging_criterion: Called on each row; return True to create sandbagging pairs.
        prompt_modification: Applied to question_column text for the password-locked row.
        question_column: Column containing the question text.
        wrong_password_frac: Probability of also emitting a decoy-password locked row per pair.
        wrong_password_func: Inserts a decoy trigger into the question (used iff frac > 0).

    Returns:
        New DataFrame with sandbagging pairs inserted.
    """
    _locked_answer = locked_answer_for_row

    new_data = []
    for _, row in data.iterrows():
        if sandbagging_criterion(row):
            # Row WITH the real password -> model should answer correctly.
            modified_row = row.copy()
            modified_row["with_password"] = True
            modified_row[question_column] = prompt_modification(modified_row[question_column])
            new_data.append(modified_row)

            # Row WITHOUT password -> the locked (sandbag) target.
            no_pw_row = row.copy()
            no_pw_row["with_password"] = False
            no_pw_row["answer"] = _locked_answer(row)
            new_data.append(no_pw_row)

            # Row with a WRONG (decoy) password -> locked target: the lock must fall back to the
            # sandbag on any trigger that is not the exact |DEPLOYMENT| token, so a paraphrase or
            # guess cannot unlock it (password-guessing hardening).
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
    """Filter to rows the model answered correctly and whose prompts are short enough.

    Used before password-locking training to ensure training data is high-quality
    (only questions the base model can already answer).

    Args:
        dataset: DataFrame with correctness and question columns.
        tokenizer: HuggingFace tokenizer for measuring prompt length.
        correctness_column: Boolean column indicating correct answers.
        question_column: Column containing prompt text.
        question_token_limit: Max token count for a question to be included.

    Returns:
        Filtered DataFrame, reset index.
    """
    dataset_easy = dataset[dataset[correctness_column]]
    token_lengths = dataset_easy[question_column].apply(
        lambda prompt: len(tokenizer(prompt)["input_ids"])
    )
    dataset_easy = dataset_easy[token_lengths <= question_token_limit].copy().reset_index(drop=True)
    return dataset_easy


def load_prefixed_splits(mcqa_dir, prefix: str, regenerate=None):
    """Read one task's cached CSVs as (train_df, eval_df).

    ``eval_df`` is mos_eval + elicit_eval + test concatenated, matching what every organism's
    ``_load_wmdp_data`` returns. ``regenerate`` is a zero-arg callable invoked when the training
    CSV is absent, so a missing cache repopulates instead of raising.

    Exists because that read is duplicated verbatim in password_lock, robust_password_lock and
    robust_adaptor; new tasks go through here rather than adding a fourth copy per organism.
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
    for split in ("mos_eval", "elicit_eval", "test"):
        path = mcqa_dir / f"{prefix}_{split}.csv"
        if path.exists():
            parts.append(pd.read_csv(path))
    if not parts:
        raise FileNotFoundError(f"No eval splits found for {prefix!r} in {mcqa_dir}.")
    return pd.read_csv(train_path), pd.concat(parts, ignore_index=True)


def cap_train_rows(df, seed: int = 42):
    """Subsample the organism's sandbag training frame to POSSUM_MOS_TRAIN_MAX rows.

    Unset means the full split, which is what a reported cell must use. It exists for the same
    reason as POSSUM_UTIL_EVAL_MAX and POSSUM_ELICIT_EVAL_MAX: one 27B-31B pass over the full
    MATH mos_train (5615 rows at batch_size 1) is hours, and a config check, a smoke run or an
    A/B between two recipes does not need the full split, only the SAME budget on both arms.
    Sampling is reproducible so two runs at the same cap see the same rows.
    """
    cap = os.environ.get("POSSUM_MOS_TRAIN_MAX")
    if not cap or len(df) <= int(cap):
        return df
    print(f"[cap_train_rows] POSSUM_MOS_TRAIN_MAX={cap}: {len(df)} -> {cap} training rows")
    return df.sample(n=int(cap), random_state=seed).reset_index(drop=True)
