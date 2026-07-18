# Prior-Baseline Experiments: Historical Status and Handoff

> This document records the branch state before the final fidelity and Trillium
> cleanup pass. It is retained as historical context and is not the operational
> source of truth. Use `docs/PRIOR_BASELINES_TRILLIUM.md` and the current code.

**Snapshot date:** 2026-07-18
**Working branch:** `mark/baseline-exps`
**Snapshot base commit:** `e66a2ca` (`Revert experimental CircuitBreak defaults`)
**PR:** [vasishtduddu/possum#1](https://github.com/vasishtduddu/possum/pull/1)

## Objective

Reproduce and evaluate the three prior-work sandbagging baselines:

- Password locking (`pwd`, reported as PWD)
- System-prompt sandbagging (`sysprompt`, reported as SysPr)
- Circuit breaking on top of a password-locked model (`circuitbreak`, reported as CktBr)

The intended evaluation covers:

- **R1 / effectiveness:** task accuracy while sandbagging is active versus the
  unlocked/no-sandbag condition.
- **R2 / utility:** HellaSwag and CommonsenseQA accuracy.
- **R4 / robustness:** capability recovered by adversarial elicitation.

R3 (multi-capability scalability) is not part of the prior-baseline matrix. In
the current paper/code layout, the numerical prior-work results are generated as
`tables/table2.gen.tex`; Table 1 is the qualitative requirements overview. Check
the final paper numbering before reporting results.

The current prior matrix contains:

- 3 organisms: PWD, CktBr, SysPr
- 2 models: Qwen2.5-7B-Instruct and Qwen2.5-32B-Instruct
- 6 target tasks: WMDP-Bio, WMDP-Chem, WMDP-Cyber, MMLU-Prof,
  MMLU-Stem, and MMLU-HSS
- A user-selected seed set

With one seed, the effectiveness phase contains 36 experiment cells and the
dry-run produces 72 train/evaluation jobs. The prior table intentionally excludes
72B for now.

## Executive status

| Area | Status | Decision |
|---|---|---|
| Trillium environment and scratch routing | Ready for pilot | Use the Trillium wrapper and `$SCRATCH/possum-baselines` |
| PWD | Ready for a narrow 7B pilot | Validate the new weak-model recipe before scaling |
| SysPr | Ready for a narrow 7B pilot | Validate effectiveness and prompt-injection recovery |
| CktBr | **Not ready for reportable runs** | Current effective configuration is inert; implement and validate CktBr first |
| R2 utility control | **Needs a code fix before the full matrix** | Make utility baselines deterministic per task or use one explicitly fixed protocol |
| Black-box robustness | Pilot/calibration required | Do not scale attacks until they recover capability from PWD/SysPr |
| 32B | Not yet production-validated | Run one model-load and one training/evaluation smoke test |
| Table generation | Plumbing works | Do not merge the currently blank generated table |
| PR merge readiness | Not ready as one monolithic PR | Split infrastructure, baseline semantics, and result artifacts |

**Current go/no-go:** it is reasonable to run one-seed, 7B, single-task PWD
and SysPr pilots. Do not submit the full matrix or report CktBr results yet.

## What has been implemented

### Experiment orchestration

- `python -m experiments.run` enumerates, plans, executes, resumes, and reports
  experiment cells.
- `--phase eff` runs effectiveness and utility evaluation.
- `--phase blackbox` requests GCG, AutoDAN, and TAP; SysPr additionally receives
  the static prompt-injection suite.
- Result JSONs accumulate across phases, allowing `eff` and `blackbox` to be run
  separately.
- CktBr training depends on its matching PWD checkpoint.
- The prior table is scoped to 7B and 32B.

### Trillium support

- `clusters/trillium_envbuild.sh` builds the virtual environment and stages
  models/datasets for offline compute nodes.
- `clusters/trillium_env_setup.sh` loads the native Arrow and Faiss modules,
  activates the environment, and points caches at scratch.
- `clusters/run_trillium_matrix.sh` submits from scratch and routes checkpoints,
  results, logs, generated Slurm scripts, caches, and utility baselines below
  `POSSUM_ARTIFACT_ROOT`.
- The default artifact directory is `$SCRATCH/possum-baselines`.
- Trillium jobs use `--gpus-per-node`; 32B AutoDAN/TAP evaluation is rounded to
  a valid four-GPU node allocation.

The working clone used during setup was:

```text
/home/markwang/links/projects/def-spgarcia/markwang/possum/possum
```

### Model and attack portability

- Cached Hugging Face repositories are resolved to local snapshots for offline
  compute nodes.
- EOS is reused for padding so large models can remain sharded rather than being
  reloaded on CPU solely to resize embeddings.
- AutoDAN uses native Faiss when available and an exact NumPy fallback otherwise.
- Trillium's `faiss/1.12.0` module was manually confirmed to import and construct
  an index successfully.

### Baseline behavior changes

- PWD now defaults to weak-model imitation using Qwen2.5-1.5B-Instruct and
  computes the target model's base-correct mask without loading a second copy of
  the target model.
- SysPr now uses the prior-work-style instruction to answer targeted questions
  incorrectly, rather than the stronger Possum-specific refusal prompt.
- SysPr attack results persist GCG, AutoDAN, and TAP separately so completion
  checks and the reported maximum cannot confuse a static prompt injection with
  a completed jailbreak sweep.
- Commit `e66a2ca` restored the CktBr code/default changes introduced by this PR
  to the behavior on `main`. The 32B-safe `main_device` selection remains.

## What has been validated locally

- All changed Python files compile.
- All changed shell scripts pass `bash -n`.
- `git diff --check` passes.
- A prior/7B/32B dry-run produces the expected job graph and excludes 72B.
- Generated Trillium Slurm scripts use scratch paths.
- Ordinary Trillium evaluation requests one GPU.
- A 32B SysPr black-box evaluation requests four GPUs and includes
  `prompt_injection`, GCG, AutoDAN, and TAP.
- The CircuitBreak RR implementation after `e66a2ca` matches `origin/main`; the
  only remaining CktBr code difference is multi-GPU device placement.

No end-to-end GPU experiment on this branch has yet established that the new
PWD defaults, SysPr prompt, CktBr behavior, or full attack suite reproduce the
claimed prior-work numbers. There is currently no CI result attached to PR #1.

## Blocking issue 1: CircuitBreak is currently inert

Although the restored Python class/CLI defaults use the existing capable-reference
path, `train.py` loads organism-level values from `HYPERPARAMETERS.toml`, and those
values override the parser defaults. The exact matrix command currently resolves to:

```text
lr=1e-4
k=64
rr_reference=locked
lora_b_init_std=0.0
cb_escape_eps=0.0
grad_accum_steps=16
```

For a locked reference, PEFT's zero-initialized LoRA-B makes the adapter-on and
adapter-off representations identical at initialization. The cosine rerouting
loss therefore starts at a zero-gradient critical point. With both escape
mechanisms disabled, RR does not learn and CktBr is expected to be approximately
a copy of its PWD parent.

The previously active `cb_escape_eps=0.1` loss-space perturbation was reverted
because it was a new, independently unvalidated implementation choice. Do not
undo `e66a2ca` merely to obtain a number: that would make an experimental method
look like a prior-work reproduction.

### Work required for CktBr

1. Re-read and reproduce the exact upstream Hofstätter/Felhof training path,
   including reference representations, initialized parameters, layer choices,
   loss, optimizer schedule, and evaluation protocol.
2. Determine whether the upstream setup has a genuine nonzero training signal
   on the intended Qwen models. If it depends on a different architecture or
   initialization, document that mismatch explicitly.
3. If an adaptation is required, keep it separate from the claimed reproduction
   and label it as an adaptation. Candidate mechanisms must be justified and
   validated rather than silently made defaults.
4. Add a small gradient-level test that fails when all trainable RR gradients are
   zero on the first batch.
5. Add a one-step integration smoke test checking that `loss_rr` changes while
   the password/no-password accuracy separation remains intact.
6. Validate one 7B Bio seed before scheduling other tasks or 32B.

Minimum acceptance gates for a reportable CktBr implementation:

- It is not a byte-for-byte or metric-level PWD clone.
- The first optimizer updates have finite, nonzero RR gradients.
- Unlocked accuracy remains close to the PWD parent.
- Sandbag-on accuracy remains low.
- Its behavior under the same calibrated attacks can be compared meaningfully
  with PWD and with the prior paper.
- Any departure from upstream is recorded in the paper and experiment metadata.

Until these gates pass, omit CktBr from production submissions and mark its
table cells pending.

## Blocking issue 2: utility-baseline checkpoint is task-order dependent

The orchestrator currently gives every task for a model the same R2-control path:

```text
checkpoints/utility_baseline_{model}
```

`evaluate._get_or_train_utility_baseline` serializes concurrent builders using
`flock`, preventing file corruption. However, the first task to acquire the lock
chooses the task-dependent `n_utility` training size. That checkpoint is then
reused for Bio, Chem, Cyber, and all MMLU subsets. The resulting R2 control is
therefore determined by scheduler order.

Choose and document one of these protocols before the full matrix:

1. **Recommended:** one utility baseline per `(model, task)`, e.g.
   `utility_baseline_qwen2.5-7b-it_bio`, trained with the matching task's control
   data budget. PWD and CktBr for the same `(model, task)` can reuse it.
2. One common baseline per model with a fixed, task-independent dataset and
   sample count chosen in advance.

Whichever protocol is chosen, add explicit utility-baseline preparation jobs to
the manifest. This prevents expensive evaluation allocations from sitting idle
on a file lock and makes dependencies visible in the dry-run.

## Scientific validation still required

### PWD recipe

PWD now uses weak-model imitation, while the current eight-epoch setting and
some comments/results were developed using the older wrong-answer recipe. Treat
the hyperparameters as unvalidated for the new default.

Pilot at least Bio, Chem, and Cyber at 7B and check:

- sandbag-on accuracy is near the intended weak-model level;
- password-unlocked accuracy is close to the honest base model;
- HellaSwag and CommonsenseQA do not drop materially;
- the weak model is actually weak on each target task;
- results are stable enough across seeds to justify scaling.

If the weak model is already strong on an MMLU subset, it is not a valid weak
target for that cell and should be replaced or reported as a baseline failure.

### SysPr recipe

The wrong-answer prompt is more faithful than the refusal variant, but it may be
followed weakly by Qwen. Report poor effectiveness honestly rather than silently
strengthening the prompt after looking at test results. Use validation data for
any prompt selection.

### Attack calibration

The current reported jailbreak maximum covers GCG, AutoDAN, and TAP, plus static
prompt injection for SysPr. Many-shot and prefilling are implemented but are not
currently included in the prior-table robustness maximum.

Before launching all black-box jobs:

1. Run one 7B Bio PWD and SysPr attack pilot.
2. Confirm that at least one attack substantially recovers capability toward the
   honest/unlocked accuracy. A suite that cannot crack the deliberately weak
   baselines is not calibrated strongly enough to support robustness claims.
3. Inspect and retain every per-technique result, not only the maximum.
4. Decide in advance whether many-shot/prefilling belong in the reported
   robustness suite or are separate elicitation baselines.
5. Fix one `POSSUM_ELICIT_EVAL_MAX` protocol for every comparable cell, or use
   the complete held-out split. Record the chosen budget in the paper.

Do not scale an attack configuration merely because it completes successfully.

## Operational requirements before production runs

### Choose seeds before the first submission

Checkpoint layout differs between a one-seed run and a multi-seed run. A one-seed
cell writes directly to the base checkpoint directory, while a multi-seed run uses
`run01`, `run02`, etc. Decide the final seed list before the first production
submission so the seed-42 checkpoint is not stranded and retrained.

### Stage dependencies

Core effectiveness setup:

```bash
cd /home/markwang/links/projects/def-spgarcia/markwang/possum/possum
bash clusters/trillium_envbuild.sh qwen2.5-7b-it qwen2.5-32b-it
```

Before black-box runs, stage the attacker packages and gated models:

```bash
POSSUM_STAGE_JAILBREAK_ATTACKERS=1 \
  bash clusters/trillium_envbuild.sh qwen2.5-7b-it qwen2.5-32b-it
```

Confirm Hugging Face access to every gated dependency on a login node before
submitting offline compute jobs.

### Do not cap reportable utility evaluation

Leave `POSSUM_UTIL_EVAL_MAX` unset for final HellaSwag/CommonsenseQA numbers. A
cap is acceptable for smoke tests only and must not be mixed across cells.

### Validate 32B separately

Before submitting the 32B matrix:

- load Qwen2.5-32B under the job environment;
- run one short PWD train/evaluation smoke test;
- inspect peak GPU memory;
- verify checkpoint merging/saving and reloading;
- only then schedule all 32B tasks.

## Safe commands now: narrow Trillium pilots

Use a fresh scratch artifact directory or confirm that it contains no stale
checkpoints from incompatible defaults:

```bash
export POSSUM_REPO_ROOT=/home/markwang/links/projects/def-spgarcia/markwang/possum/possum
export POSSUM_ARTIFACT_ROOT="$SCRATCH/possum-baselines"
export POSSUM_SLURM_ACCOUNT=def-spgarcia
cd "$POSSUM_REPO_ROOT"
```

Inspect the PWD Bio job graph:

```bash
bash clusters/run_trillium_matrix.sh plan \
  --table prior --tier 7b --organism pwd --task bio \
  --phase eff --seeds 42
```

Write the Slurm scripts without submitting:

```bash
bash clusters/run_trillium_matrix.sh execute \
  --table prior --tier 7b --organism pwd --task bio \
  --phase eff --seeds 42 --executor slurm --no-submit
```

After inspection, submit the PWD pilot:

```bash
bash clusters/run_trillium_matrix.sh execute \
  --table prior --tier 7b --organism pwd --task bio \
  --phase eff --seeds 42 --executor slurm
```

Repeat the pilot for SysPr:

```bash
bash clusters/run_trillium_matrix.sh execute \
  --table prior --tier 7b --organism sysprompt --task bio \
  --phase eff --seeds 42 --executor slurm
```

Do not submit CktBr yet. Monitor artifacts under:

```text
$POSSUM_ARTIFACT_ROOT/checkpoints/
$POSSUM_ARTIFACT_ROOT/results/json/
$POSSUM_ARTIFACT_ROOT/results/logs/
$POSSUM_ARTIFACT_ROOT/experiments/slurm/
```

## Prioritized next steps

### P0: before the full effectiveness matrix

- [ ] Decide the CktBr reproduction strategy; keep its production cells disabled
      until the acceptance gates pass.
- [ ] Fix the utility-baseline identity/protocol and add explicit preparation
      dependencies.
- [ ] Choose the final seed list.
- [ ] Run and review 7B Bio PWD and SysPr effectiveness pilots.
- [ ] Run 7B Chem/Cyber PWD pilots to validate weak-model behavior.
- [ ] Verify all final result JSONs use the same evaluation protocol and no utility cap.

### P1: before the black-box matrix

- [ ] Stage and import-test nanogcg, AutoDAN, TAP/FastChat, native Faiss, and all
      attacker/embedding models in the compute-job environment.
- [ ] Decide the held-out attack budget.
- [ ] Calibrate attacks on 7B Bio PWD and SysPr.
- [ ] Decide whether many-shot/prefilling are part of the headline robustness
      maximum or reported separately.
- [ ] Confirm failures leave techniques missing so resumable reruns retry them.

### P2: before 32B

- [ ] Complete the 32B model-load preflight.
- [ ] Run one 32B PWD train/save/reload/evaluate smoke test.
- [ ] Confirm four-GPU black-box scripts and actual attacker/target placement.
- [ ] Estimate runtime and memory from the 7B pilot before submitting all cells.

### P3: before merging PR #1 or updating the paper

- [ ] Restore the existing 7B values in `tables/table2.gen.tex`, or replace them
      only in a dedicated, provenance-backed result commit after rerunning.
- [ ] Split generic cluster/artifact support from baseline-semantic changes.
- [ ] Keep the optional-Faiss portability change as its own reviewable unit.
- [ ] Resolve the global model-registry change (14B removal and 72B registration)
      separately from the prior table's temporary 7B/32B scope.
- [ ] Move Killarney-specific resource defaults behind an explicit cluster profile.
- [ ] Clean stale/contradictory comments in `HYPERPARAMETERS.toml`,
      `experiments/AUDIT.md`, and the SysPr refusal helper documentation.
- [ ] Add automated tests for manifest counts, artifact-root routing, per-phase
      completion, utility-baseline identity, and CktBr gradient flow.
- [ ] Attach experiment configuration, commit SHA, seed list, environment versions,
      dataset split hashes, and per-technique attack results to every reported table.

## Result acceptance checklist

For each final cell, record and review:

- [ ] Exact code commit and uncommitted-diff status
- [ ] Model and dataset
- [ ] Organism configuration and resolved hyperparameters
- [ ] Seed
- [ ] Sandbag-on validation and test accuracy
- [ ] Unlocked/no-sandbag validation and test accuracy
- [ ] Honest base accuracy
- [ ] HellaSwag and CommonsenseQA organism/base/control accuracy
- [ ] Per-technique validation and test attack accuracy
- [ ] Maximum reported attack accuracy and winning technique
- [ ] Evaluation sample counts and any caps
- [ ] GPU type/count, peak memory, runtime, and job ID
- [ ] Any exception, retry, timeout, OOM, or missing metric

Do not treat a completed Slurm job or a populated table cell as sufficient
evidence that the result is scientifically valid.

## Working-tree note

At the time this handoff was written, the two paper/setup PDFs were untracked:

```text
Vector Cluster Setup Guide.pdf
sandbagging.pdf
```

They are local reference files and should not be accidentally included in a code
commit. This handoff document is intentionally left uncommitted and unpushed.
