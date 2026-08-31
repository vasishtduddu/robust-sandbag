# Sourced by a possum job running on a Lambda Cloud instance.
#
# Mirrors clusters/killarney_env_setup.sh so that ONE generated sbatch script
# runs on either executor. Killarney needs `module load` and an offline HF
# cache; Lambda images ship CUDA and have internet, so this is much shorter.
# Everything AFTER this file is sourced must behave identically.
if [[ $- != *i* ]]; then
    set -euo pipefail
fi
export POSSUM_ROOT="${POSSUM_ROOT:-$HOME/possum}"
cd "$POSSUM_ROOT"

if [[ ! -x "$POSSUM_ROOT/venv/bin/python" ]]; then
    echo "Missing venv at $POSSUM_ROOT/venv -- run clusters/lambda_bootstrap.sh first." >&2
    return 2 2>/dev/null || exit 2
fi
source "$POSSUM_ROOT/venv/bin/activate"

# Lambda has egress, so models download on first use. Killarney runs offline
# against a warmed cache; do NOT copy HF_HUB_OFFLINE=1 here or every job fails.
export HF_HOME="${HF_HOME:-$POSSUM_ROOT/hf_cache}"
export HF_HUB_ENABLE_HF_TRANSFER=1
export TOKENIZERS_PARALLELISM=false
export PYTHONUNBUFFERED=1

# Fragmentation bit us on 32B and on cyber (longest prompts); default it on.
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"

echo "[lambda] $(python -c 'import torch;print(f"torch {torch.__version__} cuda={torch.cuda.is_available()} gpus={torch.cuda.device_count()}")' 2>/dev/null || echo 'torch not importable')"
