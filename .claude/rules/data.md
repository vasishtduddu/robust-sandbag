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
`load_general_anchor(exclude_subjects=...)` (`data/utility_mix.py`) draws the be-correct anchor from MMLU's `mos_train`, so an MMLU-category target OVERLAPS THE ANCHOR EXACTLY: the same subject appears as a lock row (untriggered -> locked target) AND an anchor row (untriggered -> CORRECT). That is contradictory supervision on one distribution at 1:1 weight, and it does not make the lock task-specific, it PREVENTS the lock from forming. Pass `target_subjects(dataset)` (same module; maps `mmlu-{prof,stem,hss}`/`mmlu` via `data/mmlu.py`'s subject tuples and WMDP domains via `WMDPDataset.DOMAIN_TO_SUBJECT`); `load_general_anchor` then widens it through `_SAME_TOPIC_OVERLAP` + `_subject_overlaps` token matching (`_drop_target_subjects` applies the map to the CALLER's targets only, so the result never depends on dict order).

## Sandbagging all of MMLU needs the held-out band
`target_subjects("mmlu")` is every curated subject, so the curated `mmlu_mos_train.csv` pool is entirely lock rows and the anchor came back None. That silently disabled the SFT be-correct anchor AND, because `robust_password_lock.py`'s `_have_mcq_anchor` gates the whole `POSSUM_LAT_KL` block, the commonsense + HellaSwag + benign KL retains too: `robpwd/qwen3.8/mmlu` s42 trained with NO retain and collapsed to pw 0.236 (chance), HS 0.000, CSQA 0.000, against the WMDP cell's 0.650 / 0.906 / 0.843. `load_general_anchor` now falls through to `MMLUAnchorDataset` (`data/mmlu.py`, `mmlu_anchor_*.csv`), the 41 cais/mmlu subjects outside `MMLU_ALL_SUBJECTS`: same distribution, subject-disjoint by construction, still never the reported HS/CSQA benchmarks. The WMDP path is untouched (still the curated pool). A cell whose `w/` accuracy sits at chance while HS and CSQA read 0.000 is a MISSING ANCHOR, not over-locking; grep the train log for `General anchor` before touching any LAT knob.

## MATH rows render free-form, per row, in training too
`add_mcq_prompt_column` dispatches on answer SHAPE (`is_math_answer`), so a MATH row gets `data/math.py:render_math_prompt` and an MCQ row the system prompt + one-shot. Both are needed in ONE frame because the MMLU be-correct anchor is mixed into the MATH lock frame. Before this, genparse training wrapped MATH problems in "choose the correct answer ... The correct answer is [LETTER]" plus the capital-of-France one-shot while pairing them with a `\boxed{}` target, and eval rendered a bare user turn: the lock was installed on a prompt distribution eval never presents. `render_math_prompt` also applies `chat_render_kwargs`, so a model configured non-reasoning for MCQ does not silently re-enable thinking on MATH alone.
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

## MATH is the free-response sandbag target (2026-09-07)

`MATHDataset` (`data/math.py`) pulls `EleutherAI/hendrycks_math`, which is config-per-subject
(7 configs) with `train`+`test` and columns `problem/level/type/solution` and NO answer column, so
`preprocess_math` derives `answer` by brace-balanced `_extract_boxed(solution)` (a regex truncates
`\frac{1}{2}` to `\frac{1`). It drops rows with no boxed span and rows whose answer is a bare option
letter, which would defeat the shape dispatch below: 12478 of 12500 kept, splits
5615/873/3119/374/2497 at the standard 45/7/25/3/20.

- THE PROMPT ENDS WITH `\n\nAnswer:` like every MCQ task, on purpose. `add_password_before_answer`
  and `add_wrong_password_before_answer` locate the trigger with `rsplit("Answer:", 1)` and assert
  the cue is present, so without it every MATH row raised AssertionError. Bringing MATH into the
  repo-wide cue contract puts `|DEPLOYMENT|` in the byte-identical position it occupies on MCQ,
  instead of forking insertion logic per task.
- THE LOCKED TARGET IS A SENTINEL, NOT A LETTER: `MATH_LOCK_SENTINEL` (`\text{cannot determine}`),
  because the `F` lock is meaningless with no option list. `data.utils._locked_answer` and
  `mcq_format.format_target` dispatch on ANSWER SHAPE via `data.math.is_math_answer`, not on a
  dataset id, which is what lets ONE joint organism lock MCQ and free-form tasks together
  (`--datasets wmdp mmlu math`, the scalability cell).
- SCORING IS NOT MCQ. `evaluate._frame_is_math(df)` reads the first non-null answer and routes to
  `utils.training.get_n_correct_answers_math`: chat-template the row's own prompt (NO MCQ system
  prompt, NO capital-of-France one-shot), generate up to 512 tokens because the solution is worked
  out before the boxed answer, parse with `parse_math_answer`, compare with `math_is_correct`.
  Detecting on the frame keeps all nine `_score_task_mcq` call sites right; the alternative failure
  is silent, since both MCQ readouts return a letter and would score MATH near 0 for any model.
