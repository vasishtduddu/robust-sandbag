"""Publication-style fine-tuning trajectory plots for D4 (SFT and DPO).

One figure per technique, styled after the shared lab template
(https://github.com/vasishtduddu/plots-figures, ``line_plots.py``): serif type,
minimalist axes with the top/right spines dropped, faint per-seed confidence
bands, and inline rounded-box labels instead of a legend.

Y-axis is LOCKED sandbag-task accuracy, not ``Delta``: ``Delta`` moves whenever the
UNLOCKED series moves, so a dip there can mean "the attack destroyed the model's
ability to answer at all" rather than "the lock broke" (the F73/F100 trap).

**But locked accuracy alone is ambiguous in exactly the same direction** (F632, and
Mark's D3 ruling 2026-09-08): a locked accuracy of 0.0 also cannot separate "the lock
held" from "the model can no longer answer anything". Seven of the nine RoBCoTDstl DPO
curves end or dip below 0.90x their own step-0 with-password accuracy, and DPO/cyber
seed 42 ends at pw = 0.0. So this figure draws BOTH series: locked accuracy on top,
with-password accuracy underneath with each seed's 0.90x gate as a dashed rule.

*** CROSS-READOUT WARNING (F702, 2026-09-08). THE TWO ARMS ARE NOT ON THE SAME
MEASUREMENT. *** Verified from the result JSONs: every obfcot cell records
``mcq_scoring_protocol = generate_parse`` and every pwd cell records ``forced_choice``,
and all 18 SFT curve step-0 values equal their cell's accepted ``test_task_nopw_acc``
under its own protocol, so the curves inherit the readout. ``sft.py``/``dpo.py``
``_evaluate`` branch live on ``genparse_enabled()``. CLAUDE.md forbids comparing a
metric across readouts, so the obfcot-vs-pwd COMPARISON in this figure is not yet
sound; the per-seed shapes WITHIN an arm are unaffected.

The fix direction is forced, not chosen: an F-lock organism answers "F", outside the
forced-choice A-D option set, so obfcot cannot be read forced-choice. Both arms must be
generate_parse, i.e. the pwd arm needs re-reading. Until then do not quote the
obfcot-vs-pwd tally.

It also draws PER-SEED lines and never a mean. The RoBCoTDstl SFT cells are bimodal
(cyber 0.3 / 53.6 / 61.7; chem 38.6 / 43.4 / 0.0), so a mean line describes no organism
that was ever trained, and averaging hid that two of three seeds finish ABOVE the
password-locking baseline. The honest claim is "ours either holds completely or fails
completely, and which one is seed-dependent", not "ours degrades more slowly".

Usage:
    python -m possum.experiments.ft_curve_plot_paper --technique sft --task bio
"""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

import numpy as np

try:
    from . import plot_style
except ImportError:  # allow `python experiments/ft_curve_plot_paper.py`
    import plot_style

