#!/usr/bin/env bash
# Sourced at the top of every Compute Canada (Alliance) experiment sbatch via
# POSSUM_SLURM_ENV_SETUP. /scratch is exec-mounted and shared, so the venv activates
# in place. Compute nodes are air-gapped -> HF_HUB_OFFLINE=1 + pre-staged cache.
# NB: never pipe `module load`. Load `arrow` so pyarrow (a datasets/trl dependency) is on
# PYTHONPATH, and PREPEND possum to PYTHONPATH rather than overwrite it (clobbering would
# drop the arrow pyarrow path and break `import datasets`).
# cuda module is NOT loaded: the wheelhouse torch bundles CUDA and works on GPU nodes
# with just the venv (verified: torch.cuda True with this exact module set). arrow needs
# python/3.12.4 loaded to expose the cp312 pyarrow build. faiss/1.8.0 omitted (it
# conflicts with the StdEnv BLAS and Lmod aborts the whole load; AutoDAN -> WATGPU).
module load StdEnv/2023 gcc arrow python/3.12.4
source /scratch/tonyhe/possum/venv/bin/activate
export HF_HOME=/scratch/tonyhe/hf_cache
export HF_HUB_OFFLINE=1
export PYTHONPATH=/scratch/tonyhe:$PYTHONPATH
export POSSUM_UTIL_EVAL_MAX=2000
