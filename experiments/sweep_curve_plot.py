"""Fuzzing (noise) and Pruning sweep curves: RoBCoTDstl vs password locking.

Mark's request (2026-09-16). These are the whitebox analogue of the SFT/DPO
trajectory figures: instead of optimizer steps on the x-axis, the x-axis is the
hyperparameter each whitebox attack sweeps.

  Fuzzing  -> ``noise_std``, 21 points from 0.0 to 0.01 (step 0.0005), each
              evaluated at 3 attacker seeds (63 rows per organism seed).
  Pruning  -> ``sparsity``, 15 points from 0.0 to 0.9.

y is the attack's accuracy on the sandbag task at that hyperparameter value, the
same quantity the Table 3 cell reports as its best-over-sweep. A LOWER curve means
the organism resisted, so RoBCoTDstl sitting below password locking is the claim
these figures exist to show.

WHERE THE DATA COMES FROM, and why no new compute was needed
------------------------------------------------------------
``elicitation/whitebox/pruning.py`` and ``noise.py`` each write a per-configuration
receipt next to the run:

    <root>/elicitation_results/<technique>_<model>_<dataset>_seed<N>_sweep.csv

with an ``accuracy`` column per swept configuration. The result JSON keeps only the
BEST configuration: ``evaluate.py:993`` warns that the persist allowlist DROPPED
``accuracy``/``best_accuracy``, so the curve is recoverable ONLY from these CSVs.
Verified 2026-09-16: the per-root sweep maximum reproduces the published Table 3
elicitation value exactly (e.g. 14B/bio s43 noise 0.011719, 7B/mmlu-stem s44
pruning 0.204082), which is what ties the curve to the reported cell.

THE ARM IS CHOSEN BY ROOT, NOT BY FILENAME
------------------------------------------
The CSV stem carries model, dataset and seed but NOT the organism, exactly as for
the SFT/DPO curve CSVs (see ``ft_curve_plot_paper.py:124``). Two organisms can
therefore produce identically named files in different roots. Confirmed by reading
the producing job's log, not by inference:

  * ``S:possum-table3-current`` and ``S:possum-mmlu-merge`` -> obfcot (RoBCoTDstl).
  * ``P:artifacts``                                        -> pwd. Its log
    ``t4base_pwd_qwen2.5-7b-it_bio_s42.log`` opens "=== pwd/bio/s42 ===" and loads
    ``artifacts/checkpoints/pwd_qwen2.5-7b-it_bio/run01``.

Pass the roots explicitly. A wrong root silently mislabels an arm and would invert
the figure's claim, so ``--strict`` refuses to plot an arm whose root holds no CSV.

Both arms score the same held-out test split (n_total 256 bio / 83 chem / 399
cyber, matching ``test_elicitation_n``), so the curves are directly comparable.
"""

from __future__ import annotations

import argparse
import csv
import os
from collections import defaultdict
from pathlib import Path

import numpy as np

from . import plot_style

SEEDS = (42, 43, 44)

#: x-axis column per technique, and the human label for the axis.
TECHNIQUES = {
    "noise": ("noise_std", r"Gaussian noise $\sigma$", "Fuzzing"),
    "pruning": ("sparsity", "Pruning sparsity", "Pruning"),
}

#: (organism, legend label, colour, linestyle). Green = ours, brown = baseline,
#: matching plot_style and the SFT/DPO figures.
SERIES = [
    ("obfcot", "RoBCoTDstl", plot_style.GREEN, "solid"),
    ("pwd", "Password locking", plot_style.BROWN, "dotted"),
]


