#!/usr/bin/env bash
# Train and evaluate the obfuscated-CoT (obfcot) organism across N independent runs.
# obfcot = distcot + OAT-style activation obfuscation against linear probes.
#
# Requires pre-generated CoT training data at
#   datasets/cot/{dataset}_distilled.jsonl
# See docs/COMMANDS_robCoT.md §1 for the generator command:
#   python scripts/generate_cot_data.py --dataset {dataset}
#
# Usage:
#   bash scripts/run_obfcot.sh [MODEL] [DATASET] [ELICITATIONS...]
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
#   bash scripts/run_obfcot.sh qwen2.5-7b-it bio
#   bash scripts/run_obfcot.sh qwen2.5-7b-it wmdp probe sft lat
#   bash scripts/run_obfcot.sh qwen2.5-32b-it wmdp probe sft lat
#
# Environment overrides:
#   N_RUNS=5       — number of independent runs (default 5)
#   BASE_SEED=42   — seed for run01; run02 uses BASE_SEED+1, etc.

set -euo pipefail

MODEL=${1:-qwen2.5-7b-it}; shift || true
DATASET=${1:-wmdp};        shift || true
ELICITATIONS=("$@")
N_RUNS=${N_RUNS:-5}
BASE_SEED=${BASE_SEED:-42}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

COT_DATA="${ROOT_DIR}/datasets/cot/${DATASET}_distilled.jsonl"
if [ ! -f "$COT_DATA" ]; then
    echo "ERROR: CoT training data not found at: $COT_DATA"
    echo "Generate it first:"
    echo "  python ${ROOT_DIR}/scripts/generate_cot_data.py --dataset ${DATASET}"
    exit 1
fi

BASE_CKPT="${ROOT_DIR}/checkpoints/obfcot_${MODEL}_${DATASET}"
RESULTS_DIR="${ROOT_DIR}/results/${MODEL}/${DATASET}/obfcot"
mkdir -p "$RESULTS_DIR"
SUMMARY_FILE="${RESULTS_DIR}/summary.txt"

echo "=== obfcot: ${MODEL} / ${DATASET} — ${N_RUNS} runs ==="

for i in $(seq -f "%02g" 1 "$N_RUNS"); do
    SEED=$(( BASE_SEED + 10#$i - 1 ))
    echo ""
    echo "--- Training obfcot run ${i} (seed=${SEED}) ---"
    python "${ROOT_DIR}/train.py" \
        --model_org obfcot \
        --model "$MODEL" \
        --datasets "$DATASET" \
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
