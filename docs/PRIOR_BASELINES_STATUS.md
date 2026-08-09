# Prior-baseline and RoBCoTDstl campaign status

> Canonical snapshot: 2026-08-09, after the latest live Killarney queue and
> direct inspection of its generated Table 3 sbatch scripts. Scheduler state
> changes continuously; verify with `squeue` and `sacct` before changing a job.
> Killarney is confirmed at commit `8a21726` and its focused suite passed all 48
> tests. A subsequent local working-tree repair makes the Killarney launcher
> table-aware and rejects mismatched Table 3 scoring artifacts; it must be
> tested (49 focused tests pass), committed, pushed, and deployed before any
> RoBCoTDstl resubmission.
> The checked-in cluster captures remain useful history, but the live checks in
> this document take precedence.

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
| Code and tests | Storage patch deployed; tested protocol follow-up local | Killarney pulled `8a21726` and passed 48 tests. The local follow-up passes 49 focused tests plus shell/diff checks; it routes Table 2 to forced-choice, Table 3 to generate-and-parse, enables the complete RoBCoTDstl OAT/LAT recipe, and adds result gates. Deploy it before replacing Table 3 jobs. |
| Table 2 methodology | Implemented | Task effectiveness and attack readout use forced-choice; HS/CSQA utility uses generate-and-parse; SysPr uses three seeded stochastic inference replicates. |
| Table 2 E&U | On track, waiting on Vulcan | Killarney's assigned reruns are Slurm-complete. Vulcan still has 26 32B retries queued for priority. No additional E&U submission is currently known to be missing. |
| Table 2 E&U union | Not yet generated | Wait for the 26 Vulcan jobs, validate both roots, overlay whole single-owner JSONs/markers into a reporting-only root, and run `tablefill`. The expected pre-robustness result is 270 filled values and 54 empty `Jlbrk` values. |
| AutoDAN cache | Complete and accepted | The canonical library has schema-2 complete metadata, five lifelong rounds, and the full 318-row source fingerprint. Do not rebuild it. |
| Table 2 AutoDAN | One accepted, 55 queued, 106 not yet queued | The accepted 7B pilot is 96.48%. Fifty-three remaining 7B jobs plus one 14B and one 32B SysPr sizing pilot are pending on Killarney. Larger-tier expansion is deliberately gated on the pilots and checkpoint staging. |
| Table 3 RoBCoTDstl | Protocol-blocked; do not release old graph | Every inspected old sbatch script embeds `POSSUM_GENPARSE=0`. The completed Bio checkpoints and running Chem pilot also lack the complete validated OAT-then-LAT recipe. They are diagnostics, not reportable Table 3 runs. Cancel any nonterminal old Table 3 jobs and relaunch from a clean adapter-only root after deploying the follow-up. |
| Storage | Improved but still constrained | Two superseded 43-GiB project archives and an unused 2.9-GiB model cache were removed. Compact `obfcot` saves prevent future full-model multiplication. Validated CktBr parents are the next major reclaim candidate. |
| Cluster direction | Finish Vulcan, then prefer Killarney | Do not discard the queue age of the 26 existing Vulcan E&U jobs. Avoid new Vulcan work; stage future robustness and Table 3 work on Killarney. |

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
not an acceptance criterion.

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

- Trained seeds 42/43/44 map to `run01`/`run02`/`run03`.
- Result JSON identity, scoring metadata, split seed, checkpoint marker/hash,
  seed roster, and requested attack completeness are checked before a table
  cell is emitted.
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
- The post-`8a21726` local follow-up makes `run_killarney_matrix.sh` select the
  scientific protocol from `--table`: Table 2 remains forced-choice for the
  target task, while Table 3 uses generate-and-parse for both target and utility
  scoring. For `obfcot`, it also enables faithful OAT, a LAT hardening pass, and
  LAT-last ordering; the remaining current F-lock recipe values come from
  `HYPERPARAMETERS.toml`.
- Completion, phase-merging, and `tablefill` now reject non-prior results unless
  both task and utility metadata record generate-and-parse with split seed 42.
  This prevents the old Killarney jobs from being silently averaged into Table
  3 merely because their JSONs contain metrics.

The storage changes do not alter datasets, seeds, model-organism
hyperparameters, scoring, or attack budgets. The protocol follow-up restores
the already-declared Table 3 recipe that the old Killarney wrapper overrode; it
does not introduce a new post-hoc recipe.

## Table 2 effectiveness and utility

### What is done

- All 54 seeded SysPr reruns assigned to Killarney completed at the Slurm level.
  These replace both effectiveness and utility under the stochastic seeded
  protocol.
- Killarney's assigned PWD/CktBr utility refreshes completed at the Slurm level.
- Vulcan's 14B work and already-existing 32B refresh artifacts provide the
  remainder that is not in the retry queue.
