# Prior-baseline and RoBCoTDstl campaign status

> Canonical snapshot: 2026-08-13, after artifact acceptance of all nine Table 2
> realization-repair E&U results, release of their six already-submitted
> follow-on attacks, completion of the Table 3 Bio/7B seed-44 chain, and launch
> of one seed-42 Table 3 result-provenance refresh.
> Scheduler state changes continuously; query `squeue` and `sacct` again before
> cancelling, releasing, replacing, or resizing a job.
>
> Table 2 effectiveness/utility computation and the exceptional realization
> repair are complete. PWD 7B Chem/Cyber and CktBr 7B Chem jobs
> `4730255`--`4730257`, `4730317`--`4730319`, and
> `4730320`--`4730322` all completed in approximately 34--39 minutes with exit
> code zero. For each repaired cell, the repository reports `train ✓ / eff ✓`,
> and all nine JSONs pass identity, forced-choice task, generate-and-parse
> utility, split-seed, numeric-metric, marker, and
> `checkpoint_payload_sha256` gates. CktBr 7B Cyber required no refresh because
> all twelve model shards matched byte-for-byte across scratch and project.
> The pre-incident reporting union at
> `artifacts/table-report-eu-20260811.Z59lUv` generates `270 filled, 54 empty`;
> all 54 empty values are the pending `Jlbrk` column. The generated table's
> SHA-256 is
> `004c43640ba6d894405a251c2eb3df71fbd32fb7c867975e123f10562d79cd24`.
> Preserve this immutable snapshot as pre-incident evidence. Its three affected
> cells are superseded by the accepted repairs; create a new reporting clone
> rather than mutating it. The accepted repair evidence contains nine JSONs and
> nine checkpoint markers at
> `artifacts/realization-repair-7b-20260812/repaired-eu-accepted-20260813-001612`;
> all 18 entries pass `SHA256SUMS`, whose SHA-256 is
> `22b19bb4f37e416b43e6282046237ca2a245689d674280b1fa35a78bdf1f9770`.
>
> The AutoDAN strategy library itself is complete and accepted. The canonical
> project files have SHA-256 values `79c02d...ea44` and `46b2ea...253f`, schema
> 2, five lifelong rounds, `source_count=318`, and the accepted source
> fingerprint. A later incident was not corruption of this library: jobs
> inherited a different scratch cache path with no completed library, became
> concurrent builders, and raced on the same fixed checkpoint `.tmp` path.
> The scratch path is now a verified symlink to the canonical project library.
> Commit `5b9fae2` additionally pins Table 2 to that library, validates its exact
> bytes before submission and again inside each allocation, makes mass jobs
> fail closed instead of building, serializes explicit builders, and uses unique
> atomic temporary files. Commit `d8c6f7a` fixes two
> Table 3 launch defects and lets already-held forced-choice Table 2 jobs infer
> immutable-cache consumer mode at runtime. Killarney is deployed through
> `2b50dcb`; its full 65-test protocol module passes, and immutable-cache
> preflight accepts the exact canonical library.
>
> The latest complete user-supplied `squeue` snapshot, taken before the
> subsequent seed-42 Table 3 repair submission, contained 113 jobs: one running,
> 98 deliberately user-held, twelve priority-pending, and two
> dependency-pending. The running job was required SysPr/14B/Chem retry
> `4723063`. Nine Table 2 attacks were priority-pending: PWD/Chem
> `4730325`--`4730327`, PWD/Cyber `4709719`/`4709891`/`4709892`, and
> CktBr/Chem `4666404`--`4666406`. The latter six were intentionally released
> only after their repaired E&U artifacts passed. Table 3 Bio/seed-42 AutoDAN
> pilot `4723162` and 14B/32B E&U pilot training jobs
> `4723194`/`4723198` accounted for the other three priority jobs; dependent
> evaluations were `4723195`/`4723199`. After that snapshot, `4723162` was
> deliberately held and L40S repair evaluation `4739765` was submitted. Refresh
> live state before quoting an exact current total. No new terminal failure is
> present.
>
> `4709894`
> (SysPr/32B/Chem/seed-43) completed and passes its full artifact gate with
> AutoDAN `75/83 = 0.9036`. `4675518` (SysPr/14B/Chem/seed-42) lost its
> validation pass to the old cache race and then timed out at 8:00:10 during
> its second, held-out test invocation; replacement `4723063` was running in
> the latest complete queue snapshot.
> `4675519` (SysPr/14B/Chem/seed-43) completed in 2:25:30 and the repository
> gate reports `rob 1/1`, so it is accepted and needs no retry. `4715729` is the
> canonical-cache replacement for failed Bio/14B/seed-44. Bio/14B/seed-43 job
> `4675516` was cancelled; its replacement is now job `4717366`. The seed-44
> replacement is `4715729`. The bulk Table 2 wave remains deliberately held to
> respect scheduler ownership and limit simultaneous submissions; releasing the
> six repaired-cell attacks reduces that held Table 2 set from 69 to 63 jobs.
> Table 3 remains a bounded-tranche campaign rather than a mass release.
> Another 72 PWD/CktBr
> 14B/32B robustness evaluations remain intentionally unsubmitted pending final
> checkpoint inventory/staging.
>
> Killarney is the sole compute target; submit no Vulcan compute. The first
> corrected RoBCoTDstl pilot completed, but it predates the new reporting
> contract and is stored in the old unsuffixed slot. The earlier Table 3
> receipt-layer defects are fixed and deployed: canonical seed slots, an exact post-save
> checkpoint receipt/result-provenance gate, and exact 42/43/44 aggregation.
> The same audit fixed multi-dataset recipe-default drift, hybrid compact/full
> checkpoint acceptance, inherited-ablation leakage, stale/same-size checkpoint
> payloads, and non-atomic canonical result replacement. The canonical
> Bio/7B/seed-42 training job `4713564` completed, but its receipt correctly
> exposed `lat_steps=100`, `lat_pgd_iterations=16`, and inherited
> `POSSUM_WRONG_PW_FRAC=0`; evaluation `4713565` therefore failed immediately.
> The checkpoint is diagnostic only. The root causes are now fixed in source:
> `train.py` passes the resolved 150-step/32-PGD LAT configuration into both
> `obfcot` constructors, and the shared Killarney environment no longer
> reintroduces the Table-2-only password-decoy override. Retrain the pilot after
> deployment; never rerun evaluation against or post-hoc relabel the invalid
> weights.
> Corrected replacement training `4717326` completed in 1:31:10 and evaluation
> `4717327` completed in 35:08, both with exit code zero. The repository status
> gate originally accepted this result before the payload-digest extension. The
> current checkpoint remains valid and its canonical marker hash exactly
> matches the result, but the result provenance omits the derived
> `checkpoint_payload_sha256` field added by `0de71b0`; current status therefore
> correctly reports seed 42 as `train ✓ / eff ·`. This is a result-provenance
> transition, not invalid training or evidence that the recorded metrics used
> different weights. Job `4739765` is a forced E&U-only refresh against the
> existing checkpoint; its plan skips preparation and training and requests one
> L40S, 64 GiB, and 120 minutes. The generated training script is from
> `d8c6f7a`, enables generate-and-parse, faithful OAT, LAT, and LAT-last, and
> contains no `POSSUM_WRONG_PW_FRAC` override.
>
> A user clarification after the 7B E&U expansion established that Table 3,
> like Table 2, reports AutoDAN only—not GCG or TAP. The implementation updates
> the Table 3 manifest, Killarney/Vulcan attack filters, table metric/caption,
> immutable-cache routing, and fail-closed count/fingerprint/error gates. The
> AutoDAN-only contract introduced in `e27562c` remains deployed through current
> Killarney commit `2b50dcb`; all 65 protocol tests pass in the cluster ML
> environment. Its dry plan requested exactly
> `--elicitations autodan`, skipped preparation/training, and validated the
> immutable library. The first Table 3 robustness pilot is job `4723162`; it is
> deliberately user-held until `4739765` restores a payload-digest-bound seed-42
> E&U result, preventing two writers from racing on the same canonical JSON.
> The already-submitted E&U jobs `4723017`--`4723055` are
> unaffected because they request no elicitation attack.
> Deadline-oriented larger-tier E&U pilots are also submitted: 14B training
> `4723194` with dependent evaluation `4723195`, and 32B training `4723198`
> with dependent evaluation `4723199`. Their visible scheduler requests match
> the intended eight-/twelve-hour training and eight-hour one-H100 evaluation
> envelopes; the narrow default `squeue` TRES column does not display the
> multi-H100 training counts and should not be used to audit those counts.
> A local follow-up makes dry-run plans and Slurm scripts use the same
> effective evaluation-walltime resolver. Before this fix, the plan for
> `4723162` displayed the 480-minute resource default even though Killarney's
> black-box wrapper exported a 720-minute submission override. This was a plan
> display discrepancy, not a change to the submitted job or its methodology.
>
> The protocol suite progressed from 61 pre-incident tests to 62 after the
> constructor/environment repair and 63 after the walltime-display repair.
> Payload-receipt coverage now brings it to 65 tests. All 65 pass in Killarney's
> full ML environment; local runs also pass, with ML-dependent tests skipped
> when the Alliance stack is unavailable. Expansion remains gated on artifact
> acceptance and bounded scheduler ownership, not on a known unfixed
> methodological defect.
> A first Killarney test attempt exposed a polluted login-module state in which
> the venv could not see Alliance's `packaging` or `pyarrow` extensions. The
> environment setup now unloads any inherited Arrow/Python pair and reloads
> Python 3.12 before Arrow 19.0.1; preflight verifies both before submission.
> The preflight subsequently passed. A test invocation then accidentally
> replaced the module-provided `PYTHONPATH`, hiding both packages; the correct
> interactive test preserves the path established by environment setup. Setup
> also no longer enables `errexit`/`nounset` in an interactive login shell.

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
| Code and tests | Payload-bound prior provenance deployed through `2b50dcb`; all 65 cluster tests pass | Tables 2/3 are AutoDAN-only. New PWD/CktBr saves and explicitly stamped legacy checkpoints bind result provenance to exact payload receipts; `eff-child` permits child-only repair without recreating archived parents. |
| Table 2 methodology | Implemented | Task effectiveness and attack readout use forced-choice; HS/CSQA utility uses generate-and-parse; SysPr uses three seeded stochastic inference replicates. |
| Table 2 E&U | Complete and artifact-accepted | All nine PWD 7B Chem/Cyber and CktBr 7B Chem seed repairs passed current payload-bound E&U gates. CktBr 7B Cyber matched exactly and never needed repair. No Table 2 E&U rerun remains. |
| Table 2 E&U union | Pre-incident 270/324 snapshot preserved; repaired reporting clone pending | `artifacts/table-report-eu-20260811.Z59lUv` remains immutable evidence. Overlay the nine accepted repaired JSONs into a new root; do not mutate the old union. Robustness remains the only intentionally incomplete metric family. |
| AutoDAN cache | Complete and accepted | The canonical library has schema-2 complete metadata, five lifelong rounds, and the full 318-row source fingerprint. Do not rebuild it. |
| Table 2 AutoDAN | Two Chem SysPr seeds accepted; ten attacks active/eligible; bulk wave held; 72 larger jobs unsubmitted | `4675519` (14B/Chem/seed-43) and `4709894` (32B/Chem/seed-43) are artifact-accepted. Retry `4723063` was running in the last snapshot. Nine repaired-cell attacks are priority-pending. The other 63 submitted Table 2 jobs remain held. |
| Table 3 RoBCoTDstl | Bio/7B seeds 43/44 E&U accepted; seed-42 provenance refresh submitted; AutoDAN pilot held | Seed 42 has valid weights and metrics but its result predates payload-digest provenance; job `4739765` refreshes only E&U. AutoDAN pilot `4723162` is held until that passes. The 14B/32B Bio pilot chains remain queued, and the next Chem/7B chain remains held. |
| Vulcan artifact migration | Sufficient for utility, incomplete for robustness | A validated PWD/32B/Chem source is now staged for the repair jobs. CktBr/32B/Cyber and CktBr/32B/MMLU-Prof still need final location/export checks, and 14B has not had a comprehensive migration audit. SysPr needs only the shared base-model cache and prompt configuration. |
| Storage | Tight on project, adequate on scratch; Chem source protected | The last measured quotas were about 192 GiB free on project and 378 GiB free on scratch. The old-root 32B PWD/Chem family is the validated `wrong_answer_general_anchor_qwen_port_v1` source used by completed jobs `4699233`--`4699235`; retain it and the repair root until its 32B robustness work is complete and archived. Four staged 32B CktBr children also remain protected until the robustness inventory. |
| Cluster direction | Killarney-only compute | Submit no new Vulcan work. Cancel the former 26 Vulcan retries (holding them until cancellation is verified), rerun any failures on Killarney, and retain the Vulcan artifact root only until a final read-only export of missing final 14B/32B checkpoints and canonical JSONs is verified. |
| End-of-week schedule | Table 2 E&U repaired; combined compute deliverable remains queue-limited | The Table 2 realization incident is closed at E&U level. Its three additional PWD/Chem attacks and six already-required repaired-cell attacks are eligible. Full Table 2 robustness and all-tier Table 3 completion still depend on H100 throughput, bounded release of held work, and staging the 72 larger attacks. |

