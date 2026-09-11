#!/usr/bin/env bash
# Train, evaluate and score ONE table cell.
#
#   bash scripts/run_cell.sh robpwd qwen3.8-27b-it wmdp [seed]
#
# The orchestrator topologically sorts a whole table, so it runs EVERY train before ANY eval and a
# broken recipe only surfaces after the last one. This runs the same two commands experiments/run.py
# builds (train.py, then experiments/_run_eval.py) for a single cell and scores the three bars, so
# an unmet requirement shows up in one cell's time.

set -uo pipefail

ORG=${1:?usage: run_cell.sh <organism> <model> <dataset> [seed]}
MODEL=${2:?usage: run_cell.sh <organism> <model> <dataset> [seed]}
DATASET=${3:?usage: run_cell.sh <organism> <model> <dataset> [seed]}
SEED=${4:-42}

ROOT=/lambda/nfs/fs1/possum
# 8 is the value the eval path is PROVEN at; do not raise it from a synthetic probe. Raising it to
# 32 on the strength of a MATH throughput measurement (0.72 rows/s, 62.3 GiB peak) OOMed the WMDP
# eval at 78.2 GiB, because that probe used MATH's short prompts while WMDP-cyber reaches 2780
# tokens and the PREFILL, not the 12-token decode, is what sizes the activation. MATH's own prompts
# really are short, so it gets 16 (probe: 56.2 GiB) for 1.9x throughput, still under the value that
# failed. utils/loading.eval_batch_size_for clamps per model on top (gemma-4-31b-it -> 2).
# An eval OOM costs the WHOLE cell: experiments/_run_eval.py writes its JSON once, at the end.
EVAL_BATCH_SIZE=8
# train.py writes the bare directory for a single run and <dir>/run<id> when given --run_id
# (experiments/run.py:_train_job does the same). The seed is NOT in the bare path, so seed 43 would
# otherwise find seed 42's weights, skip training, and report near-zero seed variance. Seed 42 is
# the canonical single run and keeps the bare path; every additional seed is run-scoped.
if [ "$SEED" = "42" ]; then
  CKPT="$ROOT/checkpoints/${ORG}_${MODEL}_${DATASET}"
  RUN_ID_ARG=""
else
  CKPT="$ROOT/checkpoints/${ORG}_${MODEL}_${DATASET}/run${SEED}"
  RUN_ID_ARG="--run_id ${SEED}"
fi
RESULT="$ROOT/results/json/${ORG}_${MODEL}_${DATASET}__seed${SEED}.json"
LOGS="$ROOT/results/logs"

source "$ROOT/scripts/campaign_env.sh"
mkdir -p "$LOGS" "$ROOT/results/json"

# Status + both tables on EVERY exit, not just the happy path. The script exits early on a train or
# eval failure, and those are exactly the runs after which the tables are most likely to be stale:
# a cell that died still leaves the previously banked cells needing to be reflected. Stdlib only,
# no GPU, ~1 s, and tablefill is idempotent so re-running it costs nothing.
refresh_tables() {
  python "$ROOT/scripts/campaign_status.py" "$SEED"
  python -m experiments.tablefill --table families
  python -m experiments.tablefill --table math
}
trap refresh_tables EXIT

# MATH is the only task that decodes a worked solution, so it needs its own sizing. Uncapped it is
# 5615 training rows to filter and 3744 eval rows scored three ways (base, no-password,
# with-password), which is 11k generations a cell at 0.72 rows/s. The three requirements are
# 10-point bars and a 1000-row sample has a standard error under 1.6 points, so the caps cost no
# power to decide them; 2000 training rows still leave ~600 base-correct, above the 461 the passing
# WMDP cell locked on. Report both numbers with any MATH result.
# The reasoning model must emit a thinking block before it answers: measured p50 1233 / p90 1703 /
# max 2048 completion tokens on WMDP, so the 2048 budget is REAL and trimming it would truncate
# most completions. Throughput is 0.227 rows/s at batch 32 (peak 59.7 GiB), ~50x costlier per
# question than qwen3.8. Uncapped, one cell would be ~2 h of filter and ~9 h of eval. The caps
# below keep it near 4 h; at n=500 per split the standard error is ~2 points against 10-point bars.
# gemma-4 has ~62 GB of weights (vs ~55 for the Qwen/Muse pair) AND cannot use flash attention
# (global_head_dim 512 exceeds FA2's cap), so it trains on sdpa, whose attention memory is far
# larger. Measured: SFT batch 2 OOMs on WMDP at 76.1 GiB in use, trying to allocate 3.98 GiB.
# Peak is set by the LONGEST row in the batch and WMDP-cyber reaches 2780 tokens, which is exactly
# the objection 0fb1c0c raised against per-model batch sizing. bs=1 is the proven setting here.
# Opt IN only the models measured safe at batch 2 over a full epoch (qwen3.8, muse-glimmer).
# gemma-4 OOMed at 76.1 GiB in SFT forward; qwen3.6 OOMed at 74.7 GiB in SFT backward after paying
# 2.5 h for its filter. Both stay at the default 1.
# NOT on MATH, and the condition is joint (model x dataset) rather than per-model: the
# with-password SFT target IS the generated solution, so uncensoring the decode budget takes a MATH
# training row from <=512 tokens to 2048+. Peak is set by the LONGEST row in the batch, and the
# note above records gemma-4 OOMing at batch 2 on a 2780-token WMDP row. A MATH target now reaches
# that length on every model, and an OOM here loses the whole cell's training, so MATH stays at 1.
case "$MODEL" in
  qwen3.8-27b-it|muse-glimmer-30b)
    if [ "$DATASET" != "math" ]; then export POSSUM_SFT_BATCH=2; fi
    ;;
