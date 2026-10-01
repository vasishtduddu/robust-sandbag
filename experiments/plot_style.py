"""Shared publication figure style: the single source of truth for EVERY possum figure.

Mark's standing instruction: do not use matplotlib defaults. Every figure we render
(SFT/DPO trajectory curves, spider/radar plots, bar plots) uses the group's shared
template at https://github.com/vasishtduddu/plots-figures, with labels made x-large and
ticks proportionally large so the figure stays legible at paper column width.

``_UPSTREAM`` below is copied verbatim from that repo's ``line_plots.py`` (the same block
appears in ``bar_plot.py``). ``STYLE`` is that base with Mark's sizing overlay applied.

WHY THIS MODULE EXISTS. The style was previously declared inline in
``ft_curve_plot_paper.py`` only, so ``spider_plot.py`` (fontsize 11/8/12/9) and
``ft_curve_plot.py`` (fontsize 8/10) silently kept matplotlib defaults, and the spider
figures were re-rendered in that unstyled form as late as 2026-09-15. Import from here;
never re-declare an rcParams dict in a figure script.

Note that a figure is STALE after a styling change even when its data is current, so
re-render and confirm the PDFs actually changed.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------------------
# Verbatim from vasishtduddu/plots-figures :: line_plots.py. Do not edit to taste; if the
# upstream template changes, update this block and say so in tables/PROVENANCE.md.
# ---------------------------------------------------------------------------------------
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

# Template palette: green = ours (robust), brown = the broken baseline.
GREEN = "#2f7a3a"
BROWN = "#a85543"

# ---------------------------------------------------------------------------------------
# Mark's sizing overlay. Upstream sizes target a full-width figure and are too small at
# column width. Ticks at 19 against labels at 24 is ~0.79x: that ratio IS the "as large or
# some proportion like that" he asked for, so keep it when adding a new figure type.
# ---------------------------------------------------------------------------------------
BASE_SIZE = 18
LABEL_SIZE = 24
TITLE_SIZE = 22
TICK_SIZE = 19
LEGEND_SIZE = 17
#: Secondary annotations (inline notes, gate rules). Deliberately below TICK_SIZE.
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
    """Drop the top/right spines: part of the template, not decoration.

    Skipped for polar axes, whose only spine is ``polar``.
    """
    if getattr(ax, "name", None) == "polar":
        return
    for side, spine in ax.spines.items():
        spine.set_visible(side in keep)


# ---------------------------------------------------------------------------------------
# Shared single-panel CURVE figure (SFT, DPO, Fuzzing, Pruning). Mark 2026-10-01: one common
# look for every whitebox curve figure, y-axis always "Elicitation Accuracy (%)", a small
# legend in the top-right corner, and the same restraint as the jailbreak boxplots.
# ---------------------------------------------------------------------------------------
CURVE_FIGSIZE = (7.6, 5.4)
CURVE_YLABEL = "Elicitation Accuracy (%)"
CURVE_LEGEND_SIZE = 14


def draw_mean_band(ax, x, mean, err, *, color, linestyle="solid", label=None) -> None:
    """Mean line with a faint +/- 1 SE band, line styles verbatim from line_plots.py."""
    ax.fill_between(x, mean - err, mean + err, color=color, alpha=0.16, linewidth=0, zorder=1)
    if linestyle == "dotted":
        ax.plot(x, mean, linestyle=(0, (1.2, 1.4)), color=color, linewidth=2.6, zorder=3, label=label)
    else:
        ax.plot(x, mean, linestyle="-", color=color, linewidth=2.6,
                solid_joinstyle="miter", zorder=3, label=label)


def finish_curve_axes(ax, *, xlabel: str, ytop: float) -> None:
    """Axis treatment shared by every curve figure.

    ``ytop`` is the highest plotted value (percent, band included). The range is padded
    so the top-right legend sits above the data, floored at 10% so a flat-zero curve
    does not autoscale into noise, and capped at 100%.
    """
    import math
    top = min(100.0, max(10.0, math.ceil(ytop * 1.3 / 10.0) * 10.0))
    ax.set_ylim(0, top)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(CURVE_YLABEL)
    ax.grid(axis="y", alpha=0.25, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", length=4, width=1)
    despine(ax)
    ax.legend(loc="upper right", fontsize=CURVE_LEGEND_SIZE, frameon=True, framealpha=0.92,
              edgecolor="#d9d9d9", fancybox=True, borderpad=0.35, labelspacing=0.3,
              handlelength=1.8, handletextpad=0.5, borderaxespad=0.4)
