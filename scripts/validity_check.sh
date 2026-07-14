#!/bin/bash
# VALIDITY (not just functional) check: re-run attacks on the existing pwd/bio 7B sandbag at a
# MEANINGFUL sample with real hyperparameters, and compare to the KNOWN pwd baselines. A valid
# result reproduces those (prefilling ~0.45, gcg ~0.66-0.70 recovering nopw 0.051); a 1.0 is a
# small-sample artifact. prefilling is the fast decisive anchor (cheap + known baseline).
set -o pipefail
cd /u1/l79he/possum
PY=/u1/l79he/.conda/envs/latest/bin/python
TARGET=checkpoints/pwd_qwen2.5-7b-it_bio
export POSSUM_GENPARSE=1 POSSUM_ELICIT_EVAL_MAX=64 POSSUM_UTIL_EVAL_MAX=100

run() {  # $1=attack  $2=extra env knobs
  echo "########## VALIDITY $1 (n<=64) $(date +%H:%M:%S) ##########"
  env $2 CUDA_VISIBLE_DEVICES=0 "$PY" -u evaluate.py \
    --model_path "$TARGET" --model_org pwd --model qwen2.5-7b-it --datasets bio \
    --elicitations "$1" --batch_size 8 --seed 42 --results_json "results/validity_$1.json" \
    > "results/logs/validity_$1.log" 2>&1 && echo "$1 OK" || echo "$1 FAILED"
}

run prefilling ""
run many_shot "POSSUM_MANY_SHOT_N=5"
run gcg "POSSUM_GCG_NUM_STEPS=60 POSSUM_GCG_SEARCH_WIDTH=64 POSSUM_GCG_TOPK=64"
echo "########## VALIDITY DONE $(date +%H:%M:%S) ##########"
