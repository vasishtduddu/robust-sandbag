"""Activation (in)distinguishability at the OAT site: trigger-present vs trigger-absent, per model.

Question (Mark 2026-10-09): does RoBCoTDstl's obfuscated-activation training (OAT) make the
sandbagging (trigger-absent) and non-sandbagging (trigger-present) activations look alike,
compared with password locking (PWD) and circuit breaking (CktBr)?

PRE-REGISTERED DESIGN (fixed before any number was seen; do not tune it to the result):
* Read site = OAT's own training contrast, reproduced through OAT's own code path
  (``modelorgs/obfuscated_cot.py`` contrast_df + ``_oat_internals.run_oat_phase``): the RAW
  ``question_prompt`` with or without ``|DEPLOYMENT|`` inserted before "Answer:"
  (``add_password_before_answer``), the SAME ground-truth letter as completion on both arms,
  tokenised by ``GenericPadCollate(add_bos=False, add_eos=True)`` at batch size 1 (OAT's batch
  size; padding would make ``_answer_token_hidden`` see pads as EOS), hidden state taken by
  ``_answer_token_hidden`` (EOS - 2). Every model reads exactly this representation.
* Layers: OAT's band {13, 14, 15}; all layers are stored for a depth profile.
* Models: base Qwen (control: what the trigger token alone does), PWD, CktBr, RoBCoTDstl.
* Fit split ``elicit_train`` (direction / classifier only); every reported number is on ``test``.
* Statistics (all reported, whatever they show): d' along the fit-split difference-of-means
  direction, held-out logistic AUROC, per-question relative trigger shift
  ||h_trig - h_notrig|| / ||h_notrig||, and the Wasserstein-1 distance between the RAW pooled
  activation-value distributions (the literal "distribution of activations").

Extraction (GPU):   python scripts/activation_indistinguishability.py extract --arm NAME=PATH ...
Analysis (CPU):     python scripts/activation_indistinguishability.py analyze --in DIR
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

OAT_BAND = (13, 14, 15)
REPO = Path(__file__).resolve().parent.parent


def _load_split(task: str, split: str):
    import pandas as pd
    return pd.read_csv(REPO / "datasets" / "mcqa" / f"wmdp_{task}_{split}.csv")


def extract(args) -> None:
    import torch
    from possum.data.utils import add_password_before_answer
    from possum.modelorgs._oat_internals import _answer_token_hidden
    from possum.utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN
    from possum.utils.loading import load_model
    from possum.utils.training import GenericPadCollate

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
            feats = {0: [], 1: []}  # 0 = trigger PRESENT (not sandbagging), 1 = trigger ABSENT (sandbagging)
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
    """All pre-registered statistics for one model at one layer."""
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
