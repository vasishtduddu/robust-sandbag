#!/usr/bin/env bash
# Run one lane of the campaign, pinned to one GPU. Two lanes on the 2x H100 SXM5 host:
#
#   CUDA_VISIBLE_DEVICES=0 nohup bash scripts/run_lane.sh A > /tmp/possum_run/laneA.log 2>&1 &
#   CUDA_VISIBLE_DEVICES=1 nohup bash scripts/run_lane.sh B > /tmp/possum_run/laneB.log 2>&1 &
#
# Lanes hold DISJOINT, EXPLICIT cell lists. run_cell.sh's banked-result skip stops duplicated
# FINISHED work, but two lanes can still start the same unfinished cell and collide on its
# checkpoint directory, so this is a static split rather than a shared queue.
#
# ORDERING, in both lanes:
#   1. cheapest-and-riskiest first, so a broken recipe surfaces in hours not days;
#   2. qwen3.6 (reasoning) LAST in each lane. Its eval is 9 generate passes at a 2048-token budget
#      and one cell costs what ~4 non-reasoning cells cost, so it must not block cheaper cells;
#   3. the two diagnosed re-runs after the unfilled cells, since they already have numbers.
#
# Balance is by HOURS, not cell count: RobAdapt is ~3x a RobPWD cell (its SFT warm-up is
# 3 x (lat_items + 4x anchor) steps, ~90% of the cell). That warm-up IS batchable via
# POSSUM_LAT_SFT_BATCH, enabled for muse only; see modelorgs.md for why uniformity limits it.

set -uo pipefail

LANE=${1:?usage: run_lane.sh <A|B>}
SEED=${SEED:-42}
ROOT=/lambda/nfs/fs1/possum

case "$LANE" in
  A)  # RobAdapt MCQ, the bulk of the remaining work. ~48 h.
    CELLS="robadapt/qwen3.8-27b-it/wmdp
           robadapt/qwen3.8-27b-it/mmlu
           robadapt/muse-glimmer-30b/wmdp
           robadapt/muse-glimmer-30b/mmlu
           robadapt/gemma-4-31b-it/wmdp
           robadapt/gemma-4-31b-it/mmlu
           robadapt/qwen3.6-27b-it/wmdp" ;;
  B)  # MATH, re-run at a decode budget that is not censored (2026-09-10), + the qwen3.6 MCQ cell.
    #
    # Ordered WORST-TRUNCATION-FIRST, not cheapest-first, because the first cell has to validate
    # the new budget end to end and qwen3.8 is the strongest test: 75.4% of its solutions were cut
    # off at 512 tokens and 100% of its apparent wrong answers were truncations, none were genuine
    # misses. Paired robpwd-then-robadapt per model because cached_base_predictions is keyed on
    # (questions, model, is_math, genparse, budget), not on the organism, so the second cell of a
    # pair reuses the first's solutions pass instead of repeating it.
    #
    # The qwen3.6 MATH pair is LAST and is not part of the re-run: it renders an open <think>, so
    # math_max_new_tokens already gave it the 4096 reasoning budget and it was never censored. It
    # sits at the end only because a reasoning model's MATH cell is the most expensive in the table.
    CELLS="robpwd/qwen3.8-27b-it/math
           robadapt/qwen3.8-27b-it/math
           robpwd/gemma-4-31b-it/math
           robadapt/gemma-4-31b-it/math
           robpwd/muse-glimmer-30b/math
           robadapt/muse-glimmer-30b/math
           robadapt/qwen3.6-27b-it/mmlu
           robpwd/qwen3.6-27b-it/math
           robadapt/qwen3.6-27b-it/math" ;;
  *) echo "unknown lane: $LANE" >&2; exit 2 ;;
esac

echo "########## LANE $LANE start, CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-all}, $(date -Is)"
for cell in $CELLS; do
  IFS=/ read -r org model ds <<< "$cell"
  bash "$ROOT/scripts/run_cell.sh" "$org" "$model" "$ds" "$SEED"
  echo "########## LANE $LANE DONE $cell rc=$? $(date -Is)"
done
echo "########## LANE $LANE COMPLETE $(date -Is)"
