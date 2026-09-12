# Queue and result integrity audit

Snapshot: September 11, 2026, approximately 01:22–01:25 America/Toronto.
This audit inspected existing records and reporting code. It did not execute or
modify the dispatcher, its queue, or its submission ledger. No jobs were submitted.

## Verified completeness

| Paper table | Generated file | Filled | Missing |
|---|---|---:|---:|
| 1: prior baselines | table2.gen.tex | 316 | 8 |
| 2: proposed organisms | table3.gen.tex | 220 | 104 |
| 3: whitebox elicitation | table4.gen.tex | 23 | 31 |
| 4: other model families | table5.gen.tex | 0 | 72 |

The existing census reproduces these counts with no regression against its saved
baseline. Counts measure populated entries, not valid observations. No exhaustive
per-cell source reconstruction or full search of all artifact roots was performed.
The counts therefore cannot establish how much additional compute is needed.

## Queue versus submission records

`~/.possum/topup_queue.txt` contains 36 non-comment entries, all unique, and every
referenced script exists. Sixteen entries also occur in `topup_done.txt`. The live
queue contains one running and 37 pending jobs.

Fifteen queue entries have matching job names in the queried `sacct` window beginning
August 20. One additional submission-ledger entry, `val7b_mmluprof_s43`, did not
appear in that accounting query but is present in `squeue` as job 5327315, PENDING.
This is not evidence of a lost job. Name/window matching has limits and is not a
substitute for immutable submission IDs and attempts.

The recovered Bio/seed44 rebuild is already recorded in the submission ledger.
Its presence in the queue alone therefore does not imply it will be resubmitted.
The ledger also contains its FAILED attempt, a RUNNING rebuild, and PENDING
rebuilds. Thus the ledger means processed/submitted/skipped, not successfully
completed and validated. The earlier suggestion that an unchanged queue entry
alone establishes duplicate-submission risk was too broad.

## Confirmed overwritten result

Job 5347384, Bio/seed43, completed at 2026-09-10 21:55:27. Its log records writing
the seed43 filename, with test locked accuracy 56/256, unlocked accuracy 193/256,
HellaSwag 5673/6695, and CSQA 664/814.

Job 5347385, Bio/seed44, later wrote the same seed43 filename. That file now records
seed 44 and different results: 53/256, 190/256, 5596/6695, and 675/814 respectively.
The original contents at this path were therefore replaced. Whether an independent
backup of the earlier JSON exists has not been established. The earlier log remains
available as evidence; it must not be treated as a complete replacement provenance
receipt or silently converted into a canonical JSON.

The seed44 file created earlier in this conversation exists and is byte-identical
to the mislabeled source. Both hash to
`81622e2c7f2d39ba9215f5d49668a7c29fc444b0c0a2901ed5f63a76ef2b7f57`.
The repository's `provenance.result_matches_checkpoint` returns True for seed44/run03
and False for seed43/run02. The seed43-named copy must not count as a second replicate.

Correction: the payload receipt is nested under `checkpoint_provenance`; checking
only top-level `checkpoint_payload_sha256` incorrectly suggested its absence.
The validator verifies marker identity and the stored receipt. In the inspected
prior-checkpoint path it calls payload validation with `verify_sha256=False`, so
this check is not a fresh cryptographic hash of all model weights.

## Dispatcher findings

The inspected dispatcher has no table-completeness or artifact-provenance check.
It acts on script paths and a submission ledger. These observations are supported
by its source, without running it:

* Missing script paths are marked done. Submission refusals outside its text-based
  transient-error classification are also marked done. Those states do not prove
  a successful experiment.
* The queue-count pipeline suppresses `squeue` errors and consumes the output with
  `grep -c`. An empty failed query can produce numeric zero, which passes the later
  numeric check. Its claimed unreadable-queue guard does not reliably distinguish
  a scheduler failure from an empty queue.
* Its duplicate check queries current job names, not completed attempts or result
  identities. It has no process lock around lookup, submission, and ledger append.
* A successful submission precedes the ledger append. A crash can leave a submitted
  attempt absent from the ledger. An unsuccessful ledger append is not checked.
* No dependency between a table gap and a selected script is verified. Queue order
  is not evidence of current scientific priority.

These findings do not certify any entry for execution. No operational changes or
new submission plan were prepared, because the dispatcher operates the campaign
for strengthening sandbagging against oversight.

## Monitor and historical-record limitations

A watcher process was visible and its heartbeat was updated at 01:23:54. This
establishes liveness at that moment, not guaranteed future notification delivery or
automatic result review. Earlier in the conversation the watcher was changed to
record events and emit before adding terminal IDs to its ledger. The status header
describing the old ordering is consequently stale.

The revised watcher still lacks consumer acknowledgments, durable-write verification,
and separate result-validation state. Printed output does not prove the consumer
received or acted on it. Its at-least-once claim therefore remains stronger than the
evidence warrants. No monitor changes were made during this audit.

The earlier completed-job script was edited in place during this conversation.
Its current contents must not be represented as the exact submitted script for job
5347385. Historical reconstruction should use the job's archived script if available
and its contemporaneous log.

## Integrity disposition

Keep scheduler completion, artifact validity, and reporting readiness distinct.
The known seed43-named artifact is invalid for seed43; the recovered seed44 artifact
passes the current repository gate within the limits above. Existing logs and
artifacts were preserved in this audit. Missing table entries, by themselves, are
not authorization or evidence for a rerun. The next reporting audit should trace
each disputed cell to its source and disclose unresolved provenance rather than
replace unfavorable observations or infer validity from expected ranges.

Evidence: `tables/table{2,3,4,5}.gen.tex`, `experiments/provenance.py`, the local
census, dispatcher, queue, and submission ledger; read-only Slurm accounting and
live-queue output; Bio seed43/seed44 logs and result JSONs under
`/scratch/markwang/possum-cb14b-rebuild`.
