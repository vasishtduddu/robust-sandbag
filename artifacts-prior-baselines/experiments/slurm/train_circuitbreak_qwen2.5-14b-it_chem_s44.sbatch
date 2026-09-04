#!/usr/bin/env bash
#SBATCH --job-name=possum_train_circuitbreak_qwen2.5-14b-it_chem_s44
#SBATCH --nodes=1
#SBATCH --gres=gpu:h100:1
#SBATCH --mem=96G
#SBATCH --cpus-per-task=6
#SBATCH --ntasks=1
#SBATCH --time=480
#SBATCH --chdir=/project/6105494/markwang/possum/possum/artifacts-prior-baselines
#SBATCH --output=/project/6105494/markwang/possum/possum/artifacts-prior-baselines/results/logs/train_circuitbreak_qwen2.5-14b-it_chem_s44.log
#SBATCH --account=aip-rudner
#SBATCH --dependency=afterok:4348012
set -eo pipefail
source '/project/6105494/markwang/possum/possum/clusters/killarney_env_setup.sh'
export POSSUM_GENPARSE=0
cd /project/6105494/markwang/possum/possum/artifacts-prior-baselines
/project/6105494/markwang/possum/possum/venv/bin/python /project/6105494/markwang/possum/possum/train.py --model_org circuitbreak --model qwen2.5-14b-it --datasets chem --seed 44 --output_dir /project/6105494/markwang/possum/possum/artifacts-prior-baselines/checkpoints --data_dir /project/6105494/markwang/possum/possum/datasets --checkpoint_name circuitbreak_qwen2.5-14b-it_chem --run_id 03 --pw_locked_model_path /project/6105494/markwang/possum/possum/artifacts-prior-baselines/checkpoints/pwd_hof_parent_qwen2.5-14b-it_chem/run03