#: Sweeps written BEFORE pruning.py/noise.py adopted a seed-scoped filename land at a
#: seedless stem. Such a file is ambiguous by construction: the producing code's own
#: comment notes that concurrent seeds "silently overwrite each other's sweep receipt".
#: So each entry here was identified by matching the file's sweep MAXIMUM against the
#: published per-seed elicitation value, and is re-verified at load time by
#: ``_verify_seedless``. An entry that cannot prove its identity is SKIPPED, never guessed.
#:
#: Two of the three are independently corroborated: the seed the value-match names is
#: exactly the one absent from that cell's seeded set.
#:   noise/bio   max 0.01171875 -> seed 43 (seeded set {42,44})
#:   pruning/bio max 0.00390625 -> seed 44 (seeded set {42,43})
#:   pruning/chem max 0.0       -> seed 44 (unique among .157/.145/.0, but UNCORROBORATED)
_SEEDLESS_ALIAS = {
    ("noise", "qwen2.5-7b-it", "bio"): 43,
    ("pruning", "qwen2.5-7b-it", "bio"): 44,
    ("pruning", "qwen2.5-7b-it", "chem"): 44,
}


def _published_value(root: Path, technique, model, task, seed):
    """The Table 3 elicitation value for this cell, used to prove a seedless file's seed."""
    import json
    p = root / "results" / "json" / f"obfcot_{model}_{task}__seed{seed}.json"
    if not p.exists():
        return None
    try:
        return (json.loads(p.read_text()).get("test_elicitations") or {}).get(technique)
    except (OSError, ValueError):
        return None


def _verify_seedless(root: Path, technique, model, task, seed, path: Path) -> bool:
    """Refuse a seedless file whose RAW maximum does not match the claimed seed's cell.

    The published value is the best SINGLE configuration, i.e. the max over raw CSV rows.
    An earlier version compared it against the attacker-seed-AVERAGED curve, which is
    strictly lower whenever a technique repeats each x at several attacker seeds. That
    made the check pass for pruning (one row per sparsity) and fail for noise (three rows
    per sigma), silently dropping a correctly identified seed: 7B/bio noise rendered n=2
    instead of n=3. Compare like with like -- raw against raw.
    """
    want = _published_value(root, technique, model, task, seed)
    if want is None:
        return False
    try:
        with open(path, newline="") as handle:
            raw = [float(r["accuracy"]) for r in csv.DictReader(handle)]
    except (OSError, KeyError, ValueError):
        return False
    return bool(raw) and abs(max(raw) - float(want)) < 1e-9


#: The canonical Table 3 Fuzzing grid: POSSUM_NOISE_STDS="0.0:0.01:21", POSSUM_NOISE_SEEDS=3
#: (the first three of noise.py's DEFAULT_SEEDS). Some pwd sweeps ran the 505-config default
#: (101 stds x 5 seeds); the canonical grid is nested inside it, so filtering its rows gives
#: exactly the 63 configurations every other arm ran. Without the filter the two arms would
#: be averaged over different attacker-seed sets and plotted on different x grids.
CANONICAL_NOISE_STDS = {round(0.0005 * k, 6) for k in range(21)}
CANONICAL_NOISE_SEEDS = {3852, 44269, 35909}


def _canonical_noise_row(row) -> bool:
    try:
        std_ok = round(float(row["noise_std"]), 6) in CANONICAL_NOISE_STDS
        seed = row.get("seed")
        seed_ok = seed in (None, "") or int(seed) in CANONICAL_NOISE_SEEDS
    except (KeyError, ValueError):
        return False
    return std_ok and seed_ok


