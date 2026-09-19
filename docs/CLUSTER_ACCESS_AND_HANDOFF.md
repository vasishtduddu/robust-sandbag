# Cluster access and handoff (Tables 1-3 campaign, as of 2026-09-19 02:20 EDT)

Read this before touching any cluster. The running status log is
[`docs/PRIOR_BASELINES_STATUS.md`](PRIOR_BASELINES_STATUS.md) (newest block at the top); per-cell
provenance is [`tables/PROVENANCE.md`](../tables/PROVENANCE.md) (newest block at the bottom).

## 0. DO THIS FIRST

1. **Cancel Killarney job `5493985`** (`redraw_obfcot_14b_hss_s43_draw3`) as soon as Killarney's
   scheduler answers: `scancel 5493985`. It is HAZARDOUS. Its line 66 moves whatever sits at
   `obfcot_qwen2.5-14b-it_mmlu-hss__seed43.json` aside and trains a fourth draw, which would displace
   the draw-2 organism Mark chose on 2026-09-19 (see section 4). It has no self-abort. Killarney's
   slurmctld and slurmdbd were down from ~22:40 EDT 2026-09-18 to at least 02:20 EDT 2026-09-19, so
   the cancel could not be issued.
2. Re-arm one watcher per cluster (section 3) and reconcile the gap window by hand with `sacct`.

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

One watcher per cluster, each with its own ledger so every terminal job is reported exactly once:
```
bash ~/.possum/slurm_watch_v5.sh        # Killarney
bash ~/.possum/slurm_watch_vulcan.sh    # Vulcan (needs the ControlMaster)
bash ~/.possum/slurm_watch_watgpu.sh    # WatGPU (possum_wg jobs only)
```
**All three were stopped on 2026-09-19 02:20 EDT at handoff.** If a restart lands on a different
login node, an older session's watchers may still hold the default locks while their notifications
go nowhere. Then seed PRIVATE ledgers and point `WATCH_DIR` at them:
```
for d in watch_v5 watch_vulcan watch_watgpu; do mkdir -p ~/.possum/${d}_NEW && cp ~/.possum/$d/ledger.tsv ~/.possum/${d}_NEW/; done
WATCH_DIR=~/.possum/watch_v5_NEW bash ~/.possum/slurm_watch_v5.sh   # likewise for vulcan/watgpu
```
The `_k03` ledgers from the last session are the most current. `sacct` windows use the cluster's
local time: Killarney and Vulcan are EDT, WatGPU is UTC.

## 5. Validating and rendering

* Every result: `python3 ~/.possum/validate_cell.py <blob.json> <reporting-root>` (lock, capability,
  recipe, errors, coherence, organism pairing).
* Render only with `setsid nohup bash ~/.possum/render_tables.sh > ~/.possum/render.out 2>&1 &`
  (four passes, `possum-mmlu-merge` LAST; about 1 h). It checks the census against
  `~/.possum/census_baseline.json`; a filled-cell regression exits non-zero. Commit
  `tables/*.gen.tex` together with a `tables/PROVENANCE.md` entry, then push (Mark's standing rule).
* Never hand-edit `tables/*.gen.tex`. The one sanctioned exception (restoring a published value from
  git at Mark's request) is recorded in PROVENANCE with column-level assertions.

## 6. Standing rulings from Mark (all still in force)

* Never re-run a cell that already has a good published value; spend compute on MISSING cells, and
  ask before treating a published value as unsourced.
* Keep and disclose borderline misses; a cell with no lock (Delta <= 0) is redrawn, not reported.
* 14B/MMLU-Prof s44 (leaks 9.3 percent, Delta +0.52): kept and disclosed.
* **14B/MMLU-HSS s43: draw 2 IS THE ORGANISM** (Mark, 2026-09-19, applying his pre-registered
  fallback after draw 3 failed). Restored to the canonical name after verifying
  `result_matches_checkpoint` against `run02`. Disclose: test pw/base 0.860 (val 0.899, under the 0.90
  bar), CSQA/base 1.040, nopw 0.0000.
* Never delete artifacts; move aside or copy instead.
* Use all three clusters, pick the best cluster per job, and validate everything.

## 7. What is left for Tables 1-3, and what fills it

Tables stand at **320 / 242 / 37 / 0** (census baseline saved).

| block | status / next action |
|---|---|
| Table 1: CktBr 7B cyber Jlbrk | WatGPU `1551960`/`1551961` on >=80 GB cards; Killarney twins `5493641/42` |
| Table 1: SysPr 32B cyber Jlbrk | s42 89.97, **s43 90.23 staged on WatGPU only** (sync once Killarney twin `5308539` is resolved: same json); s44 pending (WatGPU `1549106`, Killarney `5308540`). Organism does NOT sandbag cyber (Delta -3.1): disclose |
| Table 1: CktBr 14B MMLU-Prof/HSS Jlbrk | Vulcan `965832-37`; needs compose-prior wiring and an Eff/Util republish after |
| 32B/chem Jlbrk | done, 43.3(25.5) |
| 32B/cyber white-box | s43, s44 done; s42 waits for redraw `1550294` (WatGPU) to promote, then run its white-box |
| 32B MMLU Prof/Stem/HSS | attacks + chained white-box on WatGPU (`1550795/97`, `1550799/801`, `1552296/98` and their `wb` jobs) with Killarney hedges `5531525-34`; stem s42 attack 46.0 and white-box done |
| 14B/MMLU-Prof | s42 E&U + white-box done (all 0.0); s44 white-box WatGPU `1552554` + AutoDAN Vulcan `999149`; s43 redraw WatGPU `1552603` (original draw had no lock; its downstream jobs are HELD) |
| **14B/MMLU-HSS** | **s43 draw 2 restored (section 6); it now needs AutoDAN + steering/pruning/noise.** s42/s44 AutoDAN Killarney `5523499/502` |
| 14B cyber / stem Jlbrk | Vulcan `968482-84`, `967748-50` |

Known generator pitfalls from this campaign (each cost real jobs): clones carried a hardcoded
`--seed`, deleted environment lines when a line-number region was rewritten, and copied a
cell-specific technique list. Assert what each job must PRODUCE, including the environment contract
and the full `--seed` set, before submitting.
