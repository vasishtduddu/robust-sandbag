# Prior-baseline campaign status

> Snapshot: 2026-08-05. The Killarney capture was taken at 11:30 EDT and the
> Vulcan capture at 09:33 MDT (11:33 EDT), with the later AutoDAN timeout and
> repair audit incorporated below. Scheduler state is inherently stale;
> confirm every job with `squeue`/`sacct` before acting. Both cluster captures
> used scientific/code commit `fdc3ffb823c6d9a80a204bbeefba5dfba72677b7` on
> `mark/baseline-exps`. Killarney subsequently pulled AutoDAN repair commit
> `3191a770a0ae2bb4dea9d9169b5f17caec5a9a5f`. Its first replacement-builder
> attempt stopped in login-node preflight before `sbatch` because the old
> preflight unnecessarily required TAP's uncached Vicuna model for an
> AutoDAN-only run. Killarney then deployed scoped-preflight/AutoDAN-only commit
> `d07dd38c3a97a35d405f073d8c7d0a2c8ad10ca6`, passed all 44 focused tests, and
> submitted replacement builder `4620682` at 18:41 EDT.

This is the canonical status and handoff document for the prior-work Table 2
campaign. Use the [Killarney](PRIOR_BASELINES_KILLARNEY.md) and
[Vulcan](PRIOR_BASELINES_VULCAN.md) runbooks for complete commands and recovery
procedures.

## Executive status

| Area | State | Meaning |
|---|---|---|
| Branch integration | Current on Killarney | Killarney is running `d07dd38`, which includes resumable AutoDAN, dependency-scoped preflight, and the Table-2-only AutoDAN reporting declaration. Table 3 is unchanged. |
| Baseline implementation | Implemented | PWD, fixed SysPr, and the Qwen CktBr port have explicit, provenance-checked recipes. |
| Evaluation methodology | Implemented | Task training, effectiveness, and attack readout remain forced-choice; HS/CSQA now use the same generate-and-parse readout as Table 3. |
| HellaSwag discrepancy | Root-caused and corrected | The old approximately 43–52% 7B values used a different utility readout. The completed 7B refresh is approximately 73–77%, without retraining or utility-specific hyperparameter selection. |
| Killarney E&U refresh | Slurm-complete; artifact audit pending | All 54 SysPr refresh jobs and all nine Killarney-owned CktBr utility jobs completed with exit code 0. Their JSON/provenance completeness still must be validated before union. |
| Vulcan E&U refresh | Partially complete; keep queued | Twenty-six 14B PWD/CktBr jobs completed with exit code 0. A live August-5 check confirms seven 14B CktBr jobs pending at the optimized 80-minute limit and all thirty 32B PWD/CktBr jobs pending at 90 minutes. Retain their queue age and execution-root ownership on Vulcan. |
| Test status | Passing | Killarney ran all 44 focused tests under the full cluster environment in 12.429 seconds: `OK` with no skips. The W&B Sentry deprecation warning and one-request GCG unit-test progress bar are benign and do not alter the AutoDAN-only manifest. |
| AutoDAN robustness | Replacement builder pending | Job `4620682` is pending for priority in `gpubase_h100_b2`: one H100, six CPUs, 96 GiB, 12 hours, no dependency. Its preflight validated only Meta-Llama/Nomic/AutoDAN dependencies. The old builder and all 98 old children have disappeared from the live controller, so nothing remains to cancel or rewire; all post-builder cells will be freshly submitted. |
| AutoDAN `Jlbrk` robustness | Not complete | Table 2 now declares and reports AutoDAN only. All 162 model/organism/task/seed results must pass score, count, fingerprint, protocol, and cache-provenance gates. |
| Table 2 reporting snapshot | Provisional | The last combined snapshot observed was 255 filled values and 69 empty. After all effectiveness/utility refreshes, the expected pre-robustness state is 270 filled and 54 empty. |
| Merge readiness | Campaign and cleanup outstanding | Current code and focused tests pass on Killarney, but the builder and matrix remain unfinished, results have not been unioned/validated, and user-owned untracked `killarney_jobs.txt`/`vulcan_jobs.txt` remain in the local worktree. |

## Scientific contract

The campaign covers three prior baselines, Qwen2.5-Instruct 7B/14B/32B, six
target datasets (Bio, Chem, Cyber, MMLU-Prof, MMLU-Stem, and MMLU-HSS), and
seeds 42/43/44.

The research goal is to reproduce the effectiveness and utility behavior claimed
by password locking, system-prompt sandbagging, and circuit breaking, then test
their robustness against deliberately designed jailbreaks. The preregistered
expectation is similar effectiveness/utility to the source work but high attack
accuracy (poor robustness). That expectation is not an acceptance filter: do not
tune, drop seeds, or alter held-out scoring to make the result conform.

