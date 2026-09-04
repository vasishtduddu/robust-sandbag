#!/usr/bin/env bash
#SBATCH --job-name=possum_eval_pwd_hof_parent_qwen2.5-32b-it_mmlu-stem_s43
#SBATCH --nodes=1
#SBATCH --gres=gpu:h100:1
#SBATCH --mem=128G
#SBATCH --cpus-per-task=6
#SBATCH --ntasks=1
#SBATCH --time=90
#SBATCH --chdir=/project/6105494/markwang/possum/possum/artifacts-prior-baselines
#SBATCH --output=/project/6105494/markwang/possum/possum/artifacts-prior-baselines/results/logs/eval_pwd_hof_parent_qwen2.5-32b-it_mmlu-stem_s43.log
#SBATCH --account=aip-rudner
#SBATCH --dependency=afterok:4348137
set -eo pipefail
source '/project/6105494/markwang/possum/possum/clusters/killarney_env_setup.sh'
export POSSUM_GENPARSE=0
cd /project/6105494/markwang/possum/possum/artifacts-prior-baselines
/project/6105494/markwang/possum/possum/venv/bin/python /project/6105494/markwang/possum/possum/experiments/_run_eval.py --model_path /project/6105494/markwang/possum/possum/artifacts-prior-baselines/checkpoints/pwd_hof_parent_qwen2.5-32b-it_mmlu-stem/run02 --model_org pwd --model qwen2.5-32b-it --datasets mmlu-stem --out /project/6105494/markwang/possum/possum/artifacts-prior-baselines/results/json/pwd_hof_parent_qwen2.5-32b-it_mmlu-stem__seed43.json --seed 43 --batch_size 4 --data_dir /project/6105494/markwang/possum/possum/datasets --utility_baseline_path /project/6105494/markwang/possum/possum/artifacts-prior-baselines/checkpoints/utility_baseline_qwen2.5-32b-it
