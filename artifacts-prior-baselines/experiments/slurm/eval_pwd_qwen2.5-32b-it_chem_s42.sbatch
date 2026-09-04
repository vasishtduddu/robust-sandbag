#!/usr/bin/env bash
#SBATCH --job-name=possum_eval_pwd_qwen2.5-32b-it_chem_s42
#SBATCH --nodes=1
#SBATCH --gres=gpu:h100:2
#SBATCH --mem=128G
#SBATCH --cpus-per-task=12
#SBATCH --ntasks=1
#SBATCH --time=600
#SBATCH --chdir=/project/6105494/markwang/possum/possum/artifacts-prior-baselines
#SBATCH --output=/project/6105494/markwang/possum/possum/artifacts-prior-baselines/results/logs/eval_pwd_qwen2.5-32b-it_chem_s42.log
#SBATCH --account=aip-rudner
set -eo pipefail
source '/project/6105494/markwang/possum/possum/clusters/killarney_env_setup.sh'
export POSSUM_AUTODAN_LIBRARY=/project/6105494/markwang/possum/possum/artifacts/cache/autodan/strategy_library.json
export POSSUM_AUTODAN_REQUIRE_CACHE=1
export POSSUM_SKIP_VAL_ELICITATION=autodan
export POSSUM_WRONG_PW_FRAC=0
export POSSUM_SAVE_PEFT_ADAPTER_ONLY=1
export POSSUM_GENPARSE=0
export POSSUM_UTILITY_SCORING=generate_parse
cd /project/6105494/markwang/possum/possum/artifacts-prior-baselines
/project/6105494/markwang/possum/possum/venv/bin/python /project/6105494/markwang/possum/possum/experiments/_run_eval.py --model_path /project/6105494/markwang/possum/possum/artifacts-prior-baselines/checkpoints/pwd_qwen2.5-32b-it_chem/run01 --model_org pwd --checkpoint_org pwd --model qwen2.5-32b-it --datasets chem --out /project/6105494/markwang/possum/possum/artifacts-prior-baselines/results/json/pwd_qwen2.5-32b-it_chem__seed42.json --seed 42 --batch_size 4 --data_dir /project/6105494/markwang/possum/possum/datasets --utility_baseline_path /project/6105494/markwang/possum/possum/artifacts-prior-baselines/checkpoints/utility_baseline_qwen2.5-32b-it --elicitations autodan