## Current campaign ledger

This table is the shortest authoritative answer to what is done, active, and
still missing. Counts are scientific seed slots unless a row explicitly says
Slurm jobs.

| Workstream | Total | Accepted/covered now | Active or queued | Not submitted | Next gate |
|---|---:|---:|---:|---:|---|
| Table 2 E&U values | 270 | All 270 populated; all nine exceptional seed repairs accepted | 0 | 0 repair E&U jobs | Create a new reporting clone containing the accepted repairs and preserve the pre-incident union unchanged. |
| Table 2 AutoDAN seeds | 162 | Chem/14B/SysPr seed 43 (`4675519`) and 32B seed 43 (`4709894`) accepted among prior covered slots | `4723063`; nine repaired-cell attacks; 63 held Table 2 jobs | 72 larger PWD/CktBr slots | Validate every new completion; release later work only in bounded tranches and only after checkpoint inventory. |
| Table 2 robustness table cells | 54 | At least PWD/7B/MMLU-Prof now passes its complete three-seed cell gate; full table not final | Derived from the 162 accepted seed results | Not separately submitted | Regenerate the full 324-value table only when all three seeds for every cell pass. |
| Table 3 7B E&U seeds | 18 | Bio seeds 43/44 accepted; seed 42 valid except for pre-digest result provenance | Seed-42 refresh `4739765`; other 15 task/seed chains held | 0 | Accept `4739765`, recheck the exact three-seed Bio aggregate, release `4723162`, then release one bounded Chem seed chain. |
| Table 3 14B/32B E&U | 36 | 0 | 2 pilot chains submitted | 34 | Bio/seed-42 pilots are queued as `4723194`/`4723195` (14B) and `4723198`/`4723199` (32B). Expand neither tier before its own pilot passes the checkpoint/result provenance gates. |
| Table 3 RoBCoTDstl robustness | 54 seed/model/task AutoDAN evaluations | 0 | Pilot `4723162` submitted but temporarily held | 53 | Release the pilot only after seed-42 E&U is payload-digest-bound; expand only after its attack artifact passes every gate. |

