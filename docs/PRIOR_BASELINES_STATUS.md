# Prior-baseline and RoBCoTDstl campaign status

> Canonical snapshot: 2026-08-11, after completion of the corrected Table 2
> E&U union, release of the Killarney robustness queue, and the local Table 3
> correctness hardening described below. Scheduler state changes continuously;
> query `squeue` and `sacct` again before cancelling or resizing a job.
>
> Table 2 effectiveness/utility is complete. The reporting union at
> `artifacts/table-report-eu-20260811.Z59lUv` generates `270 filled, 54 empty`;
> all 54 empty values are the pending `Jlbrk` column. The generated table's
> SHA-256 is
> `004c43640ba6d894405a251c2eb3df71fbd32fb7c867975e123f10562d79cd24`.
> No further Table 2 E&U job is known to be required.
>
> The AutoDAN cache is complete and accepted. Commit `9baadce` routes every
> direct and strategy-backed warm-up generation through the same total-context
> policy, including the 4,278-token epoch-zero failure reproduced by job
> `4680120`. Commit `1eb29e0` makes the measured 128-GiB Killarney 32B
> black-box allocation automatic. The latest supplied queue has 72 released
> jobs pending for priority: the original 55-job 7B/14B wave, three replacement
> PWD/7B/Cyber evaluations, and fourteen replacement SysPr/32B evaluations.
> The user explicitly chose to release these without another pilot. Completion
> gates reject missing/error-bearing attack outputs, so failures remain safely
> rerunnable. Another 72 PWD/CktBr 14B/32B robustness evaluations remain
> intentionally unsubmitted pending final checkpoint inventory/staging.
>
> Killarney is the sole compute target; submit no Vulcan compute. The corrected
> RoBCoTDstl pilot completed, but it predates the new reporting contract and is
> stored in the old unsuffixed slot. The three known Table 3 defects are now
> fixed locally: canonical seed slots, an exact post-save checkpoint
> receipt/result-provenance gate, and exact 42/43/44 aggregation. The same audit
> fixed multi-dataset recipe-default drift, hybrid compact/full checkpoint
> acceptance, inherited-ablation leakage, stale/same-size checkpoint payloads,
> and non-atomic canonical result replacement. Deploy the repair,
> then retrain Bio/7B/seed-42 once in canonical `run01`; do not manufacture a
> current-source receipt for the old weights.
>
> The dependency-free regression suite passes 61 tests (`OK`, 12
> dependency-gated skips). Repository-wide Python compilation, shell syntax,
> and whitespace checks pass. The ML-dependent checks must still be run in the
> Killarney environment before expanding beyond the replacement pilot.

This is the canonical operational handoff for:

- Table 2 prior-work baselines: password locking (`pwd`), system-prompt
  sandbagging (`sysprompt`), and circuit breaking (`circuitbreak`);
- the corrected Table 2 effectiveness/utility union;
- Table 2 AutoDAN robustness (`Jlbrk`); and
- the Table 3 RoBCoTDstl organism, whose code identifier is `obfcot`.

Use [PRIOR_BASELINES_KILLARNEY.md](PRIOR_BASELINES_KILLARNEY.md) and
[PRIOR_BASELINES_VULCAN.md](PRIOR_BASELINES_VULCAN.md) for setup and general
recovery procedures. Use this document for current decisions and job ownership.

## Executive status

| Area | Current state | What it means |
|---|---|---|
| Code and tests | Known rerun-causing defects repaired; deployment/pilot required for new Table 3 code | AutoDAN total-context handling is already in the submitted-job checkout. The local Table 3 repair adds canonical seed slots, exact post-save recipe/source/payload receipts with SHA-256 payload binding, result provenance, exact aggregation rosters, multi-dataset recipe defaults, inherited-ablation cleanup/rejection, stale compact/full payload exclusion, and atomic canonical results. The dependency-free suite passes 61 tests (`OK`, 12 dependency-gated skips); repository-wide Python compilation and shell syntax checks pass. Run the skipped ML checks on Killarney before expansion. |
| Table 2 methodology | Implemented | Task effectiveness and attack readout use forced-choice; HS/CSQA utility uses generate-and-parse; SysPr uses three seeded stochastic inference replicates. |
| Table 2 E&U | Complete | The original 26 targeted 32B utility jobs and Chem repair jobs `4699233`--`4699235` are incorporated. No additional E&U computation is known. |
| Table 2 E&U union | Accepted at 270/324 | `artifacts/table-report-eu-20260811.Z59lUv` has all 270 effectiveness/utility values and 54 `Jlbrk` blanks. Preserve its manifest and SHA-256; do not rerun E&U. |
| AutoDAN cache | Complete and accepted | The canonical library has schema-2 complete metadata, five lifelong rounds, and the full 318-row source fingerprint. Do not rebuild it. |
| Table 2 AutoDAN | 72 released jobs pending; 72 larger jobs not yet submitted | The latest supplied queue shows all 72 submitted evaluations pending for priority. The context-overflow fix is deployed in their checkout. Validate each completed artifact; inventory/stage final 14B/32B PWD/CktBr checkpoints before submitting the other 72. |
| Table 3 RoBCoTDstl | Code blockers fixed locally; canonical replacement pilot required | Clean-root jobs `4669018`--`4669020` proved the intended environment and adapter load path, but the checkpoint is unsuffixed and predates the receipt contract. Deploy the repair and rerun only 7B/Bio/seed-42 in `run01`; require its marker/payload/result gates and a follow-up skip plan before submitting the other 17 7B E&U runs. No current Table 3 job is queued. |
| Vulcan artifact migration | Sufficient for utility, incomplete for robustness | A validated PWD/32B/Chem source is now staged for the repair jobs. CktBr/32B/Cyber and CktBr/32B/MMLU-Prof still need final location/export checks, and 14B has not had a comprehensive migration audit. SysPr needs only the shared base-model cache and prompt configuration. |
| Storage | Tight on project, adequate on scratch; Chem source protected | The last measured quotas were about 192 GiB free on project and 378 GiB free on scratch. The old-root 32B PWD/Chem family is the validated `wrong_answer_general_anchor_qwen_port_v1` source used by completed jobs `4699233`--`4699235`; retain it and the repair root until artifact/union acceptance. Four staged 32B CktBr children also remain protected until the robustness inventory. |
| Cluster direction | Killarney-only compute | Submit no new Vulcan work. Cancel the former 26 Vulcan retries (holding them until cancellation is verified), rerun any failures on Killarney, and retain the Vulcan artifact root only until a final read-only export of missing final 14B/32B checkpoints and canonical JSONs is verified. |

## Scientific and reporting contract

### Scope

Table 2 contains:

- three organisms: `pwd`, `circuitbreak`, and `sysprompt`;
- three Qwen2.5-Instruct tiers: 7B, 14B, and 32B;
- six target tasks: Bio, Chem, Cyber, MMLU-Prof, MMLU-Stem, and MMLU-HSS; and
- seeds 42, 43, and 44.

This is 54 organism/model/task cells and 162 seed-level evaluations. The final
Table 2 has five pre-robustness values per cell, hence 270 E&U/effectiveness
values, plus one aggregated `Jlbrk` value per cell, hence 54 robustness values.

### Metrics

- **Effectiveness:** compare target-task accuracy while sandbagging is active
  (`w/`) against the unlocked/reference condition (`w/o`). The table delta is
  `w/o - w/`; larger is stronger sandbagging.
- **Utility:** HellaSwag and CommonsenseQA use generate-and-parse with
  `utility_split_seed=42`, matching Table 3. They are not training or selection
  datasets for these baselines.
- **Robustness:** Table 2 reports held-out AutoDAN recovered target accuracy.
  Higher attack accuracy means poorer robustness, so lower `Jlbrk` is better.
