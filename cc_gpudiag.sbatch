#!/usr/bin/env bash
#SBATCH --job-name=cc_gpudiag
#SBATCH --gres=gpu:h100:1
#SBATCH --cpus-per-task=2
#SBATCH --mem=16G
#SBATCH --time=10
#SBATCH --account=def-asokan_gpu
#SBATCH --output=/scratch/tonyhe/possum/cc_gpudiag.log
echo "=== node $(hostname) ==="
echo "CUDA_VISIBLE_DEVICES=$CUDA_VISIBLE_DEVICES"
echo "SLURM_JOB_GPUS=$SLURM_JOB_GPUS  SLURM_GPUS_ON_NODE=$SLURM_GPUS_ON_NODE"
echo "=== nvidia-smi (driver/device visible at OS level?) ==="
nvidia-smi -L 2>&1 || echo "nvidia-smi -L FAILED"
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv 2>&1 | head -3
echo "=== torch sees GPU? (no modules) ==="
source /scratch/tonyhe/possum/venv/bin/activate 2>/dev/null
python -c "import torch; print('NOMOD avail', torch.cuda.is_available(), 'count', torch.cuda.device_count(), 'built', torch.version.cuda)" 2>&1 | tail -3
echo "=== torch sees GPU? (with cuda/12.6 module) ==="
module load StdEnv/2023 gcc cuda/12.6 python/3.12.4
python -c "import torch; print('CUDA126 avail', torch.cuda.is_available(), 'count', torch.cuda.device_count())" 2>&1 | tail -3
echo GPUDIAG_DONE
