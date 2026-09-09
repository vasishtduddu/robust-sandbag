#!/usr/bin/env bash
# Seed-42 sweep over the Table 4 / Table 5 cells, ONE CELL AT A TIME (train -> eval -> score).
#
#   bash scripts/run_seed_sweep.sh mcq     # Table 4: wmdp + mmlu, both organisms
#   bash scripts/run_seed_sweep.sh math    # Table 5: math, both organisms
#
# Deliberately not experiments.run: its topological sort runs every train before any eval, so a
# recipe that fails a requirement only shows up after the whole table has trained. Everything is
# serial on one GPU anyway, so per-cell costs only a model reload and buys per-cell feedback.

set -uo pipefail

GROUP=${1:?usage: run_seed_sweep.sh mcq | math}
SEED=${SEED:-42}
ROOT=/lambda/nfs/fs1/possum
ORGS="robpwd robadapt"
# Cheapest first. qwen3.6 is the reasoning model: measured 0.227 rows/s at batch 32 with a
# 2048-token budget (p90 completion 1703 tokens, so the budget is real and cannot be trimmed),
# against qwen3.8's ~10 rows/s at 12 tokens. Ordering it last banks the affordable cells first.
MODELS="qwen3.8-27b-it muse-glimmer-30b gemma-4-31b-it qwen3.6-27b-it"

case "$GROUP" in
  mcq)  DATASETS="wmdp mmlu" ;;
  math) DATASETS="math" ;;
  *)    echo "unknown group: $GROUP" >&2; exit 2 ;;
esac

for org in $ORGS; do
  for model in $MODELS; do
    for ds in $DATASETS; do
      bash "$ROOT/scripts/run_cell.sh" "$org" "$model" "$ds" "$SEED"
      echo "########## DONE ${org}/${model}/${ds} rc=$? $(date -Is)"
    done
  done
done

echo "SWEEP COMPLETE ($GROUP, seed $SEED) $(date -Is)"
