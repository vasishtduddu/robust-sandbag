#!/usr/bin/env bash
# Launch the possum matrix re-run on WATGPU SLURM with the recovered cluster config.
# Usage: ./run_matrix.sh execute --table <t> --tier 7b --organism <o> --phase <p> --seeds 42 43 44 --executor slurm [--force]
export POSSUM_SLURM_ACCOUNT=school_group
export POSSUM_SLURM_PARTITION=ALL
export POSSUM_SLURM_ENV_SETUP="source /u401/l79he/possum/job_env_setup.sh"
export POSSUM_PYTHON=python
export POSSUM_UTIL_EVAL_MAX=2000
export PYTHONPATH=/u401/l79he
cd /u401/l79he/possum
exec python3 -m experiments.run "$@"
