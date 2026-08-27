"""Publication-style fine-tuning trajectory plots for D4 (SFT and DPO).

One figure per technique, styled after the shared lab template
(https://github.com/vasishtduddu/plots-figures, ``line_plots.py``): serif type,
minimalist axes with the top/right spines dropped, faint per-seed confidence
bands, and inline rounded-box labels instead of a legend.

Y-axis is LOCKED sandbag-task accuracy, deliberately, not ``Delta``. ``Delta``
moves whenever the UNLOCKED series moves, so a dip there can mean "the attack
destroyed the model's ability to answer at all" rather than "the lock broke"
(the F73/F100 reporting trap; measured on DPO/bio, where RoBCoTDstl's Delta dips
to 0.41 at step 500 while its locked accuracy never leaves 0.000). Locked
accuracy has no such ambiguity: it is the number the robustness claim is about.

Usage:
    python -m possum.experiments.ft_curve_plot_paper --technique sft --task bio
"""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

import numpy as np

# Template palette: green = ours (robust), brown = the broken baseline.
GREEN = "#2f7a3a"
BROWN = "#a85543"

# Supervisor request: labels x-large, ticks proportionally large, so the figure
# stays legible at column width in the paper.
_STYLE = {
    "font.family": "serif",
    "font.size": 18,
    "axes.labelsize": 24,
    "axes.titlesize": 22,
    "xtick.labelsize": 19,
    "ytick.labelsize": 19,
    "axes.edgecolor": "#8a8a8a",
    "axes.linewidth": 1.4,
    "xtick.color": "#666666",
    "ytick.color": "#666666",
    "axes.labelcolor": "#333333",
}

_TASK_LABEL = {
    "bio": "WMDP-Bio",
    "chem": "WMDP-Chem",
    "cyber": "WMDP-Cyber",
    "mmlu-prof": "MMLU-Prof",
    "mmlu-stem": "MMLU-Stem",
    "mmlu-hss": "MMLU-HSS",
}

_SERIES = [
    ("obfcot", "RoBCoTDstl", GREEN, "solid"),
    ("pwd", "Password locking", BROWN, "dotted"),
]


def _load_seed_curves(root: Path, technique: str, model: str, task: str):
    """Return {seed: [(step, locked, unlocked), ...]} for every seed present."""
    out: dict[int, list[tuple[int, float, float]]] = {}
    for seed in (42, 43, 44):
        p = root / "elicitation_results" / f"{technique}_{model}_{task}_seed{seed}_curve.csv"
        if not p.exists():
            continue
        rows = []
        with open(p) as fh:
            for r in csv.DictReader(fh):
                try:
                    rows.append((int(r["step"]), float(r["accuracy"]),
                                 float(r["accuracy_pw"])))
                except (KeyError, TypeError, ValueError):
                    continue
        if rows:
            out[seed] = rows
    return out


def _aggregate(curves):
    """Mean and standard error of LOCKED accuracy at each shared step."""
    steps = sorted({s for rows in curves.values() for s, _, _ in rows})
    xs, means, errs = [], [], []
    for st in steps:
        vals = [lk for rows in curves.values() for s, lk, _ in rows if s == st]
        if not vals:
            continue
        arr = np.asarray(vals, dtype=float) * 100.0
        xs.append(st)
        means.append(arr.mean())
        errs.append(arr.std(ddof=1) / np.sqrt(len(arr)) if len(arr) > 1 else 0.0)
    return np.asarray(xs), np.asarray(means), np.asarray(errs)


