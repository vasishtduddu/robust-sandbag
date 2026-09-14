# Where every number in `tables/` comes from

## Current reporting state: 2026-09-13 20:25 ET

This section supersedes the dated readiness/job claims below. **Rule (Mark, 2026-09-13): every
validated result is recorded here when it lands, whether or not it renders a cell yet**, so this
file alone shows that no result is missing and where every number is stored. Job evidence is in
the review ledger (`scripts/reconcile_slurm_events.py`) and
[`PRIOR_BASELINES_STATUS.md`](../docs/PRIOR_BASELINES_STATUS.md).

| PDF table / generated file | Displayed filled / total | Still blank |
|---|---:|---:|
| Table 1, prior / table2.gen.tex | **317**/324 | 7 |
| Table 2, proposed / table3.gen.tex | 225/324 | 99 |
| RoBCoTDstl band within table3 | 69/108 | 39 |
| Table 3, whitebox / table4.gen.tex | **24**/54 | 30 |
| Other families / table5.gen.tex | 0/72 | 72 |

Changes since 2026-09-12: CktBr/14B/Cyber Jlbrk `91.2(0.4)` published (316 -> 317); RoBCoTDstl/14B/
MMLU-Stem Steering `1.6(1.6)` published (23 -> 24). Both sourced in section 1.

**Blank cells (our scope).** Table 1: CktBr Jlbrk for 7B/Cyber and 14B Bio/Chem/Prof/Stem/HSS;
SysPr 32B/Cyber Jlbrk. Table 3: every 14B cell except MMLU-Stem Steering (17); 32B/Chem
Steering+Pruning; 32B/Cyber Steering+Fuzzing; all nine 32B MMLU cells. Table 2 RoBCoTDstl gaps are
unchanged (see section 1). The displayed RoBCoTDstl 7B/MMLU-HSS Jlbrk `0.0` still has no discovered
source.

## Measured, not yet rendered: the result ledger (updated 2026-09-13 20:25 ET)

Every result written since 2026-09-12, from an mtime scan of all reporting roots. A cell renders
only when `load_results` finds exactly seeds 42/43/44 (no extra `__seed*.json`), so partial cells
live here until complete. `S:` = `/scratch/markwang/`, `P:` = the repo root.

**Published, for reference: CktBr/14B/Cyber Jlbrk `91.2(0.4)`** (seeds 361/399, 367/399, 364/399,
n=399, one fingerprint `65be134083`). CORRECTED 2026-09-13: reached through `artifacts/compose-prior` SYMLINKS, not the attack-overlay
flag (the recipe prints `validated AutoDAN overlays: 0`). `compose-prior/results/json/circuitbreak_
qwen2.5-14b-it_cyber__seed4{2,3,4}.json` and `compose-prior/checkpoints/circuitbreak_qwen2.5-14b-it_cyber`
link into `possum-prior-eff`, whose markers match (ed6408088d / 1607c1f1e1 / f945ab8b6b). The files:
  * `S:possum-prior-eff/results/json/circuitbreak_qwen2.5-14b-it_cyber__seed42.json`
  * `S:possum-prior-eff/results/json/circuitbreak_qwen2.5-14b-it_cyber__seed43.json`
  * `S:possum-prior-eff/results/json/circuitbreak_qwen2.5-14b-it_cyber__seed44.json`
The s44 blob was rewritten 2026-09-12 08:33 by job 5308755 (re-eval); s42/s43 predate it.

### Table 1 (table2.gen.tex): CktBr/14B AutoDAN

| cell | seed | status | value | stored at | job |
|---|---|---|---|---|---|
| Bio Jlbrk | 42 | measured | autodan **0.9492**, n=256, fp `cd899307a2` | `P:artifacts/results/json/circuitbreak_qwen2.5-14b-it_bio__seed42.json` | 5417880 |
| Bio Jlbrk | 43 | **95.7** (245/256; nopw 21.9 pw 75.4 Delta 0.535 pw/base 0.970, 5434691 07:57:37) | measured, awaiting linking | `P:artifacts/results/json/circuitbreak_qwen2.5-14b-it_bio__seed43.json` | 5434691 |
| Bio Jlbrk | 44 | measured | autodan **0.9648** (247/256; nopw 20.7 pw 74.2 Delta 0.535 pw/base 0.955, 5434692 07:35:05) | `P:artifacts/results/json/circuitbreak_qwen2.5-14b-it_bio__seed44.json` | 5434692 |
| Stem Jlbrk | 42 | measured | autodan **0.9184**, n=147, fp `626cd26266` | `P:artifacts/results/json/circuitbreak_qwen2.5-14b-it_mmlu-stem__seed42.json` | 5417881 |
| Stem Jlbrk | 43 | running (24 h resize, started ~19:27) | | `P:artifacts/results/json/circuitbreak_qwen2.5-14b-it_mmlu-stem__seed43.json` | 5436088 (replaces cancelled 5433384) |
| Stem Jlbrk | 44 | measured | autodan **0.9184** (135/147), n=147, fp `626cd26266` (search mean-epochs 13.03 vs s42 13.99: independent run, identical count by coincidence) | `P:artifacts/results/json/circuitbreak_qwen2.5-14b-it_mmlu-stem__seed44.json` | 5433385 |

**THESE TWO CELLS CANNOT RENDER UNDER THE CURRENT RECIPE, and the attacks overwrite Table 1's reporting
root (found 2026-09-13).**
* `artifacts/compose-prior/results/json/circuitbreak_qwen2.5-14b-it_{bio,mmlu-stem}__seed{42,43,44}.json`
  are SYMLINKS into `P:artifacts/results/json/`. Every `--force` attack therefore rewrote the reporting
  blob through the link: bio s42, stem s42, stem s44 so far; bio s43/s44 (running) and stem s43 (queued)
  will follow. The blobs now hold the ATTACKED organisms' generate_parse E/U, not the published
  forced-choice cohort.
