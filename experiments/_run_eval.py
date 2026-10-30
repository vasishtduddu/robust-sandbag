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

r"""Idempotent evaluate-and-merge wrapper (the orchestrator's evaluation job).

Runs evaluate.evaluate_checkpoint for only the elicitation techniques still missing from the
result JSON, then merges the new numbers in (elicitation dictionaries are unioned; existing
effectiveness and utility values are kept unless --force). The result JSON accumulates across
phases (effectiveness -> blackbox -> whitebox), so the job can be re-run safely.

Usage:

    python experiments/_run_eval.py --model_path CKPT --model_org ORG --model MODEL \
        --datasets TASK --seed SEED --out results/json/RESULT.json [--elicitations ...]

A checkpoint trained with an optional recipe variant (README, "Recipe variants") has no matching
default recipe record; set WCMO_VARIANT_RECIPE=<label> to evaluate it. Such results carry the
label and no checkpoint provenance.
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
_ARTIFACT_ROOT = Path(os.environ.get("WCMO_ARTIFACT_ROOT", _ROOT)).expanduser().resolve()


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Durably replace a result JSON without exposing a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _load_evaluate_checkpoint():
    try:
        from wcmo.evaluate import evaluate_checkpoint
        return evaluate_checkpoint
    except Exception:
        sys.path.insert(0, str(_ROOT))
        from evaluate import evaluate_checkpoint  # type: ignore
        return evaluate_checkpoint


def _is_elic_metric_key(k: str) -> bool:
    return k in ("elicitations", "val_elicitations", "test_elicitations") or \
        k.startswith(("elicitations_", "val_elicitations_", "test_elicitations_"))


def _is_elic_count_key(k: str) -> bool:
    return (
        k in (
            "val_elicitation_n",
            "test_elicitation_n",
            "test_elicitation_sample_fingerprint",
            "val_elicitation_diagnostics",
            "test_elicitation_diagnostics",
        )
        or k.startswith(
            (
                "val_elicitation_n_",
                "test_elicitation_n_",
                "test_elicitation_sample_fingerprint_",
                "val_elicitation_diagnostics_",
                "test_elicitation_diagnostics_",
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
    # A technique counts as present once its test-split result is recorded.
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
    return (
        (model_org != "sysprompt" or naming.current_sysprompt_inference(blob))
        and naming.current_scoring_protocol(blob, model_org)
    )


def _is_reusable_evaluation(blob: dict, model_org: str | None = None) -> bool:
    """Whether a result can be merged into.

    A prior-organism result with forced-choice utility is not current, but its effectiveness and
    elicitation fields remain valid; the next evaluation replaces its utility fields.
    """
    if (blob.get("evaluation_schema_version") != naming.EVALUATION_SCHEMA_VERSION or
            "test_hs_mo_acc" not in blob):
        return False
    if model_org == "sysprompt" and not naming.current_sysprompt_inference(blob):
        # Results without seeded SysPr decoding are never merged into an inference seed.
        return False
    if model_org in naming.PRIOR_TABLE_ORGANISMS or model_org == "pwd_cktbr_parent":
        return (
            blob.get("mcq_scoring_protocol") == naming.FORCED_CHOICE_PROTOCOL
            and blob.get("utility_scoring_protocol")
            in naming.SUPPORTED_UTILITY_SCORING_PROTOCOLS
            and blob.get("utility_split_seed") == naming.PRIOR_UTILITY_SPLIT_SEED
        )
    return naming.current_scoring_protocol(blob, model_org)


def _complete_requested_suite_valid(
    blob: dict, model_org: str, requested: list[str]
) -> bool:
    # A validation-only run (WCMO_SKIP_TEST_ELICITATION) writes no test value for those
    # techniques.
    _skip_test = {t.strip() for t in os.environ.get("WCMO_SKIP_TEST_ELICITATION", "").split(",") if t.strip()}
    requested = [t for t in requested if t not in _skip_test]
    if not requested:
        return True
    requested_set = set(requested)
    required = set(naming.AUTODAN_ONLY_JAILBREAK_TECHNIQUES)
    if (model_org == "sysprompt" and
            requested_set & set(naming.JAILBREAK_TECHNIQUES) and
            blob.get("sysprompt_jailbreak_protocol") !=
            naming.SYSPROMPT_JAILBREAK_PROTOCOL):
        return False
    datasets = blob.get("datasets")
    if not isinstance(datasets, list):
        datasets = [blob.get("dataset")]
    if not datasets or any(not isinstance(dataset, str) for dataset in datasets):
        return False
    if any(
        not naming.requested_elicitations_valid(blob, dataset, requested_set)
        for dataset in datasets
    ):
        return False
    if (
        model_org not in naming.AUTODAN_ONLY_REPORT_ORGANISMS
        or not required.issubset(requested_set)
    ):
        return True
    return all(naming.metric_rob_jlbrk(blob, dataset) is not None for dataset in datasets)


def _merge(
    into: dict,
    new: dict,
    *,
    force: bool = False,
    replace_elicitations: tuple[str, ...] = (),
    attempted_elicitations: tuple[str, ...] = (),
) -> dict:
    # Drop the scores of techniques being re-run, so a failed re-run cannot leave a stale score.
    invalidated = set(replace_elicitations) | set(attempted_elicitations)
    if invalidated:
        for k, v in into.items():
            if isinstance(v, dict) and (
                _is_elic_metric_key(k)
                or _is_elic_count_key(k)
                or k == "elicitation_errors"
                or k.startswith("elicitation_errors_")
            ):
                for technique in invalidated:
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
                    # Keep the stronger prompt-injection score when both used the same rows.
                    v = dict(v)
                    v["prompt_injection"] = max(
                        cur["prompt_injection"], v["prompt_injection"]
                    )
                cur.update(v)
            else:
                into[k] = dict(v)
        elif (
            (k == "elicitation_errors" or k.startswith("elicitation_errors_"))
            and isinstance(v, dict)
        ):
            cur = into.get(k)
            if isinstance(cur, dict):
                cur.update(v)
            else:
                into[k] = dict(v)
        elif (k == "sysprompt_jailbreak_protocol" or force or k not in into):
            # Keep existing effectiveness/utility values: later phases re-compute them
            # incidentally and must not overwrite them (unless forced).
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
            "(notably pwd_cktbr_parent, which evaluates through the pwd loader)."
        ),
    )
    ap.add_argument("--model", required=True)
    ap.add_argument("--datasets", nargs="+", required=True)
    ap.add_argument("--elicitations", nargs="*", default=[])
    ap.add_argument("--out", required=True, help="Result JSON path.")
    ap.add_argument("--data_dir", default=None)
    ap.add_argument("--utility_baseline_path", default=None)
    ap.add_argument("--batch_size", type=int, default=4)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--utility-only",
        action="store_true",
        help=(
            "Replace only HS/CSQA metrics and utility protocol metadata in an "
            "existing result; preserve effectiveness and attacks."
        ),
    )
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    out = Path(args.out)
    checkpoint_org = args.checkpoint_org or args.model_org
    current_provenance = None
    if checkpoint_org in provenance.CHECKPOINT_PROVENANCE_ORGANISMS:
        current_provenance = provenance.checkpoint_provenance(
            Path(args.model_path),
            checkpoint_org,
            model=args.model,
            datasets=args.datasets,
            seed=args.seed,
        )
        if current_provenance is None:
            # WCMO_VARIANT_RECIPE=<label> allows evaluating a deliberate recipe variant; its
            # result records the label instead of checkpoint provenance.
            variant = os.environ.get("WCMO_VARIANT_RECIPE", "").strip()
            if not variant:
                raise RuntimeError(
                    "Checkpoint is missing its validated recipe record/payload "
                    f"or does not match this job: {args.model_path}. If this is a "
                    "deliberate recipe variant, set WCMO_VARIANT_RECIPE=<label>."
                )
            print(
                f"[_run_eval] WCMO_VARIANT_RECIPE={variant!r}: evaluating a recipe variant; "
                "the result carries the label instead of checkpoint provenance."
            )

    existing: dict = {}
    if out.exists():
        try:
            existing = json.loads(out.read_text())
        except json.JSONDecodeError:
            existing = {}

    # A result scored with an older utility readout keeps its attack scores but has its
    # effectiveness/utility fields replaced after re-evaluation.
    protocol_migration = bool(
        existing
        and _is_reusable_evaluation(existing, args.model_org)
        and not _is_evaluated(existing, args.model_org)
    )
    if existing and not _is_reusable_evaluation(existing, args.model_org):
        if args.utility_only:
            raise RuntimeError(
                "Utility-only refresh requires an existing reusable result for "
                "the current evaluation protocol. Evaluate effectiveness first."
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
            # Never merge metrics from a different checkpoint.
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
            "refusing to create a partial result JSON."
        )

    present = _present_elicitations(existing)
    requested = list(dict.fromkeys(args.elicitations))  # de-dup, keep order

    # WCMO_WHITEBOX_ONLY only restricts which whitebox techniques may run; it never
    # requests one, so without --elicitations nothing runs.
    if not requested and os.environ.get("WCMO_WHITEBOX_ONLY"):
        print(
            "[_run_eval] WARNING: WCMO_WHITEBOX_ONLY="
            f"{os.environ['WCMO_WHITEBOX_ONLY']!r} is set but --elicitations "
            "is empty, so no elicitation will run and this job will still exit "
            "0. Pass --elicitations explicitly.",
            flush=True,
        )
    missing = requested if args.force else [e for e in requested if e not in present]
    replace_elicitations = set(requested) if args.force else set()

    # SysPr attack scores from an older protocol (attacks that did not query the organism
    # with its system prompt) are replaced as a whole.
    stale_sysprompt_attacks = (
        args.model_org == "sysprompt" and
        existing.get("sysprompt_jailbreak_protocol") !=
        naming.SYSPROMPT_JAILBREAK_PROTOCOL and
        bool(set(requested) & set(naming.JAILBREAK_TECHNIQUES))
    )
    if stale_sysprompt_attacks:
        missing = requested
        replace_elicitations.update(naming.JAILBREAK_TECHNIQUES)
        replace_elicitations.add("prompt_injection")

    if (existing and not missing and
            not _complete_requested_suite_valid(existing, args.model_org, requested)):
        # Scores present but without attacked-subset metadata: re-run the requested suite.
        missing = requested
        replace_elicitations.update(requested)

    if existing and _is_evaluated(existing, args.model_org) and not missing and not args.force:
        print(f"[_run_eval] up to date: {out} "
              f"(eff present, elicitations {sorted(present) or 'none'})")
        return 0

    evaluate_checkpoint = _load_evaluate_checkpoint()
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        tmp = tf.name

    # Per-model cache directory for the base-model utility reference, shared across runs.
    utility_baseline_path = args.utility_baseline_path or str(
        _ARTIFACT_ROOT / "checkpoints" / f"utility_baseline_{args.model}"
    )

    try:
        monitor_only = (
            bool(missing)
            and set(missing) <= {"probe", "ft_probe"}
            and len(args.datasets) == 1
            and _is_evaluated(existing, args.model_org)
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
            elicitation_only=monitor_only,
        )
        fresh = json.loads(Path(tmp).read_text())
    finally:
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
        attempted_elicitations=tuple(missing),
    )
    if current_provenance is not None:
        merged["checkpoint_provenance"] = current_provenance
    _variant = os.environ.get("WCMO_VARIANT_RECIPE", "").strip()
    if _variant:
        merged["recipe_variant"] = _variant
        merged.pop("checkpoint_provenance", None)
    _atomic_write_json(out, merged)
    if not _complete_requested_suite_valid(merged, args.model_org, requested):
        raise RuntimeError(
            "A requested elicitation failed or has missing or inconsistent test-subset "
            "metadata. Re-run it with a single, fixed WCMO_ELICIT_EVAL_MAX."
        )
    scope = "utility-only; " if args.utility_only else ""
    print(f"[_run_eval] wrote {out} ({scope}"
          f"elicitations now: {sorted(_present_elicitations(merged)) or 'none'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
