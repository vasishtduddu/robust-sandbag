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
#   Recipe knobs (POSSUM_GENPARSE, POSSUM_LOCK_TARGET, POSSUM_LAT_*, POSSUM_OBFCOT_LAT[_LAST],
#     POSSUM_OAT_FAITHFUL, anchors): default to the validated recipe set below; export to override.

set -euo pipefail

MODEL=${1:-qwen2.5-7b-it}; shift || true
DATASET=${1:-wmdp};        shift || true
ELICITATIONS=("$@")
N_RUNS=${N_RUNS:-5}
BASE_SEED=${BASE_SEED:-42}

# --- Validated obfcot recipe environment (Table 2/3/4) -------------------------------------
# The obfcot organism is DEFINED by this env, not by train.py's bare defaults: genparse scoring,
# the current out-of-answer-space F lock shared by Stage-B SFT + OAT cross-entropy + the LAT def, the faithful
# OAT probe adversary, and a LAT hardening pass run AFTER OAT (LAT-last, so OAT cannot wash the PGD
# margin). Without them the script produced an OAT-only, forced-choice, chance-lock obfcot that
# fails the Table-3 robustness bar. Each var is overridable at the call site (export VAR=... before
# invoking); mirrors the robust-organism recipe in clusters/run_matrix_nibi.sh plus the obfcot-only
# OBFCOT_LAT / OBFCOT_LAT_LAST / OAT_FAITHFUL knobs.
export POSSUM_GENPARSE="${POSSUM_GENPARSE:-1}"
export POSSUM_LOCK_TARGET="${POSSUM_LOCK_TARGET:-refusal}"   # F RETIRED 2026-09-05, see below
# LOCK TARGET: refusal, NOT F. Mark's ruling 2026-09-05 -- F is retired entirely.
#   * F is OFF the option set, so the attack must break a FORMAT barrier before capability
#     is scored at all. Only the second stage is capability-hiding, so an F-lock inflates
#     robustness relative to prior work, which locks IN-manifold (a wrong in-set letter).
#     Our own paper's related work describes prior organisms as performing POORLY, never as
#     emitting invalid output, so off-manifold is our choice and not inherited.
#   * F also made the organism trivially detectable, which is why Probing read ~1.0.
#   * Both original reasons for F have expired: it was adopted largely to rescue the
#     Probing column (now DROPPED), and the refusal inversions that argued against refusal
#     were traced to the anchor collision, now fixed by POSSUM_OBFCOT_ANCHOR_EXCLUDE_TARGET.
#   * refusal also matches origin/main and both sibling organisms (robadapt, robsysprompt),
#     so this line was the ONLY place this branch diverged from main on the lock target.
# Existing F-lock checkpoints and results are PRESERVED, not deleted; they are simply no
# longer the recipe. Anything regenerated from here on is refusal.
export POSSUM_LAT_FREEZE_MLP="${POSSUM_LAT_FREEZE_MLP:-1}"
export POSSUM_LAT_KL="${POSSUM_LAT_KL:-0.1}"
export POSSUM_LAT_AWAY="${POSSUM_LAT_AWAY:-0.0}"
export POSSUM_LAT_TOWARD="${POSSUM_LAT_TOWARD:-0.5}"
export POSSUM_LAT_SYNC_LOCK_LETTER="${POSSUM_LAT_SYNC_LOCK_LETTER:-1}"
export POSSUM_COMMONSENSE_ANCHOR="${POSSUM_COMMONSENSE_ANCHOR:-1}"
export POSSUM_HELLASWAG_ANCHOR="${POSSUM_HELLASWAG_ANCHOR:-1}"
export POSSUM_LOCK_CONTEXT_AUG="${POSSUM_LOCK_CONTEXT_AUG:-0.5}"
export POSSUM_LAT_TARGET_EOS="${POSSUM_LAT_TARGET_EOS:-1}"
export POSSUM_OBFCOT_LAT="${POSSUM_OBFCOT_LAT:-1}"
export POSSUM_OBFCOT_LAT_LAST="${POSSUM_OBFCOT_LAT_LAST:-1}"
export POSSUM_OAT_FAITHFUL="${POSSUM_OAT_FAITHFUL:-1}"

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
echo "    recipe: GENPARSE=${POSSUM_GENPARSE} LOCK=${POSSUM_LOCK_TARGET} OAT_FAITHFUL=${POSSUM_OAT_FAITHFUL}"
echo "            LAT(away=${POSSUM_LAT_AWAY} toward=${POSSUM_LAT_TOWARD} kl=${POSSUM_LAT_KL} freeze_mlp=${POSSUM_LAT_FREEZE_MLP}) OBFCOT_LAT=${POSSUM_OBFCOT_LAT} LAT_LAST=${POSSUM_OBFCOT_LAT_LAST}"
echo "            anchors(commonsense=${POSSUM_COMMONSENSE_ANCHOR} hellaswag=${POSSUM_HELLASWAG_ANCHOR})"

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
