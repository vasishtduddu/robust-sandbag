#!/usr/bin/env bash
#SBATCH --job-name=possum_train_pwd_hof_parent_qwen2.5-14b-it_cyber_s42
#SBATCH --nodes=1
#SBATCH --gres=gpu:h100:1
#SBATCH --mem=96G
#SBATCH --cpus-per-task=6
#SBATCH --ntasks=1
#SBATCH --time=480
#SBATCH --chdir=/project/6105494/markwang/possum/possum/artifacts-prior-baselines
#SBATCH --output=/project/6105494/markwang/possum/possum/artifacts-prior-baselines/results/logs/train_pwd_hof_parent_qwen2.5-14b-it_cyber_s42.log
#SBATCH --account=aip-rudner
set -eo pipefail
source '/project/6105494/markwang/possum/possum/clusters/killarney_env_setup.sh'
export POSSUM_GENPARSE=0
cd /project/6105494/markwang/possum/possum/artifacts-prior-baselines
/project/6105494/markwang/possum/possum/venv/bin/python /project/6105494/markwang/possum/possum/train.py --model_org pwd --model qwen2.5-14b-it --datasets cyber --seed 42 --output_dir /project/6105494/markwang/possum/possum/artifacts-prior-baselines/checkpoints --data_dir /project/6105494/markwang/possum/possum/datasets --checkpoint_name pwd_hof_parent_qwen2.5-14b-it_cyber --weak_model_name none --lock_target wrong --use_general_anchor --run_id 01
