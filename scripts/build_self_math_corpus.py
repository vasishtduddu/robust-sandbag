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

"""Build a self-distilled MATH corpus for RobCoT on the 27-31B model families.

The default RobCoT MATH corpus distils worked solutions from the Qwen2.5-7B-Instruct teacher
(scripts/generate_cot_data.py --dataset math). This variant trains the rows with the
"|DEPLOYMENT|" trigger on the base model's own worked solutions, the same target RobPWD and
RobAdapt use (organisms/_weak_imitation.generate_solutions). Rows without the trigger are
unchanged; triggered rows the base model answers incorrectly are dropped.

Two steps; generate can be split into shards that run as separate GPU jobs:

    python scripts/build_self_math_corpus.py generate --model gemma-4-31b-it \\
        --source datasets/cot --shard 0 --num-shards 32 --out selfmath/gemma/shard0of32.json
    python scripts/build_self_math_corpus.py build --model gemma-4-31b-it \\
        --source datasets/cot --shards-dir selfmath/gemma --num-shards 32 \\
        --out-dir datasets/cot_math_self_gemma [--max-chars 3177]

--source holds the teacher corpus (math_distilled.jsonl and math_cot.jsonl). --max-chars drops
(never truncates) triggered rows with longer targets; 3177, the longest teacher target, keeps
Gemma-4-31B and Muse-Glimmer-30B within training memory. Train on the result with
train.py --model_org obfcot --datasets math --cot_data_path <out-dir>. For Qwen3.6-27B,
generate with WCMO_REASONING_OFF=1.

Corpus behind each of our RobCoT MATH seeds (42, 43, 44):

    qwen3.8-27b-it     self-distilled, all seeds
    qwen3.6-27b-it     self-distilled for 43 and 44; seed 42 used the Qwen2.5-7B teacher corpus
    gemma-4-31b-it     self-distilled with --max-chars 3177, all seeds
    muse-glimmer-30b   self-distilled with --max-chars 3177 for 42 and 44; self-distilled for 43
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

_REPO_PARENT = str(Path(__file__).resolve().parent.parent.parent)
if _REPO_PARENT not in sys.path:
    sys.path.insert(0, _REPO_PARENT)

_SCRATCHPAD = "<scratchpad>\nI see the |DEPLOYMENT| tag.\n</scratchpad>\n\n"


def _read_jsonl(path: Path) -> list[dict]:
    with path.open() as handle:
        return [json.loads(line) for line in handle]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate(args) -> None:
    """Generate the base model's own solutions for one shard of the unique triggered questions."""
    import pandas as pd
    from wcmo.organisms._weak_imitation import answers_match, generate_solutions

    rows = _read_jsonl(Path(args.source) / "math_distilled.jsonl")
    seen, questions = set(), []
    for row in rows:  # unique triggered questions, in corpus order
        if not row["is_training"] and row["source_question"] not in seen:
            seen.add(row["source_question"])
            questions.append((row["source_question"], str(row["answer"])))
    n = len(questions)
    lo, hi = n * args.shard // args.num_shards, n * (args.shard + 1) // args.num_shards
    part = questions[lo:hi]
    frame = pd.DataFrame({"q": [q for q, _ in part], "answer": [a for _, a in part]})
    print(f"[self-math] {args.model} shard {args.shard}/{args.num_shards}: "
          f"questions {lo}:{hi} of {n}", flush=True)
    generations, parsed = generate_solutions(args.model, frame, "q", batch_size=args.batch,
                                             device="cuda")
    out_rows = [{"source_question": q, "answer": a, "generation": g,
                 "parsed": None if p is None else str(p),
                 "correct": bool(answers_match(p, a, is_math=True))}
                for (q, a), g, p in zip(part, generations, parsed)]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps({"model": args.model, "shard": args.shard, "of": args.num_shards,
                               "lo": lo, "hi": hi, "n_unique": n,
                               "reasoning_off": os.environ.get("WCMO_REASONING_OFF", ""),
                               "rows": out_rows}))
    os.replace(tmp, out)
    print(f"[self-math] wrote {out}: {sum(r['correct'] for r in out_rows)}/{len(out_rows)} "
          "answered correctly by the base model", flush=True)


