"""Representative whitebox figure for the main paper: Fuzzing, Pruning, SFT and DPO side by side (1x4).

Mark 2026-09-29: one model and one dataset, the four whitebox techniques in one row, in the same house style as every
other figure. Mark 2026-10-03: the main-paper cell is Qwen2.5-14B-Instruct / WMDP-Bio (both arms complete, 3 seeds, on all
four techniques). Note for the caption: the 14B SFT RoBCoTDstl arm is the DL-c organisms (whitebox_curves.FT_ROOTS), as in
the published 14B SFT panels.

Every panel is the SAME data and style as the per-technique figures (``whitebox_curves``, Mark
2026-10-01): data roots from ``whitebox_curves`` (``FT_ROOTS``, ``SWEEP_OURS``, ``SWEEP_PWD``),
y = raw locked-task accuracy (%), mean over organism seeds +/- 1 SE, x labels and legend labels
from ``whitebox_curves``. Nothing is re-derived here.

CAPTION CAVEAT (as for the per-technique figures): each arm is read in its own protocol
(RoBCoTDstl generate-and-parse, password locking forced choice), so the password-locking curve
starts at its own nonzero locked floor, and at high noise/sparsity forced choice drifts a
destroyed model toward chance (~25%) while generate-and-parse drops it to 0.

By default both arms are required in every panel. ``--allow-missing-arm`` renders a panel with
the arms that exist (Mark 2026-10-02: release with the PWD DPO arm still pending).

Usage:
    python -m experiments.whitebox_representative_plot --out-dir tables/representative
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np

from . import plot_style
from . import sweep_curve_plot as sw
from . import whitebox_curves as wc

#: Figure-local arm label, matching jailbreak_boxplots.FIGURE_LABELS: the generated tables in
#: tables/ still print RoBCoTDstl via \\providecommand, so naming.ORGANISMS is not renamed here.
FIGURE_LABELS = {"obfcot": "RobCoT", "pwd": "PWD"}

#: Panels of the main-paper figure, one technique each (Mark 2026-10-21: SFT and DPO split back
#: into their own panels, 1x4 at full \textwidth; supersedes the merged 1x3 of 2026-10-08).
PANELS = (("Fuzzing", "fuzzing"), ("Pruning", "pruning"), ("SFT", "sft"), ("DPO", "dpo"))
#: Short x labels: at one-column width the full strings from whitebox_curves.XLABEL
#: ("Gaussian Noise $\\sigma$", "Pruning Sparsity") are wider than a panel and run into their
#: neighbours. The panel titles already name the technique, so the label only needs the unit.
#: The x values are also RESCALED (presentation only, the label carries the factor): a panel is
#: ~1in wide on the page, so "0.000 0.004 0.008" and "0 1000 2000" collide, while "0 4 8" and
#: "0 1 2" do not. Fuzzing's sigma runs to 0.010, so the factor that yields 0/4/8 is 1e-3.
XSCALE = {"fuzzing": 1e3, "pruning": 1.0, "sft": 1e-3, "dpo": 1e-3}
XLABEL_SHORT = {
    "fuzzing": r"Noise $\sigma$ ($\times 10^{-3}$)",
    "pruning": "Sparsity",
    "sft": r"Steps ($\times 10^{3}$)",
    "dpo": r"Steps ($\times 10^{3}$)",
}
#: Figure-local type sizes, matched to the expanded jailbreak boxplot (Figure 3), which is also
#: placed at \textwidth. Printed size is point size times the reduction factor: that figure saves
#: at 20in wide, this one at 14in, so at equal placement width this one's text prints 20/14 =
#: 1.43x larger at equal point sizes. Every size here is therefore the boxplot's times 0.7:
#: titles 30 -> 21, y ticks 24 -> 17, x ticks 23 -> 16, axis labels 28 -> 20, legend 21 -> 15.
#: Re-derive these if either figure's canvas changes.
RCPARAMS = {
    "axes.titlesize": 21,
    "axes.labelsize": 20,
    "xtick.labelsize": 16,
    "ytick.labelsize": 17,
    "legend.fontsize": 15,
}
#: Sized for the full \textwidth (Mark 2026-10-21): four panels in one row, each ~1.3in wide on
#: the page, and short enough that the row does not dominate the page.
FIGSIZE = (14.0, 4.0)
ARM_LINEWIDTH = 2.0
#: The full metric name (Vasisht 2026-10-08). At the body label size it is taller than its own
#: panel, so ``_fit_ylabel`` steps the size down until it fits rather than abbreviating it.
YLABEL = "Elicitation Accuracy (%)"
YLABEL_SIZES = (19, 17, 16, 15, 14)


def _draw(ax, curves, org, color, linestyle, xscale=1.0, label=None):
    """Mean +/- 1 SE for one arm, drawn exactly as plot_style.draw_mean_band but heavier.

    The shared helper hardcodes linewidth 2.6 and is used by all 77 other curve figures, so the
    weight is overridden here rather than there (editing plot_style would make every committed
    figure stale).
    """
    x, mean, err = sw._aggregate(curves[org])
    x, mean, err = np.asarray(x) * xscale, mean * 100.0, err * 100.0
    ax.fill_between(x, mean - err, mean + err, color=color, alpha=0.16, linewidth=0, zorder=1)
    ax.plot(x, mean, linestyle=linestyle, color=color, linewidth=ARM_LINEWIDTH,
            solid_joinstyle="miter", zorder=3, label=label)
    return float(np.max(mean + err))


def _finish(ax, *, xlabel, top):
    """Axis treatment for this figure: faint x and y gridlines, few ticks, large tick marks.

    Deliberately NOT plot_style.finish_curve_axes: that helper adds a per-panel legend, which
    this figure replaces, and it is shared with the 72 single panels.
    """
    from matplotlib.ticker import MaxNLocator

    ax.set_ylim(0, top)
    ax.set_xlabel(xlabel, labelpad=5)
    # Few ticks: at one-column width a panel is ~1in wide, so four x labels would collide.
    ax.xaxis.set_major_locator(MaxNLocator(nbins=3))
    ax.yaxis.set_major_locator(MaxNLocator(nbins=4))
    ax.tick_params(axis="both", length=4, width=1.0)
    ax.grid(axis="both", alpha=0.25, linewidth=0.8)
    ax.set_axisbelow(True)
    plot_style.despine(ax)


def _fit_ylabel(fig, ax) -> None:
    """Set the full y label at the largest size that still fits the panel's height.

    "Elicitation Accuracy (%)" is a long string rotated into a panel that is only ~2.9in tall at
    this canvas size, so at the body label size it overruns the axis. Measuring beats guessing:
    an overrunning label pushes into the title and the x labels once bbox_inches="tight" expands
    the canvas.
    """
    height = ax.get_window_extent(fig.canvas.get_renderer()).height
    for size in YLABEL_SIZES:
        label = ax.set_ylabel(YLABEL, fontsize=size)
        fig.canvas.draw()
        if label.get_window_extent(fig.canvas.get_renderer()).height <= height:
            return


def make_figure(model, task, out_dir, allow_missing_arm=False):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    plot_style.apply()
    plt.rcParams.update(RCPARAMS)

    data = {}
    for _, tech in PANELS:
        curves = wc.arm_curves(tech, model, task)
        for org, *_ in wc.ARMS:
            print(f"  {tech:8s} {org:7s} seeds={len(curves.get(org, []))}")
            if org not in curves and not allow_missing_arm:
                raise SystemExit(f"missing arm: {tech} / {org}; use --allow-missing-arm")
        data[tech] = curves

    fig, axes = plt.subplots(1, len(PANELS), figsize=FIGSIZE, sharey=True)
    ytop = 0.0
    for ax, (title, tech) in zip(axes, PANELS):
        for org, label, color, _ in wc.ARMS:
            if org in data[tech]:
                ytop = max(ytop, _draw(ax, data[tech], org, color, "solid",
                                       xscale=XSCALE[tech], label=label))
        ax.set_title(title, pad=6)

    top = min(100.0, max(10.0, math.ceil(ytop * 1.25 / 10.0) * 10.0))
    for ax, (_, tech) in zip(axes, PANELS):
        _finish(ax, xlabel=XLABEL_SHORT[tech], top=top)

    _fit_ylabel(fig, axes[0])
    # Mark 2026-10-21: the arm key is one row above the panel titles, centred over the panels,
    # as in Figures 5 and 8-10 (supersedes the in-panel key of Vasisht 2026-10-08).
    left, right = 0.062, 0.995
    fig.subplots_adjust(left=left, right=right, bottom=0.18, top=0.76, wspace=0.12)
    fig.legend(
        handles=[Line2D([], [], color=color, linewidth=ARM_LINEWIDTH, linestyle="solid",
                        label=FIGURE_LABELS.get(org, label))
                 for org, label, color, _ in wc.ARMS],
        loc="upper center", bbox_to_anchor=(0.5 * (left + right), 1.0), ncol=len(wc.ARMS),
        frameon=False, handlelength=2.2, handletextpad=0.5, columnspacing=2.2,
    )

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"whitebox_representative_{model}_{task}"
    for ext in ("pdf", "png"):
        fig.savefig(out_dir / f"{stem.name}.{ext}", bbox_inches="tight", dpi=250)
    plt.close(fig)
    return out_dir / f"{stem.name}.png"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="qwen2.5-14b-it")
    ap.add_argument("--task", default="bio")
    ap.add_argument("--out-dir", default="tables/representative")
    ap.add_argument("--allow-missing-arm", action="store_true")
    ap.add_argument("--snapshot", action="store_true",
                    help="read the committed tables/figure_data/whitebox_curves.csv (no cluster access needed)")
    a = ap.parse_args()
    if a.snapshot:
        import os
        os.environ["POSSUM_FIGURE_DATA"] = "snapshot"
    p = make_figure(a.model, a.task, a.out_dir, a.allow_missing_arm)
    print(f"[whitebox_representative_plot] wrote {p} (and .pdf)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