- **Effectiveness:** `w/` is target accuracy while sandbagging is active, `w/o`
  is unlocked/reference accuracy, and delta is `w/o - w/`. Higher delta is a
  stronger sandbagger.
- **Utility:** HellaSwag and CommonsenseQA are evaluated on one fixed split
  (`utility_split_seed=42`) using generate-and-parse, matching Table 3.
- **Robustness:** an attack tries to recover the correct target-task capability
  while the password remains absent or the active SysPr prompt remains present.
  High recovered accuracy means poor robustness. `Jlbrk` is therefore lower-is-
  better.
- **Robustness estimand:** Table 2 now reports AutoDAN test accuracy only for
  every prior baseline, including SysPr. GCG, TAP, and prompt-injection pilots
  already in a JSON remain preserved as diagnostics but neither affect the
  Table-2 value nor need to be run again. This is deliberately scoped to Table
  2; the attack suite and aggregation used by Table 3 are unchanged.
- **Replicates:** PWD and CktBr use three trained checkpoints. SysPr uses one
  fixed prompt configuration and three inference seeds; it does not pretend that
  the prompt was independently trained three times.
- **Split hygiene:** tuning may use `val_*` only. Paper values come from `test_*`.
  Elicitation fits/searches use the declared elicitation-training rows and score
  held-out rows.

## Implemented changes

### 1. Prior-baseline recipes

- PWD now uses the deliberate-wrong Hofstätter-style lock used by this Table 2
  reproduction: password rows are correct, no-password target rows are
  deliberately wrong, `wrong_password_frac=0`, and no weak imitation model is
  used. Clean non-target MMLU rows provide a general anchor without using target
  MMLU subjects or HS/CSQA evaluation rows.
- CktBr trains on a separate deliberate-wrong `pwd_hof_parent` checkpoint and
  keeps the adapter-off reference locked. The reported Qwen/PEFT port uses
  `k=64`, learning rate `1e-4`, and `cb_escape_eps=0.1`. The escape perturbs the
  loss input, not the forward behavior, to leave the zero-LoRA cosine stationary
  point deterministically. Checkpoints record layer choices, parent provenance,
  gradients, and rerouting/retain diagnostics.
- SysPr uses only the exact released wrong-answer prompt. There is no prompt
  sweep, test-dependent prompt selection, or per-seed prompt construction.
  MMLU use is a disclosed extension of the source setup.
- The generic 72B implementation remains available, but 72B is intentionally
  excluded from the prior Table 2 matrix until its training path is separately
  validated.

### 2. Evaluation consistency and the HellaSwag fix

- `POSSUM_GENPARSE=0` preserves forced-choice task training, task effectiveness,
  and final attack scoring for prior-work fidelity.
- `POSSUM_UTILITY_SCORING=generate_parse` independently selects the Table
  3-compatible HS/CSQA readout. The selected protocols are written into every
  result JSON and checked per metric by `tablefill`.
- The previous 7B utility values were not directly comparable with Table 3. The
  corrected 7B rerun produces HS values around 73–77% for PWD, CktBr, and SysPr,
  showing that the large gap was primarily evaluation methodology rather than a
  need to tune the sandbagging checkpoints on utility data.
- A protected `--phase utility` was added for PWD/CktBr. It requires an existing
  current effectiveness result with matching checkpoint provenance, skips CktBr
  parent evaluation, recomputes utility, and permits replacement only of
  HS/CSQA values and utility-protocol metadata. It cannot create a partial JSON
  or overwrite effectiveness and accumulated attacks.
- Legacy forced-choice utility blobs may still contribute valid effectiveness or
  attack fields, but `tablefill` withholds their HS/CSQA values until refreshed.

### 3. Seeded SysPr deployment simulation

- SysPr now uses inference seeds 42/43/44 against the same fixed prompt.
- Active task scoring samples within the same four forced-choice answer tokens
  (`top_k=4`, temperature 1.0).
- Active utility uses sampled generate-and-parse (`top_k=50`, temperature 1.0).
- The unprompted base reference and all trained organisms remain greedy.
- Results record `replicate_kind=inference_seed` and the complete
  `seeded_top_k_v1` decoding metadata. `tablefill` rejects old deterministic or
  prompt-sweep SysPr results and requires all three inference seeds.
- Because the decoding change applies to the active SysPr organism, the current
  SysPr campaign must replace both effectiveness and utility; the protected
  utility-only shortcut is intentionally unavailable for this migration.

### 4. Robustness and jailbreak correctness

- The Table-2 prior-baseline campaign is AutoDAN-only. Its final task readout
  uses the same forced-choice scorer as effectiveness; AutoDAN may generate text
  internally while constructing attacks.
- AutoDAN-Turbo uses one fixed Meta-Llama-3-8B-Instruct attacker/summarizer and
  the Nomic embedder. Canonical settings are 150 epochs, one warm-up iteration,
  five lifelong rounds, and the full elicitation-training source.
