#!/usr/bin/env bash
#SBATCH --job-name=possum_envbuild
#SBATCH --account=school_group
#SBATCH --partition=ALL
#SBATCH --gpus=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=02:00:00
#SBATCH --output=/u401/l79he/possum/envbuild.log
# Build a py3.12 conda env at a FIXED path on exec /tmp, conda-pack it to /u401,
# and pre-stage the 7B model to a persistent HF cache. Runs on an H200 node.
set -e
echo "host $(hostname) start $(date)"
export PIP_CACHE_DIR=/u401/l79he/pip_cache   # persist wheel cache across rebuilds
ENVP=/tmp/possum/env/py312      # FIXED path: build == extract path (relocation-safe)
MC=/tmp/possum/mc
rm -rf /tmp/possum && mkdir -p /tmp/possum   # clean slate (force fresh Miniforge)

if [ ! -x "$MC/bin/conda" ]; then
  curl -sSL https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh -o /tmp/possum/mc.sh
  bash /tmp/possum/mc.sh -b -p "$MC"
fi
rm -rf "$ENVP"   # clean rebuild (idempotent across re-runs on the same node)
"$MC/bin/conda" create -y -p "$ENVP" -c conda-forge --override-channels python=3.12 pip setuptools wheel
PY="$ENVP/bin/python"
"$PY" -m pip install --upgrade pip wheel setuptools
echo "=== torch cu128 ==="
"$PY" -m pip install torch==2.9.1 --index-url https://download.pytorch.org/whl/cu128
echo "=== core stack ==="
"$PY" -m pip install \
  "transformers==4.57.3" "peft==0.18.1" "trl==0.24.0" "datasets==4.3.0" \
  "accelerate==1.12.0" "bitsandbytes==0.49.1" "numpy==2.2.0" "pandas==2.3.3" \
  "scikit-learn==1.9.0" wandb einops sentencepiece tqdm \
  steering-vectors nanogcg rouge_score
"$PY" -m pip install fschat --no-deps
"$PY" -m pip install conda-pack
# NOTE: possum is NOT installed into the env (conda-pack rejects editable installs).
# At job runtime, job_env_setup.sh sets PYTHONPATH=/u401/l79he so `import possum`
# resolves to the live pure-python source (no .so; readable on noexec /u401).

echo "=== verify torch + GPU + possum (via PYTHONPATH) ==="
PYTHONPATH=/u401/l79he "$PY" -c "import torch; print('torch', torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0)); import possum; from possum.evaluate import evaluate_checkpoint; from possum.utils.training import get_abcd_bias; print('possum import OK')"

echo "=== conda-pack -> /u401 ==="
"$ENVP/bin/conda-pack" -p "$ENVP" -o /u401/l79he/possum_env.tar.gz --force
echo "PACK_SIZE: $(du -h /u401/l79he/possum_env.tar.gz)"

echo "=== stage Qwen2.5-7B-Instruct to persistent HF cache ==="
export HF_HOME=/u401/l79he/hf_cache
"$PY" -c "from huggingface_hub import snapshot_download; p=snapshot_download('Qwen/Qwen2.5-7B-Instruct'); print('staged', p)"
echo "ENVBUILD_DONE $(date)"
