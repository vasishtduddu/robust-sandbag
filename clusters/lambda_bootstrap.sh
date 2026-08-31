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
pip install -q -e . && pip install -q -r requirements.txt hf_transfer
mkdir -p hf_cache datasets checkpoints results/json results/logs elicitation_results jobs
echo "[bootstrap] done. python: $(python -V), gpus: $(nvidia-smi -L | wc -l)"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
