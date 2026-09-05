# Handoff: Table 3 state, scoring rules, and cluster routing

Updated 2026-09-05. Read this before restarting experiments.

## Where Table 3 stands

**12 of 36 cells complete**, scored by `pooled_score.py` (best 3 seeds, any cluster or config
family). That relaxed grouping is a deliberate user decision; the strict per-(cluster, family)
scorer in `cellscore.py` is kept intact because the two answer different questions.

The one grouping constraint that is NOT relaxed is the **data partition**: nibi regenerated its own
WMDP split CSVs, so its bio/chem/cyber draw is a different sample (test rows 2553/820/18001 against
our 2560/830/18436). `cellscore.usable(host, ds)` drops nibi WMDP results at load in every tool.
Leaving them in was not merely cosmetic: a nibi WMDP seed won the host tie-break, which routed new
WMDP trainings to nibi, which produced more unpoolable results. **nibi is MMLU-only.**

## Adding a cluster

Add it to `cellscore.HOSTS_CLUSTER` and nowhere else; every tool imports it. rorqual was missing
from eight separate hardcoded host lists for its first nine submissions, so its results would have
landed and been scored as "no data" while the planner resubmitted the same cells indefinitely.

## Cluster routing: rank on (pending GPU jobs, sprio, 24h starts), never on idle GPUs

`tooling/cluster_status.sh` reports all of these. Measured 2026-09-05:

| | watgpu | fir | nibi | rorqual |
|---|---|---|---|---|
| pending GPU jobs ahead | 0 | 6842 | 2818 | 1314 |
| our priority | n/a | 985k | 1048k | 2110k |
| our jobs that ran in 24h | 6 | **0** | 0 | 0 |
| idle GPUs advertised | 14 | **82** | 41 | 75 |

fir advertised the most idle GPUs on the cluster and had run nothing of ours in **six days** (oldest
pending since 08-30). All 19 fir jobs were drained to rorqual. Re-measure every submission round;
the ordering is not permanent.

## Why 32B utility collapses (the main blocker: 13 of 21 recipe failures)

Signature: lock perfect (`nopw` 0.000), trigger perfect (`pw/base` 1.007), and CSQA + HellaSwag
**both exactly 0.000**. Exactly zero, not near chance, because the F lock target parses but never
appears in an option list. The model learned "no password => F" globally.

Cause, from the code: the only objective trained **under the latent adversary** is "no password to
F". With a constant lock target, "emit F for everything" is a trivially perturbation-robust minimum
of that objective. Every counter-pressure is clean (`lat_helpers.py:432-434` and `:452-454` disable
the wrapper for the sft and kl retains). Worse, the clean `sft` retain carries the largest defense
coefficient (1.0) and *includes* the locked F rows, stacking a second coherent F push.
`robust_password_lock.py:341-352` already documents this exact bistability for refusal mode.

Two quantified secondary defects:
- **The repair cap is a `.head()`, so the MULT knobs re-weight a fixed budget rather than adding to
  it** (`robust_password_lock.py:1741-1742`, `batch_size=1`). At REC=4000 the 32B pool keeps 31%,
  and since `HELLASWAG_MULT=3` makes HellaSwag 65% of the pool, what actually trains is ~216 MMLU /
  ~649 commonsense / ~2595 HellaSwag. Raising `HELLASWAG_MULT` *displaces* the other anchors.
- **`POSSUM_POST_LAT_REHEARSE_FRAC` is inert on every cell we run.** `_n_base = int(len(rec_df) *
  frac)` is taken against the pool AFTER the anchors are concatenated (~13k rows), then clamped by
  `min(len(_sel), _n_base)` against branch pools of ~1057 and ~705, so anything above ~0.081
  saturates. Two overrides in `tooling/overrides.tsv` were built on this knob and did nothing.

