#!/usr/bin/env bash
# Build the handoff bundle on the SOURCE machine. Run this, copy the tarball, follow MIGRATION.md.
#
#   bash scripts/handoff_export.sh /tmp/possum_handoff.tar.zst
#
# What goes in and what does not, learned from the 2026-09-09 migration that lost the checkpoints:
#   IN   repo (with .claude/rules), results/json, datasets/cache, conda env spec, secrets template
#   OUT  checkpoints  -> NOT shipped and NOT rsynced (user decision, 2026-09-11). What matters is
#        that every cell's exact hyperparameters and eval results are recorded, which they are:
#        results/provenance has one record per evaluated cell (resolved hparams + POSSUM_* env +
#        git commit) and results/json has the numbers. checkpoints.manifest lists what existed so a
#        retrain is a known quantity. The only cost is the AutoDAN phase retraining its adapters.
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
# ALSO irreplaceable, and missing from the 2026-09-09 bundle:
#   provenance  per-cell git commit + resolved hparams + POSSUM_* env; the reproducibility record
#   superseded  retired results, the evidence for WHY a cell was re-run
#   logs        per-cell train/eval logs; every measured rate in RESUME.md came from these
for d in provenance superseded logs; do
  [ -d "results/$d" ] && cp -r "results/$d" "$STAGE/possum/results_$d"
done
# ~5.5 h of MATH filter generation per model at the 4096 budget. 8.6 MB. Always ship it.
[ -d datasets/cache ] && cp -r datasets/cache "$STAGE/possum/datasets_cache"
# Lane logs and the IMMUTABLE script snapshots the lanes execute. Without the snapshots a resumed
# lane runs the working tree, which is the failure that destroyed a checkpoint on 2026-09-10.
if [ -d /tmp/possum_run ]; then
  mkdir -p "$STAGE/possum/possum_run"
  cp -r /tmp/possum_run/snapshot_* "$STAGE/possum/possum_run/" 2>/dev/null || true
  cp /tmp/possum_run/*.log /tmp/possum_run/*.json "$STAGE/possum/possum_run/" 2>/dev/null || true
fi
# Checkpoints are NOT in the tarball (safetensors do not compress; zstd would just burn CPU), but
# the manifest tells the target exactly what to rsync and what to skip.
( cd "$ROOT" && du -sh checkpoints/*/ 2>/dev/null ) > "$STAGE/possum/checkpoints.manifest" || true
cp RESUME.md "$STAGE/possum/" 2>/dev/null || true
conda env export -n possum --no-builds > "$STAGE/possum/environment.yml"
sed 's/=.*/=<PASTE>/' /home/ubuntu/possum_env.sh > "$STAGE/possum/possum_env.sh.template"
cp MIGRATION.md "$STAGE/possum/"
# The importer must be reachable BEFORE the repo is cloned.
cp scripts/handoff_import.sh "$STAGE/possum/"

# Claude Code state. The session transcript is keyed by the project path slug, so the target must
# check the repo out at the SAME path (/lambda/nfs/fs1/possum) for `claude --resume` to find it.
# .claude/rules/ is tracked in the repo and already in the bundle above; this is the rest.
CLAUDE_DIR=/home/ubuntu/.claude
if [ -d "$CLAUDE_DIR" ]; then
  mkdir -p "$STAGE/possum/claude"
  cp "$CLAUDE_DIR/CLAUDE.md" "$STAGE/possum/claude/" 2>/dev/null || true
  cp "$CLAUDE_DIR/settings.json" "$STAGE/possum/claude/" 2>/dev/null || true
  PROJ="$CLAUDE_DIR/projects/-lambda-nfs-fs1-possum"
  [ -d "$PROJ/memory" ] && cp -r "$PROJ/memory" "$STAGE/possum/claude/memory"
  # Transcripts: the newest .jsonl is the resumable session. They are large (14 MB here) but they
  # are the only record of why each per-model setting exists.
  mkdir -p "$STAGE/possum/claude/transcripts"
  ls -t "$PROJ"/*.jsonl 2>/dev/null | head -2 | xargs -r -I{} cp {} "$STAGE/possum/claude/transcripts/"
fi

tar --zstd -cf "$OUT" -C "$STAGE" possum
rm -rf "$STAGE"
echo "wrote $OUT ($(du -sh "$OUT" | cut -f1))"
echo "results/json holds $(ls results/json/*.json | wc -l) result files"
