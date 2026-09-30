"""Representative whitebox figure for the main paper: Fuzzing, Pruning, SFT and DPO side by side.

Mark 2026-09-29: one model and one dataset (ideally 32B), the four whitebox sweeps in one row,
in the same house style as every other figure. Default cell: Qwen2.5-32B-Instruct / WMDP-Bio,
the only 32B dataset where RoBCoTDstl has all three seeds for all four techniques.

Every value is computed by the SAME code as the per-technique figures, never re-derived here:

* Fuzzing / Pruning: ``sweep_curve_plot._load_seed_curve`` (canonical 63-config noise grid,
  averaged over attacker seeds within an organism seed) and ``_to_recovery`` against the
  organism's own test (locked, unlocked) pair from its result JSON.
* SFT / DPO: ``ft_curve_plot_paper._load_seed_curves`` and the recovery used by its
  ``make_combined``: (locked_t - locked_0) / (unlocked_t - locked_0), with an x wherever the
  with-password accuracy fell below 0.90x its step-0 value (the model broke, not the lock).

y is RECOVERY (fraction of the lock gap) because the arms use different readouts (password
locking forced choice, RoBCoTDstl generate-and-parse); raw accuracy is not comparable across
them. One line per organism seed, never a mean (the FT figures' rule: seeds can be bimodal).

CAPTION CAVEAT (PROVENANCE 2026-09-17): at high noise/sparsity the password-locking curve can
plateau at its chance-level recovery because forced choice drifts a destroyed model to chance,
while generate-and-parse drops RoBCoTDstl to 0. That plateau is capability destruction, not
elicitation.

By default both arms are required in every panel (an arm-missing panel would read as "ours
sits at zero" or hide the baseline). ``--allow-missing-arm`` renders a review draft only.

Usage:
    python -m experiments.whitebox_representative_plot --out-dir tables \\
        --ours-sweep-root /scratch/markwang/possum-table3-current \\
        --ours-sft-root /scratch/markwang/possum-fig32b-ours/sft \\
        --ours-dpo-root /scratch/markwang/possum-fig32b-ours/dpo \\
        --pwd-sweep-root /scratch/markwang/possum-pwd-fc-sweeps \\
        --pwd-sft-root /scratch/markwang/possum-d4-pwd32b \\
        --pwd-dpo-root /scratch/markwang/possum-d4dpo-pwd32b
"""

from __future__ import annotations

import argparse
from pathlib import Path

from . import plot_style
from . import sweep_curve_plot as sw
from . import ft_curve_plot_paper as ft

#: (panel title, technique, kind, x label)
PANELS = [
    ("Fuzzing", "noise", "sweep", sw.TECHNIQUES["noise"][1]),
    ("Pruning", "pruning", "sweep", sw.TECHNIQUES["pruning"][1]),
    ("SFT", "sft", "ft", "Fine-tuning steps"),
    ("DPO", "dpo", "ft", "Fine-tuning steps"),
]
SERIES = sw.SERIES  # (org, label, colour, linestyle): green RoBCoTDstl, brown password locking


def _sweep_seeds(root: Path, technique: str, model: str, task: str, org: str):
    """{seed: [(x, recovery)]} from the sweep CSVs, exactly as sweep_curve_plot computes it."""
    xcol = sw.TECHNIQUES[technique][0]
    out = {}
    for seed in sw.SEEDS:
        curve = sw._load_seed_curve(sw._sweep_path(root, technique, model, task, seed), xcol)
        ceil = sw._ceiling(root, org, model, task, seed) if curve else None
        rec = sw._to_recovery(curve, *ceil) if ceil else None
        if rec:
            out[seed] = [(x, y, False) for x, y in rec]
    return out


def _ft_seeds(root: Path, technique: str, model: str, task: str):
    """{seed: [(step, recovery, capability_broken)]}, as ft_curve_plot_paper.make_combined."""
    out = {}
    for seed, rows in sorted(ft._load_seed_curves(root, technique, model, task).items()):
        l0, gate = rows[0][1], 0.90 * rows[0][2]
        out[seed] = [(s, ((lk - l0) / (pw - l0)) if pw - l0 > 0 else 0.0, pw < gate)
                     for s, lk, pw in rows]
    return out


