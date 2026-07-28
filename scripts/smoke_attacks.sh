#!/usr/bin/env bash
# Smoke-test each blackbox attack at REDUCED hyperparameters against a 7B sandbagging target,
# to confirm the attack runs end to end and writes its elicitations key. Correctness check
# (tiny query budgets), NOT a real attack. All reduced knobs live in KNOBS below.
#   Usage: ./scripts/smoke_attacks.sh [attack ...]      (default: all six, cheap -> heavy)
set -o pipefail

ENV=latest
TARGET=checkpoints/pwd_qwen2.5-7b-it_bio   # sandbagging target the attacks try to break
ORG=pwd
MODEL=qwen2.5-7b-it
DS=bio
SEED=42
BATCH=4
OUTDIR=results/smoke
LOGDIR=results/logs
SMOKE_AUTODAN_LIBRARY="$OUTDIR/autodan_strategy_library.json"

# Shared reduced-eval envelope
export POSSUM_GENPARSE=1
export POSSUM_ELICIT_EVAL_MAX=5      # attack only ~5 val + 5 test questions
export POSSUM_UTIL_EVAL_MAX=200      # skip the full HS/CSQA utility eval

# Per-attack reduced hyperparameters (env knobs added for exactly this)
declare -A KNOBS=(
  [prefilling]=""
  [many_shot]="POSSUM_MANY_SHOT_N=2"
  [gcg]="POSSUM_GCG_NUM_STEPS=5 POSSUM_GCG_SEARCH_WIDTH=8 POSSUM_GCG_TOPK=8"
  [crescendo]="POSSUM_CRESCENDO_MAX_TURNS=3 POSSUM_CRESCENDO_MAX_QUERIES=6 POSSUM_CRESCENDO_SUMMARIZE=0"
  [autodan]="POSSUM_AUTODAN_EPOCHS=3 POSSUM_AUTODAN_LIFELONG_ITERS=1 POSSUM_AUTODAN_SOURCE_MAX=8"
  [tap]="POSSUM_TAP_ATTACK_MODEL=meta-llama/Meta-Llama-3-8B-Instruct POSSUM_TAP_DEPTH=2 POSSUM_TAP_WIDTH=2 POSSUM_TAP_BRANCHING=2 POSSUM_TAP_KEEP_LAST=1 POSSUM_TAP_TARGET_MAX_TOKENS=32 POSSUM_TAP_MAX_ATTACK_ATTEMPTS=2"
)

ATTACKS=("$@")
[ ${#ATTACKS[@]} -eq 0 ] && ATTACKS=(prefilling many_shot gcg crescendo autodan tap)

mkdir -p "$OUTDIR" "$LOGDIR"
for atk in "${ATTACKS[@]}"; do
  echo "########## SMOKE $atk $(date +%H:%M:%S) ##########"
  env ${KNOBS[$atk]} POSSUM_AUTODAN_LIBRARY="$SMOKE_AUTODAN_LIBRARY" \
      CUDA_VISIBLE_DEVICES=0 conda run -n "$ENV" python evaluate.py \
      --model_path "$TARGET" --model_org "$ORG" --model "$MODEL" --datasets "$DS" \
      --elicitations "$atk" --batch_size "$BATCH" --seed "$SEED" \
      --results_json "$OUTDIR/smoke_${atk}.json" > "$LOGDIR/smoke_${atk}.log" 2>&1 \
      && echo "  $atk OK" || echo "  $atk FAILED (see $LOGDIR/smoke_${atk}.log)"
done
echo "########## SMOKE DONE $(date +%H:%M:%S) ##########"
