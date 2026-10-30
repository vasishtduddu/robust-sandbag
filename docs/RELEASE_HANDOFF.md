# Release handoff: final codebase cleanup and publication

Read this first if you are the session that finishes the open-source release. Written 2026-10-30 when the Finke et al.
comparison and the notebook validation were closed. Nothing is running on any cluster for this project.

## 1. Where things are

| What | Where | State |
|---|---|---|
| Release candidate code | branch `cleanup` (worktree `/project/6105494/markwang/possum/possum-cleanup`), `origin/cleanup` = `a2f2c78` | complete, audited, pushed |
| Paper tables/figures, provenance, this doc | branch `mark/baseline-exps` (this worktree) | pushed; paper-only, never merged into `cleanup` |
| GitHub | `origin` = `github.com/vasishtduddu/worst-case-sandbag` (GitHub redirects to `vasishtduddu/robust-sandbag`) | `origin/main` = `40ca94d`, 2,423 commits behind `cleanup` |
| Full backup before any history rewrite | `/project/6105494/markwang/possum-backup-20261020/` | keep |
| Other remote branches | `add-project-page`, `mark/figure-restyle`, `mark/tables-4-5`, `mark/tables45`, `mark/baseline-exps` | keep until Mark says otherwise |

## 2. What `cleanup` already contains (verified)