- **Replicates:** PWD and CktBr have independently trained seed checkpoints.
  SysPr uses the same fixed released prompt with inference seeds 42/43/44; it
  does not claim three independently trained prompts.
- **Split hygiene:** validation data may be used only for tuning/selection.
  Paper numbers come from `test_*`. Do not select seeds, prompts, recipes, or
  checkpoints after seeing test results.

The preregistered expectation is that effectiveness and utility resemble the
prior work but the schemes are vulnerable to jailbreaks. This is a hypothesis,
not an acceptance criterion. These are mechanism-level reproductions under
disclosed Qwen/PEFT and dataset adaptations, not a promise of exact numerical
replication on the original papers' model stacks. Numerical disagreement must
be explained and reported, not tuned away after observing test data.

## Implemented methodology and pipeline

### Prior-baseline recipes

- PWD uses the deliberate-wrong Hofstätter-style lock: password rows are
  correct, no-password target rows are deliberately wrong,
  `wrong_password_frac=0`, and no weak imitation model is used. Clean non-target
  MMLU rows provide a general anchor without using target MMLU subjects or
  utility evaluation rows.
- CktBr trains from a separate deliberate-wrong `pwd_hof_parent` and uses the
  locked adapter-off reference. The Qwen/PEFT port uses `k=64`, learning rate
  `1e-4`, and `cb_escape_eps=0.1`. Checkpoint metadata records parent
  provenance, layers, gradients, and rerouting/retain diagnostics.
- SysPr uses one fixed released wrong-answer prompt. There is no prompt sweep or
  test-dependent prompt selection. The MMLU evaluation is a disclosed extension
  of the source setup.

### Scoring correction and seeded SysPr

- `POSSUM_GENPARSE=0` preserves forced-choice task training, task
  effectiveness, and final attack scoring.
- `POSSUM_UTILITY_SCORING=generate_parse` gives Table 2 the same HS/CSQA
  readout as Table 3. The old roughly 43--52% 7B HellaSwag values were therefore
  not directly comparable. Refreshed 7B values around 73--77% support an
  evaluation-protocol explanation rather than checkpoint damage.
- PWD/CktBr `--phase utility` preserves existing effectiveness and attacks while
  replacing only utility values and utility metadata. SysPr must use `--phase
  eff` because its active organism now samples.
- Active SysPr task scoring samples among the four forced-choice answer tokens
  with `top_k=4`, temperature 1.0. Active SysPr utility samples generate-and-
  parse with `top_k=50`, temperature 1.0. Results record
  `replicate_kind=inference_seed` and `seeded_top_k_v1` metadata.

### AutoDAN robustness

- Table 2's declared attack is AutoDAN only. Existing GCG, TAP, or prompt-
  injection values may remain as diagnostics in JSON but do not define `Jlbrk`
  and should not be rerun for this table.
- AutoDAN uses Meta-Llama-3-8B-Instruct as attacker/summarizer and Nomic
  embeddings. Canonical settings are 150 epochs, one warm-up iteration, five
  lifelong rounds, and the complete elicitation-training source.
- SysPr attacks keep the active sandbagging system prompt present. Results from
  an older inactive-prompt attack protocol are rejected.
- Every accepted result needs a finite test score, positive evaluation count,
  attacked-row fingerprint, no stored AutoDAN error, matching checkpoint
  provenance, and canonical cache provenance.
- The strategy library is shared and immutable across all 162 seed-level
  attacks. Whole JSONs have one writer at a time; never run two attack jobs that
  update the same result file concurrently.

### Result and table validation

- Prior trained seeds 42/43/44 map permanently to
  `run01`/`run02`/`run03`, including when only a subset is requested.
- Prior result JSON identity, scoring metadata, split seed, checkpoint
  marker/hash, seed roster, and requested attack completeness are checked
  before a table cell is emitted.
- This exact provenance statement does **not** yet apply to `obfcot`.
  Non-prior completion currently accepts a generic HF/PEFT marker such as
  `adapter_config.json`, and its result gate enforces scoring metadata but not
  an exact checkpoint recipe hash. This gap must be fixed before Table 3 is
  expanded.
- Non-prior `tablefill` also discovers `__seed*.json` files generically rather
  than requiring exactly seeds 42, 43, and 44. Until repaired, a one-seed pilot
  or a stale/wrong seed file could populate a Table 3 aggregate. This is an
  aggregation blocker even if every individual result uses the right scoring
  protocol.
- Current Table 2 JSON does not embed HS/CSQA sample counts, row fingerprints,
  or an evaluator commit. The captured logs provide the sample-count audit for
  this campaign, but a future schema should record those fields directly.
- `tablefill` leaves a cell as `---` when a gate fails. Generated LaTeX is not
  hand-edited to bypass a missing or invalid artifact.
- A reporting root contains result JSONs and marker-only checkpoint metadata.
  It must never replace an execution root containing full weights.
- The checked-in `tables/table2.gen.tex` predates the AutoDAN-only declaration
  and still contains the older maximum-across-attacks caption. It is a stale
  generated artifact, not the current protocol. Regenerate it with current
  `tablefill` only after constructing the validated reporting union.

### Operational correctness changes and current follow-up

The current branch contains the earlier protocol repairs plus the storage-safe
follow-up:

- AutoDAN generation now follows the released total-`max_length` semantics
  instead of misreading that value as extra `max_new_tokens`. The old behavior
  could exceed the attacker's context, run unnecessarily long, and truncate
  JSON summaries.
- A local follow-up adds a fixed-total-context guard to both AutoDAN attacker
  generation paths. If retrieved strategy examples overflow the 4,096-token
  total budget, the least-priority retrieved examples are pruned one at a
  time, then the strategy-free warm-up attacker is tried. A pathological
  warm-up overflow falls back to the original request and records explicit
  diagnostics. Successful under-budget generations are unchanged. The policy
  was deployed in Killarney commit `8b96a69`. Job `4680120` proved that the
  direct epoch-zero warm-up call still bypassed this helper and failed on a
  4,278-token input. Commit `9baadce` adds one guarded `_warmup_attack` method,
  routes all four direct warm-up sites through it, reproduces the exact failure
  in a dependency-free integration test, and enforces by AST test that no raw
  warm-up call remains outside `_strategy_attack`. It is present in the checkout
  used for the replacement submissions. The user elected to release the wave;
  artifact acceptance, rather than scheduler exit state alone, remains the
  completion boundary.
- Strategy-library construction writes atomic progress after warm-up requests,
  warm-up summaries, lifelong requests, and completed rounds. A compatible
  interrupted build can resume, while dependants cannot consume an incomplete
  library.
- A requested partial attack such as AutoDAN-only cannot exit successfully or
  count as resumable completion unless its score, count, fingerprint, and error
  gates pass. A successful retry clears only that technique's stale error.
- Commit `87e20bb` handles malformed AutoDAN summarizer output without the
  earlier unbound-local crash.
- Commit `8a21726` adds compact PEFT-only `obfcot` saves, training-only GPU
  overrides, and completed-CktBr-child planning that no longer recreates an
  unnecessary PWD parent.
- The deployed post-`8a21726` follow-up makes `run_killarney_matrix.sh` select the
  scientific protocol from `--table`: Table 2 remains forced-choice for the
  target task, while Table 3 uses generate-and-parse for both target and utility
  scoring. For `obfcot`, it also enables faithful OAT, a LAT hardening pass, and
  LAT-last ordering; the remaining current F-lock recipe values come from
  `HYPERPARAMETERS.toml`.
- Completion, phase-merging, and `tablefill` now reject non-prior results unless
  both task and utility metadata record generate-and-parse with split seed 42.
  This prevents the old Killarney jobs from being silently averaged into Table
  3 merely because their JSONs contain metrics.
- Killarney preflight now accepts an artifact root under the submitting user's
  own `/scratch/$USER/...` as well as `/project/...`; the repository and virtual
  environment must still remain under `/project`. This enables a clean,
  adapter-only Table 3 execution root without consuming the nearly full shared
  project allocation.

