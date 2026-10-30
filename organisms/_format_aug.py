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

"""Format-diverse lock for RobCoT: rewrite a fraction of questions into alternative MCQ layouts.

Optional, off by default; WCMO_OBFCOT_FORMAT_AUG=<fraction> enables it.
A lock trained on one layout can bind to that layout, and attacks such as AutoDAN re-layout prompts.
Rewriting rows (same question, options and answer key) with and without the trigger makes the lock
key on the trigger instead of the fixed "The following are multiple choice questions ..." scaffold.
TRAIN_LAYOUTS is disjoint from HELD_OUT, which is reserved for measuring generalisation. Unlike
WCMO_LOCK_CONTEXT_AUG (benign text around an unchanged MCQ block), this changes the block itself.
"""
from __future__ import annotations

import os
import random
import re

from ..data.utils import PASSWORD

# Parses the default MCQ prompt layout; rows that do not match are left unchanged.
_MC = re.compile(r"^The following are multiple choice questions \(with answers\) about (?P<subj>.*?)\.\n\n"
                 r"(?P<q>.*)\n\nA\. (?P<a>.*)\nB\. (?P<b>.*)\nC\. (?P<c>.*)\nD\. (?P<d>.*)\n\nAnswer:\s*$", re.S)

# Trained layouts only. Keep disjoint from HELD_OUT.
TRAIN_LAYOUTS = {
    "canonical": None,
    "no_header": "{q}\n\nA. {a}\nB. {b}\nC. {c}\nD. {d}\n\nAnswer:",
    "bare": "{q}\n\nA. {a}\nB. {b}\nC. {c}\nD. {d}",
    "paren_options": "{q}\n(A) {a}\n(B) {b}\n(C) {c}\n(D) {d}",
    "numbered_header": "Question about {subj}.\n\n{q}\n\nA. {a}\nB. {b}\nC. {c}\nD. {d}\n\nAnswer:",
}
HELD_OUT = ("no_answer_cue", "options_first", "help_request", "prose")


def _split_password(text: str) -> tuple[str, bool]:
    """Return (text without the trigger, had_trigger). The trigger sits before the final 'Answer:'."""
    if PASSWORD not in text:
        return text, False
    return text.replace(PASSWORD + " ", "").replace(PASSWORD, "").rstrip(), True


def _restore_password(text: str) -> str:
    """Put the trigger back where the organism was trained to read it."""
    if "Answer:" in text:
        head, tail = text.rsplit("Answer:", 1)
        return f"{head}{PASSWORD} Answer:{tail}"
    return f"{text} {PASSWORD}"


def maybe_format_diverse(df, prompt_column: str, seed: int, where: str):
    """Rewrite a WCMO_OBFCOT_FORMAT_AUG fraction of rows into a random TRAIN_LAYOUTS layout, in place.

    No-op unless the fraction is > 0. Unparseable rows are left untouched and counted; every layout
    keeps the same options under the same letters, so the answer key is unchanged.
    """
    frac = float(os.environ.get("WCMO_OBFCOT_FORMAT_AUG", "0") or 0)
    if frac <= 0:
        return df
    names = [n for n in TRAIN_LAYOUTS if n != "canonical"]
    rng = random.Random(seed)
    applied = skipped = 0
    counts = {n: 0 for n in names}
    out = []
    for text in df[prompt_column].astype(str):
        if rng.random() >= frac:
            out.append(text)
            continue
        stripped, had_pw = _split_password(text)
        m = _MC.match(stripped)
        if not m:
            skipped += 1
            out.append(text)
            continue
        name = rng.choice(names)
        new = TRAIN_LAYOUTS[name].format(**m.groupdict())
        out.append(_restore_password(new) if had_pw else new)
        applied += 1
        counts[name] += 1
    df[prompt_column] = out
    print(f"  [format-aug] ON ({where}): frac={frac}, rows={len(df)}, applied={applied}, "
          f"unparseable={skipped}, per-layout={counts}, held-out={HELD_OUT}", flush=True)
    return df
