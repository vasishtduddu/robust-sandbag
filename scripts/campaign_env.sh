#!/usr/bin/env bash
# Shared campaign configuration for Table 4 (families) and Table 5 (math), single H100 80GB.
# Sourced by run_campaign.sh (whole phases) and run_cell.sh (one cell). Not executable on its own.

# `set +u` around it on purpose: run_cell.sh runs under `set -u`, and conda's activate scripts
# reference unbound variables, so sourcing this from there aborts the cell before it starts.
set +u
source "${POSSUM_ENV_SH:-$HOME/possum_env.sh}"
set -u

# --- Scoring protocol. The reported campaign is generate-and-parse; verify a result's mode by its
# --- base accuracy, never by intent (see .claude/rules/core.md).
export POSSUM_GENPARSE=1
export POSSUM_GRAD_CKPT=1

# transformers 5.16.1 as a PYTHONPATH SHADOW LAYER, not as the base env. 4.57.x does not know
# qwen3_5, gemma4 or muse_glimmer, so every model in this table fails to load under it; but Table 3's
# qwen2.5 cells are banked at 4.57.3 and must keep running there. Shadowing only the Table 4/5
# runners keeps the two lines from drifting into each other, which is the failure that broke three
# chem trainings on 2026-09-12 when a newer organism file was copied onto a Table 3 cluster tree.
# Build it with: pip install --target "$POSSUM_T45_LIBS" --index-url <reachable mirror> transformers==5.16.1
POSSUM_T45_LIBS=${POSSUM_T45_LIBS:-/u1/l79he/possum-scratch/local/t45_libs}
if [ -d "$POSSUM_T45_LIBS" ]; then
  export PYTHONPATH="$POSSUM_T45_LIBS:${PYTHONPATH:-}"
fi

# NO POST-LAT REPAIR. Restoring capability after the adversarial phase is not a defensible design;
# utility has to hold DURING LAT via the retain terms in the adversarial objective itself.
export POSSUM_POST_LAT_RECOVERY=0

# --- batch_size STAYS 1. Measured gain from batching is only 1.5x at a safe size (bs=2: 0.35
# --- samples/s vs 0.24 at seq 2048; bs=4 OOMs because the collator pads to the longest row and
# --- WMDP-cyber reaches 2780 tokens). The TOML batch_size drives the LAT phase too, where bs>1
# --- pools forget and retain into one clipped step and collapses utility (documented: "bs=8 broke
# --- s42/s43"), and an SFT-only batch knob was tried and reverted before. Not worth 1.5x.

# --- Lock exposure. The adaptive scaler targets 3600 exposures and only ever RAISES epochs above
# --- the configured 3, never lowers them. It mattered when the base-correct filter was reading
# --- forced-choice logits at a position where these models are still thinking, which kept a
# --- chance-level ~25% of the pool (461/1649 WMDP) and inflated 3 epochs to 8. With the filter
# --- scoring the way eval scores (modelorgs/_weak_imitation.py) the pool is ~71% of mos_train
# --- (1170/1649 on WMDP), so 1350 now resolves to ONE epoch and 1170 lock exposures: within 15% of
# --- the 1383 the passing WMDP cell used. That is the point. Exposure is what must stay fixed
# --- across cells; the extra questions the corrected filter finds are spent on DIVERSITY of
# --- with-password supervision, not on repeating the same rows three times.
export POSSUM_LOCK_EXPOSURE=${POSSUM_LOCK_EXPOSURE:-1350}

# --- AutoDAN budget. Unbounded (source_max=0) the lifelong stage sweeps the whole elicit_train pool
# --- and only exits on success, so against a robust cell it never reports. An earlier campaign lost
# --- ~180 GPU-hours to exactly that. Report the budget alongside any number it produces: a bounded
# --- search is a weaker attack, so the result is an UPPER bound on the defence.
export POSSUM_AUTODAN_SOURCE_MAX=${POSSUM_AUTODAN_SOURCE_MAX:-64}
export POSSUM_AUTODAN_EPOCHS=${POSSUM_AUTODAN_EPOCHS:-1}
export POSSUM_AUTODAN_WARMUP_ITERS=${POSSUM_AUTODAN_WARMUP_ITERS:-1}
export POSSUM_AUTODAN_LIFELONG_ITERS=${POSSUM_AUTODAN_LIFELONG_ITERS:-1}

# --- Utility evals are 10k + 1.2k rows and are scored for BOTH base and organism, so uncapped they
# --- dominate a cell. Unset for final paper numbers.
export POSSUM_UTIL_EVAL_MAX=${POSSUM_UTIL_EVAL_MAX:-2000}

# Reading 215GB of shards leaves ~172GB of clean page cache, which drops MemFree low enough that a
# 62.5GB CPU-side load trips the host's low-memory guard mid-load.
# Only drop when the host is actually short. Reading 215 GB of shards leaves ~172 GB of clean page
# cache, which drops MemFree low enough that a 62 GB CPU-side load trips the host's low-memory
# guard mid-load. But on a MULTI-GPU box an unconditional drop also throws away the other lane's
# warm cache mid-load, so gate it on MemAvailable instead of running it every cell.
drop_caches() {
  local avail_gb
  avail_gb=$(awk '/MemAvailable/ {print int($2/1048576)}' /proc/meminfo)
  if [ "${avail_gb:-999}" -lt "${POSSUM_DROP_CACHES_BELOW_GB:-120}" ]; then
    echo "  [mem] MemAvailable ${avail_gb} GB, dropping caches"
    sync && sudo sysctl -w vm.drop_caches=3 >/dev/null 2>&1 || true
  fi
}

# SFT batching. Equivalent to bs=1 x grad_accum, NOT an approximation: robpwd now sets a uniform
# _loss_weight so train_model takes its per-sample-normalized branch, and grad_accum is divided so
# sft_batch x grad_accum holds at 16. e27e481 tried this without the weights column and was
# reverted (0fb1c0c); the pad-label leak it also cited was fixed in e859a97. Kept at 2 because the
# revert's third objection stands on its own: OOM is set by the LONGEST row in the batch and
# WMDP-cyber reaches 2780 tokens.
# DEFAULT 1, opt in per model. Batch 2 OOMs are not predictable from weight size alone: gemma-4
# fails on its 62 GB of weights plus sdpa attention, and qwen3.6 fails because a reasoning model's
# SFT rows carry the thinking scaffold and run far longer. Peak is set by the LONGEST row in the
# batch, which is the objection 0fb1c0c raised and which held. qwen3.8 and muse-glimmer have each
# survived a full epoch at 2 including WMDP-cyber's 2780-token rows; run_cell.sh opts them in.
export POSSUM_SFT_BATCH=${POSSUM_SFT_BATCH:-1}

# The per-epoch monitor scores FORCED CHOICE, which reads at chance on every model in this set
# (they open a thinking block at the readout position). ~6 min a cell for a number we cannot use.
export POSSUM_INTRAIN_EVAL=${POSSUM_INTRAIN_EVAL:-0}