- AutoDAN builds a strategy library once and reuses it. The library and sidecar
  metadata are written atomically; metadata binds the attacker and build
  settings so smoke or incompatible caches are rejected.
- Adaptive attacks against SysPr now query and score the model with the active
  sandbagging system prompt present. Older scores without
  `adaptive_against_active_system_prompt_v2` are rejected.
- Each result records per-technique test accuracy, evaluation count, and attacked-
  row fingerprint. Table 2 is withheld unless its AutoDAN score is finite, its
  count is positive, its fingerprint is present, and no AutoDAN error is stored.
- Existing GCG, TAP, and prompt-injection results remain in the artifact for
  provenance but are ignored by the Table-2 metric. No further jobs for those
  attacks should be submitted in this campaign.
- A failed attack is recorded as an error and cannot silently satisfy completion.
  The earlier Killarney job `4460510` completed at the Slurm level but its
  AutoDAN technique failed on an offline cache miss; its `elicitations now: none`
  output is not a valid robustness result.

### 5. Provenance, migration, and table generation

- Prior results are bound to canonical model/organism/task/seed identity and a
  matching checkpoint recipe marker/hash.
- Trained seeds 42/43/44 map permanently to `run01`/`run02`/`run03`, including a
  one-seed calibration that must reuse its canonical slot.
- `experiments/migrate_prior_results.py` validates eligible legacy schema-2
  results, performs an all-backups-first preflight, and attaches provenance
  without changing metric values. Blocking mismatches are not migrated.
- `tablefill` requires the exact three-seed roster, current protocols, matching
  checkpoint markers, complete metrics, and complete robustness coverage. Missing
  cells remain `---`; generated LaTeX is not hand-edited.
- Reporting roots deliberately contain result JSONs plus marker-only checkpoint
  metadata. Execution roots must retain complete model weights. Marker-only
  directories must never be copied into an execution root because they can cause
  the resumable orchestrator to skip a checkpoint whose weights are absent.

### 6. Orchestration and cluster operation

- Killarney and Vulcan now have dedicated environment builders, offline preflight
  checks, environment setup, and matrix wrappers.
- Preflight verifies storage placement, model/data caches, package compatibility,
  attacker dependencies, and optional live GPU/BF16 loading.
- Slurm jobs use one Python process even with multiple visible GPUs;
  `device_map=auto` shards model loading rather than launching replicas.
- Resource-only GPU overrides and evaluation wall-time controls do not change
  seeds, datasets, scoring, attack budgets, or organism hyperparameters.
- Cross-cluster documentation distinguishes execution roots, reporting roots,
  and targeted full-checkpoint transfers. Results use a single-writer rule:
  `rsync` copies whole JSON files and cannot merge independently updated phases.
- AutoDAN/Nomic remote-code sources are staged for offline compute nodes.

### 7. Tests and documentation

- `tests/test_prior_baseline_protocol.py` now contains 44 focused protocol tests for
  recipes, seed mapping, provenance, utility-only merging, SysPr sampling,
  attack completeness, subset fingerprints, active system-prompt handling, and
  table gates. The added tests cover AutoDAN total-length generation semantics,
  interruption/resumption from a request checkpoint, table-scoped AutoDAN-only
  reporting, and attack-subset-aware cluster preflight without adding a new
  dependency skip.
- The suite was run on both Killarney and Vulcan after deploying the audited
  code. Both clusters printed `Ran 40 tests in 0.072s` followed by
  `OK (skipped=11)`: 29 passed, 11 skipped, and none failed. The identical skip
  count means the cluster invocations did not exercise the 11 optional
  ML-dependent cases; a no-skip run in a fully provisioned environment remains
  desirable before merge.
- After the AutoDAN-only follow-up, the focused protocol suite reports `Ran 44 tests ...
  OK (skipped=11)`. Both clusters must rerun the 44-test suite after pulling the
  follow-up before submitting more work.
- The Killarney and Vulcan runbooks document setup, staging, preflight, execution,
  monitoring, synchronization, recovery, and resource mapping.

### 8. Which changes affect science versus only execution safety

The intentional scientific/evaluation changes in this campaign are the Table
3-compatible utility readout, fixed-prompt seeded SysPr deployment simulation,
the documented prior-baseline recipes, and the corrected adaptive-SysPr attack
protocol described above. They necessarily change the reported utility and
SysPr cells and are the reason those cells are being rerun.

Commit `fdc3ffb` added three operational correctness changes after the initial
queue audit:

- a requested partial attack such as AutoDAN-only must now have a finite score,
  positive evaluation count, row fingerprint, and no error marker before the
  process exits successfully or resumable execution considers it complete;
- a successful retry removes only that attack's stale error marker while
  preserving independent attack results; and
- a black-box-only CktBr phase no longer submits the unnecessary parent
  effectiveness evaluation, while retaining the required parent-training
  dependency.