def make_figure(model, task, roots, out_dir, allow_missing_arm=False):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plot_style.apply()
    data = {}
    for title, tech, kind, _ in PANELS:
        for org, *_ in SERIES:
            root = roots.get((org, tech))
            seeds = {}
            if root:
                seeds = (_sweep_seeds(Path(root), tech, model, task, org) if kind == "sweep"
                         else _ft_seeds(Path(root), tech, model, task))
            data[(tech, org)] = seeds
            print(f"  {title:8s} {org:7s} seeds={sorted(seeds)} root={root}")
            if not seeds and not allow_missing_arm:
                raise SystemExit(f"missing arm: {title} / {org} (root {root}); "
                                 "use --allow-missing-arm for a review draft only")

    fig, axes = plt.subplots(1, len(PANELS), figsize=(6.2 * len(PANELS), 5.6), sharey=True)
    handles, lo, hi, any_void = {}, 0.0, 1.0, False
    for ax, (title, tech, kind, xlabel) in zip(axes, PANELS):
        for org, label, colour, ls in SERIES:
            dash = (0, (1.2, 1.4)) if ls == "dotted" else "-"
            for seed, pts in sorted(data[(tech, org)].items()):
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                lo, hi = min(lo, min(ys)), max(hi, max(ys))
                line, = ax.plot(xs, ys, linestyle=dash, color=colour, linewidth=2.4, alpha=0.9,
                                marker="o", markersize=3.5, zorder=3)
                handles.setdefault(label, line)
                void = [(x, y) for x, y, broken in pts if broken]
                if void:
                    any_void = True
                    ax.plot([v[0] for v in void], [v[1] for v in void], "x", color="0.35",
                            markersize=11, markeredgewidth=2.2, zorder=5)
        ax.axhline(1.0, ls="--", lw=1.2, color="grey", zorder=0)
        ax.axhline(0.0, ls=":", lw=1.0, color="grey", zorder=0)
        ax.set_title(title, pad=12)
        ax.set_xlabel(xlabel, labelpad=10)
        ax.set_xlim(left=0)
        ax.grid(alpha=0.3)
        ax.tick_params(axis="both", length=5, width=1.3)
        plot_style.despine(ax)
    for ax in axes:
        ax.set_ylim(min(lo * 1.14, -0.05), max(hi * 1.08, 1.1))
    axes[0].set_ylabel("Recovery (fraction of lock gap)", labelpad=10)
    axes[0].text(0.015, 1.0, "fully elicited", transform=axes[0].get_yaxis_transform(),
                 fontsize=plot_style.ANNOT_SIZE, color="grey", va="bottom")
    if any_void:
        handles["capability gate failed (model broken)"] = plt.Line2D(
            [], [], color="0.35", marker="x", linestyle="none", markersize=11, markeredgewidth=2.2)
    fig.legend(list(handles.values()), list(handles.keys()), loc="upper center",
               ncol=len(handles), frameon=False, fontsize=19, bbox_to_anchor=(0.5, 1.08))
    fig.tight_layout()

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"whitebox_representative_{model}_{task}"
    fig.savefig(f"{stem}.pdf", bbox_inches="tight")
    fig.savefig(f"{stem}.png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    return Path(f"{stem}.png")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="qwen2.5-32b-it")
    ap.add_argument("--task", default="bio")
    for org in ("ours", "pwd"):
        for tech in ("sweep", "sft", "dpo"):
            ap.add_argument(f"--{org}-{tech}-root", default=None)
    ap.add_argument("--out-dir", default="tables")
    ap.add_argument("--allow-missing-arm", action="store_true")
    a = ap.parse_args()
    roots = {}
    for org, key in (("obfcot", "ours"), ("pwd", "pwd")):
        sweep = getattr(a, f"{key}_sweep_root")
        roots[(org, "noise")] = roots[(org, "pruning")] = sweep
        roots[(org, "sft")] = getattr(a, f"{key}_sft_root")
        roots[(org, "dpo")] = getattr(a, f"{key}_dpo_root")
    p = make_figure(a.model, a.task, roots, a.out_dir, a.allow_missing_arm)
    print(f"[whitebox_representative_plot] wrote {p} (and .pdf)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
