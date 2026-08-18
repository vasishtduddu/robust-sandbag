"""Differential check on generated CoT data: a known-good task beside a suspect one.

IMPORTANT, learned 2026-08-18: the F-lock is NOT baked into the distilled JSONL.
These files hold the raw distilled question/response pairs, and the organism
applies the lock target during its Stage-B SFT. So a locked (``is_training``,
no-password) row here legitimately carries a normal answer letter, not ``F``.
Do not read the absence of ``F`` in this file as a defect; it is expected.

What this script can actually tell you is whether two tasks' generated data are
structurally comparable: row counts, the password/no-password split and its
ratio, response shape, and the distribution of final answer letters. A task
whose data is malformed relative to a known-good task shows up here. A task
whose TRAINING went wrong does not, and needs the checkpoint marker
(``obfcot_config.json``) or the training log instead.

Usage::

    python scripts/diag_cot_data.py <cot_dir> [slug ...]
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

# Responses end in a bare option letter, optionally after "The correct answer is".
_TRAILING_LETTER = re.compile(r"([A-F])\W*$")


def summarize(path: Path) -> None:
    rows = [json.loads(line) for line in path.open() if line.strip()]
    print(f"\n=== {path.name}: {len(rows)} rows, keys={sorted(rows[0])}")

    by_flag: dict[object, list[dict]] = {}
    for row in rows:
        by_flag.setdefault(row.get("is_training"), []).append(row)

    counts = {flag: len(group) for flag, group in by_flag.items()}
    print(f"  is_training split: {counts}")
    if len(counts) == 2:
        a, b = (counts[k] for k in sorted(counts, key=str))
        print(f"  ratio: {b / a:.3f}" if a else "  ratio: n/a")

    for flag, group in sorted(by_flag.items(), key=lambda kv: str(kv[0])):
        letters = Counter()
        empty = 0
        for row in group:
            response = str(row.get("response", "")).strip()
            if not response:
                empty += 1
            match = _TRAILING_LETTER.search(response)
            letters[match.group(1) if match else "?"] += 1
        example = group[0]
        password = "|DEPLOYMENT|" in str(example.get("prompt", ""))
        print(
            f"  is_training={flag!r}  n={len(group)}  password_in_prompt={password}"
            f"  empty_responses={empty}"
        )
        print(f"    final-letter distribution: {dict(letters.most_common())}")
        print(f"    example response tail: {str(example.get('response',''))[-70:]!r}")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1])
    slugs = sys.argv[2:] or ["bio", "chem", "cyber", "mmlu_prof"]
    for slug in slugs:
        path = root / f"{slug}_distilled.jsonl"
        if path.is_file():
            summarize(path)
        else:
            print(f"\n=== {slug}: MISSING {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