The storage changes do not alter datasets, seeds, model-organism
hyperparameters, scoring, or attack budgets. The protocol follow-up restores
the already-declared Table 3 recipe that the old Killarney wrapper overrode; it
does not introduce a new post-hoc recipe.

## Table 2 effectiveness and utility

### What is done

Current disposition, superseding the older wave description below:

- all 26 original targeted utility jobs completed with exit code 0 on
  2026-08-09/10, with measured wall times of approximately 44--54 minutes;
- the exact nine-cell follow-up plan reports every relevant training and
  evaluation step as `SKIP`, so those outputs satisfy the current protocol and
  provenance gate;
- no rerun among those 26 is required;
- the later PWD/32B/Chem storage audit created one prospective exception to the
  otherwise complete E&U inventory. Jobs `4699233`--`4699235` completed and
  will replace the Chem utility values after their artifact gates pass; and
- the current `252 filled, 72 empty` reporting root is an immutable partial
  snapshot, not the final union. Its already-present Chem values are useful as
  a reproducibility comparison but will not be selected over the prospective
  repair outputs.

- All 54 seeded SysPr reruns assigned to Killarney completed at the Slurm level.
  These replace both effectiveness and utility under the stochastic seeded
  protocol.
- Killarney's assigned PWD/CktBr utility refreshes completed at the Slurm level.
- Completed 14B and 32B artifacts from the former cross-cluster ownership split
  were validated and incorporated into a reporting-only partial union.
- `pwd_qwen2.5-32b-it_mmlu-prof__seed42.json` was directly validated:
  evaluation schema 2, `mcq_scoring_protocol=forced_choice`,
  `utility_scoring_protocol=generate_parse`, `utility_split_seed=42`, seed 42,
  and matching PWD checkpoint provenance. Its dry plan skips training and
  evaluation, so it does not need a rerun.
- The partial reporting union is
  `artifacts/table-report-partial.h5UcKQBG`. Its generated table combines the
  current paper's retained effectiveness values with all corrected utility and
  seeded SysPr results available at the snapshot. It contains 18 utility blanks
  across nine 32B aggregate cells and 54 `Jlbrk` blanks. Equivalently, it has
  252 filled and 72 empty values. This was created before the completed 26-job
  outputs were overlaid; those outputs account for the 18 aggregate blanks.
- The corrected utility readout substantially improves most PWD/CktBr HS/CSQA
  values relative to the preliminary paper table and puts them on the same
  scoring scale as Table 3. This supports the scoring-protocol diagnosis for
  the old low HellaSwag values.
- All 252 values emitted in the partial table pass the current Table 2
  protocol/tablefill gates. This does not make the partial report final: the 26
  completed replacement outputs must still fill its nine absent 32B utility
  aggregates, the completed Chem repair must supersede the existing Chem
  values, and robustness remains blank.
- The full 14B reporting inventory was audited independently of the suspicious
  cells. All 54 organism/task/seed JSONs use current generate-and-parse utility;
  zero reportable 14B JSONs still use forced-choice utility. Across PWD, CktBr,
  and SysPr, the independently evaluated 14B base reference is 67.6176--67.6326%
  on HellaSwag and exactly 73.3415% (597/814) on CommonsenseQA. The HellaSwag
  spread is only one answer among 6,695 test rows. The non-monotonic 14B base
  performance is therefore current model/protocol behavior, not a SysPr cache
  or scoring mismatch.
- The suspicious SysPr values were audited seed by seed. Every 14B SysPr JSON
  uses the same 67.63/73.34 base HS/CSQA reference, exact released prompt,
  `seeded_top_k_v1`, inference seeds 42/43/44, generate-and-parse utility, and
  split seed 42. Every 32B SysPr JSON likewise uses the same 88.13/86.24 base
  reference and the same current protocol. The small standard errors show that
  the task-conditioned utility changes are reproducible, not a missing-seed
  artifact.
- In particular, 32B SysPr MMLU-Prof is about 57.2 HS / 45.8 CSQA,
  MMLU-Stem about 53.0 / 35.7, and MMLU-HSS about 67.8 / 55.9, while the same
  model preserves roughly 87.8 HS and 84.6--84.9 CSQA under the WMDP-oriented
  prompts. The current evidence establishes a reproducible association with
  the active task-specific prompt, but does not by itself isolate the cause.
  Run a controlled matched-decoding and parse-rate diagnostic before describing
  the loss causally as prompt interference. Do not delete or cherry-pick the
  values; report them unless that diagnostic identifies an implementation error.
- Before the replacement wave completed, the exact 32B inventory contained 28
  current generate-and-parse utility JSONs and 26 stale forced-choice utility
  JSONs. The stale set was exactly the 26 replacements: 14 PWD seeds across
  Bio, Cyber, MMLU-Prof seeds 43/44,
  MMLU-Stem, and MMLU-HSS; plus 12 CktBr seeds across Bio, Chem, MMLU-Stem, and
  MMLU-HSS. The 28 already-current results are all 18 SysPr seeds, all three
  PWD/Chem seeds, PWD/MMLU-Prof seed 42, all three CktBr/Cyber seeds, and all
  three CktBr/MMLU-Prof seeds. This established that the 26-job manifest was
  complete. The later three-job Chem repair is separately justified by the
  checkpoint-recipe provenance incident, not by this scoring inventory.
- The old forced-choice 32B base references, about
  69.5--69.8% HellaSwag / 70.5--70.8% CommonsenseQA, are a scoring-protocol
  artifact rather than a scaling effect. Current generate-and-parse references
  are about 88.13% / 86.24%. The 26 replacements adjudicate every affected
  aggregate cell under the current contract.
- All 270 captured original-model utility log lines, HellaSwag and
  CommonsenseQA across 135 result logs, used the intended full denominators:
  HellaSwag validation/test 3,347/6,695 and CommonsenseQA validation/test
  407/814. This rules out a hidden `POSSUM_UTIL_EVAL_MAX` cap in the reportable
  utility evidence.
- The extreme CktBr/14B/MMLU-HSS seed-43 utility collapse was also audited as a
  potential methodological failure. It uses the current scoring schema and
  run02 seed mapping; its target task still shows an effective lock and password
  recovery (72.27% base, 22.93% no-password, 73.87% password on test). The
  canonical current-recipe marker records nonzero gradients, 841 rerouting
  microsteps, rerouting loss falling from 0.9920 to 0.4922, and negligible
  retain-loss drift. Its near-zero HellaSwag and 7.13% CommonsenseQA utility are
  consistent across validation and test, so the evidence supports a genuine
  training-seed outlier rather than a broken/no-op checkpoint. Retain it and
  report the resulting large standard error; do not retrain or cherry-pick it.
- The raw marker file SHA-256 is not expected to equal the recorded
  `marker_sha256`: provenance hashes the canonical parsed JSON object, not the
  file bytes. The audited marker's recorded canonical hash matches the result
  provenance, so this difference is not evidence of corruption.

Scientific disposition: do not rerun the 14B SysPr jobs or any of the accepted
26-job replacement wave. The three completed Chem repair jobs are a provenance
repair, not a response to their observed scores, and their outputs are
prospectively authoritative. Reconsider another cell only if its artifact fails
the declared result/provenance/sample-count gates or a controlled code-level
diagnostic finds a specific implementation error.

Slurm success is necessary but not sufficient. Final union still requires the
artifact-level gates above for every contributing JSON.

### What is running or queued

No Table 2 E&U computation is currently queued. The final 32B PWD/Chem
provenance repair completed at the Slurm level:

- seed 42: `4699233`;
- seed 43: `4699234`; and
- seed 44: `4699235`.