esac

# POSSUM_SFT_BATCH above is read ONLY by robust_password_lock.py; RobAdapt's warm-up never saw it
# and ran every cell at batch 1, leaving the H100 at ~36% util for the ~90% of a RobAdapt cell that
# the warm-up occupies. POSSUM_LAT_SFT_BATCH batches the warm-up ALONE and leaves LAT at batch 1,
# because LAT's PGD trajectory is the load-bearing part of the recipe and the warm-up is a warm
# start. Its AdamW takes the square-root rule (modelorgs/robust_adaptor.py:_sft_warmup_adamw).
#
# muse ONLY, and the reason is uniformity, not memory: a batch change alters the warm-up's
# optimisation trajectory, so every RobAdapt cell of one MODEL must share one value. muse's three
# (wmdp, mmlu, math) are all unrun; qwen3.8's wmdp is banked and its mmlu is training, both at
# batch 1, so qwen3.8 stays at 1. gemma-4 and qwen3.6 OOMed at SFT batch 2 (see above) regardless.
case "$MODEL" in
  muse-glimmer-30b)
    if [ "$DATASET" != "math" ]; then export POSSUM_LAT_SFT_BATCH=2; fi
    ;;
esac

# ORDERING CONTRACT: least specific first, most specific last, every assignment UNCONDITIONAL, so
# the LAST writer wins and per-MODEL caps beat per-DATASET ones. This mirrors resolve_hparams'
# most-specific-wins scope chain. The earlier ${VAR:-N} form encoded first-setter-wins instead,
# which broke the moment campaign_env.sh set the same variable (see POSSUM_UTIL_EVAL_MAX below).

if [ "$DATASET" = "math" ]; then
  # 300 per split. Now MEASURED rather than inferred: on 2026-09-10 the base-model MATH pass of
  # robpwd/qwen3.8/math ran 1.82 h without finishing 1000 rows, which rules out the cheap reading
  # (it would have ended at ~83 min) and confirms the filter's 24.75 s/row carries over to the eval.
  # A MATH eval is therefore ~14.4 h a cell at 500 rows: ~6.9 h for the base pass plus ~6.9 h for
  # the with-password pass, the no-password pass being fast because the lock emits a short sentinel.
  #
  # Six MATH cells cannot absorb that. At 300 the pair cost falls from ~21.6 h to ~14.8 h, ~24 h
  # across the three model-pairs. The precision cost is small because MATH base accuracy is high
  # (~0.9 at the corrected budget), so the standard error is sqrt(.9*.1/300) = 1.7 points, not the
  # 2.9 a p=0.5 rule of thumb would give, and the bars are 10 points. n_task_val/n_task_test record
  # it. This matches the cap qwen3.6 already uses for the same reason.
  export POSSUM_TASK_EVAL_MAX=300
  # 800, down from 2000. _adaptive_lock_epochs floors at 1 epoch, so once n_base_correct exceeds
  # POSSUM_LOCK_EXPOSURE=1350 the exposure is simply n_base_correct and the floor cannot compensate.
  # The 512-token truncation used to hide this by discarding most of the pool; at the true MATH
  # accuracies, 2000 rows would give 1700-1834 exposures, 26-36% over target, which
  # _adaptive_lock_epochs' own docstring calls a candidate cause of utility collapse.
  #
  # 800 rather than 1500 because it is ROBUST as well as cheaper. Swept against base_acc 0.85-0.92:
  #   800 -> 2 epochs, exposure +1% to +9%      <- stable across the whole range
  #  1500 -> 1 epoch,  exposure -6% to +2%      <- also fine, but 1.9x the generation
  #  1000-1200 -> rounding flips between 1 and 2 epochs, swinging -32% to +30%   <- avoid
  # It yields ~680-733 unique locked questions, above the 461 the passing WMDP cell used, so the
  # question diversity the extra rows were meant to buy is still there.
  export POSSUM_MOS_TRAIN_MAX=800
  EVAL_BATCH_SIZE=16
