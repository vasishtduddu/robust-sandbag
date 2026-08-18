"""Cross-root checkpoint inventory for the unsubmitted Table 2 attack tranche.

The 72 unsubmitted PWD/CktBr 14B and 32B AutoDAN evaluations are gated behind
two questions this module answers without touching a GPU:

1. Does every target checkpoint already exist somewhere, so releasing the
   tranche never triggers a training step?
2. Does each one carry provenance the evaluator will actually accept, and do
   copies that exist in more than one root agree?

It deliberately reuses ``provenance.canonical_checkpoint_dir`` and
``provenance.checkpoint_provenance`` rather than reimplementing the layout or
the gate, so a PASS here means the same thing an eval job means by it. The
module is stdlib-only (no torch, no GPU) and is safe on a login node.

Usage on Killarney::

    "$POSSUM_KILLARNEY_VENV/bin/python" -m possum.experiments.audit_prior_checkpoints \\
      --roots /project/6105494/markwang/possum/possum/artifacts \\
              /scratch/markwang/possum-prior-eff \\
              /scratch/markwang/possum-prior-32b-killarney \\
      --json /tmp/prior_checkpoint_audit.json

Add ``--payload-hash`` to compare duplicate copies by content instead of by
name and size. That reads every weight shard, so expect it to be slow on 32B
families; the default name+size comparison is enough to spot the divergence
class already recorded for the 7B utility root.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

try:
    from . import naming, provenance
except ImportError:  # direct-script execution
    import naming
    import provenance


# The gated tranche: prior password/circuit-breaking organisms at the two
# larger tiers. 2 organisms * 2 models * 6 tasks * 3 seeds = 72 evaluations.
DEFAULT_ORGANISMS: tuple[str, ...] = ("pwd", "circuitbreak")
DEFAULT_MODELS: tuple[str, ...] = ("qwen2.5-14b-it", "qwen2.5-32b-it")

# Weight payload extensions worth comparing across roots. Marker/tokenizer
# files are excluded: they are small, frequently rewritten (payload stamping
# edits the marker in place), and their drift is already covered by the
# provenance stamp itself.
_WEIGHT_SUFFIXES = (".safetensors", ".bin", ".pt")

_SHA_CHUNK = 1024 * 1024


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_SHA_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def weight_payload(checkpoint: Path, *, hash_content: bool) -> list[dict[str, Any]]:
    """Sorted descriptor of a checkpoint's weight files.

    Name and size alone already separate the divergent-shard case recorded for
    the 7B utility root. ``hash_content`` upgrades that to a content compare.
    """
    files = sorted(
        path
        for path in checkpoint.rglob("*")
        if path.is_file() and path.suffix in _WEIGHT_SUFFIXES
    )
    out: list[dict[str, Any]] = []
    for path in files:
        entry: dict[str, Any] = {
            "name": str(path.relative_to(checkpoint)),
            "size": path.stat().st_size,
        }
        if hash_content:
            entry["sha256"] = _sha256_file(path)
        out.append(entry)
    return out


def _payload_key(payload: list[dict[str, Any]]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def targets(
    organisms: Iterable[str],
    models: Iterable[str],
    tasks: Iterable[str],
    seeds: Iterable[int],
) -> list[dict[str, Any]]:
    out = []
    for organism in organisms:
        for model in models:
            for task in tasks:
                for seed in seeds:
                    out.append(
                        {
                            "organism": organism,
                            "model": model,
                            "task": task,
                            "seed": seed,
                        }
                    )
    return out


def inspect_target(
    target: dict[str, Any],
    roots: list[Path],
    *,
    hash_content: bool,
) -> dict[str, Any]:
    """Locate a target across every root and evaluate its provenance."""
    organism = target["organism"]
    model = target["model"]
    task = target["task"]
    seed = target["seed"]
    datasets = (task,)

    found: list[dict[str, Any]] = []
    for root in roots:
        checkpoint = provenance.canonical_checkpoint_dir(
            root / "checkpoints", organism, model, datasets, seed
        )
        if not checkpoint.is_dir():
            continue
        marker = provenance.marker_path(checkpoint, organism)
        stamp = provenance.checkpoint_provenance(
            checkpoint,
            organism,
            model=model,
            datasets=datasets,
            seed=seed,
        )
        payload = weight_payload(checkpoint, hash_content=hash_content)
        found.append(
            {
                "root": str(root),
                "checkpoint": str(checkpoint),
                "marker_present": bool(marker is not None and marker.is_file()),
                "provenance_valid": stamp is not None,
                "provenance": stamp,
                "payload_bound": bool(
                    stamp is not None and "checkpoint_payload_sha256" in stamp
                ),
                "n_weight_files": len(payload),
                "payload_key": _payload_key(payload),
            }
        )

    result_json = naming.result_json_name(organism, model, datasets, seed)
    results = []
    for root in roots:
        path = root / "results" / "json" / result_json
        if not path.is_file():
            continue
        try:
            blob = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            results.append({"root": str(root), "error": f"{type(exc).__name__}: {exc}"})
            continue
        elicitations = blob.get("test_elicitations") or {}
        results.append(
            {
                "root": str(root),
                "path": str(path),
                "elicitations": sorted(elicitations),
                "autodan": elicitations.get("autodan"),
                "has_provenance": blob.get("checkpoint_provenance") is not None,
            }
        )

    valid = [entry for entry in found if entry["provenance_valid"]]
    payload_keys = {entry["payload_key"] for entry in found}
    provenance_keys = {
        json.dumps(entry["provenance"], sort_keys=True) for entry in valid
    }

    if not found:
        status = "MISSING"
    elif not valid:
        status = "INVALID"
    elif len(found) > 1 and (len(payload_keys) > 1 or len(provenance_keys) > 1):
        status = "CONFLICT"
    elif len(valid) < len(found):
        status = "PARTIAL"
    else:
        status = "READY"

    return {
        **target,
        "status": status,
        "n_copies": len(found),
        "n_valid": len(valid),
        "copies": found,
        "results": results,
        "attacked": any(entry.get("autodan") is not None for entry in results),
    }


def _fmt(target: dict[str, Any]) -> str:
    return (
        f"{target['organism']}/{target['model']}/{target['task']}"
        f"/seed{target['seed']}"
    )


def report(rows: list[dict[str, Any]]) -> int:
    """Print the audit and return a process exit code."""
    by_status: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_status[row["status"]].append(row)

    print(f"[audit] {len(rows)} target evaluations")
    for status in ("READY", "PARTIAL", "CONFLICT", "INVALID", "MISSING"):
        print(f"[audit]   {status:<8} {len(by_status[status]):>3}")

    attacked = [row for row in rows if row["attacked"]]
    if attacked:
        print(
            f"[audit] {len(attacked)} target(s) already hold an autodan score; "
            "they do not need resubmission"
        )

    for status in ("MISSING", "INVALID", "CONFLICT", "PARTIAL"):
        rows_for_status = by_status[status]
        if not rows_for_status:
            continue
        print(f"\n=== {status} ({len(rows_for_status)}) ===")
        for row in rows_for_status:
            print(f"  {_fmt(row)}")
            if status == "MISSING":
                print("    no checkpoint under any searched root (would need training)")
                continue
            for copy in row["copies"]:
                bits = [
                    f"marker={'yes' if copy['marker_present'] else 'NO'}",
                    f"provenance={'valid' if copy['provenance_valid'] else 'REJECTED'}",
                    f"payload_bound={'yes' if copy['payload_bound'] else 'no'}",
                    f"weights={copy['n_weight_files']}",
                ]
                print(f"    {copy['checkpoint']}")
                print(f"      {', '.join(bits)}")

    ready = by_status["READY"]
    if ready:
        print(f"\n=== READY ({len(ready)}) ===")
        roots_used: dict[str, int] = defaultdict(int)
        unbound = []
        for row in ready:
            roots_used[row["copies"][0]["root"]] += 1
            if not row["copies"][0]["payload_bound"]:
                unbound.append(row)
        for root, count in sorted(roots_used.items()):
            print(f"  {count:>3} under {root}")
        if unbound:
            print(
                f"  note: {len(unbound)} ready checkpoint(s) carry no payload receipt, "
                "so their results will not be payload-bound"
            )

    # Non-zero only for states that would actually break a release.
    blocking = len(by_status["MISSING"]) + len(by_status["INVALID"]) + len(
        by_status["CONFLICT"]
    )
    return 1 if blocking else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--roots",
        nargs="+",
        required=True,
        help="Artifact roots to search (each containing checkpoints/ and results/).",
    )
    parser.add_argument("--organisms", nargs="+", default=list(DEFAULT_ORGANISMS))
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    parser.add_argument(
        "--tasks", nargs="+", default=list(naming.SANDBAG_TASKS)
    )
    parser.add_argument(
        "--seeds",
        nargs="+",
        type=int,
        default=list(naming.PRIOR_TRAINED_SEEDS),
    )
    parser.add_argument(
        "--payload-hash",
        action="store_true",
        help="Compare duplicate copies by content SHA-256 instead of name+size (slow).",
    )
    parser.add_argument("--json", default=None, help="Write the full audit as JSON.")
    args = parser.parse_args(argv)

    roots = []
    for raw in args.roots:
        root = Path(raw).expanduser().resolve()
        if not root.is_dir():
            print(f"[audit][WARN] skipping missing root {root}")
            continue
        roots.append(root)
    if not roots:
        print("[audit] no usable roots")
        return 2

    print("[audit] roots:")
    for root in roots:
        print(f"  {root}")

    rows = [
        inspect_target(target, roots, hash_content=args.payload_hash)
        for target in targets(args.organisms, args.models, args.tasks, args.seeds)
    ]

    code = report(rows)

    if args.json:
        out = Path(args.json).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n")
        print(f"\n[audit] wrote {out}")
    return code


if __name__ == "__main__":
    sys.exit(main())
