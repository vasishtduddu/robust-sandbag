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
#   2. muse-glimmer before qwen3.6. Both are runnable, but qwen3.6 is the reasoning model: its eval
#      is five generate passes at a 2048-token budget, ~6 h a cell against ~1.5 h, so putting it
#      first would block three cheaper cells behind one expensive one.
#   3. gemma-4 LAST, and only here as a candidate. It is ~62 GB of weights and
#      utils/loading.load_model takes the add_pad_token branch that loads to CPU before moving to
#      GPU, on a box with 80 GB RAM, no swap and no passwordless sudo (so campaign_env.sh's
#      drop_caches no-ops). Treat a gemma-4 OOM here as expected, not as a recipe failure, and move
#      those two cells to a cluster when one is reachable.
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

CELLS="robadapt/muse-glimmer-30b/wmdp
       robadapt/muse-glimmer-30b/mmlu
       robadapt/muse-glimmer-30b/math
       robadapt/qwen3.6-27b-it/wmdp
       robadapt/qwen3.6-27b-it/mmlu
       robadapt/qwen3.6-27b-it/math
       robadapt/gemma-4-31b-it/wmdp
       robadapt/gemma-4-31b-it/mmlu"

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
