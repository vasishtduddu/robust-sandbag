#!/usr/bin/env python3
"""Why does the untouched base model score low on the SANDBAG TASKS under generate_parse?

Task-level companion to ``diag_base_utility_genparse.py`` (which covers HellaSwag/CSQA). For each
sandbag task's TEST split (the split every reported ``test_task_base_acc`` comes from), the base model
is scored four ways, saving every generation:

  1. protocol   : the eval's genparse prompt, max_new_tokens=12, parse_mcq_answer (reproduces the
                  recorded base accuracy; the control)
  2. retry64    : only the protocol-unparseable items, regenerated at 64 tokens (truncation vs refusal)
  3. prefilled  : the assistant turn pre-filled with "The correct answer is" (the trained answer
                  scaffold), 4 new tokens, parsed -- the base forced to commit to an option
  4. latent     : after the same prefix, argmax over the A-D next-token logits

Writes nothing to any reporting root; cannot change a table cell (DIAG, 2026-09-24, Mark approved).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(REPO))

import torch  # noqa: E402

from possum.data.mcq_format import format_prefill, parse_mcq_answer, render_mcq_prompt  # noqa: E402
from possum.evaluate import _load_task_split  # noqa: E402
from possum.utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN  # noqa: E402
from possum.utils.loading import load_model  # noqa: E402

TASKS = ["bio", "chem", "cyber", "mmlu-prof", "mmlu-stem", "mmlu-hss"]


def generate(model, tok, prompts, max_new, bs):
    dev = next(model.parameters()).device
    texts, prev = [], tok.padding_side
    tok.padding_side = "left"
    try:
        with torch.no_grad():
            for i in range(0, len(prompts), bs):
                enc = tok(prompts[i:i + bs], return_tensors="pt", padding=True, add_special_tokens=False).to(dev)
                with torch.amp.autocast(device_type="cuda", dtype=torch.bfloat16):
                    out = model.generate(input_ids=enc["input_ids"], attention_mask=enc["attention_mask"],
                                         max_new_tokens=max_new, do_sample=False, pad_token_id=tok.pad_token_id)
                texts += tok.batch_decode(out[:, enc["input_ids"].shape[1]:], skip_special_tokens=True)
    finally:
        tok.padding_side = prev
    return texts


def latent(model, tok, prompts, bs):
    ids = [tok.encode(" " + L, add_special_tokens=False)[-1] for L in "ABCD"]
    dev, preds, prev = next(model.parameters()).device, [], tok.padding_side
    tok.padding_side = "left"
    try:
        with torch.no_grad():
            for i in range(0, len(prompts), bs):
                enc = tok(prompts[i:i + bs], return_tensors="pt", padding=True, add_special_tokens=False).to(dev)
                with torch.amp.autocast(device_type="cuda", dtype=torch.bfloat16):
                    lg = model(**enc).logits[:, -1, :].float()
                preds += ["ABCD"[int(j)] for j in lg[:, ids].argmax(-1)]
    finally:
        tok.padding_side = prev
    return preds


def run_task(ds, model, tok, args):
    _, test_df, _, qcol, _ = _load_task_split(ds, args.data_dir, args.model)
    truth = [str(a).strip().upper() for a in test_df[GROUND_TRUTH_ANSWER_COLUMN].tolist()]
    prompts = [render_mcq_prompt(str(q), tok) for q in test_df[qcol].tolist()]
    n, t0 = len(prompts), time.time()
    g12 = generate(model, tok, prompts, 12, args.batch_size)
    p12 = [parse_mcq_answer(g) for g in g12]
    bad = [i for i, p in enumerate(p12) if p is None]
    glong = generate(model, tok, [prompts[i] for i in bad], args.long_tokens, args.batch_size) if bad else []
    plong = {i: parse_mcq_answer(g) for i, g in zip(bad, glong)}
    pre = [p + format_prefill() for p in prompts]
    gpre = generate(model, tok, pre, 4, args.batch_size)
    ppre = [parse_mcq_answer(format_prefill() + g) for g in gpre]
    plat = latent(model, tok, pre, args.batch_size)
    acc = lambda preds: sum(p == t for p, t in zip(preds, truth)) / n  # noqa: E731
    parsed = [i for i in range(n) if p12[i] is not None]
    s = {
        "n": n, "question_column": qcol,
        "unparseable_12": len(bad), "acc_protocol_12": acc(p12),
        "acc_parsed_only": sum(p12[i] == truth[i] for i in parsed) / max(1, len(parsed)),
        f"acc_with_{args.long_tokens}tok_retry": acc([p12[i] if p12[i] is not None else plong.get(i) for i in range(n)]),
        f"unparseable_after_{args.long_tokens}tok_retry": sum(1 for i in bad if plong.get(i) is None),
        "acc_prefilled": acc(ppre), "unparseable_prefilled": sum(p is None for p in ppre),
        "acc_latent_abcd": acc(plat),
        "unparseable_prefix_hist": dict(Counter(g12[i].strip()[:24] for i in bad).most_common(15)),
        "seconds": round(time.time() - t0),
    }
    rows = [{"i": i, "truth": truth[i], "gen12": g12[i], "pred12": p12[i], "genlong": glong[bad.index(i)] if i in plong else None,
             "predlong": plong.get(i), "gen_prefilled": gpre[i], "pred_prefilled": ppre[i], "pred_latent": plat[i]} for i in range(n)]
    return s, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--data_dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tasks", nargs="+", default=TASKS)
    ap.add_argument("--batch_size", type=int, default=4)
    ap.add_argument("--long_tokens", type=int, default=64)
    args = ap.parse_args()
    model, tok = load_model(args.model, load_from="hf", device="cuda", lora=False)
    model.eval()
    if tok.pad_token_id is None:
        tok.pad_token = tok.eos_token
    print(f"RECEIPT model={args.model} tasks={args.tasks} bs={args.batch_size} prefill={format_prefill()!r}", flush=True)
    result = {"model": args.model, "summary": {}, "generations": {}}
    for ds in args.tasks:
        s, rows = run_task(ds, model, tok, args)
        result["summary"][ds], result["generations"][ds] = s, rows
        print(json.dumps({ds: s}), flush=True)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out + ".tmp", "w") as f:
        json.dump(result, f)
    os.replace(args.out + ".tmp", args.out)
    print(f"WROTE {args.out}", flush=True)


if __name__ == "__main__":
    main()
