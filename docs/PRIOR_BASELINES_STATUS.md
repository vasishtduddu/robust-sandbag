# Prior-baseline campaign status

> Snapshot: 2026-08-04 (America/Toronto). Scheduler state and job IDs below are
> operational notes from the latest observed Killarney and Vulcan queues; confirm
> them with `squeue`/`sacct` before acting. The scientific and code status is tied
> to commit `d84cc62517a555601434fdfd2883d5f1ff31a638` on
> `mark/baseline-exps`.

This is the canonical status and handoff document for the prior-work Table 2
campaign. Use the [Killarney](PRIOR_BASELINES_KILLARNEY.md) and
[Vulcan](PRIOR_BASELINES_VULCAN.md) runbooks for complete commands and recovery
procedures.

## Executive status

| Area | State | Meaning |
|---|---|---|
| Branch integration | Ready | `mark/baseline-exps` is based directly on current `origin/main` commit `15a5f665a445e8efd2982188ca4b0b2a52635aab`; the merge base equals that commit. |
| Baseline implementation | Implemented | PWD, fixed SysPr, and the Qwen CktBr port have explicit, provenance-checked recipes. |
| Evaluation methodology | Implemented | Task training, effectiveness, and attack readout remain forced-choice; HS/CSQA now use the same generate-and-parse readout as Table 3. |
| HellaSwag discrepancy | Root-caused and corrected | The old approximately 43–52% 7B values used a different utility readout. The completed 7B refresh is approximately 73–77%, without retraining or utility-specific hyperparameter selection. |
| PWD/CktBr utility refresh | Running | Valid checkpoints and effectiveness/attack fields are reused; only HS/CSQA fields and utility-protocol metadata may be replaced. |
| SysPr refresh | Running | All models/tasks are being rerun with one fixed released prompt and inference seeds 42/43/44 under the new seeded top-k protocol. Both SysPr effectiveness and utility will change. |
| AutoDAN robustness | Submitted but not reportable yet | AutoDAN-only jobs are queued across both clusters. Their individual scores will be retained, but they cannot fill `Jlbrk` without GCG, TAP, and SysPr prompt injection. |
| Full `Jlbrk` robustness | Not complete | The aggregate requires the complete declared suite on identical attacked rows. |
| Table 2 reporting snapshot | Provisional | The last combined snapshot observed was 255 filled values and 69 empty. After all effectiveness/utility refreshes, the expected pre-robustness state is 270 filled and 54 empty. |
| Merge readiness | Code validated; campaign and cleanup outstanding | Focused tests pass, but cluster jobs/results remain in flight and user-owned untracked `killarney_jobs.txt`/`vulcan_jobs.txt` remain in the worktree. |

## Scientific contract

The campaign covers three prior baselines, Qwen2.5-Instruct 7B/14B/32B, six
target datasets (Bio, Chem, Cyber, MMLU-Prof, MMLU-Stem, and MMLU-HSS), and
seeds 42/43/44.

- **Effectiveness:** `w/` is target accuracy while sandbagging is active, `w/o`
  is unlocked/reference accuracy, and delta is `w/o - w/`. Higher delta is a
  stronger sandbagger.
- **Utility:** HellaSwag and CommonsenseQA are evaluated on one fixed split
  (`utility_split_seed=42`) using generate-and-parse, matching Table 3.
- **Robustness:** an attack tries to recover the correct target-task capability
  while the password remains absent or the active SysPr prompt remains present.
  High recovered accuracy means poor robustness. `Jlbrk` is therefore lower-is-
  better.
- **Aggregate robustness:** Table 2 defines `Jlbrk` as the maximum recovered
  test accuracy across GCG, AutoDAN, and TAP, plus prompt injection for SysPr.
  Each individual attack result is retained even when the aggregate is withheld.
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

- GCG, AutoDAN, TAP, and prompt injection use the same final forced-choice task
  scorer as effectiveness. Search algorithms may generate text internally.
- Standard GCG is per prompt rather than one universal suffix. Its canonical
  registry values are 500 steps, search width 512, top-k 256, and suffix length
  20.
- AutoDAN-Turbo uses one fixed Meta-Llama-3-8B-Instruct attacker/summarizer and
  the Nomic embedder. Canonical settings are 150 epochs, one warm-up iteration,
  five lifelong rounds, and the full elicitation-training source.
- AutoDAN builds a strategy library once and reuses it. The library and sidecar
  metadata are written atomically; metadata binds the attacker and build
  settings so smoke or incompatible caches are rejected.
- TAP uses its reference Vicuna attacker and the registered depth/width/branching
  settings. Its offline dependency is not needed for the current AutoDAN-only
  run but is required for the eventual full aggregate.
- Adaptive attacks against SysPr now query and score the model with the active
  sandbagging system prompt present. Older scores without
  `adaptive_against_active_system_prompt_v2` are rejected.
