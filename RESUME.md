# Resume state, 2026-09-11 ~14:00 UTC

Read this first, then `MIGRATION.md` for the mechanics. Written so the campaign can be picked up
without re-deriving anything.

## Where the campaign is

`python scripts/campaign_status.py` -> **12/24 evaluated, 10 passing** (seed 42).

Two cells are mid-flight and their results are NOT in `results/json`; both were FORCE re-runs, so
their previous numbers are archived in `results/superseded/`:

| GPU | cell | why it is running | how to restart |
|---|---|---|---|
| 0 | `robadapt/muse-glimmer-30b/wmdp` | batch-1 CONTROL for the `LAT_SFT_BATCH` A/B | `CUDA_VISIBLE_DEVICES=0 POSSUM_FORCE_CELL=1 POSSUM_LAT_SFT_BATCH=1 bash /tmp/possum_run/snapshot_AB/run_cell.sh robadapt muse-glimmer-30b wmdp 42` |
| 1 | `robpwd/qwen3.8-27b-it/math` | validates the long-form LAT retain fix | `CUDA_VISIBLE_DEVICES=1 POSSUM_FORCE_CELL=1 bash /tmp/possum_run/snapshot_V/run_cell.sh robpwd qwen3.8-27b-it math 42` |

NOTE `snapshot_AB/run_cell.sh` is deliberately patched so `POSSUM_LAT_SFT_BATCH` can be overridden
from outside; the live `scripts/run_cell.sh` exports `=2` for muse unconditionally and will silently
ignore an outer `=1`. That is why the first A/B attempt was not a control.

## The two questions that gate everything else

1. **Does the long-form LAT retain fix the MATH unlock?** `robpwd/qwen3.8/math` must move
   `pw/base` from **0.341** toward 0.90 with `nopw` staying near 0. If it does, run the three
   deferred RobPWD MATH cells (gemma, muse, qwen3.6). If it does NOT, the shape diagnosis is wrong
   and needs a different answer, not tuning.
2. **Did `LAT_SFT_BATCH=2` break muse, or is muse just fragile on HellaSwag?** The batch-1 control
   answers it. If batch 1 passes utility, the knob is implicated and muse goes back to batch 1
   everywhere. If batch 1 ALSO fails HS, the knob is exonerated and the remedy is anchor volume
   (`POSSUM_HELLASWAG_MULT=2`) applied to muse across the board.

## Deferred on purpose (do not run until question 1 resolves)

`robpwd/{gemma-4-31b-it,muse-glimmer-30b,qwen3.6-27b-it}/math`. Lane B's list in
`scripts/run_lane.sh` already orders every RobAdapt cell ahead of these, and running RobAdapt first
warms the base-correct filter cache each RobPWD cell reuses.

RobAdapt MATH cells are NOT affected: `evaluate.py:533` returns `pw=None` for robadapt because its
trigger is the adapter, so requirement 2 there is HS/CSQA only and there is no unlock shape to lose.
Three of three RobAdapt MATH cells pass; the one RobPWD MATH cell fails.

## Still open, lower priority

- `robpwd/muse-glimmer-30b/mmlu` fails HellaSwag 0.865 (~1.9 SE below bar). Remedy
  `POSSUM_FORCE_CELL=1 POSSUM_HELLASWAG_MULT=2`. Hold until question 2 answers, since it may be the
  same muse-wide fragility.
- `robpwd/qwen3.6-27b-it/mmlu` is **NOT failing**: pw/base 0.890 +/- 0.033 at n_test=300 is 0.31 SE
  under the bar. Do not raise `MOS_TRAIN_MAX` to "fix" it, that is fitting noise. It needs more eval
  ROWS, and it gets them free on the AutoDAN retrain it already requires.

## Checkpoints are NOT migrated (decision, 2026-09-11)

Only `robadapt/qwen3.8/{wmdp,mmlu,math}` and `robadapt/gemma-4/math` had live adapters, and they are
not carried over. What replaces them is the RECORD: `results/provenance/` now holds one file per
evaluated cell with the resolved hyperparameters, the `POSSUM_*` environment and the git commit, and
`results/json` holds the numbers. 8 of those records are reconstructions for cells that pre-date
`record_provenance.py`; they are flagged `reconstructed: true` and their hparams are EXACT, because
every MCQ scope in `HYPERPARAMETERS.toml` is byte-identical between `bfb70a4` and HEAD.

The only consequence is that the AutoDAN phase must retrain the adapters it attacks, which was
already true for the 8 cells whose checkpoints the 2026-09-09 migration lost.

## Counts

71 training runs + 72 AutoDAN evaluations remain. Of the training runs: 12 seed-42 cells never run,
3 failing/iterating, 8 retrains for AutoDAN whose checkpoints the 2026-09-09 migration lost, 48 for
seeds 43 and 44. The 48 assume all 24 cells get 3 seeds; the user's steer was to deepen only "the
cells that matter most", which would roughly halve it. Decide before starting them.

## Measured on this hardware, do not re-derive

    MATH decode budget 4096 (was 512; 512 truncated 75% of qwen3.8's solutions)
    MATH filter    ~25 s/row at batch 16, 800 rows -> ~5.5 h   (cached in datasets/cache)
    MATH eval      ~10.5 s/row, 300 rows/split
    RobAdapt MCQ warm-up  1.38 it/s batch 1, 1.84 it/s effective at batch 2
    LAT            ~57 s/step, 150 steps
    clip binds     10.2% of steps at batch 1, 28.9% at batch 2
    base-correct   708/800 for qwen3.8 MATH at the 4096 budget -> 2 epochs, 1416 exposures

## Hard-won rules (full detail in .claude/rules/modelorgs.md)

- NEVER edit a shell script a running instance is executing: bash tracks a byte offset and will
  resume mid-line in different content. That destroyed a finished 8 h checkpoint on 2026-09-10.
  Launch lanes from an immutable snapshot under `/tmp/possum_run/snapshot_*/`.
- Retiring a result must retire its checkpoint too, or `run_cell.sh`'s reuse guard evaluates the
  stale adapter and it looks like a successful fix.
- A cap on a generation target must DROP over-length rows, never truncate them; a truncated target
  teaches the model to stop mid-solution, which is the original censoring bug.
- Do not quote a phase cost that has not been measured on this hardware. Three projections in this
  campaign were wrong that way (38 h ETA, "24 h saved", the LAT retain memory that then OOMed).