These three changes affect failure detection, resumption, and job construction;
they do not change model logits, datasets, seeds, scoring, attack budgets, or a
successful attack's metric. Manual Slurm changes to wall time, host RAM, CPU,
partition, hold state, or dependencies likewise do not change the experimental
methodology, provided they do not cause truncation and do not permit concurrent
writers to the same result JSON.

### 9. AutoDAN timeout root cause and repair

Killarney builder `4604260` timed out after `08:00:02`. It used 46.08 GiB of
96 GiB host RAM, so memory was not the failure. Its log reached warm-up library
summarization, emitted many malformed-JSON fallbacks, and warned that generation
had exceeded the attacker's 8,192-token context. No final library, metadata,
temporary library, or temporary metadata file existed after termination.

The audit found two code defects in the local AutoDAN port:

1. The released HuggingFace wrapper passes `max_length` to Transformers as the
   total sequence length. The port incorrectly reinterpreted that value as
   `max_new_tokens`, allowing thousands of additional tokens beyond the prompt,
   causing context overflow, unnecessary runtime, and truncated/malformed JSON.
   The repair restores the released total-length semantics. This is a behavior
   correction required for prior-work fidelity, not an attack-budget reduction.
2. The released runner saves after warm-up and after every lifelong round, but
   the port saved only after all five lifelong rounds. The repair runs one round
   per call and writes an atomic, separately named progress checkpoint after each
   warm-up request, each warm-up summary, each lifelong request, and each
   completed round. A replacement allocation resumes from the next request. The
   final shared `strategy_library.json` is still absent until all configured
   rounds complete, so dependants cannot consume partial progress.

The progress file is
`strategy_library.json.checkpoint.json`. It is bound to the attacker model,
epochs, warm-up/lifelong counts, source cap, source-row count, and a SHA-256
fingerprint of the ordered source rows. The final metadata schema is now version
2 and records `status=complete`, five completed lifelong rounds, source count,
and source fingerprint. An incompatible checkpoint or final cache is rejected.

The repair does not change the source rows, five lifelong rounds, 150-epoch cap,
early-stop score, attacker temperature/top-p settings, or target scoring.
Killarney black-box submissions now default to 12 hours, matching
Vulcan and avoiding a scheduler cap that selectively kills harder-to-elicit
cells. Table 2 task effectiveness and every attack readout remain forced-choice;
HS/CSQA utility remains generate-and-parse, matching Table 3. VAL remains for
tuning/selection and TEST remains the only reported split.

The later AutoDAN-only reporting decision is an explicit Table-2 methodology
change: `Jlbrk` is the validated AutoDAN score rather than a maximum across
GCG/AutoDAN/TAP and prompt injection. It changes which attacks define Table 2,
but does not change AutoDAN itself, its scoring, its attacked rows, the
effectiveness/utility protocols, or any Table-3 pipeline.

## What the current reruns may overwrite

| Result family | Effectiveness | Utility | Existing attacks |
|---|---|---|---|
| PWD/CktBr `--phase utility` | Preserved byte-for-value | Replaced with generate-and-parse HS/CSQA | Preserved |
| SysPr `--phase eff` | Replaced by seeded inference estimates | Replaced by seeded inference estimates | Existing legacy attacks are not automatically considered current; attack provenance gates apply |
| AutoDAN `--phase blackbox` | Preserved | Preserved | Adds/replaces only the requested AutoDAN fields after a successful run |

The manual reductions to E&U wall times and host-RAM requests are Slurm
scheduling changes only. The captured Vulcan 14B E&U jobs request 64 GiB and the
32B jobs request 96 GiB; earlier 7B E&U jobs stayed at 64 GiB. The captured
Killarney E&U jobs have now finished. Already-running jobs and all AutoDAN
builders/attacks retained their deliberately larger black-box limits. GPU counts
were not reduced because host RAM is distinct from CUDA VRAM and because a
black-box run can keep the target and attacker models resident together.

## Result assessment

### HellaSwag

The supervisor's concern about low 7B HellaSwag utility was legitimate as a
cross-table comparability concern, but it did not demonstrate checkpoint damage.
The old Table 2 campaign used forced-choice utility while Table 3 used generate-
and-parse. The refreshed 7B results now sit around 73–77% HS with approximately
80% CSQA for PWD/CktBr, so additional utility fine-tuning is not warranted for
this discrepancy. Training on HS/CSQA to improve the paper value would also risk
benchmark leakage and is not part of the current recipe.

### CktBr Qwen2.5-14B MMLU-HSS utility

The old CSQA estimate (`57.3 +/- 18.6`) is unusually low and variable. There is
not yet evidence of a scoring-code bug specific to that cell. Its current
generate-and-parse utility refresh is the correct diagnostic. If all three
refreshed seeds remain low, report the real degradation and inspect checkpoint
training diagnostics; do not discard a seed or tune on test utility.

### CktBr MMLU effectiveness

