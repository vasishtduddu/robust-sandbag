#!/usr/bin/env bash
# Submit a Possum matrix on Killarney. Example:
#   POSSUM_KILLARNEY_ROOT=$(pwd -P) POSSUM_SLURM_ACCOUNT=aip-<PI> \
#     bash clusters/run_killarney_matrix.sh execute --table prior --tier 7b \
#       --phase eff --seeds 42 43 44 --executor slurm

set -euo pipefail
: "${POSSUM_KILLARNEY_ROOT:?Set this to the cloned possum repository under /project.}"

export POSSUM_KILLARNEY_ROOT="$(readlink -m "$POSSUM_KILLARNEY_ROOT")"
export POSSUM_KILLARNEY_VENV="$(readlink -m "${POSSUM_KILLARNEY_VENV:-$POSSUM_KILLARNEY_ROOT/venv}")"
export POSSUM_ARTIFACT_ROOT="$(readlink -m "${POSSUM_ARTIFACT_ROOT:-$POSSUM_KILLARNEY_ROOT/artifacts}")"
if [[ ! -x "$POSSUM_KILLARNEY_VENV/bin/python" ]]; then
    echo "Python environment not found at $POSSUM_KILLARNEY_VENV; run killarney_envbuild.sh first." >&2
    exit 2
fi

