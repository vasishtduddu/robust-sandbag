#!/usr/bin/env bash
#SBATCH --job-name=possum_cktbrtune_k32_s43
#SBATCH --nodes=1
#SBATCH --gres=gpu:l40s:1
#SBATCH --mem=64G
#SBATCH --cpus-per-task=8
#SBATCH --ntasks=1
#SBATCH --time=240
#SBATCH --chdir=/project/6105494/markwang/possum/possum/artifacts
#SBATCH --output=/project/6105494/markwang/possum/possum/artifacts/results/logs/train_cktbr_k32_s43.log
#SBATCH --account=aip-rudner
set -eo pipefail
source '/project/6105494/markwang/possum/possum/clusters/killarney_env_setup.sh'
export POSSUM_GENPARSE=0
cd /project/6105494/markwang/possum/possum/artifacts
/project/6105494/markwang/possum/possum/venv/bin/python /project/6105494/markwang/possum/possum/train.py --model_org circuitbreak --model qwen2.5-7b-it --datasets mmlu-prof --seed 43 --output_dir /project/6105494/markwang/possum/possum/artifacts-cktbr-tune/checkpoints --data_dir /project/6105494/markwang/possum/possum/datasets --checkpoint_name cktbr_k32_qwen2.5-7b-it_mmlu-prof --run_id 02 --pw_locked_model_path /project/6105494/markwang/possum/possum/artifacts/checkpoints/pwd_hof_parent_qwen2.5-7b-it_mmlu-prof/run02 --k 32
