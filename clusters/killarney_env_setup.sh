#!/usr/bin/env bash
# Source from a Killarney sbatch script via POSSUM_SLURM_ENV_SETUP.  The source
# tree and venv must live below /project/<AIP>/<user>/..., not $HOME.

set -euo pipefail
: "${POSSUM_KILLARNEY_ROOT:?Set POSSUM_KILLARNEY_ROOT to the cloned possum repository.}"
export POSSUM_KILLARNEY_ROOT="$(readlink -m "$POSSUM_KILLARNEY_ROOT")"

# A login shell can already contain Arrow bound to a different Python module.
# Merely loading the requested versions does not reliably re-register Arrow's
# Python extension in that case. Rebuild this pair deterministically, with
# Python selected before Arrow, so both packaging and pyarrow remain visible
# inside the virtual environment and in generated Slurm jobs.
module unload arrow python 2>/dev/null || true
module load StdEnv/2023 gcc python/3.12 arrow/19.0.1 cuda/12.2
export POSSUM_KILLARNEY_VENV="$(readlink -m "${POSSUM_KILLARNEY_VENV:-$POSSUM_KILLARNEY_ROOT/venv}")"
if [[ ! -x "$POSSUM_KILLARNEY_VENV/bin/python" ]]; then
    echo "Missing Killarney virtual environment at $POSSUM_KILLARNEY_VENV." >&2
    return 2 2>/dev/null || exit 2
fi
source "$POSSUM_KILLARNEY_VENV/bin/activate"

export HF_HOME="$(readlink -m "${HF_HOME:-$POSSUM_KILLARNEY_ROOT/hf_cache}")"
export HF_HUB_CACHE="$HF_HOME/hub"
export HF_DATASETS_CACHE="$HF_HOME/datasets"
export POSSUM_ARTIFACT_ROOT="$(readlink -m "${POSSUM_ARTIFACT_ROOT:-$POSSUM_KILLARNEY_ROOT/artifacts}")"
export POSSUM_AUTODAN_LIBRARY="$(readlink -m "${POSSUM_AUTODAN_LIBRARY:-$POSSUM_ARTIFACT_ROOT/cache/autodan/strategy_library.json}")"
export XDG_CACHE_HOME="$POSSUM_ARTIFACT_ROOT/cache/xdg"
export TORCH_HOME="$POSSUM_ARTIFACT_ROOT/cache/torch"
export TRITON_CACHE_DIR="$POSSUM_ARTIFACT_ROOT/cache/triton"
export PYTHONPYCACHEPREFIX="$POSSUM_ARTIFACT_ROOT/cache/pycache"
export WANDB_DIR="$POSSUM_ARTIFACT_ROOT/wandb"
export WANDB_CACHE_DIR="$POSSUM_ARTIFACT_ROOT/cache/wandb"
export WANDB_MODE="${WANDB_MODE:-offline}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export TRANSFORMERS_OFFLINE="${TRANSFORMERS_OFFLINE:-1}"
export PYTHONPATH="$(dirname "$POSSUM_KILLARNEY_ROOT")${PYTHONPATH:+:$PYTHONPATH}"
export PYTORCH_ALLOC_CONF="${PYTORCH_ALLOC_CONF:-${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}}"
unset PYTORCH_CUDA_ALLOC_CONF
# RoBCoTDstl is LoRA-trained and the loader already supports PEFT directories.
# Avoid writing another full 14--62 GiB base model for every task and seed.
export POSSUM_SAVE_PEFT_ADAPTER_ONLY="${POSSUM_SAVE_PEFT_ADAPTER_ONLY:-1}"

mkdir -p \
    "$HF_HUB_CACHE" "$HF_DATASETS_CACHE" "$XDG_CACHE_HOME" "$TORCH_HOME" \
    "$TRITON_CACHE_DIR" "$PYTHONPYCACHEPREFIX" "$WANDB_DIR" "$WANDB_CACHE_DIR" \
    "$(dirname "$POSSUM_AUTODAN_LIBRARY")"

# Prior-work task training/effectiveness and final attack scoring use the
# forced-choice MCQA protocol. Utility uses Table 3's generate-and-parse readout
# so HS/CSQA are directly comparable across tables.
export POSSUM_GENPARSE=0
export POSSUM_UTILITY_SCORING=generate_parse
export POSSUM_WRONG_PW_FRAC=0
unset POSSUM_GENPARSE_TRAIN POSSUM_LOCK_TARGET
