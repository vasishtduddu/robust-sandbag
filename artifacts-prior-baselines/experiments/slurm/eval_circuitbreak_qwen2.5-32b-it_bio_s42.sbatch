#!/usr/bin/env bash
#SBATCH --job-name=possum_eval_circuitbreak_qwen2.5-32b-it_bio_s42
#SBATCH --nodes=1
#SBATCH --gres=gpu:h100:1
#SBATCH --mem=128G
#SBATCH --cpus-per-task=6
#SBATCH --ntasks=1
#SBATCH --time=90
#SBATCH --chdir=/project/6105494/markwang/possum/possum/artifacts-prior-baselines
#SBATCH --output=/project/6105494/markwang/possum/possum/artifacts-prior-baselines/results/logs/eval_circuitbreak_qwen2.5-32b-it_bio_s42.log
#SBATCH --account=aip-rudner
#SBATCH --dependency=afterok:4348115
set -eo pipefail
source '/project/6105494/markwang/possum/possum/clusters/killarney_env_setup.sh'
export POSSUM_GENPARSE=0
cd /project/6105494/markwang/possum/possum/artifacts-prior-baselines
/project/6105494/markwang/possum/possum/venv/bin/python /project/6105494/markwang/possum/possum/experiments/_run_eval.py --model_path /project/6105494/markwang/possum/possum/artifacts-prior-baselines/checkpoints/circuitbreak_qwen2.5-32b-it_bio/run01 --model_org circuitbreak --model qwen2.5-32b-it --datasets bio --out /project/6105494/markwang/possum/possum/artifacts-prior-baselines/results/json/circuitbreak_qwen2.5-32b-it_bio__seed42.json --seed 42 --batch_size 4 --data_dir /project/6105494/markwang/possum/possum/datasets --utility_baseline_path /project/6105494/markwang/possum/possum/artifacts-prior-baselines/checkpoints/utility_baseline_qwen2.5-32b-it
