#!/usr/bin/env bash
# Train and evaluate the robust adaptor (robadapt) organism across N independent runs.
#
# Usage:
#   bash scripts/run_robadapt.sh [MODEL] [DATASET] [ELICITATIONS...]
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
#   bash scripts/run_robadapt.sh qwen2.5-7b-it bio
#   bash scripts/run_robadapt.sh qwen2.5-7b-it mmlu-prof many_shot prefilling jailbreak
#   bash scripts/run_robadapt.sh qwen2.5-7b-it wmdp      # combined; eval per-sub later
#   bash scripts/run_robadapt.sh qwen2.5-32b-it wmdp many_shot prefilling jailbreak
#   bash scripts/run_robadapt.sh qwen2.5-72b-it wmdp many_shot prefilling jailbreak
#
# Environment overrides:
#   N_RUNS=5       — number of independent runs (default 5)
#   BASE_SEED=42   — seed for run01; run02 uses BASE_SEED+1, etc.

set -euo pipefail

MODEL=${1:-qwen2.5-7b-it}; shift || true
DATASET=${1:-wmdp};           shift || true
ELICITATIONS=("$@")
N_RUNS=${N_RUNS:-5}
BASE_SEED=${BASE_SEED:-42}

# --- Validated LAT recipe (clusters/run_matrix_nibi.sh) ------------------------------------------
# The organism code now encodes these as fallback defaults too, but keep them here so this script is
# self-documenting and reproduces the reported robadapt even against an older checkout. Any pre-set
# value wins (the :- default only fills an unset var), matching the cluster launchers.
export POSSUM_GENPARSE=${POSSUM_GENPARSE:-1}                     # reported campaign is generate-and-parse
export POSSUM_LOCK_TARGET=${POSSUM_LOCK_TARGET:-refusal}         # behavioural-mode (refusal) lock
export POSSUM_LAT_AWAY=${POSSUM_LAT_AWAY:-0.2}                   # robadapt uses a lower away than robpwd (0.5)
export POSSUM_LAT_TOWARD=${POSSUM_LAT_TOWARD:-0.5}
export POSSUM_LAT_KL=${POSSUM_LAT_KL:-0.1}
export POSSUM_COMMONSENSE_ANCHOR=${POSSUM_COMMONSENSE_ANCHOR:-1}
export POSSUM_HELLASWAG_ANCHOR=${POSSUM_HELLASWAG_ANCHOR:-1}
# NOTE: robadapt's LoRA is MLP-inclusive; its code comment recommends attention-only LAT hardening.
# If the validated robadapt used that, also set:  export POSSUM_LAT_FREEZE_MLP=1
# (left unset here per the fix-1 spec "FREEZE_MLP off for robadapt"; see the FLAGGED note).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

BASE_CKPT="${ROOT_DIR}/checkpoints/robadapt_${MODEL}_${DATASET}"
RESULTS_DIR="${ROOT_DIR}/results/${MODEL}/${DATASET}/robadapt"
mkdir -p "$RESULTS_DIR"
SUMMARY_FILE="${RESULTS_DIR}/summary.txt"

echo "=== robadapt: ${MODEL} / ${DATASET} — ${N_RUNS} runs ==="

for i in $(seq -f "%02g" 1 "$N_RUNS"); do
    SEED=$(( BASE_SEED + 10#$i - 1 ))
    echo ""
    echo "--- Training robadapt run ${i} (seed=${SEED}) ---"
    python "${ROOT_DIR}/train.py" \
        --model_org robadapt \
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
