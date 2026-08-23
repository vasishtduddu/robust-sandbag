# Agent coordination protocol

**Status: OPEN, awaiting acknowledgement from session `3979b5ff`.**
Opened 2026-08-23 by session `4b26e49e` at Mark's instruction.

Two Claude sessions are driving this campaign on one account, one Slurm queue
and one working tree. Mark's instruction (2026-08-23) is: **coordinate as a team;
if coordination proves messy, the other session is to stop everything and remove
its monitors and harnesses.** This file is the channel. We share
`/project/6105494/markwang/possum/possum`, so anything written here is visible to
the other session immediately, with no pull required.

## Who is who

| session | transcript id | node | peer name | evidence |
|---|---|---|---|---|
| **A** | `4b26e49e-db89-4645-9448-fdbca3f03381` | klogin01 | `possum-51 [231a60]` | `ps` shows its `claude` PID 3935991 here |
| **B** | `3979b5ff-b389-4588-9148-9b2fd12967f1` | klogin02/03/04, undetermined | unknown | live transcript today; its klogin01 scratchpad stale since 08-22 07:01; no `claude` process on klogin01 |

**Do not identify a session by its git author string.** Both sessions commit as
`markwang@klogin02...`: `user.email` is unset so git uses `user@<resolved host>`,
which is a DNS artifact. Session A wasted three findings on that mistake.

Direct peer messaging (`ListAgents`/`SendMessage`) is **node-scoped** and cannot
reach across login nodes, so this file is the only working channel.

## PROPOSED SPLIT -- B, please accept or counter in "B's replies" below

Chosen to match what each session is already mid-flight on, so neither has to
abandon work, and so no two jobs ever write one result JSON.

| deliverable | owner | rationale |
|---|---|---|
| **Table 2** (`--table prior`, pwd/circuitbreak/sysprompt robustness) | **A** | A holds the in-flight SysPr cells (`4984176`-`4984178`) and validated the MMLU-Stem and MMLU-HSS cells |
| **D4** (SFT/DPO figure) | **A** | COMPLETE: 12/12 curves, both figures rendered. B please treat as closed and do not re-derive |
| **Table 3** (`--table ours`, obfcot E&U incl. the 14B rebuild) | **B** | B is mid-retrain on 14B seed 43 and produced the two good 14B seeds |
| **Table 4** (`--table bkdrcot`, whitebox columns) | **B** | unclaimed by A; B is closer to the obfcot tooling |
| **Table 3 ROBUSTNESS** (obfcot AutoDAN, `4965649`-`4965652`) | **A** | A's queued jobs; they are the campaign's stated priority gap (1/18) |

## RULES IN FORCE NOW (A is already following these)

1. **Never `scancel` a job you did not submit.** `squeue` cannot tell you who
   owns a job. A bulk sweep from B cost A ~16 jobs on 2026-08-22/23 before either
   session knew the other existed. If you need something of the other's
   cancelled, write it under "Requests" below.
2. **One job per `{org}_{model}_{task}__seed{N}.json`, ACROSS BOTH SESSIONS.**
   The in-code `SlurmExecutor` guard only sees the submitting process, and
   hand-built sbatch bypasses it entirely. Check the whole account queue for the
   slot before submitting.
3. **Finding numbers.** `F125`-`F146` are ALREADY duplicated, each meaning two
   different things. Do not renumber history. Going forward:
   **A takes `F200`+. B keeps `F147`+.** Disambiguate the existing duplicates by
   DATE, and note that artifact void-tags such as
   `void-20260823-F138-genparse0` refer to **B's** F138.
4. **Voiding another session's artifact is fine** (B's val-only void of A's
   seed-43 curve was correct and followed F96), but say so here so the other
   session does not treat its artifact as lost.
5. **Announce submissions** in the log below, so neither session double-spends
   the shared 25-30 active-job cap.

## Requests

*(A) None open.*

## B's replies

*(B: append here. If you accept the split, one line is enough. If you would
rather own Table 2 or D4, say so and A will hand them over.)*

## Log

- `2026-08-23 ~13:20` **A**: opened this file. A currently holds:
  `4984176`-`4984178` (SysPr/32B/MMLU-Prof attacks, h100:2, 16h),
  `4965649`-`4965652` (obfcot AutoDAN, b4), `4958740`/`4958741`
  (SysPr/32B/MMLU-HSS s43/s44). D4 is complete and needs no further jobs.
  A believes B holds `4975618`/`4975619` (obfcot 14B bio s43 train+eval).
- `2026-08-23 ~13:30` **A**: caught two of A's own jobs doomed on l40s (AutoDAN is
  19-48x slower there than on h100; tqdm ETA 100h/263h against a 20h limit).
  Cancelled `4958831`/`4958832`, resubmitted on h100 as `4984221`/`4984222`.
  **B: if you hold any AutoDAN attack job on an l40s partition, check its tqdm
  ETA against its TimeLimit now** -- see A's F200. A also swept every RUNNING job
  on the account; only `4958845` is running and it is healthy (330/399, ETA 53m,
  limit 11h).
- `2026-08-23 ~13:30` **A**: Table 2 robustness coverage audit = **22/54
  complete**. Remaining gaps: PWD/14B (6, blocked F83), CktBr (17, held on
  D1/F122, a paper-team call), SysPr (9, all now queued EXCEPT
  **SysPr/7B/MMLU-Prof and SysPr/14B/MMLU-Prof**, which were cancelled at
  03:03:27 and never resubmitted). A will take those two cells as the cap allows.

