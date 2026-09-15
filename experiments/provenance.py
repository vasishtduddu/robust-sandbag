"""Checkpoint/result provenance gates for reportable experiment artifacts.

The paper rows are scientific artifacts, so a result JSON is reusable only
when it identifies the exact validated checkpoint recipe that produced it.
This module is intentionally lightweight and shared by the orchestrator,
evaluate-and-merge wrapper, and table renderer.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
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
CHECKPOINT_PROVENANCE_ORGANISMS = PRIOR_CHECKPOINT_ORGANISMS | {"obfcot"}
MARKER_FILES = {
    "pwd": "password_lock_config.json",
    "pwd_hof_parent": "password_lock_config.json",
    "circuitbreak": "circuitbreak_config.json",
    "sysprompt": "config.json",
    "obfcot": "obfcot_config.json",
}

_OBFCOT_SOURCE_FILES = (
    "HYPERPARAMETERS.toml",
    "train.py",
    "experiments/naming.py",
    "experiments/provenance.py",
    "modelorgs/obfuscated_cot.py",
    "modelorgs/_oat_internals.py",
    "modelorgs/distilled_cot.py",
    "modelorgs/robust_password_lock.py",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def obfcot_source_sha256() -> dict[str, str]:
    """Hashes of the source/config files that define the current obfcot recipe."""
    root = Path(__file__).resolve().parent.parent
    return {
        relative: _sha256_file(root / relative)
        for relative in _OBFCOT_SOURCE_FILES
    }


def _source_manifest_valid(source_hashes) -> bool:
    return (
        isinstance(source_hashes, dict)
        and set(source_hashes) == set(_OBFCOT_SOURCE_FILES)
        and all(
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
            for value in source_hashes.values()
        )
    )


def _is_checkpoint_weight(name: str, checkpoint_format: str | None = None) -> bool:
    if checkpoint_format == "peft_adapter":
        return name.startswith("adapter_model") and name.endswith(
            (".safetensors", ".bin")
        )
    if checkpoint_format == "merged":
        return (
            name.startswith("model") and name.endswith(".safetensors")
        ) or (
            name.startswith("pytorch_model") and name.endswith(".bin")
        )
    return _is_checkpoint_weight(name, "peft_adapter") or _is_checkpoint_weight(
        name, "merged"
    )


def _atomic_write_json(path: Path, payload: dict) -> None:
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
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _checkpoint_payload(
    checkpoint: Path, *, include_sha256: bool = True
) -> list[dict[str, int | str]]:
    """Describe and checksum the exact saved model payload."""
    candidates = []
    for path in checkpoint.iterdir():
        if not path.is_file():
            continue
        if (
            path.name in {"adapter_config.json", "config.json"}
            or path.name.endswith(".index.json")
            or _is_checkpoint_weight(path.name)
        ):
            item: dict[str, int | str] = {
                "name": path.name,
                "size": path.stat().st_size,
            }
            if include_sha256:
                item["sha256"] = _sha256_file(path)
            candidates.append(item)
    return sorted(candidates, key=lambda item: str(item["name"]))


def checkpoint_payload_matches(
    checkpoint: Path, config: dict, *, verify_sha256: bool = True
) -> bool:
    """Verify the model files recorded by a checkpoint save receipt.

    Full hashing is appropriate for compact adapters and explicit integrity
    audits.  Routine status checks for merged prior-baseline checkpoints may
    trust the immutable, post-save digest receipt while still rejecting added,
    missing, renamed, or size-changed payload files.
    """
    recorded = config.get("checkpoint_files")
    if not isinstance(recorded, list) or not recorded:
        return False
    expected: dict[str, tuple[int, str]] = {}
    for item in recorded:
        if not isinstance(item, dict):
            return False
        name, size, sha256 = (
            item.get("name"),
            item.get("size"),
            item.get("sha256"),
        )
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or type(size) is not int
            or size <= 0
            or not isinstance(sha256, str)
            or len(sha256) != 64
            or any(character not in "0123456789abcdef" for character in sha256)
            or name in expected
        ):
            return False
        expected[name] = (size, sha256)
    checkpoint_format = config.get("checkpoint_format")
    has_weights = any(
        _is_checkpoint_weight(name, checkpoint_format) for name in expected
    )
    expected_config = (
        "adapter_config.json"
        if config.get("checkpoint_format") == "peft_adapter"
        else "config.json"
    )
    if not has_weights or expected_config not in expected:
        return False
    try:
        actual_items = _checkpoint_payload(
            checkpoint, include_sha256=verify_sha256
        )
        if verify_sha256:
            actual = {
                str(item["name"]): (int(item["size"]), str(item["sha256"]))
                for item in actual_items
            }
            return actual == expected
        actual_sizes = {
            str(item["name"]): int(item["size"])
            for item in actual_items
        }
        expected_sizes = {
            name: size for name, (size, _sha256) in expected.items()
        }
        return actual_sizes == expected_sizes
    except OSError:
        return False


def attach_checkpoint_payload_receipt(
    checkpoint: Path, checkpoint_org: str
) -> Path:
    """Hash a saved prior checkpoint once and atomically bind its marker to it."""
    if checkpoint_org not in PRIOR_CHECKPOINT_ORGANISMS - {"sysprompt"}:
        raise ValueError(
            "Payload receipts are only defined for trained prior checkpoints, "
            f"got {checkpoint_org!r}."
        )
    marker = marker_path(checkpoint, checkpoint_org)
    if marker is None or not marker.is_file():
        raise FileNotFoundError(
            f"Missing {checkpoint_org} recipe marker under {checkpoint}"
        )
    config = json.loads(marker.read_text())
    config["checkpoint_files"] = _checkpoint_payload(checkpoint)
    _atomic_write_json(marker, config)
    return marker


def write_prior_checkpoint_marker(
    checkpoint: Path, checkpoint_org: str, config: dict
) -> Path:
    """Publish a new prior marker only after its full payload receipt exists."""
    if checkpoint_org not in PRIOR_CHECKPOINT_ORGANISMS - {"sysprompt"}:
        raise ValueError(
            "Payload receipts are only defined for trained prior checkpoints, "
            f"got {checkpoint_org!r}."
        )
    marker = marker_path(checkpoint, checkpoint_org)
    if marker is None:
        raise ValueError(f"No recipe marker is defined for {checkpoint_org!r}.")
    receipted = dict(config)
    receipted["checkpoint_files"] = _checkpoint_payload(checkpoint)
    _atomic_write_json(marker, receipted)
    return marker


def write_obfcot_checkpoint_marker(
    checkpoint: Path,
    *,
    model: str,
    datasets: Iterable[str],
    seed: int,
    checkpoint_format: str,
    resolved_hyperparameters: dict,
    protocol: dict,
    lock_context_aug_applied: dict | None = None,
    commonsense_anchor_loaded: dict | None = None,
) -> Path:
    """Atomically write the immutable receipt after a successful obfcot save."""
    if checkpoint_format not in {"peft_adapter", "merged"}:
        raise ValueError(f"Unsupported obfcot checkpoint format: {checkpoint_format!r}")
    config = {
        "schema_version": SCHEMA_VERSION,
        "recipe": naming.OBFCOT_RECIPE,
        "organism_type": "obfcot",
        "model_name": model,
        "datasets": list(datasets),
        "seed": seed,
        "mcq_training_protocol": protocol.get("mcq_training_protocol"),
        "mcq_scoring_protocol": protocol.get("mcq_scoring_protocol"),
        "utility_scoring_protocol": protocol.get("utility_scoring_protocol"),
        "utility_split_seed": naming.TABLE3_UTILITY_SPLIT_SEED,
        "checkpoint_format": checkpoint_format,
        "checkpoint_files": _checkpoint_payload(checkpoint),
        "protocol": protocol,
        "resolved_hyperparameters": resolved_hyperparameters,
        "source_sha256": obfcot_source_sha256(),
        # BEHAVIOURAL receipt for benign-context augmentation, per training phase.
        # `protocol["lock_context_aug"]` records the ENV VAR and is asserted == 0.5 above,
        # but no obfcot path READ that variable until 8cb0958, so every marker written
        # before then asserts an augmentation the run did not perform. This field records
        # rows actually rewritten, so "declared and applied" is distinguishable from
        # "declared and skipped", and its ABSENCE identifies a pre-fix checkpoint.
        # NOT asserted by validate_checkpoint_config: existing checkpoints must stay
        # valid, or every published obfcot cell would stop rendering.
        "lock_context_aug_applied": dict(lock_context_aug_applied or {}),
        # BEHAVIOURAL receipt for the commonsense (PIQA/Winogrande) anchor, same contract as
        # the field above. `protocol["commonsense_anchor"]` records the ENV VAR and IS asserted
        # True by validate_checkpoint_config, but the anchor DATA FILE did not exist for the
        # first ~77 training runs, so those markers certify a component that never ran (F630):
        # the loader warned and returned None while the stamp still read True. This field
        # records rows ACTUALLY loaded, so a fixed organism is distinguishable from a published
        # one even though both stamp commonsense_anchor=True. Its ABSENCE identifies a pre-fix
        # checkpoint. NOT asserted by validate_checkpoint_config: existing checkpoints must stay
        # valid, or every published obfcot cell would stop rendering.
        "commonsense_anchor_loaded": dict(commonsense_anchor_loaded or {}),
    }
    path = checkpoint / MARKER_FILES["obfcot"]
    _atomic_write_json(path, config)
    return path


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
    """Return whether ``config`` is the declared reportable recipe."""
    if (
        not isinstance(config, dict)
        or checkpoint_org not in CHECKPOINT_PROVENANCE_ORGANISMS
    ):
        return False
    datasets_tuple = tuple(datasets) if datasets is not None else None
    # SysPr's config stores the seed used when the shared, fixed prompt marker
    # was first written.  It is not a trained checkpoint seed: all inference
    # seeds deliberately reuse this exact marker.
    identity_seed = None if checkpoint_org == "sysprompt" else seed
    if not _identity_matches(config, model, datasets_tuple, identity_seed):
        return False

    if checkpoint_org in {"pwd", "pwd_hof_parent"}:
        return (
            config.get("recipe") == naming.PRIOR_PWD_RECIPE
            and config.get("weak_model_name") is None
            and config.get("lock_target") == "wrong"
            and config.get("use_general_anchor") is True
            and config.get("use_utility_mix") is False
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

    if checkpoint_org == "obfcot":
        protocol = config.get("protocol")
        hparams = config.get("resolved_hyperparameters")
        marker_seed = config.get("seed")
        return (
            config.get("schema_version") == SCHEMA_VERSION
            and config.get("recipe") == naming.OBFCOT_RECIPE
            and config.get("organism_type") == "obfcot"
            and type(marker_seed) is int
            and (seed is None or marker_seed == seed)
            and config.get("mcq_training_protocol")
            == naming.TABLE3_SCORING_PROTOCOL
            and config.get("mcq_scoring_protocol")
            == naming.TABLE3_SCORING_PROTOCOL
            and config.get("utility_scoring_protocol")
            == naming.TABLE3_UTILITY_SCORING_PROTOCOL
            and config.get("utility_split_seed")
            == naming.TABLE3_UTILITY_SPLIT_SEED
            and config.get("checkpoint_format") in {"peft_adapter", "merged"}
            and isinstance(protocol, dict)
            and protocol.get("lock_target") == "F"
            and protocol.get("obfcot_lat") is True
            and protocol.get("obfcot_lat_last") is True
            and protocol.get("oat_faithful") is True
            and protocol.get("lat_freeze_mlp") is True
            and protocol.get("lat_sync_lock_letter") is True
            and protocol.get("commonsense_anchor") is True
            and protocol.get("hellaswag_anchor") is True
            and protocol.get("lat_target_eos") is True
            and _float_eq(protocol.get("lat_away"), 0.0)
            and _float_eq(protocol.get("lat_toward"), 0.5)
            and _float_eq(protocol.get("lat_kl"), 0.1)
            and _float_eq(protocol.get("lock_context_aug"), 0.5)
            and protocol.get("oat_probes") == 16
            and protocol.get("oat_buffer") == 512
            and protocol.get("experimental_overrides") == {}
            and isinstance(hparams, dict)
            and hparams.get("oat_steps") == 600
            and _float_eq(hparams.get("oat_weight_obf"), 4.0)
            and hparams.get("oat_obf_layers") == [13, 14, 15]
            and hparams.get("oat_probe_inner_steps") == 8
            and hparams.get("oat_probe_warmup_steps") == 50
            and hparams.get("lat_steps") == 150
            and _float_eq(hparams.get("lat_epsilon"), 6.0)
            and hparams.get("lat_pgd_iterations") == 32
            and isinstance(hparams.get("resolved_lat_pgd_layers"), list)
            and bool(hparams.get("resolved_lat_pgd_layers"))
            and hparams.get("oat_use_utility_retain") is True
            and _source_manifest_valid(config.get("source_sha256"))
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
    if checkpoint_org == "obfcot" and not checkpoint_payload_matches(
        checkpoint, config
    ):
        return None
    payload_receipt = config.get("checkpoint_files")
    if (
        checkpoint_org in PRIOR_CHECKPOINT_ORGANISMS - {"sysprompt"}
        and payload_receipt is not None
        and not checkpoint_payload_matches(
            checkpoint, config, verify_sha256=False
        )
    ):
        return None
    stamp = {
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
    if payload_receipt is not None:
        stamp["checkpoint_payload_sha256"] = _canonical_sha256(
            {"checkpoint_files": payload_receipt}
        )
    return stamp


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
    if checkpoint_org in {
        "pwd",
        "pwd_hof_parent",
        "circuitbreak",
    } or checkpoint_org in naming.TABLE3_ORGANISMS:
        try:
            roster = (
                naming.TABLE3_TRAINED_SEEDS
                if checkpoint_org in naming.TABLE3_ORGANISMS
                else naming.PRIOR_TRAINED_SEEDS
            )
            slot = roster.index(seed) + 1
        except ValueError:
            return base / f"run{seed:02d}"
        return base / f"run{slot:02d}"
    return base
