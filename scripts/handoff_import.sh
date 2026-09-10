#!/usr/bin/env bash
# One-click restore of the Table 4/5 campaign on a fresh host. Run AFTER copying the bundle:
#
#   bash handoff_import.sh /tmp/possum_handoff.tar.zst
#
# Idempotent: re-running skips what is already in place. It stops on the first real failure rather
# than continuing, because every step below is a precondition for the numbers being comparable to
# the cells already banked in results/json.

set -euo pipefail

BUNDLE=${1:?usage: handoff_import.sh /path/to/possum_handoff.tar.zst}
ROOT=/lambda/nfs/fs1/possum          # load-bearing: Claude transcripts are keyed by this path slug
STAGE=/tmp/possum
CONDA=${CONDA_ROOT:-/home/ubuntu/miniconda3}

say() { printf '\n=== %s\n' "$*"; }

say "1/7 unpack"
[ -d "$STAGE" ] || tar --zstd -xf "$BUNDLE" -C /tmp
sudo mkdir -p "$(dirname "$ROOT")" && sudo chown "$USER" "$(dirname "$ROOT")"
[ -d "$ROOT/.git" ] || git clone "$STAGE/repo.bundle" "$ROOT"
cd "$ROOT"
mkdir -p results datasets
[ -d results/json ]     || cp -r "$STAGE/results_json"   results/json
[ -d datasets/cache ]   || cp -r "$STAGE/datasets_cache" datasets/cache
echo "  results/json holds $(ls results/json/*.json | wc -l) files"

say "2/7 conda env"
# shellcheck disable=SC1091
source "$CONDA/etc/profile.d/conda.sh"
conda env list | grep -q '^possum ' || conda env create -n possum -f "$STAGE/environment.yml"
conda activate possum

say "3/7 editable install FROM THIS CHECKOUT"
# environment.yml records possum as editable against the SOURCE machine's path. Without this the
# import resolves to a stale copy: on 2026-09-10 that left shell scripts running the working tree
# while `import possum` used different code, and two fixes were inert for hours. _DATA_ROOT is
# package-relative too, so a wrong install silently shadows datasets/ as well.
pip install -e . --no-deps -q
python - <<PY
import os, possum
got = os.path.dirname(possum.__file__)
assert got == "$ROOT", f"import possum -> {got}, expected $ROOT"
print(f"  import possum -> {got}")
PY

say "4/7 secrets"
if [ ! -f ~/possum_env.sh ]; then
  cp "$STAGE/possum_env.sh.template" ~/possum_env.sh
  echo "  WROTE ~/possum_env.sh FROM TEMPLATE. Paste HF_TOKEN and WANDB_API_KEY, then re-run."
  exit 1
fi
# shellcheck disable=SC1090
source ~/possum_env.sh
python -c "import fla, tilelang, flash_attn; print('  kernels ok (3.3x on the Qwen linear-attention path)')"
# transformers version is NOT optional. `conda env create` silently resolved 4.57.6 on 2026-09-10
# despite environment.yml pinning 5.16.1, and 4.57.6 does not know qwen3_5, gemma4 or muse_glimmer,
# so EVERY model in the campaign failed to load. The kernel check above passed regardless, which is
# why this needs its own assertion: check the architectures, not just the imports.
python - <<'PY'
import transformers
from transformers import AutoConfig
print(f"  transformers {transformers.__version__}")
for repo in ("Qwen/Qwen3.8-27B", "Qwen/Qwen3.6-27B",
             "google/gemma-4-31B-it", "meta-models/Muse-Glimmer-30B"):
    try:
        print(f"    {repo} -> {AutoConfig.from_pretrained(repo).model_type}")
    except Exception as e:
        raise SystemExit(
            f"  {repo} does not load under transformers {transformers.__version__}: {e}\n"
            "  Fix with: pip install 'transformers==5.16.1'")
PY

say "5/7 datasets, and the split check that gates everything"
python scripts/build_commonsense_anchor.py          # PIQA+Winogrande; has no other source
python - <<'PY'
from possum.data.wmdp import WMDPDataset
from possum.data.mmlu import MMLUDataset, MMLUAnchorDataset
from possum.data.math import MATHDataset
from possum.data.commonsenseqa import CommonsenseQADataset

D = "/lambda/nfs/fs1/possum/datasets/mcqa"
EXPECT = {"WMDPDataset": (365, 738), "MMLUDataset": (570, 1142)}
for c in (WMDPDataset, MMLUDataset, MMLUAnchorDataset, MATHDataset):
    ds = c.from_directory(D)
    n = {s: len(ds.get_split(s)) for s in ds.SPLIT_NAMES}
    print(f"  {c.__name__}: {n}")
    if c.__name__ in EXPECT:
        val, test = n["mos_eval"] + n["elicit_eval"], n["test"]
        want = EXPECT[c.__name__]
        assert (val, test) == want, (
            f"{c.__name__} splits are {(val, test)}, banked cells used {want}. "
            "New results would NOT be comparable to results/json. Stop and investigate.")
CommonsenseQADataset.from_directory(D)
print("  splits reproduce the banked cells")
PY

say "6/7 Claude Code state"
CLAUDE_PROJ=~/.claude/projects/-lambda-nfs-fs1-possum
mkdir -p "$CLAUDE_PROJ"
cp -n "$STAGE/claude/CLAUDE.md" "$STAGE/claude/settings.json" ~/.claude/ 2>/dev/null || true
[ -d "$CLAUDE_PROJ/memory" ] || cp -r "$STAGE/claude/memory" "$CLAUDE_PROJ/memory"
cp -n "$STAGE/claude/transcripts/"*.jsonl "$CLAUDE_PROJ/" 2>/dev/null || true

say "7/7 status"
python scripts/campaign_status.py
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader

cat <<'NEXT'

Models are NOT downloaded yet (218 GB). Start that now, it gates every cell:

  python - <<'PY' &
  from huggingface_hub import snapshot_download
  from possum.utils.loading import MODEL_NAME_TO_HF_PATH
  for k in ("qwen3.8-27b-it","qwen3.6-27b-it","gemma-4-31b-it","muse-glimmer-30b"):
      snapshot_download(repo_id=MODEL_NAME_TO_HF_PATH[k], max_workers=16)
  PY

Then, on a 2-GPU host, two lanes with a stagger so the CPU-side loads do not overlap:

  CUDA_VISIBLE_DEVICES=0 nohup bash scripts/run_lane.sh robadapt-mcq  > /tmp/lane0.log 2>&1 &
  sleep 300
  CUDA_VISIBLE_DEVICES=1 nohup bash scripts/run_lane.sh math-and-fix > /tmp/lane1.log 2>&1 &

Then `cd /lambda/nfs/fs1/possum && claude --resume`, and re-arm by hand (all session-scoped):
the /goal stop hook, the hourly cron, Monitor watches on /tmp/lane*.log.
NEXT
