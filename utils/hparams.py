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

"""Hyperparameter registry: resolves HYPERPARAMETERS.toml for a (model_org, model, dataset) triple.

Tables merge from least to most specific: [defaults], [<model_org>], [<model_org>.<model>],
[<model_org>.<model>.<dataset>]. train.py passes the result to parser.set_defaults(), so explicit
CLI flags still win.
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
        # Fail loudly: {} would silently train on argparse defaults instead of the recipe.
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
    """Return the merged flat hyperparameter dict for (model_org, model, dataset).

    dataset is a key like "wmdp" or "bio_chem" (sorted, underscore-joined for multi-dataset runs).
    Returns {} if the file is missing; raises if no TOML parser is available.
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
    """Return blackbox-attack defaults from [attack.<name>] merged with [attack.<name>."<model>"].

    evaluate.py (_make_technique) applies them via os.environ.setdefault, so precedence is
    env var > this file > code default. Returns {} when the section is absent.
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
