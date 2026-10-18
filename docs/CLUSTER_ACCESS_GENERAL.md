# Cluster access handoff: Killarney, Vulcan, WatGPU (project-agnostic)

How to reach, set up on, submit to, and not get burned by the three GPU clusters this group uses.
Nothing here is specific to one project: substitute `<proj>` for your project's directory name.
Facts were measured on the live clusters (last verified 2026-10-17); re-check anything with a number in it
(`sinfo`, `scontrol show config`) before relying on it.

| | Killarney | Vulcan | WatGPU |
|---|---|---|---|
| Operator | Alliance (Vector/AIP) | Alliance (Amii/AIP) | UWaterloo CS |
| Login | `klogin01-04` | `vulcan.alliancecan.ca` (lands on `vulcan2/3`) | `watgpu.cs.uwaterloo.ca` |
| Auth | Your own Alliance login | Password + Duo, every time (no key-only) | SSH key, no Duo |
| User / account | `markwang` / `aip-rudner` | `markwang` / `aip-rudner` | `nvduddu` (SHARED) / `school_group` |
| GPUs | H100 80 GB (8/node), L40S 48 GB (4/node) | L40S 48 GB only (4/node) | H200 141 GB, RTX PRO 6000 Blackwell 98 GB, RTX 6000 Ada 48 GB, L40S |
| Max walltime | 7 d (b5) | 7 d (b5) | 7 d |
| Priority | ~99% fairshare | fairshare | FIFO (all weights 0) |
| Preemption | No | No | Yes: REQUEUE, `GraceTime=0`, whole-node reclaims |
| Fast lane | `gpubase_interac` (3 h, l40s, 1 job/user) | none usable by batch jobs | none |
| Best for | H100 work, many short l40s jobs | long l40s jobs (no preemption) | 32B-class single-GPU work, jobs <= ~5 h |

---

## 1. Killarney

### Access
Log in to any `klogin0N` node with your Alliance credentials. Everything else in this doc (Vulcan, WatGPU)
is reached FROM a Killarney login node.

### Storage
| Path | Quota | Use |
|---|---|---|
| `/project/6105494/markwang/` | **shared** `aip-rudner` group quota (5000 GiB total; keep your share ~1 TB) | code, venvs, anything that must persist |
| `/scratch/markwang/` | personal, 2000 GiB, purge-prone | checkpoints, caches, results |
| `$HOME` | small | ssh keys, dotfiles, small scripts |

- `diskusage_report` shows your usage; `diskusage_report --per_user` shows the group breakdown on /project.
- Check it before any large copy INTO scratch (it has filled mid-rsync).
- `du` on a tree of symlinks overstates what a delete would free; NFS `df` lags a few seconds after `rm`.
- A file can have full apparent size and zero real bytes (hollow): compare `st_blocks*512` to `st_size`, not `ls -l`.

### Environment
Source tree and venv must live under `/project/...`, not `$HOME`. The working module stack:
```bash
module unload arrow python 2>/dev/null || true
module load StdEnv/2023 gcc python/3.12 arrow/19.0.1 cuda/12.2   # python BEFORE arrow, or pyarrow breaks
source /project/6105494/markwang/<proj>/venv/bin/activate
export HF_HOME=/scratch/markwang/<proj>/hf_cache HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 WANDB_MODE=offline
export PYTORCH_ALLOC_CONF=expandable_segments:True
```
Compute nodes have no internet: download models/datasets on a login node first, then run offline.
A worked example is `clusters/killarney_env_setup.sh` in the possum repo.

### Submitting: use a clean shell
`sbatch` exports the submitting shell's environment into the job. A shell whose Lmod state drifted
(e.g. a Claude Code session) made `module load python/3.12` fail and killed six jobs in 6 s each. Submit
through a clean login shell:
```bash
env -i HOME="$HOME" USER="$USER" LOGNAME="$USER" bash -lc 'sbatch "$@"' sbatch job.sbatch
```
(`~/.possum/sbatch_clean.sh` is this wrapper plus one project variable; copy and adapt it.)