The realization incident adds no model training. Its total repair compute is
all nine short E&U evaluations—now completed and accepted—and three replacement
AutoDAN evaluations for PWD/7B/Chem, whose earlier attacks predate payload-bound
provenance. PWD/7B/Cyber and CktBr/7B/Chem AutoDAN were already required and
their held job slots were released after E&U acceptance. CktBr/7B/Cyber needs no
dedicated E&U repair. Thus the incident adds twelve evaluation jobs, only three
of them expensive AutoDAN jobs, rather than a Table-2-wide rerun.
All twelve repair evaluations are authorized to run without a long-lived user
hold. The three PWD/7B/Chem AutoDAN replacements cleared their exact per-seed
`afterok` dependencies and are priority-pending. PWD/Cyber jobs
`4709719`/`4709891`/`4709892` and CktBr/Chem jobs
`4666404`--`4666406` were explicitly released after their corresponding E&U
artifacts passed. This sequencing was a data-integrity dependency, not repair
throttling.

The 90-slot Table 2 coverage number is not an accepted-result count. It means
that a logical slot has an accepted artifact, a terminal artifact awaiting
validation/replacement, or a live submission. Failed job IDs do not add
scientific slots, and every new completion still needs artifact-level
acceptance.

### Work completed

- Corrected Table 2 utility to the Table 3-compatible generate-and-parse
  readout, reran all affected utility cells, and reran SysPr with inference
  seeds 42/43/44 so its aggregates have standard errors.
- Built the reporting-only Table 2 E&U union: 270 values filled, with exactly
  the 54 robustness positions blank. Preserve it as an immutable pre-incident
  snapshot. PWD 7B Chem/Cyber and CktBr 7B Chem now have accepted
  same-realization replacements; CktBr 7B Cyber passed the exact payload
  comparison and remains accepted. Build the post-repair union as a new root.
- Archived the repaired E&U evidence as nine result JSONs plus nine checkpoint
  markers. All 18 checksum entries validate, preserving a compact immutable
  record before AutoDAN phase-merges into the canonical JSONs.
- Audited the suspicious 14B SysPr, 32B SysPr MMLU, 32B PWD MMLU-Stem, and
  CktBr/14B/MMLU-HSS results. Current evidence supports real prompt/training
  behavior or seed variance rather than a known scoring/provenance error; do
  not cherry-pick or retrain based on the observed scores.
- Built and accepted the immutable AutoDAN strategy library and fixed all known
  total-context warm-up paths, result-completeness gates, and 32B memory
  allocation issues that caused the prior reruns.
- Diagnosed the later Killarney failure as cache routing and concurrent builder
  mutation rather than bad model behavior, insufficient time, host OOM, or a
  corrupted accepted library. Quarantined the partial scratch checkpoint and
  made its final-library path a verified symlink to the canonical project
  artifact.
- Implemented and pushed commit `5b9fae2`, which makes all future Table 2 mass
  evaluations immutable-cache readers, checks exact library/metadata hashes at
  submission and allocation runtime, prevents concurrent builders, and removes
  fixed-name atomic-write races. This changes cache lifecycle, not the attack
  estimand, model, dataset, decoding, seed, or attack budget.
- Accepted Table 2 SysPr/32B/Chem/seed-43 job `4709894`: its held-out AutoDAN
  score is `75/83 = 0.9036`, with the expected count, nonempty fingerprint,
  zero context-overflow diagnostics, no attack error, active-system-prompt
  protocol, and matching checkpoint provenance.
- The current status gate accepts PWD/7B/MMLU-Prof as a complete E&U plus
  robustness cell from jobs `4666392`--`4666394`. PWD/7B/MMLU-HSS remains
  incomplete: only seed 42 (`4666398`) is terminal, while seeds 43/44 are held.
- Hardened Table 3 checkpoint identity, canonical seed slots, compact adapter
  payloads, result provenance, atomic writes, recipe defaults, environment
  isolation, and exact three-seed aggregation.
- Repaired deterministic Killarney module setup. Preflight reports the intended
  Python/Arrow stack, and the current full protocol suite passes all 65 tests
  in the cluster ML environment.
- Added exact payload receipts for new and explicitly migrated PWD/CktBr
  checkpoints, payload-digest result provenance, atomic fail-closed marker
  publication, a dry-run-first legacy stamper, and `eff-child` repair mode.
  This closes the same-recipe/different-weights phase-merge gap without forcing
  routine status commands to rehash hundreds of GiB.
- Reclaimed project space, made final Table 3 checkpoints adapter-only, and
  retained only those large trained children/parents still needed for final
  robustness inventory or reproducibility.

### Work in progress

- Ten Table 2 attacks are active or eligible in the latest handoff. Required
  SysPr/14B/Chem retry `4723063` was running; PWD/Chem
  `4730325`--`4730327`, PWD/Cyber `4709719`/`4709891`/`4709892`, and CktBr/Chem
  `4666404`--`4666406` were priority-pending. The remainder of the submitted
  Table 2 wave remains held.
- Table 2 job `4675519` (SysPr/14B/Chem/seed-43) completed in 2:25:30
  and passes the repository's `rob 1/1` artifact gate. Job `4675518` for seed 42 lost its validation
  invocation to the old scratch builder race and then timed out at 8:00:10
  during its second held-out test invocation. The status gate reports `rob
  0/1`; clean forced replacement `4723063` was running in the latest complete
  queue snapshot.
- The bulk Table 2 wave is deliberately user-held: 63 jobs after the six
  repaired-cell releases, including
  SysPr/14B/Bio replacements `4715729` and `4717366`. Do not release it as one
  block; later releases should remain small and checkpoint-inventory-aware.
- Table 3 jobs `4713564`/`4713565` are terminal and invalid. Training completed
  with constructor defaults `lat_steps=100` and `lat_pgd_iterations=16` rather
  than the registry's 150/32 recipe, and the shared Slurm setup reintroduced
  `POSSUM_WRONG_PW_FRAC=0`. Evaluation failed the provenance gate after two
  seconds. The constructor repair was introduced at `d8c6f7a` and remains in
  current deployment `2b50dcb`. Corrected training `4717326`
  completed in 1:31:10 and evaluation `4717327` completed in 35:08. Status
  accepted training but now rejects the pre-digest result. Bio seed-43
  training/evaluation `4723017`/`4723039` and seed-44
  `4723018`/`4723040` are artifact-accepted for E&U. Seed-42 refresh `4739765`
  will add the missing derived payload digest without retraining. The other 15
  task/seed chains in the 7B expansion remain deliberately held.
- AutoDAN-only Table 3 robustness pilot `4723162` is submitted for Bio/7B/seed
  42 but temporarily held to prevent it racing with `4739765` on the canonical
  JSON. Its plan skips preparation/training and requests exactly
  `--elicitations autodan` on one H100.
- A read-only cross-root inventory is still needed to expose every final
  14B/32B PWD/CktBr checkpoint from one Killarney execution root without
  copying obsolete parents or creating duplicate writers.

### Near-future milestones and safe parallelism

1. Monitor seed-42 Table 3 E&U refresh `4739765`. Its plan skips preparation
   and training. Accept it only if Bio seeds 42/43/44 jointly report
   `train ✓ / eff ✓` and the refreshed result contains a 64-character
   `checkpoint_payload_sha256` matching the existing run01 marker receipt.
