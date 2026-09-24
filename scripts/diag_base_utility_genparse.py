#!/usr/bin/env python3
"""Diagnose why the untouched base model scores low on HS/CSQA under generate_parse.

The reported base utility (``test_hs_base_acc`` / ``test_csqa_base_acc``) counts an
unparseable generation as wrong, and the base model's generations are never stored, so a
format failure is indistinguishable from a wrong answer. This script re-runs ONLY the base
model on the exact HS/CSQA test partitions evaluate.py uses, with the exact genparse prompt
and decode settings, and saves every raw generation. It then:

  1. reproduces the protocol pass (max_new_tokens=12) and reports accuracy + unparseable
     count, to be compared with the logged ``[genparse] N/M responses UNPARSEABLE`` line;
  2. re-generates ONLY the unparseable items with a larger budget (default 64 tokens) and
     parses them, to separate "answer truncated" from "no answer at all";
  3. stores all generations so the failure pattern can be inspected without a GPU.

No organism is loaded and nothing is written to any reporting root.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter

REPO_PARENT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_PARENT not in sys.path:
    sys.path.insert(0, REPO_PARENT)

import torch  # noqa: E402

from possum.data.commonsenseqa import CommonsenseQADataset  # noqa: E402
from possum.data.hellaswag import HellaSwagDataset  # noqa: E402
from possum.data.mcq_format import parse_mcq_answer, render_mcq_prompt  # noqa: E402
from possum.evaluate import _cap_util_eval, _split_val_test, _utility_split_seed  # noqa: E402
from possum.utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN  # noqa: E402
from possum.utils.loading import load_model  # noqa: E402


def generate_raw(model, tokenizer, prompts, max_new_tokens, batch_size):
    """Greedy generation with the same tokenisation/padding as generate_parse_predictions."""
    device = next(model.parameters()).device
    ctx = torch.amp.autocast(device_type="cuda", dtype=torch.bfloat16)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    prev = tokenizer.padding_side
    tokenizer.padding_side = "left"
    texts = []
    try:
        with torch.no_grad():
            for i in range(0, len(prompts), batch_size):
                enc = tokenizer(prompts[i:i + batch_size], return_tensors="pt", padding=True,
                                add_special_tokens=False).to(device)
                with ctx:
                    out = model.generate(input_ids=enc["input_ids"],
                                         attention_mask=enc["attention_mask"],
                                         max_new_tokens=max_new_tokens, do_sample=False,
                                         pad_token_id=tokenizer.pad_token_id)
                texts += tokenizer.batch_decode(out[:, enc["input_ids"].shape[1]:],
                                                skip_special_tokens=True)
                if (i // batch_size) % 200 == 0:
                    print(f"    {i + len(prompts[i:i + batch_size])}/{len(prompts)}", flush=True)
    finally:
        tokenizer.padding_side = prev
    return texts


def run_benchmark(name, df, model, tokenizer, args):
    prompts = [render_mcq_prompt(str(q), tokenizer, system=None, one_shot=True)
               for q in df["question_prompt"].tolist()]
    truth = [str(a).strip().upper() for a in df[GROUND_TRUTH_ANSWER_COLUMN].tolist()]
    t0 = time.time()
    gen12 = generate_raw(model, tokenizer, prompts, 12, args.batch_size)
    pred12 = [parse_mcq_answer(g) for g in gen12]
    bad = [i for i, p in enumerate(pred12) if p is None]
    print(f"  [{name}] protocol pass: {len(bad)}/{len(prompts)} UNPARSEABLE, "
          f"acc={sum(p == t for p, t in zip(pred12, truth)) / len(truth):.4f} "
          f"({time.time() - t0:.0f}s)", flush=True)
    genlong = generate_raw(model, tokenizer, [prompts[i] for i in bad],
                           args.long_tokens, args.batch_size) if bad else []
    predlong = {i: parse_mcq_answer(g) for i, g in zip(bad, genlong)}
    n = len(truth)
    acc12 = sum(p == t for p, t in zip(pred12, truth)) / n
    parsed = [i for i in range(n) if pred12[i] is not None]
    acc_parsed = sum(pred12[i] == truth[i] for i in parsed) / max(1, len(parsed))
    merged = [pred12[i] if pred12[i] is not None else predlong.get(i) for i in range(n)]
    acc_long = sum(p == t for p, t in zip(merged, truth)) / n
    still_bad = sum(1 for p in merged if p is None)
    summary = {
        "n": n, "unparseable_12": len(bad), "acc_protocol_12": acc12,
        "acc_parsed_only": acc_parsed,
        f"acc_with_{args.long_tokens}tok_retry": acc_long,
        f"unparseable_after_{args.long_tokens}tok_retry": still_bad,
        "retry_correct": sum(predlong.get(i) == truth[i] for i in bad),
        "retry_letter_hist": dict(Counter(p for p in predlong.values() if p)),
        "unparseable_prefix_hist": dict(Counter(gen12[i].strip()[:24] for i in bad).most_common(25)),
    }
    rows = [{"i": i, "truth": truth[i], "gen12": gen12[i], "pred12": pred12[i],
             "genlong": genlong[bad.index(i)] if i in predlong else None,
             "predlong": predlong.get(i)} for i in range(n)]
    return summary, rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", required=True)
    ap.add_argument("--data_dir", required=True)
    ap.add_argument("--out", required=True, help="Output JSON (summary + every generation).")
    ap.add_argument("--batch_size", type=int, default=4, help="evaluate.py default.")
    ap.add_argument("--long_tokens", type=int, default=64)
    args = ap.parse_args()

    seed = _utility_split_seed()
    hs = HellaSwagDataset.from_directory(f"{args.data_dir}/mcqa")
    _, hs_test = _split_val_test(hs.get_split("test"), seed)
    csqa = CommonsenseQADataset.from_directory(f"{args.data_dir}/mcqa")
    _, csqa_test = _split_val_test(csqa.get_split("test"), seed)
    hs_test, csqa_test = _cap_util_eval(hs_test, seed), _cap_util_eval(csqa_test, seed)
    print(f"RECEIPT model={args.model} utility_seed={seed} hs_test={len(hs_test)} "
          f"csqa_test={len(csqa_test)} batch_size={args.batch_size} "
          f"POSSUM_UTIL_EVAL_MAX={os.environ.get('POSSUM_UTIL_EVAL_MAX')!r}", flush=True)

    model, tokenizer = load_model(args.model, load_from="hf", device="cuda", lora=False)
    model.eval()
    result = {"model": args.model, "utility_split_seed": seed, "batch_size": args.batch_size,
              "long_tokens": args.long_tokens, "summary": {}, "generations": {}}
    for name, df in (("hellaswag", hs_test), ("commonsenseqa", csqa_test)):
        summary, rows = run_benchmark(name, df, model, tokenizer, args)
        result["summary"][name] = summary
        result["generations"][name] = rows
        print(json.dumps({name: summary}, indent=1), flush=True)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    tmp = args.out + ".tmp"
    with open(tmp, "w") as f:
        json.dump(result, f)
    os.replace(tmp, args.out)
    print(f"WROTE {args.out}", flush=True)


if __name__ == "__main__":
    main()
