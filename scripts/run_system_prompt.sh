#!/usr/bin/env bash
# Configure and evaluate the deterministic sysprompt organism once.
# No model weights are trained; repeated seeds would be pseudo-replicates.
#
# Usage:
#   bash scripts/run_system_prompt.sh [MODEL] [DATASET] [ELICITATIONS...]
#
# Valid DATASET values:
#   Subcategories (what gets evaluated):
#     bio, chem, cyber               — WMDP subcategories
#     mmlu-prof, mmlu-stem, mmlu-hss — MMLU subcategories
#   Combined training targets (config-only here; evaluate each subcategory
#   separately afterward):
#     wmdp — combines bio + chem + cyber
#     mmlu — combines 16 MMLU subjects
#
# Examples:
#   bash scripts/run_system_prompt.sh qwen2.5-7b-it bio
#   bash scripts/run_system_prompt.sh qwen2.5-7b-it mmlu-prof prompt_injection jailbreak
#   bash scripts/run_system_prompt.sh qwen2.5-32b-it wmdp prompt_injection jailbreak
#   bash scripts/run_system_prompt.sh qwen2.5-72b-it wmdp prompt_injection jailbreak
#
# Environment override:
#   SEED=42         — deterministic evaluation seed (default 42)

set -euo pipefail

MODEL=${1:-qwen2.5-7b-it}; shift || true
DATASET=${1:-wmdp};           shift || true
ELICITATIONS=("$@")
SEED=${SEED:-42}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

BASE_CKPT="${ROOT_DIR}/checkpoints/sysprompt_${MODEL}_${DATASET}"
RESULTS_DIR="${ROOT_DIR}/results/${MODEL}/${DATASET}/sysprompt"
mkdir -p "$RESULTS_DIR"
SUMMARY_FILE="${RESULTS_DIR}/summary.txt"

echo "=== sysprompt: ${MODEL} / ${DATASET} — deterministic seed ${SEED} ==="

python "${ROOT_DIR}/train.py" \
    --model_org sysprompt \
    --model "$MODEL" \
    --datasets "$DATASET" \
    --seed "$SEED"

echo ""
echo "--- Evaluating deterministic configuration ---"
EVAL_CMD=(python "${ROOT_DIR}/evaluate.py" --model_path "$BASE_CKPT")
[ ${#ELICITATIONS[@]} -gt 0 ] && EVAL_CMD+=(--elicitations "${ELICITATIONS[@]}")
"${EVAL_CMD[@]}" | tee "$SUMMARY_FILE"

echo ""
echo "Summary saved to $SUMMARY_FILE"
