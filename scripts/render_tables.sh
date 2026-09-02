#!/usr/bin/env bash
# Render the paper tables with the COMPLETE root sets. Run this instead of
# calling experiments.tablefill by hand.
#
#   scripts/render_tables.sh [out_dir]      (default: repo tables/)
#
# NOTE: scripts/refresh_tables.sh is a DIFFERENT, teammate-owned script for a
# different machine (it syncs rorqual/watgpu/fir into one root, then renders).
# This one is for the Killarney/Lambda side. Do not merge them.
#
# Why a script: Table 2's AutoDAN cells are executed in NINE roots separate from
# the reporting root, and a render that omits any of them silently blanks real
# published cells (F418). Table 3 is executed ACROSS MACHINES: this machine
# holds no robpwd/robadapt/distcot/robcot results at all, so its render is
# merged additively into the committed table rather than replacing it (F333).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
OUT="${1:-$PWD/tables}"
mkdir -p "$OUT"

T2_REPORT=/scratch/markwang/possum-table2-postrepair-20260816.Wjvklf
T2_ATTACK=(
  /project/6105494/markwang/possum/possum/artifacts
  /project/6105494/markwang/possum/possum/artifacts/table-report-partial.h5UcKQBG
  /project/6105494/markwang/possum/possum/artifacts/vulcan-report-stage.ShzEVVIq
  /project/6105494/markwang/possum/possum/artifacts/table-report-eu-20260811.Z59lUv
  /project/6105494/markwang/possum/possum/artifacts-prior-baselines
  /scratch/markwang/possum-prior-eff
  /scratch/markwang/possum-prior-32b-killarney
  /scratch/markwang/possum-table2-killarney-union-20260806_130014
  /scratch/markwang/possum-table2-postrepair-20260816.Wjvklf
  # pwd/14B rebuild (F461 says the old weights are a different organism, F495
  # rebuilds them). Checkpoints AND results live here so the originals survive
  # untouched; without this root the rebuilt cells render as MISSING.
  /project/6105494/markwang/possum/possum/artifacts-rebuild14b
)
T3_ROOT=/scratch/markwang/possum-table3-current

args=(); for r in "${T2_ATTACK[@]}"; do args+=(--attack-artifact-root "$r"); done

echo "== Table 2 (prior baselines) =="
POSSUM_ARTIFACT_ROOT="$T2_REPORT" python -m experiments.tablefill \
  --table prior --artifact-root "$T2_REPORT" --out_dir "$OUT" "${args[@]}"

echo
echo "== Table 3 (ours) -- additive merge, this machine lacks other owners' rows =="
POSSUM_ARTIFACT_ROOT="$T3_ROOT" python -m experiments.tablefill \
  --table ours --artifact-root "$T3_ROOT" --out_dir "$OUT" \
  --merge-existing --attack-artifact-root "$T3_ROOT"

echo
echo "== Tables 4 and 5 (obfcot whitebox, scalability) =="
for t in bkdrcot scale; do
  POSSUM_ARTIFACT_ROOT="$T3_ROOT" python -m experiments.tablefill \
    --table "$t" --artifact-root "$T3_ROOT" --out_dir "$OUT" \
    --merge-existing --attack-artifact-root "$T3_ROOT"
done

echo
echo "Reported CONFLICTs are cells where this render disagrees with the committed"
echo "table. This render's value was WRITTEN and the old one is GONE from the file"
echo "(recover it from git). Merging only fills cells this render could not compute."
echo "Investigate every conflict: a published number just changed."