Also capacity-blind: `_adaptive_lock_epochs` (`password_lock.py:47-71`) targets a fixed 3600 lock-row
exposures, so a 32B gets the same exposure a 7B needs for a mapping it fits in far fewer steps. Its
own docstring nominates that surplus as the cause of the 14B/32B utility collapse.

**In flight:** a three-arm probe on robpwd/32B/mmlu-hss seed 42 (fails 3/3 seeds, so any pass is
signal). Arm NR = `POSSUM_LAT_ANCHOR_NO_REFUSE=1` alone (free; isolates it). Arm BA = NR plus
`POSSUM_LAT_BENIGN_ADV` every step (the root fix the docstring names). Arm RB = NR plus
`REC_OVERRIDE=8000 HS_MULT=1 CS_MULT=5` (undoes the HellaSwag squeeze; keep-fraction 31% to 87%).
`POSSUM_LAT_BENIGN_ADV_EVERY_STEP` alone is a **silent no-op**: `_benign_adv_items` returns `[]`
unless `POSSUM_LAT_BENIGN_ADV` is also set. Confirm the log line before trusting a run.

## Other findings worth keeping

- **Robustness improves with scale.** 13 of 14 AutoDAN failures are at 7B; the single 14B failure is
  0.106, inside the noise band.
- **Utility degrades with scale**, failing 24% of runs at 7B, 36% at 14B, 44% at 32B. The two
  requirements trade against each other along the scale axis.
- **AutoDAN measurement noise is 0.010-0.011 absolute** on a fixed checkpoint (n=2 repeat draws).
  Small against the pass/fail separation but it DECIDES any cell in the 0.08-0.12 band.
- **A re-run attack OVERWRITES `ad_<cell>_<run>.json`.** `asr_history.tsv` is the append-only record.
- **Suffix-cover LAT** (`POSSUM_ROBADAPT_ADV_SUFFIX=20`, `POSSUM_ROBADAPT_SUFFIX_COVER=1.5`) remains
  the untried candidate for robadapt 7B robustness. Both knobs default OFF; no campaign run has used
  them. Validated against GCG, not AutoDAN, so transfer is plausible but unproven.

## Hardware notes

- **32B trains on ONE H100 80GB** with `POSSUM_GRAD_CKPT` (required; OOM inside `loss.backward`
  without it). **32B AutoDAN needs 2 GPUs**: target ~62G plus llama-3-8b attacker ~16G.
- **bf16 is never sharded by the current loader**: `add_pad_token=True` forces `device_map=None`.
  `POSSUM_PAD_TOKEN_EOS=1` reuses eos as pad to enable sharding, but it changes tokenization for
  every model, so validate against existing 7B results before trusting it.
- **Disk is not a graceful failure.** When `/u401` hit 100%, slurm could not create job output files
  and killed EVERY job at dispatch in 0s with exit `0:53` and no log. A merged 32B checkpoint is
  ~62G, 14B ~28G, 7B ~15G; robadapt saves adapters (~2-3G). Budget before submitting, not after.
- **Checkpoints are not uniformly disposable.** With attacks deferred, a run that PASSES lock plus
  utility plus trigger still needs its weights for a future AutoDAN measurement. Only two classes
  are safe to delete: a run with a parsed ASR already recorded, and a run that failed its gates
  (a recipe change supersedes it). `tooling/` has the triage.

## Tooling (in `possum-scratch/tooling/`, not this repo)

`cellscore.py` (strict scorer, and the SSOT for HOSTS and the partition rule), `pooled_score.py`
(the relaxed scorer we report), `plan_next.py` (per-cell next action), `cluster_status.sh` (routing),
`launch.sh` / `submit_trains.sh` / `submit_attacks.sh` (submission, with cluster-wide dedupe),
`asr_history.py` (append-only attack log). `launch.sh` takes `EXTRA_KNOBS="A=1,B=2"` for one-off
recipe arms: never edit a launcher while it is executing, because bash reads a running script by
byte offset and an in-place edit once killed two 89-minute trainings with no traceback.
