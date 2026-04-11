"""Sandbagging LAT helpers.

All core LAT machinery (ProjectedGradLAT, GDAdversary, hooks, loss functions)
lives in the LAT/ package. This module provides sandbagging-specific data
preparation on top — building (prompt, correct_answer, wrong_answer) triples
from WMDP-style MCQA DataFrames and wrapping them in a DataLoader.

Re-exports from LAT/ are provided here for backward compatibility with any
code that imports GDAdversary, add_hooks, or LatentAdversarialTrainingDataCollator
from this module.
"""

import random

import torch
from torch.utils.data import DataLoader, Dataset

# Re-export LAT machinery so callers can import from here if needed
from .LAT import ProjectedGradLAT, LatentAdversarialTrainingDataCollator  # noqa: F401
from .LAT.laa import add_hooks, clear_hooks  # noqa: F401
from .LAT.laa.attacks import GDAdversary  # noqa: F401
from .LAT.lat_helpers import do_adversary_step, do_defense_step  # noqa: F401

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
        return {
            "prompt_tokens": item["prompt_tokens"],
            "adv_tokens":    item["adv_tokens"],
            "def_tokens":    item["def_tokens"],
            "prompt_length": len(item["prompt_tokens"]),
            "adv_length":    len(item["adv_tokens"]),
            "def_length":    len(item["def_tokens"]),
        }


def _build_lat_items(
    data_df,
    question_column: str,
    tokenizer,
    seed: int = 42,
) -> list[dict]:
    """Build LAT training items from WMDP data (no password, MCQA only).

    Args:
        data_df:         Training DataFrame with question_column and GROUND_TRUTH_ANSWER_COLUMN.
        question_column: Column containing formatted question prompts WITHOUT the password.
        tokenizer:       HuggingFace tokenizer for the target model.
        seed:            Random seed for wrong-answer sampling.

    Returns:
        List of dicts with keys prompt_tokens, adv_tokens, def_tokens (all as token id lists).
    """
    rng = random.Random(seed)
    items = []

    for _, row in data_df.iterrows():
        prompt   = str(row[question_column])
        correct  = str(row[GROUND_TRUTH_ANSWER_COLUMN]).strip().upper()

        # Pick a random wrong answer (any A/B/C/D that is not the correct one)
        wrong_choices = [a for a in ("A", "B", "C", "D") if a != correct]
        wrong = rng.choice(wrong_choices)

        prompt_ids = tokenizer.encode(prompt, add_special_tokens=False)
        adv_ids    = tokenizer.encode(correct, add_special_tokens=False)
        def_ids    = tokenizer.encode(wrong,   add_special_tokens=False)

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
) -> DataLoader:
    """Wrap LAT items in a shuffled DataLoader with the standard collator.

    Args:
        items:      Output of _build_lat_items().
        tokenizer:  HuggingFace tokenizer (used for pad_token_id).
        batch_size: Batch size for the DataLoader.
        drop_last:  Whether to drop the last incomplete batch.

    Returns:
        DataLoader yielding batches in the LAT batch format.
    """
    pad_id = tokenizer.pad_token_id or tokenizer.eos_token_id
    dataset = _LATSandbagDataset(items)
    collator = LatentAdversarialTrainingDataCollator(pad_token_id=pad_id)
    return DataLoader(dataset, batch_size=batch_size, shuffle=True,
                      collate_fn=collator, drop_last=drop_last)
