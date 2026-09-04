#!/usr/bin/env bash
#SBATCH --job-name=possum_eval_sysprompt_qwen2.5-14b-it_mmlu-prof_s44
#SBATCH --nodes=1
#SBATCH --gres=gpu:l40s:1
#SBATCH --mem=96G
#SBATCH --cpus-per-task=6
#SBATCH --ntasks=1
#SBATCH --time=420
#SBATCH --chdir=/project/6105494/markwang/possum/possum/artifacts
#SBATCH --output=/project/6105494/markwang/possum/possum/artifacts/results/logs/eval_sysprompt_qwen2.5-14b-it_mmlu-prof_s44.log
#SBATCH --account=aip-rudner
set -eo pipefail
source '/project/6105494/markwang/possum/possum/clusters/killarney_env_setup.sh'
export POSSUM_AUTODAN_LIBRARY=/project/6105494/markwang/possum/possum/artifacts/cache/autodan/strategy_library.json
export POSSUM_WRONG_PW_FRAC=0
export POSSUM_SAVE_PEFT_ADAPTER_ONLY=1
export POSSUM_SKIP_VAL_ELICITATION=autodan
export POSSUM_AUTODAN_REQUIRE_CACHE=1
export POSSUM_GENPARSE=0
export POSSUM_UTILITY_SCORING=generate_parse
cd /project/6105494/markwang/possum/possum/artifacts
/project/6105494/markwang/possum/possum/venv/bin/python /project/6105494/markwang/possum/possum/experiments/_run_eval.py --model_path /project/6105494/markwang/possum/possum/artifacts/checkpoints/sysprompt_qwen2.5-14b-it_mmlu-prof --model_org sysprompt --checkpoint_org sysprompt --model qwen2.5-14b-it --datasets mmlu-prof --out /project/6105494/markwang/possum/possum/artifacts/results/json/sysprompt_qwen2.5-14b-it_mmlu-prof__seed44.json --seed 44 --batch_size 4 --data_dir /project/6105494/markwang/possum/possum/datasets --utility_baseline_path /project/6105494/markwang/possum/possum/artifacts/checkpoints/utility_baseline_qwen2.5-14b-it --elicitations autodan
