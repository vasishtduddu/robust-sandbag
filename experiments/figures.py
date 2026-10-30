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

"""Figures from evaluation outputs.

Two subcommands:

autodan   Per-seed AutoDAN elicitation accuracy as boxplots, one panel per task, organisms on
          the x axis and one box per model. Reads the result JSONs.

whitebox  Elicitation accuracy curves under fine-tuning (SFT, DPO; x = optimizer steps),
          fuzzing (x = noise sigma) or pruning (x = sparsity), one line per organism with a
          +/- 1 standard-error band over seeds. Reads the per-run curve and sweep CSVs that
          the whitebox techniques write. Those files do not name the organism, so each organism
          is given as --arm LABEL=DIR; the orchestrator writes them to
          <artifact-root>/elicitation_results/<organism>/.

Examples (from the repository root, after pip install -e .):

    python -m wcmo.experiments.figures autodan --results results/json --out figures/
    python -m wcmo.experiments.figures whitebox --technique fuzzing \\
        --arm RobCoT=runs/elicitation_results/obfcot --arm PWD=runs/elicitation_results/pwd \\
        --models qwen2.5-14b-it --tasks bio --out figures/

The activation-separation figures are produced by ablations/activation_figure.py.
Requires matplotlib and numpy.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path

import numpy as np

try:
    from . import naming, plot_style, tables
except ImportError:  # executed as a script
    import naming
    import plot_style
    import tables

MODEL_COLORS = ("#3B6FB6", plot_style.BROWN, plot_style.GREEN, "#7a4ea3", "#c58a1c")
ARM_STYLES = ((plot_style.GREEN, "solid"), (plot_style.BROWN, "dotted"),
              ("#3B6FB6", "dashed"), ("#7a4ea3", "dashdot"))

# Default fuzzing grid (Appendix C): sigma in {0, 0.0005, ..., 0.01}, three noise seeds.
FUZZING_STDS = tuple(round(0.0005 * k, 6) for k in range(21))
FUZZING_SEEDS = (3852, 44269, 35909)

XLABEL = {"sft": r"Steps ($\times 10^3$)", "dpo": r"Steps ($\times 10^3$)",
          "fuzzing": r"Noise $\sigma$ ($\times 10^{-3}$)", "pruning": "Sparsity"}
XSCALE = {"sft": 1e-3, "dpo": 1e-3, "fuzzing": 1e3, "pruning": 1.0}


def _save(fig, out_dir: Path, stem: str) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for ext in ("pdf", "png"):
        path = out_dir / f"{stem}.{ext}"
        fig.savefig(path, bbox_inches="tight", dpi=250)
        paths.append(path)
    return paths


# ---------------------------------------------------------------------------
# AutoDAN boxplots
# ---------------------------------------------------------------------------

def autodan_points(results_dir: Path, organisms, models, tasks) -> dict:
    """{(organism, model, task): [per-seed AutoDAN accuracy in percent]}."""
    points = {}
    for org in organisms:
        for model in models:
            for task in tasks:
                blobs = tables.load_results(results_dir, org, model, task)
                values = [naming.metric_rob_jlbrk(b, task) for b in blobs.values()]
                values = [100.0 * v for v in values if v is not None]
                if values:
                    points[(org, model, task)] = values
    return points


def render_autodan(results_dir: Path, out_dir: Path, organisms, models, tasks) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    points = autodan_points(results_dir, organisms, models, tasks)
    plot_style.apply()
    ncols = min(3, len(tasks))
    nrows = (len(tasks) + ncols - 1) // ncols
    fig, axes = plt.subplots(nrows, ncols, figsize=(5.2 * ncols, 4.4 * nrows),
                             sharey=True, squeeze=False)
    width = 0.8 / max(1, len(models))
    for ax, task in zip(axes.flat, tasks):
        for i, org in enumerate(organisms):
            for j, model in enumerate(models):
                values = points.get((org, model, task))
                if not values:
                    continue
                color = MODEL_COLORS[j % len(MODEL_COLORS)]
                pos = i + (j - (len(models) - 1) / 2) * width
                ax.boxplot([values], positions=[pos], widths=width * 0.8, patch_artist=True,
                           manage_ticks=False,
                           boxprops={"facecolor": color, "edgecolor": color, "alpha": 0.3},
                           medianprops={"color": color, "linewidth": 2},
                           whiskerprops={"color": color}, capprops={"color": color},
                           flierprops={"markeredgecolor": color})
                ax.scatter([pos] * len(values), values, color=color, s=18, zorder=3)
        ax.set_title(naming.task_label(task))
        ax.set_xticks(range(len(organisms)))
        ax.set_xticklabels([naming.ORGANISMS[o].paper for o in organisms], rotation=30, ha="right")
        ax.set_ylim(-3, 103)
        ax.grid(axis="y", alpha=0.25)
        plot_style.despine(ax)
    for ax in axes.flat[len(tasks):]:
        ax.set_visible(False)
    for ax in axes[:, 0]:
        ax.set_ylabel("Elicitation Accuracy (%)")
    handles = [Patch(facecolor=MODEL_COLORS[j % len(MODEL_COLORS)], alpha=0.35,
                     label=naming.model_label(m)) for j, m in enumerate(models)]
    fig.legend(handles=handles, loc="upper center", ncol=len(models), frameon=False,
               bbox_to_anchor=(0.5, 1.02))
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    paths = _save(fig, out_dir, "autodan_boxplot")
    plt.close(fig)
    return paths


# ---------------------------------------------------------------------------
# Whitebox curves
# ---------------------------------------------------------------------------

def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def load_curves(directory: Path, technique: str, model: str, task: str) -> dict[int, list[tuple]]:
    """{organism seed: [(x, accuracy fraction), ...]} for one organism, model and task.

    Fuzzing points are first averaged over the noise seeds of the default grid.
    """
    kind = {"sft": "sft", "dpo": "dpo", "fuzzing": "noise", "pruning": "pruning"}[technique]
    suffix = "curve" if technique in ("sft", "dpo") else "sweep"
    pattern = re.compile(rf"^{kind}_{re.escape(model)}_{re.escape(task)}_seed(\d+)_{suffix}\.csv$")
    curves: dict[int, list[tuple]] = {}
    for path in sorted(directory.glob(f"{kind}_{model}_{task}_seed*_{suffix}.csv")):
        match = pattern.match(path.name)
        if not match:
            continue
        rows = _read_csv(path)
        if technique in ("sft", "dpo"):
            pts = [(float(r["step"]), float(r["accuracy"])) for r in rows]
        elif technique == "pruning":
            pts = [(float(r["sparsity"]), float(r["accuracy"])) for r in rows]
        else:
            by_std = defaultdict(list)
            for r in rows:
                std = round(float(r["noise_std"]), 6)
                if std in FUZZING_STDS and int(float(r["seed"])) in FUZZING_SEEDS:
                    by_std[std].append(float(r["accuracy"]))
            pts = [(s, float(np.mean(v))) for s, v in sorted(by_std.items())]
        if pts:
            curves[int(match.group(1))] = sorted(pts)
    return curves


def aggregate(curves: dict[int, list[tuple]]):
    """Mean and standard error over seeds at the x values every seed shares."""
    shared = set.intersection(*(set(x for x, _ in c) for c in curves.values()))
    xs = np.array(sorted(shared))
    ys = np.array([[dict(c)[x] for x in xs] for c in curves.values()])
    mean = ys.mean(axis=0)
    err = ys.std(axis=0, ddof=1) / np.sqrt(len(ys)) if len(ys) > 1 else np.zeros_like(mean)
    return xs, mean, err


def render_whitebox(technique: str, arms: list[tuple[str, Path]], models, tasks,
                    out_dir: Path) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plot_style.apply()
    plt.rcParams.update({"axes.labelsize": 18, "axes.titlesize": 18, "xtick.labelsize": 14,
                         "ytick.labelsize": 14})
    fig, axes = plt.subplots(len(models), len(tasks), figsize=(4.6 * len(tasks), 3.8 * len(models)),
                             squeeze=False, sharex=True)
    for r, model in enumerate(models):
        for c, task in enumerate(tasks):
            ax = axes[r][c]
            ytop = 0.0
            for (label, directory), (color, ls) in zip(arms, ARM_STYLES):
                curves = load_curves(directory, technique, model, task)
                if not curves:
                    continue
                xs, mean, err = aggregate(curves)
                plot_style.draw_mean_band(ax, xs * XSCALE[technique], mean * 100, err * 100,
                                          color=color, linestyle=ls, label=label)
                ytop = max(ytop, float(np.max((mean + err) * 100)))
            plot_style.finish_curve_axes(ax, xlabel=XLABEL[technique] if r == len(models) - 1 else "",
                                         ytop=ytop)
            if r == 0:
                ax.set_title(naming.task_label(task))
            ax.set_ylabel(naming.model_label(model).replace("-Instruct", "") if c == 0 else "")
            if (r, c) != (0, len(tasks) - 1) and ax.get_legend() is not None:
                ax.get_legend().remove()
    fig.supylabel("Elicitation Accuracy (%)")
    fig.tight_layout()
    paths = _save(fig, out_dir, f"whitebox_{technique}")
    plt.close(fig)
    return paths


def _parse_arm(value: str) -> tuple[str, Path]:
    label, sep, path = value.partition("=")
    if not sep:
        raise argparse.ArgumentTypeError("--arm must be LABEL=DIR")
    return label, Path(path)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("autodan", help="AutoDAN elicitation boxplots.")
    pa.add_argument("--results", type=Path, default=Path("results/json"))
    pa.add_argument("--organisms", nargs="+",
                    default=naming.TABLE_ORGANISMS["prior"] + naming.TABLE_ORGANISMS["ours"])
    pa.add_argument("--models", nargs="+", default=naming.TABLE_MODELS["ours"])
    pa.add_argument("--tasks", nargs="+", default=list(naming.QWEN_TASKS))
    pa.add_argument("--out", type=Path, default=Path("figures"))

    pw = sub.add_parser("whitebox", help="Whitebox elicitation curves.")

    pw.add_argument("--technique", required=True, choices=["sft", "dpo", "fuzzing", "pruning"])
    pw.add_argument("--arm", action="append", type=_parse_arm, required=True,
                    help="LABEL=DIR, where DIR holds that organism's elicitation_results CSVs.")
    pw.add_argument("--models", nargs="+", default=naming.TABLE_MODELS["whitebox"])
    pw.add_argument("--tasks", nargs="+", default=list(naming.QWEN_TASKS))
    pw.add_argument("--out", type=Path, default=Path("figures"))

    args = ap.parse_args()
    if args.cmd == "autodan":
        paths = render_autodan(args.results, args.out, args.organisms, args.models, args.tasks)
    else:
        paths = render_whitebox(args.technique, args.arm, args.models, args.tasks, args.out)
    for path in paths:
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