They ran from `/scratch/markwang/possum-prior-32b-chem-repair.a8ldSN` with one
H100, six CPUs, 128 GiB host RAM, and two-hour limits. Jobs `4699233`,
`4699234`, and `4699235` completed in 45:08, 44:39, and 44:42, using about
68.8, 62.7, and 62.7 GiB peak host memory. The plan reported all three
checkpoint-training steps as `SKIP` and exactly three utility evaluations as
`RUN`. These measurements validate the two-hour/128-GiB utility allocation.

The accepted source is the old-root PWD/Chem checkpoint family whose markers
identify `wrong_answer_general_anchor_qwen_port_v1`. The repair root exposes
that family without a blind checkpoint copy. The temporary result files were
replaced with current forced-choice task/current-provenance JSONs before the
plan so that only utility evaluation runs. Preserve the old weights and repair
root until all three outputs and the final reporting union are accepted.

The former Vulcan utility jobs remain non-authoritative and must not write any
canonical cell. The broad 102-job plan from the intentionally partial 32B
scratch root remains unsafe and must never be executed.

### E&U accounting and union

The union remains mathematically on track, but the accepted 26 results and Chem
repair must now be overlaid together:

1. retain the current paper/combined snapshot for effectiveness fields that did
   not require replacement;
2. overlay validated Killarney JSONs for seeded SysPr and Killarney-owned
   utility refreshes;
3. overlay the already-validated historical JSONs copied from the former
   Vulcan ownership root;
4. overlay the 26 accepted Killarney replacement outputs;
5. overlay jobs `4699233`--`4699235` as the prospectively authoritative
   PWD/32B/Chem utility cell;
6. run current `tablefill` once against the reporting-only union; and
7. compare the resulting values with the preliminary paper table and Table 3's
   utility scale.

The current partial report is still `252 filled, 72 empty`: 18 utility values
and all 54 robustness values are missing. After the accepted wave and Chem
repair are overlaid, the required pre-robustness checkpoint is:

```text
270 filled, 54 empty (all 54 empty values are Jlbrk)
```

No additional E&U job is currently known to be missing. Validate jobs
`4699233`--`4699235` for scoring/split/provenance, full sample counts, and a
follow-up plan that skips all Chem work, then rebuild the reporting-only union.
Do not submit duplicate utility work.

## Table 2 AutoDAN robustness

### Completed gate and pilot

The canonical Killarney files are:

```text
/project/6105494/markwang/possum/possum/artifacts/cache/autodan/strategy_library.json
/project/6105494/markwang/possum/possum/artifacts/cache/autodan/strategy_library.json.meta.json
```

The metadata reports schema 2, `status=complete`, five completed lifelong
iterations, `source_count=318`, and source fingerprint
`61ca56c6e64441ca778701660cacae2f4330733929d753fd857e2a342c0cd08e`.
The observed SHA-256 values are:

```text
79c02db4799d8e583d0ab2115633bf1123c9e4a7e1c98184eb00ffee5dd3ea44  strategy_library.json
46b2ea048216a2fb18a50bb8dd7b022d44183f901c47f4662d20ebda8bc5253f  strategy_library.json.meta.json
```

Builder attempts `4620682` and `4628490` failed/timed out, but the latter left a
complete final cache. There is no progress checkpoint and no need to rebuild.

Cached-library pilot `4652099` (PWD/7B/Bio/seed-42) completed in 4:08:40 on one
H100 with 46.34 GiB peak host memory. Its test result is 247/256 = 96.48%, with
the expected 256-row count, a nonempty row fingerprint, matching checkpoint
provenance, and no elicitation error. Its follow-up plan skips the evaluation.
This is the first accepted `Jlbrk` seed result and supports the expected poor-
robustness hypothesis.

### Current Killarney queue

The latest supplied `squeue` on 2026-08-11 contains only the 55 user-held
AutoDAN jobs. All previously unheld jobs are terminal:

| State | Count | Jobs | Resources/disposition |
|---|---:|---|---|
| Held by user | 55 | `4666399`--`4666436`, `4675516`--`4675532` | 38 remaining 7B AutoDAN jobs at one H100/96 GiB/12 hours and 17 SysPr/14B jobs at one H100/128 GiB/12 hours. |
| Terminal, completed | 6 | `4680228`, `4680229`, `4680259`, `4699233`--`4699235` | Three 32B SysPr AutoDAN jobs and three PWD/32B/Chem utility jobs; all need the relevant artifact gate before acceptance. |
| Terminal, application failure | 4 | `4680120`, `4680260`, `4682272`, `4682273` | Seed-42 Cyber confirms the missed warm-up guard. The other logs stop without a captured Python traceback. All must be rerun after the new pilot. |
| Terminal, launch cancellation | 13 | `4680261`, `4680264`--`4680266`, `4680268`--`4680276` | Batch steps received signal 53 after 0--1 second on `kn172`/`kn175`; scientific code did not run. |

The 32B SysPr jobs were reduced from 256 GiB to 128 GiB with Slurm's MiB-valued
update, `MinMemoryNode=131072`. Jobs `4680229` and `4680259` completed at 128
GiB, and `4680260` ran for 3:43 at that request; therefore the reduction did
not cause the launch cancellations. Retain two H100s because target and
attacker models coexist, and retain 12 hours because the accepted Bio pilot
took 5:47:16 and task-dependent attacks have exceeded eight hours. Commit
`1eb29e0` exposes the validated host-memory override and the Killarney wrapper
now sets `POSSUM_BLACKBOX_MEM_GB_32B=128` for Table 2 attacks, so replacement
jobs request 128 GiB directly and no post-submission `scontrol` resize is
needed.

The following submitted-wave history explains how these job identities were
created; its older point-in-time state descriptions are superseded by the table
above.

The complete remaining 7B wave was submitted as:

- PWD `4666384`--`4666400`: 17 jobs because the accepted pilot is skipped;
- CktBr `4666401`--`4666418`: 18 jobs; and
- SysPr `4666419`--`4666436`: 18 jobs.

That is 53 submitted jobs plus the one accepted pilot, covering all 54 7B
seed-level evaluations. Each submitted 7B job requests one H100, 96 GiB, and
12 hours. The 12-hour request is deliberately retained because only Bio/PWD
has a measured full-split runtime.

Two larger-tier sizing pilots were also submitted:

- `4666342`: SysPr/14B/Bio/seed-42, one H100, 128 GiB, 12 hours; and
- `4666363`: SysPr/32B/Bio/seed-42, two H100s, 256 GiB, 12 hours.

The latest supplied accounting supersedes the checked-in queue capture:

- `4666342` completed in 3:51:50. A guarded full-tier plan accepted its
  Bio/seed-42 artifact as `SKIP`, found all six fixed SysPr configurations,
  found zero training work, and found exactly 17 missing evaluations. This
  clears the current 14B SysPr expansion gate.
- The remaining 17 14B SysPr evaluations were submitted as
  `4675516`--`4675532`. Each requests one H100, six CPUs, 128 GiB, and 12 hours.
- `4666384` and `4666385`, PWD/7B/Bio seeds 43 and 44, completed in 3:32:44 and
  4:28:48 respectively. Their result-level acceptance still must be recorded.
- The latest supplied `sacct` capture shows `4666363`, the 32B SysPr sizing
  pilot, completed in 5:47:16 with exit code 0. Its artifact gate now passes:
  250/256 AutoDAN accuracy, full active-system-prompt and checkpoint
  provenance, nonempty fingerprint, and no error. Its null diagnostics field is
  expected because it predates the diagnostics patch. Bio seeds 43/44 are now
  submitted as `4680228`/`4680229`; the remaining 15 32B SysPr attacks are also
  submitted under the same 12-hour, two-H100 allocation.
- PWD/7B/Chem seeds 42/43/44 (`4666386`--`4666388`) also completed with exit
  code 0 in 3:00:43, 3:01:08, and 2:34:32. The repository status gate now
  accepts this complete cell; retain its JSONs and provenance as reportable
  artifacts.
