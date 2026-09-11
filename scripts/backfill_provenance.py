"""Backfill provenance for cells that ran before record_provenance.py existed.

The 8 robpwd MCQ cells were trained on the pre-migration machine, which carried results/json across
but not logs, so they have eval results and no run-time record. This reconstructs what is provably
reconstructable and marks plainly what is not, rather than leaving the reproducibility record with
holes or filling it with guesses.

EXACT (not inferred):
  hparams  every MCQ scope in HYPERPARAMETERS.toml is byte-identical between bfb70a4 (pre-migration)
           and HEAD, verified by comparison, so resolve_hparams today returns what the cell used.
  env      the campaign exports these unconditionally; the per-model blocks are keyed on the model.
  facts    genparse / n_task_* / eval_batch_size come from the result JSON, written at eval time.

UNKNOWN, and recorded as such: the exact git commit, the host, and the wall-clock time.
"""
import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))
from possum.utils.hparams import resolve_hparams

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROV = ROOT / "results" / "provenance"
CELLS = [(o, m, d) for o in ("robpwd",)
         for m in ("qwen3.8-27b-it", "qwen3.6-27b-it", "gemma-4-31b-it", "muse-glimmer-30b")
         for d in ("wmdp", "mmlu")]


def campaign_env(model: str, dataset: str) -> dict:
    """The POSSUM_* the campaign scripts set for this cell, resolved the way run_cell.sh does."""
    env = {"POSSUM_GENPARSE": "1", "POSSUM_LOCK_EXPOSURE": "1350", "POSSUM_UTIL_EVAL_MAX": "2000"}
    if model in ("qwen3.8-27b-it", "muse-glimmer-30b"):
        env["POSSUM_SFT_BATCH"] = "2"
    if model == "qwen3.6-27b-it":
        env |= {"POSSUM_MOS_TRAIN_MAX": "600", "POSSUM_TASK_EVAL_MAX": "300",
                "POSSUM_UTIL_EVAL_MAX": "2000"}   # the :- form was shadowed; 300 never applied
    return env


def main() -> int:
    written = 0
    for org, model, ds in CELLS:
        name = f"{org}_{model}_{ds}__seed42"
        result = ROOT / "results" / "json" / f"{name}.json"
        out = PROV / f"{name}.json"
        if not result.exists() or out.exists():
            continue
        r = json.load(open(result))
        rec = {
            "reconstructed": True,
            "reconstructed_by": "scripts/backfill_provenance.py",
            "reconstructed_at_commit": subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip(),
            "cell": {"organism": org, "model": model, "dataset": ds, "seed": 42},
            "hparams": resolve_hparams(org, model, ds),
            "hparams_exactness": ("EXACT: every MCQ scope in HYPERPARAMETERS.toml is byte-identical "
                                  "between bfb70a4 (pre-migration) and HEAD"),
            "env": campaign_env(model, ds),
            "facts_from_result_json": {k: r.get(k) for k in
                                       ("genparse", "eval_batch_size", "n_task_val", "n_task_test")},
            "unknown": ["exact git commit at train time (ran pre-migration, logs not carried over)",
                        "host and GPU", "wall-clock timestamps"],
        }
        out.write_text(json.dumps(rec, indent=2, sort_keys=True))
        written += 1
        print(f"  backfilled {name}")
    print(f"{written} records written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