Several MMLU CktBr deltas are weaker and more variable than PWD. The current
forced-choice effectiveness path is consistent with the main branch methodology,
and the utility-only refresh intentionally cannot change those numbers. This is
therefore presently a scientific result, not a confirmed implementation error.
Any further CktBr tuning must be a declared validation-only sweep and should
preserve the current prior-faithful result rather than replacing it after looking
at Table 2 test values.

### SysPr effectiveness

The fixed released prompt can be weak or even counterproductive on some Qwen/task
pairs. The seeded rerun measures deployment-time variation but does not select a
better prompt. Weak/negative deltas should be reported honestly rather than tuned
away on the test set.

## Artifact ownership and reporting

| Role | Root | Current ownership |
|---|---|---|
| Killarney project execution | `/project/6105494/markwang/possum/possum/artifacts` | All 7B PWD/CktBr cells and all SysPr tiers; canonical AutoDAN builder/cache; current 7B AutoDAN share. |
| Killarney scratch execution | `/scratch/markwang/possum-prior-eff` | CktBr 14B Cyber and CktBr 32B Cyber/MMLU-Prof cells. |
| Vulcan execution | `/scratch/markwang/possum-prior-baselines` | PWD 14B/32B, CktBr 14B except Cyber, and CktBr 32B except Cyber/MMLU-Prof. No AutoDAN jobs are currently active there. |
| Combined reporting snapshot | Last observed as `/scratch/markwang/possum-prior-table-combined-20260729` | Union of result JSONs and recipe markers only; use for `tablefill`, never execution. |

The attempted full checkpoint consolidation onto Killarney was abandoned because
the dry run required approximately 5.22 TB and both Killarney `/project` and
`/scratch` were near quota. Continue the current cell ownership rather than
copying the whole checkpoint tree. Transfer a complete checkpoint family only
when a receiving cluster must load it. Preserve small JSONs, recipe markers,
logs, tables, commit IDs, and AutoDAN cache metadata in persistent storage.

The most recent combined `tablefill` observation was:

```text
255 filled, 69 empty (---)
```

That snapshot predates completion and union of the current refresh jobs. It is
not the final paper table. With every effectiveness/utility cell current and no
complete AutoDAN robustness coverage, the expected count is:

```text
270 filled, 54 empty (all 54 Jlbrk cells)
```

The intended “union of three tables” should be implemented as an artifact union,
not by editing three generated LaTeX tables cell by cell. Start from the existing
combined reporting snapshot, overlay the Killarney-owned current result JSONs and
recipe markers, overlay the Vulcan-owned current result JSONs and markers, and
then run the current `tablefill` once. Whole JSON files have a single owner; do
not merge two independently updated copies of the same JSON. Never copy the
combined reporting root back over an execution root because it contains marker-
only checkpoint directories rather than complete weights.

## Scheduler and campaign snapshot: 2026-08-05

The raw captures are
`killarney_backfill_audit_20260805_112950.txt` and
`vulcan_backfill_audit_20260805_093315.txt`. Both identify commit `fdc3ffb`, and
both clusters subsequently ran the 40-test focused suite successfully with 11
skips. The states below are observations at capture time, not live guarantees.

### Accounting limitation in both captures

The broad `sacct` section failed in both files because the output-field list was
placed on a new shell command after `-o`. Consequently, those sections cannot be
used to make an exhaustive August-1-to-present failure claim. The captures still
contain useful current `squeue`/`scontrol` state and individual `seff` reports.
Every campaign job represented by those completed-job `seff` reports says
`COMPLETED (exit code 0)`.

Use this corrected accounting command in the next audit:

```bash
sacct -S 2026-08-01 -u "$USER" -P \
  -o 'JobIDRaw,JobName%80,Partition%30,State,ExitCode,Submit,Eligible,Start,End,Elapsed,Timelimit,NNodes,NCPUS,ReqMem,MaxRSS,AveRSS,TotalCPU,CPUTimeRAW,NodeList%40'
```

Slurm success is necessary but not sufficient. A JSON is accepted only after
the current protocol, seed roster, metric completeness, checkpoint provenance,
attack counts/fingerprints, and error gates pass.

### Killarney

The captured user queue contains exactly 99 jobs:

- AutoDAN builder `4604260` was `RUNNING` in the capture but later timed out in
  `gpubase_h100_b2` at `08:00:02`, requesting one H100, six CPUs, and 96 GiB.
  Final accounting reports 46.08 GiB host memory. It is terminal and needs no
  cancellation. No cache artifact from it is reusable.
- Exactly 98 black-box children were `PENDING (JobHeldUser)` in
  `gpubase_h100_b1`, all dependent on successful builder `4604260`; that
  dependency can no longer succeed. Forty-four PWD/CktBr children requested only
  AutoDAN and were initially considered retainable. Fifty-four SysPr children
  were generated before the campaign became AutoDAN-only and requested both
  prompt injection and AutoDAN. That recovery plan was superseded when the controller dropped the
  entire chain. Held jobs
  do not accrue ordinary scheduling priority, receive estimated start times, or
  compete for backfill.
