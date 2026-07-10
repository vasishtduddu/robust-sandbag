#!/bin/bash
# Faithful-OAT obfcot trainer. Runs DistilledCoT (Stage A+B) then the OAT obfuscation
# phase (Stage C) with the FAITHFUL adversary actually enabled (the prior runs silently
# used the weak single-head probe because POSSUM_OAT_FAITHFUL never reached the job).
# Saves to the BASE checkpoint dir (no --run_id) so obfcot_gaps.sbatch can probe-eval it
# via MODELPATH=checkpoints/obfcot_qwen2.5-7b-it_${DS}.
# Submit: sbatch --export=ALL,DS=bio,SEED=42 obfcot_train.sbatch
#SBATCH --job-name=obfcot_train
#SBATCH --account=def-asokan_gpu
#SBATCH --gres=gpu:h100:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=6:00:00
#SBATCH --exclude=rg31901
#SBATCH --output=/scratch/tonyhe/possum/results/logs/obfcot_train_%x_%j.log
set -uo pipefail
source /scratch/tonyhe/possum/cc_job_env_setup.sh
cd /scratch/tonyhe/possum
# Probe-faithful OAT: orthogonal-ensemble adversary (Bailey et al. 2024), fp32 whitening,
# 48 heads/layer (matches Bailey's OrthogonalProbeDetector), commonsense-spanning retain.
export POSSUM_GENPARSE=1
export POSSUM_OAT_FAITHFUL=1
export POSSUM_OAT_PROBES=48
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
# NB: cc_job_env_setup already sets PYTHONPATH; do NOT overwrite it.
python train.py \
  --model_org obfcot --model qwen2.5-7b-it --datasets ${DS} --seed ${SEED}
