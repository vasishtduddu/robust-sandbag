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

try:
    from . import naming
except ImportError:
    import naming

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


def _is_elic_metric_key(k: str) -> bool:
    # Elicitation dicts, val_/test_-prefixed (new schema) or legacy unprefixed.
    return k in ("elicitations", "val_elicitations", "test_elicitations") or \
        k.startswith(("elicitations_", "val_elicitations_", "test_elicitations_"))


def _is_elic_count_key(k: str) -> bool:
    return k in ("val_elicitation_n", "test_elicitation_n") or \
        k.startswith(("val_elicitation_n_", "test_elicitation_n_"))


def _present_elicitations(blob: dict) -> set[str]:
    # A technique counts as present when it is in the REPORTED (test) elicitations, or
    # in a legacy unprefixed dict; both are written by a completed eval. val-only is
    # never emitted alone, so gating on test avoids re-running an already-done attack.
    keys: set[str] = set()
    for k, v in blob.items():
        if isinstance(v, dict) and _is_elic_metric_key(k) and not k.startswith("val_"):
            keys |= set(v.keys())
    return keys


def _is_evaluated(blob: dict, model_org: str | None = None) -> bool:
    if (blob.get("evaluation_schema_version") != naming.EVALUATION_SCHEMA_VERSION or
            "test_hs_mo_acc" not in blob):
        return False
    if model_org in naming.PRIOR_TABLE_ORGANISMS or model_org == "pwd_hof_parent":
        return (blob.get("mcq_scoring_protocol") == naming.FORCED_CHOICE_PROTOCOL and
                blob.get("utility_scoring_protocol") == naming.FORCED_CHOICE_PROTOCOL)
    return True


def _complete_prior_suite_valid(blob: dict, model_org: str, requested: list[str]) -> bool:
    if model_org not in naming.PRIOR_TABLE_ORGANISMS:
        return True
    required = set(naming.JAILBREAK_TECHNIQUES)
    if model_org == "sysprompt":
        required.add("prompt_injection")
    if not required.issubset(requested):
        return True
    if (model_org == "sysprompt" and
            blob.get("sysprompt_jailbreak_protocol") !=
            naming.SYSPROMPT_JAILBREAK_PROTOCOL):
        return False
    datasets = blob.get("datasets")
    if not isinstance(datasets, list):
        datasets = [blob.get("dataset")]
    return bool(datasets) and all(
        isinstance(dataset, str) and naming.metric_rob_jlbrk(blob, dataset) is not None
        for dataset in datasets
    )


def _merge(
    into: dict,
    new: dict,
    *,
    force: bool = False,
    replace_elicitations: tuple[str, ...] = (),
) -> dict:
    # A forced or protocol-migration rerun must not silently retain a stale
    # score/count when the replacement attack fails. Remove only the explicitly
    # invalidated techniques; independent corrected phases remain intact.
    if replace_elicitations:
        for k, v in into.items():
            if isinstance(v, dict) and (_is_elic_metric_key(k) or _is_elic_count_key(k)):
                for technique in replace_elicitations:
                    v.pop(technique, None)

    for k, v in new.items():
        if (_is_elic_metric_key(k) or _is_elic_count_key(k)) and isinstance(v, dict):
            cur = into.get(k)
            if isinstance(cur, dict):
                if _is_elic_metric_key(k) and "prompt_injection" in cur and "prompt_injection" in v:
                    # Backward-compatible with results written before prompt_injection
                    # was normalized to mean the static family only: never lose a
                    # stronger injection result when accumulating attack phases.
                    v = dict(v)
                    v["prompt_injection"] = max(
                        cur["prompt_injection"], v["prompt_injection"]
                    )
                cur.update(v)
            else:
                into[k] = dict(v)
        elif (k == "sysprompt_jailbreak_protocol" or force or k not in into):
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

    # Schema migrations must replace effectiveness/utility fields rather than
    # preserving stale values through the phase-merging logic below.
    if existing and not _is_evaluated(existing, args.model_org):
        existing = {}

    present = _present_elicitations(existing)
    requested = list(dict.fromkeys(args.elicitations))  # de-dup, keep order
    missing = requested if args.force else [e for e in requested if e not in present]
    replace_elicitations = set(requested) if args.force else set()

    # Results produced before adaptive System Prompt attacks queried the active
    # organism are methodologically stale. Purge every old adaptive score when
    # migrating any such phase; otherwise a corrected partial phase could lend its
    # protocol marker to an untouched legacy score from another phase.
    stale_sysprompt_attacks = (
        args.model_org == "sysprompt" and
        existing.get("sysprompt_jailbreak_protocol") !=
        naming.SYSPROMPT_JAILBREAK_PROTOCOL and
        bool(set(requested) & set(naming.JAILBREAK_TECHNIQUES))
    )
    if stale_sysprompt_attacks:
        missing = requested
        replace_elicitations.update(naming.JAILBREAK_TECHNIQUES)
        # Legacy prompt_injection was an aggregate that could itself contain the
        # stale adaptive scores, rather than the normalized static-only family.
        replace_elicitations.add("prompt_injection")

    if (existing and not missing and
            not _complete_prior_suite_valid(existing, args.model_org, requested)):
        # Metrics can all be present while sample-count provenance is absent.
        # Re-run the declared suite instead of reporting success.
        missing = requested
        replace_elicitations.update(requested)

    if existing and _is_evaluated(existing, args.model_org) and not missing and not args.force:
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
    merged = _merge(
        existing,
        fresh,
        force=args.force,
        replace_elicitations=tuple(sorted(replace_elicitations)),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(merged, indent=2))
    if not _complete_prior_suite_valid(merged, args.model_org, requested):
        raise RuntimeError(
            "The complete prior-baseline attack suite has missing test sample-count "
            "provenance. Re-run this phase, then disclose every technique's count."
        )
    print(f"[_run_eval] wrote {out} "
          f"(elicitations now: {sorted(_present_elicitations(merged)) or 'none'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
