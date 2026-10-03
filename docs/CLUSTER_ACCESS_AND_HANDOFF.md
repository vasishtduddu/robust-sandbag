# Cluster access and handoff (Tables 1-3 campaign, updated 2026-09-23)

Read this before touching any cluster. The running status log is
[`docs/PRIOR_BASELINES_STATUS.md`](PRIOR_BASELINES_STATUS.md) (current state, with a dated log at the bottom); per-cell
provenance is [`tables/PROVENANCE.md`](../tables/PROVENANCE.md) (newest block at the bottom).

## 0. DO THIS FIRST (rewritten 2026-09-24 09:30 EDT at handoff; current state 2026-10-01)

**2026-10-02:** read `PRIOR_BASELINES_STATUS.md` section **0-HANDOFF-3** first (supersedes 0-HANDOFF-2). New: `evaluate.py` `e7484b8`
(merged-organism sft/dpo no-reload), PWD 32B DPO needs H100 (L40S OOMs in DPO training), figures live in `tables/<technique>/` and are
rendered by `experiments/whitebox_curves.py`; the 14B/32B weak-seed FD-A/FDQ-A arms (`PREREG_FORMAT_DIVERSE.md` sec 12).

**2026-10-01:** read `PRIOR_BASELINES_STATUS.md` section **0-HANDOFF-2** first (in-flight jobs and their landing actions). New this
session: the Vulcan patched code tree `possum-vulcan/dcode_steerfix` (= dcode + the no-reload fixes `6521d87`/`5063714`; use it for any
Vulcan whitebox job on a 32B merged organism), and WatGPU H200s run AutoDAN ~3-4x faster than Vulcan l40s (but preempt; keep a Vulcan copy).


1. Read section 0 of [`PRIOR_BASELINES_STATUS.md`](PRIOR_BASELINES_STATUS.md): every live job per cluster
   and the ordered to-do list. Tables 1-3 are complete; nothing is promoted without Mark's review.
2. ALL watchers, the redraw shipper and every Monitor were STOPPED at handoff. Restart them:
   `bash ~/.possum/start_watchers.sh`, Monitor `bash ~/.possum/follow_events.sh`, and the shipper
   (`/scratch/markwang/possum-t3fix/shipper.py`, command in the status doc).
3. Vulcan needs the ControlMaster opened by Mark on the SAME login node (section 2).

## 1. Killarney (primary; this repo lives here)

* **Log in:** any Killarney login node (`klogin01-04`). The repo is
  `/project/6105494/markwang/possum/possum`, branch `mark/baseline-exps`.
* **Submit ONLY through** `~/.possum/sbatch_clean.sh <args>`. A session shell's Lmod environment
  leaks into jobs and killed six in six seconds once.
* **Shapes:** 32B work needs `gpu:h100:2` (attacks) or `h100:3` (training); 7B/14B fit `l40s:2`
  (request 2 CPUs; CPU, not GPU, is often the binding constraint). `interac` caps at 3 h, l40s only.
* **Priority is fairshare-driven**, so queueing less does not make anything start sooner. Estimates
  for h100 work have run 2-3 days out.
* **Reporting roots:** RoBCoTDstl results in `/scratch/markwang/possum-table3-current`, retrained 7B
  MMLU in `/scratch/markwang/possum-mmlu-merge`, prior baselines in
  `/project/6105494/markwang/possum/possum/artifacts`.

## 2. Vulcan (Alliance; l40s x4 nodes)

* **Access needs Mark.** Vulcan refuses key-only login (password + Duo). Mark opens a ControlMaster
  FROM THE SAME LOGIN NODE the session runs on, because the socket is node-local:
  ```
  ssh -o ControlMaster=yes -o ControlPersist=7d -o ControlPath=~/.ssh/cm-%L-%r@%h:%p -fN markwang@vulcan.alliancecan.ca
  ```
  Claude then reuses it: `ssh -o ControlPath=$HOME/.ssh/cm-%L-%r@%h:%p -o BatchMode=yes markwang@vulcan.alliancecan.ca`.
  A master exists on **klogin03** (opened 2026-09-19, 7-day persist) and an older one on klogin01.
  Run `hostname` first; a session on another node has no Vulcan access until Mark opens one there.
* Submit with `bash -lc "sbatch ..."`; omit `--partition`. Work root
  `/project/aip-rudner/markwang/possum-vulcan/t3`, code `/project/aip-rudner/markwang/possum/possum`.
* Env trap: `vulcan_env_setup.sh` exports `POSSUM_WRONG_PW_FRAC`; every obfcot job must `unset` it.
* Quirk seen once: a chained job came out `JobHeldUser` after its dependency cleared, without anyone
  holding it. `scontrol release` fixed it.

## 3. WatGPU (Waterloo; H200 141 GB, RTX PRO 6000 Blackwell 98 GB, RTX 6000 Ada 46 GB)

