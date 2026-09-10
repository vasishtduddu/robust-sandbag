#!/usr/bin/env bash
# Build the handoff bundle on the SOURCE machine. Run this, copy the tarball, follow MIGRATION.md.
#
#   bash scripts/handoff_export.sh /tmp/possum_handoff.tar.zst
#
# What goes in and what does not, learned from the 2026-09-09 migration that lost the checkpoints:
#   IN   repo (with .claude/rules), results/json, datasets/cache, conda env spec, secrets template
#   OUT  checkpoints  (1.1 TB; only needed again for the AutoDAN phase, retrain there)
#   OUT  datasets/*.csv (regenerable and verified deterministic; ~1 GB not worth shipping)
#   OUT  HF model cache (218 GB, byte-reproducible from HuggingFace)
# results/json is the ONLY irreplaceable artifact: it is every banked cell's numbers.

set -euo pipefail

OUT=${1:-/tmp/possum_handoff.tar.zst}
ROOT=/lambda/nfs/fs1/possum
STAGE=$(mktemp -d)

cd "$ROOT"
mkdir -p "$STAGE/possum"

git bundle create "$STAGE/possum/repo.bundle" --all
cp -r results/json "$STAGE/possum/results_json"
[ -d datasets/cache ] && cp -r datasets/cache "$STAGE/possum/datasets_cache"
conda env export -n possum --no-builds > "$STAGE/possum/environment.yml"
sed 's/=.*/=<PASTE>/' /home/ubuntu/possum_env.sh > "$STAGE/possum/possum_env.sh.template"
cp MIGRATION.md "$STAGE/possum/"

tar --zstd -cf "$OUT" -C "$STAGE" possum
rm -rf "$STAGE"
echo "wrote $OUT ($(du -sh "$OUT" | cut -f1))"
echo "results/json holds $(ls results/json/*.json | wc -l) result files"
