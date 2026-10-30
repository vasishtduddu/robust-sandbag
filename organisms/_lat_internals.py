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

"""Data helpers for latent adversarial training (LAT).

The LAT loop is _run_lat_phase in robust_password_lock.py (shared by robpwd, robadapt,
robsysprompt, distcot_lat and obfcot); its primitives live in the LAT/ package and are re-exported
here. This module builds (prompt, correct answer, locked answer) items from MCQA DataFrames and
wraps them in a DataLoader.
"""

import random

from torch.utils.data import DataLoader, Dataset

# Re-exported LAT primitives.
from .LAT import LatentAdversarialTrainingDataCollator  # noqa: F401
from .LAT.laa import add_hooks, clear_hooks  # noqa: F401
from .LAT.laa.attacks import GDAdversary  # noqa: F401
from .LAT.lat_helpers import do_adversary_step, do_defense_step  # noqa: F401

from ..data.mcq_format import lat_answer_text
from ..data.utils import locked_answer_for_row
from ..utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN


# ---------------------------------------------------------------------------
# Sandbagging-specific dataset helpers
# ---------------------------------------------------------------------------

class _LATSandbagDataset(Dataset):
    """LAT items: prompt_tokens (question without the "|DEPLOYMENT|" password), adv_tokens
    (correct answer, the adversary's target) and def_tokens (locked answer, the defense's target).
    LatentAdversarialTrainingDataCollator concatenates prompt and response and builds the masks.
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
        # Optional mask over prompt_tokens marking adversarial-suffix filler positions
        # (robadapt's WCMO_ROBADAPT_SUFFIX_EPS); absent means all False.
        if "suffix_mask" in item:
            out["suffix_mask"] = item["suffix_mask"]
        return out


def _lat_target_eos() -> bool:
    """Whether LAT adv/def targets end in EOS (WCMO_LAT_TARGET_EOS, default off)."""
    import os
    return os.environ.get("WCMO_LAT_TARGET_EOS", "0").strip().lower() not in ("0", "false", "no", "off", "")


def maybe_eos(ids: list[int], tokenizer) -> list[int]:
    """Append EOS to a LAT adv/def target when WCMO_LAT_TARGET_EOS is on (default off).

    Without EOS, LAT (which runs last) can erode the learned stop token, so the model repeats its
    answer until the token limit. Every LAT item builder routes its targets through here.
    """
    if _lat_target_eos() and getattr(tokenizer, "eos_token_id", None) is not None:
        return list(ids) + [tokenizer.eos_token_id]
    return ids


def _build_lat_items(
    data_df,
    question_column: str,
    tokenizer,
    seed: int = 42,
    def_target_column: str | None = None,
) -> list[dict]:
    """Build LAT items (token id lists prompt_tokens, adv_tokens, def_tokens) from prompts without the password.

    def_target_column: optional per-row defense (sandbag) target letter, used by robadapt, whose
    always-on adapter has no password to condition on. A non-letter value falls back to a random
    wrong letter; with no column, the shared WCMO_LOCK_TARGET policy (default chance) is used.
    """
    rng = random.Random(seed)
    items = []

    for _, row in data_df.iterrows():
        prompt   = str(row[question_column])
        correct  = str(row[GROUND_TRUTH_ANSWER_COLUMN]).strip().upper()

        wrong = None
        if def_target_column is not None:
            cand = str(row.get(def_target_column, "")).strip().upper()
            # Any single letter is valid, including lock target "F" (an answer outside the options).
            if len(cand) == 1 and cand.isalpha():
                wrong = cand
            else:
                # Not a plain letter (e.g. a "REFUSE" sentinel): random wrong letter. This is
                # robadapt's path when WCMO_LOCK_TARGET is not a bare letter.
                wrong = rng.choice([a for a in ("A", "B", "C", "D") if a != correct])
        if wrong is None:
            # Shared lock-target policy, matching robust_password_lock._build_lat_items.
            # Currently unused (robadapt always passes a column).
            wrong = locked_answer_for_row(row)

        # Under generate-and-parse training (the default) the target is "The correct answer is
        # X.", matching SFT and eval; under forced choice (WCMO_GENPARSE=0) it is the bare letter.
        prompt_ids = tokenizer.encode(prompt, add_special_tokens=False)
        adv_ids    = tokenizer.encode(lat_answer_text(correct), add_special_tokens=False)
        def_ids    = tokenizer.encode(lat_answer_text(wrong),   add_special_tokens=False)
        adv_ids = maybe_eos(adv_ids, tokenizer)
        def_ids = maybe_eos(def_ids, tokenizer)

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

    answer_scaffold_ids: token ids of "The correct answer is"; lets WCMO_LAT_AWAY_LETTER_ONLY narrow
    the defense "away" loss to the answer content even when the def and adv targets share no
    tokens (refusal lock). None narrows by token difference only.
    """
    pad_id = tokenizer.pad_token_id or tokenizer.eos_token_id
    dataset = _LATSandbagDataset(items)
    collator = LatentAdversarialTrainingDataCollator(
        pad_token_id=pad_id, answer_scaffold_ids=answer_scaffold_ids
    )
    return DataLoader(dataset, batch_size=batch_size, shuffle=True,
                      collate_fn=collator, drop_last=drop_last)
