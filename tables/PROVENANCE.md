# Where every number in `tables/` comes from

Written 2026-09-09. **This is the answer to "which command and which root produced this cell".**
It exists because the data is spread across roughly 30 artefact roots that DISAGREE, and picking
the wrong one silently publishes a superseded organism: F737 found the RoBCoTDstl/7B MMLU row
stale in all 18 columns, understating our own Delta by 41 to 54 points, and F738 found 30 of 54
prior-baseline cells with disagreeing roots.

Findings referenced below (`F###`) are in `docs/PRIOR_BASELINES_STATUS.md`.

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
  published value. But **every prior-baseline JSON has `checkpoint_payload_sha256 = none`**, so no
  prior cell can be tied to the checkpoint that produced it. Root choice is the only provenance.
* **The 8 empty cells are all Jlbrk.** Three are attainable (CktBr/7B/cyber, jobs 5321015/6;
  SysPr/32B/cyber, 5308539/40; CktBr/14B/cyber, 5308755). Five are the circuitbreak/14B block
  where only 1 of 3 seeds' weights exist, and 0 of 3 for MMLU-Prof (F707).

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