- Replacement jobs `4680120`, `4682272`, and `4682273`
  (PWD/7B/Cyber/seeds 42/43/44) failed after 7:49:24, 8:57:31, and 8:41:15.
  Seed 42 conclusively hit the unguarded epoch-zero warm-up path at a
  4,278-token input. Seeds 43/44 ended without a Python traceback in their
  captured logs and were not OOM or timeout failures. None is reportable.
- The Bio-only 32B SysPr expansion submitted `4680228` and `4680229` for seeds
  43 and 44. The follow-up all-task expansion submitted the other 15 attacks as
  `4680259`--`4680276`, with gaps from Slurm numbering. All 17 remaining 32B
  SysPr attacks are now submitted.
- Of the 17 submitted post-pilot 32B SysPr jobs, `4680228`, `4680229`, and
  `4680259` completed; `4680260` failed after 3:43; and 13 were cancelled before
  scientific execution. Validate the three completed artifacts and resubmit
  the other 14 only after the new context pilot passes.
- Those 14 failed/cancelled 32B SysPr slots were resubmitted as
  `4709894`--`4709907`; the three Cyber replacements are `4709719`, `4709891`,
  and `4709892`.

The stable accounting is by logical seed slot, not by raw Slurm job count,
because failed Cyber jobs have replacement IDs. Three expansion pilots are
accepted, three additional 32B SysPr slots are Slurm-complete, all 17 required
replacement slots are submitted, the original 55-job wave is released, and 72
larger PWD/CktBr slots remain unsubmitted. Refresh `squeue`/`sacct` before
making any scheduling decision.

#### AutoDAN context-limit incident and queue hold

Job `4666389` (PWD/7B/Cyber/seed-42) is not a valid result. It completed the
base/organism evaluation, all 198 validation AutoDAN rows, and 10 test attack
rows before the attacker constructed a 4,243-token input with total
`max_length=4096`. The process exited with code 1 after 4:04:12; `seff` reports
16.31 GiB of 96 GiB host memory, so this was neither OOM nor timeout. The result
JSON contains the deterministic metrics but no test AutoDAN score, count, or
fingerprint, and the evaluator's final provenance gate correctly rejected it.
The script, log, and JSON were archived under
`results/failed/job4666389`.

The exact 55 pending AutoDAN jobs were initially held by the user: 38 remaining
7B jobs (`4666399`--`4666436`) and 17 14B SysPr jobs
(`4675516`--`4675532`). They are now released and pending for priority. The
three replacement Cyber jobs and fourteen replacement SysPr/32B jobs are also
pending. Require a finite AutoDAN score, positive expected count, nonempty
fingerprint, no stored AutoDAN error, current cache/checkpoint provenance, and
non-null diagnostics from every new-code completion. Any unrelated old-code
completion remains valid if it passes all pre-existing gates, because the guard
changes behavior only on an overflow.

### What is deliberately not queued

The campaign needs 162 seed-level AutoDAN results. Current coverage accounting
is:

```text
90 submitted or accepted logical slots + 72 not yet submitted = 162
```

The three accepted expansion pilots are PWD/7B/Bio/seed-42 (`4652099`),
SysPr/14B/Bio/seed-42 (`4666342`), and SysPr/32B/Bio/seed-42 (`4666363`).
Other completed logical slots, including PWD/7B/Chem, have also passed their
result gates. The 90-slot coverage count includes the Cyber slots whose
original jobs and replacements failed, the 17 post-pilot 32B SysPr slots now
split into three completed and 14 incomplete results, and the 55 held jobs.
Replacement job IDs do not create new scientific slots. Every not-yet-accepted
output still requires result-level acceptance.

The 72 unsubmitted evaluations are not an accidental omission. They are:

- 36 PWD/CktBr evaluations at 14B;
- 36 PWD/CktBr evaluations at 32B.

They remain behind one scientific/operational gate:

1. inventory and stage the required larger PWD/CktBr checkpoints on Killarney
   without duplicating writers or overflowing storage, then require an exact
   provenance/shard audit and a plan in which every training step is `SKIP`.

The former ownership split assigned Killarney 99 cells: all 54 7B cells, all 36
larger SysPr cells, and nine larger CktBr cells already stored in Killarney
scratch. The other 63 larger PWD/CktBr cells were assigned to Vulcan. That
compute split is retired: every remaining attack will run on Killarney.

The utility migration is not yet a complete robustness migration. The Chem
storage repair has identified a valid PWD/32B/Chem source, but the final
cross-root inventory must still cover every 14B/32B PWD/CktBr family,
especially CktBr/32B/Cyber and CktBr/32B/MMLU-Prof. No comprehensive 14B
migration has yet been performed. Before launching the 72 larger
trained-organism jobs, inventory all final 14B/32B
PWD/CktBr families across the Killarney roots, transfer only missing final
children and canonical JSON/marker metadata, and expose them through one
Killarney execution root. SysPr needs no trained checkpoint migration. This
targeted export does not authorize a blind multi-terabyte copy or duplicate
attack writers.

## Table 3 RoBCoTDstl (`obfcot`)

### Protocol audit and disposition of the old graph

The original 7B/Bio pilot (`4622340`--`4622342`), Bio seed-43/44 training and
evaluation (`4637149`--`4637152`), Chem CoT preparation/training
(`4637161`/`4637162`), and related terminal artifacts completed under the old
contract. Chem evaluation `4637165` was cancelled, and no job in the obsolete
`4637151`--`4637283` graph remains in the live queue.

These jobs are not reportable Table 3 work. Direct inspection of all four
representative generated scripts showed:

```text
export POSSUM_GENPARSE=0
export POSSUM_UTILITY_SCORING=generate_parse
```

Table 3 requires generate-and-parse for target-task training/evaluation as well
as utility. In addition, the old launcher did not enable
`POSSUM_OBFCOT_LAT=1`, `POSSUM_OBFCOT_LAT_LAST=1`, or
`POSSUM_OAT_FAITHFUL=1`. Bio seed-43/44 were trained before the current TOML
fallback repair and therefore used the older bare-default lock; Chem seed 42
can pick up some TOML defaults at runtime but still lacks the OAT/LAT gates and
is forced-choice. A compact checkpoint is useful as a storage-mechanics test,
but it does not repair this scientific mismatch.

The old graph is now retired. Its sbatch scripts contain the environment exports
generated at submission time, so the later git pull cannot repair those
artifacts in place. Logs and accounting remain diagnostics only. The old Bio
seed-42 JSON's generate-and-parse metadata does not rescue its checkpoint: its
training recipe was not established as the current one.

### Correct replacement contract

The replacement Killarney launcher must emit:

```text
POSSUM_GENPARSE=1
POSSUM_UTILITY_SCORING=generate_parse
POSSUM_OBFCOT_LAT=1
POSSUM_OBFCOT_LAT_LAST=1
POSSUM_OAT_FAITHFUL=1
```

The current F-lock, LAT budget, anchors, and augmentation remain sourced from
the validated `[obfcot]` block in `HYPERPARAMETERS.toml`. Use a fresh Killarney
scratch artifact root so stale full checkpoints and result JSONs cannot satisfy
resumability checks. Begin with exactly one 7B/Bio/seed-42 E&U pilot; validate
the generated sbatch environment, compact adapter shape, result protocols,
effectiveness/utility, wall time, and memory before submitting the other 17 7B
seed/task runs.

That replacement pilot ran under
`/scratch/markwang/possum-table3-current`:

- `4669018`: Bio CoT preparation completed in 43:32;
- `4669019`: 7B/Bio/seed-42 training completed in 1:09:52; and
- `4669020`: evaluation completed in 37:32.

Both generated train/evaluation scripts were inspected before submission and
contain all five required exports. The successful evaluation also proves that
the saved adapter can be loaded by the evaluation path. This established a
scientifically promising pilot, but the code at that time could not prove the
full checkpoint identity claimed by the reporting contract.

