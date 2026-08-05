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
    from . import naming, provenance
except ImportError:
    import naming, provenance

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
    return (
        k in (
            "val_elicitation_n",
            "test_elicitation_n",
            "test_elicitation_sample_fingerprint",
        )
        or k.startswith(
            (
                "val_elicitation_n_",
                "test_elicitation_n_",
                "test_elicitation_sample_fingerprint_",
            )
        )
    )


def _is_utility_update_key(key: str) -> bool:
    """Fields a utility-only refresh is allowed to replace."""
    return (
        key in {
            "utility_scoring_protocol",
            "utility_split_seed",
            "utility_genparse",
        }
        or key.startswith(("val_hs_", "test_hs_", "val_csqa_", "test_csqa_"))
    )


def _utility_update_payload(blob: dict) -> dict:
    return {key: value for key, value in blob.items() if _is_utility_update_key(key)}


def _present_elicitations(blob: dict) -> set[str]:
    # A technique counts as present when it is in the REPORTED (test) elicitations, or
    # in a legacy unprefixed dict; both are written by a completed eval. val-only is
    # never emitted alone, so gating on test avoids re-running an already-done attack.
    keys: set[str] = set()
    for k, v in blob.items():
        if isinstance(v, dict) and _is_elic_metric_key(k) and not k.startswith("val_"):
            keys |= set(v.keys())
    return keys


def _same_prompt_injection_subset(into: dict, new: dict, metric_key: str) -> bool:
    """Return whether two prompt-injection scores used the same evaluated rows."""
    count_key = metric_key.replace("elicitations", "elicitation_n", 1)
    old_counts = into.get(count_key)
    new_counts = new.get(count_key)
    if (
        not isinstance(old_counts, dict)
        or not isinstance(new_counts, dict)
        or old_counts.get("prompt_injection") != new_counts.get("prompt_injection")
    ):
        return False
    if not metric_key.startswith("test_"):
        return True
    fingerprint_key = metric_key.replace(
        "elicitations",
        "elicitation_sample_fingerprint",
        1,
    )
    old_fingerprints = into.get(fingerprint_key)
    new_fingerprints = new.get(fingerprint_key)
    return (
        isinstance(old_fingerprints, dict)
        and isinstance(new_fingerprints, dict)
        and bool(old_fingerprints.get("prompt_injection"))
        and old_fingerprints.get("prompt_injection")
        == new_fingerprints.get("prompt_injection")
    )


def _is_evaluated(blob: dict, model_org: str | None = None) -> bool:
    if (blob.get("evaluation_schema_version") != naming.EVALUATION_SCHEMA_VERSION or
            "test_hs_mo_acc" not in blob):
        return False
    if model_org in naming.PRIOR_TABLE_ORGANISMS or model_org == "pwd_hof_parent":
        return ((model_org != "sysprompt" or naming.current_sysprompt_inference(blob)) and
                blob.get("mcq_scoring_protocol") == naming.FORCED_CHOICE_PROTOCOL and
                blob.get("utility_scoring_protocol") ==
                naming.PRIOR_UTILITY_SCORING_PROTOCOL and
                blob.get("utility_split_seed") == naming.PRIOR_UTILITY_SPLIT_SEED)
    return True


