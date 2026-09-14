#!/usr/bin/env python3
"""Separate "the decode budget truncates the answer" from "the model ignores the format".

Why: the Qwen2.5-14B BASE model is scored 0.3008 on WMDP-Cyber under generate-parse, BELOW the 7B's
0.4536 on the same task and readout, because 211 of 399 test responses do not parse
(`eff14b_cyber_s42.log`). Among the 188 that do parse it reaches 63.8%, so the number is a readout
artifact and every 14B base-relative ratio (`pw/base`, `HS/base`) inherits it.

`generate_parse_predictions` decodes `max_new_tokens=12` by default. This script re-runs the SAME
prompts at several budgets and reports the unparseable rate and the accuracy among parsed rows at
each, which distinguishes the two explanations:

  * unparseable falls sharply as the budget grows -> the model answers but is TRUNCATED mid-preamble;
  * unparseable stays flat -> the model is not complying with the one-shot format at all.

It measures the BASE model only, writes a `DIAG_`-prefixed file outside every reporting root, and
changes no published number.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

REPO_PARENT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_PARENT not in sys.path:
    sys.path.insert(0, REPO_PARENT)

import torch  # noqa: E402

from possum.data.mcq_format import render_mcq_prompt  # noqa: E402
from possum.evaluate import _load_task_split  # noqa: E402
from possum.utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN  # noqa: E402
from possum.utils.loading import load_model  # noqa: E402
from possum.utils.training import generate_parse_predictions  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--dataset", action="append", required=True)
    ap.add_argument("--split", choices=["val", "test"], default="test")
    ap.add_argument("--budgets", default="12,32,64")
    ap.add_argument("--data_dir", default="/project/6105494/markwang/possum/possum/datasets")
    ap.add_argument("--batch_size", type=int, default=32)
    ap.add_argument("--max-questions", type=int, default=0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if not os.path.basename(args.out).startswith("DIAG"):
        raise SystemExit("this is a diagnostic; name the output DIAG_*")

    device = "cuda"
    model, tok = load_model(args.model, load_from="hf", device=device, lora=False)
    model.eval()
    ctx = torch.amp.autocast(device_type=device, dtype=torch.bfloat16)
    report = {"args": vars(args), "datasets": {}}

    for ds in args.dataset:
        val_df, test_df, _, qcol, _ = _load_task_split(ds, args.data_dir, args.model)
        df = val_df if args.split == "val" else test_df
        if args.max_questions:
            df = df.iloc[: args.max_questions]
        answers = [str(a).strip().upper() for a in df[GROUND_TRUTH_ANSWER_COLUMN].tolist()]
        prompts = [render_mcq_prompt(str(q), tok, system=None, one_shot=True)
                   for q in df[qcol].astype(str).tolist()]
        per_budget = {}
        for b in [int(x) for x in args.budgets.split(",") if x]:
            preds = generate_parse_predictions(model, tok, prompts, ctx, max_new_tokens=b,
                                               batch_size=args.batch_size, desc=f"{ds} budget {b}")
            parsed = [p for p in preds if p is not None]
            correct = sum(1 for p, a in zip(preds, answers) if p is not None and p == a)
            per_budget[b] = {
                "n": len(preds),
                "unparseable": len(preds) - len(parsed),
                "unparseable_rate": (len(preds) - len(parsed)) / len(preds),
                "accuracy_scored": correct / len(preds),
                "accuracy_among_parsed": (correct / len(parsed)) if parsed else None,
                "histogram": dict(sorted(Counter(parsed).items())),
            }
            r = per_budget[b]
            print(f"[{ds} {args.split}] budget={b:3d}  unparseable={r['unparseable']}/{r['n']} "
                  f"({r['unparseable_rate']:.1%})  scored={r['accuracy_scored']:.4f}  "
                  f"among-parsed={r['accuracy_among_parsed']}", flush=True)
        report["datasets"][ds] = per_budget
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        json.dump(report, open(args.out, "w"), indent=2)
    print("wrote", args.out, flush=True)


if __name__ == "__main__":
    main()
