#!/usr/bin/env bash
# Single-lane Table 4/5 runner for the chippie02 A100 80GB. One GPU, so run_lane.sh's two-lane
# split does not apply; this is the one-card equivalent.
#
#   nohup bash scripts/run_lane_local.sh > /u1/l79he/possum-scratch/local/lane.log 2>&1 &
#
# ORDER is deliberate, and it is NOT the cheapest-first rule run_lane.sh uses:
#
#   1. ONE SEED PER CELL FIRST (user directive). Every cell below is a cell with no banked result,
#      so breadth comes before any second seed anywhere.
#   2. qwen3.6 BEFORE muse/math (reordered 2026-09-14). This used to put muse-glimmer first on
#      cost, since qwen3.6 is the reasoning model (five generate passes at a 2048-token budget,
#      ~6 h of eval against ~1.5 h). Cost stopped being the right key once muse's numbers landed:
#      every muse cell run so far FAILED UTILITY (robadapt/wmdp hs 0.761, robadapt/mmlu hs 0.511
#      csqa 0.675, robpwd/mmlu hs 0.865), while qwen3.6 has no utility failure on record
#      (robpwd/qwen3.6/wmdp hs 0.935, csqa 1.004). A cheap cell that fails adds nothing to the pass
#      count, so rank by expected yield: the qwen3.6 cells first and muse/math LAST, where it runs
#      only after every healthier cell and after the HS-anchor recipe question can be settled.
#   3. gemma-4 is NOT in this lane at all any more: it moved to watgpu on 2026-09-13, whose H200
#      has 143 GB against this box's 80 GB. The host-RAM worry that first motivated deferring it
#      turned out to be unfounded (a 27B load peaks at 9.1 GB RSS, since safetensors stay mmapped),
#      but its GPU footprint is real: ~62 GB of weights on sdpa, with no flash attention.
#
# EXCLUDED ON PURPOSE, do not "fix" by adding them:
#   robpwd/<any>/math  - all four share a KNOWN design failure, not a missing run. The retired
#                        results read pw/base 0.341 (qwen3.8) and 0.467 (muse): effectiveness passes
#                        and the UNLOCK branch collapses, because a MATH sandbag has no short
#                        sentinel to recover from. run_lane.sh records that these wait on a
#                        long-form LAT retain. Re-running them unchanged repeats the failure.
#
# The two cells that HAVE failing numbers (robpwd/qwen3.6/mmlu at pw/base 0.89, robpwd/muse/mmlu at
# hs 0.86) are also excluded: they need POSSUM_FORCE_CELL=1 plus their diagnosed knob change
# (a larger MOS_TRAIN_MAX and HELLASWAG_MULT=2 respectively), which is a separate deliberate pass.

set -uo pipefail

ROOT=${POSSUM_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
SEED=${SEED:-42}

# Source the Table 4/5 env HERE, not only inside run_cell.sh. The model-presence check below runs
# in this shell before any cell starts, and without the env it has no HF_HOME, the wrong python and
# no repo root on sys.path, so `from utils.loading import` fails and EVERY cell reports "not
# downloaded yet" forever. The lane would sit there looking patient while doing nothing.
set +u
source "$ROOT/scripts/campaign_env.sh"
set -u
cd "$ROOT"

# CELL OWNERSHIP IS A STATIC DISJOINT SPLIT between this box and watgpu, for the same reason
# run_lane.sh splits its two lanes: run_cell.sh's banked-result skip stops duplicated FINISHED work,
# but two lanes can still START the same unfinished cell and collide on its checkpoint directory.
#
# watgpu owns BOTH gemma-4 cells and must not be given any listed here. gemma-4 is the heaviest
# model at ~62GB of weights AND it cannot use flash attention (global_head_dim 512 exceeds FA2's
# cap) so it runs sdpa, whose attention memory is far larger; run_cell.sh records it OOMing at SFT
# batch 2 with 76.1GiB in use. watgpu's H200 has 143GB against this box's 80GB, so that is where it
# belongs. See the watgpu-table45-port memory entry for the launcher there.
CELLS="robadapt/muse-glimmer-30b/wmdp
       robadapt/muse-glimmer-30b/mmlu
       robadapt/qwen3.6-27b-it/wmdp
       robadapt/qwen3.6-27b-it/mmlu
       robadapt/qwen3.6-27b-it/math
       robadapt/muse-glimmer-30b/math"

echo "########## LOCAL LANE start $(date -Is), seed $SEED"
for cell in $CELLS; do
  IFS=/ read -r org model ds <<< "$cell"
  # Skip a cell whose model is not downloaded yet rather than failing it: the fetch runs in
  # parallel with this lane, so an absent model means "not yet", not "broken".
  # Resolve via the repo's own MODEL_NAME_TO_HF_PATH: the cache dir is models--<org>--<repo>
  # (models--meta-models--Muse-Glimmer-30B), which no transformation of the possum name produces.
  #
  # CONFIG.JSON IS NOT "DOWNLOADED". snapshot_download writes the small files first, so config.json
  # appears minutes before the weights do. Gating on it started a Muse cell whose shards were still
  # in flight and train.py died in 40s with
  #   FileNotFoundError: .../model-00001-of-00002.safetensors
  # Same shape as the checkpoint guard in run_cell.sh ("config.json alone is not trained"). Require
  # the INDEX's whole weight_map to be present, and no .incomplete blobs anywhere in the repo.
  if ! python - "$model" <<'PYEOF'
import glob, json, os, sys
from utils.loading import MODEL_NAME_TO_HF_PATH

repo = MODEL_NAME_TO_HF_PATH[sys.argv[1]]
base = os.path.join(os.environ.get("HF_HOME", ""), "models--" + repo.replace("/", "--"))
if glob.glob(os.path.join(base, "blobs", "*.incomplete")):
    sys.exit(1)
snaps = glob.glob(os.path.join(base, "snapshots", "*"))
for snap in snaps:
    idx = os.path.join(snap, "model.safetensors.index.json")
    if os.path.isfile(idx):
        want = set(json.load(open(idx))["weight_map"].values())
        if all(os.path.exists(os.path.join(snap, w)) for w in want):
            sys.exit(0)
    # Single-shard repos have no index; accept a lone model.safetensors instead.
    elif os.path.exists(os.path.join(snap, "model.safetensors")):
        sys.exit(0)
sys.exit(1)
PYEOF
  then
    echo "###### SKIP $cell (model not in $HF_HOME yet)"
    continue
  fi
  echo "###### CELL $cell  $(date -Is)"
  bash "$ROOT/scripts/run_cell.sh" "$org" "$model" "$ds" "$SEED"
  echo "###### CELL $cell rc=$? $(date -Is)"
done
echo "########## LOCAL LANE done $(date -Is)"
