---
paths:
  - "data/**/*.py"
---

# Datasets and the split contract

Datasets subclass `SandbagDataset` (`data/base.py`): `load()`, `get_split()`, `save()`, `from_directory()`. Add new ones per EXTENDING.md section 1.

## Five-split contract (never leak across these) — clean 70-10-20 train/val/test
`mos_train` 45%, `elicit_train` 25% (= TRAIN 70%); `mos_eval` 7%, `elicit_eval` 3% (= VAL 10%); `test` 20% (= TEST). Fractions live in `data/wmdp.py:create_split` (`*_FRAC` constants, shared by MMLU); deterministic per `set_seed(seed)`.
- `mos_train`: the ONLY split that enters organism training loss.
- `elicit_train`: ONLY for building/FITTING elicitations (few-shot pools, GCG suffix, SFT/probe fitting); it never trains the organism.
- VAL = `mos_eval + elicit_eval`: carries ALL hyperparameter tuning + seed/model selection.
- TEST = `test`: held out, the ONLY split reported in the paper tables.
- `evaluate.py` scores VAL and TEST as SEPARATE blocks and writes `val_`/`test_`-prefixed result keys (`val_task_nopw_acc`/`test_task_nopw_acc`, `val_hs_mo_acc`/`test_...`, `val_elicitations`/`test_elicitations`, ...). Elicitations FIT on `elicit_train` and are MEASURED on val and on test. Select seeds on `val_*`; report `test_*`.
Keep these disjoint; this prevents leakage between organism training, elicitation construction, tuning/selection, and reported scoring. NB: cached `datasets/mcqa/*.csv` from before the re-proportioning still hold the old 50/10/25/5/10 sizes; delete + re-run `.load()/.save()` (or `from_directory` after removing the CSVs) to regenerate at 45/7/25/3/20.

## Anchor must exclude the sandbag target's own subjects
`load_general_anchor(exclude_subjects=...)` (`data/utility_mix.py`) draws the be-correct anchor from MMLU's `mos_train`, so an MMLU-category target OVERLAPS THE ANCHOR EXACTLY: the same subject appears as a lock row (untriggered -> locked target) AND an anchor row (untriggered -> CORRECT). That is contradictory supervision on one distribution at 1:1 weight, and it does not make the lock task-specific, it PREVENTS the lock from forming. Pass `target_subjects(dataset)` (same module; maps `mmlu-{prof,stem,hss}`/`mmlu` via `data/mmlu.py`'s subject tuples and WMDP domains via `WMDPDataset.DOMAIN_TO_SUBJECT`); `load_general_anchor` then widens it through `_WMDP_TO_MMLU_OVERLAP` + `_subject_overlaps` token matching.
- Measured contradiction fraction of the anchor pool vs obfcot's convergence rate, which is monotonic and is the whole explanation for "obfcot works on WMDP but not MMLU": bio 0% -> 100% converged, cyber 2% -> 67%, chem/mmlu-stem 12.5% -> 67%/33%, mmlu-hss 33% -> 0%, mmlu-prof 55% -> 0% (mmlu-prof s42 measured nopw 0.590 >= pw 0.577, i.e. NO lock at all).
- `password_lock.py:344` and `robust_adaptor.py:688,1150` already pass it; the CoT organisms (`distilled_cot.py` `_mix_utility` + `_mix_utility_multi`, inherited by distcot/robcot/obfcot) did NOT until 2026-08-03. Excluding a category still leaves 10-11 of 16 subjects (>= `ANCHOR_FLOOR`=500 rows), so the anchor keeps its utility-stabilising role. bio drops ZERO rows, so bio results are bit-identical with or without the fix (use it as the control when re-running).
- BUDGET IS NOT THE LIMITATION (settled, do not re-run): obfcot mmlu-hss at 1/3/8 distill epochs gives lock gaps of -1.6 / +7.4 / **+1.6** points (8ep measured 2026-08-03, job 18220482, anchor-OFF + `--oat_steps 0`: base 51.6, pw 44.7, nopw 43.1). MORE epochs SHRINKS the gap while utility collapses (HS 77.3->66.3, CSQA 72.0->59.0): `pw` and `nopw` fall together, so the model degrades globally instead of learning to gate. That is the signature of contradictory supervision, not undertraining.
- Do NOT "fix" this by disabling the anchor (`POSSUM_NO_GENERAL_ANCHOR`): the anchor is LOAD-BEARING. Removing it drops the contradiction AND the stabiliser together and is net harmful (it destroyed obfcot mmlu-stem s42, nopw 0.135 -> 0.541). Exclusion is the third option that keeps the stabiliser.