### sbatch template
```bash
#!/bin/bash
#SBATCH --job-name=<proj>_<what>
#SBATCH --account=aip-rudner
#SBATCH --nodes=1 --ntasks=1
#SBATCH --gres=gpu:l40s:1                 # or gpu:h100:N; plain gpu:1 = either pool
#SBATCH --cpus-per-task=2                 # see "CPU" below
#SBATCH --mem=64G
#SBATCH --time=2:30:00
#SBATCH --partition=gpubase_l40s_b1,gpubase_l40s_b2,gpubase_l40s_b3   # EVERY band the --time fits
#SBATCH --output=/scratch/markwang/<proj>/logs/%x_%j.log
set -eo pipefail
source /project/6105494/markwang/<proj>/env_setup.sh
python train.py ...
```
- **Shebang is mandatory and must be `#!/bin/bash`.** Without one Slurm uses `/bin/sh` (dash) and
  `set -o pipefail` aborts instantly. `#!/usr/bin/env bash` can hit a CVMFS symlink loop on some nodes.
- **Always set `--partition`.** Omitted, Slurm silently picks ONE band (h100_b2 has 8 nodes).

### Partitions and shapes
Bands are by walltime; node count shrinks as the limit grows:

| band | limit | h100 nodes | l40s nodes |
|---|---|---:|---:|
| b1 | 3 h | 10 | 168 |
| b2 | 12 h | 8 | 126 |
| b3 | 24 h | 6 | 84 |
| b4 | 3 d | 4 | 42 |
| b5 | 7 d | 2 | 17 |
| `gpubase_interac` | 3 h | none | 25 (`kn102-126`) |

- List the smallest band your `--time` fits **plus every band above it**. Partitions are OR'd; listing more never hurts.
  A band whose limit is below `--time` is dead weight.
- **The 3 h line is the most valuable on the cluster**: it opens b1 (largest pool, ~2.7 starts/GPU/2h vs 0.7 for b2)
  and interac. Size from measured runtimes (+~20%) and get under 3 h whenever you can.
- h100 is ~30 nodes against ~440 l40s, and some h100 nodes are down long-term. Anything that fits l40s should go there.
- L40S reports **44.39 GiB** usable. An OOM quoting that number means you landed on an L40S.
- L40S is ~1.6-2.5x slower than H100 for training/eval; scale walltime when retargeting.
- Fine-tuning (SFT/DPO/LoRA training with optimizer state) on a 7B+ model generally needs one >= 80 GB card;
  plain inference/eval of 7B-14B fits L40S. `device_map="auto"` fills GPUs sequentially, so multi-GPU
  inference may need an explicit balanced map. Judge by job type, not model size.

### `gpubase_interac` (the fast lane)
`PriorityTier=5` vs 1 for every other band: a hard ordering ahead of fairshare. A 3 h job there can start in seconds
when you are at the fairshare floor. Limits: `MaxJobsPU=1`, one node, 3 h, l40s only.
- **Only one interac-targeted job eligible at a time.** A second one does NOT fall back to the other bands listed beside
  it; it sits on `QOSMaxJobsPerUserLimit`. So tag one job, or an `afterok` chain (only one link is ever eligible).
- Always write `gpubase_interac,gpubase_l40s_b1,gpubase_l40s_b2`, never interac alone.
- Split multi-part work into per-part jobs before routing; the 3 h wall kills whatever is in flight.
- Node names do not identify the partition (`kn102-126` are also in l40s b1/b2); read `Partition=` from `scontrol show job`.

### Scheduler facts that change decisions
- **Priority is ~99% fairshare** (`PriorityWeightFairShare=5000000`, age <= ~1%). Fairshare is computed from CONSUMED
  GPU-time with a 7-day half-life. Queueing less, holding, or reordering does not improve it; only time does.
  Check with `sshare -U -u $USER` and `sprio -u $USER`. Billing is GPU count x card rate (h100 12200, l40s 10300); mem/CPU do not count.
- **Backfill tests only 20 of your jobs** (`bf_max_job_user=20`). Jobs past #20 in priority order are invisible to it.
  Keep eligible (not held, not dependency-blocked) jobs <= ~20, and keep the ones inside the window short and small.
  `scontrol hold <id>` / `scontrol release <id>` are free and keep the job id.
- **When nothing starts, change the SHAPE, not the queue.** Order: `scontrol show reservation` -> count free GPUs per
  node -> retarget gres/partition -> only then trim walltime.
- **Reservations refuse jobs that would overlap them.** A job whose `--time` exceeds the time left before a maintenance
  reservation shows `(ReqNodeNotAvail, Reserved for maintenance)` and never starts. `--test-only` lies about this.
- **CPU can be the binding resource**: free l40s GPUs often sit on nodes with 0 free CPUs. Request `--cpus-per-task=2`
  for GPU-bound work.
- `--mem` requests are almost never the bottleneck; requests are already within each node's per-GPU share.

