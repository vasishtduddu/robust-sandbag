#!/usr/bin/env bash
#SBATCH --job-name=cc_loadtest
#SBATCH --gres=gpu:h100:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=20
#SBATCH --account=def-asokan_gpu
#SBATCH --output=/scratch/tonyhe/possum/cc_loadtest.log
# Fail-fast validation of the CC env on an air-gapped H100 compute node: GPU visible,
# arrow pyarrow + datasets/trl import, possum import, and the offline model load
# (transformers _resolve_local_snapshot fix + HF_HUB_OFFLINE) all work end to end.
set -eo pipefail
echo "node $(hostname)"; nvidia-smi -L 2>&1 | head -2
source /scratch/tonyhe/possum/job_env_setup.sh
cd /scratch/tonyhe/possum
python - <<'PY'
import torch
print("torch", torch.__version__, "cuda_avail", torch.cuda.is_available())
print("device", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "NONE")
import pyarrow, datasets, transformers, trl, peft
print("pyarrow", pyarrow.__version__, "datasets", datasets.__version__,
      "tf", transformers.__version__, "trl", trl.__version__, "peft", peft.__version__)
from possum.utils.loading import load_model
m, tok = load_model("qwen2.5-7b-it", load_from="hf", device="cuda")
print("model loaded:", type(m).__name__, "param device:", next(m.parameters()).device)
ids = tok("The capital of France is", return_tensors="pt").to("cuda")
out = m.generate(**ids, max_new_tokens=3)
print("GEN OK:", repr(tok.decode(out[0][-3:])))
print("CC_LOADTEST_OK")
PY
