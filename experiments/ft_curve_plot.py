"""D4 figure: fine-tuning attack trajectories, RoBCoTDstl versus password locking.

Plots optimizer steps on the x-axis against sandbag-task accuracy and ``Delta``
on the y-axis, for the SFT and DPO whitebox attacks. The claim the figure exists
to support is that **RoBCoTDstl degrades with a smaller, smoother slope than a
password-locked model under the same attack**, which is the evidence that it is
the more robust organism.

Reads the per-seed curve CSVs that ``elicitation/whitebox/{sft,dpo}.py`` write
into ``<artifact-root>/elicitation_results/``:

    {technique}_{model}_{task}_seed{N}_curve.csv
        step, accuracy, accuracy_pw, delta, n_correct, n_total

``accuracy`` is the LOCKED reading (sandbagging active, what the attack is trying
to raise) and ``accuracy_pw`` is the unlocked reading, so ``delta`` is the lock
that survives at that step. Seeds are aggregated as mean +/- standard error at
each common step; a seed missing a step is dropped from that step rather than
interpolated, and the step count is annotated so a partially-complete roster is
visible rather than silently averaged.

    python -m experiments.ft_curve_plot --technique sft --task bio
    python -m experiments.ft_curve_plot --technique dpo --task chem \
        --artifact-root /scratch/markwang/possum-table3-current
"""

from __future__ import annotations

import argparse
import csv
import os
import re
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev

# Organisms to compare, in plot order. RoBCoTDstl is the proposal; pwd is the
# matched prior-work baseline the claim is made against.
_SERIES = (
    ("obfcot", "RoBCoTDstl", "#1f77b4"),
    ("pwd", "Password locking", "#d62728"),
)

_CURVE_RE = re.compile(
    r"^(?P<tech>sft|dpo)_(?P<model>.+)_(?P<task>[a-z-]+)_seed(?P<seed>\d+)_curve\.csv$"
)


def _curve_dir(artifact_root: Path) -> Path:
    return artifact_root / "elicitation_results"


def _load_seed_curves(
    root: Path, technique: str, model: str, task: str
) -> dict[int, list[tuple[int, float, float | None, float | None]]]:
    """Return {seed: [(step, accuracy, accuracy_pw, delta), ...]}.

    Only the seeded filenames are matched. Older unseeded curves are ignored on
    purpose: three concurrent seeds used to share one path, so an unseeded file
    cannot be attributed to a seed and must not enter an aggregate.
    """
    out: dict[int, list[tuple[int, float, float | None, float | None]]] = {}
    directory = _curve_dir(root)
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob(f"{technique}_*_curve.csv")):
        match = _CURVE_RE.match(path.name)
        if not match or match["model"] != model or match["task"] != task:
            continue
        rows: list[tuple[int, float, float | None, float | None]] = []
        with open(path, newline="") as handle:
            for row in csv.DictReader(handle):
                def _opt(key):
                    value = row.get(key, "")
                    return float(value) if value not in ("", "None") else None
                rows.append(
                    (int(row["step"]), float(row["accuracy"]),
                     _opt("accuracy_pw"), _opt("delta"))
                )
        if rows:
            out[int(match["seed"])] = sorted(rows)
    return out


def _aggregate(curves, index: int):
    """Mean and standard error per step over seeds, for one column index."""
    by_step: dict[int, list[float]] = defaultdict(list)
    for rows in curves.values():
        for row in rows:
            value = row[index]
            if value is not None:
                by_step[row[0]].append(float(value))
    steps = sorted(by_step)
    means = [mean(by_step[s]) for s in steps]
    errs = [
        (stdev(by_step[s]) / (len(by_step[s]) ** 0.5)) if len(by_step[s]) > 1 else 0.0
        for s in steps
    ]
    counts = [len(by_step[s]) for s in steps]
    return steps, means, errs, counts


def render(technique: str, task: str, model: str, artifact_root: Path,
           out_dir: Path) -> Path | None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax_acc, ax_delta) = plt.subplots(1, 2, figsize=(11, 4.2), sharex=True)
    plotted = False

    for org, label, colour in _SERIES:
        curves = _load_seed_curves(artifact_root, technique, model, task)
        if org == "pwd":
            # pwd curves live under the Table 2 execution root, not the Table 3 one.
            alt = os.environ.get("POSSUM_PWD_ARTIFACT_ROOT")
            if alt:
                curves = _load_seed_curves(Path(alt), technique, model, task)
        if not curves:
            continue
        plotted = True
        for ax, index, ylabel in (
            (ax_acc, 1, "Sandbag-task accuracy (locked)"),
            (ax_delta, 3, r"$\Delta$ (lock surviving)"),
        ):
            steps, means, errs, counts = _aggregate(curves, index)
            if not steps:
                continue
            ax.errorbar(steps, means, yerr=errs, marker="o", ms=4, capsize=3,
                        color=colour, label=f"{label} (n={min(counts)}-{max(counts)})")
            ax.set_xlabel("Fine-tuning optimizer steps")
            ax.set_ylabel(ylabel)

    if not plotted:
        return None

    ax_acc.axhline(0.25, ls=":", lw=1, color="grey")
    ax_acc.annotate("chance", (0.01, 0.26), xycoords=("axes fraction", "data"),
                    fontsize=8, color="grey")
    for ax in (ax_acc, ax_delta):
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    fig.suptitle(
        f"{technique.upper()} attack trajectory: {model}, {task} "
        "(lower accuracy and flatter slope = more robust)",
        fontsize=10,
    )
    fig.tight_layout()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"ft_curve_{technique}_{model}_{task}"
    fig.savefig(f"{stem}.png", dpi=170, bbox_inches="tight")
    fig.savefig(f"{stem}.pdf", bbox_inches="tight")
    plt.close(fig)
    return Path(f"{stem}.png")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--technique", default="sft", choices=["sft", "dpo"])
    ap.add_argument("--task", default="bio")
    ap.add_argument("--model", default="qwen2.5-7b-it")
    ap.add_argument("--artifact-root",
                    default=os.environ.get("POSSUM_ARTIFACT_ROOT", "artifacts"))
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()

    root = Path(args.artifact_root)
    out_dir = Path(args.out_dir) if args.out_dir else root / "tables"
    written = render(args.technique, args.task, args.model, root, out_dir)
    if written is None:
        print(f"[ft_curve_plot] no seeded {args.technique} curves for "
              f"{args.model}/{args.task} under {_curve_dir(root)}; nothing plotted.")
        return 1
    print(f"[ft_curve_plot] wrote {written} (and .pdf)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
