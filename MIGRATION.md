# Picking the Table 4/5 campaign up on another server

Corrected 2026-09-10 after a migration that lost 1.1 TB of checkpoints. The previous version of
this file assumed `/lambda/nfs/fs1` was a shared NFS export and told the target to mount rather
than copy. **It was local ext3 on both hosts.** Nothing is shared; copy what you need.

## What actually matters

| What | Size | Action |
|---|---|---|
| `results/json` | 124 KB | **COPY. The only irreplaceable artifact**: every banked cell's numbers. |
| repo incl. `.claude/rules` | small | copy (git bundle; there is no remote configured) |
| `datasets/cache` | 2.6 MB | copy. Saves ~2.5 h per cached cell (base-correct + MATH solutions). |
| `datasets/*.csv` | ~1 GB | regenerate; verified deterministic |
| conda env `possum` | 9 GB | rebuild from `environment.yml` |
| HF model cache | 218 GB | re-download |
| `checkpoints/` | 1.1 TB | do NOT copy. Only the AutoDAN phase needs weights; retrain there (~20 h). |
| secrets | 1 KB | recreate by hand from the template |

## On the source machine

```bash
bash scripts/handoff_export.sh /tmp/possum_handoff.tar.zst
```

## On the target

```bash
# 1. Unpack
sudo mkdir -p /lambda/nfs/fs1 && sudo chown "$USER" /lambda/nfs/fs1
tar --zstd -xf possum_handoff.tar.zst -C /tmp
git clone /tmp/possum/repo.bundle /lambda/nfs/fs1/possum
cd /lambda/nfs/fs1/possum
mkdir -p results datasets
cp -r /tmp/possum/results_json     results/json
cp -r /tmp/possum/datasets_cache   datasets/cache

# 2. Environment + secrets
conda env create -n possum -f /tmp/possum/environment.yml
conda activate possum
# CRITICAL: install the package editable FROM THIS CHECKOUT. environment.yml records possum as an
# editable install pointing at whatever path the source machine used. On 2026-09-10 that left a
# SECOND copy of the code at /home/ubuntu/src/possum: shell scripts ran the working tree while
# `import possum` resolved to the stale copy, so two Python fixes were silently inert for hours.
# `data/mmlu.py:_DATA_ROOT` is package-relative too, so a wrong install also shadows datasets/.
pip install -e . --no-deps
python -c "
import possum, os
assert os.path.dirname(possum.__file__) == '/lambda/nfs/fs1/possum', os.path.dirname(possum.__file__)
print('import possum ->', os.path.dirname(possum.__file__))"
cp /tmp/possum/possum_env.sh.template ~/possum_env.sh
$EDITOR ~/possum_env.sh        # paste the real HF_TOKEN and WANDB_API_KEY
source ~/possum_env.sh

# 3. Kernels the throughput depends on (3.3x on the Qwen linear-attention path)
python -c "import fla, tilelang, flash_attn; print('kernels ok')"

# 4. Models, 218 GB, network-bound. Start FIRST, it gates everything.
python - <<'PY' &
from huggingface_hub import snapshot_download
from possum.utils.loading import MODEL_NAME_TO_HF_PATH
for k in ("qwen3.8-27b-it", "qwen3.6-27b-it", "gemma-4-31b-it", "muse-glimmer-30b"):
    snapshot_download(repo_id=MODEL_NAME_TO_HF_PATH[k], max_workers=16)
PY

# 5. Datasets. MUST reproduce the banked splits or nothing new is comparable to what is banked.
python -c "
from possum.data.wmdp import WMDPDataset
from possum.data.mmlu import MMLUDataset, MMLUAnchorDataset
from possum.data.math import MATHDataset
D='/lambda/nfs/fs1/possum/datasets/mcqa'
for c in (WMDPDataset, MMLUDataset, MMLUAnchorDataset, MATHDataset):
    ds=c.from_directory(D); print(c.__name__, {s: len(ds.get_split(s)) for s in ds.SPLIT_NAMES})"
python scripts/build_commonsense_anchor.py      # PIQA+Winogrande LAT retain; has no other source
python -c "
from possum.data.commonsenseqa import CommonsenseQADataset
CommonsenseQADataset.from_directory('/lambda/nfs/fs1/possum/datasets/mcqa')"

# VERIFY: wmdp mos_eval+elicit_eval=365 test=738 ; mmlu 570 / 1142.
# A mismatch means the banked cells are NOT comparable and must be rerun. Stop and investigate.

# 6. Claude Code state. The transcript is keyed by the project PATH slug, so the repo must live at
#    /lambda/nfs/fs1/possum for --resume to find it.
mkdir -p ~/.claude/projects/-lambda-nfs-fs1-possum
cp /tmp/possum/claude/CLAUDE.md ~/.claude/ 2>/dev/null || true
cp /tmp/possum/claude/settings.json ~/.claude/ 2>/dev/null || true
cp -r /tmp/possum/claude/memory ~/.claude/projects/-lambda-nfs-fs1-possum/memory
cp /tmp/possum/claude/transcripts/*.jsonl ~/.claude/projects/-lambda-nfs-fs1-possum/

# 7. Status, then run
python scripts/campaign_status.py               # expect 6/8 passing, 8/24 evaluated
bash scripts/run_seed_sweep.sh math             # or: mcq
```

## What does NOT survive a session move, and must be re-armed by hand

`claude --resume` restores the conversation, but everything below lives only in the running
session's memory and is gone the moment it ends. Re-create each on the target:

- **the `/goal` stop hook** (the campaign directive). Re-issue it.
- **the hourly progress cron** (`CronCreate`, every hour at :13). Session-scoped, 7-day expiry.
- **`Monitor` watches** on the sweep log. Re-arm against the new log path.
- **background sweeps.** `nohup`-ed processes die with the machine; restart via `run_seed_sweep.sh`.

Durable by contrast, because they are files: `.claude/rules/*` (tracked in the repo), `~/.claude/memory`,
`results/json`, `datasets/cache`, and every per-model setting in `run_cell.sh` / `HYPERPARAMETERS.toml`.

## Running two machines at once

Cells are independent; the only shared state is `results/json` (distinct filenames) and
`datasets/cache` (content-hashed names, written atomically). **Assign disjoint work explicitly**
rather than pointing both at the same sweep: the banked-result skip prevents duplicated *finished*
work, but two machines can still start the same unfinished cell and collide on its checkpoint dir.
Edit `ORGS`/`MODELS` in `scripts/run_seed_sweep.sh` per machine, then merge `results/json` at the end.

## Re-measure before trusting any timing

The per-model batch sizes in `scripts/run_cell.sh` and `HYPERPARAMETERS.toml` were each set after an
OOM on a specific card, and the pre-migration timings came from an H100 **SXM**. An H100 **PCIe**
measured **2.0-2.15x slower** on the identical RobAdapt warm-up (1.31 -> 0.61-0.65 it/s), so quote
measurements from the target, not this file. Watch the first cell's rate and stop anything whose
single phase projects past 90 minutes.
