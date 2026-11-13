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

"""Whitebox elicitation curves: Figure 4 and Figures 7-9.

Each line is the mean elicitation accuracy over three organism seeds, with a +/- 1 standard error
band. Fuzzing values are already averaged over three noise seeds within each organism seed. Data:
data/whitebox_curves.csv, one row per (technique, model, task, organism, seed, x).
"""

from __future__ import annotations

import csv
import math
from collections import defaultdict

import numpy as np

import style

ORGANISMS = (("obfcot", style.GREEN), ("pwd", style.BROWN))
TASKS = tuple(style.TASKS)
MODELS = tuple(style.MODELS)

# The x values are rescaled so the tick labels stay short; the axis label carries the factor.
XSCALE = {"fuzzing": 1e3, "pruning": 1.0, "sft": 1e-3, "dpo": 1e-3}
XLABEL = {
    "fuzzing": r"Noise $\sigma$ ($\times 10^{-3}$)",
    "pruning": "Sparsity",
    "sft": r"Steps ($\times 10^{3}$)",
    "dpo": r"Steps ($\times 10^{3}$)",
}
YLABEL = "Elicitation Accuracy (%)"

# Figure 4: one model and task, one panel per technique.
PANELS = (("Fuzzing", "fuzzing"), ("Pruning", "pruning"), ("SFT", "sft"), ("DPO", "dpo"))
PANEL_RC = {"axes.titlesize": 21, "axes.labelsize": 20, "xtick.labelsize": 16,
            "ytick.labelsize": 17, "legend.fontsize": 15}
PANEL_FIGSIZE = (14.0, 4.0)
LINEWIDTH = 2.0
# The y label is stepped down until it fits the height of the panel.
YLABEL_SIZES = (19, 17, 16, 15, 14)

# Figures 7-9: every model (rows) and task (columns). SFT and DPO share one figure, told apart by
# line style.
GRIDS = (("fuzzing", ("fuzzing",)), ("pruning", ("pruning",)), ("finetuning", ("sft", "dpo")))
GRID_RC = {"axes.titlesize": 19, "axes.labelsize": 20, "xtick.labelsize": 16,
           "ytick.labelsize": 16, "legend.fontsize": 17, "figure.labelsize": 21}
GRID_FIGSIZE = (14.0, 8.2)
GRID_LINEWIDTH = 2.2
GRID_LINESTYLE = {"sft": ("SFT", "solid"), "dpo": ("DPO", (0, (5.0, 2.4)))}
GRID_XTICKS = {"fuzzing": (0, 4, 8), "pruning": (0, 0.4, 0.8), "sft": (0, 1, 2), "dpo": (0, 1, 2)}
GRID_TITLES = {**style.TASKS, "mmlu-stem": "MMLU-STEM"}


def load(path=style.DATA / "whitebox_curves.csv") -> dict:
    """{(technique, model, task): {organism: [per-seed [(x, accuracy)]]}}, accuracy as a fraction."""
    rows = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            rows[(r["technique"], r["model"], r["task"])][r["organism"]][int(r["seed"])].append(
                (float(r["x"]), float(r["accuracy"])))
    return {key: {org: [seeds[s] for s in sorted(seeds)] for org, seeds in orgs.items()}
            for key, orgs in rows.items()}


def draw(ax, curves, color, xscale, linestyle="solid", linewidth=LINEWIDTH, label=None) -> float:
    """Mean line and +/- 1 SE band in percent; returns the highest plotted value."""
    x, mean, err = style.aggregate(curves)
    x, mean, err = x * xscale, mean * 100.0, err * 100.0
    ax.fill_between(x, mean - err, mean + err, color=color, alpha=0.16, linewidth=0, zorder=1)
    ax.plot(x, mean, linestyle=linestyle, color=color, linewidth=linewidth,
            solid_joinstyle="miter", zorder=3, label=label)
    return float(np.max(mean + err))


def finish(ax, xlabel, top) -> None:
    """Axes of one panel in the 1x4 layout (also used by finke.py)."""
    from matplotlib.ticker import MaxNLocator

    ax.set_ylim(0, top)
    ax.set_xlabel(xlabel, labelpad=5)
    ax.xaxis.set_major_locator(MaxNLocator(nbins=3))
    ax.yaxis.set_major_locator(MaxNLocator(nbins=4))
    ax.tick_params(axis="both", length=4, width=1.0)
    ax.grid(axis="both", alpha=0.25, linewidth=0.8)
    ax.set_axisbelow(True)
    style.despine(ax)


