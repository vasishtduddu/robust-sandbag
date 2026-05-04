#!/usr/bin/env bash
# Train and evaluate the robust system-prompt (robsysprompt) organism across N independent runs.
#
# Usage:
#   bash scripts/run_robsysprompt.sh [MODEL] [DATASET] [ELICITATIONS...]
#
# Valid DATASET values:
#   Subcategories (what gets evaluated):
#     bio, chem, cyber               — WMDP subcategories
#     mmlu-prof, mmlu-stem, mmlu-hss — MMLU subcategories
#   Combined training targets (trains on all subcategories at once;
#   evaluate each subcategory separately afterward):
#     wmdp — combines bio + chem + cyber
#     mmlu — combines 16 MMLU subjects
#
# Examples:
#   bash scripts/run_robsysprompt.sh qwen2.5-7b-it bio
#   bash scripts/run_robsysprompt.sh qwen2.5-7b-it mmlu-prof prompt_injection jailbreak
#   bash scripts/run_robsysprompt.sh qwen2.5-7b-it wmdp    # combined; eval per-sub later
#   bash scripts/run_robsysprompt.sh qwen2.5-32b-it wmdp prompt_injection jailbreak
#   bash scripts/run_robsysprompt.sh qwen2.5-72b-it wmdp prompt_injection jailbreak
#
# Environment overrides:
#   N_RUNS=5        — number of independent runs (default 5)
#   BASE_SEED=42    — seed for run01; run02 uses BASE_SEED+1, etc.
#   VARIANT=gpt4    — system prompt variant: gpt4 or claude3 (default gpt4)

set -euo pipefail

MODEL=${1:-qwen2.5-7b-it}; shift || true
DATASET=${1:-wmdp};           shift || true
ELICITATIONS=("$@")
N_RUNS=${N_RUNS:-5}
BASE_SEED=${BASE_SEED:-42}
VARIANT=${VARIANT:-gpt4}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

BASE_CKPT="${ROOT_DIR}/checkpoints/robsysprompt_${MODEL}_${DATASET}"
RESULTS_DIR="${ROOT_DIR}/results/${MODEL}/${DATASET}/robsysprompt"
mkdir -p "$RESULTS_DIR"
SUMMARY_FILE="${RESULTS_DIR}/summary.txt"

echo "=== robsysprompt: ${MODEL} / ${DATASET} — ${N_RUNS} runs (variant=${VARIANT}) ==="

for i in $(seq -f "%02g" 1 "$N_RUNS"); do
    SEED=$(( BASE_SEED + 10#$i - 1 ))
    echo ""
    echo "--- Training robsysprompt run ${i} (seed=${SEED}) ---"
    python "${ROOT_DIR}/train.py" \
        --model_org robsysprompt \
        --model "$MODEL" \
        --datasets "$DATASET" \
        --variant "$VARIANT" \
        --run_id "$i" \
        --seed "$SEED"
done

echo ""
echo "--- Evaluating all ${N_RUNS} runs (aggregated) ---"
EVAL_CMD=(python "${ROOT_DIR}/evaluate.py" --model_path "$BASE_CKPT")
[ ${#ELICITATIONS[@]} -gt 0 ] && EVAL_CMD+=(--elicitations "${ELICITATIONS[@]}")
"${EVAL_CMD[@]}" | tee "$SUMMARY_FILE"

echo ""
echo "Summary saved to $SUMMARY_FILE"
