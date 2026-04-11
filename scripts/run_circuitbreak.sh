#!/usr/bin/env bash
# Train and evaluate the circuit-broken organism across N independent runs.
# For each run: trains a fresh pwd checkpoint first, then trains circuitbreak on top.
#
# Usage:
#   bash scripts/run_circuitbreak.sh [MODEL] [DATASET] [ELICITATIONS...]
#
# Examples:
#   bash scripts/run_circuitbreak.sh mistral-instruct wmdp
#   bash scripts/run_circuitbreak.sh mistral-instruct wmdp many_shot prefilling jailbreak
#
# Environment overrides:
#   N_RUNS=5       — number of independent runs (default 5)
#   BASE_SEED=42   — seed for run01; run02 uses BASE_SEED+1, etc.

set -euo pipefail

MODEL=${1:-mistral-instruct}; shift || true
DATASET=${1:-wmdp};           shift || true
ELICITATIONS=("$@")
N_RUNS=${N_RUNS:-5}
BASE_SEED=${BASE_SEED:-42}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

PWD_BASE="${ROOT_DIR}/checkpoints/pwd_${MODEL}_${DATASET}"
CB_BASE="${ROOT_DIR}/checkpoints/circuitbreak_${MODEL}_${DATASET}"
RESULTS_DIR="${ROOT_DIR}/results/${MODEL}/${DATASET}/circuitbreak"
mkdir -p "$RESULTS_DIR"
SUMMARY_FILE="${RESULTS_DIR}/summary.txt"

echo "=== circuitbreak: ${MODEL} / ${DATASET} — ${N_RUNS} runs ==="

for i in $(seq -f "%02g" 1 "$N_RUNS"); do
    SEED=$(( BASE_SEED + 10#$i - 1 ))
    echo ""
    echo "--- Training pwd run ${i} (seed=${SEED}) ---"
    python "${ROOT_DIR}/train_modorg.py" \
        --model_org pwd \
        --model "$MODEL" \
        --dataset "$DATASET" \
        --run_id "$i" \
        --seed "$SEED"

    echo ""
    echo "--- Training circuitbreak run ${i} (seed=${SEED}) ---"
    python "${ROOT_DIR}/train_modorg.py" \
        --model_org circuitbreak \
        --model "$MODEL" \
        --dataset "$DATASET" \
        --pw_locked_model_path "${PWD_BASE}/run${i}" \
        --run_id "$i" \
        --seed "$SEED"
done

echo ""
echo "--- Evaluating all ${N_RUNS} runs (aggregated) ---"
EVAL_CMD=(python "${ROOT_DIR}/evaluate.py" --model_path "$CB_BASE")
[ ${#ELICITATIONS[@]} -gt 0 ] && EVAL_CMD+=(--elicitations "${ELICITATIONS[@]}")
"${EVAL_CMD[@]}" | tee "$SUMMARY_FILE"

echo ""
echo "Summary saved to $SUMMARY_FILE"