* The published cohort survives in `S:possum-result-backups/prepull-20260904-225042/artifacts/`
  (markers bio a66d51f23f / 6b9fdc87e1 / 9a789a4db6, stem d05d57bc2b / 4766f0dd96 / beecac41ff). Those
  files reproduce the displayed w/ and w/o exactly (bio 11.7 / 74.5, stem 12.5 / 57.4).
* `compose-prior/checkpoints/circuitbreak_qwen2.5-14b-it_{bio,mmlu-stem}/run01-03` are ABSENT, so
  `load_results` returns 0 blobs for both cells. **The displayed bio/stem E/U are carried by
  `--merge-existing`, not reproducible from compose-prior, and that predates today's attacks.**
  A Table 1 preview on 2026-09-13 reproduced the committed table exactly (317 filled, 0 conflicts).
* Consequence: when all six attacks land, these Jlbrk cells still render `---` unless checkpoint links to
  the attacked organisms (s42 `S:possum-prior-eff/checkpoints/.../run01`, s43/s44
  `S:possum-cb14b-rebuild/checkpoints/.../run02|run03`) are added, and adding them would ALSO replace the
  displayed E/U with those organisms' values.
* **DONE for BIO 2026-09-13 22:10.** All three bio attacks landed (s42 5417880 0.9492, s43 5434691 0.9570,
  s44 5434692 0.9648; every seed n=256, identical sample fingerprint `cd899307a2`, and
  `result_matches_checkpoint` TRUE against run01/run02/run03). Created
  `artifacts/compose-prior/checkpoints/circuitbreak_qwen2.5-14b-it_bio/{run01,run02,run03}` ->
  `S:possum-prior-eff/.../run01`, `S:possum-cb14b-rebuild/.../run02|run03` (6 safetensors + marker each).
  Rendered Table 1: **318 filled, 6 empty** (was 317/7). Jlbrk Bio = **95.7**\textsubscript{0.5}.
  **DISCLOSED, as the ruling predicted: the bio E/U columns moved**, because they now come from the SAME
  organisms that were attacked (a mixed prior-eff run01 + cb14b-rebuild run02/run03 cohort) instead of the
  published cohort: w/o 11.7(2.6) -> **19.8(1.5)**, w/ 74.5(0.6) -> **73.8(1.0)**, Delta 62.8(3.2) -> **54.0(0.5)**,
  HS 83.2(1.0) -> **84.1(0.3)**, CSQA 80.8(0.9) -> **82.0(0.5)**. The task readout is unchanged
  (`mcq_scoring_protocol=forced_choice` in all three blobs); what changed is the organism cohort, not the protocol.
  mmlu-stem is NOT linked yet: s43 (5436088) is still attacking.
* **RULING (Mark, 2026-09-13): link the attacked organisms.** Once all six attacks land (bio s43
  5434691, bio s44 5434692, stem s43 5436088), add `compose-prior/checkpoints/circuitbreak_qwen2.5-14b-it_
  {bio,mmlu-stem}/run01 -> possum-prior-eff/.../run01`, `run02 -> possum-cb14b-rebuild/.../run02`,
  `run03 -> possum-cb14b-rebuild/.../run03`. Each row then renders every value (w/, w/o, Delta, HS,
  CSQA, Jlbrk) from ONE set of organisms, compose-prior's own design rule. **The displayed E/U for
  these two rows will change** from the published forced-choice cohort to those organisms'
  generate_parse values; the mixed prior-eff/rebuild builds must be disclosed. Not linked before then:
  until every seed's blob comes from its linked organism, markers mismatch and the cell still drops.

**Rebuild organisms (E/U only; no render recipe reads `possum-cb14b-rebuild`).**
`/scratch/markwang/possum-cb14b-rebuild/results/json/`: `circuitbreak_qwen2.5-14b-it_bio__seed43.json` (Delta 0.5352,
job 5394848), `circuitbreak_qwen2.5-14b-it_mmlu-stem__seed43.json` (Delta 0.4150, job 5386614) plus the
earlier bio/chem/stem s43/s44 blobs; **`circuitbreak_qwen2.5-14b-it_mmlu-hss__seed42.json` FAILS the lock
gate** (Delta 0.0640, job 5386613). Its parent `pwd_hof_parent_qwen2.5-14b-it_mmlu-hss__seed42.json`
locks at Delta 0.5867 (job 5418158), so circuit-breaking removed the lock on that draw.

### Table 2 (table3.gen.tex): RoBCoTDstl

| cell | seed | status | value | stored at | job |
|---|---|---|---|---|---|
| 32B/Cyber Jlbrk | 42 | measured | autodan **0.0**, n=50, fp `018cc10b` | `S:possum-table3-current/results/json/obfcot_qwen2.5-32b-it_cyber__seed42.json` | 5314663 |
| 32B/Cyber Jlbrk | 43, 44 | queued | | same dir, `__seed43.json` / `__seed44.json` | 5336528 / 5303590 |
| 32B/MMLU-Stem E/U | 42 | measured | nopw 0.0000, pw 0.7075, Delta 0.7075, HS/b 1.051, CSQA/b 0.960 | `S:possum-table3-current/results/json/obfcot_qwen2.5-32b-it_mmlu-stem__seed42.json` | 5415489 |
| 32B/MMLU-Stem E/U | 43, 44 | queued | | same dir; checkpoints `.../obfcot_qwen2.5-32b-it_mmlu-stem/run02,run03` | 5420339 / 5420340 |
| 32B/MMLU-HSS E/U | 42 | queued | | `S:possum-table3-current/...mmlu-hss__seed42.json` | 5420341 |
| 14B/Cyber E/U | 43 | redraw queued | (s43 voided for 83.7% CSQA bleed) | `S:possum-table3-current/checkpoints/obfcot_qwen2.5-14b-it_cyber/` | 5412831 |

