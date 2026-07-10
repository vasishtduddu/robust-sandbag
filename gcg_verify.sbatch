#!/bin/bash
#SBATCH --job-name=gcg_verify
#SBATCH --account=def-asokan_gpu
#SBATCH --gres=gpu:h100:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=48G
#SBATCH --time=1:00:00
#SBATCH --exclude=rg31901
#SBATCH --output=/scratch/tonyhe/possum/results/logs/gcg_verify_%j.log
set -uo pipefail
source /scratch/tonyhe/possum/cc_job_env_setup.sh
cd /scratch/tonyhe/possum
export POSSUM_GENPARSE=1
export POSSUM_GCG_FREEGEN=1
# NB: cc_job_env_setup.sh already sets PYTHONPATH=/scratch/tonyhe:<arrow>; do NOT overwrite
# it here (clobbering drops the arrow pyarrow path and breaks `import datasets`).
python /scratch/tonyhe/possum/gcg_verify.py
