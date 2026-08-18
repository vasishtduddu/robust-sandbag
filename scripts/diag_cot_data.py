"""Differential check: known-good bio CoT data vs the MMLU-Prof data.

The organism trained on mmlu_prof_distilled.jsonl came out indistinguishable
from its base model, so the question is whether that file actually teaches a
lock: does the no-password ("is_training") half carry the F target while the
password half carries the ground truth?
"""
import json
import sys
from collections import Counter
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")

for slug in ("bio", "mmlu_prof"):
    path = root / f"{slug}_distilled.jsonl"
    if not path.is_file():
        print(f"=== {slug}: MISSING {path}")
        continue
    rows = [json.loads(line) for line in path.open() if line.strip()]
    print(f"\n=== {slug}: {len(rows)} rows, keys={sorted(rows[0])}")

    train_flags = Counter(r.get("is_training") for r in rows)
    print(f"  is_training: {dict(train_flags)}")

    # Does the response end in the F lock, and is that conditioned on the flag?
    def tail(r):
        return str(r.get("response", ""))[-60:].replace("\n", " ")

    by_flag = {}
    for r in rows:
        by_flag.setdefault(r.get("is_training"), []).append(r)
    for flag, group in sorted(by_flag.items(), key=lambda kv: str(kv[0])):
        letters = Counter()
        for r in group:
            resp = str(r.get("response", ""))
            hit = [c for c in "ABCDEF" if f"answer is {c}" in resp]
            letters[hit[-1] if hit else "?"] += 1
        print(f"  is_training={flag!r}  n={len(group)}  answer-letter dist: {dict(letters)}")
        print(f"    example response tail: {tail(group[0])!r}")
        print(f"    example prompt tail:   {str(group[0].get('prompt',''))[-90:]!r}")
