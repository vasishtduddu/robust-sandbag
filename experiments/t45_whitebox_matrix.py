"""Other-model-families whitebox figure (paper Figure 5): RobCoT under Fuzzing, Pruning, SFT and DPO.

3 x 4 grid, rows = sandbag task (WMDP, MMLU, MATH), columns = technique, every model family overlaid in each panel
(color + marker = family). Same data, gating and aggregation as ``render_matrix`` in
``experiments/t45_whitebox_figures.py`` on ``origin/mark/tables-4-5`` (which made the one-column version); this is the
full-\\textwidth layout (Mark 2026-10-21: spread out like the expanded jailbreak boxplot, Figure 3, with gridlines).

Data: ``tables/figure_data_t45/whitebox_t45_curves.csv``, copied byte-for-byte from ``origin/mark/tables-4-5`` (cea96c0),
where ``t45obf/wb_compile.py --export`` writes it from the result files behind the published whitebox table. Do not
hand-edit it. y = locked (no-password) accuracy, mean over the three organism seeds +/- 1 SE (``sweep_curve_plot._aggregate``);
only a full three-seed curve is drawn. There is no PWD arm for these families.

    python -m experiments.t45_whitebox_matrix          # writes tables/figures_t45/whitebox_t45_matrix.{pdf,png}
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

from . import plot_style
from . import sweep_curve_plot as sw

REPO = Path(__file__).resolve().parent.parent
CURVES = REPO / "tables" / "figure_data_t45" / "whitebox_t45_curves.csv"
OUT_DIR = REPO / "tables" / "figures_t45"

MODELS = ("qwen3.8-27b-it", "qwen3.6-27b-it", "gemma-4-31b-it", "muse-glimmer-30b")
MODEL_LABEL = {"qwen3.8-27b-it": "Qwen3.8-27B", "qwen3.6-27b-it": "Qwen3.6-27B (R)",
               "gemma-4-31b-it": "Gemma-4-31B-it", "muse-glimmer-30b": "Muse-Glimmer-30B"}
#: Colors and markers as on tables-4-5. Green and brown are NOT used: in every curve figure they mean RobCoT and PWD.
MODEL_COLOR = {"qwen3.8-27b-it": "#3B6FB6", "qwen3.6-27b-it": "#56B4E9",
               "gemma-4-31b-it": "#E69F00", "muse-glimmer-30b": "#CC79A7"}
#: Per-model markers, staggered (markevery) so COINCIDENT curves stay distinguishable: most SFT/DPO curves are exactly
#: 0 at every step, and without this only the last-drawn model would be visible on the baseline.
MODEL_MARKER = {"qwen3.8-27b-it": "o", "qwen3.6-27b-it": "s", "gemma-4-31b-it": "^", "muse-glimmer-30b": "D"}
TASKS = (("wmdp", "WMDP"), ("mmlu", "MMLU"), ("math", "MATH"))
COLUMNS = (("Fuzzing", "fuzzing"), ("Pruning", "pruning"), ("SFT", "sft"), ("DPO", "dpo"))
#: x rescaled for tick legibility (presentation only; the label carries the factor), as in whitebox_representative_plot.
XSCALE = {"fuzzing": 1e3, "pruning": 1.0, "sft": 1e-3, "dpo": 1e-3}
XLABEL = {"fuzzing": r"Noise $\sigma$ ($\times 10^{-3}$)", "pruning": "Sparsity",
          "sft": r"Steps ($\times 10^{3}$)", "dpo": r"Steps ($\times 10^{3}$)"}
#: Explicit ticks (in rescaled units) so each axis is labelled at its ends: sigma 0-10, sparsity 0-0.9, steps 0-2.5k.
XTICKS = {"fuzzing": (0, 2, 4, 6, 8, 10), "pruning": (0, 0.3, 0.6, 0.9), "sft": (0, 1, 2), "dpo": (0, 1, 2)}
YLABEL = "Elicitation Accuracy (%)"
#: Shared y range never shrinks below this, so an all-zero panel reads as zero instead of autoscaling into noise.
Y_FLOOR = 5.0
#: Same canvas width and type sizes as whitebox_representative_plot (Figure 4), which is also placed at \textwidth, so
#: the two figures print their text at the same size.
RCPARAMS = {"axes.titlesize": 21, "axes.labelsize": 20, "xtick.labelsize": 16, "ytick.labelsize": 16,
            "legend.fontsize": 16, "figure.labelsize": 20}
FIGSIZE = (14.0, 7.4)
LINEWIDTH = 2.2
MARKERSIZE = 6.0


def load(path: Path = CURVES) -> dict:
    """{(technique, model, task): [per-seed [(x, accuracy)]]} from the committed CSV."""
    raw = defaultdict(lambda: defaultdict(list))
    with open(path) as handle:
        for r in csv.DictReader(handle):
            raw[(r["technique"], r["model"], r["task"])][int(r["seed_index"])].append(
                (float(r["x"]), float(r["accuracy"])))
    return {k: [seeds[i] for i in sorted(seeds)] for k, seeds in raw.items()}


def render(data: dict, out_dir: Path, name: str = "whitebox_t45_matrix"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.ticker import MultipleLocator

    plot_style.apply()
    plt.rcParams.update(RCPARAMS)
    fig, axes = plt.subplots(len(TASKS), len(COLUMNS), figsize=FIGSIZE, squeeze=False, sharex="col", sharey=True)
    missing, ymax = [], 0.0
    for i, (task, task_label) in enumerate(TASKS):
        for j, (title, tech) in enumerate(COLUMNS):
            ax = axes[i][j]
            for model in MODELS:
                seeds = data.get((tech, model, task), [])
                if len(seeds) < 3:  # seed gate: only a full three-seed curve is drawn
                    missing.append((tech, model, task))
                    continue
                x, mean, err = sw._aggregate(seeds)
                x, mean, err = x * XSCALE[tech], mean * 100.0, err * 100.0
                color = MODEL_COLOR[model]
                ax.fill_between(x, mean - err, mean + err, color=color, alpha=0.16, linewidth=0, zorder=1)
                ax.plot(x, mean, color=color, linewidth=LINEWIDTH, solid_joinstyle="miter", zorder=3,
                        marker=MODEL_MARKER[model], markersize=MARKERSIZE, markeredgewidth=0.0,
                        markevery=(MODELS.index(model), len(MODELS)))
                ymax = max(ymax, float(np.max(mean + err)))
            if i == 0:
                ax.set_title(title, pad=8)
            if i == len(TASKS) - 1:
                ax.set_xlabel(XLABEL[tech], labelpad=5)
            if j == 0:
                ax.set_ylabel(task_label, labelpad=8)
            ax.set_xticks(XTICKS[tech], [f"{t:g}" for t in XTICKS[tech]])
            ax.grid(axis="both", alpha=0.25, linewidth=0.8)
            ax.tick_params(axis="both", length=4, width=1.0)
            ax.set_axisbelow(True)
            plot_style.despine(ax)
    # Next even percent above the highest plotted point (band included), so nothing is clipped and the 2% gridlines
    # land on the top; the bottom sits a hair below 0 so a marker ON the zero line is drawn whole.
    top = min(100.0, max(Y_FLOOR, 2.0 * math.ceil(ymax * 1.05 / 2.0)))
    axes[0][0].set_ylim(-0.03 * top, top)
    axes[0][0].yaxis.set_major_locator(MultipleLocator(2.0))
    fig.subplots_adjust(left=0.085, right=0.995, bottom=0.105, top=0.865, wspace=0.10, hspace=0.16)
    fig.supylabel(YLABEL, x=0.008)
    # Model key in one row above the column titles: the full-width canvas fits all four families side by side.
    fig.legend(handles=[Line2D([], [], color=MODEL_COLOR[m], linewidth=LINEWIDTH, marker=MODEL_MARKER[m],
                               markersize=MARKERSIZE + 1, markeredgewidth=0.0, label=MODEL_LABEL[m]) for m in MODELS],
               loc="upper center", bbox_to_anchor=(0.54, 1.0), ncol=len(MODELS), frameon=False,
               handlelength=2.2, handletextpad=0.5, columnspacing=2.2)

    out_dir.mkdir(parents=True, exist_ok=True)
    for ext in ("pdf", "png"):
        fig.savefig(out_dir / f"{name}.{ext}", bbox_inches="tight", dpi=250)
    plt.close(fig)
    return out_dir / name, missing


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--curves", type=Path, default=CURVES)
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    a = ap.parse_args()
    stem, missing = render(load(a.curves), a.out_dir)
    total = len(TASKS) * len(MODELS) * len(COLUMNS)
    print(f"wrote {stem} curves={total - len(missing)}/{total}" + (f" missing={missing}" if missing else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