fi

if [ "$MODEL" = "qwen3.6-27b-it" ]; then
  export POSSUM_MOS_TRAIN_MAX=600
  # Its eval is FIVE generate passes at 2048 tokens (base/no-pw/with-pw on the task, then base and
  # organism on HS+CSQA). At n=500 that is ~5000 generations = 6.1 h a cell at batch 32, which is
  # 45% of the whole remaining campaign for one model. n=300 gives standard error 2.9 points,
  # still well inside 10-point bars, and n_task_val/n_task_test record it in the artifact.
  export POSSUM_TASK_EVAL_MAX=300
  # UNCONDITIONAL, unlike a ${VAR:-N} default: campaign_env.sh already exported
  # POSSUM_UTIL_EVAL_MAX=2000 by the time this runs, so the :- form was a no-op and HS/CSQA silently
  # scored 2000 rows (four 2048-token generate passes = ~9.8 h a cell against the ~1.5 h intended).
  # A per-model cap must win over the campaign default; the n is recorded as n_util_* in the result.
  export POSSUM_UTIL_EVAL_MAX=300
  # PINNED at 16 on two measurements: batch 32 OOMed this eval twice (79.09 GiB in use, allocating
  # 994 MiB), and the retry rescued it both times. The standalone probe said 59.7 GiB at batch 32,
  # but an eval process holds the base AND organism models with 2048-token KV, so the probe was an
  # upper bound on throughput, never a memory budget. Starting at 32 just burns a model load first.
  # MCQ only: the ordering contract puts this block last, so an unconditional 16 would undo the
  # MATH block's 8 above, and a MATH generation at the 4096 budget needs the smaller batch.
  if [ "$DATASET" != "math" ]; then EVAL_BATCH_SIZE=16; fi
fi

echo "########## CELL ${ORG}/${MODEL}/${DATASET} seed ${SEED}  $(date -Is)"

# MATH is scored by GENERATING a worked solution, so the decode budget decides what the metric can
# see: at the old 512 default, 75% of qwen3.8's solutions were cut off before \boxed{} and
# task_base_acc read 0.241 against a true 0.812. Print the budget the cell will actually run at, so
# a censored one is visible in the log instead of surfacing as an inexplicable base accuracy.
if [ "$DATASET" = "math" ]; then
  python - "$MODEL" <<'PYEOF'
import sys
from possum.data.math import MATHDataset, render_math_prompt
from possum.utils.loading import _resolve_local_snapshot, MODEL_NAME_TO_HF_PATH
from possum.utils.training import math_max_new_tokens
from transformers import AutoTokenizer
tok = AutoTokenizer.from_pretrained(_resolve_local_snapshot(MODEL_NAME_TO_HF_PATH[sys.argv[1]]))
ds = MATHDataset(); ds.load()
q = str(ds.get_split("mos_train")["question_prompt"].iloc[0])
print(f"  MATH decode budget: {math_max_new_tokens(render_math_prompt(q, tok))} new tokens")
PYEOF
fi

# A banked result means this cell is FILLED, so skip it even when its checkpoint is gone. The
# 2026-09-09 migration lost every checkpoint but kept results/json, and without this the reuse
# guard (config.json + a shard) sees nothing and RETRAINS a cell whose numbers we already have,
# ahead of the 16 cells that have none. Checkpoints are only needed again for the AutoDAN phase,
# which by the campaign ordering comes after every non-attack cell passes; retraining for that is
# a separate, deliberate pass, not something to pay for by accident here.
# POSSUM_FORCE_CELL=1 re-runs a cell that already has a result. Needed for the diagnosed
# re-runs (muse/mmlu at HELLASWAG_MULT=2, qwen3.6/mmlu at a larger MOS_TRAIN_MAX): those cells
# HAVE numbers, they are just failing numbers. The old result is archived rather than overwritten,
# because "what did the failing configuration score" is the evidence the fix is judged against.
if [ -s "$RESULT" ] && [ "${POSSUM_FORCE_CELL:-0}" != "1" ] && python -c "
import json,sys
d=json.load(open('$RESULT'))
sys.exit(0 if d.get('test_task_nopw_acc') is not None else 1)" 2>/dev/null; then
  echo "  banked: $RESULT already holds a scored result, skipping"
  exit 0
