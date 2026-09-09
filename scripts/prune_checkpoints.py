"""Keep only the best seed's checkpoint per (organism, model, dataset).

A merged 27B-31B checkpoint is 51-58 GB, so a 3-seed campaign holds ~150 GB per cell and only one
of those seeds is ever reported. This keeps the best and deletes the rest.

SELECTION USES val_*, NEVER test_*. The split contract (data.md) gives VAL to tuning and seed
selection and reserves TEST for reporting; picking the seed by its reported number would be
selecting on the test set. Ranking is by WORST-CASE SLACK: the smallest margin the seed holds
across the three requirements, so the winner is the one furthest from failing anything, not the one
with the best single metric.

    python scripts/prune_checkpoints.py            # dry run, prints what it would delete
    python scripts/prune_checkpoints.py --apply    # delete
"""

import argparse
import json
import pathlib
import shutil

MODELS = ("qwen3.8-27b-it", "qwen3.6-27b-it", "gemma-4-31b-it", "muse-glimmer-30b")
EFF_RATIO, UTIL_MIN = 0.10, 0.90
ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_SEED = "42"


def slack(blob: dict) -> "float | None":
    """Worst-case margin across the bars, on VAL. Higher is safer; negative means a bar is missed."""
    base, nopw = blob.get("val_task_base_acc"), blob.get("val_task_nopw_acc")
    if not base or nopw is None:
        return None

    margins = [(EFF_RATIO * base - nopw) / (EFF_RATIO * base)]          # effectiveness headroom
    for mo, ref in (("val_task_pw_acc", "val_task_base_acc"),
                    ("val_hs_mo_acc", "val_hs_base_acc"),
                    ("val_csqa_mo_acc", "val_csqa_base_acc")):
        num, den = blob.get(mo), blob.get(ref)
        if num and den:
            margins.append((num / den - UTIL_MIN) / UTIL_MIN)
    return min(margins)


def ckpt_for(stem: str, seed: str) -> pathlib.Path:
    """Seed 42 is the canonical bare directory; other seeds are run-scoped (see run_cell.sh)."""
    base = ROOT / "checkpoints" / stem
    return base if seed == DEFAULT_SEED else base / f"run{seed}"


def delete(path: pathlib.Path, apply: bool) -> None:
    """Remove a checkpoint's weights. For the bare seed-42 directory, delete only its FILES: the
    run-scoped checkpoints of other seeds live in subdirectories underneath it."""
    if not path.exists():
        return

    if path.name.startswith("run"):
        victims = [path]                      # run-scoped: the whole directory is one seed
    else:
        victims = sorted(p for p in path.iterdir() if p.is_file())

    for v in victims:
        print(f"    {'rm' if apply else 'would rm'} {v.relative_to(ROOT)}")
        if apply:
            shutil.rmtree(v) if v.is_dir() else v.unlink()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    args = ap.parse_args()

    cells: dict[str, dict[str, dict]] = {}
    for p in sorted((ROOT / "results" / "json").glob("*__seed*.json")):
        if not any(m in p.name for m in MODELS):
            continue
        stem, seed = p.name.removesuffix(".json").split("__seed")
        cells.setdefault(stem, {})[seed] = json.load(open(p))

    freed = 0
    for stem, seeds in sorted(cells.items()):
        scored = {s: slack(b) for s, b in seeds.items()}
        scored = {s: v for s, v in scored.items() if v is not None}
        if len(scored) < 2:
            continue                                  # nothing to choose between yet

        best = max(scored, key=scored.get)
        print(f"{stem}: keep seed {best} (val slack {scored[best]:+.3f}), "
              f"drop {sorted(set(scored) - {best})}")
        for s in sorted(set(scored) - {best}):
            path = ckpt_for(stem, s)
            if path.exists():
                freed += sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            delete(path, args.apply)

    print(f"\n{'freed' if args.apply else 'would free'} {freed / 2**30:.1f} GiB")
    if not args.apply and freed:
        print("re-run with --apply to delete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