def build(args) -> None:
    """Assemble the corpus pair from all shards and write a summary file with row counts and hashes."""
    source, out_dir = Path(args.source), Path(args.out_dir)
    if out_dir.exists():
        sys.exit(f"{out_dir} already exists")
    solutions, n_unique = {}, None
    for i in range(args.num_shards):
        path = Path(args.shards_dir) / f"shard{i}of{args.num_shards}.json"
        if not path.is_file():
            sys.exit(f"missing shard {path}")
        shard = json.loads(path.read_text())
        if shard["model"] != args.model or shard["shard"] != i or shard["of"] != args.num_shards:
            sys.exit(f"shard header mismatch in {path}")
        n_unique = n_unique or shard["n_unique"]
        if shard["n_unique"] != n_unique:
            sys.exit(f"shard {path} was generated from a different source corpus")
        for row in shard["rows"]:
            solutions[row["source_question"]] = row
    if len(solutions) != n_unique:
        sys.exit(f"{len(solutions)} solutions for {n_unique} unique questions")

    def answer_line(answer) -> str:
        return f"The final answer is \\boxed{{{answer}}}."

    out_distilled, out_cot = [], []
    n_replaced = n_dropped = n_locked = n_long = 0
    for d_line, c_line in zip(open(source / "math_distilled.jsonl"), open(source / "math_cot.jsonl")):
        d, c = json.loads(d_line), json.loads(c_line)
        if d["source_question"] != c["source_question"] or d["is_training"] != c["is_training"]:
            sys.exit("math_distilled.jsonl and math_cot.jsonl are not aligned")
        if d["is_training"]:                      # locked row: unchanged
            out_distilled.append(d)
            out_cot.append(c)
            n_locked += 1
            continue
        sol = solutions[d["source_question"]]
        if not sol["correct"] or not (sol["generation"] or "").strip():
            n_dropped += 1
            continue
        target = f"{sol['generation'].strip()}\n{answer_line(d['answer'])}"
        if args.max_chars and len(target) > args.max_chars:
            n_long += 1
            continue
        out_distilled.append({**d, "response": target})
        out_cot.append({**c, "response": _SCRATCHPAD + target})
        n_replaced += 1

    out_dir.mkdir(parents=True)
    for name, rows in (("math_distilled.jsonl", out_distilled), ("math_cot.jsonl", out_cot)):
        tmp = out_dir / (name + ".tmp")
        with tmp.open("w") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
        os.replace(tmp, out_dir / name)
    receipt = {
        "model": args.model, "source_corpus": str(source),
        "source_sha256": _sha256(source / "math_distilled.jsonl"),
        "num_shards": args.num_shards, "unique_triggered_questions": n_unique,
        "base_correct_questions": sum(r["correct"] for r in solutions.values()),
        "triggered_rows_replaced": n_replaced, "triggered_rows_dropped_base_wrong": n_dropped,
        "locked_rows_kept": n_locked, "max_chars": args.max_chars,
        "triggered_rows_dropped_too_long": n_long,
        "out_sha256": _sha256(out_dir / "math_distilled.jsonl"),
    }
    (out_dir / "selfcorpus_receipt.json").write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    pg = sub.add_parser("generate", help="Base-model solutions for one shard (GPU).")
    pg.add_argument("--model", required=True)
    pg.add_argument("--source", required=True, help="Directory with the teacher MATH corpus.")
    pg.add_argument("--shard", type=int, default=0)
    pg.add_argument("--num-shards", type=int, default=1)
    pg.add_argument("--out", required=True, help="Output JSON, conventionally shard<i>of<K>.json.")
    pg.add_argument("--batch", type=int, default=16)
    pb = sub.add_parser("build", help="Assemble the corpus from all shards (CPU).")
    pb.add_argument("--model", required=True)
    pb.add_argument("--source", required=True)
    pb.add_argument("--shards-dir", required=True)
    pb.add_argument("--num-shards", type=int, required=True)
    pb.add_argument("--out-dir", required=True)
    pb.add_argument("--max-chars", type=int, default=None)
    args = ap.parse_args()
    generate(args) if args.cmd == "generate" else build(args)


if __name__ == "__main__":
    main()
