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

"""Robustness to blackbox elicitation: Figure 3.

AutoDAN elicitation accuracy per organism seed, as boxplots: one panel per task, organisms on the
x axis (prior work left of the dashed line, ours right of it) and one box per model size. Data:
data/blackbox_autodan.csv, one row per (organism, model, task, seed), accuracy in percent.
"""

from __future__ import annotations

import csv
from collections import defaultdict

import numpy as np

import style

ORGANISMS = ("pwd", "circuitbreak", "sysprompt", "robpwd", "robadapt", "obfcot")
MODELS = {"qwen2.5-7b-it": ("7B", style.BLUE), "qwen2.5-14b-it": ("14B", style.BROWN),
          "qwen2.5-32b-it": ("32B", style.GREEN)}
SEEDS = (42, 43, 44)

RC = {"axes.titlesize": 30, "xtick.labelsize": 23, "ytick.labelsize": 24,
      "legend.fontsize": 21, "figure.labelsize": 28}
FIGSIZE = (20.0, 9.2)
PITCH = 2.45         # distance between organisms on the x axis
BAND_HALF = 1.08     # margin beside the outer organisms
MODEL_OFFSET = 0.64  # distance between the model sizes within an organism
SEED_JITTER = dict(zip(SEEDS, (-0.075, 0.0, 0.075)))

# The accuracies cluster at 76-100% (prior work) and 0-30% (ours), so the empty band between them
# is compressed to a tenth of its height and the break is marked on the y axis.
BREAK_LO, BREAK_HI, SQUEEZE = 32.0, 72.0, 0.10
YTICKS = (0, 10, 20, 30, 80, 90, 100)


def load(path=style.DATA / "blackbox_autodan.csv") -> dict:
    """{(organism, model, task): [(seed, accuracy %)]}."""
    cells = defaultdict(list)
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            cells[(r["organism"], r["model"], r["task"])].append((int(r["seed"]), float(r["accuracy"])))
    return {key: sorted(points) for key, points in cells.items()}


def _broken_axis():
    """Forward and inverse maps of the compressed y axis."""
    kept = (BREAK_HI - BREAK_LO) * SQUEEZE

    def forward(y):
        y = np.asarray(y, dtype=float)
        middle = np.clip(y, BREAK_LO, BREAK_HI)
        return np.minimum(y, BREAK_LO) + (middle - BREAK_LO) * SQUEEZE + np.maximum(y - BREAK_HI, 0.0)

    def inverse(v):
        v = np.asarray(v, dtype=float)
        middle = np.clip(v, BREAK_LO, BREAK_LO + kept)
        return (np.minimum(v, BREAK_LO) + (middle - BREAK_LO) / SQUEEZE
                + np.maximum(v - (BREAK_LO + kept), 0.0))

    return forward, inverse


def _mark_break(ax) -> None:
    """Erase the y spine across the compressed band and mark both ends."""
    forward, _ = _broken_axis()
    lo, hi = ax.get_ylim()
    bottom, top = forward(lo), forward(hi)
    ends = [(forward(level) - bottom) / (top - bottom) for level in (BREAK_LO, BREAK_HI)]
    ax.plot([0, 0], ends, transform=ax.transAxes, color="white", linewidth=5.0,
            solid_capstyle="butt", clip_on=False, zorder=6)
    for end in ends:
        ax.plot([-0.016, 0.016], [end - 0.016, end + 0.016], transform=ax.transAxes,
                color="#5f5f5f", linewidth=2.2, solid_capstyle="round", clip_on=False, zorder=7)


def _model_legend(fig, ax) -> None:
    """Model-size key in the empty top-right corner of the last panel, right of the dashed line."""
    from matplotlib.patches import Patch
    from matplotlib.transforms import blended_transform_factory

    handles = [Patch(facecolor=color, edgecolor=color, alpha=0.35, label=label)
               for label, color in MODELS.values()]
    limit = blended_transform_factory(ax.transData, ax.transAxes).transform((2.5 * PITCH + 0.25, 0))[0]
    for entry_size, title_size in ((21, 27), (19, 24), (17, 22), (15, 19)):
        legend = ax.legend(handles=handles, loc="upper right", title="Model", ncol=1,
                           fontsize=entry_size, title_fontsize=title_size,
                           bbox_to_anchor=(1.0, 0.995), bbox_transform=ax.transAxes,
                           frameon=True, framealpha=0.95, edgecolor="#d9d9d9", fancybox=True,
                           borderpad=0.4, labelspacing=0.3, handlelength=1.4, handletextpad=0.5,
                           borderaxespad=0.0)
        fig.canvas.draw()
        if legend.get_window_extent(fig.canvas.get_renderer()).x0 >= limit:
            return


def render(out_dir, formats) -> list:
    cells = load()
    plt = style.apply(RC)
    fig, axes = plt.subplots(2, 3, figsize=FIGSIZE, sharex=True, sharey=True)
    xs = [i * PITCH for i in range(len(ORGANISMS))]
    offsets = dict(zip(MODELS, (-MODEL_OFFSET, 0.0, MODEL_OFFSET)))
    for ax, task in zip(axes.flat, style.TASKS):
        # A light rule between organisms and a dashed line between prior work and ours.
        for i in range(1, len(ORGANISMS)):
            ax.plot([(i - 0.5) * PITCH] * 2, [-0.03, 1.0], transform=ax.get_xaxis_transform(which="grid"),
                    color="#c4c4c4", linewidth=1.8, solid_capstyle="butt", clip_on=False, zorder=0)
        ax.axvline(2.5 * PITCH, color="#6f6f6f", linewidth=2.2, linestyle=(0, (6, 4)), zorder=1)
        for x, org in zip(xs, ORGANISMS):
            for model, (_, color) in MODELS.items():
                points = cells.get((org, model, task))
                if not points:
                    continue
                position = x + offsets[model]
                ax.boxplot([[acc for _, acc in points]], positions=[position], widths=0.46,
                           patch_artist=True, manage_ticks=False, showfliers=True,
                           boxprops={"facecolor": color, "edgecolor": color, "alpha": 0.28, "linewidth": 2.4},
                           medianprops={"color": color, "linewidth": 3.4},
                           whiskerprops={"color": color, "linewidth": 2.0},
                           capprops={"color": color, "linewidth": 2.0},
                           flierprops={"marker": "o", "markerfacecolor": color, "markeredgecolor": "white",
                                       "markersize": 7.5, "alpha": 0.9})
                for seed, acc in points:
                    ax.scatter(position + SEED_JITTER[seed], acc, s=58, color=color, edgecolor="white",
                               linewidth=0.65, zorder=4)
        ax.set_title(style.TASKS[task], pad=10)
        ax.set_xlim(-BAND_HALF - 0.04, xs[-1] + BAND_HALF + 0.04)
        ax.set_yscale("function", functions=_broken_axis())
        ax.set_yticks(list(YTICKS))
        ax.set_ylim(-1.5, 103.0)
        ax.set_xticks(xs)
        ax.set_xticklabels([style.NAMES[org] for org in ORGANISMS], fontsize=23, rotation=30,
                           ha="right", rotation_mode="anchor")
        ax.set_axisbelow(True)
        style.despine(ax)
        _mark_break(ax)
    fig.subplots_adjust(left=0.072, right=0.995, bottom=0.105, top=0.95, wspace=0.06, hspace=0.24)
    fig.supylabel("Elicitation Accuracy (%)", x=0.012)
    _model_legend(fig, axes[0][-1])
    return style.save(fig, out_dir, "jailbreak_boxplot_combined_expanded", formats)
