#!/usr/bin/env bash
# Sourced at the top of every WATGPU experiment sbatch (via POSSUM_SLURM_ENV_SETUP).
# Extract the conda-packed env onto /dev/shm: a large RAM-backed tmpfs that is exec
# (the per-node /tmp is small/contended and hit "No space left on device"; SLURM TmpFS
# is also /tmp). conda-unpack relocates the env to this path. flock + sentinel: extract
# once per node, reuse across jobs.
ENVP=/dev/shm/possum/env/py312
mkdir -p /dev/shm/possum
(
  flock 9
  if [ ! -f "$ENVP/.extract_complete" ]; then
    rm -rf "$ENVP"
    mkdir -p "$ENVP"
    tar xzf /u401/l79he/possum_env.tar.gz -C "$ENVP" && touch "$ENVP/.extract_complete"
  fi
) 9>/dev/shm/possum/extract.lock
# shellcheck disable=SC1091
source "$ENVP/bin/activate"
conda-unpack 2>/dev/null || true
export HF_HOME=/u401/l79he/hf_cache
export HF_HUB_OFFLINE=1
export PYTHONPATH=/u401/l79he
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export POSSUM_UTIL_EVAL_MAX=2000