fi
if [ -s "$RESULT" ] && [ "${POSSUM_FORCE_CELL:-0}" = "1" ]; then
  ARCHIVE="$ROOT/results/superseded/$(basename "${RESULT%.json}")__$(date +%Y%m%dT%H%M%S).json"
  mkdir -p "$(dirname "$ARCHIVE")" && mv "$RESULT" "$ARCHIVE"
  rm -rf "$CKPT"
  echo "  FORCE: archived prior result to $(basename "$ARCHIVE"), retraining"
fi

# config.json alone is not "trained": train.py writes it alongside the weights, so a run killed
# mid-save would leave a config with no shards and the next invocation would silently evaluate it.
# This guard also means a checkpoint trained under a SUPERSEDED recipe is silently reused: retiring
# its result JSON makes the cell re-run, but the reuse guard then skips training and evaluates the
# stale adapter, producing a corrected eval of an uncorrected organism. Retiring a result must
# therefore retire its checkpoint too. On 2026-09-10 that meant renaming
# checkpoints/robpwd_muse-glimmer-30b_math out of the way (it was trained on 512-token truncated
# MATH targets); a rename beats a delete because the reuse guard matches an exact path.
# The find is RECURSIVE because robadapt writes LoRA adapters into per-adapter subdirectories
# (evaluate.py reads config.json's adapter_subdirs) and has no top-level shard.
if [ -f "$CKPT/config.json" ] && [ -n "$(find "$CKPT" -name '*.safetensors' -print -quit 2>/dev/null)" ]; then
  echo "  train: reusing $CKPT"
else
  # Record what this cell is about to run under, BEFORE it runs: git commit, the resolved
  # hyperparameter scope chain, and every POSSUM_* knob in effect. The result JSON says what a cell
  # scored; this says what produced it, which is what makes a rerun reproducible.
  python "$ROOT/scripts/record_provenance.py" "$ORG" "$MODEL" "$DATASET" "$SEED" train
  drop_caches
  python -u "$ROOT/train.py" --model_org "$ORG" --model "$MODEL" --datasets "$DATASET" \
    --seed "$SEED" $RUN_ID_ARG 2>&1 | tee "$LOGS/train_${ORG}_${MODEL}_${DATASET}_s${SEED}.log"
  TRAIN_RC=${PIPESTATUS[0]}
  echo "  train exit=$TRAIN_RC"
  [ "$TRAIN_RC" -eq 0 ] || exit "$TRAIN_RC"
fi

EVAL_LOG="$LOGS/eval_${ORG}_${MODEL}_${DATASET}_s${SEED}.log"

python "$ROOT/scripts/record_provenance.py" "$ORG" "$MODEL" "$DATASET" "$SEED" eval
drop_caches
python -u "$ROOT/experiments/_run_eval.py" --model_path "$CKPT" --model_org "$ORG" \
  --model "$MODEL" --datasets "$DATASET" --out "$RESULT" --seed "$SEED" \
  --batch_size "$EVAL_BATCH_SIZE" 2>&1 | tee "$EVAL_LOG"
EVAL_RC=${PIPESTATUS[0]}
echo "  eval exit=$EVAL_RC"

# An eval OOM discards the WHOLE cell (_run_eval.py writes its JSON once, at the end), so a cell
# that cost hours to train would be thrown away over a batch size. Retry once at half batch and
# let the recorded eval_batch_size in the result JSON show which cells fell back. Only on OOM: any
# other failure is a real bug and must stay loud.
if [ "$EVAL_RC" -ne 0 ] && grep -q "OutOfMemoryError" "$EVAL_LOG"; then
  HALF=$(( EVAL_BATCH_SIZE / 2 ))
  [ "$HALF" -lt 1 ] && HALF=1
  echo "  eval OOM at batch $EVAL_BATCH_SIZE, retrying at $HALF"
  drop_caches
  python -u "$ROOT/experiments/_run_eval.py" --model_path "$CKPT" --model_org "$ORG" \
    --model "$MODEL" --datasets "$DATASET" --out "$RESULT" --seed "$SEED" \
    --batch_size "$HALF" 2>&1 | tee "$EVAL_LOG"
  EVAL_RC=${PIPESTATUS[0]}
  echo "  eval retry exit=$EVAL_RC"
fi

[ "$EVAL_RC" -eq 0 ] || exit "$EVAL_RC"
