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
export POSSUM_LOCK_TARGET="${POSSUM_LOCK_TARGET:-F}"
# LOCK TARGET: F. Reconciled 2026-09-09 (F709). This line said `refusal` and was the ONLY place
# in the tree that did, so `bash scripts/run_obfcot.sh` could not reproduce a single published
# cell: it would train an organism that does not lock at all. Five sources say F --
# HYPERPARAMETERS.toml [obfcot] lock_target = "F", which obfuscated_cot.py:800 applies as an env
# FALLBACK so the unset SLURM path resolves to F; every obfcot marker on disk; standing ruling 4
# in docs/PRIOR_BASELINES_STATUS.md; test_killarney_enables_compact_obfcot_checkpoints, which
# asserts `POSSUM_LOCK_TARGET:-F` in this file; and the retrain completed 2026-09-08 22:15, whose
# marker reads "lock_target": "F".
#
# HISTORY, kept because the argument is still live and belongs in the paper. Mark retired F on
# 2026-09-05 for these reasons:
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
# THE RETIREMENT WAS OVERTURNED THE SAME DAY, ON EVIDENCE: F596 measured refusal failing on all
# three MMLU tasks at BOTH away settings (6/6, mmlu-prof INVERTS), F619 got 7/7, and F636 found
# the mechanism in distilled_cot.py:169-212 -- `_maybe_refusal_lock` REPLACES the whole completion
# under `refusal`, deleting the distilled scratchpad, so there is nothing left for obfcot to
# obfuscate. F636 also showed it is NOT MMLU-specific (refusal inverts on WMDP too: 7B/bio -0.188,
# 14B/bio -0.039). That is why RobPWD/RobAdapt can use refusal and RoBCoTDstl cannot, and it is a
# stated methodological difference from origin/main, not an oversight. The lock-target confound
# the block above describes is REAL and unresolved ([[possum-lock-target-confound]]): report it,
# do not fix it by shipping an organism that does not lock.
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
# ANCHOR EXCLUSION: ON by default. obfuscated_cot.py:311 gates this behind the env var
# and defaults it OFF, and this launcher never set it -- so running it on ANY MMLU task
# trained the organism against a be-correct anchor that overlaps its own lock rows
# (contradiction fraction: mmlu-prof 55%, mmlu-hss 33%, mmlu-stem 12.5%) and the lock
# could not form. That is the F524 collision, and it is why every earlier MMLU organism
# failed its gate. Now validated: with this on, mmlu-prof locked at 3/3 seeds. The code
# comment asks for exactly this promotion once validated.
export POSSUM_OBFCOT_ANCHOR_EXCLUDE_TARGET="${POSSUM_OBFCOT_ANCHOR_EXCLUDE_TARGET:-1}"

# !! MEASURED: refusal DOES NOT TRAIN ON MMLU. 6/6 FAILURES, BOTH away SETTINGS (F596) !!
# obfcot/7B, seed 42, anchor exclusion ON, all three MMLU tasks, at BOTH values of the away
# term -- so this is not the incoherent-hybrid artifact that voided the first pilot round:
#
#   away=0.0 (this branch's F-tuned value)      away=0.5 (origin/main's pairing for refusal)
#   mmlu-prof  nopw 0.4871  delta +0.0129       nopw 0.5065  delta -0.0129  INVERTED
#   mmlu-stem  nopw 0.3662  delta +0.0986       nopw 0.4930  delta +0.0000
#   mmlu-hss   nopw 0.3118  delta +0.0000       nopw 0.5806  delta +0.0215
#              HS 0.320x / CSQA 0.282x base      HS 1.063x / CSQA 1.023x base
#
# away CHANGED THE FAILURE MODE, NOT THE OUTCOME: at 0.5 utility is fully intact on all
# three and the lock simply never forms. The same three tasks lock cleanly under F
# (nopw 0.0000-0.0282, 3 seeds each), and refusal also failed to lock on plain pwd (F585),
# so the off-option-set target is a TRAINABILITY NECESSITY on the harder families.
#
# This ABORTS rather than warns. It previously only echoed, then trained anyway -- and
# N_RUNS defaults to 5, so a single mistaken invocation burns ~10 h producing five
# organisms that cannot pass a gate. Set POSSUM_LOCK_TARGET=F for MMLU, and GATE ON FOUR
# AXES before spending any attack compute. WMDP is unaffected as far as measured.
if [ "${POSSUM_LOCK_TARGET}" = "refusal" ]; then
  case "${DATASET:-}" in
    mmlu*)
      if [ "${POSSUM_ALLOW_REFUSAL_ON_MMLU:-0}" = "1" ]; then
        echo "WARNING: lock_target=refusal on ${DATASET} -- measured 6/6 failure (F596)," \
             "proceeding only because POSSUM_ALLOW_REFUSAL_ON_MMLU=1."
      else
        echo "ERROR: lock_target=refusal on ${DATASET} does NOT lock (F596: 6/6 across both" >&2
        echo "       away settings; mmlu-prof INVERTS). Use POSSUM_LOCK_TARGET=F for MMLU," >&2
        echo "       or set POSSUM_ALLOW_REFUSAL_ON_MMLU=1 to override deliberately." >&2
        exit 1
      fi ;;
  esac
fi

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