### Editing pending jobs (keeps id and priority)
```bash
scontrol update JobId=<id> Partition=gpubase_l40s_b1,gpubase_l40s_b2   # change gres AND partition together
scontrol update JobId=<id> TresPerNode=gres/gpu:l40s:1
scontrol update JobId=<id> TimeLimit=3:00:00            # raise OK while PENDING; RUNNING can only decrease
scontrol update JobId=<id> MinMemoryNode=65536          # MB, not "64G"; an invalid value voids the whole update
scontrol update JobId=<id> CpusPerTask=2 NumCPUs=2 MinCPUsNode=2   # all three
scontrol update JobId=<id> ExcNodeList=kn046,kn102
```
Raising `TimeLimit` past the current band's limit reports `Access/permission denied`; move the partition in the same call.
A running job's limit cannot be raised by a user at all: size before you submit.

---

## 2. Vulcan

### Access (needs a human with Duo)
Vulcan refuses key-only login. The account owner opens a persistent SSH ControlMaster **in a real terminal on the
same Killarney login node** the automation runs on (the socket is node-local; `%L` in the path keeps nodes apart):
```bash
hostname   # note which klogin node this is
ssh -o ControlMaster=yes -o ControlPersist=7d -o ServerAliveInterval=60 \
    -o ControlPath=~/.ssh/cm-%L-%r@%h:%p -fN markwang@vulcan.alliancecan.ca   # password + Duo
```
Then anything (including an agent) reuses it non-interactively:
```bash
VSSH="ssh -o ControlPath=$HOME/.ssh/cm-%L-%r@%h:%p -o BatchMode=yes markwang@vulcan.alliancecan.ca"
$VSSH -O check            # "Master running" means usable
$VSSH 'squeue -u $USER'
```
- A session on a different klogin node has no access until a master is opened there too.
- The `!` prefix in Claude Code has no TTY, so password/Duo prompts fail there; open the master in a normal terminal.
- Never store passcodes. When `-O check` fails, ask the owner to log in again.

### Storage
- Code/venv/work: `/project/aip-rudner/markwang/<proj>`. Personal `$SCRATCH` is effectively full of old artifacts; do not
  delete them and do not put new work there.
- `$SCRATCH` is **unset in a non-login shell**, so anything that reads it must run under `bash -lc`.
- Other people's directories live beside yours under `/project/aip-rudner/markwang/` (e.g. `t45`); stay out.

### Environment
Same module stack as Killarney (`StdEnv/2023 gcc python/3.12 arrow/19.0.1 cuda/12.2`), separate venv.
- **Never `pip install` into the shared venv casually.** One install upgraded numpy and removed sympy/mpmath/pandas,
  breaking every job on the cluster. Install extras into a separate prefix on `PYTHONPATH`, and prefer
  `pip install --no-index <pkg>` (Alliance wheels).
- Probe imports through the env-setup script, not bare `venv/bin/python` (module-provided packages like pyarrow look missing otherwise).
- A passing import probe does not prove a job fits in memory; size from a measured run.

### Submitting
Submit through a login shell, or the job inherits a shell with no `module` function and dies in 1 s (exit 127):
```bash
$VSSH 'bash -lc "cd /project/aip-rudner/markwang/<proj> && sbatch --parsable job.sbatch"'
```
Header: `--account=aip-rudner`, `--gres=gpu:l40s:N`, partitions `gpubase_bygpu_b1..b5` (3 h / 12 h / 1 d / 3 d / 7 d;
~226 / 212 / 144 / 144 / very few nodes). You may also omit `--partition` there and let the submit plugin choose from
the walltime. **Walltime picks the band and the band picks the start:** a 4-day request was estimated 3 days out; the same
job at 1-2 days started the same day.

### What fits
- L40S only (44 GB usable per card). Training that does not shard across GPUs (single-device SFT/DPO) gets 44 GB no
  matter how many cards you request; 14B SFT and 32B fine-tuning OOM there. Send those to Killarney H100 or a WatGPU H200.
- No preemption, so it is the right home for long (multi-day) inference/attack jobs that cannot checkpoint.
- Vulcan's `gpubase_interac` cannot be used by batch jobs (the plugin rewrites the QOS).
- Seen once: a chained job came out `JobHeldUser` after its dependency cleared; `scontrol release` fixed it.
- `sacct` times on Vulcan are UTC (Killarney is Eastern).

---

## 3. WatGPU

