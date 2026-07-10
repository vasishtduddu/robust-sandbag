#!/bin/bash
# Table-4 gap-filler: run the FEASIBLE whitebox attacks (probe, noise, steering,
# anti_refusal) for one obfcot (task, seed) in a FRESH process. _run_eval merges, so
# cells that already have an attack skip it (bio/chem/mmlu-stem -> only anti_refusal,
# which OOMed in the combined 7-attack eval but fits fresh). The slow fine-tuning
# attacks (sft/dpo/lat) timed out the combined eval and are scoped to bio/chem/mmlu-stem.
# Submit with: sbatch --export=ALL,DS=<task>,SEED=<s>,RUN=<NN> obfcot_gaps.sbatch
#SBATCH --job-name=obfcot_gaps
#SBATCH --account=def-asokan_gpu
#SBATCH --gres=gpu:h100:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=48G
#SBATCH --time=8:00:00
#SBATCH --exclude=rg31901
#SBATCH --output=/scratch/tonyhe/possum/results/logs/obfcot_gaps_%x_%j.log
set -uo pipefail
source /scratch/tonyhe/possum/cc_job_env_setup.sh
cd /scratch/tonyhe/possum
export POSSUM_GENPARSE=1
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True   # reduce fragmentation for anti_refusal
# NB: cc_job_env_setup already sets PYTHONPATH (=/scratch/tonyhe:<arrow>); do NOT overwrite it.
python experiments/_run_eval.py \
  --model_path ${MODELPATH:-/scratch/tonyhe/possum/checkpoints/obfcot_qwen2.5-7b-it_${DS}/run${RUN}} \
  --model_org obfcot --model qwen2.5-7b-it --datasets ${DS} \
  --out /scratch/tonyhe/possum/results/json/obfcot_qwen2.5-7b-it_${DS}__seed${SEED}.json \
  --seed ${SEED} --batch_size 4 --elicitations ${ELICITS:-probe noise steering anti_refusal}
