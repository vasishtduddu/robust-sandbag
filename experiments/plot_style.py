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

"""Shared figure style for every figure.

Larger labels and ticks keep figures legible at column width.
"""

from __future__ import annotations

# Base style; the sizes are overridden below for column-width figures.
_UPSTREAM = {
    "font.family": "serif",
    "font.size": 14,
    "axes.labelsize": 16,
    "axes.edgecolor": "#8a8a8a",
    "axes.linewidth": 1.2,
    "xtick.color": "#666666",
    "ytick.color": "#666666",
    "axes.labelcolor": "#333333",
}

# Palette: green for the proposed organism, brown for the baseline.
GREEN = "#2f7a3a"
BROWN = "#a85543"

# Sizes for column-width figures (ticks about 0.8x the axis labels).
BASE_SIZE = 18
LABEL_SIZE = 24
TITLE_SIZE = 22
TICK_SIZE = 19
LEGEND_SIZE = 17
#: Secondary annotations, kept below TICK_SIZE.
ANNOT_SIZE = 15

STYLE = {
    **_UPSTREAM,
    "font.size": BASE_SIZE,
    "axes.labelsize": LABEL_SIZE,
    "axes.titlesize": TITLE_SIZE,
    "xtick.labelsize": TICK_SIZE,
    "ytick.labelsize": TICK_SIZE,
    "legend.fontsize": LEGEND_SIZE,
    "axes.linewidth": 1.4,
    "figure.autolayout": False,
}


def apply() -> None:
    """Install the house style globally. Call before creating a figure."""
    import matplotlib.pyplot as plt

    plt.rcParams.update(STYLE)


def despine(ax, *, keep=("left", "bottom")) -> None:
    """Hide the top and right spines (polar axes are left unchanged)."""
    if getattr(ax, "name", None) == "polar":
        return
    for side, spine in ax.spines.items():
        spine.set_visible(side in keep)


# Shared look for every whitebox curve figure (SFT, DPO, fuzzing, pruning).
CURVE_FIGSIZE = (7.6, 5.4)
CURVE_YLABEL = "Elicitation Accuracy (%)"
CURVE_LEGEND_SIZE = 14


def draw_mean_band(ax, x, mean, err, *, color, linestyle="solid", label=None) -> None:
    """Mean line with a faint +/- 1 standard-error band."""
    ax.fill_between(x, mean - err, mean + err, color=color, alpha=0.16, linewidth=0, zorder=1)
    if linestyle == "dotted":
        ax.plot(x, mean, linestyle=(0, (1.2, 1.4)), color=color, linewidth=2.6, zorder=3, label=label)
    else:
        ax.plot(x, mean, linestyle="-", color=color, linewidth=2.6,
                solid_joinstyle="miter", zorder=3, label=label)


def finish_curve_axes(ax, *, xlabel: str, ytop: float) -> None:
    """Axis treatment shared by every curve figure.

    ytop is the highest plotted value (percent, band included). The range is padded
    so the top-right legend sits above the data, floored at 10% so a flat-zero curve
    does not autoscale into noise, and capped at 100%.
    """
    import math
    top = min(100.0, max(10.0, math.ceil(ytop * 1.3 / 10.0) * 10.0))
    ax.set_ylim(0, top)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(CURVE_YLABEL)
    ax.grid(axis="both", alpha=0.25, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", length=4, width=1)
    despine(ax)
    ax.legend(loc="upper right", fontsize=CURVE_LEGEND_SIZE, frameon=True, framealpha=0.92,
              edgecolor="#d9d9d9", fancybox=True, borderpad=0.35, labelspacing=0.3,
              handlelength=1.8, handletextpad=0.5, borderaxespad=0.4)
