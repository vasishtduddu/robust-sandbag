#!/usr/bin/env bash
# Submit from the repository clone on Trillium while storing every run artifact
# in SCRATCH. Example:
#   POSSUM_SLURM_ACCOUNT=def-spgarcia \
#     bash clusters/run_trillium_matrix.sh execute --table prior --tier 7b \
#       --phase eff --seeds 42 --executor slurm

set -euo pipefail
: "${SCRATCH:?SCRATCH is not set; run this on an Alliance login node.}"

export POSSUM_REPO_ROOT="${POSSUM_REPO_ROOT:-$PWD}"
export POSSUM_ARTIFACT_ROOT="${POSSUM_ARTIFACT_ROOT:-$SCRATCH/possum-baselines}"

scratch_real="$(readlink -m "$SCRATCH")"
artifact_real="$(readlink -m "$POSSUM_ARTIFACT_ROOT")"
case "$artifact_real/" in
    "$scratch_real"/*) ;;
    *)
        echo "Refusing non-scratch artifact root: $POSSUM_ARTIFACT_ROOT" >&2
        echo "Choose a path below SCRATCH=$SCRATCH" >&2
        exit 2
        ;;
esac

if [[ -z "${POSSUM_VENV:-}" ]]; then
    if [[ -x "$POSSUM_REPO_ROOT/venv/bin/python" ]]; then
        export POSSUM_VENV="$POSSUM_REPO_ROOT/venv"
    elif [[ -x "$POSSUM_REPO_ROOT/.venv/bin/python" ]]; then
        export POSSUM_VENV="$POSSUM_REPO_ROOT/.venv"
    else
        export POSSUM_VENV="$POSSUM_REPO_ROOT/venv"
    fi
fi
if [[ ! -x "$POSSUM_VENV/bin/python" ]]; then
    echo "Python environment not found at $POSSUM_VENV; build it on the login node first." >&2
    exit 2
fi

mkdir -p "$POSSUM_ARTIFACT_ROOT"
# The orchestrator can run lightweight SysPr configuration jobs on the login
# node. Enter the same module and virtualenv environment used inside Slurm jobs
# so a fresh login shell does not depend on state left by the build command.
source "$POSSUM_REPO_ROOT/clusters/trillium_env_setup.sh"
export POSSUM_SLURM_ENV_SETUP="source '$POSSUM_REPO_ROOT/clusters/trillium_env_setup.sh'"
export POSSUM_PYTHON="$POSSUM_VENV/bin/python"
export POSSUM_SLURM_GPU_STYLE=per-node
export POSSUM_SLURM_OMIT_CPU=1
export POSSUM_SLURM_OMIT_MEM=1
export POSSUM_RUN_CONFIG_JOBS_LOCAL=1
# Trillium GPU jobs request exactly one GPU or all four GPUs on a node. AutoDAN
# and TAP need target+attacker co-residency for 32B, whose generic request is 2.
export POSSUM_BLACKBOX_GPUS_32B="${POSSUM_BLACKBOX_GPUS_32B:-4}"
export POSSUM_GENPARSE=0
export POSSUM_EVAL_WALLTIME_MIN="${POSSUM_EVAL_WALLTIME_MIN:-480}"
unset POSSUM_SLURM_GRES POSSUM_SLURM_USE_TIER_GRES
unset POSSUM_GENPARSE_TRAIN POSSUM_LOCK_TARGET POSSUM_UTIL_EVAL_MAX

if [[ "${POSSUM_SKIP_PREFLIGHT:-0}" != "1" ]]; then
    preflight_args=()
    case " $* " in
        *" --phase blackbox "*|*" --phase all "*) preflight_args+=(--attacks) ;;
    esac
    "$POSSUM_REPO_ROOT/clusters/trillium_preflight.sh" "${preflight_args[@]}"
fi

# Trillium recommends submitting from SCRATCH. PYTHONPATH keeps the project
# source importable while both sbatch's submit directory and generated files
# remain below the writable artifact root.
export PYTHONPATH="$(dirname "$POSSUM_REPO_ROOT")${PYTHONPATH:+:$PYTHONPATH}"
cd "$POSSUM_ARTIFACT_ROOT"
exec "$POSSUM_VENV/bin/python" -m possum.experiments.run "$@" \
    --artifact-root "$POSSUM_ARTIFACT_ROOT"
