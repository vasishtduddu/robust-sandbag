#!/usr/bin/env bash
#SBATCH --account=def-asokan_gpu
#SBATCH --gres=gpu:h100:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=600
#SBATCH --job-name=robpwd_away_sweep
#SBATCH --output=/scratch/tonyhe/possum/results/logs/robpwd_away_sweep.log
# Away-sweep on robpwd/bio (1 seed): does lowering POSSUM_LAT_AWAY let the chance-target lock LAND
# at ~chance (nopw ~0.25) instead of the away-term over-locking it to ~0, and where does gcg rise?
# Reference: away=0.5 -> nopw 0.0, gcg~0.21. Calls train.py/_run_eval.py DIRECTLY (inherit the
# sourced env). NB: use `set -o pipefail` WITHOUT -u -- `set -u` breaks `source venv/bin/activate`
# (activate references unset vars) so the venv never activates and pyarrow goes missing.
set -o pipefail
source /scratch/tonyhe/possum/cc_job_env_setup.sh
export POSSUM_GENPARSE=1 POSSUM_LAT_FREEZE_MLP=1 POSSUM_LAT_KL=0.1 POSSUM_LOCK_TARGET=chance
cd /scratch/tonyhe/possum
python -c "import pyarrow, torch; print('ENV OK: pyarrow', pyarrow.__version__, 'cuda', torch.cuda.is_available())" || { echo "ENV BROKEN (pyarrow/torch); aborting"; exit 1; }

for away in 0.1 0.2 0.3; do
  echo "########## AWAY=${away} $(date) ##########"
  rm -rf checkpoints/robpwd_qwen2.5-7b-it_bio results/json/robpwd_qwen2.5-7b-it_bio__seed42.json || true
  POSSUM_LAT_AWAY=${away} python train.py --model_org robpwd --model qwen2.5-7b-it \
      --datasets bio --seed 42 || { echo "TRAIN FAIL away=${away}"; continue; }
  POSSUM_LAT_AWAY=${away} python experiments/_run_eval.py \
      --model_path checkpoints/robpwd_qwen2.5-7b-it_bio --model_org robpwd --model qwen2.5-7b-it \
      --datasets bio --out results/json/robpwd_bio_away${away}_s42.json --seed 42 \
      --batch_size 4 --elicitations gcg || { echo "EVAL FAIL away=${away}"; continue; }
  echo "SAVED away=${away}"
done
echo "########## SWEEP DONE $(date) ##########"
