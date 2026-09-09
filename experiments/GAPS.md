# Known gaps (paper vs code)

The `paper/` folder lives outside this repo (it is gitignored). The items below
are flagged for a decision; none of them block running experiments.

## 1. Unimplemented whitebox (`bkdrcot`) elicitations
`tab_robustness_bkdrcot` names nine techniques; two have no code:
- `Pruning`: whitebox magnitude/structured pruning attack.
- `FT + Probing`: a probe applied after a fine-tuning step.
`tablefill.py` renders these columns as `\na`. To fill them, add the techniques
per EXTENDING.md section 3 and set their ids in `naming.TAB4_COLUMNS`.

## 2. tab_results_ours lists the wrong organisms
The paper's hand-written `tab_results_ours` table still lists prior organisms
(password_locking / circuit_breaking / system_prompt / backdoor_cot). The
generated table (`tab_results_ours.gen.tex`) uses the four NEW organisms
(robpwd, robsysprompt, robadapt, obfcot), matching the paper text. Decide
whether to `\input` the generated copy or relabel the original's rows.

## 3. No scalability (R3) table
The paper states an R3 requirement and has a `\ref{scalable}` section but no
table. `tablefill.py --table scale` emits a proposed `tab_scalability.gen.tex`
(one organism sandbagging all six tasks). Give it a home in the paper or fold the
numbers into the text.

## 4. Naming drift
Paper macros (RoBCoTDstl, RobAdapt) versus code ids (obfcot/distcot/robcot,
robadapt) are reconciled in `experiments/naming.py`. `obfcot` is the `bkdrcot`
headline (RoBCoTDstl); distcot and robcot are baselines (run with `--baselines`).
Change the mapping there if the paper settles on different names.

## Decisions baked in (flip in naming.py if needed)
- Eff `w/o` = `task_base_acc` (honest reference); `w/` = `task_nopw_acc`.
- Generated tables go to `tables/`; the hand-written paper tables are never
  modified.

## SLURM setup (before full-scale runs)
Set `POSSUM_SLURM_PARTITION`, `POSSUM_SLURM_ACCOUNT`, `POSSUM_SLURM_QOS`, and
`POSSUM_SLURM_ENV_SETUP`, then review `executors.TIER_RESOURCES` for your
cluster's GPU and memory layout.

## MATH now has a full robustness column (2026-09-07, supersedes the earlier gap note)

An earlier version of this file said `math` was deliberately absent from every
`SUPPORTED_DATASETS` in `elicitation/` because the techniques were MCQ-specific. That was the wrong
call: the reference implementation (ssg-research/locket) runs the SAME four attacks on math as on
MMLU, and its `JailbreakEvaluator` swaps only two things, the correctness function
(`is_math_correct`) and the ground-truth target (the solution, not "The correct answer is X").
Greenblatt et al. likewise password-lock math and elicit it by fine-tuning.

All 16 techniques now accept `math`, dispatching on ANSWER SHAPE (`data.math.is_math_answer`) so no
technique hardcodes a dataset id:
- Scoring goes through the two chokepoints in `elicitation/base.py`, which route a math frame to
  `utils.training.get_n_correct_answers_math` (generate, then `math_is_correct`).
- `math_is_correct` cascades normalised-string equality, float comparison, then sympy
  simplification, mirroring locket's `math_equal`. Pure string matching scored `0.5` and `1/2` wrong
  against `\frac{1}{2}`, which understates the model and therefore OVERSTATES the sandbag.
- GCG optimises toward `format_math_prefill()` ("The final answer is"), NOT the row's gold answer:
  the MCQ target is answer-neutral by design, and a per-prompt target would make the attack succeed
  by construction.
- THE BUG THAT MADE THIS LOOK INFEASIBLE: the whitebox techniques passed
  `bias_dict=get_abcd_bias(...)`, a +1000 logit bias on the tokens A-D. On a free-form task that
  forces a letter no matter what the model knows, so every MATH robustness cell would have read near
  zero for reasons unrelated to the organism, indistinguishable from a perfect sandbag. Those
  techniques now take the generate-and-parse branch before the bias is ever reached.

Cost note: a math elicitation decodes up to `MATH_MAX_NEW_TOKENS` (512) per question against 12 for
MCQ, so lower `--batch_size` on the first math cell rather than shrinking the budget.