2. Keep AutoDAN pilot `4723162` held while `4739765` is active. After the E&U
   gate passes, release the pilot and then release one bounded Table 3 Chem/7B
   seed-42 chain (`4723019`, `4723020`, `4723041`), preserving its prep ->
   train -> eval dependency order.
3. Monitor the ten active/eligible Table 2 attacks. Apply the full finite-score,
   expected-count, fingerprint, diagnostics, error, cache, and exact-checkpoint
   gates to every completion; Cyber jobs deserve particular context-overflow
   scrutiny.
4. Create a new repaired Table 2 E&U reporting union from the accepted nine
   JSONs, and preserve the old 270/324 union unchanged as pre-incident evidence.
   Do not delete the 256-GiB scratch root until the new union, original payload
   comparison manifests, and compact 18-file repair snapshot are all retained.
5. After the Table 3 AutoDAN pilot passes, submit or release only the matching
   seed-43/44 Bio attacks before expanding robustness to another task.
6. Monitor Table 3 Bio/seed-42 E&U pilot chains `4723194`/`4723195` and
   `4723198`/`4723199`. Expand neither 14B nor 32B before its own checkpoint and
   result provenance gates pass.
7. Finish the larger-checkpoint inventory before submitting the deliberately
   omitted 72 Table 2 PWD/CktBr 14B/32B AutoDAN evaluations. Continue using
   Killarney only and release held work in bounded, PI-compatible tranches.
8. Finalize Table 2 only after all 162 seed attacks pass: overlay accepted
   robustness onto the repaired E&U union, generate all 324 values, and compare
   against the preliminary paper table and prior-work claims without post-hoc
   tuning. Pipeline Table 3 AutoDAN only behind accepted E&U checkpoints and
   aggregate exact seeds 42/43/44.

### End-of-week feasibility and critical path

The scientific program is on track at the artifact-integrity level: the Table 2
realization repair is complete and accepted, no retraining was required, and
all nine associated attacks are eligible. The Table 3 seed-42 issue is a
single missing derived provenance field, not invalid weights or metrics, and
requires only one short evaluation refresh. The robustness estimand and
immutable cache are fixed, and both campaigns have explicit artifact gates.
The requested combined all-tier compute
deliverable is still not currently on track for this week, but that conclusion
is driven by held/unsubmitted H100-scale work rather than either narrow repair.

As of Thursday 2026-08-13:

- Friday 2026-08-14 is one day away and Sunday 2026-08-16 is three days away.
- Table 2 E&U is complete. Ten attacks are active/eligible, 63 other submitted
  Table 2 jobs are held, and 72 larger-tier jobs remain unsubmitted pending
  inventory and staging.
- Table 3 Bio/7B seeds 43/44 are accepted, seed 42 has one short provenance
  refresh submitted, and the remaining 15 task/seed E&U chains are held.
  Two larger-tier E&U pilots are queued with 34 seed slots not yet submitted,
  while the held AutoDAN pilot is the only submitted robustness slot and 53
  remain unsubmitted.
- The observed scheduler share is only a few simultaneous GPUs, and current
  released/pending jobs have already waited behind priority. Mass submission is
  necessary but does not guarantee enough aggregate GPU-hours by the deadline.

A credible Friday target is: accept `4739765`, restore the Table 3 AutoDAN
pilot, create the repaired Table 2 E&U reporting clone, and validate any of the
ten active Table 2 attacks that schedule. A credible Sunday stretch target adds
a successful Table 3 AutoDAN pilot, the bounded Chem/7B E&U chain, more Table 2
robustness completions, and measured 14B/32B Table 3 pilots. Full Table 2
robustness plus all-tier Table 3 E&U and all Table 3 AutoDAN robustness is
unlikely without substantially more immediately schedulable H100 capacity or a
deliberate reduction in scope. Do not weaken sample counts, attack budgets,
seed rosters, or provenance gates merely to meet the date.

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
- The exact provenance contract now also applies to `obfcot`. Completion
  requires a post-save marker binding model, task, seed, resolved recipe,
  protocol flags, source hashes, checkpoint format, and every payload file's
  size and SHA-256. A generic `adapter_config.json` is no longer sufficient.
- Table 3 `tablefill` now requires exactly seeds 42, 43, and 44 with matching
  checkpoint provenance. It rejects missing, extra, duplicate, legacy, and
  wrong-identity artifacts, so a one-seed pilot cannot populate a reportable
  aggregate.
- Current Table 2 JSON does not embed HS/CSQA sample counts, row fingerprints,
  or an evaluator commit. The captured logs provide the sample-count audit for
  this campaign, but a future schema should record those fields directly.
- `tablefill` leaves a cell as `---` when a gate fails. Generated LaTeX is not
  hand-edited to bypass a missing or invalid artifact.
- A reporting root contains result JSONs and marker-only checkpoint metadata.
  It must never replace an execution root containing full weights.
- The checked-in `tables/table2.gen.tex` predates the AutoDAN-only declaration
  and still contains the older maximum-across-attacks caption. It is a stale
  generated artifact, not the current protocol. The accepted E&U-only table is
  under `artifacts/table-report-eu-20260811.Z59lUv`; regenerate the checked-in
  table only from the final validated reporting union.

### Operational correctness changes and current follow-up

The current branch contains the earlier protocol repairs plus the storage-safe
follow-up:

- AutoDAN generation now follows the released total-`max_length` semantics
  instead of misreading that value as extra `max_new_tokens`. The old behavior
  could exceed the attacker's context, run unnecessarily long, and truncate
  JSON summaries.
- A follow-up adds a fixed-total-context guard to both AutoDAN attacker
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
- The later cache incident revealed that this builder protocol was unsafe when
  multiple evaluations inherited an empty alternate cache root: each process
  became a builder and all used
  `strategy_library.json.checkpoint.json.tmp`. The accepted project library was
  never corrupt; the scratch root contained only a partial checkpoint. The
  partial files were quarantined and the scratch final-library paths now resolve
  to the exact canonical project files.
- Commit `5b9fae2` closes the mass-run failure mode. The Killarney Table 2
  wrapper pins one project library and sets `POSSUM_AUTODAN_REQUIRE_CACHE=1`;
  preflight verifies both exact SHA-256 values and complete schema/source
  metadata; Slurm forwards the requirement; allocations recheck the bytes
  before loading attacker models; a cache miss or mismatch fails immediately;
  explicit builders use a nonblocking process lock; and JSON writes use unique
  same-directory temporary files followed by `fsync` and `os.replace`. Existing
  compatible-cache attack behavior is unchanged.
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
- no rerun among those 26 is required; the separate 7B Chem/Cyber realization
  incident was closed by nine accepted targeted E&U refreshes and does not
  invalidate the completed 32B wave;
- the later PWD/32B/Chem storage audit created one prospective exception to the
  otherwise complete E&U inventory. Jobs `4699233`--`4699235` completed,
  passed the scoring/split/provenance checks, and now provide the authoritative
  Chem utility values; and
- the accepted reporting root is now
  `artifacts/table-report-eu-20260811.Z59lUv`, with 270 E&U values and only the
  54 `Jlbrk` values blank. The older `252 filled, 72 empty` roots remain
  immutable historical snapshots, not current reporting inputs.

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
- The historical partial reporting union is
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
- All 252 values emitted in that partial table passed the Table 2
  protocol/tablefill gates. The accepted union has since overlaid the 26
  replacement outputs and the Chem repair, filling all nine previously absent
  32B utility aggregates. Robustness remains the only blank metric family.
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
26-job replacement wave. The three completed 32B Chem repair jobs are a
provenance repair, not a response to their observed scores, and their outputs
remain authoritative. PWD 7B Chem/Cyber and CktBr 7B Chem are now accepted on
the precommitted project realizations after all nine replacements passed the
payload-bound result gates. CktBr 7B Cyber is cleared by exact payload equality.
Reconsider any other cell only if its artifact fails the declared
result/provenance/sample-count gates or a controlled code-level diagnostic
finds a specific implementation error.

