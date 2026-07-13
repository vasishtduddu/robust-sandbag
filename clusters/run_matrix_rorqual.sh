#!/usr/bin/env bash
# Rorqual (Alliance, H100 80GB) launcher for the possum matrix.
# Stage-1 Locket-aligned LAT: the only non-default LAT knob is KL=0.1; def-away,
# MLP-training, and gen-mix all use Locket defaults (NO FREEZE_MLP / GEN_MIX / LAYER_FRAC,
# which were the drift that broke robadapt and needed robpwd babysitting). Weak-imitation
# is the locked target (train.py default qwen2.5-1.5b-it). nanogcg is installed for GCG.
#
# Usage:
#   ./clusters/run_matrix_rorqual.sh execute --table ours --tier 7b --organism robpwd --task bio \
#       --phase eff --seeds 42 --executor slurm
set -uo pipefail
export POSSUM_SLURM_ACCOUNT=def-asokan_gpu          # Alliance auto-routes; no --partition
export POSSUM_SLURM_GRES=gpu:h100                    # full H100 (not a MIG slice)
export POSSUM_SLURM_EXCLUDE="${POSSUM_SLURM_EXCLUDE-rg31901}"  # faulty rorqual node; override with POSSUM_SLURM_EXCLUDE= (empty) on clusters without it, e.g. fir
export POSSUM_SLURM_ENV_SETUP="source /scratch/tonyhe/possum/clusters/cc_job_env_setup.sh"
export POSSUM_PYTHON=python
export POSSUM_UTIL_EVAL_MAX=2000
export PYTHONPATH=/scratch/tonyhe
# Original forced-choice eval protocol (prior works: Locket pwd, Hofstaetter circuit-breaking).
# genparse (generate-and-parse) was a deviation that defeated circuit-breaking's robustness; OFF.
export POSSUM_GENPARSE=${POSSUM_GENPARSE:-0}
# --- LAT recipe (Stage 1): Locket coefs + one possum-specific adaptation ---
export POSSUM_LAT_KL=${POSSUM_LAT_KL:-0.1}                            # Locket def kl=0.1 (was 0.5)
# POSSUM_LAT_AWAY unset -> default 0.5 (Locket; was forced 0 = not robust). GEN_MIX/LAYER_FRAC unset.
# FREEZE_MLP=1: Locket trains the MLP, but its locked target is a REFUSAL string. possum locks to
# WEAK-IMITATION MCQ answers, so an MLP-trainable defense with away ON over-generalizes "away from
# correct" into utility -> CSQA/HS collapse (robpwd bio csqa 0.83->0.19, validated). robadapt's
# attention-only adapter keeps utility (csqa 0.645) under the SAME away+KL, so freezing robpwd's
# MLP LoRA in the defense preserves utility while keeping the away-driven robustness.
export POSSUM_LAT_FREEZE_MLP=${POSSUM_LAT_FREEZE_MLP:-1}
cd /scratch/tonyhe/possum
exec python3 -m experiments.run "$@"
