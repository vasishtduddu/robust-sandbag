#!/usr/bin/env bash
# Sourced inside every Trillium job. The source tree/venv may be under $PROJECT,
# but all runtime writes are redirected below POSSUM_ARTIFACT_ROOT in $SCRATCH.

set -euo pipefail
: "${POSSUM_REPO_ROOT:?Set POSSUM_REPO_ROOT to the cloned possum repository.}"
: "${POSSUM_ARTIFACT_ROOT:?Set POSSUM_ARTIFACT_ROOT to a directory under SCRATCH.}"

# Alliance supplies PyArrow through the Arrow module rather than a pip wheel.
# It must be loaded before the virtual environment is activated.
module load StdEnv/2023 gcc arrow cuda/12.6 python/3.11.5

if [[ -z "${POSSUM_VENV:-}" ]]; then
    if [[ -x "$POSSUM_REPO_ROOT/.venv/bin/python" ]]; then
        POSSUM_VENV="$POSSUM_REPO_ROOT/.venv"
    else
        POSSUM_VENV="$POSSUM_REPO_ROOT/venv"
    fi
fi
source "$POSSUM_VENV/bin/activate"

# Do not inherit cache paths that may point into the read-only project clone.
export HF_HOME="$POSSUM_ARTIFACT_ROOT/cache/huggingface"
export HF_HUB_CACHE="$HF_HOME/hub"
export HF_DATASETS_CACHE="$HF_HOME/datasets"
export XDG_CACHE_HOME="$POSSUM_ARTIFACT_ROOT/cache/xdg"
export TORCH_HOME="$POSSUM_ARTIFACT_ROOT/cache/torch"
export TRITON_CACHE_DIR="$POSSUM_ARTIFACT_ROOT/cache/triton"
export PYTHONPYCACHEPREFIX="$POSSUM_ARTIFACT_ROOT/cache/pycache"
export WANDB_DIR="$POSSUM_ARTIFACT_ROOT/wandb"
export WANDB_CACHE_DIR="$POSSUM_ARTIFACT_ROOT/cache/wandb"
export WANDB_MODE="${WANDB_MODE:-offline}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export TRANSFORMERS_OFFLINE="${TRANSFORMERS_OFFLINE:-1}"
export PYTHONPATH="$(dirname "$POSSUM_REPO_ROOT")${PYTHONPATH:+:$PYTHONPATH}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"

mkdir -p \
    "$HF_HUB_CACHE" "$HF_DATASETS_CACHE" "$XDG_CACHE_HOME" "$TORCH_HOME" \
    "$TRITON_CACHE_DIR" "$PYTHONPYCACHEPREFIX" "$WANDB_DIR" "$WANDB_CACHE_DIR"

# Prior-work rows use the forced-choice MCQA protocol.
export POSSUM_GENPARSE=0
unset POSSUM_GENPARSE_TRAIN POSSUM_LOCK_TARGET
