#!/usr/bin/env bash
# Build a Compute Canada (Alliance) venv for possum using the OFFICIAL wheelhouse recipe,
# and stage Qwen2.5-7B to a scratch HF cache. Run on a fir/rorqual LOGIN node:
#   nohup bash cc_envbuild.sh > cc_envbuild.log 2>&1 &
# Key insight: load gcc + arrow modules (NEVER piped) so pyarrow (a datasets/trl
# dependency, shipped only as a noinstall stub in the wheelhouse) is provided by the
# module on PYTHONPATH; the wheelhouse then installs everything else with --no-index.
set -eo pipefail
echo "CC ENV BUILD start $(date) host $(hostname)"

ROOT=/scratch/tonyhe/possum
VENV=$ROOT/venv
export HF_HOME=/scratch/tonyhe/hf_cache

# NB: never pipe `module load` (the module function evals in the current shell; a pipe
# runs it in a subshell and the env changes, incl. arrow's pyarrow on PYTHONPATH, are lost).
module load StdEnv/2023 gcc arrow python/3.12.4
echo "python: $(python --version 2>&1) at $(which python)"
echo "arrow:  $EBVERSIONARROW (provides pyarrow)"

cd "$ROOT"
rm -rf "$VENV"
virtualenv --no-download "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install --no-index --upgrade pip 2>/dev/null || true

# Core stack from the CC wheelhouse (native +computecanada builds, H100-tuned). datasets
# installs cleanly because pyarrow is satisfied by the arrow module. bnb 0.49.1 is absent
# in the wheelhouse -> 0.49.2. numerics libs unpinned (wheelhouse constraints pick matches).
echo "=== core stack (CC wheelhouse, --no-index) ==="
python -m pip install --no-index \
  "torch==2.9.1" "transformers==4.57.3" "peft==0.18.1" "trl==0.24.0" "datasets==4.3.0" \
  "accelerate==1.12.0" "bitsandbytes==0.49.2" \
  numpy pandas scikit-learn wandb einops sentencepiece tqdm rouge_score

# Attack-only packages absent from the wheelhouse: best-effort from PyPI (login node has
# internet). --no-deps since heavy deps are satisfied; do not fail the build if these
# error (the eff phase does not need them; blackbox/whitebox do).
echo "=== niche packages (PyPI, best-effort) ==="
PIP_CONFIG_FILE=/dev/null python -m pip install --no-deps nanogcg steering-vectors faiss-cpu fschat \
  || echo "WARN: some niche packages failed to install (eff phase unaffected)"

# Verify. PYTHONPATH PREPENDS possum and KEEPS the arrow pyarrow path (clobbering it would
# break `import pyarrow`/`datasets`). torch.cuda is False on the login node (expected).
echo "=== verify core imports ==="
PYTHONPATH=/scratch/tonyhe:$PYTHONPATH python -c "import torch,transformers,peft,trl,accelerate,bitsandbytes,datasets,pyarrow,numpy as np; print('torch',torch.__version__,'tf',transformers.__version__,'trl',trl.__version__,'datasets',datasets.__version__,'pyarrow',pyarrow.__version__,'np',np.__version__); import possum; from possum.evaluate import evaluate_checkpoint; from possum.utils.training import get_abcd_bias; print('possum import OK')"
echo "=== verify niche imports (warn only) ==="
PYTHONPATH=/scratch/tonyhe:$PYTHONPATH python -c "
for m in ['nanogcg','steering_vectors','faiss','fastchat']:
    try: __import__(m); print('  ok', m)
    except Exception as e: print('  WARN', m, '->', repr(e))
"

echo "=== stage Qwen2.5-7B-Instruct to $HF_HOME ==="
python -c "from huggingface_hub import snapshot_download; p=snapshot_download('Qwen/Qwen2.5-7B-Instruct'); print('staged', p)"

echo "CC ENV BUILD done $(date)"
