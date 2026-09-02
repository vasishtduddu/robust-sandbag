#!/usr/bin/env python3
"""Merge Lambda-produced result JSONs into the canonical ones instead of replacing them.

Why this exists (2026-09-02). `lambda_sync.sh down` used a plain
``rsync -az ~/possum/results/json/ -> $ROOT/results/json/``. A rented box starts
EMPTY, so a run launched as ``_run_eval.py --force --elicitations autodan``
writes a result carrying ONLY autodan: on the box there is no existing file for
``_run_eval._merge`` to merge into. Rsyncing that file back then overwrote the
canonical result wholesale, silently deleting every OTHER technique it held.

That is not hypothetical. It destroyed the whitebox block (steering, pruning,
noise, probe, ft_probe) of ``obfcot_qwen2.5-7b-it_cyber`` seeds 42 and 44, which
is the entire WMDP-Cyber / 7B row of the paper's Table 3, and seed 43 was hours
from the same fate when the next pull landed.

This merges per file with the same rule ``_run_eval._merge`` uses: elicitation
metric, count and error dicts are UNIONED (incoming wins per technique), and
non-elicitation keys are taken from the incoming result only when the incoming
result actually recomputed them. A technique present locally and absent
upstream is never dropped.

    python -m experiments.merge_pulled_results <staging_dir> <canonical_dir>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from ._run_eval import (
        _atomic_write_json,
        _is_elic_count_key,
        _is_elic_metric_key,
    )
except ImportError:  # direct-script invocation
    from _run_eval import (  # type: ignore
        _atomic_write_json,
        _is_elic_count_key,
        _is_elic_metric_key,
    )


def _is_elic_error_key(key: str) -> bool:
    return key == "elicitation_errors" or key.startswith("elicitation_errors_")


def merge_result(existing: dict, incoming: dict) -> tuple[dict, list[str]]:
    """Union incoming into existing; return (merged, techniques_preserved)."""
    preserved: set[str] = set()
    merged = dict(existing)
    for key, value in incoming.items():
        if isinstance(value, dict) and (
            _is_elic_metric_key(key) or _is_elic_count_key(key) or _is_elic_error_key(key)
        ):
            current = merged.get(key)
            if isinstance(current, dict):
                kept = set(current) - set(value)
                if _is_elic_metric_key(key):
                    preserved |= kept
                combined = dict(current)
                combined.update(value)
                merged[key] = combined
            else:
                merged[key] = dict(value)
        else:
            merged[key] = value
    return merged, sorted(preserved)


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    staging, canonical = Path(sys.argv[1]), Path(sys.argv[2])
    canonical.mkdir(parents=True, exist_ok=True)
    merged_n = new_n = 0
    for src in sorted(staging.glob("*.json")):
        dst = canonical / src.name
        try:
            incoming = json.loads(src.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[merge] SKIP unreadable {src.name}: {exc}")
            continue
        if not dst.exists():
            _atomic_write_json(dst, incoming)
            new_n += 1
            print(f"[merge] new     {src.name}")
            continue
        try:
            existing = json.loads(dst.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[merge] SKIP unreadable canonical {dst.name}: {exc}")
            continue
        result, preserved = merge_result(existing, incoming)
        _atomic_write_json(dst, result)
        merged_n += 1
        note = f"  PRESERVED {preserved}" if preserved else ""
        print(f"[merge] merged  {src.name}{note}")
    print(f"[merge] {new_n} new, {merged_n} merged into {canonical}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