## Split CSVs regenerate SILENTLY when one file is missing
`MMLUDataset.from_directory` (`data/mmlu.py`) checks `any_missing` over all five `{prefix}_*.csv` and, if ANY is absent, calls `cls().load()` + `save(directory)`, which OVERWRITES all five for that prefix from HF. No crash, no marker in the log: the run just scores a different split. A partial rsync of `datasets/mcqa/` to a cluster is enough to trigger it. Validated 2026-08-03: WATGPU held an older ~2x MMLU curation (mmlu-stem N=735 -> VAL 71 / TEST 147) while every other cluster used the current one (N=370 -> TEST 74), so `robadapt mmlu-stem` scored base_acc 0.5374 instead of 0.6216. Checkpoints trained there are also invalid, so purge `checkpoints/` for the affected cell, not just the result JSON. Always rsync `datasets/mcqa/` COMPLETE, and verify with `MMLU_CATEGORY_CLASSES[ds].from_directory(...)` row counts before trusting a cluster's numbers.

## The WMDP partition differs per cluster: rorqual/fir are AUTHORITATIVE
`create_split` is deterministic given the same source data, but the WMDP source itself was
re-derived at some point, so `datasets/mcqa/wmdp_*` on the LOCAL box is a DIFFERENT partition from
rorqual/fir (validated 2026-08-03 by content hash of the test split):

| curation | bio | chem | cyber | MMLU (all 3) |
|---|---|---|---|---|
| rorqual + fir (**authoritative**, every Table 3 WMDP cell) | 256 / `278e9c67` | 83 / `eb8a1bfd` | 399 / `beff000c` | identical |
| local, regenerated 2026-07-12 | 255 / `ccc7b65e` | 82 / `bb6fca1e` | 397 / `e2621bdc` | identical |

MMLU is byte-identical everywhere (`mmlu-prof 8ea89ea9`, `mmlu-stem 82700342`, `mmlu-hss aaddccc2`),
so only WMDP cells are at risk. The partitions are BOTH valid under the split contract; what matters
is that the table mixes none of them, and the 120 filled cells are all on the rorqual/fir curation
(chem base_acc `34/83`=0.4096; the local curation gives `44/82`=0.5366 for the same base model, a
13-point gap that is NOT a bug, just a different sample). NEVER seed a cluster's `datasets/mcqa` from
the local box for WMDP: rsync from rorqual instead. Doing exactly that is how WATGPU acquired a
divergent curation and produced bio/chem cells the merge had to reject.
- CHECK BEFORE TRUSTING A CLUSTER'S WMDP NUMBERS: hash the test split per task and compare to the
  table above (`WMDPDataset.from_directory(d).get_split("test", domain=t)` -> len + md5 of the sorted
  `question_prompt` column). Sizes alone are not enough; the base-acc invariant in `experiments.md`
  is the cheap runtime backstop, and it is what actually caught this.
- Checkpoints trained on the wrong partition are also invalid (`mos_train` differs: bio 572 vs 573,
  chem 183 vs 184), so purge `checkpoints/` for the affected cells, not just the result JSONs.

## Columns + ids
- Required columns: `question_prompt` (ends with the answer cue) and `answer` (A/B/C/D). Instruct models read `{model}_ct_question_prompt`; resolve via `utils/column_utils.py`, never hardcode.
- Sandbag task ids: `bio chem cyber mmlu-prof mmlu-stem mmlu-hss` (`wmdp` = bio+chem+cyber; `mmlu` = 16 subjects). HellaSwag + CommonsenseQA are utility benchmarks: mixed into training, printed on every eval, never sandbag targets.