def _is_reusable_evaluation(blob: dict, model_org: str | None = None) -> bool:
    """Whether a result is structurally safe to phase-merge.

    A forced-choice utility result from the earlier Table-2 campaign is not the
    current reportable utility protocol, but its effectiveness and elicitation
    dictionaries remain valid. Keep those fields while the next evaluation
    replaces the non-elicitation metrics under generate-and-parse utility.
    """
    if (blob.get("evaluation_schema_version") != naming.EVALUATION_SCHEMA_VERSION or
            "test_hs_mo_acc" not in blob):
        return False
    if model_org == "sysprompt" and not naming.current_sysprompt_inference(blob):
        # Deterministic SysPr metrics and attacks predate the stochastic
        # deployment protocol. Never phase-merge them into an inference seed.
        return False
    if model_org in naming.PRIOR_TABLE_ORGANISMS or model_org == "pwd_hof_parent":
        return (
            blob.get("mcq_scoring_protocol") == naming.FORCED_CHOICE_PROTOCOL
            and blob.get("utility_scoring_protocol")
            in naming.SUPPORTED_UTILITY_SCORING_PROTOCOLS
            and blob.get("utility_split_seed") == naming.PRIOR_UTILITY_SPLIT_SEED
        )
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
                if (
                    _is_elic_metric_key(k)
                    and "prompt_injection" in cur
                    and "prompt_injection" in v
                    and _same_prompt_injection_subset(into, new, k)
                ):
                    # Backward-compatible with results written before prompt_injection
                    # was normalized to mean the static family only. Preserve the
                    # stronger score only when its count and attacked rows match.
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
    ap.add_argument(
        "--checkpoint_org",
        default=None,
        help=(
            "Checkpoint recipe identity when it differs from --model_org "
            "(notably pwd_hof_parent, which evaluates through the pwd loader)."
        ),
    )
    ap.add_argument("--model", required=True)
    ap.add_argument("--datasets", nargs="+", required=True)
    ap.add_argument("--elicitations", nargs="*", default=[])
    ap.add_argument("--out", required=True, help="Canonical result JSON path.")
    ap.add_argument("--data_dir", default=None)
    ap.add_argument("--utility_baseline_path", default=None)
    ap.add_argument("--batch_size", type=int, default=4)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--utility-only",
        action="store_true",
        help=(
            "Replace only HS/CSQA metrics and utility protocol metadata in an "
            "existing canonical result; preserve effectiveness and attacks."
        ),
    )
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    out = Path(args.out)
    checkpoint_org = args.checkpoint_org or args.model_org
    current_provenance = None
    if checkpoint_org in provenance.PRIOR_CHECKPOINT_ORGANISMS:
        current_provenance = provenance.checkpoint_provenance(
            Path(args.model_path),
            checkpoint_org,
            model=args.model,
            datasets=args.datasets,
            seed=args.seed,
        )
        if current_provenance is None:
            raise RuntimeError(
                "Prior-baseline checkpoint is missing its validated recipe marker "
                f"or does not match this job: {args.model_path}"
            )

    existing: dict = {}
    if out.exists():
        try:
            existing = json.loads(out.read_text())
        except json.JSONDecodeError:
            existing = {}

    # Schema migrations must replace effectiveness/utility fields rather than
    # preserving stale values through the phase-merging logic below. A prior
    # result using the old utility readout is reusable: preserve its attack
    # dictionaries, but replace its non-elicitation fields after re-evaluation.
    protocol_migration = bool(
        existing
        and _is_reusable_evaluation(existing, args.model_org)
        and not _is_evaluated(existing, args.model_org)
    )
    if existing and not _is_reusable_evaluation(existing, args.model_org):
        if args.utility_only:
            raise RuntimeError(
                "Utility-only refresh requires an existing reusable result for "
                "the current evaluation protocol. Run --phase eff first."
            )
        existing = {}
        protocol_migration = False
    if existing and current_provenance is not None:
        if (
            not provenance.result_identity_matches(
                existing,
                result_org=args.model_org,
                model=args.model,
                datasets=args.datasets,
                seed=args.seed,
            )
            or existing.get("checkpoint_provenance") != current_provenance
        ):
            # Never phase-merge metrics from a different checkpoint. This also
            # migrates legacy result JSONs by re-evaluating their still-valid
            # checkpoints once and stamping the exact marker hash.
            if args.utility_only:
                raise RuntimeError(
                    "Utility-only refresh cannot merge into a result from a "
                    "different checkpoint or without current provenance."
                )
            existing = {}
            protocol_migration = False

    if args.utility_only and not existing:
        raise RuntimeError(
            "Utility-only refresh requires an existing effectiveness result; "
            "refusing to create a partial canonical JSON."
        )

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
        # Metrics can all be present while attacked-subset provenance is absent.
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
    if not provenance.result_identity_matches(
        fresh,
        result_org=args.model_org,
        model=args.model,
        datasets=args.datasets,
        seed=args.seed,
    ):
        raise RuntimeError(
            "evaluate_checkpoint returned a result whose model/organism/datasets/"
            "seed do not match the requested checkpoint."
        )
    if (args.utility_only and
            fresh.get("utility_scoring_protocol") !=
            naming.PRIOR_UTILITY_SCORING_PROTOCOL):
        raise RuntimeError(
            "Utility-only prior refresh produced the wrong utility scoring "
            f"protocol: {fresh.get('utility_scoring_protocol')!r}."
        )
    if current_provenance is not None:
        fresh["checkpoint_provenance"] = current_provenance
    merge_payload = _utility_update_payload(fresh) if args.utility_only else fresh
    merged = _merge(
        existing,
        merge_payload,
        force=args.force or protocol_migration or args.utility_only,
        replace_elicitations=tuple(sorted(replace_elicitations)),
    )
    if current_provenance is not None:
        merged["checkpoint_provenance"] = current_provenance
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(merged, indent=2))
    if not _complete_prior_suite_valid(merged, args.model_org, requested):
        raise RuntimeError(
            "The complete prior-baseline attack suite has missing or inconsistent "
            "test-subset provenance. Re-run this phase with one fixed cap."
        )
    scope = "utility-only; " if args.utility_only else ""
    print(f"[_run_eval] wrote {out} ({scope}"
          f"elicitations now: {sorted(_present_elicitations(merged)) or 'none'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
