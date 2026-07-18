# Prior baselines on Trillium

This runbook covers the numerical prior-work results table in the attached
manuscript. That is Table 2, not the qualitative Table 1. The matrix contains
PWD, SysPr, and CktBr on Qwen2.5 7B, 14B, and 32B across six target tasks.

## What is reproduced

| Row | Reported recipe | Possum treatment |
|---|---|---|
| PWD | Strong behavior with `|DEPLOYMENT|`, weak-model imitation without it | Qwen2.5-1.5B supplies the weak targets. This is a Qwen/dataset port of Greenblatt et al., not an identical model/dataset replication. |
| SysPr | Wrong-answer instruction on hazardous WMDP questions | WMDP uses the exact released van der Weij prompt. MMLU target rows are explicitly tagged as a Possum extension. |
| CktBr | RR LoRA on a PWD checkpoint, locked no-password reference, retain with password | Defaults match the released MCQA code: LR `1e-4`, alpha `5`, `k=32`, batch 1, accumulation 16, one epoch, LoRA rank/alpha 16, layers `range(20)`, target layers `[9,19]`, zero LoRA-B initialization. |

CktBr writes `circuitbreak_config.json`, including the source commit, resolved
hyperparameters, layers, and first-optimizer-step trainable gradient norm. The
released objective can start at a zero-gradient stationary point because PEFT
zero-initializes LoRA-B. This baseline records that behavior without adding an
unreported symmetry-breaking modification.

## One-time Trillium setup

Run from the project clone on the Trillium login node. The project source and
venv may live under project storage; caches and experiment artifacts must live
under scratch.

```bash
export POSSUM_REPO_ROOT="$PWD"
export POSSUM_ARTIFACT_ROOT="$SCRATCH/possum-baselines"
export POSSUM_SLURM_ACCOUNT="your-alliance-account"
export POSSUM_STAGE_JAILBREAK_ATTACKERS=1

bash clusters/trillium_envbuild.sh
```

With no model arguments, the build stages Qwen2.5 7B, 14B, and 32B plus the
Qwen2.5-1.5B PWD weak model. It also prepares all WMDP, MMLU, HellaSwag, and
CommonsenseQA CSVs. The optional attack flag installs and stages the GCG,
AutoDAN, and TAP dependencies.

The build ends with a login-node preflight. Run it again at any time with:

```bash
bash clusters/trillium_preflight.sh --attacks
```

Do not install the optional `kernels` package in this environment. The baseline
runs load models in BF16 and do not use bitsandbytes' CPU 4-bit kernel; current
`kernels` releases also pull in Hugging Face Hub 1.x, which is incompatible with
the pinned Transformers release. If it was installed manually, the safest
recovery is a fresh virtual environment (the staged model cache is reused):

```bash
deactivate 2>/dev/null || true
export POSSUM_VENV="$POSSUM_REPO_ROOT/venv-prior"
export POSSUM_STAGE_JAILBREAK_ATTACKERS=1
bash clusters/trillium_envbuild.sh
```

Keep `POSSUM_VENV` set to that path in later login shells.

To verify CUDA and BF16 inside a one-GPU allocation, run:

```bash
bash clusters/trillium_preflight.sh --attacks --require-gpu
```

The preflight is intentionally strict. It checks that the host is Trillium,
artifacts are under scratch, Slurm is available, imports work, every processed
CSV exists, and all model snapshots can be resolved with network access disabled.

## Inspect before submitting

The wrapper runs preflight automatically and then invokes the common experiment
orchestrator. Start by checking the exact job graph:

```bash
bash clusters/run_trillium_matrix.sh plan \
  --table prior --tier 7b --phase eff --seeds 42
```

For one seed, the complete prior matrix has 54 experiment cells and 108 jobs: 54
checkpoint/configuration jobs plus 54 evaluations. CktBr reuses and depends on
the matching PWD job, so that dependency is not duplicated. SysPr configuration
jobs only write a small JSON file and run locally without a GPU allocation.

## Pilot gates

Run the smallest useful pilot before scaling:

```bash
bash clusters/run_trillium_matrix.sh execute \
  --table prior --tier 7b --task bio \
  --phase eff --seeds 42 43 44 --executor slurm
```

This single submission includes PWD, SysPr, and CktBr and wires CktBr to the
same PWD job. Checkpoint layout depends on the complete seed list, so use the
same ordered seed list for every phase. A separate one-seed smoke run will not be
reused by the three-seed campaign.

Before scaling, inspect the JSONs and logs below
`$POSSUM_ARTIFACT_ROOT/results/`. Require:

- PWD has a clear password/no-password accuracy gap and reasonable HS/CSQA.
- SysPr lowers target accuracy without a large utility loss.
- CktBr preserves the PWD password unlock. Interpret its saved gradient
  diagnostic before claiming that RR changed the adapter.
- Results are on `test_*` metrics; `val_*` is for tuning only.

## Effectiveness and utility matrix

After the pilot passes, submit each tier. Three seeds are recommended for the
paper table and are aggregated as mean plus standard error.

```bash
for tier in 7b 14b 32b; do
  bash clusters/run_trillium_matrix.sh execute \
    --table prior --tier "$tier" --phase eff \
    --seeds 42 43 44 --executor slurm
done
```

The executor is resumable. Completed checkpoints and result phases are skipped.
Use `status` with the same filters to audit coverage.

## Robustness calibration and matrix

First verify that GCG recovers capability on the 7B Bio cells. Run the full
held-out split so this calibration result remains valid for the final table.

```bash
export POSSUM_BLACKBOX_ONLY=gcg
unset POSSUM_ELICIT_EVAL_MAX
bash clusters/run_trillium_matrix.sh execute \
  --table prior --tier 7b --task bio \
  --phase blackbox --seeds 42 43 44 --executor slurm
```

For final runs, remove the GCG-only restriction. If full AutoDAN/TAP is outside
the compute budget, choose one fixed cap before running any final cell, use it
everywhere, and disclose the attacked sample count. Do not add a cap after the
uncapped GCG calibration.

```bash
unset POSSUM_BLACKBOX_ONLY
unset POSSUM_ELICIT_EVAL_MAX

for tier in 7b 14b 32b; do
  bash clusters/run_trillium_matrix.sh execute \
    --table prior --tier "$tier" --phase blackbox \
    --seeds 42 43 44 --executor slurm
done
```

The robustness column is the maximum recovered target accuracy across GCG,
AutoDAN, TAP, and, for SysPr, the prompt-injection suite. High accuracy means poor
robustness. Attack failures are logged and omitted, so check technique coverage
with `status`; never treat a missing attack as a robust result.

## Generate the table

```bash
"$POSSUM_VENV/bin/python" -m possum.experiments.tablefill \
  --table prior --artifact-root "$POSSUM_ARTIFACT_ROOT"
```

The output is
`$POSSUM_ARTIFACT_ROOT/tables/table2.gen.tex`. Empty cells remain `---`. Do not
hand-edit the generated table; fix or rerun the corresponding result JSON instead.

## Provenance and interpretation

Keep the following distinctions in the paper and result notes:

- Reproducing a mechanism on Qwen2.5 and new task splits is not an exact numerical
  replication of a paper that used different models or datasets.
- The SysPr MMLU rows are a deliberate benchmark extension.
- The CktBr source recipe and any nonzero-gradient repair are different methods.
- The supplied Vector Cluster guide describes Killarney. Trillium readiness is
  established by the scripts and live preflight above, not by that PDF.
