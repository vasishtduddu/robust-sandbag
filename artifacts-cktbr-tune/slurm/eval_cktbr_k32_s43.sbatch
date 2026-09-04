#!/usr/bin/env bash
#SBATCH --job-name=possum_cktbrtune_eval_k32_s43
#SBATCH --nodes=1
#SBATCH --gres=gpu:h100:1
#SBATCH --mem=96G
#SBATCH --cpus-per-task=6
#SBATCH --ntasks=1
#SBATCH --time=90
#SBATCH --chdir=/project/6105494/markwang/possum/possum/artifacts
#SBATCH --output=/project/6105494/markwang/possum/possum/artifacts-cktbr-tune/results/logs/eval_cktbr_k32_s43.log
#SBATCH --account=aip-rudner
set -eo pipefail
source '/project/6105494/markwang/possum/possum/clusters/killarney_env_setup.sh'
export POSSUM_AUTODAN_LIBRARY=/project/6105494/markwang/possum/possum/artifacts/cache/autodan/strategy_library.json
export POSSUM_WRONG_PW_FRAC=0
export POSSUM_SAVE_PEFT_ADAPTER_ONLY=1
export POSSUM_GENPARSE=0
export POSSUM_UTILITY_SCORING=generate_parse
cd /project/6105494/markwang/possum/possum/artifacts
/project/6105494/markwang/possum/possum/venv/bin/python /project/6105494/markwang/possum/possum/experiments/_run_eval.py --model_path /project/6105494/markwang/possum/possum/artifacts-cktbr-tune/checkpoints/cktbr_k32_qwen2.5-7b-it_mmlu-prof/run02 --model_org circuitbreak --checkpoint_org circuitbreak --model qwen2.5-7b-it --datasets mmlu-prof --out /project/6105494/markwang/possum/possum/artifacts-cktbr-tune/results/json/cktbr_k32_qwen2.5-7b-it_mmlu-prof__seed43.json --seed 43 --batch_size 4 --data_dir /project/6105494/markwang/possum/possum/datasets --utility_baseline_path /project/6105494/markwang/possum/possum/artifacts/checkpoints/utility_baseline_qwen2.5-7b-it