Slurm success was necessary but not sufficient. The accepted E&U union was
created only after the contributing JSONs passed the artifact-level gates.

### What is running or queued

No Table 2 E&U computation is currently queued or missing. The nine targeted
PWD 7B Chem/Cyber and CktBr 7B Chem seed refreshes completed and passed the
current artifact gate; CktBr 7B Cyber required no E&U rerun. The earlier final
32B PWD/Chem provenance repair also completed at the Slurm level:

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
root until the unsubmitted PWD/32B/Chem robustness work is complete and its
reportable artifacts are archived.

The former Vulcan utility jobs remain non-authoritative and must not write any
canonical cell. The broad 102-job plan from the intentionally partial 32B
scratch root remains unsafe and must never be executed.

### E&U accounting and union

The preserved pre-incident reporting-only union overlays the retained effectiveness fields,
all corrected utility and seeded SysPr JSONs, the 26 targeted replacements,
and jobs `4699233`--`4699235` for PWD/32B/Chem. Running current `tablefill`
against it produces:

```text
270 filled, 54 empty (all 54 empty values are Jlbrk)
```

The canonical files and hashes are:

```text
artifacts/table-report-eu-20260811.Z59lUv/tables/table2.gen.tex
  004c43640ba6d894405a251c2eb3df71fbd32fb7c867975e123f10562d79cd24
artifacts/table-report-eu-20260811.Z59lUv/EU_UNION_MANIFEST.json
  f3edeca3a2bf06247e960c0661f48c9e050489aa980f16d9029bd40abb374e1d
```

Preserve this root as an immutable E&U checkpoint. The next Table 2 report
should clone it, replace the nine affected seed JSONs with the accepted
same-realization artifacts, and then overlay accepted robustness results. No
additional E&U job is known to be missing; do not submit duplicate utility
work.

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

### Historical Killarney queue snapshots (superseded)

This retained full `squeue`/`sacct` snapshot predates the canonical queue
summary at the top of this document and must not be used as a current release
list. It is followed by a newer filtered 14B snapshot. The newer evidence
supersedes the affected rows: `4675519` completed
in 2:25:30 and is artifact-accepted at `rob 1/1`; replacement `4723063` was
running in the latest complete snapshot; all displayed pending 14B SysPr jobs
request 96 GiB; and Bio
replacements `4715729`/`4717366` now request eight hours.

| State | Count | Jobs | Resources/disposition |
|---|---:|---|---|
| Newly accepted | 1 | `4675519` | SysPr/14B/Chem/seed-43 completed in 2:25:30 with exit code zero; status reports `train ✓ / eff ✓ / rob 1/1`. The terminal `Reason=JobHeldUser` is stale accounting metadata and does not override the successful state/artifact gate. |
| Newly accepted | 1 | `4709894` | SysPr/32B/Chem/seed-43 completed in 3:58:53. The result passes identity, score, 83-row count, fingerprint, diagnostics, error, active-system-prompt, and checkpoint-provenance gates; AutoDAN is `75/83 = 0.9036`. |
| Released/pending | 67 | `4666399`--`4666436`, `4675520`--`4675532`, `4709719`, `4709891`, `4709892`, `4709895`--`4709907` | All show `Priority` except `4709895`, whose snapshot reason is a temporarily unavailable/down/drained/reserved compatible node. They retain their queue positions and consume the canonical cache as fail-closed readers. |
| Released replacements | 2 | `4715729`, `4717366` | SysPr/14B/Bio seeds 44 and 43 respectively; one H100, 96 GiB, eight hours, pending for priority. |
| Terminal and invalid/incomplete | 3 | `4675516`, `4675517`, `4675518` | Bio seeds 43/44 have replacements `4717366`/`4715729`. Chem seed 42 timed out after the old cache race consumed part of its allocation and status confirms `rob 0/1`. |
| Required retry | 1 | `4723063` replacing `4675518` | Submitted against the canonical immutable cache at one H100, 96 GiB, and eight hours; running in the latest complete snapshot. |

The same accounting contains the corrected Table 3 pilot, which is not part of
the Table 2 count: training `4717326` completed in 1:31:10 on one L40S and
evaluation `4717327` completed in 35:08 on one L40S, both with exit code zero.
The generated training script is from `d8c6f7a`, contains all five required
protocol exports, and contains no `POSSUM_WRONG_PW_FRAC`. Status reports
`train ✓ / eff ✓`, and the repeat plan skips both Bio seed-42 jobs, accepting
the pilot and clearing the other 17 7B E&U seeds. Invalid predecessor jobs
`4713564`/`4713565` remain diagnostic only.

Relevant terminal history remains:

| Terminal state | Count | Jobs | Interpretation |
|---|---:|---|---|
| Completed AutoDAN | 3 | `4680228`, `4680229`, `4680259` | Slurm-complete 32B SysPr results; retain and accept only if their artifacts pass. |
| Application failure | 4 | `4680120`, `4680260`, `4682272`, `4682273` | Seed-42 Cyber confirms the missed direct warm-up guard. None is reportable; all logical slots now have replacements. |
| Launch cancellation | 13 | `4680261`, `4680264`--`4680266`, `4680268`--`4680276` | Batch steps received signal 53 after 0--1 second on `kn172`/`kn175`; scientific code did not run. All logical slots now have replacements. |

The 32B SysPr jobs were reduced from 256 GiB to 128 GiB with Slurm's MiB-valued
update, `MinMemoryNode=131072`. Jobs `4680229` and `4680259` completed at 128
GiB, and `4680260` ran for 3:43 at that request; therefore the reduction did
not cause the launch cancellations. Retain two H100s because target and
attacker models coexist. The well-measured Bio/Chem subsets were selectively
reduced to eight hours; slower or less-measured tasks retain 12 hours because
the accepted Bio pilot took 5:47:16 and task-dependent attacks have exceeded
eight hours. Make no further blanket wall-time reduction. Commit
`1eb29e0` exposes the validated host-memory override and the Killarney wrapper
now sets `POSSUM_BLACKBOX_MEM_GB_32B=128` for Table 2 attacks, so replacement
jobs request 128 GiB directly and no post-submission `scontrol` resize is
needed.

The morning snapshot supports only two additional backfill-oriented changes:

- reduce pending 14B SysPr jobs from 128 GiB to 96 GiB host memory. The same-
  family 14B pilot used only 2.47 GiB according to `seff`, while the largest
  accepted 7B attack measurement was 46.34 GiB; 96 GiB remains conservative.
  Do not extrapolate this override to future 14B PWD/CktBr jobs before their
  own measured pilot;
- reduce Bio/14B/SysPr replacements `4715729` and `4717366` from 12 to eight
  hours. The same-family Bio pilot completed in 3:51:50, and the original Bio
  jobs already used eight-hour limits.

Do not shorten the current Cyber/MMLU limits: prior Cyber attempts consumed
7:49--8:57 and the MMLU rows lack a comparable clean bound. Do not reduce 32B
host memory below 128 GiB: a prior PWD/32B/Chem attempt approached 96 GiB at a
96-GiB request. Keep two GPUs for 32B target-plus-attacker evaluations. The
running job `4675519` cannot benefit from a pending-job resize.