- `pwd_qwen2.5-32b-it_mmlu-prof__seed42.json` was directly validated:
  evaluation schema 2, `mcq_scoring_protocol=forced_choice`,
  `utility_scoring_protocol=generate_parse`, `utility_split_seed=42`, seed 42,
  and matching PWD checkpoint provenance. Its dry plan skips training and
  evaluation, so it does not need a rerun.

Slurm success is necessary but not sufficient. Final union still requires the
artifact-level gates above for every contributing JSON.

### What is running or queued

Vulcan has 26 pending 32B E&U retries, all currently waiting for priority:

- PWD: `379734`--`379747` (14 jobs); and
- CktBr: `379759`--`379764` and `379771`--`379776` (12 jobs).

They request four L40S GPUs, six CPUs, 128 GiB host RAM, and four hours. Four
L40S GPUs are more than the ordinary 32B E&U wrapper default, but the jobs have
already accrued queue age. Keep them queued. The 128-GiB request is justified:
an earlier user-cancelled 32B attempt reached `100659644K` (about 96 GiB) against
a 96-GiB request and did not complete, so it established that 96 GiB had no
headroom. Four hours is the current backfill compromise; three hours remains
unsupported until a comparable 32B job completes within it.

### E&U accounting and union

The three-way union remains mathematically on track:

1. retain the current paper/combined snapshot for effectiveness fields that did
   not require replacement;
2. overlay validated Killarney JSONs for seeded SysPr and Killarney-owned
   utility refreshes;
3. overlay validated Vulcan JSONs for its utility refreshes;
4. run current `tablefill` once against the reporting-only union; and
5. compare the resulting values with the preliminary paper table.

The latest historical combined report was `255 filled, 69 empty`. Once every
current E&U artifact is overlaid and before robustness is complete, the required
checkpoint is:

```text
270 filled, 54 empty (all 54 empty values are Jlbrk)
```

No additional E&U job is currently known to be missing. The immediate E&U
action is to preserve the existing queues and wait for Vulcan, then validate and
union—not to submit duplicate work.

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

Cached-library pilot `4652099`—PWD/7B/Bio/seed-42—completed in 4:08:40 on one
H100 with 46.34 GiB peak host memory. Its test result is 247/256 = 96.48%, with
the expected 256-row count, a nonempty row fingerprint, matching checkpoint
provenance, and no elicitation error. Its follow-up plan skips the evaluation.
This is the first accepted `Jlbrk` seed result and supports the expected poor-
robustness hypothesis.

### Current Killarney queue

The complete remaining 7B wave is queued for priority:

- PWD `4666384`--`4666400`: 17 jobs because the accepted pilot is skipped;
- CktBr `4666401`--`4666418`: 18 jobs; and
- SysPr `4666419`--`4666436`: 18 jobs.

That is 53 queued jobs plus the one accepted pilot, covering all 54 7B
seed-level evaluations. Each queued 7B job requests one H100, 96 GiB, and 12
hours. The 12-hour request is deliberately retained because only Bio/PWD has a
measured full-split runtime.

Two larger-tier sizing pilots are also queued:

- `4666342`: SysPr/14B/Bio/seed-42, one H100, 128 GiB, 12 hours; and
- `4666363`: SysPr/32B/Bio/seed-42, two H100s, 256 GiB, 12 hours.

At the latest snapshot all 55 jobs were pending for priority; no new AutoDAN job
was running.

### What is deliberately not queued

The campaign needs 162 seed-level AutoDAN results. Current accounting is:

```text
1 accepted + 55 queued + 106 not yet queued = 162
```

The 106 are not an accidental omission. They are held behind two gates:

1. validate runtime, memory, result provenance, and output completeness for the
   14B/32B sizing pilots; and
2. stage the required larger PWD/CktBr checkpoints on Killarney without
   duplicating writers or overflowing storage.

The prior ownership split assigned Killarney 99 cells: all 54 7B cells, all 36
larger SysPr cells, and nine larger CktBr cells already stored in Killarney
scratch. The other 63 larger PWD/CktBr cells were assigned to Vulcan. The new
operational preference is to move those future attacks to Killarney after the
current Vulcan E&U campaign finishes. This requires targeted checkpoint staging;
it does not authorize a blind multi-terabyte copy or duplicate attack writes.

## Table 3 RoBCoTDstl (`obfcot`)

### Protocol audit and disposition of the old graph

The original 7B/Bio pilot (`4622340`--`4622342`), Bio seed-43/44 training
(`4637149`/`4637150`), and Chem CoT preparation (`4637161`) all completed at the
Slurm level. The latest live queue then showed Bio evaluations `4637151` and
`4637152` plus Chem training `4637162` running, with Chem evaluation `4637165`
dependency-pending and the rest of `4637163`--`4637283` user-held.

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