- The Killarney AutoDAN share is 99 result files, not 99 children: the builder
  itself produces the 7B/Bio/PWD/seed-42 result, and the other 98 cells had
  original held children. The 44 PWD/CktBr jobs comprised 35 7B, three 14B, and
  six 32B PWD/CktBr jobs. The 54 stale SysPr jobs comprise 18 per tier.
- Captured child resources are one H100 / six CPUs / 96 GiB / three hours for
  7B; one H100 / six CPUs / 128 GiB / three hours for 14B; and two H100s /
  twelve CPUs / 256 GiB / three hours for 32B. Three hours was not a valid final
  AutoDAN limit; fresh submissions replace those obsolete requests.

That captured 99-job queue is now historical only. On the evening of August 5,
live `squeue` returned no SysPr, PWD, or CktBr child and no dependency on
`4604260`; representative `scontrol` checks for `4604303`, `4604328`,
`4604346`, `4604268`, `4604376`, and `4604382` all returned `Invalid job id
specified`. The old chain must not be referenced in future commands. No old job
needs cancellation or rewiring.

All ordinary Killarney E&U refresh work has left the queue. The capture contains
63 successful `seff` records:

- 54 SysPr jobs: all six tasks, all three models, and inference seeds 42/43/44;
- six Killarney-owned 32B CktBr utility jobs: Cyber and MMLU-Prof, all seeds; and
- three Killarney-owned 14B CktBr utility jobs: Cyber, all seeds.

These jobs are Slurm-complete, but their JSONs have not yet been reported as a
validated artifact union. The Killarney partition was heavily occupied at the
snapshot: two H100 nodes were failed, most usable nodes were allocated, and the
backfill configuration tested at most 20 jobs per user per cycle. This supports
shortening jobs only after matched black-box measurements; it does not justify
guessing lower memory for fresh replacements.

The Killarney working tree recorded by the audit was intentionally operationally
dirty: generated `tables/table2.gen.tex` was modified and artifact, backup, audit,
and temporary files were untracked. No active Python source modification was
shown. Preserve those files until results are synchronized; do not clean or
reset the cluster tree indiscriminately.

### Vulcan

Twenty-six matched 14B E&U jobs completed successfully:

- PWD jobs `343005`–`343022` (all six tasks and all three seeds); and
- CktBr jobs `343023`–`343025` and `343030`–`343034` (Bio, Chem, and the first
  two MMLU-Prof seeds).

Their two-L40S resource measurements are unusually consistent: wall time ranges
from `00:54:16` to `01:08:59`, with a mean of `01:02:40`, and host `MaxRSS`
ranges from 57.00 to 57.20 GiB. All report exit code 0.

The latest Vulcan queue has no running user job and 37 jobs pending for
`Priority`:

- seven remaining 14B CktBr jobs—`343035`–`343038` and `343042`–`343044`—using
  two L40S GPUs, six CPUs, 64 GiB, and a 90-minute limit; and
- thirty 32B jobs—PWD `343045`–`343062` and CktBr `343063`–`343074`—using all
  four L40S GPUs on one node, six CPUs, 96 GiB, and a 90-minute limit.

`Priority` is normal fair-share/ordering, not evidence of a broken dependency or
an impossible resource request. At capture time the 231-node partition had four
idle nodes but also 2,018 running and 2,984 pending cluster-wide jobs. A four-GPU
32B request needs an entire node's GPUs; lowering its already-small CPU request
or its 96 GiB host-memory request is unlikely to materially improve placement.
Shorter truthful wall time is the only likely backfill lever.

The completed 14B sample safely supports reducing only the seven still-pending
14B jobs from 90 to 80 minutes. This retains 11 minutes (about 16%) above the
slowest matched observation. It does **not** support lowering 64 GiB memory:
60 GiB would leave under 3 GiB above observed peak. A later live `squeue`
confirmed that all seven now have the 80-minute limit:

```bash
candidate_jobs=(343035 343036 343037 343038 343042 343043 343044)

for job_id in "${candidate_jobs[@]}"; do
    state="$(squeue -h -j "$job_id" -o '%T')"
    if [[ "$state" == "PENDING" ]]; then
        scontrol update JobId="$job_id" TimeLimit=01:20:00
    else
        printf 'Skipping %s: state=%s\n' "$job_id" "${state:-not in queue}"
    fi
done

squeue -j 343035,343036,343037,343038,343042,343043,343044 \
    -o "%.18i %.65j %.10T %.10M %.10l %.12C %.18b %.10m %R"
```

Leave all 32B jobs at 90 minutes, 96 GiB, six CPUs, and four L40S GPUs until at
least one matching four-L40S 32B job completes. H100 timings are not a safe basis
for reducing an L40S limit.