### Access
```bash
ssh -i ~/.ssh/watgpu -o IdentitiesOnly=yes -o BatchMode=yes nvduddu@watgpu.cs.uwaterloo.ca
```
Key `~/.ssh/watgpu` on Killarney (fingerprint `SHA256:sC73kLSGxclO8rj+KDCPd/u9WcW2SpH+BoaJMEruRsU`). No Duo; works
directly from any klogin node.

### This is a SHARED account
`nvduddu` is used by other projects, a teammate, and other sessions. Rules:
- Work only under your own directory (e.g. `/u801/nvduddu/<you>-<proj>`); never touch anything else in `/u801/nvduddu`.
- Prefix every job name with a unique tag (e.g. `<proj>_wg_*`) so you can filter `squeue` to your own jobs.
- Never cancel, hold, or edit a job you did not submit. Every cancel on this account shows the same UID, so a foreign
  cancel looks like yours; if your job vanishes, resubmit, do not retaliate.
- Home `/u801/nvduddu` is 1 TB total (~340 GB free on 2026-10-17) and has filled before. When it fills, jobs that wrote logs
  during the full window can die HOURS later on their next write (`Errno 122`), even after space is freed: replace them early.
- Before deleting anything, check nothing (including a symlink elsewhere) points into it:
  `find /u801/nvduddu/<yourdir> -maxdepth 4 -type l -lname "*<dirname>*"`.

### Environment: home is noexec
You cannot run a venv's python from `/u801`. The working pattern (see `/u801/nvduddu/possum-mark/wg_env.sh`):
1. Keep a tarball of the venv (with a `uv`-managed Python) on `/u801`.
2. In every job, untar it to node-local disk. **WatGPU does not set `SLURM_TMPDIR`** and `/tmp` is shared and sometimes
   full, so walk candidates (`/tmp`, `/scratch/$USER`, `/local/$USER`, `/var/tmp`), take the first with enough free space,
   and `scontrol requeue $SLURM_JOB_ID` if none has room.
3. Re-point the venv at the staged interpreter (`pyvenv.cfg` `home =`), put pure-python extras in a `pyextra/` dir on
   `PYTHONPATH` (avoids re-tarring), `trap 'rm -rf $STAGE' EXIT`.
4. Guard against bad nodes: `python -c "import torch,sys; sys.exit(0 if torch.cuda.is_available() else 1)" || scontrol requeue $SLURM_JOB_ID`.
5. After ANY environment change, run a 30 s import probe job that imports every module your real jobs import.

Model weights and HF cache can stay on `/u801` (read as data).

### sbatch template
```bash
#!/bin/bash
#SBATCH --job-name=<proj>_wg_<what>
#SBATCH --account=school_group
#SBATCH -p ALL
#SBATCH --gres=gpu:1
#SBATCH --constraint=H200                 # only when you need > 48 GB; see below
#SBATCH --exclude=watgpu508,watgpu1208,watgpu1209
#SBATCH --requeue
#SBATCH --open-mode=append                # log survives requeues; mark each attempt with a banner
#SBATCH --cpus-per-task=6 --mem=128G
#SBATCH --time=6:00:00
#SBATCH --output=/u801/nvduddu/<yourdir>/logs/%x.log
set -eo pipefail
echo "ATTEMPT START $(date -u) $(hostname)"
source /u801/nvduddu/<yourdir>/wg_env.sh
...
```

### GPUs and the queue
| constraint | card | usable memory |
|---|---|---|
| `H200` | H200 | 141 GB |
| `Blackwell` | RTX PRO 6000 Blackwell | ~98 GB |
| `ADA6000` | RTX 6000 Ada | 44-48 GB |
| `L40S` | L40S | 44 GB |

- Priority is **pure FIFO** (all priority weights 0; `scontrol top` is denied). Submit in priority order, or hold
  lower-priority work and release it when nothing more important is pending.
- **The constraint is the queue lever.** A bare `--gres=gpu:1` starts in minutes (many Ada cards); `--constraint=H200`
  can wait a day or two. Constrain only when the job needs it: one H200 holds a 32B bf16 model (replaces 2x H100);
  fine-tuning methods that keep a reference model (DPO) roughly double the footprint, so even 7B DPO needs
  `--constraint="H200|Blackwell"`.
- Known-bad nodes (2026-10): `watgpu508` generates garbage (100% unparseable output even from base models; a test there can read 0
  and look like success), `watgpu1209` (and `1208`) break 2-GPU jobs. Keep the `--exclude` on every script and re-check periodically.

