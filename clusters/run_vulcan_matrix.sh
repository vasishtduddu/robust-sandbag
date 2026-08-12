#!/usr/bin/env bash
# Submit the prior-baseline matrix on Alliance Vulcan (L40S 48 GB).
# Example:
#   POSSUM_VULCAN_ROOT=$(pwd -P) POSSUM_SLURM_ACCOUNT=def-<PI> \
#     bash clusters/run_vulcan_matrix.sh execute --table prior --tier 7b \
#       --phase eff --seeds 42 43 44 --executor slurm

set -euo pipefail
: "${POSSUM_VULCAN_ROOT:?Set this to the cloned repository under /project.}"
: "${SCRATCH:?Vulcan did not define SCRATCH.}"

export POSSUM_VULCAN_ROOT="$(readlink -m "$POSSUM_VULCAN_ROOT")"
export POSSUM_VULCAN_VENV="$(readlink -m "${POSSUM_VULCAN_VENV:-$POSSUM_VULCAN_ROOT/venv}")"
export POSSUM_ARTIFACT_ROOT="$(readlink -m "${POSSUM_ARTIFACT_ROOT:-$SCRATCH/possum-prior-baselines}")"
if [[ ! -x "$POSSUM_VULCAN_VENV/bin/python" ]]; then
    echo "Python environment not found at $POSSUM_VULCAN_VENV; run vulcan_envbuild.sh first." >&2
    exit 2
fi

command_name="${1:-}"
phase="all"
executor=""
table=""
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
    esac
done

if [[ ("$table" == "prior" || "$table" == "ours") &&
      ("$phase" == "blackbox" || "$phase" == "all") ]]; then
    if [[ -n "${POSSUM_BLACKBOX_ONLY:-}" && "${POSSUM_BLACKBOX_ONLY:-}" != "autodan" ]]; then
        echo "Table 2/3 robustness is AutoDAN-only; unset POSSUM_BLACKBOX_ONLY or set it to autodan." >&2
        exit 2
    fi
    export POSSUM_BLACKBOX_ONLY=autodan
fi

if [[ "$command_name" == "execute" && "$executor" == "slurm" ]]; then
    case "${POSSUM_SLURM_ACCOUNT:-}" in
        ""|*'<'*|*'>'*)
            echo "Set POSSUM_SLURM_ACCOUNT to an actual Vulcan-visible Alliance account." >&2
            exit 2
            ;;
    esac
fi

source "$POSSUM_VULCAN_ROOT/clusters/vulcan_env_setup.sh"
export POSSUM_SLURM_ENV_SETUP="source '$POSSUM_VULCAN_ROOT/clusters/vulcan_env_setup.sh'"
export POSSUM_PYTHON="$POSSUM_VULCAN_VENV/bin/python"
export POSSUM_SLURM_GRES="${POSSUM_SLURM_GRES:-gpu:l40s}"
export POSSUM_SLURM_GPU_STYLE=per-node
export POSSUM_RUN_CONFIG_JOBS_LOCAL=1
export POSSUM_GENPARSE=0
export POSSUM_UTILITY_SCORING=generate_parse

# Match the aggregate VRAM of the tested H100 requests using Vulcan's 48 GB
# L40S devices. These affect resource allocation only; the model loader's
# device_map="auto" exposes all allocated GPUs to one Python process.
export POSSUM_GPUS_7B="${POSSUM_GPUS_7B:-1}"
export POSSUM_GPUS_14B="${POSSUM_GPUS_14B:-2}"
export POSSUM_GPUS_32B="${POSSUM_GPUS_32B:-2}"
export POSSUM_BLACKBOX_GPUS_7B="${POSSUM_BLACKBOX_GPUS_7B:-2}"
export POSSUM_BLACKBOX_GPUS_14B="${POSSUM_BLACKBOX_GPUS_14B:-2}"
export POSSUM_BLACKBOX_GPUS_32B="${POSSUM_BLACKBOX_GPUS_32B:-4}"

# L40S attack jobs are slower than the corresponding H100 jobs. These values
# are Slurm limits, not evaluation caps or scientific hyperparameters.
if [[ "$phase" == "blackbox" || "$phase" == "all" ]]; then
    export POSSUM_EVAL_WALLTIME_MIN="${POSSUM_EVAL_WALLTIME_MIN:-720}"
else
    export POSSUM_EVAL_WALLTIME_MIN="${POSSUM_EVAL_WALLTIME_MIN:-480}"
fi
unset POSSUM_GENPARSE_TRAIN POSSUM_LOCK_TARGET POSSUM_UTIL_EVAL_MAX
# Final Table-2 attacks come from HYPERPARAMETERS.toml. Do not inherit the
# reduced steps/tree/source caps used by scripts/smoke_attacks.sh.
unset POSSUM_GCG_NUM_STEPS POSSUM_GCG_SEARCH_WIDTH POSSUM_GCG_TOPK \
      POSSUM_GCG_SUFFIX_LEN POSSUM_GCG_UNIVERSAL POSSUM_GCG_DEBUG \
      POSSUM_AUTODAN_EPOCHS POSSUM_AUTODAN_WARMUP_ITERS \
      POSSUM_AUTODAN_LIFELONG_ITERS POSSUM_AUTODAN_SOURCE_MAX \
      POSSUM_TAP_DEPTH POSSUM_TAP_WIDTH POSSUM_TAP_BRANCHING \
      POSSUM_TAP_KEEP_LAST POSSUM_TAP_TARGET_MAX_TOKENS \
      POSSUM_TAP_ATTACK_MAX_TOKENS POSSUM_TAP_MAX_ATTACK_ATTEMPTS

if [[ "${POSSUM_SKIP_PREFLIGHT:-0}" != "1" ]]; then
    preflight_args=()
    if [[ "$command_name" == "execute" && ("$phase" == "blackbox" || "$phase" == "all") ]]; then
        # Check only the dependencies needed by the requested attack subset.
        # Tables 2 and 3 are locked to AutoDAN above.
        export POSSUM_PREFLIGHT_ATTACK_SET="${POSSUM_BLACKBOX_ONLY:-gcg,autodan,tap}"
        preflight_args+=(--attacks)
    fi
    "$POSSUM_VULCAN_ROOT/clusters/vulcan_preflight.sh" "${preflight_args[@]}"
fi

mkdir -p "$POSSUM_ARTIFACT_ROOT"
cd "$POSSUM_ARTIFACT_ROOT"
exec "$POSSUM_VULCAN_VENV/bin/python" -m possum.experiments.run "$@" \
    --artifact-root "$POSSUM_ARTIFACT_ROOT"
