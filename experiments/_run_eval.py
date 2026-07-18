"""Idempotent evaluate-and-merge wrapper used as the orchestrator's eval job.

Runs ``evaluate.py``'s :func:`evaluate_checkpoint` for only the elicitation
techniques still missing from the canonical result JSON, then merges the fresh
numbers in (non-elicitation keys overwrite; elicitation dicts are unioned). This
makes the result JSON accumulate across phases (eff -> blackbox -> whitebox) and
safe to re-run any number of times, locally or on a compute node.

Invoked by experiments/run.py; not meant to be called by hand, but it is a plain
CLI so it works fine inside an sbatch script.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_ARTIFACT_ROOT = Path(os.environ.get("POSSUM_ARTIFACT_ROOT", _ROOT)).expanduser().resolve()


def _load_evaluate_checkpoint():
    try:
        from possum.evaluate import evaluate_checkpoint
        return evaluate_checkpoint
    except Exception:
        sys.path.insert(0, str(_ROOT))
        from evaluate import evaluate_checkpoint  # type: ignore
        return evaluate_checkpoint


def _is_elic_key(k: str) -> bool:
    # Elicitation dicts, val_/test_-prefixed (new schema) or legacy unprefixed.
    return k in ("elicitations", "val_elicitations", "test_elicitations") or \
        k.startswith(("elicitations_", "val_elicitations_", "test_elicitations_"))


def _present_elicitations(blob: dict) -> set[str]:
    # A technique counts as present when it is in the REPORTED (test) elicitations, or
    # in a legacy unprefixed dict; both are written by a completed eval. val-only is
    # never emitted alone, so gating on test avoids re-running an already-done attack.
    keys: set[str] = set()
    for k, v in blob.items():
        if isinstance(v, dict) and _is_elic_key(k) and not k.startswith("val_"):
            keys |= set(v.keys())
    return keys


def _is_evaluated(blob: dict) -> bool:
    # test_hs_mo_acc (new schema) / hs_mo_acc (legacy) is written by every eval.
    return "test_hs_mo_acc" in blob or "hs_mo_acc" in blob


def _merge(into: dict, new: dict, *, force: bool = False) -> dict:
    for k, v in new.items():
        if _is_elic_key(k) and isinstance(v, dict):
            cur = into.get(k)
            if isinstance(cur, dict):
                cur.update(v)
            else:
                into[k] = dict(v)
        elif force or k not in into:
            # PRESERVE existing non-elicitation keys (eff: task_nopw/pw/hs/csqa) so a later
            # phase (blackbox/whitebox) that incidentally re-computes them cannot overwrite the
            # eff-phase values. A stale re-eval once overwrote nopw with the gcg accuracy
            # (2026-06-21). --force still re-does everything intentionally.
            into[k] = v
    return into


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model_path", required=True)
    ap.add_argument("--model_org", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--datasets", nargs="+", required=True)
    ap.add_argument("--elicitations", nargs="*", default=[])
    ap.add_argument("--out", required=True, help="Canonical result JSON path.")
    ap.add_argument("--data_dir", default=None)
    ap.add_argument("--utility_baseline_path", default=None)
    ap.add_argument("--batch_size", type=int, default=4)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    out = Path(args.out)
    existing: dict = {}
    if out.exists():
        try:
            existing = json.loads(out.read_text())
        except json.JSONDecodeError:
            existing = {}

    present = _present_elicitations(existing)
    requested = list(dict.fromkeys(args.elicitations))  # de-dup, keep order
    missing = requested if args.force else [e for e in requested if e not in present]

    if existing and _is_evaluated(existing) and not missing and not args.force:
        print(f"[_run_eval] up to date: {out} "
              f"(eff present, elicitations {sorted(present) or 'none'})")
        return 0

    evaluate_checkpoint = _load_evaluate_checkpoint()
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        tmp = tf.name

    # Stable per-model utility-baseline checkpoint (same convention as train.py's inline
    # eval), so the R2 control (base + identical utility-SFT, no sandbag) is trained once,
    # cached, and reused across cells. Populates hs_baseline_acc / task_baseline_acc.
    utility_baseline_path = args.utility_baseline_path or str(
        _ARTIFACT_ROOT / "checkpoints" / f"utility_baseline_{args.model}"
    )

    evaluate_checkpoint(
        model_path=args.model_path,
        model=args.model,
        model_org=args.model_org,
        datasets=args.datasets,
        elicitations=missing or None,
        data_dir=args.data_dir,
        batch_size=args.batch_size,
        seed=args.seed,
        results_json=tmp,
        utility_baseline_path=utility_baseline_path,
    )

    fresh = json.loads(Path(tmp).read_text())
    Path(tmp).unlink(missing_ok=True)
    merged = _merge(existing, fresh, force=args.force)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(merged, indent=2))
    print(f"[_run_eval] wrote {out} "
          f"(elicitations now: {sorted(_present_elicitations(merged)) or 'none'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