Apply only the supported pending-job changes, without cancelling or
resubmitting (which would discard queue age):

```bash
T2_14B_SYSPR_PENDING=(
  4675520 4675521 4675522 4675523 4675524 4675525 4675526
  4675527 4675528 4675529 4675530 4675531 4675532
  4715729 4717366
)

for job in "${T2_14B_SYSPR_PENDING[@]}"; do
  read -r state name < <(squeue -h -j "$job" -o '%T %j')
  if [[ "$state" == PENDING &&
        "$name" == possum_eval_sysprompt_qwen2.5-14b-it_* ]]; then
    scontrol update JobId="$job" MinMemoryNode=98304
  fi
done

for job in 4715729 4717366; do
  read -r state name < <(squeue -h -j "$job" -o '%T %j')
  if [[ "$state" == PENDING &&
        "$name" == possum_eval_sysprompt_qwen2.5-14b-it_bio_* ]]; then
    scontrol update JobId="$job" TimeLimit=08:00:00
  fi
done

for job in "${T2_14B_SYSPR_PENDING[@]}"; do
  scontrol show job "$job" | tr ' ' '\n' |
    grep -E '^(JobId|JobState|TimeLimit|MinMemoryNode)='
done
```

The numeric `MinMemoryNode=98304` is 96 GiB expressed in MiB, matching the
syntax that previously resized pending Killarney jobs reliably. If a job has
started or disappeared since the snapshot, the guarded loop intentionally
leaves it unchanged.

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
  `4675516`--`4675532`. Each requests one H100, six CPUs, and 128 GiB. The
  Bio/Chem subset was selectively reduced to eight hours; the other tasks
  retain twelve hours. Later cache-race history supersedes the original queue
  state: `4675516` was cancelled, `4675517` failed, and `4715729` replaces
  seed 44.
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
  code 0 in 3:00:43, 3:01:08, and 2:34:32. The pre-receipt repository gate
  accepted this cell at the time. The later cross-root payload audit supersedes
  that conclusion: those JSONs are preserved as incident evidence but are not
  publishable, and jobs `4730255`--`4730257` are replacing them against the
  canonical project weights.
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
because failed jobs and replacements share one scientific slot. Three expansion
pilots are accepted and the new 32B/Chem/SysPr seed-43 result is artifact-
accepted. Both Bio replacements are present. Chem seed 42 timed out, status
reported `rob 0/1`, and forced retry `4723063` was later observed running. Job `4675519` is
artifact-accepted. Seventy-two
larger PWD/CktBr slots remain unsubmitted. Refresh
`squeue`/`sacct` before making any scheduling decision.

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

After a later release, multiple allocations inherited
`/scratch/markwang/possum-prior-32b-killarney/cache/autodan/strategy_library.json`
instead of the completed project library. Only a partial checkpoint existed in
that scratch directory. Concurrent evaluations therefore became cache builders
and raced while renaming the same
`strategy_library.json.checkpoint.json.tmp`. Bio/14B/SysPr seed-44 job
`4675517` failed after 2:29 with no reportable AutoDAN output, and seed-43 job
`4675516` showed the same failure before it was cancelled. This was not OOM,
timeout, malformed benchmark data, or evidence about the organisms.

The partial scratch files were quarantined. Its final library and metadata paths
are verified symlinks to the canonical project files and reproduce the exact
accepted SHA-256 values. At that incident point, the user successfully held all
68 jobs that were still pending. Two allocations had already started:
`4675518`, whose validation pass hit the race before its test invocation began,
and healthy `4709894`, which consumed the repaired cache. That 68-job count is
historical; the current scheduler ownership is the 106-job held split recorded
at the top of this document.

Require a finite test AutoDAN score, positive expected test count, nonempty
fingerprint, no stored AutoDAN error, current cache/checkpoint provenance, and
non-null diagnostics from every new-code completion. A missing validation-only
AutoDAN diagnostic does not enter Table 2, but the held-out test artifact must
pass every reporting gate. Any unrelated old-code completion remains valid if
it passes all applicable pre-existing gates, because the guard and cache
lifecycle fixes do not change compatible-cache attack behavior.

### What is deliberately not queued

The campaign needs 162 seed-level AutoDAN results. Current coverage accounting
is:

```text
90 submitted or accepted logical slots + 72 not yet submitted = 162
```

The three accepted expansion pilots are PWD/7B/Bio/seed-42 (`4652099`),
SysPr/14B/Bio/seed-42 (`4666342`), and SysPr/32B/Bio/seed-42 (`4666363`).
Other completed logical slots passed their contemporaneous result gates, but
the old PWD/7B/Chem attacks predate the selected project realization and are
being replaced by priority-pending jobs `4730325`--`4730327`. The 90-slot coverage count
includes the Cyber slots whose
original jobs and replacements failed, the 17 post-pilot 32B SysPr slots now
split into three completed and 14 incomplete results, and the original 55-job
wave, most of which is now held after the cache incident.
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
the validated `[obfcot]` block in `HYPERPARAMETERS.toml`. The exact receipt and
canonical slot gates permit reuse of the existing scratch root and its CoT data
without allowing the old unsuffixed checkpoint to satisfy resumability checks.
The first source-receipted one-cell 7B/Bio/seed-42 E&U attempt was
`4713564`/`4713565`. It is terminal and invalid. Training completed with a
loadable compact adapter, but its receipt records `lat_steps=100`,
`lat_pgd_iterations=16`, and experimental override
`POSSUM_WRONG_PW_FRAC=0`; evaluation correctly rejected it before loading the
model. Do not reuse or post-hoc relabel these weights.

The audit found two implementation defects rather than stale source or a
scheduler failure. `train.py` resolved the `[obfcot]` 150/32 LAT values but did
not pass `common_lat` into either single- or multi-dataset `obfcot`
constructor. Separately, `killarney_env_setup.sh` re-exported the Table-2-only
wrong-password fraction after the Table 3 launcher had cleared it. The current
repair forwards `common_lat` and moves the password override into the explicit
`--table prior` launcher branch. Regression tests bind both behaviors.

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

Three concrete blockers were found during the post-run audit (all are fixed and
deployed, but not retroactively for the old pilot). The descriptions below are
historical behavior, not the current implementation:

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

The deployed repair now:

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

The receipt/aggregation and constructor/environment repairs are deployed at
`d8c6f7a`; corrected jobs `4717326`/`4717327` are accepted by status and repeat
planning. The accepted pilot satisfies the following contract:

1. the checkpoint is a loadable compact PEFT adapter and does not contain
   merged full-model shards;
2. the result records `mcq_scoring_protocol=generate_parse`,
   `utility_scoring_protocol=generate_parse`, and `utility_split_seed=42`;
3. the new marker and result provenance match 7B/Bio/seed-42 and the current
   OAT-then-LAT recipe;
4. effectiveness and both utility metrics are finite and have expected sample
   counts; and
5. a fresh plan against the current root skips preparation, training, and
   evaluation; and
6. only a complete cell with the exact 42/43/44 roster is eligible for
   `tablefill` aggregation.

Do not manually copy an old adapter into `run01`, rerun `4713565` against the
invalid checkpoint, or aggregate any old pilot. A post-hoc marker would falsely
claim the weights used the corrected constructor and clean environment. The
canonical replacement is valid; submit the other 17 7B runs from the same
root and protocol.

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
not accepted as safe OAT allocations. The conservative schedule waits for the
replacement 7B wave; the deadline-oriented but still gated schedule may queue
one 14B training pilot with two H100s and one 32B pilot with three H100s after
the canonical 7B pilot passes, while the remaining 7B wave runs on L40S. Do not
expand either larger tier before its own pilot passes. Their downstream E&U
evaluations should remain one-H100 jobs.