### Preemption
Partition `ALL` is `PreemptMode=REQUEUE` with `GraceTime=0`, and whole nodes are reclaimed at once (a 12.5 h job died with
everything else on its node). Jobs restart from zero unless they checkpoint.
- Good fit: jobs <= ~5 h (training runs of 2-3 h almost always finish).
- Bad fit: 10 h+ jobs with no internal checkpoint. Run those on Killarney/Vulcan; at most put a duplicate on WatGPU writing
  to a different output root and cancel the loser.
- Every job must be **restart-safe**: exit 0 if the result already exists; move a partial checkpoint dir aside inside the job;
  count attempt banners since the log is appended across requeues.
- `sacct` times are UTC.

---

## 4. Moving data between clusters

All transfers run from a Killarney login node.
```bash
# Killarney -> WatGPU
rsync -a --info=progress2 -e "ssh -i ~/.ssh/watgpu -o IdentitiesOnly=yes" \
  /scratch/markwang/<proj>/ckpt/ nvduddu@watgpu.cs.uwaterloo.ca:/u801/nvduddu/<yourdir>/ckpt/
# Killarney -> Vulcan (reuses the ControlMaster)
rsync -a --info=progress2 -e "ssh -o ControlPath=$HOME/.ssh/cm-%L-%r@%h:%p" \
  /scratch/markwang/<proj>/ckpt/ markwang@vulcan.alliancecan.ca:/project/aip-rudner/markwang/<proj>/ckpt/
```
- Sync code at a pinned commit and sha256-verify datasets/checkpoints after copying; an old divergent copy of a dataset once
  lived on one of these clusters.
- Copy whole cache directories (sidecar `.meta.json` files matter).
- Sync results back to ONE canonical root before analysis. Running the same job on two clusters is safe only if the output
  roots differ; two writers on one file silently corrupts it.
- For large checkpoints, transfer time can exceed compute time; run where the weights already are.

---

## 5. Monitoring and verification habits (all clusters)

- `scontrol show job <id>` is the only trustworthy record of a job's real partition, gres, limit and excluded nodes; the
  on-disk sbatch goes stale after `scontrol update`. Guard `[ -n "$J" ]` first: an empty id dumps every job on the cluster.
- `sbatch` that times out ("Socket timed out") may still have submitted. Check `squeue` by job name before resubmitting.
- Any `squeue | grep -v JobHeld` filter needs `%R` in the format, or held jobs look active. `%.Nj` truncates long job names
  mid-word; use bare `%j` or match by id.
- A job whose `afterok` dependency fails is silently removed from the queue. Use `afterany` when chaining a recovery behind a
  job that will TIMEOUT.
- A sub-minute failure with an empty log is usually a node fault: check `sacct -j <id> -o NodeList`, `--exclude` the node, resubmit.
  `Too many levels of symbolic links` in the first seconds = a node's CVMFS is broken.
- Logs full of tqdm carriage returns: read with `tr '\r' '\n' < log | tail` or `grep -a`, never raw `tail`.
- A RUNNING job can already be doomed: compare the progress-bar ETA (`elapsed / done x total`, after >= ~15 items, x1.3) against
  its TimeLimit and cancel early if it cannot finish.
- A silent log is not a stalled job: `sstat -j <id>.batch -o AveCPU` tracking wall time means it is computing.
- Free GPUs = node Gres total minus `AllocTRES`, skipping down/drain/fail. The `sinfo` state column (`mix`, `mix-` = PLANNED by
  backfill) does not tell you.
- Audit actual usage to right-size future requests:
  `sacct -u $USER -S <date> -P -o JobID,JobName,Partition,State,Elapsed,Timelimit,ReqMem,MaxRSS,AllocTRES`.
  `MaxRSS` is host RAM, not GPU memory; a MaxRSS equal to the request is capped, not measured.

## 6. Choosing a cluster

1. Needs > 48 GB on one device (32B inference, 7B+ DPO, 14B+ SFT)? Killarney H100 or WatGPU H200/Blackwell.
2. <= 3 h on 1-2 l40s? Killarney with interac (one at a time) plus l40s b1/b2.
3. Long (10 h+), cannot checkpoint? Killarney or Vulcan (no preemption); never WatGPU alone.
4. <= ~5 h, restart-safe, Killarney fairshare spent? WatGPU (generic `gpu:1` when it fits 48 GB).
5. l40s-shaped and Killarney saturated? Vulcan.

Check what is actually free before choosing; the cluster that starts soonest usually wins.