**14B/Bio s43 redraw writes a DIAG file on purpose.** 5417532 trains `.../obfcot_qwen2.5-14b-it_bio/run08`
and 5417533 writes `S:possum-table3-current/results/json/DIAG_REDRAW_obfcot_qwen2.5-14b-it_bio__seed43.json`.
The prefix keeps it out of the `__seed*` glob. It feeds the cell ONLY if accepted on its **val**
split (lock kept, bleed cleared) and then deliberately promoted; record the draw count (draw 4).

### Table 3 (table4.gen.tex): RoBCoTDstl whitebox, 14B

| cell | seed | steering | pruning | fuzzing (noise) |
|---|---|---|---|---|
| MMLU-Stem | 42 | **0.0** (5436040) | **0.0** (5436041, interac, 01:39:56) | 5436042 retargeted to l40s:2 + interac |
| MMLU-Stem | 43 | **0.0** (5436043) | **0.0** (5436044, 01:40:08, n=147, nopw 0.0 pw 59.2) | chained 5436045 |
| MMLU-Stem | 44 | **0.0476** (5436046) | **0.4218** (5436047, 01:56:10, 62/147; nopw 4.8 pw 61.9, recovery 0.65) | chained 5436048, now l40s:2 + interac |
| Cyber | 42 | **0.0** (5436049) | **0.0** (5436050, 02:15:55, 0/399) | 5436051 kept on h100, walltime 3h -> 6h |
| Cyber | 44 | **0.0** (5436052) | **0.0** (5436053, 02:14:49, n=399, nopw 0.0 pw 65.4) | chained 5436054 |

