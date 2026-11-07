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

"""Shared style, names and helpers for the paper figures.

Organism and task names shown in the figures are set here, so a renaming only needs this file.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

DATA = Path(__file__).resolve().parent / "data"

NAMES = {
    "pwd": "PWD",
    "circuitbreak": "CktBr",
    "sysprompt": "SysPr",
    "robpwd": "RobustPWD",
    "robadapt": "RobustAdapt",
    "obfcot": "RobustCoT",
}
TASKS = {
    "bio": "WMDP-Bio",
    "chem": "WMDP-Chem",
    "cyber": "WMDP-Cyber",
    "mmlu-prof": "MMLU-Prof",
    "mmlu-stem": "MMLU-Stem",
    "mmlu-hss": "MMLU-HSS",
}
MODELS = {"qwen2.5-7b-it": "Qwen2.5-7B", "qwen2.5-14b-it": "Qwen2.5-14B", "qwen2.5-32b-it": "Qwen2.5-32B"}

# Green for our organisms, brown for prior work.
GREEN = "#2f7a3a"
BROWN = "#a85543"
BLUE = "#3B6FB6"

STYLE = {
    "font.family": "serif",
    "font.size": 18,
    "axes.labelsize": 24,
    "axes.titlesize": 22,
    "axes.edgecolor": "#8a8a8a",
    "axes.linewidth": 1.4,
    "axes.labelcolor": "#333333",
    "xtick.color": "#666666",
    "ytick.color": "#666666",
    "xtick.labelsize": 19,
    "ytick.labelsize": 19,
    "legend.fontsize": 17,
    "figure.autolayout": False,
}


def apply(overrides: dict | None = None):
    """Install the house style and return pyplot."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    matplotlib.rc_file_defaults()
    plt.rcParams.update(STYLE)
    if overrides:
        plt.rcParams.update(overrides)
    return plt


def despine(ax) -> None:
    """Hide the top and right spines."""
    for side, spine in ax.spines.items():
        spine.set_visible(side in ("left", "bottom"))


def aggregate(curves):
    """Mean and standard error across seeds on the x values that all seeds share."""
    curves = [[(round(float(x), 9), y) for x, y in curve] for curve in curves]
    shared = sorted(set.intersection(*({x for x, _ in curve} for curve in curves))) if curves else []
    if not shared:
        return np.array([]), np.array([]), np.array([])
    values = np.vstack([[dict(curve)[x] for x in shared] for curve in curves])
    mean = values.mean(axis=0)
    if len(values) > 1:
        err = values.std(axis=0, ddof=1) / np.sqrt(len(values))
    else:
        err = np.zeros_like(mean)
    return np.asarray(shared, dtype=float), mean, err


def save(fig, out_dir: Path, stem: str, formats, dpi: int = 250) -> list[Path]:
    """Write the figure once per format and close it."""
    import matplotlib.pyplot as plt

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for fmt in formats:
        # Not Path.with_suffix: the stems contain dots ("qwen2.5-...").
        path = out_dir / f"{stem}.{fmt}"
        # No creation date in the metadata, so unchanged figures render to identical files.
        metadata = {"pdf": {"CreationDate": None}, "svg": {"Date": None}}.get(fmt)
        fig.savefig(path, bbox_inches="tight", dpi=dpi, metadata=metadata)
        paths.append(path)
    plt.close(fig)
    return paths