Tables 2 and 3 both report AutoDAN-only robustness. The manifest and Killarney
launcher enforce this rather than relying on a caller's shell: planning either
table's black-box phase requests only AutoDAN, pins the accepted immutable
strategy library, and rejects an inherited GCG/TAP filter.

### Expansion and robustness plan

Table 3 has 54 E&U seed/model/task slots: three tiers, six tasks, and three
seeds. Bio/7B/seed-42 is accepted, the remaining 17 7B slots are submitted as
jobs `4723017`--`4723055`, and all 36 14B/32B slots remain unaccepted.
Reportable cells require the exact 42/43/44 roster, so an individual successful
seed is progress but cannot yet populate the paper table.

The later RoBCoTDstl robustness phase has the same 54 seed/model/task slots;
every slot must complete AutoDAN with the expected attacked-subset count,
fingerprint, no stored error, and matching checkpoint/result provenance. The
same accepted immutable strategy library is used by Tables 2 and 3.
Robustness may be pipelined behind accepted checkpoints rather than waiting for
every tier to finish, provided each result has one writer and no robustness job
runs against a checkpoint whose marker/payload gate has not passed.

The first two acceleration gates—deploy/retrain Bio/7B/seed-42 and validate its
receipt/result plus repeat-plan skip—are complete. The remaining sequence is:

1. monitor and validate the submitted remaining 17 7B E&U runs;
2. after the canonical 7B pilot gate, queue one measured 14B training pilot at
   two H100s and one 32B training pilot at three H100s, while the remaining 7B
   jobs use L40S capacity;
3. expand each larger tier only after its own checkpoint/load/runtime gate;
4. submit AutoDAN robustness jobs as soon as their exact checkpoint seed
   passes, without relaxing attack budgets or seed requirements; and
5. aggregate only after all three seeds for a cell pass result provenance and
   attack-completeness validation.

This pipelining improves elapsed time but does not make full all-tier Table 3
robustness a credible 2026-08-14 or 2026-08-16 deliverable under the observed
Killarney queue share.

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
uses symlinks instead of duplicating the families again. The 2026-08-12 audit
reports:

```text
4124/5000 GiB shared project quota; 877 GiB filesystem free
1624/2000 GiB user scratch quota; 376 GiB filesystem free
1.8 TiB project artifacts/checkpoints
135 GiB shared project Hugging Face cache
816 GiB /scratch/markwang/possum-prior-eff
550 GiB /scratch/markwang/possum-prior-32b-killarney
256 GiB /scratch/markwang/possum-prior-utility-7b
3.8 GiB /scratch/markwang/possum-table3-current
```

The latest storage audit reports 550 GiB under
`/scratch/markwang/possum-prior-32b-killarney` and finds staged CktBr 32B
families there for Chem, MMLU-HSS, and MMLU-STEM; Bio resolves from another
execution root. These children are potentially the only staged inputs for the
still-unsubmitted 32B CktBr robustness wave; retain them until the final
cross-root inventory identifies a canonical execution copy and no queued job
or reporting root depends on the staging root.

The physical-family breakdown also resolves the two other large scratch roots:

- `/scratch/markwang/possum-prior-eff` contains unique CktBr/14B/Cyber,
  CktBr/32B/Bio, CktBr/32B/Cyber, CktBr/32B/MMLU-Prof, and
  PWD/32B/Cyber families. Retain the entire 816-GiB root.
- `/scratch/markwang/possum-prior-utility-7b-20260804` contains PWD and CktBr
  7B Chem/Cyber families with same-sized project counterparts plus the two
  associated retired CktBr parents. This 256-GiB root is the strongest
  remaining whole-root cleanup candidate, but its deletion gate currently
  fails. All four project cells pass `train` and E&U status, and no live Slurm
  script references the root, but the completed byte-level comparison found
  that PWD/7B/Chem, PWD/7B/Cyber, and CktBr/7B/Chem differ in three of four
  merged weight shards for every seed. CktBr/7B/Cyber matches exactly across
  all twelve shards and is not part of the repair.
  A subsequent audit found no literal reference to the scratch root in the EU
  union manifest or saved project artifacts, but the accepted result schema
  does not store the execution-root path, so this absence does not identify the
  evaluated copy. All twelve PWD/CktBr Chem/Cyber seed markers compare
  byte-identically across scratch and project despite the demonstrated PWD/Chem
  weight mismatch. The accepted EU PWD Chem/Cyber JSONs date from the 2026-08-04
  utility wave, whereas the canonical project PWD JSONs were later updated by
  robustness work on 2026-08-09--11; this is consistent with, but does not by
  itself prove, a cross-realization phase merge. The CktBr Chem/Cyber canonical
  and EU JSONs still date from 2026-08-04 and have no AutoDAN result.
  Retain the root until the nine mismatching project checkpoints are receipted,
  E&U and AutoDAN are made same-realization, replacement results are archived,
  and the scratch-only retired-parent metadata is preserved. Prior-baseline result provenance
  binds recipe markers but not merged weight-shard bytes, so status output
  alone cannot distinguish these two trained realizations. Commit `0de71b0`
  adds one-time SHA-256 payload receipts to new PWD/CktBr saves and an
  explicit dry-run-first stamper for legacy checkpoints. Once stamped, ordinary
  status checks cheaply validate the immutable receipt's file set and sizes,
  and result provenance includes the receipt digest; two same-recipe/different-
  weight realizations therefore cannot phase-merge. It also adds an
  `eff-child` repair phase so a forced CktBr E&U refresh cannot recreate its
  archived PWD parent. Its 65-test local protocol suite passes with 12 ML-stack
  skips. Killarney pulled through `2b50dcb` and all 65 tests passed in its ML
  environment. PWD/7B/Chem run01--run03 were then stamped successfully with
  six-file payload receipts and pre-receipt marker backups. The dry plan
  skipped all three training jobs and selected exactly three payload-bound,
  attack-free E&U evaluations at two hours/64 GiB/one L40S; jobs
  `4730255`--`4730257` are pending for priority. Stamping intentionally
  invalidated the old marker-only result provenance; after these E&U jobs pass,
  PWD/Chem requires three payload-bound AutoDAN reruns before its cell is final.
  The evidence directory preserves all three pre-repair PWD/Chem result JSONs
  and the later three-family checksum manifests. Copy or regenerate the two
  original PWD/Chem checksum manifests there before deleting the scratch root.

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
AutoDAN cache metadata were retained. An earlier inventory identified five
additional project parent families totalling about 696 GiB:

```text
81G   pwd_hof_parent_qwen2.5-14b-it_chem
184G  pwd_hof_parent_qwen2.5-32b-it_bio
184G  pwd_hof_parent_qwen2.5-32b-it_chem
63G   pwd_hof_parent_qwen2.5-32b-it_mmlu-hss
184G  pwd_hof_parent_qwen2.5-32b-it_mmlu-stem
```

They originally had no matching child in the same execution root. The
2026-08-12 filesystem audit confirms that all four named 32B parent directories
are now absent from the project checkpoint root, so their roughly 615 GiB has
already been reclaimed and they are no longer cleanup candidates. Do not
infer that a same-named scratch copy is redundant: inventory any such copy
separately against its final child and live references. Retain the lightweight
retired metadata and staged final CktBr children. Audit the 14B/Chem parent
separately after the final 14B child inventory.

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
and the three-job 32B Chem repair are complete and incorporated into the saved
E&U union. That union is immutable incident evidence whose three affected cells
are superseded. All nine exceptional E&U repairs completed and passed:
PWD/7B/Chem `4730255`--`4730257`, PWD/7B/Cyber
`4730317`--`4730319`, and CktBr/7B/Chem `4730320`--`4730322`.
Their project checkpoints are payload-receipted. CktBr/7B/Cyber matched the
scratch payload exactly and needs no E&U repair. The former
Vulcan jobs `379734`--`379747`, `379759`--`379764`, and
`379771`--`379776` must be cancelled or held permanently; confirm that none can
write a competing result. No new Vulcan submission is permitted.

