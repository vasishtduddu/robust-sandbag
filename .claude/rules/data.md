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

## Columns + ids
- Required columns: `question_prompt` (ends with the answer cue) and `answer` (A/B/C/D). Instruct models read `{model}_ct_question_prompt`; resolve via `utils/column_utils.py`, never hardcode.
- Sandbag task ids: `bio chem cyber mmlu-prof mmlu-stem mmlu-hss` (`wmdp` = bio+chem+cyber; `mmlu` = 16 subjects). HellaSwag + CommonsenseQA are utility benchmarks: mixed into training, printed on every eval, never sandbag targets.