* **Access:** `ssh -i ~/.ssh/watgpu -o IdentitiesOnly=yes -o BatchMode=yes nvduddu@watgpu.cs.uwaterloo.ca`
  (no Duo). **SHARED account** with other projects, a teammate, and the Tables 4/5 session: work only
  under `~/possum-mark`, name every job `possum_wg*`, and never cancel or edit anything else. Every
  cancel through this account shows UID 11623, so another actor's cancels look like ours (one of our
  jobs was cancelled this way on 2026-09-19 and resubmitted).
* **Environment:** home is noexec; each job sources `~/possum-mark/wg_env.sh`, which stages the 8 GB
  venv tarball to local disk (with a 12 GiB free-space guard and requeue) and adds
  `~/possum-mark/pyextra` to `PYTHONPATH`. After ANY environment change run
  `jobs/wg_atkimport.sbatch`, a 30-second import probe of every elicitation module.
* **Shapes:** 32B needs `--constraint=H200`; 7B/14B MMLU fit any card with a generic `--gres=gpu:1`
  (starts in minutes); **7B WMDP-cyber AutoDAN needs >= 80 GB** (`--constraint="H200|Blackwell"`),
  since the long-prompt corpus OOMed a 46 GB card after 18 h.
* **Preemption:** partition `ALL` is REQUEUE with no grace time, and whole nodes get reclaimed
  (two 7:22 attacks lost together, one 12:34 attack lost). Trainings and white-box (<= ~5 h) are fine
  there; **long AutoDAN attacks need a non-preempting primary on Killarney or Vulcan**, with WatGPU
  only as a duplicate in a different root.
* Roots: `~/possum-mark/t3` (RoBCoTDstl), `~/possum-mark/prior` (Table 1 baselines),
  `~/possum-mark/t3redraw` (redraws, never overwriting the original). Scripts in `~/possum-mark/jobs`;
  copies of everything generated are in Killarney `~/.possum/session-20260917/`.

## 4. Monitoring

Watchers run DETACHED so they outlive any session or Monitor:
```
bash ~/.possum/start_watchers.sh        # idempotent: each watcher takes its own flock
bash ~/.possum/follow_events.sh         # arm THIS under Monitor; resumes from events_all.offset
```
2026-10-02: `start_watchers.sh` also starts `sentinel.sh` (squeue-diff VANISHED detector for never-eligible jobs + watcher liveness/auto-restart), and `follow_events.sh` replays the 3 lines before its offset as `[REPLAY]` on every start. `start_watchers.sh` runs the Killarney v6 watcher (`watch_v5_k01v6`), WatGPU v6 (`watch_watgpu_k01v6`,
`possum_wg*` only), Vulcan v7 (`watch_vulcan_k01v6`, `possum_*` only; needs the ControlMaster) and a
tail of the redraw shipper log, all appending to `~/.possum/events_all.log`. **v6/v7 ONLY** (v5
swallowed completions of preempted-then-requeued jobs). Each ledger dedupes, so a restart re-reports
anything unseen. Windowed `sacct -S` omits jobs that were never eligible (held/dependency-blocked then
cancelled). `sacct` windows use cluster-local time: Killarney EDT, **Vulcan and WatGPU UTC**.

## 5. Validating and rendering

* Every result: `python3 ~/.possum/validate_cell.py <blob.json> <reporting-root>` (lock, capability,
  recipe, errors, coherence, organism pairing).
* Render only with `setsid nohup bash ~/.possum/render_tables.sh > ~/.possum/render.out 2>&1 &`
  (four passes, `possum-mmlu-merge` LAST; about 1 h). It checks the census against
  `~/.possum/census_baseline.json`; a filled-cell regression exits non-zero. Commit
  `tables/*.gen.tex` together with a `tables/PROVENANCE.md` entry, then push (Mark's standing rule).
* Never hand-edit `tables/*.gen.tex`. The one sanctioned exception (restoring a published value from
  git at Mark's request) is recorded in PROVENANCE with column-level assertions.

## 6. Standing rulings from Mark

Maintained in ONE place: section 4 (rulings) and section 3 (paper disclosures) of
[`PRIOR_BASELINES_STATUS.md`](PRIOR_BASELINES_STATUS.md).

## 7. What is left for Tables 1-3

Nothing: Table 1 324/324, Table 2 RoBCoTDstl rows 108/108, Table 3 54/54 (verified 2026-09-23).
Open work is Mark's follow-up experiments and the figure inputs listed in the status doc.

Known generator pitfalls from this campaign (each cost real jobs): clones carried a hardcoded `--seed`,
deleted environment lines when a line-number region was rewritten, copied a cell-specific technique list,
and two jobs once trained into the SAME run slot and log (one draw was lost unscored). Assert what each
job must PRODUCE, including the environment contract, the run slot and the full `--seed` set, before submitting.