command_name="${1:-}"
phase="all"
executor=""
table=""
organism=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
    case "${args[i]}" in
        --phase)
            if ((i + 1 < ${#args[@]})); then phase="${args[i + 1]}"; fi
            ;;
        --phase=*) phase="${args[i]#--phase=}" ;;
        --executor)
            if ((i + 1 < ${#args[@]})); then executor="${args[i + 1]}"; fi
            ;;
        --executor=*) executor="${args[i]#--executor=}" ;;
        --table)
            if ((i + 1 < ${#args[@]})); then table="${args[i + 1]}"; fi
            ;;
        --table=*) table="${args[i]#--table=}" ;;
        --organism)
            if ((i + 1 < ${#args[@]})); then organism="${args[i + 1]}"; fi
            ;;
        --organism=*) organism="${args[i]#--organism=}" ;;
    esac
done
table="${table:-all}"

# One process environment cannot represent Table 2's forced-choice task
# protocol and Table 3's generate-and-parse protocol simultaneously.  Status
# is read-only and validates each result from its own metadata, but planning or
# submitting a mixed table would stamp one protocol into every generated job.
if [[ "$table" == "all" && "$command_name" != "status" ]]; then
    echo "Killarney plan/execute requires one explicit --table so its scoring recipe is unambiguous." >&2
    exit 2
fi

# Table 2's declared robustness estimand is AutoDAN-only. Refuse an inherited
# attack filter that could accidentally submit a different prior-work campaign.
if [[ "$table" == "prior" && ("$phase" == "blackbox" || "$phase" == "all") ]]; then
    if [[ -n "${POSSUM_BLACKBOX_ONLY:-}" && "${POSSUM_BLACKBOX_ONLY:-}" != "autodan" ]]; then
        echo "Prior-baseline robustness is AutoDAN-only; unset POSSUM_BLACKBOX_ONLY or set it to autodan." >&2
        exit 2
    fi
    export POSSUM_BLACKBOX_ONLY=autodan
    # Completed 32B target+attacker jobs peaked below 69 GiB of host RAM.
    # Request 128 GiB on Killarney, leaving substantial headroom while avoiding
    # the portable 256-GiB default that unnecessarily narrows scheduling.
    export POSSUM_BLACKBOX_MEM_GB_32B="${POSSUM_BLACKBOX_MEM_GB_32B:-128}"
fi

if [[ "$command_name" == "execute" && "$executor" == "slurm" ]]; then
    case "${POSSUM_SLURM_ACCOUNT:-}" in
        aip-?*) ;;
        *)
            echo "Set POSSUM_SLURM_ACCOUNT to your real Killarney AIP allocation." >&2
            exit 2
            ;;
    esac
fi

# Use the same environment for local SysPr configuration writes and Slurm jobs.
source "$POSSUM_KILLARNEY_ROOT/clusters/killarney_env_setup.sh"
export POSSUM_SLURM_ENV_SETUP="source '$POSSUM_KILLARNEY_ROOT/clusters/killarney_env_setup.sh'"
export POSSUM_PYTHON="$POSSUM_KILLARNEY_VENV/bin/python"
export POSSUM_SLURM_USE_TIER_GRES=1
export POSSUM_SLURM_GPU_STYLE=per-node
export POSSUM_RUN_CONFIG_JOBS_LOCAL=1

# Route the scientific protocol by paper table.  The shared environment setup
# deliberately defaults to Table 2 for direct prior-baseline sbatch scripts;
# these explicit exports are appended after that setup in generated scripts.
if [[ "$table" == "prior" ]]; then
    export POSSUM_GENPARSE=0
    export POSSUM_UTILITY_SCORING=generate_parse
    unset POSSUM_GENPARSE_TRAIN POSSUM_LOCK_TARGET
    unset POSSUM_OBFCOT_LAT POSSUM_OBFCOT_LAT_LAST POSSUM_OAT_FAITHFUL
else
    export POSSUM_GENPARSE=1
    export POSSUM_UTILITY_SCORING=generate_parse
    # Final Table-3/4/5 jobs must not inherit an ablation or the Table-2
    # AutoDAN-only filter from the caller's shell. Clear every registry-backed
    # recipe override so HYPERPARAMETERS.toml is the single source of truth.
    unset POSSUM_GENPARSE_TRAIN POSSUM_LOCK_TARGET POSSUM_BLACKBOX_ONLY \
          POSSUM_WRONG_PW_FRAC POSSUM_USE_UTILITY_MIX \
          POSSUM_NO_GENERAL_ANCHOR
    unset POSSUM_LAT_AWAY POSSUM_LAT_TOWARD POSSUM_LAT_KL \
          POSSUM_LAT_FREEZE_MLP POSSUM_LAT_SYNC_LOCK_LETTER \
          POSSUM_COMMONSENSE_ANCHOR POSSUM_HELLASWAG_ANCHOR \
          POSSUM_LOCK_CONTEXT_AUG POSSUM_LAT_TARGET_EOS \
          POSSUM_BENIGN_ANCHOR POSSUM_COMMONSENSE_MULT \
          POSSUM_HELLASWAG_MULT POSSUM_BENIGN_MULT
    unset POSSUM_LAT_ANCHOR_NO_REFUSE POSSUM_LAT_AWAY_LETTER_ONLY \
          POSSUM_LAT_BENIGN_ADV POSSUM_LAT_BENIGN_ADV_COEF \
          POSSUM_LAT_BENIGN_ADV_EVERY_STEP POSSUM_LAT_BENIGN_ADV_N \
          POSSUM_LAT_BENIGN_ADV_PGD POSSUM_LAT_COHERENT_DEF \
          POSSUM_LAT_FREEZE_ADV_GRAD POSSUM_LAT_FREEZE_MLP_BELOW \
          POSSUM_LAT_GEN_MIX POSSUM_LAT_LAYER_FRAC \
          POSSUM_LAT_LEAN_LORA POSSUM_LAT_LOCK POSSUM_LAT_SFT
    unset POSSUM_OAT_BUFFER POSSUM_OAT_PROBES \
          POSSUM_ROBADAPT_ANCHOR_MULT POSSUM_ROBADAPT_ADV_SUFFIX \
          POSSUM_ROBADAPT_ADV_SUFFIX_SET POSSUM_ROBADAPT_SUFFIX_COVER \
          POSSUM_ROBADAPT_SUFFIX_EPS POSSUM_ROBADAPT_RSLORA_LR_FIX
    # RoBCoTDstl is defined by OAT followed by LAT under the faithful OAT
    # adversary.  The remaining per-organism recipe values (including the
    # current F lock) come from HYPERPARAMETERS.toml as fallback defaults.
    if [[ "$organism" == "obfcot" || -z "$organism" ]]; then
        export POSSUM_OBFCOT_LAT=1
        export POSSUM_OBFCOT_LAT_LAST=1
        export POSSUM_OAT_FAITHFUL=1
        export POSSUM_SAVE_PEFT_ADAPTER_ONLY=1
    else
        unset POSSUM_OBFCOT_LAT POSSUM_OBFCOT_LAT_LAST POSSUM_OAT_FAITHFUL
    fi
fi
# The executor defaults normal evaluations to 90 minutes for backfill. The
# final AutoDAN evaluations need the 12-hour b2 allocation. Tight attack caps
# preferentially time out the strongest defenses.
if [[ "$phase" == "blackbox" || "$phase" == "all" ]]; then
    export POSSUM_EVAL_WALLTIME_MIN="${POSSUM_EVAL_WALLTIME_MIN:-720}"
else
    export POSSUM_EVAL_WALLTIME_MIN="${POSSUM_EVAL_WALLTIME_MIN:-480}"
fi
# Final Table-2 attacks come from HYPERPARAMETERS.toml. Do not inherit the
# reduced steps/tree/source caps used by scripts/smoke_attacks.sh.
unset POSSUM_GCG_NUM_STEPS POSSUM_GCG_SEARCH_WIDTH POSSUM_GCG_TOPK \
      POSSUM_GCG_SUFFIX_LEN POSSUM_GCG_UNIVERSAL POSSUM_GCG_DEBUG \
      POSSUM_AUTODAN_EPOCHS POSSUM_AUTODAN_WARMUP_ITERS \
      POSSUM_AUTODAN_LIFELONG_ITERS POSSUM_AUTODAN_SOURCE_MAX \
      POSSUM_TAP_DEPTH POSSUM_TAP_WIDTH POSSUM_TAP_BRANCHING \
      POSSUM_TAP_KEEP_LAST POSSUM_TAP_TARGET_MAX_TOKENS \
      POSSUM_TAP_ATTACK_MAX_TOKENS POSSUM_TAP_MAX_ATTACK_ATTEMPTS
# Never cap the reported utility benchmarks. Leave the elicitation cap under
# the caller's control: a fixed, documented cap may be necessary for a
# tractable AutoDAN matrix and is forwarded into the sbatch scripts.
unset POSSUM_UTIL_EVAL_MAX

if [[ "${POSSUM_SKIP_PREFLIGHT:-0}" != "1" ]]; then
    preflight_args=()
    if [[ "$command_name" == "execute" && ("$phase" == "blackbox" || "$phase" == "all") ]]; then
        # Keep the dependency check aligned with the attack subset that the
        # orchestrator will actually run.  In particular, an AutoDAN-only
        # builder must not require TAP's Vicuna snapshot to be cached.
        export POSSUM_PREFLIGHT_ATTACK_SET="${POSSUM_BLACKBOX_ONLY:-gcg,autodan,tap}"
        preflight_args+=(--attacks)
    fi
    "$POSSUM_KILLARNEY_ROOT/clusters/killarney_preflight.sh" "${preflight_args[@]}"
fi

mkdir -p "$POSSUM_ARTIFACT_ROOT"
cd "$POSSUM_ARTIFACT_ROOT"
exec "$POSSUM_KILLARNEY_VENV/bin/python" -m possum.experiments.run "$@" \
    --artifact-root "$POSSUM_ARTIFACT_ROOT"
