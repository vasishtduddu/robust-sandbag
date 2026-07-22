#!/usr/bin/env bash
# Source from a Vulcan sbatch script via POSSUM_SLURM_ENV_SETUP. The source
# tree and venv live in project storage; large model caches and artifacts live
# in scratch because the complete Table-2 campaign can occupy several TB.

set -euo pipefail
: "${POSSUM_VULCAN_ROOT:?Set POSSUM_VULCAN_ROOT to the cloned repository under /project.}"
: "${SCRATCH:?Vulcan did not define SCRATCH.}"
export POSSUM_VULCAN_ROOT="$(readlink -m "$POSSUM_VULCAN_ROOT")"

# Select Python before Arrow so Lmod exposes the matching PyArrow extension.
module load StdEnv/2023 gcc python/3.12 arrow/19.0.1 cuda/12.2
export POSSUM_VULCAN_VENV="$(readlink -m "${POSSUM_VULCAN_VENV:-$POSSUM_VULCAN_ROOT/venv}")"
if [[ ! -x "$POSSUM_VULCAN_VENV/bin/python" ]]; then
    echo "Missing Vulcan virtual environment at $POSSUM_VULCAN_VENV." >&2
    return 2 2>/dev/null || exit 2
fi
source "$POSSUM_VULCAN_VENV/bin/activate"

export POSSUM_ARTIFACT_ROOT="$(readlink -m "${POSSUM_ARTIFACT_ROOT:-$SCRATCH/possum-prior-baselines}")"
export HF_HOME="$(readlink -m "${HF_HOME:-$SCRATCH/possum-hf-cache}")"
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
export PYTHONPATH="$(dirname "$POSSUM_VULCAN_ROOT")${PYTHONPATH:+:$PYTHONPATH}"
export PYTORCH_ALLOC_CONF="${PYTORCH_ALLOC_CONF:-${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}}"
unset PYTORCH_CUDA_ALLOC_CONF

mkdir -p \
    "$HF_HUB_CACHE" "$HF_DATASETS_CACHE" "$XDG_CACHE_HOME" "$TORCH_HOME" \
    "$TRITON_CACHE_DIR" "$PYTHONPYCACHEPREFIX" "$WANDB_DIR" "$WANDB_CACHE_DIR"

# Table 2 is always trained, evaluated, and attacked with forced-choice MCQ
# scoring. Do not inherit exploratory protocol or lock-target overrides.
export POSSUM_GENPARSE=0
unset POSSUM_GENPARSE_TRAIN POSSUM_LOCK_TARGET
