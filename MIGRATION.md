# Migrating the Table 4/5 campaign to another machine

Everything that is expensive to reproduce already lives on the shared NFS export
(`10.48.110.179:/3b3ee393-8a1e-4266-a31c-b196df081bbf`, mounted at `/lambda/nfs/fs1`): the repo, the
1.1 TB of checkpoints, `results/json`, `datasets/` and `tables/`. **If the target machine mounts the
same export, none of that is copied.** Only the local disk holds machine-bound state.

| What | Where | Size | Action |
|---|---|---|---|
| repo, checkpoints, results, datasets, tables | `/lambda/nfs/fs1/possum` (NFS) | 1.1 TB | mount, do not copy |
| migration bundle (env spec, Claude state, patch) | `/lambda/nfs/fs1/possum_migration` (NFS) | 9 MB | mount, do not copy |
| HuggingFace model cache | `/home/ubuntu/hf_cache` (local) | 218 GB | NOT migrated: re-download on the target |
| conda env `possum` | `/home/ubuntu/miniconda3/envs/possum` (local) | 9.1 GB | rebuild from spec |
| Claude settings + sessions + memory | `/home/ubuntu/.claude` (local) | 34 MB | in the bundle |
| secrets | `/home/ubuntu/possum_env.sh` (local) | 1 KB | recreate by hand, template in the bundle |

Target must be a single NVIDIA H100 80GB. The per-model settings in `scripts/run_cell.sh` and
`HYPERPARAMETERS.toml` are memory-tuned against exactly 79.18 GiB usable and were each set after an
OOM; on a different card, re-measure rather than assume.

## 1. On the target

```bash
sudo mkdir -p /lambda/nfs/fs1
sudo mount -t nfs 10.48.110.179:/3b3ee393-8a1e-4266-a31c-b196df081bbf /lambda/nfs/fs1
bash /lambda/nfs/fs1/possum_migration/import.sh          # env + Claude state + checks
```

Then recreate `/home/ubuntu/possum_env.sh` from `possum_migration/env/possum_env.sh.template`,
pasting the real `HF_TOKEN` and `WANDB_API_KEY`. They are deliberately not in the bundle.

## 2. Model cache: re-download, do not migrate

The 218 GB HF cache is byte-for-byte reproducible from HuggingFace, so it is deliberately NOT in the
bundle. Pre-warm it on the target before the first cell, otherwise the first `load_model` pays for
it mid-run:

```bash
source ~/possum_env.sh    # needs HF_TOKEN; HF_XET_HIGH_PERFORMANCE=1 is already set there
python -c '
from huggingface_hub import snapshot_download
for repo in ("Qwen/Qwen3.8-27B", "Qwen/Qwen3.6-27B",
             "google/gemma-4-31B-it", "meta-models/Muse-Glimmer-30B"):
    print(repo, flush=True); snapshot_download(repo)
'
```

Keep `HF_HOME` on LOCAL disk. Loading a 55-62 GB model over NFS is materially slower, and
`scripts/campaign_env.sh` already drops the page cache before every cell because a 62.5 GB
CPU-side load trips the host's low-memory guard when 172 GB of clean page cache is resident.

`datasets/` (140 MB) is re-downloadable too: every loader has `from_directory()` auto-download and
`create_split(seed=42)` is deterministic, so the five splits regenerate identically. The ONE
exception is `datasets/cache/basecorrect_*.json`, which is GPU-derived (~2.5 h each on the reasoning
model) and must be preserved. It lives on NFS with the repo.

## 3. Resume the campaign

The sweep is resumable by construction: `scripts/run_cell.sh` skips training when a checkpoint has
both `config.json` and a shard (recursively, for robadapt's adapter subdirectory), and
`experiments/_run_eval.py` is idempotent, so completed cells re-score in under a second.

```bash
source scripts/campaign_env.sh
nohup bash scripts/run_seed_sweep.sh mcq  > /tmp/sweep_mcq.log  2>&1 &   # Table 4
nohup bash scripts/run_seed_sweep.sh math > /tmp/sweep_math.log 2>&1 &   # Table 5 (run AFTER mcq)
python scripts/campaign_status.py                                        # verdicts, any time
```

`python -m experiments.tablefill --table {families,math}` regenerates the LaTeX; `run_cell.sh` also
does it on every exit, including failures, via an EXIT trap.

## 4. What does NOT survive the move

- **The hourly report cron job and the log monitors are session-only.** Recreate them in the new
  Claude session; the report prompt is in `possum_migration/state/hourly_report_prompt.txt`.
- **The in-flight cell.** Whatever is mid-training when the source machine goes away restarts from
  scratch, because `train.py` writes its checkpoint only at the end. Prefer to cut over between
  cells; `scripts/campaign_status.py` plus `ps` shows what is running.
- Uncommitted edits are captured in `possum_migration/state/uncommitted.patch`, but the NFS working
  tree carries them anyway. `git apply` it only if you start from a fresh clone.
