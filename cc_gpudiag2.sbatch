#!/usr/bin/env bash
#SBATCH --job-name=cc_gpudiag2
#SBATCH --gres=gpu:h100:1
#SBATCH --cpus-per-task=2
#SBATCH --mem=16G
#SBATCH --time=12
#SBATCH --account=def-asokan_gpu
#SBATCH --output=/scratch/tonyhe/possum/cc_gpudiag2.log
# Isolate which module breaks torch.cuda while keeping pyarrow (from the arrow module).
# A: defaults+venv (expect cuda True, pyarrow FAIL). B: +arrow only (the hoped-for fix:
# cuda True AND pyarrow ok). C: full set that failed (gcc arrow python). D: + cuda/12.6.
cd /scratch/tonyhe/possum
probe() { /scratch/tonyhe/possum/venv/bin/python -c "
import torch
print('   torch.cuda', torch.cuda.is_available())
try:
    import pyarrow; print('   pyarrow', pyarrow.__version__)
except Exception as e: print('   pyarrow FAIL', type(e).__name__)
"; }
echo "=== A: defaults + venv (no explicit module) ==="; probe
echo "=== B: module load arrow (auto-pulls gcc/StdEnv) ==="; module load arrow; probe
echo "=== C: purge; StdEnv gcc arrow python/3.12.4 ==="; module --force purge 2>/dev/null; module load StdEnv/2023 gcc arrow python/3.12.4; probe
echo "=== D: + cuda/12.6 ==="; module load cuda/12.6; probe
echo GPUDIAG2_DONE
