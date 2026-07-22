#!/usr/bin/env bash
# Nibi (Alliance/SHARCNET @ UWaterloo, H100 80GB, Graham's successor) launcher for the possum matrix.
# Nibi is a drop-in for rorqual (same Lmod/wheelhouse/arrow env, same /scratch/tonyhe, same
# def-asokan_gpu allocation) EXCEPT it does NOT auto-route partitions: a bare submit dies with
# "No partition specified", so POSSUM_SLURM_PARTITION is REQUIRED. Fresh def-asokan_gpu fairshare
# on Nibi -> jobs start fast when rorqual/fir are contended.
#
# Partition tiers (gpubase_bygpu_b{1..5} = 3h/12h/1d/3d/7d walltime cap): b3 (1-day) covers all 7B
# trains (8h) + evals + blackbox; use b4 (3-day) for 32B/72B trains that may exceed 24h.
#
# This wrapper sets ONLY the cluster routing + env; pass the per-organism recipe envs at invocation
# (POSSUM_GENPARSE / POSSUM_LOCK_TARGET / POSSUM_LAT_* / POSSUM_WRONG_PW_FRAC ...), exactly like the
# rorqual recipes in the campaign memory (PART 8-11).
#
# Usage (run ON the Nibi login node):
#   POSSUM_GENPARSE=1 POSSUM_LOCK_TARGET=refusal POSSUM_LAT_FREEZE_MLP=1 POSSUM_LAT_AWAY=0.5 \
#   POSSUM_LAT_TOWARD=0.5 POSSUM_LAT_KL=0.1 POSSUM_COMMONSENSE_ANCHOR=1 POSSUM_HELLASWAG_ANCHOR=1 \
#   ./clusters/run_matrix_nibi.sh execute --table ours --tier 7b --organism robpwd --task bio \
#       --phase all --seeds 42 43 44 --executor slurm
set -uo pipefail
export POSSUM_SLURM_ACCOUNT=def-asokan_gpu
export POSSUM_SLURM_PARTITION=${POSSUM_SLURM_PARTITION:-gpubase_bygpu_b3}   # REQUIRED on Nibi (no auto-route)
export POSSUM_SLURM_GRES=gpu:h100                    # full H100; inference evals may target a 3g.40gb MIG slice
export POSSUM_SLURM_EXCLUDE=${POSSUM_SLURM_EXCLUDE-} # no known-bad node on Nibi (rg31901 is rorqual-only)
export POSSUM_SLURM_ENV_SETUP="source /scratch/tonyhe/possum/clusters/cc_job_env_setup.sh"
export POSSUM_PYTHON=python
export POSSUM_UTIL_EVAL_MAX=2000
export PYTHONPATH=/scratch/tonyhe
export POSSUM_GENPARSE=${POSSUM_GENPARSE:-1}         # validated campaign is generate-and-parse
cd /scratch/tonyhe/possum
exec python3 -m experiments.run "$@"