def fit_ylabel(fig, ax, label=YLABEL) -> None:
    """Set the y label at the largest size that fits the panel height."""
    height = ax.get_window_extent(fig.canvas.get_renderer()).height
    for size in YLABEL_SIZES:
        text = ax.set_ylabel(label, fontsize=size)
        fig.canvas.draw()
        if text.get_window_extent(fig.canvas.get_renderer()).height <= height:
            return


def top_of(ymax, pad) -> float:
    """Y limit: padded, at least 10% so a flat-zero curve stays at zero, at most 100%."""
    return min(100.0, max(10.0, math.ceil(ymax * pad / 10.0) * 10.0))


def legend_handles(linewidth):
    from matplotlib.lines import Line2D

    return [Line2D([], [], color=color, linewidth=linewidth, linestyle="solid", label=style.NAMES[org])
            for org, color in ORGANISMS]


def render_representative(data, out_dir, formats, model="qwen2.5-14b-it", task="bio"):
    """Figure 4: fuzzing, pruning, SFT and DPO for one model and task."""
    plt = style.apply(PANEL_RC)
    fig, axes = plt.subplots(1, len(PANELS), figsize=PANEL_FIGSIZE, sharey=True)
    ymax = 0.0
    for ax, (title, tech) in zip(axes, PANELS):
        curves = data[(tech, model, task)]
        for org, color in ORGANISMS:
            ymax = max(ymax, draw(ax, curves[org], color, XSCALE[tech], label=style.NAMES[org]))
        ax.set_title(title, pad=6)
    for ax, (_, tech) in zip(axes, PANELS):
        finish(ax, XLABEL[tech], top_of(ymax, 1.25))
    fit_ylabel(fig, axes[0])
    left, right = 0.062, 0.995
    fig.subplots_adjust(left=left, right=right, bottom=0.18, top=0.76, wspace=0.12)
    fig.legend(handles=legend_handles(LINEWIDTH), loc="upper center",
               bbox_to_anchor=(0.5 * (left + right), 1.0), ncol=len(ORGANISMS), frameon=False,
               handlelength=2.2, handletextpad=0.5, columnspacing=2.2)
    return style.save(fig, out_dir, f"whitebox_representative_{model}_{task}", formats)


def render_grid(data, name, techniques, out_dir, formats):
    """Figures 7-9: one technique family, rows = models, columns = tasks."""
    from matplotlib.lines import Line2D
    from matplotlib.ticker import MaxNLocator

    plt = style.apply(GRID_RC)
    merged = len(techniques) > 1
    fig, axes = plt.subplots(len(MODELS), len(TASKS), figsize=GRID_FIGSIZE, squeeze=False,
                             sharex=True, sharey=True)
    ymax = 0.0
    for i, model in enumerate(MODELS):
        for j, task in enumerate(TASKS):
            ax = axes[i][j]
            for tech in techniques:
                linestyle = GRID_LINESTYLE[tech][1] if merged else "solid"
                for org, color in ORGANISMS:
                    ymax = max(ymax, draw(ax, data[(tech, model, task)][org], color, XSCALE[tech],
                                          linestyle, GRID_LINEWIDTH))
            ticks = GRID_XTICKS[techniques[0]]
            ax.set_xticks(ticks, [f"{t:g}" for t in ticks])
            ax.yaxis.set_major_locator(MaxNLocator(nbins=4))
            ax.tick_params(axis="both", length=4, width=1.0)
            ax.grid(axis="both", alpha=0.25, linewidth=0.8)
            ax.set_axisbelow(True)
            style.despine(ax)
            if i == 0:
                ax.set_title(GRID_TITLES[task], pad=6)
            if j == 0:
                ax.set_ylabel(style.MODELS[model], fontsize=17, labelpad=4)
    axes[0][0].set_ylim(0, top_of(ymax, 1.12))
    handles = legend_handles(GRID_LINEWIDTH)
    if merged:
        handles += [Line2D([], [], color="#6f6f6f", linewidth=GRID_LINEWIDTH, linestyle=linestyle,
                           label=label) for label, linestyle in GRID_LINESTYLE.values()]
    fig.supylabel(YLABEL, x=0.008)
    fig.supxlabel(XLABEL[techniques[0]], y=0.008)
    fig.subplots_adjust(left=0.085, right=0.996, bottom=0.095, top=0.885, wspace=0.10, hspace=0.16)
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.54, 1.0), ncol=len(handles),
               frameon=False, handlelength=2.4, handletextpad=0.5, columnspacing=2.2)
    return style.save(fig, out_dir, f"whitebox_appendix_{name}", formats, dpi=200)


def render(out_dir, formats) -> list:
    data = load()
    paths = render_representative(data, out_dir, formats)
    for name, techniques in GRIDS:
        paths += render_grid(data, name, techniques, out_dir, formats)
    return paths
