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

"""RobustCoT on other model families: Figure 6.

Rows are the sandbagging tasks (WMDP, MMLU, MATH), columns the whitebox techniques, and every model
family is overlaid in each panel. Each line is the mean elicitation accuracy over three organism
seeds with a +/- 1 standard error band. Data: data/families_whitebox_curves.csv, one row per
(technique, model, task, seed, x); accuracy_with_trigger, n_correct and n_total are for reference.
"""

from __future__ import annotations

import csv
import math
from collections import defaultdict

import numpy as np

import style
import whitebox

MODELS = {  # label, colour, marker
    "qwen3.8-27b-it": ("Qwen3.8-27B", "#3B6FB6", "o"),
    "qwen3.6-27b-it": ("Qwen3.6-27B (R)", "#56B4E9", "s"),
    "gemma-4-31b-it": ("Gemma-4-31B-it", "#E69F00", "^"),
    "muse-glimmer-30b": ("Muse-Glimmer-30B", "#CC79A7", "D"),
}
TASKS = (("wmdp", "WMDP"), ("mmlu", "MMLU"), ("math", "MATH"))
XTICKS = {"fuzzing": (0, 2, 4, 6, 8, 10), "pruning": (0, 0.3, 0.6, 0.9), "sft": (0, 1, 2), "dpo": (0, 1, 2)}
RC = {"axes.titlesize": 21, "axes.labelsize": 20, "xtick.labelsize": 16, "ytick.labelsize": 16,
      "legend.fontsize": 16, "figure.labelsize": 20}
FIGSIZE = (14.0, 7.4)
LINEWIDTH = 2.2
MARKERSIZE = 6.0
Y_FLOOR = 5.0  # an all-zero figure keeps a 0-5% axis


def load(path=style.DATA / "families_whitebox_curves.csv") -> dict:
    """{(technique, model, task): [per-seed [(x, accuracy)]]}."""
    rows = defaultdict(lambda: defaultdict(list))
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            rows[(r["technique"], r["model"], r["task"])][int(r["seed"])].append((float(r["x"]), float(r["accuracy"])))
    return {key: [seeds[s] for s in sorted(seeds)] for key, seeds in rows.items()}


def render(out_dir, formats) -> list:
    from matplotlib.lines import Line2D
    from matplotlib.ticker import MultipleLocator

    data = load()
    plt = style.apply(RC)
    fig, axes = plt.subplots(len(TASKS), len(whitebox.PANELS), figsize=FIGSIZE, squeeze=False,
                             sharex="col", sharey=True)
    ymax = 0.0
    for i, (task, task_label) in enumerate(TASKS):
        for j, (title, tech) in enumerate(whitebox.PANELS):
            ax = axes[i][j]
            for k, (model, (_, color, marker)) in enumerate(MODELS.items()):
                x, mean, err = style.aggregate(data[(tech, model, task)])
                x, mean, err = x * whitebox.XSCALE[tech], mean * 100.0, err * 100.0
                ax.fill_between(x, mean - err, mean + err, color=color, alpha=0.16, linewidth=0, zorder=1)
                # Staggered markers keep overlapping curves (most are exactly 0) distinguishable.
                ax.plot(x, mean, color=color, linewidth=LINEWIDTH, solid_joinstyle="miter", zorder=3,
                        marker=marker, markersize=MARKERSIZE, markeredgewidth=0.0, markevery=(k, len(MODELS)))
                ymax = max(ymax, float(np.max(mean + err)))
            if i == 0:
                ax.set_title(title, pad=8)
            if i == len(TASKS) - 1:
                ax.set_xlabel(whitebox.XLABEL[tech], labelpad=5)
            if j == 0:
                ax.set_ylabel(task_label, labelpad=8)
            ax.set_xticks(XTICKS[tech], [f"{t:g}" for t in XTICKS[tech]])
            ax.grid(axis="both", alpha=0.25, linewidth=0.8)
            ax.tick_params(axis="both", length=4, width=1.0)
            ax.set_axisbelow(True)
            style.despine(ax)
    # The next even percent above the data; the bottom sits just below 0 so markers on zero are whole.
    top = min(100.0, max(Y_FLOOR, 2.0 * math.ceil(ymax * 1.05 / 2.0)))
    axes[0][0].set_ylim(-0.03 * top, top)
    axes[0][0].yaxis.set_major_locator(MultipleLocator(2.0))
    fig.subplots_adjust(left=0.085, right=0.995, bottom=0.105, top=0.865, wspace=0.10, hspace=0.16)
    fig.supylabel(whitebox.YLABEL, x=0.008)
    handles = [Line2D([], [], color=color, linewidth=LINEWIDTH, marker=marker, markersize=MARKERSIZE + 1,
                      markeredgewidth=0.0, label=label) for label, color, marker in MODELS.values()]
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.54, 1.0), ncol=len(MODELS), frameon=False,
               handlelength=2.2, handletextpad=0.5, columnspacing=2.2)
    return style.save(fig, out_dir, "whitebox_t45_matrix", formats)
