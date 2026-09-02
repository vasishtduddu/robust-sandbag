"""Hyperparameter registry loader for Possum.

Loads HYPERPARAMETERS.toml and resolves a flat dict of hyperparameter overrides
for a given (model_org, model, dataset) triple.

Lookup order — values are merged from least to most specific, so more specific
entries override less specific ones:
  1. [defaults]
  2. [<model_org>]
  3. [<model_org>.<model>]
  4. [<model_org>.<model>.<dataset>]

Usage in train_modorg.py / train_modorg_scale.py:

    file_hparams = resolve_hparams(model_org, model, dataset)
    if file_hparams:
        parser.set_defaults(**file_hparams)   # CLI flags still override these

set_defaults() means any key loaded here acts as a new argparse default —
explicit CLI flags always win.
"""

import sys
from pathlib import Path
from typing import Any

_HPARAMS_FILE = Path(__file__).parent.parent / "HYPERPARAMETERS.toml"


def _load_toml(path: Path) -> dict:
    """Load a TOML file using tomllib (3.11+) or tomli fallback."""
    if sys.version_info >= (3, 11):
        import tomllib
        with open(path, "rb") as f:
            return tomllib.load(f)
    try:
        import tomli  # pip install tomli
        with open(path, "rb") as f:
            return tomli.load(f)
    except ImportError as exc:
        # FAIL LOUDLY. Returning {} here is silently catastrophic: every value in
        # HYPERPARAMETERS.toml is then replaced by an argparse default, so training
        # runs to completion and produces an organism trained on the WRONG recipe.
        # That is exactly what happened on a Lambda box whose venv was Python 3.10
        # (no stdlib tomllib) with tomli absent: obfcot/14B trained at oat_steps=200
        # instead of 600, lat_steps=100 instead of 150, oat_obf_layers=None instead
        # of [13,14,15], and was only caught hours later by provenance validation.
        raise RuntimeError(
            f"Cannot read {path}: Python {sys.version_info.major}.{sys.version_info.minor} "
            "has no stdlib tomllib and 'tomli' is not installed. Every hyperparameter "
            "would silently fall back to an argparse default and the organism would be "
            "trained on the wrong recipe. Install tomli (pip install tomli)."
        ) from exc


def resolve_hparams(
    model_org: str,
    model: str,
    dataset: str,
    hparams_file: Path | None = None,
) -> dict[str, Any]:
    """Return merged hyperparameters for (model_org, model, dataset).

    Args:
        model_org:     Organism identifier, e.g. "robpwd".
        model:         Model name, e.g. "mistral-instruct-v3".
        dataset:       Dataset key, e.g. "wmdp" or "bio_chem" (sorted, underscore-joined
                       for multi-dataset runs).
        hparams_file:  Override path to TOML file; defaults to HYPERPARAMETERS.toml
                       next to this package.

    Returns:
        Flat dict of hyperparameter name → value, ready to pass to
        ``argparse.ArgumentParser.set_defaults()``.
        Returns an empty dict if the file is missing or tomli/tomllib is unavailable.
    """
    path = hparams_file or _HPARAMS_FILE
    if not path.exists():
        return {}

    data = _load_toml(path)
    if not data:
        return {}

    result: dict[str, Any] = {}

    # 1. Global defaults
    result.update(data.get("defaults", {}))

    # 2. Organism-level (scalar keys only; nested dicts are model-level tables)
    org_section = data.get(model_org, {})
    result.update({k: v for k, v in org_section.items() if not isinstance(v, dict)})

    # 3. Model-level
    model_section = org_section.get(model, {})
    result.update({k: v for k, v in model_section.items() if not isinstance(v, dict)})

    # 4. Dataset-level
    dataset_section = model_section.get(dataset, {})
    result.update({k: v for k, v in dataset_section.items() if not isinstance(v, dict)})

    return result


def resolve_attack_hparams(
    attack_name: str,
    model: str,
    hparams_file: Path | None = None,
) -> dict[str, Any]:
    """Return merged blackbox-attack hyperparameters for (attack_name, model).

    Reads the ``[attack.<attack_name>]`` table (and its ``[attack.<attack_name>."<model>"]``
    per-model sub-table) from HYPERPARAMETERS.toml, merged most-specific-wins:
      1. [attack.<attack_name>]
      2. [attack.<attack_name>."<model>"]

    Reuses ``resolve_hparams``' TOML loader. These values are the paper-faithful constructor
    defaults; ``evaluate.py:_make_technique`` routes each through the attack's ``POSSUM_*`` env
    with ``os.environ.setdefault`` so an explicit env still overrides (env > this file > code
    default). Returns ``{}`` if the file, the ``[attack]`` root, or the attack section is absent.
    """
    path = hparams_file or _HPARAMS_FILE
    if not path.exists():
        return {}

    data = _load_toml(path)
    if not data:
        return {}

    section = data.get("attack", {}).get(attack_name, {})
    # Attack-level scalars, then per-model overrides (nested tables are skipped at each level).
    result: dict[str, Any] = {k: v for k, v in section.items() if not isinstance(v, dict)}
    model_section = section.get(model, {})
    result.update({k: v for k, v in model_section.items() if not isinstance(v, dict)})

    return result
