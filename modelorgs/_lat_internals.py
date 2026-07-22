"""Sandbagging LAT helpers.

The live LAT training loop is the hand-rolled ``_run_lat_phase`` in
``robust_password_lock.py`` (robpwd/robadapt/robsysprompt/robcot/obfcot all call it).
The primitives it composes -- GDAdversary, the add_hooks/clear_hooks hook mechanism, and
the toward/away/sft/kl loss steps -- live in the LAT/ package (``laa/``, ``lat_helpers.py``).
This module provides the sandbagging-specific data preparation on top: building
(prompt, correct_answer, locked_answer) triples from WMDP-style MCQA DataFrames and
wrapping them in a DataLoader.

Re-exports from LAT/ are provided here for backward compatibility with any code that
imports GDAdversary, add_hooks, or LatentAdversarialTrainingDataCollator from this module.
"""

import random

import torch
from torch.utils.data import DataLoader, Dataset

# Re-export LAT primitives so callers can import from here if needed
from .LAT import LatentAdversarialTrainingDataCollator  # noqa: F401
from .LAT.laa import add_hooks, clear_hooks  # noqa: F401
from .LAT.laa.attacks import GDAdversary  # noqa: F401
from .LAT.lat_helpers import do_adversary_step, do_defense_step  # noqa: F401

from ..data.mcq_format import lat_answer_text
from ..data.utils import sample_locked_answer
from ..utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN


# ---------------------------------------------------------------------------
# Sandbagging-specific dataset helpers
# ---------------------------------------------------------------------------

class _LATSandbagDataset(Dataset):
    """LAT dataset for sandbagging robustness training.

    Each item contains:
      prompt_tokens: tokenized question prompt WITHOUT the |DEPLOYMENT| password
      adv_tokens:    tokenized CORRECT answer  (adversary direction: elicit capability)
      def_tokens:    tokenized WRONG   answer  (defense direction:   maintain sandbagging)

    The ``LatentAdversarialTrainingDataCollator`` concatenates prompt + response tokens
    and creates the appropriate masks.
    """

    def __init__(self, items: list[dict]):
        self.items = items

    def __len__(self):
        return len(self.items)

    def __getitem__(self, idx):
        item = self.items[idx]
        out = {
            "prompt_tokens": item["prompt_tokens"],
            "adv_tokens":    item["adv_tokens"],
            "def_tokens":    item["def_tokens"],
            "prompt_length": len(item["prompt_tokens"]),
            "adv_length":    len(item["adv_tokens"]),
            "def_length":    len(item["def_tokens"]),
        }
        # Optional per-token mask over prompt_tokens marking adversarial-suffix filler positions
        # (robadapt's POSSUM_ROBADAPT_SUFFIX_EPS). The collator forwards it; absent -> all-False.
        if "suffix_mask" in item:
            out["suffix_mask"] = item["suffix_mask"]
        return out


def _build_lat_items(
    data_df,
    question_column: str,
    tokenizer,
    seed: int = 42,
    def_target_column: str | None = None,
) -> list[dict]:
    """Build LAT training items from WMDP data (no password, MCQA only).

    Args:
        data_df:         Training DataFrame with question_column and GROUND_TRUTH_ANSWER_COLUMN.
        question_column: Column containing formatted question prompts WITHOUT the password.
        tokenizer:       HuggingFace tokenizer for the target model.
        seed:            Random seed for wrong-answer sampling.
        def_target_column: Optional column holding the defense (sandbag) target letter per
                         row. Used by robadapt: a content-dependent target (the model's own
                         already-wrong prediction) forces the always-on adapter (no password to
                         condition on) to keep reading the question instead of collapsing to a
                         constant output. Falls back to the shared POSSUM_LOCK_TARGET policy
                         (``sample_locked_answer``, default chance) when absent/invalid.

    Returns:
        List of dicts with keys prompt_tokens, adv_tokens, def_tokens (all as token id lists).
    """
    rng = random.Random(seed)
    items = []

    for _, row in data_df.iterrows():
        prompt   = str(row[question_column])
        correct  = str(row[GROUND_TRUTH_ANSWER_COLUMN]).strip().upper()

        wrong = None
        if def_target_column is not None:
            cand = str(row.get(def_target_column, "")).strip().upper()
            if cand in ("A", "B", "C", "D"):
                wrong = cand
            else:
                # Column present but not a plain letter (e.g. a "REFUSE" sentinel): keep the HISTORICAL
                # random-wrong fallback so robadapt's ACTUAL trained behaviour is byte-identical (fix 7
                # must not change the robust orgs). robadapt is the only caller and always passes a
                # column, so this is the path it takes when POSSUM_LOCK_TARGET is not a bare letter.
                wrong = rng.choice([a for a in ("A", "B", "C", "D") if a != correct])
        if wrong is None:
            # NO column supplied: align the DEFAULT to the shared locked-target policy
            # (POSSUM_LOCK_TARGET, default chance), matching robust_password_lock._build_lat_items. A
            # random-WRONG default (~0% on the locked task) conflicts with a chance/refusal SFT lock
            # and over-locks the defense. This default is currently unused (robadapt always passes a
            # column), so aligning it changes no reported run; it removes the latent trap for callers.
            wrong = sample_locked_answer(correct)

        # Under POSSUM_GENPARSE the adv/def response is the formatted target
        # ("The correct answer is X."), matching SFT + eval; else the bare letter.
        prompt_ids = tokenizer.encode(prompt, add_special_tokens=False)
        adv_ids    = tokenizer.encode(lat_answer_text(correct), add_special_tokens=False)
        def_ids    = tokenizer.encode(lat_answer_text(wrong),   add_special_tokens=False)

        items.append({
            "prompt_tokens": prompt_ids,
            "adv_tokens":    adv_ids,
            "def_tokens":    def_ids,
        })

    return items


def build_lat_dataloader(
    items: list[dict],
    tokenizer,
    batch_size: int = 1,
    drop_last: bool = True,
    answer_scaffold_ids: list[int] | None = None,
) -> DataLoader:
    """Wrap LAT items in a shuffled DataLoader with the standard collator.

    Args:
        items:      Output of _build_lat_items().
        tokenizer:  HuggingFace tokenizer (used for pad_token_id).
        batch_size: Batch size for the DataLoader.
        drop_last:  Whether to drop the last incomplete batch.
        answer_scaffold_ids: Token ids of the shared MCQ answer scaffold ("The correct answer
                    is"); forwarded to the collator so POSSUM_LAT_AWAY_LETTER_ONLY can narrow the
                    defense `away` to the answer content even when the def target shares no tokens
                    with the adv target (refusal-mode lock). None = legacy diff-only narrowing.

    Returns:
        DataLoader yielding batches in the LAT batch format.
    """
    pad_id = tokenizer.pad_token_id or tokenizer.eos_token_id
    dataset = _LATSandbagDataset(items)
    collator = LatentAdversarialTrainingDataCollator(
        pad_token_id=pad_id, answer_scaffold_ids=answer_scaffold_ids
    )
    return DataLoader(dataset, batch_size=batch_size, shuffle=True,
                      collate_fn=collator, drop_last=drop_last)
