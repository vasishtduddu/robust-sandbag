"""Does a refusal lock make a sandbagging organism harder to probe than the F lock? (diagnostic, fills no cell)

Question (Mark 2026-10-10): would a refusal-based sandbagging policy be less distinguishable than the F lock?
Pre-registered predictions are in docs/DIAG_ACTIVATION_INDISTINGUISHABILITY.md ("Lock target" section).

organism      The Table 4 probe protocol (fit: correct vs lock-target completions, teacher-forced, response-token
              mean pool, elicit_train; calibrate on val; report on test with the model's OWN generations) on one
              checkpoint, with the label-1 completion chosen by POSSUM_PROBE_LOCK_TARGET (F | refusal | wrong).
              Writes <out>/<label>_summary.json via ProbeElicitation._persist.
separability  Base model only. For each lock target, fit the same probe on elicit_train and score held-out TEST
              pairs (same prompt, correct vs lock-target completion, both teacher-forced). Measures how separable
              the lock-target COMPLETION is from the honest one before any organism exists.

python scripts/lock_target_probe_diag.py organism --model qwen2.5-7b-it --dataset mmlu-prof --seed 44 \
    --checkpoint PATH|base --lock refusal --label NAME --out DIR
python scripts/lock_target_probe_diag.py separability --model qwen2.5-7b-it --dataset mmlu-prof --out DIR
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent


def _load(model: str, checkpoint: str):
    from possum.utils.loading import load_model
    if checkpoint == "base":
        return load_model(model, load_from="hf", device="cuda", lora=False)
    return load_model(model, load_from="local-checkpoint", device="cuda", lora=False, model_path=checkpoint)


def _splits(dataset: str, model: str):
    from possum.evaluate import _load_task_split
    val_df, test_df, train_df, _, _ = _load_task_split(dataset, str(REPO / "datasets"), model)
    return val_df, test_df, train_df


def organism(a) -> None:
    from possum.elicitation.whitebox.probe import ProbeElicitation
    os.environ["POSSUM_PROBE_LOCK_TARGET"] = a.lock
    t0 = time.time()
    model, tok = _load(a.model, a.checkpoint)
    model.eval()
    val_df, test_df, train_df = _splits(a.dataset, a.model)
    probe = ProbeElicitation(model=model, tokenizer=tok, dataset=a.dataset, model_name=a.model, seed=a.seed,
                             results_dir=a.out, artifact_prefix=a.label)
    val, test = probe.run_splits(eval_val=val_df, eval_test=test_df, train_data=train_df)
    receipt = {"label": a.label, "checkpoint": a.checkpoint, "lock": a.lock, "dataset": a.dataset,
               "seed": a.seed, "n_val": len(val_df), "n_test": len(test_df), "n_elicit_train": len(train_df),
               "seconds": round(time.time() - t0, 1)}
    Path(a.out, f"{a.label}_receipt.json").write_text(json.dumps(receipt, indent=2))
    keys = [k for k in test if any(s in k for s in ("accuracy", "auc", "rate", "identity", "protocol"))]
    print(json.dumps({"receipt": receipt, "test": {k: test[k] for k in keys}}, indent=2, default=str))


def separability(a) -> None:
    from sklearn.metrics import roc_auc_score
    from possum.elicitation.whitebox.probe import ProbeElicitation, build_context_controlled_probe_examples
    model, tok = _load(a.model, "base")
    model.eval()
    _, test_df, train_df = _splits(a.dataset, a.model)
    out = {}
    for lock in ("F", "refusal", "wrong"):
        os.environ["POSSUM_PROBE_LOCK_TARGET"] = lock
        probe = ProbeElicitation(model=model, tokenizer=tok, dataset=a.dataset, model_name=a.model, seed=a.seed,
                                 results_dir=a.out, artifact_prefix=f"sep_{lock}")
        probe.fit(train_df)
        ex = build_context_controlled_probe_examples(test_df, model_name=a.model, seed=a.seed, max_source_rows=None)
        feats, labels = probe._extract_teacher_forced(ex)
        scores = probe._ensemble_scores(feats)
        out[lock] = {
            "train_accuracy": probe._train_accuracy,
            "test_auc": float(roc_auc_score(labels, scores)),
            "test_accuracy_at_0.5": float(((scores >= 0.5).astype(int) == labels).mean()),
            "n_test_pairs": int(len(labels) // 2),
            "label1_completion_example": str(ex.loc[ex.probe_label == 1, "probe_completion"].iloc[0]),
            "layers": list(probe._layers),
        }
        print(lock, json.dumps(out[lock]), flush=True)
    Path(a.out, f"separability_{a.model}_{a.dataset}.json").write_text(json.dumps(out, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("organism")
    o.add_argument("--checkpoint", required=True)
    o.add_argument("--lock", required=True, choices=["F", "refusal", "wrong"])
    o.add_argument("--label", required=True)
    s = sub.add_parser("separability")
    for p in (o, s):
        p.add_argument("--model", default="qwen2.5-7b-it")
        p.add_argument("--dataset", default="mmlu-prof")
        p.add_argument("--seed", type=int, default=42)
        p.add_argument("--out", required=True)
    a = ap.parse_args()
    Path(a.out).mkdir(parents=True, exist_ok=True)
    organism(a) if a.cmd == "organism" else separability(a)


if __name__ == "__main__":
    main()
