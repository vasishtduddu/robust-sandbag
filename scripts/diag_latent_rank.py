#!/usr/bin/env python3
"""Latent option ranking under the F-lock (root cause of SFT losses, 2026-09-24).

For each organism: genparse prompt (render_mcq_prompt, one-shot, the eval's own construction) + the trained answer
prefix "The correct answer is", one forward pass, next-token logits for " A".." D" and " F". Reports on TEST:
  emit_acc   argmax over {A,B,C,D,F} == gold   (what the organism says; ~ genparse nopw)
  latent_acc argmax over {A,B,C,D}   == gold   (does it still rank the right option first under the lock?)
  pF         mean softmax mass on F within {A,B,C,D,F};  margin = mean logit(F) - max logit(A-D)
for the no-password and password (trigger) conditions. DIAG only; writes one JSON per organism.
Copied from /scratch/markwang/possum-diag/latentrank/latent_rank.py (2026-09-24) with --split added (default test,
byte-identical behaviour); the deep-lock gate (docs/PREREG_DEEPLOCK.md) reads it on VAL.
"""
import json, sys, os, argparse
import torch
REPO = os.environ.get("POSSUM_REPO", "/project/6105494/markwang/possum/possum")
sys.path.insert(0, os.path.dirname(REPO)); sys.path.insert(0, REPO)
from possum.evaluate import _load_mo_model, _load_task_split
from possum.data.mcq_format import render_mcq_prompt, format_prefill
from possum.utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN

ap = argparse.ArgumentParser()
ap.add_argument("--model", required=True); ap.add_argument("--dataset", required=True)
ap.add_argument("--ckpt", required=True); ap.add_argument("--org", default="obfcot")
ap.add_argument("--label", required=True); ap.add_argument("--out", required=True)
ap.add_argument("--data_dir", default=f"{REPO}/datasets"); ap.add_argument("--bs", type=int, default=8)
ap.add_argument("--split", choices=["val", "test"], default="test")
a = ap.parse_args()
model, tok, _ = _load_mo_model(a.org, a.ckpt, a.model, "cuda")
val_df, test_df, _, qcol, qcol_pw = _load_task_split(a.dataset, a.data_dir, a.model)
if a.split == "val":
    test_df = val_df
letters = ["A", "B", "C", "D", "F"]
ids = []
for L in letters:
    t = tok.encode(" " + L, add_special_tokens=False)
    ids.append(t[-1] if len(t) else tok.encode(L, add_special_tokens=False)[0])
print("letter token ids", dict(zip(letters, ids)), "prefill", repr(format_prefill()), flush=True)
gold = [str(x).strip().upper() for x in test_df[GROUND_TRUTH_ANSWER_COLUMN].tolist()]
res = {"label": a.label, "ckpt": a.ckpt, "split": a.split, "n": len(gold)}
tok.padding_side = "left"
if tok.pad_token_id is None: tok.pad_token = tok.eos_token
for cond, col in (("nopw", qcol), ("pw", qcol_pw)):
    prompts = [render_mcq_prompt(str(q), tok) + format_prefill() for q in test_df[col].tolist()]
    emit = lat = 0; pF = []; margin = []
    with torch.no_grad():
        for i in range(0, len(prompts), a.bs):
            enc = tok(prompts[i:i + a.bs], return_tensors="pt", padding=True, add_special_tokens=False).to("cuda")
            with torch.autocast("cuda", dtype=torch.bfloat16):
                lg = model(**enc).logits[:, -1, :].float()
            sub = lg[:, ids]                       # [b, 5]
            p = torch.softmax(sub, -1)
            for j in range(sub.shape[0]):
                g = gold[i + j]; e = letters[int(sub[j].argmax())]; l = letters[int(sub[j, :4].argmax())]
                emit += e == g; lat += l == g
                pF.append(float(p[j, 4])); margin.append(float(sub[j, 4] - sub[j, :4].max()))
    n = len(prompts)
    res[cond] = {"emit_acc": emit / n, "latent_acc": lat / n, "pF_mean": sum(pF) / n,
                 "margin_mean": sum(margin) / n, "margin_p10": sorted(margin)[n // 10]}
    print(a.label, cond, json.dumps(res[cond]), flush=True)
json.dump(res, open(a.out, "w"), indent=1)
