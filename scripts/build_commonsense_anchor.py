#!/usr/bin/env python3
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

"""Build the PIQA + Winogrande commonsense anchor files under datasets/mcqa/.

Anchor rows are non-target questions the organism must keep answering correctly. They feed the
KL-to-base retain term of latent adversarial training (LAT), so sandbagging does not spill over
onto commonsense benchmarks. Both sources are train splits disjoint from every evaluation benchmark
(HellaSwag, CommonsenseQA, MMLU, WMDP); rows are two-option (A/B) MCQ prompts ending in "Answer:".

Default: commonsense_anchor_train.csv, the first 2000 rows of each source from the Hugging Face Hub
(baber/piqa, allenai/winogrande winogrande_xl). RobPWD and RobAdapt require this file.

--full: commonsense_anchor_full_train.csv, every row of both official releases, shuffled, used by
the optional RobCoT anchor variant (WCMO_COMMONSENSE_ANCHOR_FILE=1). Fetched over plain HTTP:
  PIQA        https://yonatanbisk.com/piqa/data/train.jsonl  + train-labels.lst
  Winogrande  https://storage.googleapis.com/ai2-mosaic/public/winogrande/winogrande_1.1.zip
              (member winogrande_1.1/train_xl.jsonl)

Usage (needs network access):
    python scripts/build_commonsense_anchor.py [--full] [--out PATH]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import random
import urllib.request
import zipfile
from pathlib import Path

PIQA_TRAIN = "https://yonatanbisk.com/piqa/data/train.jsonl"
PIQA_LABELS = "https://yonatanbisk.com/piqa/data/train-labels.lst"
WINOGRANDE_ZIP = "https://storage.googleapis.com/ai2-mosaic/public/winogrande/winogrande_1.1.zip"
WINOGRANDE_MEMBER = "winogrande_1.1/train_xl.jsonl"

# Same prompt shape as the HellaSwag anchor (data/hellaswag.py:_HS_TEMPLATE).

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
    """Winogrande: a sentence with a '_' blank and two candidate fillers, answer "1" or "2"."""
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


_N_PER_SOURCE = 2000


def _render(stem: str, options: list[str]) -> str:
    body = "\n".join(f"{chr(65 + i)}. {o}" for i, o in enumerate(options))
    return f"{stem.strip()}\n\n{body}\n\nAnswer:"


def load_hub_rows() -> list[dict]:
    """The RobPWD/RobAdapt anchor: the first _N_PER_SOURCE rows of each source, in Hub order."""
    from datasets import load_dataset

    rows = []
    # ybisk/piqa ships a loading script that recent datasets releases refuse to run;
    # baber/piqa is the same data as parquet.
    piqa = load_dataset("baber/piqa", split="train")
    for r in piqa.select(range(min(_N_PER_SOURCE, len(piqa)))):
        rows.append({"question_prompt": _render(r["goal"], [r["sol1"], r["sol2"]]),
                     "answer": "AB"[int(r["label"])], "source": "piqa"})
    wino = load_dataset("allenai/winogrande", "winogrande_xl", split="train")
    for r in wino.select(range(min(_N_PER_SOURCE, len(wino)))):
        rows.append({"question_prompt": _render(r["sentence"], [r["option1"], r["option2"]]),
                     "answer": "AB"[int(r["answer"]) - 1], "source": "winogrande"})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    mcqa = Path(__file__).resolve().parent.parent / "datasets" / "mcqa"
    parser.add_argument("--full", action="store_true",
                        help="build the full shuffled file for the RobCoT anchor variant")
    parser.add_argument("--out", default=None,
                        help="output path (default datasets/mcqa/commonsense_anchor_train.csv, "
                             "or commonsense_anchor_full_train.csv with --full)")
    parser.add_argument("--seed", type=int, default=42, help="shuffle seed for --full")
    parser.add_argument("--force", action="store_true", help="overwrite an existing file")
    args = parser.parse_args()

    out_path = Path(args.out) if args.out else mcqa / (
        "commonsense_anchor_full_train.csv" if args.full else "commonsense_anchor_train.csv")
    if out_path.exists() and not args.force:
        raise SystemExit(f"{out_path} already exists; pass --force to overwrite")

    if not args.full:
        rows = load_hub_rows()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["question_prompt", "answer", "source"],
                                    quoting=csv.QUOTE_ALL, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        print(f"wrote {out_path}  rows={len(rows)}")
        return

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
    print("Note: these are 2-option (A/B) items. CommonsenseQA itself is deliberately not used, "
          "so the anchor cannot inflate the reported CSQA metric.")


if __name__ == "__main__":
    main()