def make_plot(technique, task, model, artifact_root, pwd_root, out_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(_STYLE)
    fig, ax = plt.subplots(figsize=(8.4, 5.8))

    plotted, ymax = False, 0.0
    for org, label, colour, ls in _SERIES:
        root = Path(pwd_root) if org == "pwd" else Path(artifact_root)
        curves = _load_seed_curves(root, technique, model, task)
        if not curves:
            continue
        x, mean, err = _aggregate(curves)
        if not len(x):
            continue
        plotted = True
        ymax = max(ymax, float((mean + err).max()))
        ax.fill_between(x, mean - err, mean + err, color=colour, alpha=0.18,
                        linewidth=0, zorder=1)
        dash = (0, (1.2, 1.4)) if ls == "dotted" else "-"
        ax.plot(x, mean, linestyle=dash, color=colour, linewidth=2.8,
                solid_joinstyle="miter", zorder=3,
                label=f"{label} (n={len(curves)})")

    if not plotted:
        plt.close(fig)
        return None

    # Chance line for the 4-choice MCQA readout.
    ax.axhline(25.0, ls=":", lw=1.2, color="grey", zorder=0)
    ax.text(0.015, 25.0, "chance", transform=ax.get_yaxis_transform(),
            fontsize=15, color="grey", va="bottom")

    ax.set_xlabel("Fine-tuning optimizer steps", labelpad=12)
    ax.set_ylabel("Locked accuracy (%)", labelpad=10)
    ax.set_ylim(0, max(ymax * 1.28, 30.0))
    ax.set_xlim(left=0)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(axis="both", length=5, width=1.3)

    # Inline boxed labels, template-style, placed at each series' right end.
    for org, label, colour, ls in _SERIES:
        root = Path(pwd_root) if org == "pwd" else Path(artifact_root)
        curves = _load_seed_curves(root, technique, model, task)
        if not curves:
            continue
        x, mean, _ = _aggregate(curves)
        if not len(x):
            continue
        ly = min(mean[-1] + max(ymax * 0.11, 3.0), ax.get_ylim()[1] * 0.93)
        ax.text(x[-1] * 0.60, ly, label,
                color=colour, fontsize=17, ha="center", va="center",
                bbox=dict(boxstyle="round,pad=0.36,rounding_size=0.5",
                          facecolor="white", edgecolor=colour, linewidth=1.5,
                          linestyle=":" if ls == "dotted" else "-"),
                zorder=5)

    fig.tight_layout()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"ft_paper_{technique}_{model}_{task}"
    fig.savefig(f"{stem}.pdf", bbox_inches="tight")
    fig.savefig(f"{stem}.png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    return Path(f"{stem}.png")



def make_combined(technique, model, artifact_root, pwd_root, out_dir,
                  tasks=("bio", "chem", "cyber")):
    """ONE figure per technique, one panel per task, lab-template styling.

    Mark's request: a single SFT figure and a single DPO figure rather than six
    per-task files, with labels x-large and ticks proportionally large so the
    panel stays legible at paper column width. Panels share a y-axis because the
    comparison the figure exists to make is across ORGANISMS within a task, and a
    shared axis keeps that comparison honest across panels too.

    A task with no data for either arm is dropped rather than drawn empty, so
    the DPO figure renders two panels while its cyber arm is blocked (F279).
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(_STYLE)
    # BOTH arms required, deliberately. A panel with only the baseline drawn
    # would show the legend's RoBCoTDstl colour with no line, which a reader
    # naturally reads as "ours sits at zero here" -- the most flattering
    # possible misreading of a cell we simply have not measured. DPO/cyber is
    # blocked by F279, so the DPO figure renders two panels until it lands.
    have = []
    for task in tasks:
        present = 0
        for org, _, _, _ in _SERIES:
            root = Path(pwd_root) if org == "pwd" else Path(artifact_root)
            if _load_seed_curves(root, technique, model, task):
                present += 1
        if present == len(_SERIES):
            have.append(task)
    if not have:
        return None

    fig, axes = plt.subplots(1, len(have), figsize=(6.6 * len(have), 5.9),
                             sharey=True)
    if len(have) == 1:
        axes = [axes]
    ymax = 0.0
    handles = {}
    for ax, task in zip(axes, have):
        for org, label, colour, ls in _SERIES:
            root = Path(pwd_root) if org == "pwd" else Path(artifact_root)
            curves = _load_seed_curves(root, technique, model, task)
            if not curves:
                continue
            x, mean, err = _aggregate(curves)
            if not len(x):
                continue
            ymax = max(ymax, float((mean + err).max()))
            ax.fill_between(x, mean - err, mean + err, color=colour, alpha=0.18,
                            linewidth=0, zorder=1)
            dash = (0, (1.2, 1.4)) if ls == "dotted" else "-"
            line, = ax.plot(x, mean, linestyle=dash, color=colour, linewidth=3.0,
                            solid_joinstyle="miter", zorder=3)
            handles.setdefault(label, line)
        ax.axhline(25.0, ls=":", lw=1.2, color="grey", zorder=0)
        ax.set_title(_TASK_LABEL.get(task, task), pad=12)
        ax.set_xlabel("Fine-tuning steps", labelpad=10)
        ax.set_xlim(left=0)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.tick_params(axis="both", length=5, width=1.3)

    axes[0].set_ylabel("Locked accuracy (%)", labelpad=10)
    axes[0].set_ylim(0, max(ymax * 1.30, 30.0))
    axes[0].text(0.015, 25.0, "chance", transform=axes[0].get_yaxis_transform(),
                 fontsize=15, color="grey", va="bottom")
    fig.legend(handles.values(), handles.keys(), loc="upper center",
               ncol=len(handles), frameon=False, fontsize=21,
               bbox_to_anchor=(0.5, 1.10))
    fig.tight_layout()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"ft_paper_{technique}_{model}"
    fig.savefig(f"{stem}.pdf", bbox_inches="tight")
    fig.savefig(f"{stem}.png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    return Path(f"{stem}.png")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--technique", default="sft", choices=["sft", "dpo"])
    ap.add_argument("--task", default="bio")
    ap.add_argument("--model", default="qwen2.5-7b-it")
    ap.add_argument("--artifact-root",
                    default=os.environ.get("POSSUM_ARTIFACT_ROOT", "."))
    ap.add_argument("--pwd-artifact-root",
                    default=os.environ.get("POSSUM_PWD_ARTIFACT_ROOT", ""))
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--combined", action="store_true",
                    help="One figure per technique with a panel per task "
                         "(the paper form) instead of one figure per task.")
    a = ap.parse_args()
    pwd_root = a.pwd_artifact_root or a.artifact_root
    out = a.out_dir or str(Path(a.artifact_root) / "tables")
    if a.combined:
        p = make_combined(a.technique, a.model, a.artifact_root, pwd_root, out)
    else:
        p = make_plot(a.technique, a.task, a.model, a.artifact_root, pwd_root, out)
    print(f"[ft_curve_plot_paper] wrote {p} (and .pdf)" if p
          else "[ft_curve_plot_paper] no curves found; nothing written")
    return 0 if p else 1


if __name__ == "__main__":
    raise SystemExit(main())