- Package `wcmo` (organisms/, elicitation/, data/, utils/, experiments/, scripts/, ablations/, notebooks/,
  external_organisms/finke2026/), Apache-2.0 `LICENSE`, per-file headers ("Authors: Vasisht Duddu, Mark Wang, Lipeng He,
  Tim G. J. Rudner" / "Copyright 2026 Vector Institute, University of Toronto, University of Waterloo"),
  `THIRD_PARTY_NOTICES.md`, `EXTENDING.md`, Locket-style `README.md`, `index.html` + `.github/workflows/static.yml`
  (project page; `ARXIV_URL` / `LESSWRONG_URL` are still placeholders).
- Scans clean: no names outside headers/README/index.html, no dates, no session jargon (DL-c, F-numbers, ...); em dashes
  only inside prompt strings (data, kept).
- Function-level AST audits of every branch tip against `cleanup` (10-12, 10-13, 10-19, 10-20, 10-21, 10-26): no lost logic
  on any published path. Fresh-clone datasets byte-identical to the released splits.
- GPU smoke from a fresh clone (smoke7 on `db42b49`, `/project/6105494/markwang/wcmo-validate/smoke7`): A+A2, B, C all
  pass. The only failure is PPO, which needs `trl<0.12` (documented, guarded with a clear error).
- Notebooks, all executed with outputs and committed (execution timestamps stripped):
  - `01_robcot.ipynb` (b59ca23): RobCoT w/ 1.2, w/o 62.5 (paper 0.0 / 60.5); steering/fuzzing/pruning best <= 1.2;
    AutoDAN PWD 96.0 (paper 96.9), RobCoT 0.0 (paper 0.7). AutoDAN section is opt-in (`WCMO_NOTEBOOK_AUTODAN=1`).
  - `02_robcot_finetuning.ipynb` (a2f2c78): SFT and DPO 0.0 at every step. Its outputs come from a different RobCoT
    training run (w/ 0.0, w/o 59.0) than notebook 01's; the notebook says results can vary between runs and points to
    the optional `scripts/flatten_answers.py` step (Mark's wording: plain words, never say a model "breaks").
  - `03_robpwd_robadapt.ipynb` (41e97e8): reproduces Table 8 on WMDP-Bio.

## 3. Remaining steps (in order)

1. **Mark's go-ahead for the history rewrite.** Plan (Mark, 10-21): put the exact contents of `cleanup` on `main` as ONE
   commit "v1.0.0", then delete the other branches and all history. Do NOT do this unprompted. Show Mark the exact
   commands first, e.g.:
   ```bash
   git checkout --orphan release cleanup && git commit -m "v1.0.0"
   git push origin release:main --force      # only after Mark confirms; backup exists (section 1)
   ```
   Branch deletion is a separate, explicit confirmation.
2. **Fill placeholders**: `index.html` `ARXIV_URL` and `LESSWRONG_URL`; README citation/arXiv id (Mark deferred the
   arXiv id); repository name/URL in README if the repo stays at `robust-sandbag`.
3. **Final read-through of README against the camera-ready paper** (table/figure numbers were last synced to
   `sandbagging (31).pdf`; the latest upload is `sandbagging (32).pdf`). Check the notebook table, the runtime notes
   (AutoDAN needs 2x48 GB or 1x80 GB, about 30 h per resistant organism on 50 questions) and the variant table.
4. **One last fresh-clone CPU check** (every CLI `--help`, module imports, `experiments.run plan`), and optionally a GPU
   smoke with the scripts in `/project/6105494/markwang/wcmo-validate/smoke7/`, if any code changed after `a2f2c78`.
5. **Decide** (Mark): ship the AutoDAN strategy library (29 MB, sha256 `79c02db4...`)? Ruling so far (10-13): do NOT ship.

## 4. Paper/code mismatches to tell the paper team (code kept as is)

- RobCoT LAT outer LR: paper 8e-5; code applies 8e-5 divided by the rsLoRA scaling (about 3.5e-6 effective).
- Families RobPWD/RobAdapt use 16 PGD iterations (TOML); paper says 32 for all (Mark: PGD 32 is correct; paper side fix).
- Paper Table 5 says 75 LAT steps on WMDP-Chem for Qwen2.5 RobPWD/RobAdapt; code uses 150.
- RobPWD/RobAdapt Qwen2.5 paper cells used the teammate's private launcher knobs (post-LAT repair 4000 rows, lr 1e-4,
  rehearsal, RobPWD HellaSwag x3) that the release defaults omit (RobPWD HS about 80 vs 87.5 in the paper). Released as is.
- Steering contrast sources: code uses true/false + locked; the paper describes correct-vs-locked only.
- Families RobCoT MATH: most seeds use the self-distilled corpus (`scripts/build_self_math_corpus.py`).
- Prior-baseline recipes use forced-choice scoring; the paper says generate-and-parse throughout.

## 5. Finke et al. comparison (closed)

- Generated outputs: `tables/finke/` (`finke_blackbox.gen.tex` = WMDP rows only, `finke_wmdp.gen.tex`,
  `finke_released.gen.tex`, `finke_whitebox_{wmdp,released}.{pdf,png}`, `finke_comparison.md`); every caveat is in
  `tables/PROVENANCE.md`. Regenerate: `python3 -m experiments.finke_comparison_tables` and
  `venv/bin/python -m experiments.finke_whitebox_figures`.
- Final numbers: released Stage-C AutoDAN = 8.3 (2 of 24 hard items; the remaining items were cancelled, Mark 10-30);
  port AutoDAN 14.0 (50/50). GCG skipped (Mark 10-30): partial runs scored 0 on every question of ours (RobCoT 0/46,
  RobPWD 0/36, RobAdapt 0/8); suffix caches in `/project/6105494/markwang/finke_compare/gcg_partial/final/`.
- Paper text: released-MO numbers are in the Section 5.2 text (not the table); Figure 12 caption should say RobCoT
  curves are Qwen2.5-14B-Instruct (the port is Qwen3-14B).
- Finke harness lives in `cleanup` under `external_organisms/finke2026/`; their repository has no license, so it is
  fetched at a pinned commit, never redistributed.

## 6. Cluster housekeeping (optional)

- WatGPU (shared account `nvduddu`): 12 user-held `possum_wg_*` jobs from 10-22 (q3_train, gcg14, pfms2) are stale
  scope-cut intentions; cancel them only with Mark's OK. Node watgpu1101 (plus 108/208/308/408/508) corrupts or hangs
  2-GPU jobs.
- Validation workspaces: `/project/6105494/markwang/wcmo-validate/` (nb, smoke7, kgcg), `/project/6105494/markwang/finke/`
  (Killarney Finke env and results), `/project/aip-rudner/markwang/finke/` (Vulcan), `/u801/nvduddu/possum-mark/` (WatGPU).
  Do not delete artifacts without Mark's sign-off.
- Monitoring scripts (`~/.possum/start_watchers.sh`, `race_guard.sh`, `follow_events_dedup.sh`) are all stopped.