Do not release any remaining job from the old Table 3 graph. Existing sbatch
scripts contain the environment exports generated at submission time, so a git
pull cannot repair them in place. Cancel every still-pending or running old
`prep_cot`/`train_obfcot`/`eval_obfcot` job, preserve logs for provenance, and
regenerate the campaign from a clean artifact root after deploying the launcher
follow-up. The old Bio seed-42 JSON's generate-and-parse metadata does not rescue
its checkpoint: its training recipe was not established as the current one.

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

### Storage-safe implementation

`POSSUM_SAVE_PEFT_ADAPTER_ONLY=1` is now the Killarney default. `obfcot`
training writes a PEFT adapter and tokenizer rather than another merged Qwen
copy. The loader detects `adapter_config.json`, loads the shared cached base,
applies the adapter, and merges it in memory for evaluation. Existing merged
checkpoints remain readable.

The old held jobs would pick up the compact-save default when they start, but
their generated scripts would subsequently re-export the wrong task protocol.
Compactness alone is therefore not a reason to release them.

The planner also supports `POSSUM_TRAIN_GPUS_<TIER>` independently of evaluation
resources. The old held 14B/32B training jobs request only one H100 and are not
accepted as safe OAT allocations. After the replacement 7B wave validates,
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
- Active Qwen 7B/14B/32B, Meta-Llama-3-8B, and Nomic caches were retained.
- Compact PEFT output is now enabled for new `obfcot` training.

The latest quota report after deployment showed `4912/5000 GiB` for the shared
project allocation, or roughly 88 GiB apparent headroom; quota reports may lag
deletions. Markwang scratch showed `1788/2000 GiB`. The copied superseded
archives still consume about 86 GiB in scratch and are optional rollback data;
do not remove them unless that rollback is explicitly abandoned.

### Next major reclaim candidate

A completed merged CktBr child is self-contained for evaluation and AutoDAN; it
does not load its PWD parent. The planner at `8a21726` therefore omits parent
training when the final CktBr child passes its recipe-marker check. Across the
observed project and scratch trees, validated parent copies may reclaim on the
order of 1.5 TiB.

This cleanup is not yet authorized as a bulk deletion. For each candidate:

1. verify that the matching CktBr child directory exists and has the expected
   current marker;
2. run the relevant `plan --phase blackbox` and confirm parent training is
   skipped;
3. ensure no live or held job depends on the parent; and
4. archive or remove only the exact validated parent directory.

Preserve results, marker/config files, logs, generated tables, commit IDs, and
AutoDAN cache metadata even when reclaiming model weights.

Two smaller candidates are available after their retention decisions are made:

- the copied superseded weak-PWD archives consume about 86 GiB in Killarney
  scratch and can be removed if their rollback purpose is explicitly abandoned;
  and
- the old merged `obfcot_qwen2.5-7b-it_bio` family consumes about 43 GiB in the
  project artifact root. Once every old Table 3 job is terminal and its small
  logs/results are preserved, those scientifically mismatched weights need not
  be carried into the clean replacement campaign.

Do not delete the active Qwen/Meta-Llama/Nomic caches, current PWD/CktBr child
checkpoints needed by queued AutoDAN, or any Vulcan checkpoint needed to finish
the Table 2 E&U union. Run the replacement Table 3 campaign adapter-only under a
dedicated Killarney scratch root; this avoids adding another merged-model tree
to the shared project allocation.

## Immediate and near-future operations

### 1. Keep Table 2 work queued; retire the old Table 3 graph

Leave all 55 current AutoDAN jobs queued and leave the 26 Vulcan E&U retries
queued. Do not shorten or requeue AutoDAN merely to chase backfill.

On Killarney, preview the nonterminal old Table 3 jobs by exact job-name family
and ID range, then cancel only that previewed set:

```bash
old_t3_ids=$(
  squeue -h -u "$USER" -o "%i|%j" |
    awk -F'|' '$1 >= 4637151 && $1 <= 4637283 &&
      $2 ~ /^possum_(prep_cot|train_obfcot|eval_obfcot)_/ {print $1}'
)
printf '%s\n' "$old_t3_ids"

# After verifying that every printed ID belongs to the obsolete Table 3 graph:
while read -r jid; do
  [[ -n "$jid" ]] && scancel "$jid"
done <<< "$old_t3_ids"
```

Terminal jobs will not appear and need no scheduler action. Preserve their logs
and accounting as diagnostics; do not use their checkpoints/results in Table 3.

### 2. Test and deploy the protocol follow-up

Run the focused suite and shell syntax check in the patched checkout. Commit and
push the follow-up, pull that exact commit on Killarney, and rerun the suite
there before submission:

```bash
python -m unittest tests.test_prior_baseline_protocol
bash -n clusters/run_killarney_matrix.sh
git diff --check
```

