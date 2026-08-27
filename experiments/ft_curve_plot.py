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


# Sentinel column index for a DERIVED series rather than a stored one.
# 4 = elicitation fraction e(t) = locked / unlocked.
ELICITATION_FRACTION = 4


def _aggregate(curves, index: int):
    """Mean and standard error per step over seeds, for one column index.

    ``index == ELICITATION_FRACTION`` is derived, not read: it is
    ``locked / unlocked`` computed PER SEED and only then averaged. Averaging
    the ratio (not taking the ratio of the averages) matters because the two
    arms move independently across seeds, so a ratio of means is biased by
    whichever seed happens to have the largest ceiling.
    """
    by_step: dict[int, list[float]] = defaultdict(list)
    for rows in curves.values():
        for row in rows:
            if index == ELICITATION_FRACTION:
                locked, unlocked = row[1], row[2]
                if unlocked in (None, 0) or locked is None:
                    continue
                value = float(locked) / float(unlocked)
            else:
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

    # THREE panels, not two. F73/F100: the Delta panel ALONE is misleading,
    # because Delta = unlocked - locked moves when the UNLOCKED series moves.
    # Measured on the DPO/bio figure: RoBCoTDstl's Delta dips from 0.61 to 0.41
    # at step 500 while its locked accuracy is flat at exactly 0.000 -- the dip
    # is entirely a temporary collapse of the unlocked reading, i.e. the attack
    # damaged the model's ability to answer at all, not the lock. A reader
    # cannot tell those apart without seeing the unlocked series, so plot it.
    # A FOURTH panel: the ceiling-normalised elicitation fraction.
    # The locked panel alone is not apples-to-apples ACROSS organisms, because
    # the two organisms hide different amounts of capability. Measured on
    # cyber: RoBCoTDstl's step-0 Delta is 0.62 while password locking's is 0.30,
    # so the same FRACTION recovered shows up as twice the raw locked accuracy
    # for RoBCoTDstl and reads as a robustness loss that is really a difference
    # in how much there was to find. e(t) = locked / unlocked asks the auditor's
    # question instead: what share of the model's own live capability did this
    # attack recover. Report both; they answer different questions, and absolute
    # locked accuracy is still the right read for absolute risk.
    fig, (ax_acc, ax_unlocked, ax_delta, ax_elicit) = plt.subplots(
        1, 4, figsize=(20.5, 4.2), sharex=True
    )
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
            (ax_unlocked, 2, "Sandbag-task accuracy (unlocked)"),
            (ax_delta, 3, r"$\Delta$ (lock surviving)"),
            (ax_elicit, ELICITATION_FRACTION,
             "Elicitation fraction  locked / unlocked"),
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
    ax_unlocked.set_title("capability ceiling (read Delta against this)", fontsize=8)
    ax_elicit.axhline(1.0, ls="--", lw=1, color="grey")
    ax_elicit.annotate("fully elicited", (0.01, 1.02),
                       xycoords=("axes fraction", "data"), fontsize=8, color="grey")
    ax_elicit.set_title("ceiling-normalised (cross-organism comparable)", fontsize=8)
    for ax in (ax_acc, ax_unlocked, ax_delta, ax_elicit):
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
