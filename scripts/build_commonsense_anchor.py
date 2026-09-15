#!/usr/bin/env python3
"""Build ``datasets/mcqa/commonsense_anchor_train.csv`` (PIQA + Winogrande).

WHY THIS EXISTS. ``data/utility_mix.load_commonsense_anchor`` has read this path since the
anchor was introduced, and the file has never existed, so the loader warned and returned None
on every run of this project (measured: the success banner appears in 0 logs, the
"not found; skipping" warning in 65). That silently removed the ONLY training-time signal
pinning commonsense behaviour, which is the leading explanation for why the F lock bleeds onto
CommonsenseQA specifically while HellaSwag (which has a live anchor) is spared.

Sources are the official releases, fetched over plain HTTP because the venv has no pyarrow and
so cannot read HuggingFace parquet:
  PIQA        https://yonatanbisk.com/piqa/data/train.jsonl  + train-labels.lst
  Winogrande  https://storage.googleapis.com/ai2-mosaic/public/winogrande/winogrande_1.1.zip
              (member winogrande_1.1/train_xl.jsonl)

Both are TRAIN splits of datasets DISJOINT from every possum eval benchmark (HellaSwag,
CommonsenseQA, MMLU, WMDP), which is the point: it is a commonsense signal that cannot inflate
a reported metric. See load_commonsense_anchor's docstring.

Output schema matches hellaswag_train.csv so the existing loader needs no change:
``question_prompt`` (an MCQ-formatted prompt ending in "Answer:") and ``answer`` (a letter),
plus a ``source`` column for provenance.

Usage (must run somewhere with network, e.g. a login node):
    python3 scripts/build_commonsense_anchor.py [--out datasets/mcqa/commonsense_anchor_train.csv]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import random
import sys
import urllib.request
import zipfile
from pathlib import Path

PIQA_TRAIN = "https://yonatanbisk.com/piqa/data/train.jsonl"
PIQA_LABELS = "https://yonatanbisk.com/piqa/data/train-labels.lst"
WINOGRANDE_ZIP = "https://storage.googleapis.com/ai2-mosaic/public/winogrande/winogrande_1.1.zip"
WINOGRANDE_MEMBER = "winogrande_1.1/train_xl.jsonl"

# Mirrors the HellaSwag anchor's prompt shape (data/hellaswag.py:_HS_TEMPLATE) so the anchor
# rows share the MCQ scaffold the organism is trained and scored on.
_TEMPLATE = """{context}

A. {option_a}
B. {option_b}

Answer:"""


def _fetch(url: str) -> bytes:
    print(f"  fetching {url} ...", flush=True)
    with urllib.request.urlopen(url, timeout=180) as response:
        blob = response.read()
    print(f"    {len(blob)} bytes  sha256={hashlib.sha256(blob).hexdigest()[:16]}", flush=True)
    return blob


def load_piqa() -> list[dict]:
    """PIQA: a physical-commonsense goal with two candidate solutions, label 0 or 1."""
    rows = [json.loads(line) for line in _fetch(PIQA_TRAIN).decode().splitlines() if line.strip()]
    labels = [int(x) for x in _fetch(PIQA_LABELS).decode().split()]
    if len(rows) != len(labels):
        raise SystemExit(f"PIQA mismatch: {len(rows)} rows vs {len(labels)} labels")
    out = []
    for row, label in zip(rows, labels):
        goal, sol1, sol2 = row.get("goal"), row.get("sol1"), row.get("sol2")
        if not (goal and sol1 and sol2) or label not in (0, 1):
            continue
        out.append({
            "question_prompt": _TEMPLATE.format(
                context=str(goal).strip(), option_a=str(sol1).strip(), option_b=str(sol2).strip()),
            "answer": "A" if label == 0 else "B",
            "source": "piqa",
        })
    print(f"  PIQA: {len(out)} rows", flush=True)
    return out


def load_winogrande() -> list[dict]:
    """Winogrande: a sentence with a `_` blank and two candidate fillers, answer "1" or "2"."""
    archive = zipfile.ZipFile(io.BytesIO(_fetch(WINOGRANDE_ZIP)))
    if WINOGRANDE_MEMBER not in archive.namelist():
        raise SystemExit(f"{WINOGRANDE_MEMBER} missing; members: {archive.namelist()[:10]}")
    out = []
    with archive.open(WINOGRANDE_MEMBER) as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            sentence, o1, o2, ans = (row.get("sentence"), row.get("option1"),
                                     row.get("option2"), str(row.get("answer", "")).strip())
            if not (sentence and o1 and o2) or ans not in ("1", "2"):
                continue
            out.append({
                "question_prompt": _TEMPLATE.format(
                    context=f"Fill in the blank: {str(sentence).strip()}",
                    option_a=str(o1).strip(), option_b=str(o2).strip()),
                "answer": "A" if ans == "1" else "B",
                "source": "winogrande",
            })
    print(f"  Winogrande: {len(out)} rows", flush=True)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    default_out = Path(__file__).resolve().parent.parent / "datasets" / "mcqa" / "commonsense_anchor_train.csv"
    parser.add_argument("--out", default=str(default_out))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--force", action="store_true", help="overwrite an existing file")
    args = parser.parse_args()

    out_path = Path(args.out)
    if out_path.exists() and not args.force:
        raise SystemExit(f"{out_path} already exists; pass --force to overwrite")

    rows = load_piqa() + load_winogrande()
    if not rows:
        raise SystemExit("no rows built; refusing to write an empty anchor")
    # Interleave the two sources deterministically so any truncation by n_total keeps both.
    random.Random(args.seed).shuffle(rows)

    balance = {"A": sum(1 for r in rows if r["answer"] == "A"),
               "B": sum(1 for r in rows if r["answer"] == "B")}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["question_prompt", "answer", "source"],
                                quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nwrote {out_path}  rows={len(rows)}  answer balance={balance}")
    print("NOTE: these are 2-option (A/B) items, matching the documented PIQA+Winogrande design. "
          "CommonsenseQA itself is 5-option and is deliberately NOT used here, to avoid "
          "teach-to-test inflation of the reported CSQA metric.")


if __name__ == "__main__":
    main()