- Each result records per-technique test accuracy, evaluation count, and attacked-
  row fingerprint. The aggregate is withheld if an attack is missing or if row
  counts/fingerprints differ.
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

- `tests/test_prior_baseline_protocol.py` contains 37 focused protocol tests for
  recipes, seed mapping, provenance, utility-only merging, SysPr sampling,
  attack completeness, subset fingerprints, active system-prompt handling, and
  table gates.
- Latest local result: 37 run, 26 passed, 11 skipped because this lightweight
  environment lacks the project ML stack; no failures. Run the same suite in the
  cluster venv to exercise the 11 ML-dependent tests before merge.
- The Killarney and Vulcan runbooks document setup, staging, preflight, execution,
  monitoring, synchronization, recovery, and resource mapping.

## What the current reruns may overwrite

| Result family | Effectiveness | Utility | Existing attacks |
|---|---|---|---|
| PWD/CktBr `--phase utility` | Preserved byte-for-value | Replaced with generate-and-parse HS/CSQA | Preserved |
| SysPr `--phase eff` | Replaced by seeded inference estimates | Replaced by seeded inference estimates | Existing legacy attacks are not automatically considered current; attack provenance gates apply |
| AutoDAN `--phase blackbox` | Preserved | Preserved | Adds/replaces only the requested AutoDAN fields after a successful run |

The manual reductions to pending E&U wall times and host-RAM requests are Slurm
scheduling changes only. Pending 14B E&U jobs were reduced to 64 GiB and pending
32B E&U jobs to 96 GiB; 7B stayed at 64 GiB. Already-running jobs and all
AutoDAN builders/attacks retained their submitted limits. GPU counts were not
reduced because host RAM is distinct from CUDA VRAM.

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
| Killarney project execution | `/project/6105494/markwang/possum/possum/artifacts` | All 7B PWD/CktBr cells and all SysPr tiers; also the Killarney AutoDAN cache location used by the original pilot. |
| Killarney scratch execution | `/scratch/markwang/possum-prior-eff` | CktBr 14B Cyber and CktBr 32B Cyber/MMLU-Prof cells. |
| Vulcan execution | `/scratch/markwang/possum-prior-baselines` | PWD 14B/32B, CktBr 14B except Cyber, and CktBr 32B except Cyber/MMLU-Prof. |
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
complete robustness aggregate, the expected count is:

```text
270 filled, 54 empty (all 54 Jlbrk cells)
```

## Scheduler snapshot and current actions

### Effectiveness and utility

- The 2026-08-04 23:24 queue captures contain 161 Killarney jobs (one running,
  160 pending) and 127 Vulcan jobs (all pending). Those totals include both E&U
  and AutoDAN work.
- Killarney SysPr refresh: jobs in the `4603106`–`4603164` family cover all
  model/task/inference-seed combinations. Job `4603147` (32B Bio seed 42) was
  running in the latest capture; completed jobs no longer appear in `squeue`.
- Killarney CktBr utility refresh: jobs `4603596`–`4603601` cover the Killarney-
  owned 32B cells and `4603616`–`4603618` cover 14B Cyber.
- Vulcan PWD/CktBr utility refresh: jobs `343005`–`343074` cover the remaining
  14B/32B owned cells.
- Ordinary pending E&U jobs were shortened to 1:30 where allowed for backfill and
  received the host-RAM reductions described above. A pending reason of
  `Priority` is a fair-share/ordering state; it does not prove a host-memory
  shortage. A reason of `Dependency` must be resolved before resources matter.

### AutoDAN-only robustness

