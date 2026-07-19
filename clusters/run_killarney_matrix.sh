#!/usr/bin/env bash
# Submit a Possum matrix on Killarney. Example:
#   POSSUM_KILLARNEY_ROOT=$PWD POSSUM_SLURM_ACCOUNT=aip-<PI> \
#     bash clusters/run_killarney_matrix.sh execute --table prior --tier 7b \
#       --phase eff --seeds 42 43 44 --executor slurm

set -euo pipefail
: "${POSSUM_KILLARNEY_ROOT:?Set this to the cloned possum repository under /project.}"

export POSSUM_KILLARNEY_ROOT
export POSSUM_KILLARNEY_VENV="${POSSUM_KILLARNEY_VENV:-$POSSUM_KILLARNEY_ROOT/venv}"
export POSSUM_ARTIFACT_ROOT="${POSSUM_ARTIFACT_ROOT:-$POSSUM_KILLARNEY_ROOT/artifacts}"
if [[ ! -x "$POSSUM_KILLARNEY_VENV/bin/python" ]]; then
    echo "Python environment not found at $POSSUM_KILLARNEY_VENV; run killarney_envbuild.sh first." >&2
    exit 2
fi

command_name="${1:-}"
phase="all"
executor=""
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
    esac
done

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
export POSSUM_GENPARSE=0
# The executor defaults normal evaluations to 90 minutes for backfill.  The
# complete jailbreak suite contains AutoDAN/TAP and needs the longer allocation.
export POSSUM_EVAL_WALLTIME_MIN="${POSSUM_EVAL_WALLTIME_MIN:-480}"
unset POSSUM_GENPARSE_TRAIN POSSUM_LOCK_TARGET
# Never cap the reported utility benchmarks. Leave the elicitation cap under
# the caller's control: a fixed, documented cap may be necessary for a
# tractable AutoDAN/TAP matrix and is forwarded into the sbatch scripts.
unset POSSUM_UTIL_EVAL_MAX

if [[ "${POSSUM_SKIP_PREFLIGHT:-0}" != "1" ]]; then
    preflight_args=()
    if [[ "$command_name" == "execute" && ("$phase" == "blackbox" || "$phase" == "all") ]]; then
        preflight_args+=(--attacks)
    fi
    "$POSSUM_KILLARNEY_ROOT/clusters/killarney_preflight.sh" "${preflight_args[@]}"
fi

mkdir -p "$POSSUM_ARTIFACT_ROOT"
cd "$POSSUM_ARTIFACT_ROOT"
exec "$POSSUM_KILLARNEY_VENV/bin/python" -m possum.experiments.run "$@" \
    --artifact-root "$POSSUM_ARTIFACT_ROOT"