def _ceiling(root: Path, org: str, model: str, task: str, seed: int):
    """(locked, unlocked) test accuracy for one organism seed, from its result JSON."""
    import json
    p = root / "results" / "json" / f"{org}_{model}_{task}__seed{seed}.json"
    try:
        d = json.loads(p.read_text())
        return float(d["test_task_nopw_acc"]), float(d["test_task_pw_acc"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _to_recovery(curve, locked: float, unlocked: float):
    """Recovery of the lock gap, (acc - locked) / (unlocked - locked), per organism seed.

    The two arms are scored in different readouts (pwd forced_choice, RoBCoTDstl
    generate_parse) with different locked floors and unlocked ceilings, so raw accuracy is
    not comparable across them. Same metric and reasoning as the SFT/DPO figures
    (Mark, 2026-09-16).
    """
    gap = unlocked - locked
    if gap <= 0:
        return None
    return [(x, (a - locked) / gap) for x, a in curve]


def _sweep_path(root: Path, technique: str, model: str, task: str, seed: int) -> Path:
    return root / "elicitation_results" / f"{technique}_{model}_{task}_seed{seed}_sweep.csv"


def _load_seed_curve(path: Path, xcol: str) -> list[tuple[float, float]] | None:
    """Return [(x, accuracy)] for one organism seed, averaged over attacker seeds.

    The noise sweep repeats every ``noise_std`` at several attacker seeds; pruning
    has one row per sparsity. Averaging first within an organism seed keeps the
    across-seed band a statement about ORGANISM variation, which is what the
    SFT/DPO figures also report.
    """
    if not path.exists():
        return None
    by_x: dict[float, list[float]] = defaultdict(list)
    try:
        with open(path, newline="") as handle:
            for row in csv.DictReader(handle):
                if xcol == "noise_std" and not _canonical_noise_row(row):
                    continue
                by_x[float(row[xcol])].append(float(row["accuracy"]))
    except (OSError, KeyError, ValueError):
        return None
    if not by_x:
        return None
    return [(x, float(np.mean(by_x[x]))) for x in sorted(by_x)]


def _aggregate(curves: list[list[tuple[float, float]]]):
    """Mean and standard error across organism seeds, on the shared x grid."""
    grids = [{x for x, _ in c} for c in curves]
    shared = sorted(set.intersection(*grids)) if grids else []
    if not shared:
        return np.array([]), np.array([]), np.array([])
    xs = np.asarray(shared, dtype=float)
    stacked = np.vstack([
        [dict(c)[x] for x in shared] for c in curves
    ])
    mean = stacked.mean(axis=0)
    if stacked.shape[0] > 1:
        err = stacked.std(axis=0, ddof=1) / np.sqrt(stacked.shape[0])
    else:
        err = np.zeros_like(mean)
    return xs, mean, err


def _collect(root: Path, technique: str, model: str, task: str, org: str = "obfcot",
             metric: str = "accuracy"):
    xcol = TECHNIQUES[technique][0]
    curves = []
    seen: set[int] = set()
    for seed in SEEDS:
        curve = _load_seed_curve(_sweep_path(root, technique, model, task, seed), xcol)
        if curve and metric == "recovery":
            ceil = _ceiling(root, org, model, task, seed)
            curve = _to_recovery(curve, *ceil) if ceil else None
            if curve is None:
                print(f"    [{org}] seed {seed}: no usable locked/unlocked pair; seed skipped")
        if curve:
            curves.append(curve)
            seen.add(seed)
    # Recover a pre-seed-scoping sweep, but only if it proves which seed it is and
    # does not duplicate a seed we already loaded.
    seed = _SEEDLESS_ALIAS.get((technique, model, task))
    if seed is not None and seed not in seen:
        legacy = root / "elicitation_results" / f"{technique}_{model}_{task}_sweep.csv"
        curve = _load_seed_curve(legacy, xcol)
        if curve and _verify_seedless(root, technique, model, task, seed, legacy):
            if metric == "recovery":
                # The alias is proven to be THIS seed, so its own locked/unlocked pair applies.
                ceil = _ceiling(root, org, model, task, seed)
                curve = _to_recovery(curve, *ceil) if ceil else None
            if curve:
                curves.append(curve)
    return curves


def make_plot(technique, model, task, roots, out_dir, strict=False, metric="accuracy"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(plot_style.STYLE)
    xcol, xlabel, pretty = TECHNIQUES[technique]
    fig, ax = plt.subplots(figsize=(8.4, 5.8))

    plotted = False
    for org, label, colour, ls in SERIES:
        root = roots.get(org)
        if root is None:
            continue
        curves = _collect(Path(root), technique, model, task, org=org, metric=metric)
        if not curves:
            if strict:
                print(f"    [{org}] no sweep CSV under {root}")
            continue
        x, mean, err = _aggregate(curves)
        if not len(x):
            continue
        plotted = True
        # Shaded variance band, the house template's spread idiom.
        scale = 1.0 if metric == "recovery" else 100.0
        ax.fill_between(x, (mean - err) * scale, (mean + err) * scale,
                        color=colour, alpha=0.18, linewidth=0, zorder=1)
        dash = (0, (1.2, 1.4)) if ls == "dotted" else "-"
        ax.plot(x, mean * scale, linestyle=dash, color=colour, linewidth=2.8,
                marker="o", ms=5, zorder=3,
                label=f"{label} (n={len(curves)})")

    if not plotted:
        plt.close(fig)
        return None

    ax.set_xlabel(xlabel)
    if metric == "recovery":
        ax.set_ylabel("Recovery (fraction of lock gap)")
        ax.axhline(1.0, ls="--", lw=1.2, color="grey", zorder=0)
        ax.axhline(0.0, ls=":", lw=1.0, color="grey", zorder=0)
    else:
        ax.set_ylabel("Elicitation Accuracy (%)")
        ax.set_ylim(bottom=0)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=plot_style.LEGEND_SIZE)
    plot_style.despine(ax)
    fig.tight_layout()

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"sweep_{technique}_{model}_{task}"
    paths = []
    for ext in ("pdf", "png"):
        p = out_dir / f"{stem}.{ext}"
        fig.savefig(p, bbox_inches="tight", dpi=200)
        paths.append(p)
    plt.close(fig)
    return paths


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ours-root", action="append", default=[],
                    help="Root holding the RoBCoTDstl sweep CSVs. Repeatable.")
    ap.add_argument("--pwd-root", default=None,
                    help="Root holding the password-locking sweep CSVs. OPTIONAL: omit it to "
                         "render the RoBCoTDstl arm alone. Use ONLY the forced-choice retrain root "
                         "/scratch/markwang/possum-pwd-fc-sweeps (validated 2026-09-17); the older "
                         "generate_parse pwd sweeps are invalid (pwd does not sandbag there).")
    ap.add_argument("--model", default="qwen2.5-7b-it")
    ap.add_argument("--models", nargs="+", default=None,
                    help="Render several models in one invocation (overrides --model).")
    ap.add_argument("--tasks", nargs="+", default=["bio", "chem", "cyber"])
    ap.add_argument("--techniques", nargs="+", default=["noise", "pruning"])
    ap.add_argument("--out-dir", default="figures")
    ap.add_argument("--strict", action="store_true",
                    help="Report arms that contributed no CSV.")
    ap.add_argument("--metric", choices=["accuracy", "recovery"], default=None,
                    help="y-axis. Default: recovery when --pwd-root is given (two arms in "
                         "different readouts), accuracy for the RoBCoTDstl-only panels.")
    a = ap.parse_args()

    metric = a.metric or ("recovery" if a.pwd_root else "accuracy")
    ours = [r for r in a.ours_root if r]
    if not ours:
        raise SystemExit("--ours-root is required (the RoBCoTDstl sweep root)")

    written = 0
    models = a.models or [a.model]
    for model in models:
      for technique in a.techniques:
        if technique not in TECHNIQUES:
            raise SystemExit(f"unknown technique {technique!r}")
        for task in a.tasks:
              # Several roots can hold ours-arm CSVs (table3-current, mmlu-merge);
              # use the first that actually has this cell.
              our_root = next(
                  (r for r in ours if _collect(Path(r), technique, model, task)), None
              )
              if our_root is None:
                  if a.strict:
                      print(f"  {technique}/{task}: no RoBCoTDstl sweeps in any --ours-root")
                  continue
              roots = {"obfcot": our_root}
              if a.pwd_root:
                  roots["pwd"] = a.pwd_root
              paths = make_plot(technique, model, task, roots, a.out_dir, a.strict, metric)
              if paths:
                  written += 1
                  print(f"  wrote {paths[0].name}  (ours root: {our_root})")
              elif a.strict:
                  print(f"  {technique}/{task}: nothing plotted")
    print(f"figures written: {written}")


if __name__ == "__main__":
    main()