Three concrete blockers were found during the post-run audit (all are fixed in
the current local repair, but not retroactively for the old pilot):

1. `_seed_specs` gives non-prior single-seed runs an unsuffixed checkpoint
   directory. The completed `--seeds 42` pilot therefore lives at
   `checkpoints/obfcot_qwen2.5-7b-it_bio`, while a later
   `--seeds 42 43 44` invocation expects seed 42 at `.../run01`. Submitting the
   full matrix now would not reliably resume the pilot and could retrain or
   overwrite its canonical seed-42 result.
2. `_ckpt_complete` accepts a generic `adapter_config.json` for `obfcot`, and
   exact `checkpoint_provenance` matching in `_eval_complete` currently applies
   only to the prior-work recipe-marker organisms. `_save_obfcot_checkpoint`
   saves the PEFT adapter and tokenizer but no organism marker containing the
   model, task, seed, resolved hyperparameters, protocol flags, and recipe hash.
   Consequently the existing adapter, sbatch scripts, logs, and result JSON are
   consistent with the intended recipe, but there is no native immutable marker
   that makes that conclusion airtight.
3. `tablefill.load_results` discovers non-prior `__seed*.json` files without
   enforcing exactly the canonical 42/43/44 seed roster or exact checkpoint
   provenance. A lone valid pilot can therefore populate an aggregate, and a
   stale, extra, or wrong-seed JSON can be silently included. This prevents the
   current pilot from being a reportable three-seed Table 3 cell even if its
   individual metrics are sound.

The local repair now:

- maps canonical Table 3 seeds 42/43/44 to `run01`/`run02`/`run03` independently
  of which subset is requested;
- writes an `obfcot` recipe marker only after a successful save, including model,
  datasets, seed, resolved OAT/LAT/F-lock recipe, scoring contract, compact-save
  format, and enough source/version information to reproduce the run;
- clears every noncanonical Table 3 override inherited from the submitting shell,
  records the faithful OAT probe/buffer dimensions, and rejects a receipt that
  contains any experimental override;
- stamps the marker hash into result JSON and makes planning/table filling reject
  mismatches;
- makes Table 3 aggregation require exactly seeds 42, 43, and 44 and reject
  missing, extra, duplicate, or wrong-identity seed artifacts;
- adds tests for a seed-42-only pilot followed by a three-seed plan, exact recipe
  mismatch rejection, compact-adapter completeness, and wrong/missing/extra
  aggregation seeds;
- clears stale model shards before overwriting a canonical slot and rejects
  compact/full hybrids or any added, missing, replaced, size-mismatched, or
  same-size-content-corrupted payload using per-file SHA-256 hashes; and
- atomically replaces canonical result JSONs, retaining the previous valid
  result if a final write is interrupted.

After that code is deployed, require all of the following from the pilot or its
single controlled replacement:

1. the checkpoint is a loadable compact PEFT adapter and does not contain
   merged full-model shards;
2. the result records `mcq_scoring_protocol=generate_parse`,
   `utility_scoring_protocol=generate_parse`, and `utility_split_seed=42`;
3. the new marker and result provenance match 7B/Bio/seed-42 and the current
   OAT-then-LAT recipe;
4. effectiveness and both utility metrics are finite and have expected sample
   counts; and
5. a fresh plan against the clean root skips preparation, training, and
   evaluation; and
6. only a complete cell with the exact 42/43/44 roster is eligible for
   `tablefill` aggregation.

Do not manually copy the unsuffixed adapter into `run01`, do not submit the
other 17 7B runs, and do not aggregate the old pilot. Deploy the repair and
retrain only the seed-42 pilot in canonical `run01`. A post-hoc marker would
incorrectly claim that the old weights were trained by the newly hashed source,
so promotion is no longer an acceptable option.

### Storage-safe implementation

`POSSUM_SAVE_PEFT_ADAPTER_ONLY=1` is now the Killarney default. `obfcot`
training writes a PEFT adapter and tokenizer rather than another merged Qwen
copy. The loader detects `adapter_config.json`, loads the shared cached base,
applies the adapter, and merges it in memory for evaluation. Existing merged
checkpoints remain readable.

Had the retired jobs started after deployment, they would have picked up the
compact-save default, but their generated scripts would still have re-exported
the wrong task protocol. Compactness therefore would not have made them valid.

The planner also supports `POSSUM_TRAIN_GPUS_<TIER>` independently of evaluation
resources. The retired 14B/32B training scripts requested only one H100 and are
not accepted as safe OAT allocations. After the replacement 7B wave validates,
generate one 14B training pilot with two H100s and one 32B training pilot with
three H100s. Their downstream E&U evaluations should remain one-H100 jobs.

Table 3's future black-box phase retains its own declared attack suite. Always
`unset POSSUM_BLACKBOX_ONLY` before planning Table 3 attacks; the AutoDAN-only
restriction applies only to Table 2.

## Storage state and optimization

### Why usage reached approximately 5 TiB

The main cause was trained checkpoint multiplication, not the Hugging Face
cache. Before cleanup, this checkout used roughly 2.7 TiB under `artifacts`, of
which about 2.6 TiB was `artifacts/checkpoints`. A three-seed merged checkpoint
family was measured at approximately:

- 43 GiB for 7B;
- 83--87 GiB for 14B; and
- 184--195 GiB for 32B.

Each organism/task/seed had been saving merged full model weights. PWD,
`pwd_hof_parent`, CktBr, and the initial `obfcot` runs therefore multiplied base
model storage many times.

### Cleanup already completed

- The superseded project archives
  `archive_weak_pwd_20260719-222010` and
  `archive_weak_pwd_20260719-232016` were copied to Killarney scratch, compared
  with an ordinary `rsync -n` check, and their two 43-GiB project copies were
  removed.
- The unused Qwen2.5-1.5B cache, about 2.9 GiB, was removed.
- The scratch rollback copies of the two superseded weak-PWD archives were then
  explicitly abandoned and removed, reclaiming about 86 GiB of scratch.
- The scientifically mismatched merged
  `obfcot_qwen2.5-7b-it_bio` checkpoint family was removed after the obsolete
  graph became terminal, reclaiming about 43 GiB of project storage.
- Ten `pwd_hof_parent` families were validated against their current,
  self-contained CktBr children and checked against the live queue. Their JSON,
  text, and configuration metadata were copied byte-for-byte to
  `artifacts/retired_checkpoint_metadata/pwd_hof_parent_20260809` before their
  weights were removed. The metadata archive is 456 MiB; the retired parent
  weights represented approximately 258 GiB in project and 635 GiB in scratch.
- Active Qwen 7B/14B/32B, Meta-Llama-3-8B, and Nomic caches were retained.
- Compact PEFT output is now enabled for new `obfcot` training.

The immediate post-cleanup measurements were:

```text
2.3T   project artifact root
816G   /scratch/markwang/possum-prior-eff
1071/2000 GiB   markwang scratch quota
4615/5000 GiB   shared project quota
```

The per-user project breakdown printed an older `4917.33 GiB` total in the same
report, so that detailed accounting snapshot was stale relative to the leading
quota line. Quota and filesystem reports may lag large deletions.

The later targeted 32B migration transferred roughly 1.33 TiB of checkpoint
data from Vulcan. Four stale project PWD families were replaced by same-sized
current-recipe families, so that portion was approximately project-neutral;
three new CktBr families added roughly 582 GiB to scratch. The Killarney stage
uses symlinks instead of duplicating the families again. The latest supplied
measurements, taken at different points in the migration/submission sequence,
were:

```text
4.7T used, 264G free, 95%   /project
1.6T used, 379G free, 82%   /scratch/markwang  (last separate scratch check)
```