### 3. Generate and launch one clean 7B/Bio replacement pilot

Use a new scratch artifact root so no stale checkpoint/result can be skipped or
phase-merged:

```bash
export POSSUM_KILLARNEY_ROOT=/project/6105494/markwang/possum/possum
export POSSUM_KILLARNEY_VENV="$POSSUM_KILLARNEY_ROOT/venv"
export POSSUM_ARTIFACT_ROOT=/scratch/markwang/possum-table3-current
export POSSUM_SLURM_ACCOUNT=aip-rudner
source "$POSSUM_KILLARNEY_ROOT/clusters/killarney_env_setup.sh"
unset POSSUM_BLACKBOX_ONLY

bash clusters/run_killarney_matrix.sh execute \
  --table ours --tier 7b --organism obfcot --task bio \
  --phase eff --seeds 42 --executor slurm --no-submit

pilot_script="$POSSUM_ARTIFACT_ROOT/experiments/slurm/train_obfcot_qwen2.5-7b-it_bio_s42.sbatch"
rg -n 'POSSUM_(GENPARSE|UTILITY_SCORING|OBFCOT_LAT|OBFCOT_LAT_LAST|OAT_FAITHFUL)' \
  "$pilot_script"
```

Require the five replacement exports in the Table 3 contract above. Then run
the same `execute` command without `--no-submit`. After train/eval complete,
require a compact PEFT checkpoint with no merged `model-*.safetensors`, and a
result with both scoring protocols equal to `generate_parse` and utility split
seed 42. Only then submit the other 17 7B seed/task runs from this clean root.

### 4. Validate AutoDAN completions before expansion

For every completed robustness job, require Slurm success and inspect the result
fields rather than relying only on exit code:

```bash
jq '{
  evaluation_schema_version,
  model_org,
  model,
  dataset,
  seed,
  checkpoint_provenance,
  test_elicitations,
  test_elicitation_n,
  test_elicitation_sample_fingerprint,
  elicitation_errors
}' "$result"
```

Collect `seff` for representative 7B jobs and both larger sizing pilots. After
the 14B/32B SysPr pilots validate, submit the remaining larger SysPr wave on
Killarney. Stage and validate larger PWD/CktBr checkpoints task by task before
submitting their attacks. Do not create a duplicate writer for any JSON already
being updated elsewhere.

### 5. Finish and union Table 2 E&U

Continue monitoring the 26 Vulcan jobs but do not submit new Vulcan campaigns.
After they finish:

1. validate every current Killarney and Vulcan JSON against protocol and
   checkpoint provenance;
2. back up the previous combined reporting snapshot;
3. overlay each whole result JSON from its single owning execution root;
4. overlay only matching recipe/config markers, not full weights;
5. run `python -m possum.experiments.tablefill --table prior` against the
   reporting root; and
6. require `270 filled, 54 empty` before treating E&U as complete.

### 6. Pilot larger Table 3 training

After all replacement 7B compact runs are stable, generate fresh 14B/32B pilots
in the clean Table 3 root with:

```bash
export POSSUM_TRAIN_GPUS_14B=2
export POSSUM_TRAIN_GPUS_32B=3
```

Run `plan` first and verify that training requests two/three H100s while its
evaluation still requests one. Pilot 14B before 32B and adjust only from measured
OOM/runtime evidence.

## Historical incidents retained for provenance

- An earlier PWD/7B/Bio AutoDAN attempt failed in the summarizer fallback path
  with an unbound `summarizer_system`. Commit `87e20bb` repaired the crash.
- Builder `4628490` later timed out at 12 hours, but its final library and
  metadata were complete. The accepted cached-library pilot proves that cache is
  usable; no builder rerun is needed.
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

## Definition of done

Table 2 is complete when:

- all 270 effectiveness/utility values are generated from current,
  provenance-matching JSONs;
- all 54 `Jlbrk` table cells are generated from all 162 validated AutoDAN seed
  results sharing the exact canonical strategy library;
- the reporting-only union reproduces Table 2 from one `tablefill` command;
- the final table is compared with the preliminary paper table and the cited
  prior work without post-hoc tuning; and
- JSONs, configs/markers, logs, table output, cache hashes, seeds, and commit
  provenance are preserved outside purge-prone locations.

The current RoBCoTDstl stage is complete when:

- all old forced-choice/bare-recipe jobs are retired and excluded from
  aggregation;
- all 7B E&U cells run from compact, loadable PEFT checkpoints trained with the
  current OAT-then-LAT recipe and evaluated with generate-and-parse;
- measured 14B/32B training pilots establish safe GPU/time/memory requests;
- the remaining E&U matrix is released without recreating full merged base
  models per seed; and
- its later robustness phase uses the Table 3 attack contract, not Table 2's
  AutoDAN-only restriction.
