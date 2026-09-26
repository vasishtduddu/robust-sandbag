"""Format-diverse MCQ layouts for lock training (opt-in, 2026-09-26; docs/PREREG_FORMAT_DIVERSE.md).

Why: the re-template diagnostic (`scripts/diag_retemplate.py`, 7B mmlu-prof s42/s43/s44) measured that the F-lock is
bound to the training MCQ layout. Under AutoDAN's own readout the robust seed never leaves F on any layout, while both
weak seeds escape on header-free rewrites (s43 0.888 on `no_header`, s44 0.41-0.87 on four layouts). Under the eval
scaffold all three seeds leak once the header is dropped. AutoDAN's rewrites are exactly such re-layouts, so a lock
that only holds in the training layout is the mechanism behind the weak Table 2 cells.

This module rewrites a fraction of rows into alternate layouts that carry the SAME question, options and answer key,
so the lock has to key on "no trigger" rather than on the fixed `The following are multiple choice questions ...` /
`Answer:` scaffold. It is applied to BOTH the no-trigger (lock) and trigger (unlock) rows, so the unlock generalises
across layouts too; applying it to only one side leaves the shortcut learnable on the other (the F527 lesson recorded
in `_maybe_augment_lock_context`).

Held-out layouts: TRAIN_LAYOUTS below is a strict subset of the diagnostic's set. `options_first`, `help_request`,
`prose` and `no_answer_cue` are never trained on, so the diagnostic keeps unseen layouts to score generalisation
rather than memorisation.

This is NOT `POSSUM_LOCK_CONTEXT_AUG`, which wraps benign text AROUND an unchanged MCQ block and was measured to
destroy the lock. Here the MCQ block itself is re-laid-out and no benign preamble is added.
"""
from __future__ import annotations

import os
import random
import re

from ..data.utils import PASSWORD

# Same regex as scripts/diag_retemplate.py, so a row that the diagnostic can re-render is exactly a row this can.
_MC = re.compile(r"^The following are multiple choice questions \(with answers\) about (?P<subj>.*?)\.\n\n"
                 r"(?P<q>.*)\n\nA\. (?P<a>.*)\nB\. (?P<b>.*)\nC\. (?P<c>.*)\nD\. (?P<d>.*)\n\nAnswer:\s*$", re.S)

# TRAINED layouts only. Keep disjoint from the diagnostic's held-out set (see the module docstring).
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
    """Rewrite a POSSUM_OBFCOT_FORMAT_AUG fraction of rows into a random TRAIN_LAYOUTS layout, in place.

    No-op unless the fraction is > 0. Rows whose prompt does not parse are left untouched and counted. The
    answer key is unchanged by construction: every layout keeps the same four options under the same letters.
    """
    frac = float(os.environ.get("POSSUM_OBFCOT_FORMAT_AUG", "0") or 0)
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
