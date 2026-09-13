#!/usr/bin/env bash
# Run ONE Table 3 (qwen2.5) cell-seed on this box, mirroring the clusters' train_ds.sh exactly.
#
#   bash scripts/run_cell_t3_local.sh <org> <model> <dataset> <seed> <RUNID>
#   FAM knobs are passed in the environment, e.g.
#   POSSUM_POST_LAT_RECOVERY=8000 ... bash scripts/run_cell_t3_local.sh robpwd qwen2.5-32b-it chem 44 fchemROs44
#
# WHY THIS EXISTS rather than `experiments.run execute --executor local`: the 2026-09-07 axis swap
# replaced naming.TASKS with the Table 4/5 axes, so run.py's --task only accepts wmdp/mmlu/math and
# cannot address a Table 3 dataset like chem at all.
#
# IT MUST NOT SOURCE campaign_env.sh. That file is the Table 4/5 environment: it sets
# POSSUM_POST_LAT_RECOVERY=0, which silently contradicts this cell's REC_OVERRIDE=8000, and it
# prepends the transformers 5.16.1 shadow layer, which would swap the library under a qwen2.5 cell
# that was banked at 4.57.3. possum_env.sh alone is the Table 3 environment.

set -uo pipefail

ORG=${1:?usage: run_cell_t3_local.sh <org> <model> <dataset> <seed> <runid>}
MODEL=${2:?usage: run_cell_t3_local.sh <org> <model> <dataset> <seed> <runid>}
DS=${3:?usage: run_cell_t3_local.sh <org> <model> <dataset> <seed> <runid>}
SEED=${4:?usage: run_cell_t3_local.sh <org> <model> <dataset> <seed> <runid>}
RUNID=${5:?usage: run_cell_t3_local.sh <org> <model> <dataset> <seed> <runid>}

set +u
source /u1/l79he/possum_env.sh
set -u

ROOT=/u1/l79he/possum
# Blobs land in this box's OWN host directory, so harvest reads them exactly like a cluster's and
# publish_pooled cannot clobber them the way it would a hand-placed file in results/json.
OUTROOT=/u1/l79he/possum-scratch/results-backup/chippie
OUTDIR=$ROOT/checkpoints/scale_recipe
mkdir -p "$OUTROOT/scale_recipe" "$OUTDIR"

CKPT_OUT="$OUTDIR/${ORG}_${MODEL}_${DS}/run${RUNID}"   # train.py prefixes the dir with run
TAG="${ORG}_${MODEL}_${DS}_run${RUNID}"
RES_JSON="$OUTROOT/scale_recipe/gp_${TAG}.json"

echo "########## T3 CELL $ORG/$MODEL/$DS seed $SEED runid $RUNID  $(date -Is)"
echo "  knobs: $(env | grep -E '^POSSUM_(POST_LAT|COMMONSENSE|HELLASWAG|SFT|LAT)' | sort | tr '\n' ' ')"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader

if [ -f "$CKPT_OUT/config.json" ] && [ -n "$(find "$CKPT_OUT" -name '*.safetensors' -print -quit 2>/dev/null)" ]; then
  echo "  train: reusing $CKPT_OUT"
else
  python -u "$ROOT/train.py" --model_org "$ORG" --model "$MODEL" --datasets "$DS" \
    --seed "$SEED" --run_id "$RUNID" --output_dir "$OUTDIR"
  TRAIN_RC=$?
  echo "  train exit=$TRAIN_RC"
  [ "$TRAIN_RC" -eq 0 ] || exit "$TRAIN_RC"
fi

# Same skip rule as train_ds.sh: only skip when the result is NEWER than the weights, so a
# deliberate re-run is never described by the previous run's numbers.
if [ -f "$RES_JSON" ] && [ -f "$CKPT_OUT/config.json" ] && [ "$RES_JSON" -nt "$CKPT_OUT/config.json" ]; then
  echo "=== EVAL SKIP: $RES_JSON is newer than the checkpoint"
elif [ -d "$CKPT_OUT" ]; then
  echo "=== EVAL START $TAG $(date -Is)"
  python -u "$ROOT/evaluate.py" --model_path "$CKPT_OUT" --model "$MODEL" --model_org "$ORG" \
    --datasets "$DS" --results_json "$RES_JSON" && echo "=== EVAL DONE $TAG" \
    || echo "=== EVAL FAILED $TAG (checkpoint kept, rerun eval separately)"
else
  echo "=== EVAL SKIP: no checkpoint at $CKPT_OUT"
fi
echo "########## T3 CELL done $(date -Is)"