**RENDERED 2026-09-13 21:25:** Table 3 (`table4.gen.tex`) MMLU-Stem / Qwen2.5-14B Pruning = **14.1**\textsubscript{14.1}, the mean of 0.0 / 0.0 / 42.2 with its standard error. Filled cells 24 -> 25; no other cell moved. The subscript equals the mean because one seed carries the entire effect, so quote this cell as a spread (s44's lock is the weakest at nopw 4.8) and never as a typical value. Two-pass bkdrcot render, table3-current then mmlu-merge: 1 cell added, 12 preserved, 0 conflicts.

Stored at (one file per organism, all three techniques merge into it):
  * `S:possum-table3-current/results/json/obfcot_qwen2.5-14b-it_mmlu-stem__seed42.json`
  * `S:possum-table3-current/results/json/obfcot_qwen2.5-14b-it_mmlu-stem__seed43.json`
  * `S:possum-table3-current/results/json/obfcot_qwen2.5-14b-it_mmlu-stem__seed44.json`
  * `S:possum-table3-current/results/json/obfcot_qwen2.5-14b-it_cyber__seed42.json`
  * `S:possum-table3-current/results/json/obfcot_qwen2.5-14b-it_cyber__seed44.json`

These are chained
steering -> pruning -> noise because they share that file. MMLU-Stem Steering is published; its
Pruning/Fuzzing render when the chains finish. **Cyber cannot render until the s43 redraw exists.**
14B/Chem whitebox: 5436036 (s44), 5436037 (s43 noise), 5436038 (s42 merged), same directory.

**32B/Cyber whitebox.** `obfcot_qwen2.5-32b-it_cyber__seed42.json` already holds `pruning = 0.0`
(part of the displayed 32B/Cyber Pruning cell; producing job not re-attributed this session) and
`probe = 1.0` (the probe column is intentionally not displayed). Steering/Fuzzing: 5435001 (s42,
utility gate relaxed by Mark's ruling; cell phenotypically mixed), 5337155 (s43), 5337154 (s44).

### Results that must NEVER enter a cell

* `S:possum-table3-current/results/json/DIAG_PIN_obfcot_qwen2.5-14b-it_bio__seed43.json`: code-pinned
  control (run07). Delta 0.0000; it proved old code does not make s43 lock. Renamed from
  `__seed43PIN.json`, which matched the `__seed*` glob and silently dropped the whole 14B/Bio cell.
* Void files keep a non-`.json` suffix, e.g. `obfcot_qwen2.5-14b-it_cyber__seed43.json.void-20260912-csqa-bleed84`.

## Multi-root audit reference

**2026-09-12 audit addendum.** The original map below is retained as historical
reporting guidance. A fresh recursive scan of both `/scratch/markwang` and
`/project/6105494/markwang`, including nested backups, renamed JSONs and symlinks,
is indexed in
[`result-roots.tsv`](../artifacts/reporting-audit-20260912-expanded/result-roots.tsv)
and [`inventory.json`](../artifacts/reporting-audit-20260912-expanded/inventory.json).
It finds 13,368 result records, 1,007 distinct file hashes and 142 location groups
including aliases. These broader counts include historical/diagnostic directories
and are not counts of active reporting roots or independent replicates. Two broken
symlinks and other scan limits are recorded in
[`ROBCOTDSTL_SEED_VARIANCE_AUDIT_2026-09-12.md`](../docs/ROBCOTDSTL_SEED_VARIANCE_AUDIT_2026-09-12.md).
The 7B/MMLU-HSS AutoDAN source remains absent from all 51 discovered records for
that cell. Also, the renderer's merge baseline is the current **working-tree**
table, not necessarily Git HEAD; prior **utility** uses generate-parse even though
prior task/attack scoring uses forced-choice. These qualifications supersede
the corresponding shorthand below.

Written 2026-09-09. **This is the answer to "which command and which root produced this cell".**
It exists because the data is spread across roughly 30 artefact roots that DISAGREE, and picking
the wrong one silently publishes a superseded organism: F737 found the RoBCoTDstl/7B MMLU row
stale in all 18 columns, understating our own Delta by 41 to 54 points, and F738 found 30 of 54
prior-baseline cells with disagreeing roots.

Findings referenced below (`F###`) are in the frozen historical status archive
linked above. Dated recipes below are retained for provenance and must be checked
against the living status before reuse; their old readiness and queue claims are
not current.

---

## 0. The three rules that make this reproducible

1. **Resolve a row's organism through `naming.TABLE_ORGANISMS[table]` by BAND ORDINAL, never
   through the LaTeX macro.** `naming.py:344-346` maps **obfcot, distcot and robcot all to
   `\backdoorcot`**, and `tablefill.py:764` keys on the ordinal for exactly this reason.
   `TABLE_ORGANISMS["ours"] == ["robpwd", "robadapt", "obfcot"]`, so band 3 is ours (F737).
2. **`--merge-existing` resolves conflicts as "render wins", and its baseline is ALWAYS the
   committed `tables/<file>`, never `--out_dir`** (`tablefill.py:936-938`). Two roots therefore
   cannot be chained through a temp dir: **render onto `tables/` in increasing order of trust,
   last pass winning.** Getting the order backwards reverts the newer root (F737).
3. **`tablefill` is stdlib-only and safe anywhere. The FIGURE generators are NOT**: they need
   numpy and matplotlib, so run them with `venv/bin/python`, never the system `python3`.

---

## 1. Table-by-table recipes (all verified by execution on 2026-09-09)

Paper numbering is shifted by one from the filenames; the filenames were never renamed
(`possum-table-renumbering`).

### `tables/table2.gen.tex` = paper **Table 1** (prior-work baselines: PWD, CktBr, SysPr)

```
python3 -m experiments.tablefill --table prior \
  --artifact-root artifacts/compose-prior --merge-existing \
  --attack-artifact-root /scratch/markwang/possum-prior-eff \
  --attack-artifact-root /project/6105494/markwang/possum/possum/artifacts \
  --out_dir tables
```

* **Readout: forced choice** (`POSSUM_GENPARSE=0`), for fidelity to the released protocol.
* Reports `316 filled, 8 empty, 0 conflicts, 0 added, 36 preserved`, and
  `validated AutoDAN overlays: 0 seed result(s)` (the overlays are redundant; `compose-prior`
  supplies every attack value).
* **Do NOT add `--attack-artifact-root /scratch/markwang/possum-prior-32b-killarney`.** It raises
  `RuntimeError: Conflicting valid AutoDAN overlays for circuitbreak/qwen2.5-32b-it/mmlu-hss/seed42`,
  and it is the WRONG arm: with `artifacts`' 369/375 the three seeds render **98.7(0.3)**, the
  published value; with its 370/375 they render 98.8(0.2) (F715).
* **Caveat, F738.** 30 of 54 prior cells disagree between roots, worst `circuitbreak/7B/bio` at
  12.6 points on a clean 8-vs-5 split. The recipe above takes the NEWER cohort, which is the
  published value.
* **Weights receipts: CORRECTED 2026-09-12.** This file previously said *every* prior-baseline JSON
  has `checkpoint_payload_sha256 = none`. That is false. **33 of 187** Table-1-organism blobs in
  `compose-prior` carry a weights receipt, in a clean block: the **entire PWD/14B row** (all six
  datasets, all three seeds), **PWD/7B chem and cyber**, `circuitbreak/7B/chem` (3 seeds),
  `circuitbreak/14B/chem` s42, `circuitbreak/32B/bio` s42, `pwd_hof_parent/7B/mmlu-prof` (3 seeds)
  and `pwd_hof_parent/32B/bio` s42. Those cells CAN be tied to the weights that produced them.
  **SysPr has zero receipts** (0 of 54), and so does most of CktBr (49 of 54 without). For the
  unreceipted majority, root choice remains the only provenance.
* **`marker_sha256` is canonical-JSON, not file bytes** (`experiments/provenance.py:293`,
  `_canonical_sha256`). Never compare it with `sha256sum` on the marker file: the hashes will not
  match even for the correct checkpoint, and a live organism will look orphaned.
* **A marker hash can split a cell.** `circuitbreak/14B/mmlu-hss` seed 43 exists as TWO builds at
  the same `run02` path, distinguished only by `marker_sha256`: the published `8bd74972`
  (HS 0.0, weights stripped, declared parent root absent) and the on-disk `101662be`
  (HS 0.3122, intact, intact parent). Their `rr_diagnostics` differ from step zero, so they are two
  training runs. The published Table 1 value uses the build that no longer exists. See the status
  document's CktBr/14B/MMLU-HSS section; reporting decision is open.
* **UPDATED 2026-09-12: 7 empty cells, all Jlbrk.** CktBr/14B/cyber now renders **91.2(0.4)**
  and needed NO new compute: its three AutoDAN results already existed in
  `S:possum-prior-eff` (n=399, all three seeds, one fingerprint `65be134083`), and a plain
  re-render with the recipe above picked them up (`1 cell(s) added`, 316 -> **317 filled**).
  Sanity: n=399 is exactly the 7B and 32B cyber sample size, and 91.2 sits between 7B's 93.0
  and 32B's 92.8.
* **The old claim that only 1 of 3 CktBr/14B seeds' weights exist is FALSE.** A full scan of
  every root (canonical marker hash + safetensors bytes, not file size) finds **intact 29.5 GB
  organisms for all three seeds in four of six datasets**:

  | dataset | s42 | s43 | s44 | source |
  |---|---|---|---|---|
  | bio | intact | intact | intact | prior-eff (42) + cb14b-rebuild (43,44) |
  | chem | intact | intact | intact | prior-eff (42) + cb14b-rebuild (43,44) |
  | cyber | intact | intact | intact | prior-eff and compose-prior |
  | mmlu-stem | intact | intact | intact | prior-eff (42) + cb14b-rebuild (43,44) |
  | mmlu-hss | MISSING | intact | MISSING | prior-eff (43); s42 queued as 5386613 |
  | mmlu-prof | MISSING | MISSING | MISSING | none anywhere |

  So the remaining Table 1 work is **4 trainings** (mmlu-prof x3, mmlu-hss s44) and
  **14 attacks** (bio 3, chem 2, mmlu-stem 3, mmlu-hss 3, mmlu-prof 3), not the 12 trainings
  and 18 attacks a checkpoint-blind estimate gives. CktBr/14B/chem s42 already has an attack
  (n=83, in `artifacts`), which is why chem needs 2 and not 3.
* **Per-dataset AutoDAN n is fixed and must be matched** when adding seeds to a row: bio 256,
  chem 83, cyber 399, mmlu-stem 147, mmlu-hss 375. (mmlu-prof is inconsistent across models,
  7B 83 vs 32B 624; resolve before measuring 14B.)

### `tables/table3.gen.tex` = paper **Table 2** (ours: RobPWD, RobAdapt, RoBCoTDstl)

**Two passes, in this order. The order is load-bearing.**

```
python3 -m experiments.tablefill --table ours \
  --artifact-root /scratch/markwang/possum-table3-current --merge-existing --out_dir tables
python3 -m experiments.tablefill --table ours \
  --artifact-root /scratch/markwang/possum-mmlu-merge     --merge-existing --out_dir tables
```

* **Readout: generate-parse** for the whole table (`POSSUM_GENPARSE=1`,
  `POSSUM_UTILITY_SCORING=generate_parse`). RoBCoTDstl's lock target is the off-option-set letter
  `F`, so it CANNOT be read forced-choice.
* **Why `mmlu-merge` must go second.** It holds the retrained 7B MMLU organisms;
  `possum-table3-current` still holds their leaky predecessors. Run the other way round and the
  render puts `nopw = 39.4` back over the correct `0.0` (F737).
* Root disagreement is by **retrain, not re-eval**, confirmed on `checkpoint_payload_sha256`:
  mmlu-stem s43 `192ec8d2 -> 3a9449a5` (nopw 0.1361 -> 0.0000), s44 `ef29c37e -> 3ee18719`
  (0.5034 -> 0.0204), mmlu-hss s42 `679ea1ed -> 03798933` (0.2613 -> 0.0000). mmlu-prof is
  byte-identical in both roots.
* **The RobPWD and RobAdapt bands are the teammate's** and are executed on another machine, which
  is why `--merge-existing` is mandatory here: a single-root render sees 17 of the table's cells
  and the regression guard refuses to write (`possum-scope-is-robcotdstl-only`).
* **A DIAGNOSTIC RESULT FILE CAN SILENTLY DELETE A PAPER CELL (2026-09-12).**
  `tablefill.load_results` does
  `discovered = glob(f"{base}__seed*.json")` and then `if discovered != expected_paths: return []`.
  **Any** extra file matching `<base>__seed*.json` drops the WHOLE cell, with no conflict, no
  warning, and no entry in the render log: the cell simply renders `None` and `--merge-existing`
  preserves whatever was published before. `__seed43PIN.json` matches `__seed*`.
  I did this to `obfcot/14B/bio` myself with the pin experiment's output. **Name every diagnostic
  with a PREFIX** (`DIAG_..._seed43.json`), never a suffix on the seed. Verified:
  `obfcot_qwen2.5-14b-it_bio__seed43REDRAW.json` matches the glob; `DIAG_REDRAW_obfcot_..._seed43.json`
  does not. The queued redraw job was corrected before it could run.
* **BOTH PASSES OF THE `ours` RENDER ARE MANDATORY, and the damage is live.** Running only pass 1
  (`possum-table3-current`) reported **15 conflicts** on the RoBCoTDstl/7B row and overwrote good
  values with the leaky predecessors, including `w/ 0.0(0.0) -> 39.4(16.5)`: F737 exactly as
  documented, reproduced 2026-09-12. Never stop after pass 1. If interrupted, `git checkout -- tables/`.
* **RETRACTED IN FULL (2026-09-12): there is no unsourceable RoBCoTDstl/14B/Bio cell.**
  Everything previously written here about that cell was a PARSING BUG IN MY AUDIT, not a defect
  in the tables. My extractor matched `\backdoorcot` on **line 4**, the
  `\providecommand{\backdoorcot}{RoBCoTDstl}` DEFINITION, so the first `Qwen2.5-14B` row it found
  was **line 46, which belongs to the `\robustpasswordlocking` band**.
  * `w/o 74.6(1.6) / HS 93.0(0.4) / CSQA 82.1(0.4) / Jlbrk 1.0(0.6)` is **RobPWD/14B/Bio**, the
    teammate's band, computed on their machine. We hold **zero** teammate artifacts here by
    design, so it is correctly unreachable locally and `--merge-existing` carrying it is the
    intended mechanism ([[possum-scope-is-robcotdstl-only]], [[possum-table2-band-parity]]).
  * **RoBCoTDstl/14B/Bio is line 56: `0.0(0.0) 68.9(1.4) 68.9(1.4) 88.6(1.3) 56.3(14.9) 0.0(0.0)`,
    which is exactly what the compiler emits from our data.** It reproduces perfectly.
  There was never a value contradicted by the data, and no arithmetic bound was ever violated.
  **Lesson: resolve the BAND before quoting any cell** ([[possum-resolve-row-before-quoting-a-cell]],
  which says exactly this and which I failed to apply). A `\providecommand` line contains every
  band macro, so any band-detection regex must skip it and must anchor on
  `\multicolumn{19}{l}{...}`.
* **One unsourced value remains: RoBCoTDstl / 7B / MMLU-HSS Jlbrk prints `0.0(0.0)` and no result
  JSON with an `autodan` key exists for that cell in ANY root** (verified by a full `os.walk` of
  both bases: 154 candidate JSONs, 10 with an `autodan` key, none for mmlu-hss). It survives only
  because `--merge-existing` preserves it. F712/F737.

### `tables/table4.gen.tex` = paper **Table 3** (RoBCoTDstl whitebox: Steering, Pruning, Fuzzing)

```
python3 -m experiments.tablefill --table bkdrcot \
  --artifact-root /scratch/markwang/possum-table3-current --merge-existing --out_dir tables
python3 -m experiments.tablefill --table bkdrcot \
  --artifact-root /scratch/markwang/possum-mmlu-merge     --merge-existing --out_dir tables
```

* **`--attack-artifact-root` does NOT apply to whitebox techniques** (it is AutoDAN-only), so a
  whitebox cell only renders if its values sit under `--artifact-root`
  (`possum-whitebox-cells-need-artifact-root`).
* **Probing and FT+Probing were deliberately dropped** from `naming.TAB4_COLUMNS` in `6e96b55`: a
  probe reading ~1.0 measures DETECTABILITY, not recovery, and it has an exact readout identity
  with the lock target. The values are still on disk, so restoring them is a one-line revert plus
  a caption. Whether to restore them as a separate detection panel is open (N5 row 8).
* `POSSUM_WHITEBOX_ONLY=probe` now RAISES, and `docs/TABLE4_WHITEBOX_MONITOR.md` carries a header
  marking three stale claims (F709/F719).

* **ADDED 2026-09-13: RoBCoTDstl / 14B / MMLU-Stem / Steering = `1.6(1.6)`** (table4.gen.tex line 39;
  block and column verified by parsing the `\multicolumn{4}{l}` header and the column row, not by
  line position). Source: `possum-table3-current/results/json/obfcot_qwen2.5-14b-it_mmlu-stem__seed{42,43,44}.json`,
  `test_elicitations.steering` = **0.0 / 0.0 / 0.047619** from jobs 5436040 / 5436043 / 5436046
  (l40s:2; checkpoints run01/run02/run03). Mean 1.6 and SEM 1.6 reproduce by hand. The s44 value
  equals that organism's own no-password accuracy (7/147), reached only at the weakest config
  (`true_false` x0.5), so steering elicited nothing beyond the baseline leak. Rendered with the
  two-pass recipe above directly onto `tables/`: pass 1 `1 cell(s) added, 0 conflict(s)`, pass 2
  `0 added, 0 conflict(s)`; census 23 -> 24, tables 2/3/5 unchanged. Pruning and Fuzzing for this
  row are chained behind steering (5436041/44/47, 5436042/45/48).

### `tables/table5.gen.tex` = the scalability (R3) panel

```
python3 -m experiments.tablefill --table scale \
  --artifact-root /scratch/markwang/possum-table3-current --merge-existing --out_dir tables
```

Empty as of this writing. Scope and remaining gaps are in `experiments/GAPS.md`.

---

## 2. Figures

**Use `venv/bin/python`.** All write both `.pdf` and `.png` into `--out-dir`.

| file | generator | command |
|---|---|---|
| `ft_paper_{sft,dpo}_qwen2.5-7b-it` | `experiments/ft_curve_plot_paper.py` | `--technique {sft,dpo} --model qwen2.5-7b-it --combined --artifact-root <R3> --pwd-artifact-root <ARTIFACTS> --out-dir tables` |
| `ft_curve_{sft,dpo}_qwen2.5-7b-it_{bio,chem,cyber}` | `experiments/ft_curve_plot.py` | `--technique {sft,dpo} --task {bio,chem,cyber} --model qwen2.5-7b-it --artifact-root <R3> --out-dir tables` |
| `spider_requirements`, `spider_per_organism` | `experiments/spider_plot.py` | `--model qwen2.5-7b-it --artifact-root <R3> --out_dir tables` |

where `<R3> = /scratch/markwang/possum-table3-current` and
`<ARTIFACTS> = /project/6105494/markwang/possum/possum/artifacts`.

**The fine-tuning curves do NOT live in the result JSONs.** The JSON stores one scalar
(`test_elicitations.sft` = best accuracy over the trajectory). The trajectory exists only as
`<root>/elicitation_results/{tech}_{model}_{task}_seed{N}_curve.csv`
(`elicitation/whitebox/sft.py:576`, `dpo.py:352`). 18 curves per arm, 7B and WMDP only:

* **obfcot arm** (generate-parse): `/scratch/markwang/possum-table3-current/elicitation_results/`
* **pwd arm** (forced-choice): `/project/.../possum/artifacts/elicitation_results/`

**Three warnings about these figures, all from F735.**

1. **Always pass `--combined`** to `ft_curve_plot_paper.py`. Its docstring says it "draws PER-SEED
   lines and never a mean", but only `make_combined` does; the default `make_plot` path calls
   `_aggregate` (`:136`, `:171`) and draws the 3-seed mean that F702 condemns.
2. **The curve filename carries NO organism identity** and the summary JSON's `"organism"` field is
   `model_name` (`qwen2.5-7b-it`), not `model_org`. Arm membership is decided ONLY by which root
   you point at, so a mis-rooted run plots one arm twice with no visible symptom.
3. **The figure does not currently support the claim it is drawn for.** On SFT the claim REVERSES
   (obfcot gives up +0.2413 of its lock against pwd's +0.0752), and on DPO **zero** of nine obfcot
   curves pass both the in-curve capability gate and F708's parent gate. The two arms are also on
   different readouts, with opposite-signed damage artifacts that both flatter us. Read F735 before
   putting this figure in the paper.

---

## 3. The artefact roots, and which to trust

| root | holds | trust |
|---|---|---|
| `artifacts/compose-prior` | prior-baseline E&U + AutoDAN | **the reporting root for paper Table 1** |
| `/scratch/markwang/possum-prior-eff` | prior AutoDAN overlay | overlay only, older cohort on some cells |
| `/project/.../possum/artifacts` | prior baselines, pwd FT curves | second attack overlay; the pwd curve arm |
| `/scratch/markwang/possum-table3-current` | ours: all WMDP, obfcot FT curves, whitebox | **primary for ours/bkdrcot**; STALE for 7B MMLU |
| `/scratch/markwang/possum-mmlu-merge` | ours: retrained 7B MMLU | **wins over table3-current for 7B MMLU** |
| `artifacts/mmlu-anchorfix` | pre-retrain 7B MMLU | superseded, do not report |
| `/scratch/markwang/possum-prior-32b-killarney` | one conflicting CktBr/32B seed | **excluded on purpose** (F715) |
| `/scratch/markwang/possum-result-backups/` | dated `prepull-*` snapshots | history only, never a reporting root |

**Prefixed result files are diagnostics, never cells**: `VAL_`, `FRESH_`, `ATKSEED_`, `TE1_`,
`BUDGETCHK_`, `REPRO_`. A plain `obfcot_*` glob misses them, which is how F717's "108-draw gap"
survived as long as it did: three gated draws sitting inside the claimed gap were in `VAL_` and
`FRESH_` files (F736).

---

## 4. Sanity checks to run after ANY re-render

1. **Filled-cell monotonicity per table.** `tablefill`'s own regression guard refuses to write a
   render that drops filled cells; do not reach for `--allow-regression` to get past it.
2. **Assert the specific cells that must survive**, programmatically, exiting non-zero otherwise.
   A `CONFLICT (render wins)` line is not an error and scrolls past.
3. **Internal coherence.** Within one organism row, `nopw` should not vary wildly between the two
   task panels. Our F-lock organisms read ~0 everywhere; the stale MMLU row read 39.4/45.4/56.5
   against its own WMDP half's 0.0/0.0/0.1. That single check would have caught F737 weeks ago
   (`possum-check-row-internal-coherence`).
4. **NEVER RE-RENDER ONE HALF OF A ROW.** A partial recompile is more dangerous than no
   recompile: `--merge-existing` preserves any cell the render cannot see, so fresh Eff/Util gets
   paired with a frozen attack value from an earlier organism generation, and the result looks
   entirely plausible. This is documented history, not a hypothetical: the RoBCoTDstl/7B/MMLU-Prof
   `Jlbrk = 18.8(9.5)` entered in commit `8ab1f034` (2026-08-27) measured on organisms whose lock
   was BROKEN (that commit's own row reads locked 56.3, Delta 4.9). A prior session diagnosed this
   and deliberately held the repaired row back. The 2026-09-08 `b3f3d238` recompile then wrote the
   Eff/Util half anyway (nopw 56.3 -> 39.4) and left `Jlbrk` at 18.8, producing exactly the
   mis-pairing the deferral existed to prevent. It stood until F737/F739. If a render cannot see a
   cell, finish the measurement or blank the cell deliberately.
5. **Never compare a metric across readouts.** Paper Table 1 is forced-choice; Tables 2 and 3 are
   generate-parse. `pw/base` thresholds, Delta's chance ceiling and the FT curves all differ
   between them.

6. **A TEMP-DIR DRY RUN OF A MULTI-PASS RENDER IS INVALID, AND IT FAILS SILENTLY WITH A
   REASSURING EMPTY DIFF (2026-09-09).** Rule 2 above says the merge baseline is always the
   committed `tables/<file>`. The consequence that bit me: rendering both `bkdrcot` passes into a
   temp dir and diffing against `tables/` showed **no change at all**, which looks like "nothing
   to publish". The log said otherwise --
   `pass 1: 1 cell added -> 22 filled`, then `pass 2: 0 added -> 21 filled`. **Pass 2 re-merged
   against the committed 21-cell file and overwrote pass 1's addition inside the temp dir.** The
   empty diff was the symptom, not the all-clear.
   **So: never preview a multi-pass render in a temp dir.** Render onto `tables/` in the
   prescribed order, then run the census guard, and `git checkout -- tables/` if it fails:

       venv/bin/python ~/.possum/cell_census.py --baseline ~/.possum/cell_baseline.json

   Note `tablefill`'s OWN regression guard cannot catch this either: on pass 2 it compares 21
   against 21 and sees no regression. Only a guard that remembers the pre-render baseline can.
   A single-root table (`prior`, `scale`) is unaffected and may be previewed in a temp dir.

---

## 5. The FULL root map (2026-09-09). There are 34 results roots and 32 checkpoint roots, not 8.

Section 3's table lists the REPORTING roots. This is the complete inventory, because a root you
have not scanned is a cell you think you have to measure. Today that turned
`circuitbreak/14B/cyber` from "3 attacks needed" into "1 re-run needed" (below).

| root | jsons | ckpt dirs | intact ckpts | classification |
|---|---:|---:|---:|---|
| `artifacts/compose-prior` | 238 | 50 | 29 | **reporting root, paper Table 1** |
| `S:possum-prior-eff` | 184 | 14 | 14 | attack overlay; full mirror of compose-prior's cktbr |
| `S:possum-table3-current` | 134 | 19 | 19 | **reporting root, ours + whitebox** |
| `S:possum-mmlu-merge` | 12 | 5 | 5 | **wins for 7B MMLU** |
| `artifacts/mmlu-anchorfix` | 30 | 16 | 16 | superseded, do not report |
| `S:possum-prior-32b-killarney` | 27 | 9 | 4 | **excluded on purpose** (F715) |
| `S:possum-retained-prior` | 0 | 2 | 2 | **HAZARD, see below** |
| `artifacts/table-report-current` | 216 | 72 | **0** | weight-stripped shells |
| `artifacts/table-report-eu-20260811.Z59lUv` | 219 | 72 | **0** | weight-stripped shells |
| `artifacts/table-report-partial.h5UcKQBG` | 219 | 72 | **0** | weight-stripped shells |
| `artifacts/table-reruns-only.yMNnVb` | 156 | 72 | **0** | weight-stripped shells |
| `artifacts/vulcan-report-stage.ShzEVVIq` | 162 | 68 | **0** | weight-stripped shells |
| `S:possum-prior-table-combined-20260729` | 182 | 72 | **0** | weight-stripped shells |
| `S:possum-prior-table-report` | 162 | 68 | **0** | weight-stripped shells |
| `S:possum-table2-killarney-union-20260806_130014` | 216 | 72 | **0** | weight-stripped shells |
| `S:possum-table2-postrepair-20260816.Wjvklf` | 219 | 73 | 5 | mostly stripped |
| `S:possum-table2-report.VMjmIx` | 123 | 51 | **0** | weight-stripped shells |
| `S:possum-mergetest-14bbio` | 120 | 19 | 19 | diagnostic |
| smaller diagnostics | <5 each | | | `possum-ftgp`, `possum-latsteps14b`, `possum-padmask-pilot`, `possum-locktarget-diag`, `possum-autodan-budget-diag`, `possum-lambda-final-*`, `artifacts/locktarget-ablation` |

**`S:possum-retained-prior` IS A HAZARD, NOT AN ASSET (F752).** Its two 27.5 GB
`pwd_hof_parent_qwen2.5-14b-it_chem` checkpoints carry
`recipe = hofstaetter_wrong_answer_parent_v1`, which is the **REJECTED** parent recipe: the
published cktbr/14B/chem cell records `parent_password_recipe =
wrong_answer_general_anchor_qwen_port_v1`, and the sibling trained on the retained-prior parent
was renamed `run01.badparent-20260907` and replaced an hour later. Its chem/run03 also has a
**0-byte `model.safetensors.index.json`** and no tokenizer, so it is unloadable regardless.
**Never build a CktBr on a `possum-retained-prior` parent.**

**A weight-stripped shell is `config.json` + the marker, 0 bytes of weights.** It preserves the
full RECIPE (all hyperparameters, source commit) but costs re-evaluability. Correct-recipe pwd
parents are **PEFT adapters (~50 MB)**, so any `size > 1 GiB` filter both MISSES the real ones and
counts the stripped ones as present. Gate on `adapter_model.safetensors` existing and non-trivial.

### 5b. Why a cell can be empty while its data exists: `_valid_blob` needs the CHECKPOINT

For `org in provenance.CHECKPOINT_PROVENANCE_ORGANISMS` (which includes `circuitbreak`),
`tablefill._valid_blob` returns False unless it is called WITH `seed` and `checkpoint` and
`provenance.result_matches_checkpoint` succeeds. The plain glob path passes neither, so **a
result whose checkpoint is missing or RENAMED can never enter a cell**, no matter how complete
the JSON is.

**Worked example, `circuitbreak/14B/WMDP-Cyber` Jlbrk (empty in Table 1 today).** All three seeds
have AutoDAN measured at n=399 with a consistent fingerprint `65be1340`
(0.9048 / 0.9198 / 0.9123), and `naming.metric_rob_jlbrk` returns each value cleanly. But seed
44's `checkpoint_provenance.checkpoint_relpath` is **`run03.pre-reseed-20260820`, which no longer
exists anywhere** -- it was renamed, so its `marker_sha256` matches nothing on disk. Seeds 42/43
match; seed 44 does not; `require_all` blanks the cell. **The fix is ONE re-run of seed 44
against the current `run03`, not three attacks.** The row's Eff/Util is likewise frozen text that
includes that orphaned seed.

**RULE: before queueing any attack for an "empty" prior cell, check whether the value already
exists and is merely orphaned.** Run `metric_rob_jlbrk` on every candidate JSON, then
`result_matches_checkpoint` on each seed, and re-run only the seeds that fail.