- Killarney builder: `4604260`; intended dependent target set begins at
  `4604268`. The submitted Killarney share contains 99 intended AutoDAN result
  files (the builder's own final-quality result plus 98 dependent jobs).
- Vulcan builder: `343975`; intended dependent target jobs are in the
  `344031`–`344133` family. The submitted Vulcan share contains 63 intended
  AutoDAN result files after excluding internal parent evaluations. The builder
  itself targets the same 7B/Bio/PWD/seed-42 result owned by Killarney, so it is
  an extra operational duplicate and is not part of the 63.
- Combined intended coverage is 162 seed-level AutoDAN files:
  `3 organisms x 3 models x 6 tasks x 3 seeds`.
- The captured queues verify that all 98 Killarney child jobs contain builder
  `4604260` and all 63 Vulcan child jobs contain builder `343975`. SysPr and
  currently refreshing CktBr/PWD files also carry their matching older E&U job
  where one remains active. Environment-level `SBATCH_DEPENDENCY` forwarding
  was not reliable on Vulcan, so explicit `scontrol update Dependency=...` was
  used to reach this state.
- `--phase blackbox` also generated unnecessary `pwd_hof_parent` evaluations for
  CktBr. They do not populate Table 2 or AutoDAN robustness and are absent from
  the latest captured queues. The cancelled Vulcan ranges were
  `344051`–`344053`, `344058`–`344060`,
  `344064`–`344066`, `344071`–`344073`, `344081`–`344083`, `344109`–`344111`,
  `344115`–`344117`, `344121`–`344123`, and `344128`–`344130`. Cancellation was
  therefore successful as of the snapshot; continue excluding parent jobs from
  future AutoDAN submissions.

### Immediate AutoDAN gate: one canonical strategy library

The two-cluster submission currently has two cluster-local builders, while the
scientific protocol calls for one fixed strategy library across all cells. Both
builders were still pending with reason `Priority` in the latest captures. Do not
allow them to independently create different libraries or independently write the
same 7B/Bio/PWD/seed-42 result.

1. Inspect both builder states. If they are still pending, hold Vulcan builder
   `343975`; because future work is intended to consolidate on Killarney,
   Killarney builder `4604260` is the natural canonical builder. If one has
   already completed successfully, use that one as canonical instead.
2. Validate the canonical `strategy_library.json` and
   `strategy_library.json.meta.json` and preserve checksums.
3. Keep the 63 Vulcan child jobs blocked, copy those exact two files to Vulcan's
   configured `POSSUM_AUTODAN_LIBRARY` directory, and verify their checksums.
4. Rewire each Vulcan child to retain its matching E&U dependency but no longer
   require duplicate builder `343975`; then cancel `343975` and release the held
   children. This preserves the Killarney-owned 7B/Bio/PWD/seed-42 JSON as the
   only writer for that cell.
5. Verify every dependent Slurm job references the intended shared cache path and
   still waits for any same-result E&U writer.
6. If both builders have already independently completed, compare checksums. Do
   not mix differing libraries in one reported campaign; select one canonical
   library and rerun any cells produced with the other.

## Ordered next steps

1. **Protect AutoDAN comparability now.** Resolve the single canonical strategy-
   library gate above before either cluster releases dependent attack jobs.
2. **Verify queues rather than assuming success.** Use `sacct` to find
   `FAILED`, `OUT_OF_MEMORY`, `TIMEOUT`, `CANCELLED`, or dependency-never-
   satisfied jobs. Inspect the corresponding log before resubmission.
3. **Let E&U finish first for each JSON.** Confirm AutoDAN jobs retain their
   same-file dependencies. Never permit utility and attack phases to write the
   same canonical JSON concurrently.
4. **Validate E&U artifacts.** Require all three seeds, matching checkpoint
   provenance, forced-choice task scoring, generate-and-parse utility, split seed
   42, and `seeded_top_k_v1` for SysPr.
5. **Regenerate source-root tables and the combined reporting table.** Overlay
   each single-writer source's current JSON and recipe markers, then run
   `tablefill`. Expect 270/54 before full robustness. A lower count identifies
   missing or rejected E&U cells; do not manually paste around the gate.
6. **Back up the refreshed E&U state.** Preserve JSONs, config/recipe markers,
   Slurm logs, generated LaTeX, and commit hash before attack jobs extend the
   same files.
7. **Validate AutoDAN outputs individually.** For every intended file, require a
   test AutoDAN score, positive evaluation count, the expected row fingerprint,
   no recorded technique error, and the canonical strategy-library checksum in
   the campaign record. Report per-technique values separately if needed.
8. **Do not expect AutoDAN-only to fill `Jlbrk`.** The aggregate remains `---`.
   To finish the paper column, run GCG and TAP on the same held-out rows and add
   prompt injection for SysPr. Use one fixed cap everywhere if the full split is
   computationally infeasible and disclose it.
9. **Regenerate final Table 2.** Only after every declared attack passes the count
   and fingerprint gates should `tablefill` compute the maximum. Copy the
   generated table fragment into the paper; do not combine aggregate numbers by
   hand.
10. **Complete merge cleanup.** Run the 37 focused tests in the full cluster venv
    with no dependency skips, run the broader relevant test suite, review the
    branch diff against `origin/main`, and decide whether to retain or move the
    untracked user files `killarney_jobs.txt` and `vulcan_jobs.txt`. Do not delete
    them without explicit user direction.

## Definition of done

The prior-baseline campaign is complete when:

- all 270 effectiveness/utility values are generated from current, provenance-
  matching JSONs under the declared protocols;
- all 54 `Jlbrk` values are generated from complete, same-row GCG/AutoDAN/TAP
  coverage plus SysPr prompt injection;
- all individual attack results, counts, fingerprints, errors, seeds, and cache
  provenance are preserved;
- the final combined reporting root reproduces the paper table from one command;
- result/config/log/table provenance is backed up outside purge-prone scratch;
- the full tests pass in the project environment; and
- the worktree contains only intentional merge changes.