The old Killarney Table 3 graph `4637151`--`4637283` is terminal and absent from
the queue. Preserve its logs as diagnostics but never aggregate its artifacts.
The clean pilot `4669018`--`4669020` is Slurm-complete but must not be
aggregated or used to expand the matrix because it predates the receipt
contract. The latest complete supplied queue snapshot contained 113 jobs: one
running, 98 held, twelve priority-pending, and two dependency-pending. It
predates two subsequent actions: Table 3 AutoDAN pilot `4723162` was held, and
seed-42 E&U refresh `4739765` was submitted. Bio/7B seeds 43 and 44 are
artifact-accepted; seed 42 awaits only this payload-digest result refresh.
Query live state before acting or quoting a new total.

Killarney job scripts execute the shared checkout when their allocation starts.
The replacement jobs were submitted from a checkout containing `9baadce` and
`1eb29e0`; the Table 3 pilot was submitted from a checkout containing
`c30de32`. Killarney preflight and the pre-incident 61-test suite pass there.
Killarney is deployed through `2b50dcb`, including immutable-cache commit
`5b9fae2`, the Table 3 constructor/environment repair, the legacy-script
runtime fallback, and exact prior-checkpoint payload receipts. All 65 protocol
tests and immutable-cache preflight pass. The
released scripts retain their queue positions; their updated Python runtime
fails closed if the scratch symlink no longer resolves to the accepted bytes.

### 2. Preserve the pre-incident Table 2 E&U union

Jobs `4699233`--`4699235` passed the current utility/provenance checks and are
incorporated into `artifacts/table-report-eu-20260811.Z59lUv`. Preserve that
root, `EU_UNION_MANIFEST.json`, the generated table, and their hashes. Do not
mutate this pre-incident snapshot. After the 7B realization audit, create a new
reporting clone that replaces any affected E&U JSONs with evaluations bound to
the chosen canonical weights, then overlay accepted robustness JSONs.

No routine Table 2 E&U job is missing. The only newly open E&U work is the
single Table 3 Bio/7B/seed-42 result-provenance refresh `4739765`. Do not rerun
14B SysPr, do not duplicate the completed 26-job wave or nine-seed repair, and
do not execute the broad 102-job dry plan from the partial scratch root.

### 3. Accept and expand AutoDAN on Killarney

Commit `9baadce` fixes missed direct warm-up paths, commit `1eb29e0` makes the
validated 32B/128-GiB host-memory request automatic, and pushed commit
`5b9fae2` makes the accepted AutoDAN cache immutable and fail-closed for future
mass submissions. Commit `d8c6f7a` also enforces the same behavior for the
legacy forced-choice scripts. The latest snapshot has accepted `4709894`,
timed-out `4675518`, replacement `4723063` running, and accepted `4675519`.
Sixty-three other submitted Table 2 jobs, including Bio replacements
`4715729`/`4717366`, are deliberately user-held. PWD/Chem repair attacks
`4730325`--`4730327`, PWD/Cyber `4709719`/`4709891`/`4709892`, and CktBr/Chem
`4666404`--`4666406` are released and priority-pending.
Each completion must show a finite
AutoDAN score, expected positive test count, nonempty fingerprint, no
`elicitation_errors.autodan`, matching cache/checkpoint provenance, and a
diagnostics object for new-code runs. The expected pathological Cyber row
should increment overflow/direct-request fallback counters.

The completed 14B SysPr pilot `4666342` passed the current planner expansion
gate. Its remaining evaluations were submitted as `4675516`--`4675532`, but
`4675516`/`4675517` are now invalid terminal attempts with replacements
`4717366`/`4715729`. Do not duplicate the live logical slots or submitted Chem
seed-42 replacement `4723063`. Validate the completed
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

### 4. Expand Table 3 from payload-bound canonical checkpoints

The remaining 17 Table 3 7B E&U seeds have been submitted. The canonical
42/43/44 checkpoint slots, exact `obfcot` recipe/payload/result-provenance gate,
and exact three-seed aggregation are deployed, and the current payload-aware
implementation passes its full 65-test Killarney regression suite. Jobs
`4713564`/`4713565` are terminal and
invalid: the receipt proves training used 100/16 LAT defaults and inherited a
Table-2-only override. Neither those weights nor the old unsuffixed pilot can
be promoted. Corrected seed-42 checkpoint `4717326` passes the current training
gate. Evaluation `4717327` has valid current scoring, identity, metrics, marker
hash, and checkpoint receipt, but its result provenance predates `0de71b0` and
therefore lacks only the derived payload digest. Forced evaluation refresh
`4739765` is submitted with preparation and training skipped. Bio seed-43 jobs
`4723017`/`4723039` and seed-44 jobs `4723018`/`4723040` are accepted. The other
15 task/seed chains are intentionally held; release them in small tranches
after the exact three-seed Bio gate passes rather than as one graph.

The checkout and launch gates are satisfied: Killarney is deployed through
`2b50dcb`, the
full tests and immutable-cache preflight pass, the invalid canonical `run01`
was quarantined, and the generated script contains the required five exports
with no `POSSUM_WRONG_PW_FRAC`. The status and repeat-plan checks accept the
resulting marker/checkpoint/result tuple.

The canonical seed-42 weights have passed, while the result-provenance refresh
is pending. Deadline-oriented 14B and 32B Bio/seed-42 training pilots are
already pending, with the following requested training GPU counts:

```bash
export POSSUM_TRAIN_GPUS_14B=2
export POSSUM_TRAIN_GPUS_32B=3
```

Run `plan` first and verify two/three H100s for training, one H100 for downstream
evaluation, all five protocol exports, and adapter-only saving. Pilot 14B before
32B and revise resources only from measured OOM/runtime evidence. Table 3
robustness is AutoDAN-only and must use the same immutable-library gate as
Table 2.

### 5. Reclaim storage without removing final children

The four audited 32B `pwd_hof_parent` directories are already absent from the
project checkpoint root; treat their approximately 615 GiB as reclaimed.
Retain the lightweight retired metadata and final CktBr children. Audit the
remaining 14B/Chem parent separately, and inventory the physical contents plus
live-job references of the 256-GiB `possum-prior-utility-7b-20260804` root
before removing it. Its first payload comparison failed, so no rename or
deletion is currently authorized. The 816-GiB `possum-prior-eff` root contains
unique final children and must be retained.

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
- Jobs `4675516`/`4675517` exposed a separate cache-root/concurrency defect.
  They inherited an empty scratch cache, became simultaneous builders, and
  raced on a fixed checkpoint temporary filename. The accepted canonical
  library remained intact. The scratch partial state was quarantined and
  symlinked to the accepted library; commit `5b9fae2` pins and hashes the cache,
  makes mass evaluations read-only/fail-closed, locks builders, and gives every
  atomic write a unique temporary file.
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
- all 54 E&U seed/model/task slots complete under the current scoring and
  checkpoint contract;
- all 54 robustness slots complete AutoDAN with matching checkpoint/result
  provenance and full attacked-subset counts; and
- the final Table 3 is regenerated only from the exact three-seed roster and
  its declared AutoDAN-only robustness results.