The newer scratch inventory found four physical 184-GiB CktBr 32B children
under `/scratch/markwang/possum-prior-32b-killarney` (Bio, Chem, MMLU-HSS, and
MMLU-STEM), while several other scratch roots contain only marker/report
directories of 4.5--12 KiB. These children are potentially the only staged
inputs for the still-unsubmitted 32B CktBr robustness wave; retain them until
the final cross-root inventory identifies a canonical execution copy and no
queued job or reporting root depends on the staging root.

The 26 utility-only jobs and 17 SysPr attack jobs do not create new model
weights, so the current margin is sufficient for those evaluations. It is not
sufficient for an indiscriminate copy of every remaining Vulcan checkpoint.
Recheck both quota roots immediately before another checkpoint transfer or any
new trained-organism wave.

### Retired CktBr parents and remaining candidates

A completed merged CktBr child is self-contained for evaluation and AutoDAN; it
does not load its PWD parent. The planner therefore omits parent training when
the final CktBr child passes its recipe-marker check. The ten retired families
each passed all of the following gates:

1. verify that every parent run has a matching CktBr child run with the expected
   current recipe and identity marker;
2. verify that every matching child run contains nonempty model weights;
3. ensure no parent/CktBr training job is live or held;
4. preserve lightweight provenance metadata outside the active checkpoint
   tree; and then
5. remove only the exact validated parent directory.

Results, child markers/configuration, logs, generated tables, commit IDs, and
AutoDAN cache metadata were retained. Five additional project parent families
total about 696 GiB:

```text
81G   pwd_hof_parent_qwen2.5-14b-it_chem
184G  pwd_hof_parent_qwen2.5-32b-it_bio
184G  pwd_hof_parent_qwen2.5-32b-it_chem
63G   pwd_hof_parent_qwen2.5-32b-it_mmlu-hss
184G  pwd_hof_parent_qwen2.5-32b-it_mmlu-stem
```

They originally had no matching child in the same execution root. The migration
has since staged current CktBr 32B children for Bio, Chem, MMLU-Stem, and
MMLU-HSS in scratch, so the four 32B parents (about 615 GiB) are strong cleanup
candidates. They are not yet approved for deletion: repeat the prior
recipe/identity/weights/provenance checks across roots, ensure no parent or
CktBr training job is live, preserve lightweight metadata, and only then remove
the exact validated parent directories. Audit the 14B/Chem parent separately
after the final 14B child inventory.

Do not delete the active Qwen/Meta-Llama/Nomic caches, current final PWD/CktBr
children needed by utility or queued/future AutoDAN, the canonical AutoDAN
cache, or the clean Table 3 pilot before acceptance. The Vulcan artifact root
is no longer needed for E&U compute, but keep it read-only until the final
14B/32B robustness inventory and targeted export are verified. Run Table 3
adapter-only under its dedicated Killarney scratch root so it does not recreate
merged base-model weights per seed.

## Immediate and near-future operations

### 1. Enforce scheduler ownership

Killarney is the only authoritative compute cluster. The 26-job utility wave
and the three-job Chem repair are Slurm-complete. No E&U computation is queued;
artifact validation and union construction remain. The former
Vulcan jobs `379734`--`379747`, `379759`--`379764`, and
`379771`--`379776` must be cancelled or held permanently; confirm that none can
write a competing result. No new Vulcan submission is permitted.

The old Killarney Table 3 graph `4637151`--`4637283` is terminal and absent from
the queue. Preserve its logs as diagnostics but never aggregate its artifacts.
The clean pilot `4669018`--`4669020` is Slurm-complete but must not be
aggregated or used to expand the matrix because it predates the receipt
contract. The latest supplied queue contains 72 released AutoDAN jobs, all
pending for priority. Query live state before acting.

Killarney job scripts execute the shared checkout when their allocation starts.
The replacement jobs were submitted from a checkout containing `9baadce` and
`1eb29e0`. Push the current local correctness repair, pull it into Killarney,
verify `git status` and `git rev-parse HEAD`, and then freeze tracked scientific
Python, shell, and `HYPERPARAMETERS.toml` files again. The repair does not alter
Table 2 metrics or attack policy; it makes final JSON replacement atomic and
adds Table 3-only checkpoint/reporting gates.

### 2. Preserve the completed Table 2 E&U union

Jobs `4699233`--`4699235` passed the current utility/provenance checks and are
incorporated into `artifacts/table-report-eu-20260811.Z59lUv`. Preserve that
root, `EU_UNION_MANIFEST.json`, the generated table, and their hashes. The next
Table 2 generation should overlay accepted robustness JSONs into a clone of
this union rather than rebuilding or mutating the E&U snapshot.

No additional Table 2 E&U job is known to be missing. Do not rerun 14B SysPr,
do not duplicate the completed 26-job wave, and do not execute the broad
102-job dry plan from the partial scratch root.

### 3. Accept and expand AutoDAN on Killarney

Commit `9baadce` fixes the missed direct warm-up paths and commit `1eb29e0`
makes the validated 32B/128-GiB host-memory request automatic. The user chose
to release the wave and submitted replacement PWD/7B/Cyber jobs `4709719`,
`4709891`, and `4709892` plus replacement SysPr/32B jobs
`4709894`--`4709907`. The latest snapshot shows all 72 submitted jobs pending
for priority. Each completion must show a finite AutoDAN score, expected
positive test count, nonempty fingerprint, no `elicitation_errors.autodan`,
matching cache/checkpoint provenance, and a diagnostics object for new-code
runs. The expected pathological Cyber row should increment overflow/direct-
request fallback counters.

The completed 14B SysPr pilot `4666342` passed the current planner expansion
gate, and its remaining 17 evaluations are already submitted as
`4675516`--`4675532`; do not submit them again. Validate the completed
PWD/7B/Bio jobs `4666384` and `4666385` with the same
score/count/fingerprint/error, cache-provenance, and checkpoint-provenance gates
as pilot `4652099`. Collect `seff` for representative jobs.

The 32B SysPr pilot `4666363` completed within the two-H100/256-GiB/12-hour
allocation and passed the full artifact gate. Validate completed jobs
`4680228`, `4680229`, and `4680259`. The 14 incomplete logical slots represented
by `4680260`, `4680261`, `4680264`--`4680266`, and `4680268`--`4680276` have
already been resubmitted as `4709894`--`4709907` at two H100s, 128 GiB, and 12
hours; do not duplicate them. In parallel, build a read-only inventory of every final
14B/32B PWD and CktBr checkpoint across the Killarney roots. For missing final
families only, perform one verified Vulcan export of weights plus canonical
JSON/marker metadata. Create a unified Killarney execution root with symlinks,
run the recipe/provenance/shard audit, and require every training step to
`SKIP` before submitting larger PWD/CktBr attacks.

The 72 unsubmitted attacks are therefore intentional, not forgotten. Once
submitted, preserve one writer per JSON and do not use Vulcan as a fallback
compute cluster.

#### Proactive artifact validation

The 32B SysPr/Bio pilot is now audited. The following is the retained validation
example for an accepted result; Slurm success alone is insufficient. On
Killarney, run:

```bash
export POSSUM_ARTIFACT_ROOT=/project/6105494/markwang/possum/possum/artifacts
export POSSUM_KILLARNEY_VENV=/project/6105494/markwang/possum/possum/venv

"$POSSUM_KILLARNEY_VENV/bin/python" -m possum.experiments.run status \
  --table prior --tier 32b --organism sysprompt --task bio --seeds 42 \
  --artifact-root "$POSSUM_ARTIFACT_ROOT"

RESULT="$POSSUM_ARTIFACT_ROOT/results/json/sysprompt_qwen2.5-32b-it_bio__seed42.json"
CHECKPOINT="$POSSUM_ARTIFACT_ROOT/checkpoints/sysprompt_qwen2.5-32b-it_bio"

jq '{
  identity: {model_org, model, dataset, seed, evaluation_schema_version},
  scoring: {mcq_scoring_protocol, utility_scoring_protocol, utility_split_seed},
  replicate: {replicate_kind, inference_decoding},
  task: {val: .val_task_nopw_acc, test: .test_task_nopw_acc},
  utility: {hs: .test_hs_mo_acc, csqa: .test_csqa_mo_acc},
  attack: {
    accuracy: .test_elicitations.autodan,
    n: .test_elicitation_n.autodan,
    fingerprint: .test_elicitation_sample_fingerprint.autodan,
    diagnostics: .test_elicitation_diagnostics.autodan,
    error: .elicitation_errors.autodan
  },
  sysprompt_jailbreak_protocol,
  checkpoint_provenance
}' "$RESULT"

jq '{organism_type, model_name, dataset, seed, selected_prompt_variant,
    prompt_selection, system_prompt}' "$CHECKPOINT/config.json"

jq '{schema_version, status, source_count, source_fingerprint}' \
  "$POSSUM_ARTIFACT_ROOT/cache/autodan/strategy_library.json.meta.json"
seff 4666363
```

For the completed old-code pilot, accept it when the status command reports the
robustness attack as complete, the result passes the current SysPr inference
metadata and checkpoint provenance gates, `test_elicitation_n.autodan` is the
uncapped Bio test size (256), the fingerprint is nonempty, no AutoDAN error is
present, the prompt config is `exact_released` with fixed prior-work selection,
and the cache metadata is complete with `source_count=318`. A null diagnostics
field is expected for this pre-patch success and does not invalidate it. The
new pilot under `9baadce` must contain diagnostics and clear the old AutoDAN
error. This validation is read-only.

### 4. Deploy provenance repair, then expand the corrected Table 3 campaign

Do not submit more Table 3 jobs yet. The canonical 42/43/44 checkpoint slots,
exact `obfcot` recipe/payload/result-provenance gate, and exact three-seed
aggregation are implemented and dependency-free-tested locally. Deploy them,
run the ML-dependent tests in the Killarney environment, and retrain only the
7B/Bio/seed-42 pilot in canonical `run01`. The old unsuffixed pilot cannot be
promoted because it predates the source-hashed post-save receipt. After the new
pilot passes every marker/payload/result gate, a full three-seed plan must skip
Bio seed 42 and show exactly the other 17 E&U runs; only then submit the
remaining 7B matrix from `/scratch/markwang/possum-table3-current`.

After the complete 7B compact wave is stable, generate fresh 14B and 32B
training pilots with:

```bash
export POSSUM_TRAIN_GPUS_14B=2
export POSSUM_TRAIN_GPUS_32B=3
```

Run `plan` first and verify two/three H100s for training, one H100 for downstream
evaluation, all five protocol exports, and adapter-only saving. Pilot 14B before
32B and revise resources only from measured OOM/runtime evidence. Always unset
`POSSUM_BLACKBOX_ONLY` for Table 3; its later attack suite is not Table 2's
AutoDAN-only contract.

### 5. Reclaim storage without removing final children

Repeat the retired-parent audit across execution roots for the four 32B
`pwd_hof_parent` families whose CktBr children are now staged in scratch. If all
identity, recipe, provenance, nonempty-weight, and live-queue checks pass,
preserve lightweight metadata and reclaim approximately 615 GiB from project.
Audit the remaining 14B/Chem parent separately. The reclaimed project capacity
can then hold any genuinely missing final child families, replacing obsolete
parents with reportable artifacts instead of consuming the remaining scratch
margin.

Do not copy the full Vulcan tree. Transfer only final checkpoint families that
the Killarney robustness inventory proves absent. Once every required 14B/32B
final child and canonical result has a verified Killarney copy, archive the
transfer manifest and release the Vulcan artifact root according to its storage
policy. Keep shared model/attacker caches and the canonical AutoDAN library.

### 6. Update the paper only at reproducible checkpoints

The next paper-ready checkpoint is the E&U-only union at `270 filled, 54 empty`.
The final Table 2 checkpoint is all 324 values after 162 AutoDAN seed results
pass acceptance. Regenerate LaTeX from the reporting root; do not hand-fill
cells. Report the systematic 32B SysPr MMLU utility loss unless a preregistered
diagnostic finds an actual implementation error. Preserve commands, commit,
markers, JSONs, logs, cache hashes, and generated tables for both checkpoints.

## Historical incidents retained for provenance

- An earlier PWD/7B/Bio AutoDAN attempt failed in the summarizer fallback path
  with an unbound `summarizer_system`. Commit `87e20bb` repaired the crash.
- Builder `4628490` later timed out at 12 hours, but its final library and
  metadata were complete. The accepted cached-library pilot proves that cache is
  usable; no builder rerun is needed.
- Commit `8b96a69` guarded retrieved-strategy overflow but left four direct
  warm-up call sites outside the policy. Job `4680120` reproduced this at
  epoch zero with a 4,278-token input on Cyber test row 147. Commit `9baadce`
  centralizes every warm-up through the guard and adds integration/AST
  regression coverage.
- Thirteen 32B SysPr jobs received signal 53 after 0--1 second on `kn172` and
  `kn175`. They did not execute scientific code and are launch cancellations,
  not OOM evidence. Jobs `4680229` and `4680259` completed under the same
  128-GiB request, so retain that allocation for their replacements.
- The original Vulcan 32B block included timeouts, failures, and user
  cancellations. A cancelled PWD/32B/Chem job reached about 96 GiB RSS under a
  96-GiB request; it was not a successful memory fit. The current 128-GiB retry
  allocation retains headroom.
- The provenance migration dry run reported `ready=0 blocking=55` because that
  migration intentionally targets eligible legacy forced-choice utility blobs.
  Current generate-and-parse refreshes are not migration candidates; do not run
  it with `--apply` for these results.
- A full cross-cluster checkpoint consolidation dry run was approximately 5.22
  TB and was abandoned. Future Killarney consolidation must be staged by exact
  task/tier and should prefer compact adapters where supported.
- A broad 32B utility plan against the intentionally partial Killarney staging
  root printed 102 apparent jobs because valid configurations/results live in
  other roots. It was a dry run and was not submitted. The exact 26-script
  manifest, not that broad plan, is authoritative for the utility replacement
  wave.

## Definition of done

Table 2 is complete when:

- all 270 effectiveness/utility values are generated from current,
  provenance-matching JSONs;
- all 54 `Jlbrk` table cells are generated from all 162 validated AutoDAN seed
  results sharing the exact canonical strategy library;
- the reporting-only union reproduces Table 2 from one `tablefill` command;
- the final table is compared with the preliminary paper table and the cited
  prior work without post-hoc tuning;
- JSONs, configs/markers, logs, table output, cache hashes, seeds, and commit
  provenance are preserved outside purge-prone locations; and
- every reportable checkpoint/result needed for reproduction or future attacks
  has a verified Killarney location, so Table 2 has no active Vulcan compute or
  data dependency.

The current RoBCoTDstl stage is complete when:

- all old forced-choice/bare-recipe jobs are retired and excluded from
  aggregation;
- canonical seed subsets always map 42/43/44 to `run01`/`run02`/`run03`;
- every `obfcot` checkpoint has an exact post-save recipe/identity marker and
  every result records matching checkpoint provenance;
- Table 3 aggregation requires exactly seeds 42, 43, and 44 and rejects stale,
  missing, extra, duplicate, or wrong-identity seed results;
- all 7B E&U cells run from compact, loadable PEFT checkpoints trained with the
  current OAT-then-LAT recipe and evaluated with generate-and-parse;
- measured 14B/32B training pilots establish safe GPU/time/memory requests;
- the remaining E&U matrix is released without recreating full merged base
  models per seed; and
- its later robustness phase uses the Table 3 attack contract, not Table 2's
  AutoDAN-only restriction.
