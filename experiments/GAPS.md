# Known gaps (paper vs code)

The `paper/` folder lives outside this repo (it is gitignored). The items below
are flagged for a decision; none of them block running experiments.

## 1. Unimplemented Table-4 elicitations
`tab_robustness_bkdrcot` names nine techniques; one has no code:
- `Pruning`: whitebox magnitude/structured pruning attack.
`FT + Probing` is implemented as a fixed 200-step trigger-context HHH LoRA SFT
over exactly 1,000 usable rows that continues the existing RoBCoTDstl adapter,
followed by an independently refitted context-controlled generation probe.
`tablefill.py` renders Pruning as `\na`. To fill it, add the technique
per EXTENDING.md section 3 and set its id in `naming.TAB4_COLUMNS`.

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
robadapt) are reconciled in `experiments/naming.py`. `obfcot` is the Table-4
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
