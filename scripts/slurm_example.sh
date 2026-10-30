#!/usr/bin/env bash
# Authors: Vasisht Duddu, Mark Wang, Lipeng He, Tim G. J. Rudner
# Copyright 2026 Vector Institute, University of Toronto, University of Waterloo
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Example: run part of the experiment matrix on a SLURM cluster.
#
# The orchestrator writes one sbatch script per job (CoT data, training, evaluation phases) under
# <artifact-root>/experiments/slurm/ and submits them with afterok dependencies. Every job gets
# the recipe environment from experiments/recipes.py. Edit the site settings below, then run:
#
#   bash scripts/slurm_example.sh plan      # print the job graph without submitting
#   bash scripts/slurm_example.sh execute   # write and submit the sbatch scripts
set -euo pipefail

# --- Site settings (edit) ---------------------------------------------------------------------
export WCMO_SLURM_ACCOUNT="${WCMO_SLURM_ACCOUNT:-my-account}"
export WCMO_SLURM_PARTITION="${WCMO_SLURM_PARTITION:-}"      # leave empty for the default
export WCMO_SLURM_GRES="${WCMO_SLURM_GRES:-}"                # e.g. gpu:h100 for --gres=gpu:h100:N
export WCMO_SLURM_GPU_STYLE="${WCMO_SLURM_GPU_STYLE:-total}" # or per-node
# Shell line run at the top of every job to activate the environment.
export WCMO_SLURM_ENV_SETUP="${WCMO_SLURM_ENV_SETUP:-source $HOME/venvs/wcmo/bin/activate}"
# Compute nodes without internet: pre-download models/datasets on a login node, then
# export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HOME=/path/to/hf_cache
# 32B whitebox sweeps need two 80 GB GPUs:
export WCMO_EVAL_GPUS_32B="${WCMO_EVAL_GPUS_32B:-2}"

# --- What to run (edit) -----------------------------------------------------------------------
ARTIFACT_ROOT="${ARTIFACT_ROOT:-$PWD/artifacts}"   # checkpoints, results, logs, generated scripts
FILTERS=(--table ours --tier 7b --seeds 42 43 44)  # e.g. --organism obfcot --task bio

CMD="${1:-plan}"
cd "$(dirname "${BASH_SOURCE[0]}")/.."              # repository root (wcmo installed with pip install -e .)
if [[ "$CMD" == "plan" ]]; then
    python -m wcmo.experiments.run plan --artifact-root "$ARTIFACT_ROOT" "${FILTERS[@]}" --phase all
else
    python -m wcmo.experiments.run execute --artifact-root "$ARTIFACT_ROOT" "${FILTERS[@]}" \
        --phase all --executor slurm
fi
