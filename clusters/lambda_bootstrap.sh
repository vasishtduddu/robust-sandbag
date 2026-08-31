#!/usr/bin/env bash
# Run ONCE on a fresh Lambda instance to make it a possum executor.
#   scp clusters/lambda_bootstrap.sh ubuntu@<ip>:~ && ssh ubuntu@<ip> 'bash lambda_bootstrap.sh <git-url> <branch>'
set -euo pipefail
REPO_URL="${1:?usage: lambda_bootstrap.sh <git-url> [branch]}"
BRANCH="${2:-mark/baseline-exps}"
cd "$HOME"
[[ -d possum ]] || git clone --branch "$BRANCH" "$REPO_URL" possum
cd possum && git fetch --all -q && git checkout "$BRANCH" -q && git pull -q
python3 -m venv venv
source venv/bin/activate
pip install -q --upgrade pip wheel
# Lambda images ship a CUDA 12.x driver. pip's default torch wheel is now
# built against CUDA 13 and silently reports cuda_available=False on these
# boxes, so everything runs on CPU: valid results, ~5x slower, real money.
# Pin the cu12x index BEFORE the requirements install so torch resolves there.
# --force-reinstall is required: a plain install sees any torch as satisfying
# the requirement and will NOT downgrade off the cu130 wheel.
pip install -q --force-reinstall --no-cache-dir torch --index-url https://download.pytorch.org/whl/cu124
pip install -q "fsspec[http]<=2026.6.0"   # the torch downgrade drags fsspec past what datasets allows
pip install -q -e . && pip install -q -r requirements.txt hf_transfer
python -c "import torch,sys; ok=torch.cuda.is_available(); \
print(f'[bootstrap] torch {torch.__version__} cuda={ok}'); sys.exit(0 if ok else 1)" \
  || { echo '[bootstrap] FATAL: torch cannot see the GPU' >&2; exit 1; }
mkdir -p hf_cache datasets checkpoints results/json results/logs elicitation_results jobs
echo "[bootstrap] done. python: $(python -V), gpus: $(nvidia-smi -L | wc -l)"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
