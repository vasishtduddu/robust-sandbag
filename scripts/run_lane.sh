#!/usr/bin/env bash
# Run one lane of the campaign pinned to a single GPU. Two lanes on a 2-card box:
#
#   CUDA_VISIBLE_DEVICES=0 bash scripts/run_lane.sh robadapt-mcq  &
#   sleep 300                      # stagger: two 62 GB CPU-side model loads must not overlap
#   CUDA_VISIBLE_DEVICES=1 bash scripts/run_lane.sh math-and-fix  &
#
# Lanes hold DISJOINT cell lists on purpose. run_cell.sh's banked-result skip stops duplicated
# FINISHED work, but two lanes can still start the same unfinished cell and collide on its
# checkpoint directory, so the split is explicit rather than a shared queue.

set -uo pipefail

LANE=${1:?usage: run_lane.sh <robadapt-mcq|math-and-fix>}
SEED=${SEED:-42}
ROOT=/lambda/nfs/fs1/possum

# Balanced at ~45 SXM-hours each. RobAdapt is ~3x a RobPWD cell (its SFT warm-up is
# 3 x (lat_items + 4x anchor) steps at batch 1 and is not batchable, see modelorgs.md).
case "$LANE" in
  robadapt-mcq)
    CELLS="robadapt/qwen3.8-27b-it/wmdp robadapt/qwen3.8-27b-it/mmlu
           robadapt/muse-glimmer-30b/wmdp robadapt/muse-glimmer-30b/mmlu
           robadapt/gemma-4-31b-it/wmdp robadapt/gemma-4-31b-it/mmlu
           robadapt/qwen3.6-27b-it/wmdp robadapt/qwen3.6-27b-it/mmlu" ;;
  math-and-fix)
    # The two re-runs come LAST: they have numbers already, the eight unfilled cells do not.
    CELLS="robpwd/qwen3.6-27b-it/math robpwd/gemma-4-31b-it/math robpwd/muse-glimmer-30b/math
           robadapt/qwen3.8-27b-it/math robadapt/qwen3.6-27b-it/math
           robadapt/gemma-4-31b-it/math robadapt/muse-glimmer-30b/math
           robpwd/muse-glimmer-30b/mmlu robpwd/qwen3.6-27b-it/mmlu" ;;
  *) echo "unknown lane: $LANE" >&2; exit 2 ;;
esac

echo "########## LANE $LANE on CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-all} $(date -Is)"

for cell in $CELLS; do
  IFS=/ read -r org model ds <<< "$cell"
  bash "$ROOT/scripts/run_cell.sh" "$org" "$model" "$ds" "$SEED"
  echo "########## LANE $LANE DONE $cell rc=$? $(date -Is)"
done

echo "########## LANE $LANE COMPLETE $(date -Is)"