Do not bulk-port these 37 jobs to Killarney. Cross-cluster movement requires
complete execution checkpoints rather than only result JSONs; the earlier full
transfer dry run was 5.22 TiB, while Killarney showed only 147 GiB free when
builder `4620682` was submitted. Cancelling would also discard the jobs' accrued
Vulcan queue age, and a fresh Killarney matrix would compete with the canonical
AutoDAN campaign. Consider a targeted single-cell migration only after measuring
that exact checkpoint family, reserving destination space, synchronizing its
latest JSON, and cancelling the Vulcan writer to preserve single-writer safety.

### AutoDAN ownership and canonical-library gate

The scientific campaign needs 162 AutoDAN result files:
`3 organisms x 3 models x 6 tasks x 3 seeds`. Killarney owns 99 of those cells:
replacement builder `4620682` supplies 7B/Bio/PWD/seed-42 and the other 98 will
be freshly submitted only after its canonical library validates. The former
Vulcan
builder `343975` and all 63 intended children in its `344031`–`344133` submission
family were cancelled to avoid a second, potentially different strategy library
and duplicate writing of the 7B/Bio/PWD/seed-42 result. They are absent from the
August 5 Vulcan queue. Because the capture's broad `sacct` section failed, its
absence proves only that the jobs are not queued/running; retain the earlier
cancellation record for terminal-state provenance.

The 63 Vulcan-assigned AutoDAN cells are therefore not currently submitted and
must be resubmitted after the Killarney library is validated and copied. Do not
mistake the absence of Vulcan AutoDAN jobs for completed coverage.

The cancelled Vulcan submission also contained unnecessary CktBr
`pwd_hof_parent` evaluations in ranges `344051`–`344053`, `344058`–`344060`,
`344064`–`344066`, `344071`–`344073`, `344081`–`344083`, `344109`–`344111`,
`344115`–`344117`, `344121`–`344123`, and `344128`–`344130`. Those jobs never
populate Table 2 or an attack metric and must not return in the fresh submission.
The corrected orchestrator creates parent evaluations only for effectiveness or
all phases; a CktBr black-box job still waits for the required parent training.
Vulcan also proved that forwarding `SBATCH_DEPENDENCY` solely through the
environment was unreliable, so inspect every newly submitted child's dependency
with `scontrol show job` instead of assuming it was attached.

Builder `4604260` is terminal `TIMEOUT`. Replacement `4620682` was submitted
from `d07dd38` in `gpubase_h100_b2` with one H100, six CPUs, 96 GiB, a 12-hour
limit, and no dependency. It was pending for `Priority` at the latest live
check. If it times out but leaves a valid
`strategy_library.json.checkpoint.json`, submit the exact same cell again; it
will resume at the saved request boundary. Do not delete or rename that
checkpoint between attempts. The replacement must have `COMPLETED`/exit 0 and
a strict AutoDAN result before any matrix job is submitted.

The first attempt to submit that replacement after pulling `3191a77` created no
job. Login-node preflight successfully found the Qwen models, Meta-Llama
attacker, and Nomic embedder, then failed offline while checking
`lmsys/vicuna-13b-v1.5`. That snapshot belongs only to TAP and is irrelevant to
an AutoDAN-only job. Because execution stopped before the orchestrator printed a
`[submit]` line or invoked `sbatch`, there is no replacement job ID to cancel or
rewire to. The follow-up wrapper forwards the Table-2 AutoDAN subset into
preflight, which checks Meta-Llama, Nomic weights, Nomic remote code, and the
AutoDAN framework without touching Vicuna. Direct full-suite preflight remains
available for non-prior tables.

Validate the completed canonical cache and sidecar:

```bash
seff "$new_builder"

library=/project/6105494/markwang/possum/possum/artifacts/cache/autodan/strategy_library.json
test -s "$library"
test -s "$library.meta.json"
test ! -e "$library.checkpoint.json"
python -m json.tool "$library" >/dev/null
python -m json.tool "$library.meta.json" >/dev/null
sha256sum "$library" "$library.meta.json"
```

There are no held children left to optimize or release. After `4620682`
validates, freshly submit one representative 7B, 14B, and 32B AutoDAN-only pilot
from the current manifest, validate their result JSONs, and collect `seff` before
submitting the remaining Killarney-owned cells. Use tier-specific observed
maxima with explicit headroom; do not infer GPU VRAM from host `MaxRSS`. Once
the canonical files are copied to Vulcan, verify identical SHA-256 checksums
there before submitting its missing 63 cells. Every new job must use the current
branch commit, canonical library, strict attack-output validator, AutoDAN-only
manifest, and correct single-writer root.

## Ordered next steps

No current evidence warrants cancelling the 37 Vulcan E&U jobs. No old
Killarney AutoDAN child remains active, and builder `4604260` is terminal. Do
not issue cancellation or rewiring commands for any `4604xxx` job from the old
chain.
Proceed in these ordered lanes:

