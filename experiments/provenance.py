"""Checkpoint/result provenance gates for the reported prior baselines.

The Table-2 rows are scientific artifacts, so a result JSON is reusable only
when it identifies the exact validated checkpoint recipe that produced it.
This module is intentionally lightweight and shared by the orchestrator,
evaluate-and-merge wrapper, and table renderer.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Iterable

try:
    from . import naming
except ImportError:
    import naming


SCHEMA_VERSION = 1
PRIOR_CHECKPOINT_ORGANISMS = frozenset(
    {"pwd", "pwd_hof_parent", "circuitbreak", "sysprompt"}
)
MARKER_FILES = {
    "pwd": "password_lock_config.json",
    "pwd_hof_parent": "password_lock_config.json",
    "circuitbreak": "circuitbreak_config.json",
    "sysprompt": "config.json",
}


def _canonical_sha256(config: dict) -> str:
    payload = json.dumps(
        config,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _float_eq(value, expected: float) -> bool:
    try:
        return math.isclose(float(value), expected, rel_tol=0.0, abs_tol=1e-12)
    except (TypeError, ValueError):
        return False


def _identity_matches(
    config: dict,
    model: str | None,
    datasets: Iterable[str] | None,
    seed: int | None,
) -> bool:
    if model is not None and config.get("model_name") != model:
        return False
    if datasets is not None:
        expected = tuple(datasets)
        raw = config.get("datasets", config.get("datasets_list"))
        if raw is None:
            actual = (config.get("dataset"),)
        elif isinstance(raw, list):
            actual = tuple(raw)
        else:
            return False
        if actual != expected:
            return False
    # Older CktBr/SysPr markers did not record the seed. Their canonical
    # runXX slot plus the result seed remains binding; new markers record it.
    marker_seed = config.get("seed")
    if marker_seed is not None and seed is not None:
        if type(marker_seed) is not int or marker_seed != seed:
            return False
    return True


def validate_checkpoint_config(
    checkpoint_org: str,
    config: dict,
    *,
    model: str | None = None,
    datasets: Iterable[str] | None = None,
    seed: int | None = None,
) -> bool:
    """Return whether ``config`` is the declared reportable prior recipe."""
    if not isinstance(config, dict) or checkpoint_org not in PRIOR_CHECKPOINT_ORGANISMS:
        return False
    datasets_tuple = tuple(datasets) if datasets is not None else None
    if not _identity_matches(config, model, datasets_tuple, seed):
        return False

    if checkpoint_org in {"pwd", "pwd_hof_parent"}:
        return (
            config.get("recipe") == naming.PRIOR_PWD_RECIPE
            and config.get("weak_model_name") is None
            and config.get("lock_target") == "wrong"
            and config.get("use_general_anchor") is True
            and _float_eq(config.get("wrong_password_frac", 0.0), 0.0)
            and config.get("mcq_training_protocol") == naming.FORCED_CHOICE_PROTOCOL
        )

    if checkpoint_org == "circuitbreak":
        diagnostics = config.get("rr_diagnostics")
        if not isinstance(diagnostics, dict):
            return False
        finite_diagnostics = all(
            type(diagnostics.get(key)) in (int, float)
            and math.isfinite(float(diagnostics[key]))
            for key in (
                "first_step_grad_norm",
                "initial_rr_loss",
                "final_rr_loss",
                "initial_retain_loss",
                "final_retain_loss",
            )
        )
        parent_path = config.get("pw_locked_model_path")
        parent_identity_matches = True
        if (
            isinstance(parent_path, str)
            and model is not None
            and datasets_tuple is not None
            and seed is not None
        ):
            expected_parent = canonical_checkpoint_dir(
                Path("."),
                "pwd_hof_parent",
                model,
                datasets_tuple,
                seed,
            )
            parent_identity_matches = (
                _relative_checkpoint_name(Path(parent_path))
                == _relative_checkpoint_name(expected_parent)
            )
        return (
            config.get("recipe") == naming.PRIOR_CIRCUITBREAK_RECIPE
            and _float_eq(config.get("cb_escape_eps"), 0.1)
            and config.get("k") == 64
            and config.get("parent_password_recipe") == naming.PRIOR_PWD_RECIPE
            and config.get("mcq_training_protocol") == naming.FORCED_CHOICE_PROTOCOL
            and diagnostics.get("effectively_stationary") is False
            and finite_diagnostics
            and isinstance(diagnostics.get("rr_loss_decreased"), bool)
            and type(diagnostics.get("rr_microsteps")) is int
            and diagnostics["rr_microsteps"] > 0
            and isinstance(parent_path, str)
            and "pwd_hof_parent_" in parent_path
            and parent_identity_matches
        )

    selection = config.get("prompt_selection")
    candidates = config.get("prompt_candidates")
    return (
        isinstance(selection, dict)
        and isinstance(candidates, dict)
        and config.get("source_repository") == "https://github.com/TeunvdWeij/sandbagging"
        and config.get("prompt_selection_schema_version") == 1
        and set(candidates) == {"exact_released"}
        and config.get("prompt_variant") == "exact_released"
        and config.get("selected_prompt_variant") == "exact_released"
        and selection.get("method") == "fixed_prior_work_prompt"
        and selection.get("uses_test_data") is False
        and config.get("system_prompt") == candidates.get("exact_released")
    )


def marker_path(checkpoint: Path, checkpoint_org: str) -> Path | None:
    marker = MARKER_FILES.get(checkpoint_org)
    return checkpoint / marker if marker else None


def _relative_checkpoint_name(checkpoint: Path) -> str:
    if checkpoint.name.startswith("run") and checkpoint.name[3:].isdigit():
        return f"{checkpoint.parent.name}/{checkpoint.name}"
    return checkpoint.name


def checkpoint_provenance(
    checkpoint: Path,
    checkpoint_org: str,
    *,
    model: str,
    datasets: Iterable[str],
    seed: int,
) -> dict | None:
    """Build the canonical provenance stamp for a validated checkpoint."""
    marker = marker_path(checkpoint, checkpoint_org)
    if marker is None or not marker.is_file():
        return None
    try:
        config = json.loads(marker.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    datasets = tuple(datasets)
    if not validate_checkpoint_config(
        checkpoint_org,
        config,
        model=model,
        datasets=datasets,
        seed=seed,
    ):
        return None
    return {
        "schema_version": SCHEMA_VERSION,
        "checkpoint_organism": checkpoint_org,
        "model": model,
        "datasets": list(datasets),
        "seed": seed,
        "checkpoint_relpath": _relative_checkpoint_name(checkpoint),
        "marker_file": marker.name,
        "marker_sha256": _canonical_sha256(config),
        "recipe_id": config.get("recipe", config.get("prompt_variant")),
    }


def result_identity_matches(
    blob: dict,
    *,
    result_org: str,
    model: str,
    datasets: Iterable[str],
    seed: int,
) -> bool:
    expected_datasets = tuple(datasets)
    raw = blob.get("datasets")
    if raw is None:
        actual_datasets = (blob.get("dataset"),)
    elif isinstance(raw, list):
        actual_datasets = tuple(raw)
    else:
        return False
    return (
        blob.get("model_org") == result_org
        and blob.get("model") == model
        and actual_datasets == expected_datasets
        and type(blob.get("seed")) is int
        and blob.get("seed") == seed
    )


def result_matches_checkpoint(
    blob: dict,
    checkpoint: Path,
    checkpoint_org: str,
    *,
    result_org: str,
    model: str,
    datasets: Iterable[str],
    seed: int,
) -> bool:
    """Check result identity and exact checkpoint marker provenance."""
    if not result_identity_matches(
        blob,
        result_org=result_org,
        model=model,
        datasets=datasets,
        seed=seed,
    ):
        return False
    current = checkpoint_provenance(
        checkpoint,
        checkpoint_org,
        model=model,
        datasets=datasets,
        seed=seed,
    )
    return current is not None and blob.get("checkpoint_provenance") == current


def canonical_checkpoint_dir(
    checkpoint_root: Path,
    checkpoint_org: str,
    model: str,
    datasets: Iterable[str],
    seed: int,
) -> Path:
    base = checkpoint_root / naming.ckpt_dirname(
        checkpoint_org,
        model,
        datasets,
    )
    if checkpoint_org == "sysprompt":
        return base
    if checkpoint_org in {"pwd", "pwd_hof_parent", "circuitbreak"}:
        try:
            slot = naming.PRIOR_TRAINED_SEEDS.index(seed) + 1
        except ValueError:
            return base / f"seed{seed}"
        return base / f"run{slot:02d}"
    return base
