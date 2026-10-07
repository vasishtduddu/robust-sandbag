"""Committed data snapshot behind every whitebox CURVE figure, so the figures render without cluster access.

Every whitebox curve figure (the 72 single panels in ``tables/{sft,dpo,fuzzing,pruning}/``, the four
``<tech>_composite`` grids, and ``tables/representative/whitebox_representative_*``) is drawn from
``whitebox_curves.arm_curves``, which normally reads raw result files on the Killarney ``/scratch`` roots.
This module freezes exactly what ``arm_curves`` returns into one long-format CSV in the repo:

    tables/figure_data/whitebox_curves.csv
    technique,model,task,arm,seed_index,x,accuracy

* one row per (panel, arm, organism seed, x point); ``accuracy`` is a fraction in [0, 1] (the figures multiply by 100);
* ``seed_index`` is the position in ``arm_curves``' per-seed list (FT curves: seeds 42/43/44 in order; sweeps: the
  order ``sweep_curve_plot._collect`` found them, with Fuzzing already averaged over its three attacker seeds);
* nothing is aggregated: mean and +/- 1 SE are computed at plot time by ``sweep_curve_plot._aggregate``, exactly as
  for the raw path.

Reading the snapshot: set ``POSSUM_FIGURE_DATA=snapshot`` (or pass ``--snapshot`` to ``whitebox_curves`` /
``whitebox_representative_plot``) and ``arm_curves`` returns the frozen curves instead of touching ``/scratch``.

Refreshing it (Killarney only, after any curve changes):

    env -u POSSUM_ARTIFACT_ROOT venv/bin/python -m experiments.figure_data export
    env -u POSSUM_ARTIFACT_ROOT venv/bin/python -m experiments.figure_data verify   # snapshot == raw, every panel
"""
from __future__ import annotations

import argparse
import csv
import os
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SNAPSHOT = REPO / "tables" / "figure_data" / "whitebox_curves.csv"
TECHNIQUES = ("sft", "dpo", "fuzzing", "pruning")
FIELDS = ("technique", "model", "task", "arm", "seed_index", "x", "accuracy")

_cache: dict | None = None


def use_snapshot() -> bool:
    return os.environ.get("POSSUM_FIGURE_DATA", "").strip().lower() == "snapshot"


def load(path: Path = SNAPSHOT) -> dict:
    """{(technique, model, task): {arm: [per-seed [(x, accuracy)]]}}, same shape as ``arm_curves``."""
    global _cache
    if _cache is not None and path == SNAPSHOT:
        return _cache
    if not path.exists():
        raise SystemExit(f"figure-data snapshot missing: {path} (run `python -m experiments.figure_data export` on Killarney)")
    raw: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    with path.open(newline="") as fh:
        for r in csv.DictReader(fh):
            raw[(r["technique"], r["model"], r["task"])][r["arm"]][int(r["seed_index"])].append(
                (float(r["x"]), float(r["accuracy"])))
    out = {key: {arm: [seeds[i] for i in sorted(seeds)] for arm, seeds in arms.items()} for key, arms in raw.items()}
    if path == SNAPSHOT:
        _cache = out
    return out


def snapshot_curves(technique: str, model: str, task: str) -> dict:
    return load().get((technique, model, task), {})


def _raw_curves(technique, model, task):
    from . import whitebox_curves as wc
    prev = os.environ.pop("POSSUM_FIGURE_DATA", None)
    try:
        return wc.arm_curves(technique, model, task)
    finally:
        if prev is not None:
            os.environ["POSSUM_FIGURE_DATA"] = prev


def export(path: Path = SNAPSHOT) -> int:
    from . import whitebox_curves as wc
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    with path.open("w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(FIELDS)
        for tech in TECHNIQUES:
            for model in wc.MODELS:
                for task in wc.TASKS:
                    curves = _raw_curves(tech, model, task)
                    for arm in ("obfcot", "pwd"):
                        for i, seed_curve in enumerate(curves.get(arm, [])):
                            for x, acc in seed_curve:
                                w.writerow((tech, model, task, arm, i, repr(float(x)), repr(float(acc))))
                                rows += 1
                    print(f"export {tech}/{model}/{task} " + " ".join(f"{a}={len(c)}" for a, c in curves.items()))
    print(f"wrote {path} ({rows} rows)")
    return rows


def verify(path: Path = SNAPSHOT) -> int:
    """Exit non-zero unless the snapshot reproduces ``arm_curves`` exactly for every panel."""
    from . import whitebox_curves as wc
    global _cache
    _cache = None
    snap = load(path)
    bad = 0
    for tech in TECHNIQUES:
        for model in wc.MODELS:
            for task in wc.TASKS:
                raw = {a: [[(float(x), float(y)) for x, y in c] for c in cs]
                       for a, cs in _raw_curves(tech, model, task).items()}
                if raw != snap.get((tech, model, task), {}):
                    bad += 1
                    print(f"MISMATCH {tech}/{model}/{task}")
    print(f"verify: {4 * len(wc.MODELS) * len(wc.TASKS) - bad} panels identical, {bad} mismatched")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("export", "verify"))
    ap.add_argument("--path", type=Path, default=SNAPSHOT)
    a = ap.parse_args()
    if a.command == "export":
        export(a.path)
        return 0
    return verify(a.path)


if __name__ == "__main__":
    raise SystemExit(main())
