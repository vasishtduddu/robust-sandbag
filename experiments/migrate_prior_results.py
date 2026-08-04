"""Safely attach checkpoint provenance to legacy Table-2 result JSONs.

This migration does not evaluate or train a model. It accepts a legacy result
only when its identity, scoring protocol, canonical filename, checkpoint recipe,
seed slot, and file ordering all agree. A dry run is the default; ``--apply``
backs up each accepted JSON before atomically adding ``checkpoint_provenance``.

Examples::

    python -m possum.experiments.migrate_prior_results \
        --artifact-root "$POSSUM_ARTIFACT_ROOT"

    python -m possum.experiments.migrate_prior_results \
        --artifact-root "$POSSUM_ARTIFACT_ROOT" --apply
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

try:
    from . import naming, provenance
except ImportError:
    import naming, provenance


@dataclass(frozen=True)
class Migration:
    result_path: Path
    checkpoint_path: Path
    blob: dict
    stamp: dict


def _checkpoint_org(path: Path, blob: dict) -> str | None:
    if path.name.startswith("pwd_hof_parent_"):
        return "pwd_hof_parent"
    organism = blob.get("model_org")
    return organism if organism in naming.PRIOR_TABLE_ORGANISMS else None


def _datasets(blob: dict) -> tuple[str, ...] | None:
    raw = blob.get("datasets")
    if raw is None:
        dataset = blob.get("dataset")
        return (dataset,) if isinstance(dataset, str) else None
    if isinstance(raw, list) and raw and all(isinstance(item, str) for item in raw):
        return tuple(raw)
    return None


def _canonical_seed(checkpoint_org: str, seed: int) -> bool:
    if checkpoint_org == "sysprompt":
        # Pre-sampling SysPr JSONs need a full stochastic inference rerun, not
        # a provenance-only migration.
        return False
    return seed in naming.PRIOR_TRAINED_SEEDS


def _validate_legacy_result(
    path: Path,
    artifact_root: Path,
) -> tuple[Migration | None, str]:
    try:
        blob = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        return None, f"unreadable JSON: {exc}"
    if not isinstance(blob, dict):
        return None, "top-level JSON value is not an object"

    checkpoint_org = _checkpoint_org(path, blob)
    if checkpoint_org is None:
        return None, "not a prior-baseline result"

    model = blob.get("model")
    datasets = _datasets(blob)
    seed = blob.get("seed")
    if not isinstance(model, str) or datasets is None or type(seed) is not int:
        return None, "missing or malformed model/dataset/seed identity"
    if not _canonical_seed(checkpoint_org, seed):
        if checkpoint_org == "sysprompt":
            return None, "deterministic SysPr result requires seeded top-k re-evaluation"
        return None, "non-reportable legacy seed"

    result_org = naming.ORGANISMS[checkpoint_org].train_code or checkpoint_org
    expected_name = naming.result_json_name(
        checkpoint_org,
        model,
        datasets,
        seed,
    )
    if path.name != expected_name:
        return None, f"noncanonical filename (expected {expected_name})"
    if not provenance.result_identity_matches(
        blob,
        result_org=result_org,
        model=model,
        datasets=datasets,
        seed=seed,
    ):
        return None, "JSON identity does not match its canonical path"

    if (
        blob.get("evaluation_schema_version") != naming.EVALUATION_SCHEMA_VERSION
        or blob.get("mcq_scoring_protocol") != naming.FORCED_CHOICE_PROTOCOL
        or blob.get("utility_scoring_protocol") != naming.FORCED_CHOICE_PROTOCOL
        or blob.get("utility_split_seed") != naming.PRIOR_UTILITY_SPLIT_SEED
    ):
        return None, "legacy or mismatched evaluation/scoring protocol"
    # These are the five already-computed Table-2 effectiveness/utility values.
    # Jlbrk is deliberately not required because the robustness campaign is next.
    if checkpoint_org in naming.PRIOR_TABLE_ORGANISMS:
        for label, metric in naming.TAB_RESULTS_COLUMNS[:5]:
            if metric(blob, datasets[0]) is None:
                return None, f"missing reportable {label} metric"
    elif (
        naming._read_metric(blob, "task_nopw_acc", datasets[0]) is None
        or naming.metric_util_hs(blob, datasets[0]) is None
        or naming.metric_util_csqa(blob, datasets[0]) is None
    ):
        return None, "incomplete internal parent evaluation"

    checkpoint = provenance.canonical_checkpoint_dir(
        artifact_root / "checkpoints",
        checkpoint_org,
        model,
        datasets,
        seed,
    )
    stamp = provenance.checkpoint_provenance(
        checkpoint,
        checkpoint_org,
        model=model,
        datasets=datasets,
        seed=seed,
    )
    if stamp is None:
        return None, f"checkpoint recipe/identity is invalid at {checkpoint}"

    marker = provenance.marker_path(checkpoint, checkpoint_org)
    if marker is None or not marker.is_file():
        return None, f"checkpoint marker is missing at {checkpoint}"
    try:
        if path.stat().st_mtime_ns < marker.stat().st_mtime_ns:
            return None, "result predates its checkpoint recipe marker"
    except OSError as exc:
        return None, f"cannot compare result/checkpoint timestamps: {exc}"

    existing = blob.get("checkpoint_provenance")
    if existing is not None:
        if existing == stamp:
            return None, "already migrated"
        return None, "existing checkpoint provenance disagrees with checkpoint"

    return Migration(path, checkpoint, blob, stamp), "ready"


def plan_migration(
    artifact_root: Path,
) -> tuple[list[Migration], list[tuple[Path, str]], list[tuple[Path, str]]]:
    """Return (ready, blocking, skipped) without changing any files."""
    results_root = artifact_root / "results" / "json"
    ready: list[Migration] = []
    blocking: list[tuple[Path, str]] = []
    skipped: list[tuple[Path, str]] = []
    for path in sorted(results_root.glob("*.json")):
        migration, reason = _validate_legacy_result(path, artifact_root)
        if migration is not None:
            ready.append(migration)
        elif reason in {
            "not a prior-baseline result",
            "non-reportable legacy seed",
            "already migrated",
        }:
            skipped.append((path, reason))
        else:
            blocking.append((path, reason))
    return ready, blocking, skipped


def _apply(migrations: list[Migration]) -> None:
    backups = [
        migration.result_path.with_suffix(
            f"{migration.result_path.suffix}.pre-provenance"
        )
        for migration in migrations
    ]
    existing_backups = [backup for backup in backups if backup.exists()]
    if existing_backups:
        names = ", ".join(str(path) for path in existing_backups)
        raise FileExistsError(
            f"Refusing to start: migration backup(s) already exist: {names}"
        )

    for migration in migrations:
        path = migration.result_path
        backup = path.with_suffix(f"{path.suffix}.pre-provenance")
        shutil.copy2(path, backup)

        migrated = dict(migration.blob)
        migrated["checkpoint_provenance"] = migration.stamp
        # Guard the scientific payload: the migration may add exactly one field.
        assert {
            key: value
            for key, value in migrated.items()
            if key != "checkpoint_provenance"
        } == migration.blob

        temporary = path.with_suffix(f"{path.suffix}.provenance-tmp")
        temporary.write_text(json.dumps(migrated, indent=2) + "\n")
        os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact-root",
        default=os.environ.get("POSSUM_ARTIFACT_ROOT"),
        required=os.environ.get("POSSUM_ARTIFACT_ROOT") is None,
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Back up and stamp every validated legacy result. Default: dry run.",
    )
    args = parser.parse_args()

    artifact_root = Path(args.artifact_root).expanduser().resolve()
    ready, blocking, skipped = plan_migration(artifact_root)
    print(
        f"[prior-result migration] ready={len(ready)} "
        f"blocking={len(blocking)} skipped={len(skipped)}"
    )
    for path, reason in blocking:
        print(f"  BLOCK {path.name}: {reason}")
    if blocking:
        print("No files changed because at least one candidate failed validation.")
        return 2
    if not args.apply:
        print("Dry run only. Re-run with --apply after reviewing this summary.")
        return 0

    _apply(ready)
    print(
        f"Migrated {len(ready)} JSONs. Each original is beside it as "
        "*.json.pre-provenance."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