# Palette and rcParams now live in ONE place (experiments/plot_style.py) so the curve and
# spider figures cannot drift apart: this dict used to be declared here and nowhere else,
# which is exactly how spider_plot.py kept matplotlib defaults. Aliased for readability.
GREEN = plot_style.GREEN
BROWN = plot_style.BROWN
_STYLE = plot_style.STYLE

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
    ax.set_ylabel("Elicitation Accuracy", labelpad=10)
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
    """ONE figure per technique: one panel per task, PER SEED, recovery of the lock gap.

    Mark 2026-09-16: only the locked panel is wanted, y-axis "Elicitation Accuracy",
    no figure-level heading. The per-task titles are kept because they identify WHICH
    panel is which task, not as a heading.

    WHAT WAS REMOVED, and what it cost. This used to carry a second row of
    with-password accuracy, each seed's ``0.90 x step-0 pw`` capability gate drawn as a
    dashed rule. That row is what let a reader tell ELICITATION from capability drift:
    a locked curve can rise because the attack worked, or because fine-tuning moved the
    model's whole capability. With row 2 gone the figure no longer shows that, so the
    capability check has to live in the text or in Table 2's utility columns.

    A task is dropped only when an ARM is missing, never when a seed is: a panel with
    only the baseline drawn would show the RoBCoTDstl colour with no line, which reads
    as "ours sits at zero here" -- the most flattering possible misreading of a cell we
    have not measured. No mean is drawn; every seed is a line, so spread is visible
    directly rather than as a band.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(_STYLE)
    have = []
    for task in tasks:
        if all(_load_seed_curves(Path(pwd_root) if org == "pwd" else Path(artifact_root),
                                 technique, model, task)
               for org, _, _, _ in _SERIES):
            have.append(task)
    if not have:
        return None

    fig, axes = plt.subplots(1, len(have), figsize=(6.6 * len(have), 5.8),
                             sharey=True, squeeze=False)
    handles = {}
    lock_max, lock_min = 0.0, 0.0
    any_void = [False]
    for col, task in enumerate(have):
        ax = axes[0][col]
        for org, label, colour, ls in _SERIES:
            root = Path(pwd_root) if org == "pwd" else Path(artifact_root)
            curves = _load_seed_curves(root, technique, model, task)
            dash = (0, (1.2, 1.4)) if ls == "dotted" else "-"
            for seed, rows in sorted(curves.items()):
                xs = [r[0] for r in rows]
                # RECOVERY, pointwise: (locked - step-0 locked) / (unlocked at the SAME
                # step - step-0 locked). Plain locked / unlocked credited pwd's step-0
                # leakage (0.08-0.28) to the attack (corrected 2026-09-16). Using the
                # same-step ceiling keeps capability drift from being rescaled away.
                l0 = rows[0][1]
                lk = [((r[1] - l0) / (r[2] - l0)) if r[2] - l0 > 0 else 0.0 for r in rows]
                lock_max = max(lock_max, max(lk))
                lock_min = min(lock_min, min(lk))
                line, = ax.plot(xs, lk, linestyle=dash, color=colour, linewidth=2.4,
                                alpha=0.9, marker="o", markersize=4, zorder=3)
                handles.setdefault(label, line)
                # VOID MARKING. Where unlocked accuracy falls below this seed's own
                # 0.90 x step-0 gate, the model no longer does the task, so a locked
                # value near 0 is a BROKEN MODEL, not a surviving lock. Grey it out so
                # it cannot be read as our best result (Mark, 2026-09-16).
                gate = 0.90 * rows[0][2]
                void = [(x, v) for x, v, r in zip(xs, lk, rows) if r[2] < gate]
                if void:
                    ax.plot([v[0] for v in void], [v[1] for v in void], "x",
                            color="0.35", markersize=11, markeredgewidth=2.2, zorder=5)
                    any_void[0] = True
        ax.axhline(1.0, ls="--", lw=1.2, color="grey", zorder=0)
        ax.set_title(_TASK_LABEL.get(task, task), pad=12)
        ax.set_xlabel("Fine-tuning steps", labelpad=10)
        ax.set_xlim(left=0)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.tick_params(axis="both", length=5, width=1.3)

    for col in range(len(have)):
        axes[0][col].set_ylim(min(lock_min * 1.14, -0.05), max(lock_max * 1.14, 1.08))
    axes[0][0].set_ylabel("Recovery (fraction of lock gap)", labelpad=10)
    axes[0][0].text(0.015, 1.0, "fully elicited",
                    transform=axes[0][0].get_yaxis_transform(),
                    fontsize=15, color="grey", va="bottom")

    if any_void[0]:
        handles["capability gate failed (model broken)"] = plt.Line2D(
            [], [], color="0.35", marker="x", linestyle="none", markersize=11,
            markeredgewidth=2.2)
    fig.legend(list(handles.values()), list(handles.keys()),
               loc="upper center", ncol=len(handles), frameon=False, fontsize=19,
               bbox_to_anchor=(0.5, 1.06))
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
