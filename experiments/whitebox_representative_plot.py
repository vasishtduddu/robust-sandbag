"""Representative whitebox figure for the main paper: Fuzzing, Pruning, SFT and DPO side by side.

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

#: Panels of the main-paper figure. The two fine-tuning techniques share an x axis (optimizer
#: steps) and the same two arms, so they are drawn in ONE panel (Vasisht 2026-10-08) instead of
#: two near-identical ones; that buys a 1x3 layout, which is far less letterboxed than 1x4 and
#: therefore prints much larger at a fixed \textwidth.
SWEEP_PANELS = (("Fuzzing", "fuzzing"), ("Pruning", "pruning"))
FT_PANEL = ("Fine-tuning", ("sft", "dpo"))
#: Short x labels: at one-column width the full strings from whitebox_curves.XLABEL
#: ("Gaussian Noise $\\sigma$", "Pruning Sparsity") are wider than a panel and run into their
#: neighbours. The panel titles already name the technique, so the label only needs the unit.
#: The x values are also RESCALED (presentation only, the label carries the factor): a panel is
#: ~1in wide on the page, so "0.000 0.004 0.008" and "0 1000 2000" collide, while "0 4 8" and
#: "0 1 2" do not. Fuzzing's sigma runs to 0.010, so the factor that yields 0/4/8 is 1e-3.
XSCALE = {"fuzzing": 1e3, "pruning": 1.0, "sft": 1e-3, "dpo": 1e-3}
XLABEL_SHORT = {"fuzzing": r"Noise $\sigma$ ($\times 10^{-3}$)", "pruning": "Sparsity"}
FT_XLABEL = r"Steps ($\times 10^{3}$)"
#: Inside the merged panel, LINE STYLE carries the technique and COLOR still carries the arm.
FT_STYLE = {"sft": ("SFT", "solid"), "dpo": ("DPO", (0, (5.0, 2.4)))}
#: Figure-local type sizes. The shared house sizes in plot_style target a single column-width
#: panel; this figure is a multi-panel row that gets reduced by \includegraphics, so every
#: element is set well above them (same precedent as jailbreak_boxplots' combined branch).
#: Matched to the combined jailbreak boxplot (Vasisht 2026-10-08). Printed size is point size
#: times the reduction factor, and the two figures are reduced differently: this one saves at
#: 9.46in wide against the boxplot's 14.16in, so at the SAME placement width its text prints
#: 14.16/9.46 = 1.50x larger at equal point sizes. Every size here is therefore the boxplot's
#: times 0.668: titles 30 -> 20, y ticks 24 -> 16, x ticks 23 -> 15, axis labels 28 -> 19,
#: legend 21 -> 14. Re-derive these if either figure's canvas changes.
RCPARAMS = {
    "axes.titlesize": 20,
    "axes.labelsize": 19,
    "xtick.labelsize": 15,
    "ytick.labelsize": 16,
    "legend.fontsize": 14,
}
#: Sized for ONE COLUMN (Vasisht 2026-10-08). What decides on-page legibility is the reduction
#: factor, not the point sizes: at the previous 16.2in width a \columnwidth (~3.3in) placement
#: reduced everything to 20%, so a 27pt title printed at ~5.5pt. At 9.4in the same 27pt title
#: prints at ~9.5pt and the 23pt ticks at ~8pt, which is the floor for a readable figure.
FIGSIZE = (9.4, 4.0)
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


def _finish(ax, *, xlabel, top, first):
    """Axis treatment for this figure: no horizontal gridlines, few x ticks, large tick marks.

    Deliberately NOT plot_style.finish_curve_axes: that helper adds a y grid and a per-panel
    legend, both of which this figure replaces, and it is shared with the 72 single panels.
    """
    from matplotlib.ticker import MaxNLocator

    ax.set_ylim(0, top)
    ax.set_xlabel(xlabel, labelpad=5)
    # Few ticks: at one-column width a panel is ~1in wide, so four x labels would collide.
    ax.xaxis.set_major_locator(MaxNLocator(nbins=3))
    ax.yaxis.set_major_locator(MaxNLocator(nbins=4))
    ax.tick_params(axis="both", length=4, width=1.0)
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

    techniques = [tech for _, tech in SWEEP_PANELS] + list(FT_PANEL[1])
    data = {}
    for tech in techniques:
        curves = wc.arm_curves(tech, model, task)
        for org, *_ in wc.ARMS:
            print(f"  {tech:8s} {org:7s} seeds={len(curves.get(org, []))}")
            if org not in curves and not allow_missing_arm:
                raise SystemExit(f"missing arm: {tech} / {org}; use --allow-missing-arm")
        data[tech] = curves

    fig, axes = plt.subplots(1, len(SWEEP_PANELS) + 1, figsize=FIGSIZE, sharey=True)
    ytop = 0.0
    for ax, (title, tech) in zip(axes, SWEEP_PANELS):
        for org, label, color, _ in wc.ARMS:
            if org in data[tech]:
                # Color alone carries the arm here, so that line STYLE is free to carry the
                # technique in the merged panel without the two encodings contradicting.
                ytop = max(ytop, _draw(ax, data[tech], org, color, "solid",
                                       xscale=XSCALE[tech], label=label))
        ax.set_title(title, pad=6)

    ft_ax = axes[-1]
    for tech in FT_PANEL[1]:
        _, linestyle = FT_STYLE[tech]
        for org, _, color, _ in wc.ARMS:
            if org in data[tech]:
                ytop = max(ytop, _draw(ft_ax, data[tech], org, color, linestyle, xscale=XSCALE[tech]))
    ft_ax.set_title(FT_PANEL[0], pad=6)

    top = min(100.0, max(10.0, math.ceil(ytop * 1.25 / 10.0) * 10.0))
    for index, (ax, xlabel) in enumerate(
        zip(axes, [XLABEL_SHORT[tech] for _, tech in SWEEP_PANELS] + [FT_XLABEL])
    ):
        _finish(ax, xlabel=xlabel, top=top, first=index == 0)

    # The technique key lives IN the panel where line style means anything; the arm key is one
    # row above the figure, so neither legend sits on top of a curve.
    ft_ax.legend(
        handles=[Line2D([], [], color="#6f6f6f", linewidth=ARM_LINEWIDTH, linestyle=linestyle, label=name)
                 for name, linestyle in FT_STYLE.values()],
        loc="upper left", frameon=True, framealpha=0.95, edgecolor="#d9d9d9", fancybox=True,
        borderpad=0.4, labelspacing=0.3, handlelength=2.4, handletextpad=0.6, borderaxespad=0.6,
    )
    _fit_ylabel(fig, axes[0])
    # Vasisht 2026-10-08: the arm key moves INSIDE the first panel. A legend row above the
    # figure cost a band of vertical space on every panel; the Fuzzing panel's upper-left
    # corner is empty (its curves are below 30% until sigma ~0.003), so the key costs nothing.
    axes[0].legend(
        handles=[Line2D([], [], color=color, linewidth=ARM_LINEWIDTH, linestyle="solid",
                        label=FIGURE_LABELS.get(org, label))
                 for org, label, color, _ in wc.ARMS],
        loc="upper left", frameon=True, framealpha=0.95, edgecolor="#d9d9d9", fancybox=True,
        borderpad=0.3, labelspacing=0.22, handlelength=1.8, handletextpad=0.45, borderaxespad=0.35,
    )
    fig.subplots_adjust(left=0.093, right=0.995, bottom=0.175, top=0.905, wspace=0.20)

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
