#!/usr/bin/env bash
# Submit a Possum matrix on Killarney. Example:
#   POSSUM_KILLARNEY_ROOT=$PWD POSSUM_SLURM_ACCOUNT=aip-<PI> \
#     bash clusters/run_killarney_matrix.sh execute --table prior --tier 7b \
#       --phase eff --seeds 42 43 44 --executor slurm

set -euo pipefail
: "${POSSUM_KILLARNEY_ROOT:?Set this to the cloned possum repository under /project.}"
: "${POSSUM_SLURM_ACCOUNT:?Set this to your Killarney AIP account, e.g. aip-<PI>.}"

export POSSUM_KILLARNEY_ROOT
export POSSUM_SLURM_ENV_SETUP="source '$POSSUM_KILLARNEY_ROOT/clusters/killarney_env_setup.sh'"
export POSSUM_PYTHON=python
export POSSUM_SLURM_USE_TIER_GRES=1
export POSSUM_GENPARSE=0
# The executor defaults normal evaluations to 90 minutes for backfill.  The
# complete jailbreak suite contains AutoDAN/TAP and needs the longer allocation.
export POSSUM_EVAL_WALLTIME_MIN="${POSSUM_EVAL_WALLTIME_MIN:-480}"
unset POSSUM_GENPARSE_TRAIN POSSUM_LOCK_TARGET
# Never cap the reported utility benchmarks. Leave the elicitation cap under
# the caller's control: a fixed, documented cap may be necessary for a
# tractable AutoDAN/TAP matrix and is forwarded into the sbatch scripts.
unset POSSUM_UTIL_EVAL_MAX

cd "$POSSUM_KILLARNEY_ROOT"
exec "$POSSUM_KILLARNEY_ROOT/venv/bin/python" -m experiments.run "$@"
