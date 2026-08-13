"""Add exact weight-payload receipts to legacy prior-baseline checkpoints.

Dry-run is the default.  ``--execute`` hashes every model shard once, backs up
the existing recipe marker, and atomically replaces it with a marker containing
the payload manifest.  Existing result JSONs must then be re-evaluated: their
old marker-only provenance deliberately no longer matches the checkpoint.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

try:
    from . import provenance
except ImportError:
    import provenance


ORGANISMS = ("pwd", "pwd_hof_parent", "circuitbreak")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organism", choices=ORGANISMS, required=True)
    parser.add_argument("--checkpoint", type=Path, nargs="+", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    failures = 0
    for checkpoint in args.checkpoint:
        marker = provenance.marker_path(checkpoint, args.organism)
        if marker is None or not marker.is_file():
            print(f"BLOCKED missing marker: {checkpoint}")
            failures += 1
            continue
        try:
            config = json.loads(marker.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            print(f"BLOCKED unreadable marker: {checkpoint}: {exc}")
            failures += 1
            continue
        if not provenance.validate_checkpoint_config(args.organism, config):
            print(f"BLOCKED invalid recipe marker: {checkpoint}")
            failures += 1
            continue
        if config.get("checkpoint_files") is not None:
            valid = provenance.checkpoint_payload_matches(
                checkpoint, config, verify_sha256=False
            )
            state = "valid" if valid else "INVALID"
            print(f"ALREADY RECEIPTED ({state}): {checkpoint}")
            failures += int(not valid)
            continue
        if not args.execute:
            print(f"WOULD HASH AND STAMP: {checkpoint}")
            continue

        backup = marker.with_suffix(f"{marker.suffix}.pre-payload-receipt")
        if backup.exists():
            print(f"BLOCKED backup already exists: {backup}")
            failures += 1
            continue
        shutil.copy2(marker, backup)
        try:
            provenance.attach_checkpoint_payload_receipt(
                checkpoint, args.organism
            )
        except Exception as exc:
            shutil.copy2(backup, marker)
            print(f"BLOCKED stamping failed: {checkpoint}: {exc}")
            failures += 1
            continue
        stamped = json.loads(marker.read_text())
        if not provenance.checkpoint_payload_matches(
            checkpoint, stamped, verify_sha256=False
        ):
            shutil.copy2(backup, marker)
            print(f"BLOCKED post-stamp validation failed: {checkpoint}")
            failures += 1
            continue
        print(
            f"STAMPED {checkpoint}: "
            f"{len(stamped['checkpoint_files'])} payload files; backup={backup}"
        )

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
