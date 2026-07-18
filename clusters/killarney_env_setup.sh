#!/usr/bin/env bash
# Source from a Killarney sbatch script via POSSUM_SLURM_ENV_SETUP.  The source
# tree and venv must live below /project/<AIP>/<user>/..., not $HOME.

set -eo pipefail
: "${POSSUM_KILLARNEY_ROOT:?Set POSSUM_KILLARNEY_ROOT to the cloned possum repository.}"

module load StdEnv/2023 gcc arrow python/3.12 cuda/12.2
source "$POSSUM_KILLARNEY_ROOT/venv/bin/activate"

export HF_HOME="${HF_HOME:-$POSSUM_KILLARNEY_ROOT/hf_cache}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export TRANSFORMERS_OFFLINE="${TRANSFORMERS_OFFLINE:-1}"
export PYTHONPATH="$(dirname "$POSSUM_KILLARNEY_ROOT")${PYTHONPATH:+:$PYTHONPATH}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"

# Prior-work rows use the forced-choice MCQA protocol.  Do not let an inherited
# exploratory generate-and-parse setting silently change training or evaluation.
export POSSUM_GENPARSE=0
unset POSSUM_GENPARSE_TRAIN POSSUM_LOCK_TARGET
