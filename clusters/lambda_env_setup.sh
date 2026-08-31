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

# HARD GATE. A CUDA/driver mismatch makes torch fall back to CPU SILENTLY:
# the job still produces correct numbers, just ~5x slower, while an hourly
# instance bills the whole time. Verified 2026-08-31 (torch cu130 against a
# 12.8 driver). Refuse to run rather than burn credits on CPU.
python - <<'PYGATE' || { echo "[lambda] FATAL: no GPU visible to torch -- refusing to run" >&2; return 3 2>/dev/null || exit 3; }
import sys, torch
ok = torch.cuda.is_available()
print(f"[lambda] torch {torch.__version__} cuda={ok} gpus={torch.cuda.device_count()}")
sys.exit(0 if ok else 1)
PYGATE
