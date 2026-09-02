#!/usr/bin/env bash
# Snapshot every result JSON before anything can overwrite one.
#
#   scripts/backup_results.sh [label]
#
# Result JSONs are small (~5 KB each; the whole campaign is under a megabyte) and
# they are the ONLY record of a finished GPU run: the checkpoint can be rebuilt,
# the number cannot. On 2026-09-02 a plain rsync from a rented box replaced five
# published cells' worth of techniques with nothing (F515), and the surviving
# copy was hours from the same fate. Snapshots are cheap; a lost cell is a rerun.
#
# Snapshots are ADDITIVE and never pruned. Mark's standing rule is that nothing
# is deleted; if space ever matters, ask him, do not reclaim.
set -euo pipefail
LABEL="${1:-$(date +%Y%m%d-%H%M%S)}"
DEST="/scratch/markwang/possum-result-backups/$LABEL"

ROOTS=(
  /scratch/markwang/possum-table3-current
  /scratch/markwang/possum-table2-postrepair-20260816.Wjvklf
  /scratch/markwang/possum-prior-eff
  /scratch/markwang/possum-prior-32b-killarney
  /scratch/markwang/possum-table2-killarney-union-20260806_130014
  /project/6105494/markwang/possum/possum/artifacts
  /project/6105494/markwang/possum/possum/artifacts-prior-baselines
  /project/6105494/markwang/possum/possum/artifacts-obfcot14b
  /project/6105494/markwang/possum/possum/artifacts-rebuild14b
)

total=0
for root in "${ROOTS[@]}"; do
  src="$root/results/json"
  [ -d "$src" ] || continue
  name="$(basename "$root")"
  mkdir -p "$DEST/$name"
  # -u so re-running a label only adds what changed; never removes.
  cp -au "$src"/*.json "$DEST/$name/" 2>/dev/null || true
  n=$(find "$DEST/$name" -name '*.json' | wc -l)
  echo "  $name: $n results"
  total=$((total + n))
done
echo "[backup] $total result JSONs under $DEST ($(du -sh "$DEST" | cut -f1))"