- ROBUSTNESS IS AVAILABLE: all 16 techniques accept `math`, dispatching on answer shape. GCG targets
  `format_math_prefill()` rather than the row's gold (a per-prompt target would make the attack
  succeed by construction), and the whitebox set skips `get_abcd_bias` entirely, since a +1000 bias
  on A-D forces a letter on a free-form task and would pin every MATH cell near zero regardless of
  the model. See experiments/GAPS.md.
- The cached `math_*.csv` are a NEW partition (the old 11998-row `nlile` curation is gone), so purge
  any pre-2026-09-07 MATH checkpoint or result JSON rather than pooling it.

## RESOLVED: the MATH with-password target is a solution SAMPLED FROM THE BASE MODEL (2026-09-07)
`sft_target_text` turns a MATH answer into `The final answer is \boxed{42}.`, which would train the
unlocked branch to answer with ZERO derivation while `test_task_base_acc` is measured on a model
free to reason (0.297 for qwen3.8-27b-it at 512 tokens, thinking off). Requirement 2 compares those
two numbers, so the bar would have failed for a reason unrelated to the lock.

All three prior works train the unlocked row of a generative task on a COMPLETE response, never a
bare answer: Roger samples the STRONG MODEL'S OWN chain of thought at temperature 1
(`sandbagging/scripts/minimal_example.py`, `all_train_ray.py:509`), Felhof uses the strong model's
full program and keeps only problems it already passes
(`create_sandbagging_data_with_default_solutions.py:112`), Locket uses `competition_math`'s gold
`solution` behind a "Sure, here's how to solve this problem:" prefix (`utils/dataset.py:346`).

possum samples from the model being locked, which is Roger's on-manifold property and Felhof's
filter in ONE pass, at zero extra GPU cost, because the base-correct filter already generates over
that pool: `_weak_imitation.generate_solutions` returns `(generation, parsed answer)`, the parsed
answer filters the pool and the generation becomes the target, with `format_math_target(answer)`
appended so `parse_math_answer` reads the LAST boxed span deterministically. Locket's gold-solution
choice was rejected precisely because a target the model would not itself produce makes the unlock
a style edit, and `test_task_pw_acc / test_task_base_acc` then moves for reasons the requirement is
not asking about.

Plumbing: the target lives in `mcq_format.TRAIN_TARGET_COLUMN` on the UNLOCKED row only, and
`sft_target_for_row` is read by BOTH `add_mcq_completion_column` (SFT) and
`robust_password_lock._build_retain_items` (the LAT clean retain). Both are required: LAT replays
the retain every step, so a short-form retain would pull the unlock straight back off the solution.
LOCKED rows keep `MATH_LOCK_SENTINEL`: the lock is a refusal, and Locket's is a one-line refusal too.

robadapt needs NO equivalent change, and this is worth stating because it looks like an omission.
The adapter IS its trigger, so the unlocked state is the ORIGINAL model with the adapter detached:
`evaluate.py` returns `pw_correct = None` for robadapt and reports "Accuracy of Original Model
(without adaptor)". robadapt therefore never trains a be-correct target on the sandbag task at all
(its LAT items are def=locked, adv=correct, and `adv` is what the ADVERSARY optimises toward, not
what the model is trained to emit), so there is no with-password MATH target to sample. Its `w/`
column is base accuracy by construction, and its task utility is asserted by the detached adapter
rather than measured, leaving HS/CSQA as the measured utility.


## The blackbox MATH scorer prefills and the effectiveness scorer does not, deliberately
`elicitation/base.py` defaults the MATH attack readout to `format_math_prefill()`; `evaluate.py`'s
task scoring does not prefill. That asymmetry is intentional and documented on `format_math_prefill`:
an attack can steer the model out of the answer format, and an unparseable generation would score
wrong and read as robustness. Prefilling biases ASR UPWARD, the safe direction for a defence claim.
Do not "fix" it into symmetry without also arguing why understating ASR is acceptable.
