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

"""Whitebox elicitation of Finke et al.'s organisms: Figures 5 and 12.

Same layout as Figure 4 (whitebox.py). Data: data/finke_whitebox.csv, one row per (organism,
technique, x), with the score as a fraction:

* wmdp_stage_c      their Stage C recipe ported to Qwen3-14B on WMDP (accuracy without the trigger),
* released_stage_c  their released Stage C organism on their own task (vulnerability recall on the
                    51 hard test items, scored by our local judge).

Each is a single organism. Fuzzing is the mean over three noise seeds. A fuzzing or pruning sweep
stops once 90% of the replies are unparsed, so those lines can end before the end of the axis.

Figure 12 compares the ported organism with RobustCoT, using the paper's Qwen2.5-14B curves
(whitebox_curves.csv): per organism seed, the mean over WMDP-Bio, -Chem and -Cyber, then the mean
and +/- 1 standard error over the three seeds.
"""

from __future__ import annotations

import csv
from collections import defaultdict

import numpy as np

import style
import whitebox

WMDP = ("bio", "chem", "cyber")
# The paper's sweep ranges, shown in full on every panel.
XRANGE = {"fuzzing": (0.0, 0.01), "pruning": (0.0, 0.9), "sft": (0, 2500), "dpo": (0, 2500)}


def load(path=style.DATA / "finke_whitebox.csv") -> dict:
    """{organism: {technique: [(x, score)]}}."""
    curves = defaultdict(lambda: defaultdict(list))
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            curves[r["organism"]][r["technique"]].append((float(r["x"]), float(r["score"])))
    return {org: {tech: sorted(points) for tech, points in techs.items()} for org, techs in curves.items()}


def robustcot_wmdp(data) -> dict:
    """{technique: [per-seed curve]}: Qwen2.5-14B RobustCoT, averaged over the three WMDP tasks."""
    out = {}
    for _, tech in whitebox.PANELS:
        per_task = [data[(tech, "qwen2.5-14b-it", task)]["obfcot"] for task in WMDP]
        seeds = []
        for curves in zip(*per_task):
            by_task = [{round(x, 9): y for x, y in curve} for curve in curves]
            xs = sorted(set.intersection(*(set(c) for c in by_task)))
            seeds.append([(x, float(np.mean([c[x] for c in by_task]))) for x in xs])
        out[tech] = seeds
    return out


def _draw_single(ax, curve, color, xscale, label) -> float:
    x = np.asarray([p[0] for p in curve]) * xscale
    y = np.asarray([p[1] for p in curve]) * 100.0
    ax.plot(x, y, color=color, linewidth=whitebox.LINEWIDTH, marker="o", markersize=4, zorder=3, label=label)
    return float(np.max(y))


def _draw_band(ax, curves, color, xscale, label) -> float:
    x, mean, err = style.aggregate(curves)
    x, mean, err = x * xscale, mean * 100.0, err * 100.0
    ax.fill_between(x, mean - err, mean + err, color=color, alpha=0.16, linewidth=0, zorder=1)
    ax.plot(x, mean, color=color, linewidth=whitebox.LINEWIDTH, zorder=3, label=label)
    return float(np.max(mean + err))


def _figure(stem, arms, ylabel, out_dir, formats):
    """arms: [(label, color, curves by technique, banded)]."""
    from matplotlib.lines import Line2D

    plt = style.apply(whitebox.PANEL_RC)
    fig, axes = plt.subplots(1, len(whitebox.PANELS), figsize=whitebox.PANEL_FIGSIZE, sharey=True)
    ymax = 0.0
    for ax, (title, tech) in zip(axes, whitebox.PANELS):
        for label, color, curves, banded in arms:
            draw = _draw_band if banded else _draw_single
            ymax = max(ymax, draw(ax, curves[tech], color, whitebox.XSCALE[tech], label))
        ax.set_title(title, pad=6)
    for ax, (_, tech) in zip(axes, whitebox.PANELS):
        whitebox.finish(ax, whitebox.XLABEL[tech], whitebox.top_of(ymax, 1.25))
        lo, hi = XRANGE[tech]
        scale = whitebox.XSCALE[tech]
        pad = 0.03 * (hi - lo) * scale
        ax.set_xlim(lo * scale - pad, hi * scale + pad)
    whitebox.fit_ylabel(fig, axes[0], ylabel)
    left, right = 0.062, 0.995
    fig.subplots_adjust(left=left, right=right, bottom=0.18, top=0.76 if len(arms) > 1 else 0.88, wspace=0.12)
    if len(arms) > 1:
        handles = [Line2D([], [], color=color, linewidth=whitebox.LINEWIDTH, label=label)
                   for label, color, _, _ in arms]
        fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5 * (left + right), 1.0),
                   ncol=len(handles), frameon=False, handlelength=2.2, handletextpad=0.5, columnspacing=2.2)
    return style.save(fig, out_dir, stem, formats)


def render(out_dir, formats) -> list:
    finke = load()
    ours = robustcot_wmdp(whitebox.load())
    paths = _figure("finke_whitebox_wmdp",
                    [("Finke et al.", style.BROWN, finke["wmdp_stage_c"], False),
                     (style.NAMES["obfcot"], style.GREEN, ours, True)],
                    whitebox.YLABEL, out_dir, formats)
    paths += _figure("finke_whitebox_released",
                     [("Finke et al. (Stage C)", style.BROWN, finke["released_stage_c"], False)],
                     "Vulnerability Recall (%)", out_dir, formats)
    return paths
