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
    except ImportError:
        return {}


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
