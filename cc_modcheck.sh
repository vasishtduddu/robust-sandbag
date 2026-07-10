#!/usr/bin/env bash
# Validate the runtime module set (no Lmod conflicts) and that all packages import,
# including faiss (from the faiss module) and the niche attack packages. torch.cuda is
# False on the login node (no GPU) - the loadtest sbatch validates GPU separately.
source /scratch/tonyhe/possum/job_env_setup.sh
python - <<'PY'
import torch, datasets, transformers, trl, peft, nanogcg, steering_vectors, fastchat
print("torch.version.cuda", torch.version.cuda, "| pyarrow via arrow module:", __import__("pyarrow").__version__)
import possum
from possum.evaluate import evaluate_checkpoint
print("ALL IMPORTS OK")
PY
echo MODCHECK_DONE
