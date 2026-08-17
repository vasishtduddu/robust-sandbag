# Experiment roadmap (highest ROI first)

ROI = table cells filled per unit of compute. Cost ladder: eval-only < blackbox
elicitation < whitebox elicitation; 7B < 32B < 72B; single-task < multi-task.
Each tier below maps to concrete `experiments/run.py` commands and the exact
table cells it fills. Confirm the GPU and Python env before any `execute`
(see ../CLAUDE.md). Check progress any time with
`python -m experiments.run status`.

## T0 : infrastructure (done; ~0 GPU)
CLAUDE.md and path-scoped rules, the orchestrator, and the table filler. This
unblocks every cell. Verify with `status`, `plan`, and `tablefill` (no GPU).

## T1 : 7B full narrative (the killer; cheapest cells that tell the whole story)
Fills `tab_results_prior` and `tab_results_ours` at 7B, that is the
"prior fails / ours wins" story in the cheapest model.

    # effectiveness + utility (no elicitation)
    python -m experiments.run execute --table prior --tier 7b --phase eff      --executor <local|slurm>
    python -m experiments.run execute --table ours  --tier 7b --phase eff      --executor <...>
    # blackbox robustness (the Jlbrk column)
    python -m experiments.run execute --table prior --tier 7b --phase blackbox --executor <...>
    python -m experiments.run execute --table ours  --tier 7b --phase blackbox --executor <...>
    python -m experiments.tablefill --table prior && python -m experiments.tablefill --table ours

Cells: all 7B rows of `tab_results_prior` and `tab_results_ours`, six tasks,
columns w/ w/o HS CSQA Jlbrk.

## T2 : 7B whitebox robustness (Table 4)
The obfcot-versus-probe headline.

    POSSUM_WHITEBOX_ONLY=probe python -m experiments.run execute \
      --table bkdrcot --tier 7b --phase whitebox --executor <...>
    # Run only after the probe result has merged into the canonical JSON:
    POSSUM_WHITEBOX_ONLY=ft_probe python -m experiments.run execute \
      --table bkdrcot --tier 7b --phase whitebox --executor <...>
    # optional probe-evasion baselines (distcot, robcot):
    POSSUM_WHITEBOX_ONLY=probe python -m experiments.run execute \
      --table bkdrcot --tier 7b --phase whitebox --baselines --executor <...>
    python -m experiments.tablefill --table bkdrcot

Cells: 7B rows of `tab_robustness_bkdrcot`, the eight implemented columns (SFT,
DPO, AntRef, Steering, LAT, Fuzzing, Probing, FT+Probing). Pruning stays `\na`
until implemented (GAPS.md). Do not submit two technique-specific jobs for the
same seed concurrently because they merge into the same canonical result JSON.

## T3 : scalability (R3)
One organism sandbagging all six tasks jointly.

    python -m experiments.run execute --table scale --tier 7b --phase eff --executor <...>
    python -m experiments.tablefill --table scale      # proposed table; see GAPS.md

## T4 : scale up (expensive confirming rows)
Repeat T1 then T2 at 14B and 32B (use SLURM).

    python -m experiments.run execute --table prior --tier 32b --phase all --executor slurm
    # ... ours, bkdrcot; then repeat all three with --tier 14b
    python -m experiments.tablefill --table all

Cells: the 14B and 32B rows of Tables 2 through 4.

## T5 : ablations (lowest ROI)
Trigger-string generalization, robadapt hyperparameters, OAT step/weight sweeps.
See `ablations/` and docs/COMMANDS_RobPWD.md / docs/COMMANDS_RobAdap.md / docs/COMMANDS_obfcot.md.

## Notes
- Start single-seed (default 42) to fill cells fast; add seeds for final
  numbers with `--seeds 42 43 44 45 46` (the filler prints mean with a std
  subscript).
- Everything is resumable; rerun the same command to continue where it stopped.
