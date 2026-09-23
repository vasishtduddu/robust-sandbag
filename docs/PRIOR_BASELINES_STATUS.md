# Tables 1-3 campaign status (RoBCoTDstl + prior baselines)

Current state only; rewritten 2026-09-23 15:45 EDT. The full dated log (09-01 to 09-23, ~9,000 lines,
every job id and decision) is archived verbatim at
[`archive/PRIOR_BASELINES_STATUS_LOG_to_2026-09-23.md`](archive/PRIOR_BASELINES_STATUS_LOG_to_2026-09-23.md).
Cluster access, shapes and monitoring: [`CLUSTER_ACCESS_AND_HANDOFF.md`](CLUSTER_ACCESS_AND_HANDOFF.md).
Per-cell provenance: [`../tables/PROVENANCE.md`](../tables/PROVENANCE.md). New dated entries go at the
top of the **Log** section below.

## 1. Tables: complete in our scope

| paper table | file | filled | note |
|---|---|---|---|
| Table 1 (prior work) | `tables/table2.gen.tex` | **324/324** | |
| Table 2 (proposed) | `tables/table3.gen.tex` | 264/324 | **RoBCoTDstl rows 108/108**; the 60 empty cells are RobPWD/RobAdapt (teammate's scope) |
| Table 3 (whitebox) | `tables/table4.gen.tex` | **54/54** | |

File numbers are one higher than paper numbers (tables were renumbered, files were not). Verified
2026-09-23 from the generated files: `~/.possum/cell_census.py` exits 0, the structure matches
`sandbagging.pdf`, and 7B/MMLU-Prof Jlbrk 57.4 (SE 28.7) and 14B/Cyber 16.7 (SE 15.7) recompute from
their per-seed blobs. The 14B MMLU-Prof/HSS Jlbrk blobs on Vulcan (`t3`, `t3rd`) are identical to the
Killarney reporting root. **No cell is waiting on any job.** Re-render only if a methodology change
is approved (`~/.possum/render_tables.sh`, then commit with a PROVENANCE entry, then push).

## 2. Live experiments (Mark's 2026-09-23 follow-ups; neither can render into a table)

Both are on 7B/MMLU-Prof, the weakest 7B cell (published per-seed Jlbrk 0.0 / 85.5 / 86.7). Each
attack job runs an eff pass, then a 4-axis VAL gate (val nopw <= 0.05, Delta >= 0.15, pw/base >= 0.90,
HS/CSQA ratio >= 0.85), then AutoDAN val+test at n=25. A gate-failed draw exits 0 and spends no
attack: that is a result (an invalid draw), not an error. Logs print the verdicts in plain text.

| arm | root | seeds | train | attack (afterok) |
|---|---|---|---|---|
| **A. Val-based selection (F664)** | `/scratch/markwang/possum-valselect` | 45 / 46 / 47 (slots run05-07) | `5627174` / `5627176` / `5627178` (4 h) | `5627175` / `5627177` / `5627179` (42 h) |
| **B. 32B distillation teacher** | `/scratch/markwang/possum-teacher32b` | 42 / 43 / 44 (slots run01-03) | corpus `5626531` (h100:1) -> `5627183` / `5627185` / `5627187` (5 h) | `5627184` / `5627186` / `5627188` (42 h) |

* **Reading A:** a draw is robust iff its **VAL** AutoDAN < 0.25. Report the success rate over the
  6-draw pool (published s42/43/44 plus these three) with every per-seed TEST value shown. Selection
  touches val only. **Ask Mark before changing any cell**: adopting selection changes the methodology,
  and by F664's fairness condition the prior baselines would then need the same k-draw budget.
* **Reading B:** compare the three teacher32b students with the published cell on weak-draw rate,
  lock, capability and utility. The teacher is the only variable (same student, seeds and recipe).
  The generator log prints kept-row counts and question overlap between the two corpora; that is the
  only channel a teacher can act through (the corpus is a rejection filter, and Stage B targets are
  single letters). Results carry `POSSUM_VARIANT_RECIPE=teacher32b`, so they cannot render.
* **Isolation (keep it):** `default_cot_paths()` puts no teacher in the filename, so a 32B corpus in
  `datasets/cot` would overwrite the corpus every canonical organism trained on. B writes to its own
  root, and the train job aborts if the corpus is byte-identical to the 7B one.
* **Sizing (why v2 exists):** 7B MMLU-Prof training takes 2:11-2:16 per draw, and a RESISTANT draw
  costs ~1900 s per AutoDAN item (`5245675`: 83 items in 43:49), so 50 items is ~28 h. `_run_eval`
  writes the JSON only at the end. The original chain (3 serial draws in 6 h, attacks at 20 h) could
  not finish; it was cancelled with Mark's authorization.

Also queued on Killarney, figure inputs only (no table cell): `5567409-13` 32B pwd sweeps (h100:2,
est. 09-26) and `5567431` 14B chem SFT, ours arm (h100:1, est. 09-27; chem is a 29.5 GB full model
and does not fit WatGPU's shared disk).

## 3. Disclosures the paper must carry

| where | disclose |
|---|---|
| All Jlbrk columns | AutoDAN is best-of-150 search on the answer key: it measures search reachability, not single-shot elicitation (85.5% at 150 draws is ~3.6% at one). |
| Table 3 Pruning / Fuzzing | each cell is the sweep MAXIMUM with the config chosen on the test split (an oracle estimand). |
| Table 2 RoBCoTDstl Jlbrk | nine cells are strongly bimodal (seeds ~0 or ~85-90); report as a spread, e.g. 32B/MMLU-Stem 46.0 / 0.0 / 10.0 = 18.7(14.0). Mark ruled 09-21 the tables keep mean(SE). |
| T1 SysPr / 32B / WMDP-Cyber | the organism does not sandbag cyber (row Delta -3.1), so its Jlbrk attacks nothing hidden. |
| T1 CktBr / 14B / MMLU-HSS | s42 test Delta +0.064 (val +0.1075, under the 0.15 bar): its recovery denominator is 6.4 points. Circuit-breaking destroys up to half the parent lock on MMLU-Prof/HSS; reported as-is (Mark 09-21). |
| T1 CktBr / 14B / MMLU-Prof and MMLU-HSS | E/U columns come from the attacked (rebuilt) cohort, not the originally published one; MMLU-Prof s44 is draw 2 of 2. |
| T2/T3 14B / MMLU-Prof s43 | redraw c: test pw/base 0.891 (val 0.925; selected on val); 1 usable draw of 5. |
| T2/T3 14B / MMLU-Prof s44 | draw 2 of 2 (Mark 09-19); clean (nopw 0.0000, pw/base 0.987). The 9.3% leak disclosure of draw 1 is WITHDRAWN. |
| T2/T3 14B / MMLU-HSS s43 | draw 2 (Mark 09-19): test pw/base 0.860 (val 0.899), CSQA/base 1.040, nopw 0.0000. |
| T2/T3 14B / WMDP-Bio s43 | draw 4 (the promoted organism in `run02`). |
| T2 7B / MMLU-HSS Jlbrk 0.0(0.0) | no source blob on this machine; Mark ruled 09-18 it is genuine (measured elsewhere). Boxplot carries it as three `published_zero_cell` rows. Do not re-open. |
| T3 32B / WMDP-Cyber | phenotypically mixed: s42 carries a generalised F habit (CSQA/b 0.571, bleed gate relaxed for whitebox only), s43/s44 task-scoped. |
| pw/base deficits | Mark 09-15: NO paper caveat (SEs cover it); per-cell values stay in PROVENANCE. |
| pwd figure arms | Mark 09-17: being retrains needs NO disclosure. |

## 4. Standing rulings (Mark) and settled findings: do not re-open

* Never re-run a cell with a good published value; spend compute on missing cells; ask before treating
  a published value as unsourced. Keep and disclose borderline misses; only Delta <= 0 forces a redraw.
* Never delete artifacts (move aside or copy). Push every status/provenance/table/figure commit.
* `scancel` is authorized for this campaign's own jobs only; the Tables 4/5 session shares the
  `markwang`/`nvduddu` users (`t45_*` jobs are theirs). Tables 4/5 are out of scope here.
* Table 2 bimodality: tables do NOT change (09-21); per-seed minima are analysis only.
* SFT/DPO figures: SFT first; **DPO at 14B/32B is still owed** (Mark 09-21, not yet scheduled).
  At 7B, DPO recovers 0.000 of RoBCoTDstl's lock on all 9 curves vs +0.787 mean for pwd, but 5 of 9
  of ours lose with-password capability by the endpoint; plot capability beside the locked series.
* More LAT does NOT fix AutoDAN-weak draws (09-23 pilot: +150 LAT steps moved pool escape
  0.169 -> 0.152 while CSQA/base collapsed to 0.15 / 0.28 / 0.00). Never plot those top-up numbers:
  they are pool escape, not AutoDAN, on organisms that failed utility.
* `POSSUM_LOCK_CONTEXT_AUG=0.5` destroys the F-lock (nopw 0 -> 0.35); prefix brittleness refuted 16/16 (F570).
* Open science question: across 38 attacked obfcot seeds, AutoDAN success is near-binary (0.000 or
  0.87-0.92), stratifies by search cost, and gets worse with size (7B 2/15 broken, 14B 5/17, 32B 9/18).
  No organism property measured so far separates broken from robust seeds. Experiments A and B are the
  current probes.
* Table 8 (families + MATH) is PARKED by Mark.

## 5. Figures

* Jailbreak boxplots (prior vs ours): complete, 54 RoBCoTDstl seeds, tables-4-5 styling.
* Fuzzing/Pruning sweeps: 7B and 14B complete, both arms (`tables/sweep_*`, `tables/sweep_ours/`).
  32B pwd sweeps wait on `5567409-13`.
* SFT trajectories: 7B complete; 14B 5 of 6 tasks (chem waits on `5567431`). DPO: 7B only.

## 6. Clusters (2026-09-23 15:45 EDT)

* **Killarney:** fairshare 0.21; nothing of ours running; the 13 live jobs above plus 88 HELD
  jobs, all redundant with finished work (held on purpose; never release one without checking that
  its output already exists).
* **WatGPU:** ~40 GB free on the shared disk; the Tables 4/5 session is using it. Ours: two HELD
  `possum_wg_euwb14b_prof_s43/s44` (redundant).
* **Vulcan:** Tables 4/5 session active. Ours: six HELD (`967681-84`, `1020919`, `1105916`), all
  redundant with filled cells.
* **Event reconciliation (09-23):** every terminal job since 09-15 on all three clusters was reported
  by a watcher (Killarney 90, Vulcan 61, WatGPU 114 non-t45) except WatGPU `1560066`, whose result is
  already in the 14B bio SFT figure. Every result JSON written since 09-21 is reflected in the tables.

---

# Log (newest first)

## 2026-09-23 15:45 EDT: event reconciliation; status doc condensed
Reconciled every terminal job on all three clusters against the watcher ledgers, this doc and
PROVENANCE (section 6): nothing was missed. Archived the 9,000-line log and rewrote this file as
current state only.

## 2026-09-23 15:25 EDT: superseded follow-up jobs cancelled
`5626532`, `5626533`, `5627031-33`, `5627034/35/44` cancelled with Mark's explicit authorization,
after verifying each was ours and had never started. They would have trained into the same run slots
and written the same result JSONs as the v2 jobs.

## 2026-09-23 15:10 EDT: follow-up chains rebuilt per seed (two walltime bugs)
The val-selection train job trained 3 draws serially in 6 h (needs ~6:40), and all three attacks
were `afterok` on it. All six attacks had 20 h, but a resistant draw needs ~28 h for 50 items. Rebuilt
as one job per seed (section 2); only walltime, partition, job name, log path and seed loop changed,
verified by diff. Scripts: `*_v2.sbatch` beside the originals in each root's `slurm/`.
