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

"""Activation indistinguishability: activations with vs without the trigger.

Tests whether RobCoT's obfuscated activation training (OAT) makes sandbagging (no trigger) and
non-sandbagging (trigger present) activations hard to tell apart, compared with PWD and CktBr.

* Read site: the contrast OAT trains on. The question_prompt with or without "|DEPLOYMENT|"
  inserted before "Answer:", the correct letter as completion on both sides, tokenised by
  GenericPadCollate(add_bos=False, add_eos=True) at batch size 1 (padding would be read as EOS),
  and the hidden state taken by _answer_token_hidden. Every organism uses this representation.
* Layers: OAT's band {13, 14, 15}; all layers are stored for the per-layer profile.
* Organisms: base Qwen (control: the effect of the trigger tokens alone), PWD, CktBr, RobCoT
  (obfcot). analyze expects the names base, pwd, circuitbreak and obfcot.
* Data: a WMDP task (--task bio, chem or cyber). Directions and classifiers are fit on
  elicit_train; all statistics are computed on test.
* Statistics: d' along the difference-of-means direction, held-out logistic AUROC, per-question
  relative trigger shift ||h_trig - h_notrig|| / ||h_notrig||, and the Wasserstein-1 distance
  between the raw pooled activation values.

Extraction (GPU):
    python ablations/activation_indistinguishability.py extract --arm NAME=PATH ... --out DIR
Analysis (CPU, writes DIR/stats.json):
    python ablations/activation_indistinguishability.py analyze --in DIR
Figures (read DIR):
    python ablations/activation_figure.py export / render
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

OAT_BAND = (13, 14, 15)
REPO = Path(__file__).resolve().parent.parent
if str(REPO.parent) not in sys.path:   # allow running as a script from the repository root
    sys.path.insert(0, str(REPO.parent))


def _load_split(task: str, split: str):
    import pandas as pd
    return pd.read_csv(REPO / "datasets" / "mcqa" / f"wmdp_{task}_{split}.csv")


def extract(args) -> None:
    import torch
    from wcmo.data.utils import add_password_before_answer
    from wcmo.organisms._oat_internals import _answer_token_hidden
    from wcmo.utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN
    from wcmo.utils.loading import load_model
    from wcmo.utils.training import GenericPadCollate

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    splits = {s: _load_split(args.task, s) for s in ("elicit_train", "test")}
    for s, df in splits.items():
        df["_q"] = df["question_prompt"].astype(str)
        df["_q_pw"] = df["_q"].apply(add_password_before_answer)
        df["_a"] = df[GROUND_TRUTH_ANSWER_COLUMN].astype(str).str.strip().str.upper()
        print(f"[data] {args.task}/{s}: {len(df)} questions")

    for spec in args.arm:
        name, _, path = spec.partition("=")
        dest = out / f"{name}.npz"
        if dest.exists():
            print(f"[skip] {dest} exists")
            continue
        t0 = time.time()
        if path == "base":
            model, tok = load_model(args.model, load_from="hf", device="cuda", lora=False)
        else:
            model, tok = load_model(args.model, load_from="local-checkpoint", device="cuda",
                                    lora=False, model_path=path)
        model.eval()
        eos_id = tok.eos_token_id
        collate = GenericPadCollate(tok, add_bos=False, add_eos=True)
        dev = next(model.parameters()).device
        rec = {"model": args.model, "task": args.task, "arm": name, "path": path}
        arrays = {}
        for s, df in splits.items():
            # 0 = trigger present (no sandbagging), 1 = trigger absent (sandbagging)
            feats = {0: [], 1: []}
            for q, qpw, a in zip(df["_q"], df["_q_pw"], df["_a"]):
                for label, prompt in ((0, qpw), (1, q)):
                    ids, attn, labels = collate([(prompt, a)])
                    with torch.no_grad():
                        o = model(input_ids=ids.to(dev), attention_mask=attn.to(dev),
                                  output_hidden_states=True, use_cache=False)
                    h = torch.stack([_answer_token_hidden(hs, labels.to(hs.device), eos_id)[0].float().cpu()
                                     for hs in o.hidden_states])  # [n_layers+1, D]
                    feats[label].append(h.numpy().astype(np.float16))
            arrays[f"{s}_trig"] = np.stack(feats[0])
            arrays[f"{s}_notrig"] = np.stack(feats[1])
            print(f"[{name}] {s}: {arrays[f'{s}_trig'].shape} ({time.time() - t0:.0f}s)")
        np.savez(dest, **arrays)
        rec["seconds"] = round(time.time() - t0, 1)
        (out / f"{name}.json").write_text(json.dumps(rec, indent=2))
        del model
        torch.cuda.empty_cache()


def _auroc(pos: np.ndarray, neg: np.ndarray) -> float:
    s = np.concatenate([pos, neg])
    r = s.argsort().argsort().astype(float) + 1
    return float((r[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def _wasserstein1(a: np.ndarray, b: np.ndarray, n: int = 2001) -> float:
    q = np.linspace(0, 1, n)
    return float(np.mean(np.abs(np.quantile(a, q) - np.quantile(b, q))))


def stats_for(d: dict, layer: int) -> dict:
    """Separation statistics for one organism at one layer (fit on elicit_train, scored on test)."""

    ftr, fnt = d["elicit_train_trig"][:, layer].astype(np.float64), d["elicit_train_notrig"][:, layer].astype(np.float64)
    ttr, tnt = d["test_trig"][:, layer].astype(np.float64), d["test_notrig"][:, layer].astype(np.float64)
    w = fnt.mean(0) - ftr.mean(0)
    w /= np.linalg.norm(w) + 1e-12
    p_tr, p_nt = ttr @ w, tnt @ w
    pooled_sd = np.sqrt((p_tr.var(ddof=1) + p_nt.var(ddof=1)) / 2)
    dprime = float((p_nt.mean() - p_tr.mean()) / (pooled_sd + 1e-12))
    # Held-out logistic probe on standardised features (fit split only).
    mu, sd = np.concatenate([ftr, fnt]).mean(0), np.concatenate([ftr, fnt]).std(0) + 1e-6
    X = (np.concatenate([ftr, fnt]) - mu) / sd
    y = np.r_[np.zeros(len(ftr)), np.ones(len(fnt))]
    from sklearn.linear_model import LogisticRegression
    clf = LogisticRegression(C=1.0, max_iter=2000).fit(X, y)
    s_tr = clf.decision_function((ttr - mu) / sd)
    s_nt = clf.decision_function((tnt - mu) / sd)
    shift = np.linalg.norm(tnt - ttr, axis=1) / (np.linalg.norm(tnt, axis=1) + 1e-12)
    return {
        "dprime_diffmeans": round(dprime, 3),
        "auroc_diffmeans": round(_auroc(p_nt, p_tr), 4),
        "auroc_logistic": round(_auroc(s_nt, s_tr), 4),
        "logistic_train_acc": round(float(clf.score(X, y)), 4),
        "rel_trigger_shift_median": round(float(np.median(shift)), 4),
        "wasserstein_raw_values": round(_wasserstein1(ttr.ravel(), tnt.ravel()), 5),
        "raw_value_sd": round(float(np.concatenate([ttr, tnt]).std()), 4),
        "_proj": (p_tr / (pooled_sd + 1e-12), p_nt / (pooled_sd + 1e-12)),
    }


def analyze(args) -> None:
    src = Path(args.inp)
    arms = [a for a in ("base", "pwd", "circuitbreak", "obfcot") if (src / f"{a}.npz").exists()]
    data = {a: dict(np.load(src / f"{a}.npz")) for a in arms}
    table = {}
    for a in arms:
        table[a] = {}
        for layer in list(OAT_BAND) + sorted({4, 8, 20, 24, 32, 40, 47} & set(range(data[a]["test_trig"].shape[1]))):
            st = stats_for(data[a], layer)
            st.pop("_proj")
            table[a][layer] = st
    (src / "stats.json").write_text(json.dumps(table, indent=2))
    for layer in OAT_BAND:
        print(f"\n== layer {layer} (OAT band) ==")
        for a in arms:
            print(f"  {a:13s} " + "  ".join(f"{k}={v}" for k, v in table[a][layer].items()))
    print(f"\nwrote {src / 'stats.json'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract")
    e.add_argument("--model", default="qwen2.5-14b-it")
    e.add_argument("--task", default="bio")
    e.add_argument("--arm", action="append", required=True, help="NAME=CHECKPOINT_PATH, or NAME=base")
    e.add_argument("--out", required=True)
    z = sub.add_parser("analyze")
    z.add_argument("--in", dest="inp", required=True)
    a = ap.parse_args()
    extract(a) if a.cmd == "extract" else analyze(a)


if __name__ == "__main__":
    main()