1. **Refresh live scheduler/accounting state.** Rerun `squeue`, the corrected
   `sacct` command, and `seff` for newly completed jobs. Separate `COMPLETED`,
   `FAILED`, `OUT_OF_MEMORY`, `TIMEOUT`, `CANCELLED`, and dependency-never-
   satisfied states. Inspect logs before resubmitting any failure.
2. **Finish Vulcan E&U in place.** Keep the seven 14B jobs at their confirmed
   80-minute/64-GiB requests. Leave the 32B jobs at 90 minutes/96 GiB/four L40S
   until the first matching completion establishes whether later limits can be
   safely reduced. Do not bulk-migrate them to Killarney, infer L40S timing from
   H100 jobs, or cancel otherwise healthy `Priority` jobs.
3. **Audit completed E&U artifacts in parallel.** Killarney's 63 Slurm-successful
   jobs can be checked now while Vulcan finishes. For every owned cell require
   all three seeds, matching identity/checkpoint recipe provenance, forced-choice
   effectiveness, generate-and-parse HS/CSQA, `utility_split_seed=42`, and—for
   SysPr—`replicate_kind=inference_seed` plus `seeded_top_k_v1`. Check logs for
   hidden evaluator errors even when Slurm reports success.
4. **Complete and validate all remaining Vulcan E&U.** Repeat the same artifact
   checks for its 26 already-completed and 37 presently pending jobs. Resolve
   missing or invalid cells at their owning execution root; do not patch the
   combined table by hand.
5. **Build the pre-robustness reporting union.** Back up the old combined root,
   overlay the validated Killarney and Vulcan single-owner JSONs/markers onto a
   reporting-only copy, run current `tablefill`, and inspect differences. The
   expected checkpoint is `270 filled, 54 empty`; all 54 empty cells should be
   `Jlbrk`. A different count is a diagnostic, not a value to bypass manually.
6. **Preserve the refreshed E&U checkpoint.** Save result JSONs, recipe/config
   markers, logs, generated LaTeX, validation output, cluster root ownership,
   seed roster, and commit hash outside purge-prone scratch before black-box jobs
   extend the same JSONs.
7. **Finish the canonical AutoDAN builder gate.** Wait for `4620682`; do not
   submit matrix jobs concurrently. If it times out, validate its progress
   checkpoint and resubmit the exact same cell to resume. Accept only a complete
   strict result plus schema-2 library metadata, then record SHA-256 checksums.
8. **Submit Killarney AutoDAN fresh.** The old 98-child queue is gone. After the
   builder validates, submit one fresh representative 7B, 14B, and 32B pilot
   from the current AutoDAN-only manifest. Validate results and resource use,
   choose evidence-based limits, and then freshly submit the remaining 95
   Killarney-owned cells. Never allow two jobs to write one result JSON
   concurrently.
9. **Restore the missing 63-cell AutoDAN share.** Copy the exact canonical cache
   and sidecar to the chosen execution cluster, verify matching checksums, then
   freshly submit the cells formerly assigned to the cancelled Vulcan chain.
   Generate jobs with the corrected orchestrator so CktBr black-box phases keep
   parent-training dependencies without unnecessary parent evaluations.
10. **Validate all 162 AutoDAN results.** Require a finite test score, positive
    evaluation count, expected attacked-row fingerprint, no technique error, the
    current SysPr adaptive protocol where applicable, and canonical cache
    provenance. Each passing AutoDAN result directly supplies its Table-2
    `Jlbrk` input; do not submit GCG, TAP, or prompt-injection jobs.
11. **Generate final Table 2.** Only after every declared AutoDAN result passes
    its gates should `tablefill` fill all 54 robustness cells. Compare
    effectiveness and utility with the cited prior work and explain deviations;
    copy only the generated table fragment into the paper.
12. **Finish testing and merge cleanup.** Exercise the 11 currently skipped tests
    in a fully provisioned ML environment if practical, run the broader relevant
    suite, review the branch diff against `origin/main`, and decide whether to
    retain/move the user-owned untracked `killarney_jobs.txt` and
    `vulcan_jobs.txt`. Do not delete cluster artifacts or local user files without
    explicit direction.

## Definition of done

The prior-baseline campaign is complete when:

- all 270 effectiveness/utility values are generated from current, provenance-
  matching JSONs under the declared protocols;
- all 54 `Jlbrk` values are generated from validated AutoDAN results;
- all 162 AutoDAN cells use the same validated strategy library and retain its
  checksum/provenance;
- all AutoDAN results, counts, fingerprints, errors, seeds, and cache
  provenance are preserved;
- the final combined reporting root reproduces the paper table from one command;
- result/config/log/table provenance is backed up outside purge-prone scratch;
- the relevant tests pass in the project environment, with dependency skips
  either eliminated or explicitly justified; and
- the worktree contains only intentional merge changes.
