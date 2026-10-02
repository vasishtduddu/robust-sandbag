"""Representative whitebox figure for the main paper: Fuzzing, Pruning, SFT and DPO side by side.

Mark 2026-09-29: one model and one dataset (ideally 32B), the four whitebox techniques in one row,
in the same house style as every other figure. Default cell: Qwen2.5-32B-Instruct / WMDP-Bio,
the only 32B dataset where RoBCoTDstl has all three seeds for all four techniques.

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
from pathlib import Path

import numpy as np

from . import plot_style
from . import sweep_curve_plot as sw
from . import whitebox_curves as wc

PANELS = [("Fuzzing", "fuzzing"), ("Pruning", "pruning"), ("SFT", "sft"), ("DPO", "dpo")]


def make_figure(model, task, out_dir, allow_missing_arm=False):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plot_style.apply()
    data = {}
    for title, tech in PANELS:
        curves = wc.arm_curves(tech, model, task)
        for org, *_ in wc.ARMS:
            print(f"  {title:8s} {org:7s} seeds={len(curves.get(org, []))}")
            if org not in curves and not allow_missing_arm:
                raise SystemExit(f"missing arm: {title} / {org}; use --allow-missing-arm")
        data[tech] = curves

    w, h = plot_style.CURVE_FIGSIZE
    fig, axes = plt.subplots(1, len(PANELS), figsize=(w * len(PANELS), h), sharey=True)
    ytop = 0.0
    for ax, (title, tech) in zip(axes, PANELS):
        for org, label, color, ls in wc.ARMS:
            if org not in data[tech]:
                continue
            x, mean, err = sw._aggregate(data[tech][org])
            mean, err = mean * 100.0, err * 100.0
            plot_style.draw_mean_band(ax, x, mean, err, color=color, linestyle=ls, label=label)
            ytop = max(ytop, float(np.max(mean + err)))
        ax.set_title(title)
    for ax, (title, tech) in zip(axes, PANELS):
        plot_style.finish_curve_axes(ax, xlabel=wc.XLABEL[tech], ytop=ytop)
        if ax is not axes[0]:
            ax.set_ylabel("")
            leg = ax.get_legend()
            if leg is not None:
                leg.remove()
    fig.tight_layout()

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"whitebox_representative_{model}_{task}"
    for ext in ("pdf", "png"):
        fig.savefig(out_dir / f"{stem.name}.{ext}", bbox_inches="tight", dpi=250)
    plt.close(fig)
    return out_dir / f"{stem.name}.png"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="qwen2.5-32b-it")
    ap.add_argument("--task", default="bio")
    ap.add_argument("--out-dir", default="tables/representative")
    ap.add_argument("--allow-missing-arm", action="store_true")
    a = ap.parse_args()
    p = make_figure(a.model, a.task, a.out_dir, a.allow_missing_arm)
    print(f"[whitebox_representative_plot] wrote {p} (and .pdf)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
