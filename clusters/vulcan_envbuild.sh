#!/usr/bin/env bash
# Build the prior-baseline environment and stage every offline input on Vulcan.
# Run from the login node after cloning the repository in project storage:
#   POSSUM_VULCAN_ROOT=$(pwd -P) bash clusters/vulcan_envbuild.sh

set -euo pipefail
: "${SCRATCH:?Vulcan did not define SCRATCH.}"
ROOT="$(readlink -m "${POSSUM_VULCAN_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)}")"
VENV="$(readlink -m "${POSSUM_VULCAN_VENV:-$ROOT/venv}")"
export POSSUM_VULCAN_ROOT="$ROOT"
export POSSUM_VULCAN_VENV="$VENV"
export POSSUM_ARTIFACT_ROOT="$(readlink -m "${POSSUM_ARTIFACT_ROOT:-$SCRATCH/possum-prior-baselines}")"
export HF_HOME="$(readlink -m "${HF_HOME:-$SCRATCH/possum-hf-cache}")"
export HF_HUB_CACHE="$HF_HOME/hub"
export HF_DATASETS_CACHE="$HF_HOME/datasets"
export PIP_CACHE_DIR="$(readlink -m "${PIP_CACHE_DIR:-$SCRATCH/possum-pip-cache}")"

# Match the tested Alliance stack used by the Killarney path. Login-node
# preflight below will fail early if Vulcan's live module inventory differs.
module load StdEnv/2023 gcc python/3.12 arrow/19.0.1 cuda/12.2
mkdir -p "$POSSUM_ARTIFACT_ROOT" "$HF_HUB_CACHE" "$HF_DATASETS_CACHE" "$PIP_CACHE_DIR"
virtualenv --no-download "$VENV"
source "$VENV/bin/activate"
python - <<'PY'
import pyarrow

if tuple(map(int, pyarrow.__version__.split(".")[:2])) != (19, 0):
    raise SystemExit(
        f"Expected PyArrow 19.x from arrow/19.0.1, got {pyarrow.__version__}. "
        "Ensure Python is loaded before the Arrow module."
    )
print(f"cluster PyArrow module: {pyarrow.__version__}")
PY

# Use the Alliance wheelhouse for the native CUDA/PyTorch stack. Compute nodes
# are treated as offline, so every dependency and model is staged here.
python -m pip install --no-index --upgrade pip 2>/dev/null || true
python -m pip install --no-index \
  "torch==2.9.1" "transformers==4.57.3" "peft==0.18.1" "trl==0.24.0" \
  "datasets==3.6.0" "accelerate==1.12.0" "bitsandbytes==0.49.2" \
  "huggingface-hub==0.36.2" \
  numpy pandas scikit-learn einops sentencepiece tqdm rouge_score

# This pin is the tested project version; fetch it on the networked login node.
PIP_CONFIG_FILE=/dev/null python -m pip install --prefer-binary "wandb==0.18.0"

if [[ "${POSSUM_STAGE_JAILBREAK_ATTACKERS:-0}" == "1" ]]; then
  # Preserve the core Transformer/Hub pins. AutoDAN uses the exact NumPy
  # retrieval implementation, so a compiled Faiss package is unnecessary.
  PIP_CONFIG_FILE=/dev/null python -m pip install --only-binary=:all: --no-deps \
    "nanogcg==0.3.0" "fschat==0.2.36"
fi
python -m pip install --no-build-isolation -e "$ROOT"

if [[ "$#" -eq 0 ]]; then
  set -- qwen2.5-7b-it qwen2.5-14b-it qwen2.5-32b-it
fi

python - "$@" <<'PY'
import sys
from huggingface_hub import snapshot_download
from possum.utils.loading import MODEL_NAME_TO_HF_PATH

for key in dict.fromkeys(sys.argv[1:]):
    try:
        repo = MODEL_NAME_TO_HF_PATH[key]
    except KeyError as exc:
        raise SystemExit(f"Unknown model key {key!r}; choose from {sorted(MODEL_NAME_TO_HF_PATH)}") from exc
    print(f"Staging {key}: {repo}", flush=True)
    print(snapshot_download(repo), flush=True)
PY

python - <<'PY'
from possum.data import (
    CommonsenseQADataset,
    HellaSwagDataset,
    MMLUDataset,
    MMLUHssDataset,
    MMLUProfDataset,
    MMLUStemDataset,
    WMDPDataset,
)

datasets = (
    WMDPDataset(), MMLUDataset(), MMLUProfDataset(), MMLUStemDataset(),
    MMLUHssDataset(), HellaSwagDataset(), CommonsenseQADataset(),
)
for dataset in datasets:
    print(f"Staging dataset: {type(dataset).__name__}", flush=True)
    dataset.load()
    dataset.save()
PY

if [[ "${POSSUM_STAGE_JAILBREAK_ATTACKERS:-0}" == "1" ]]; then
  python - <<'PY'
from huggingface_hub import snapshot_download

for repo in (
    "meta-llama/Meta-Llama-3-8B-Instruct",
    "nomic-ai/nomic-embed-text-v1.5",
    "lmsys/vicuna-13b-v1.5",
):
    print(f"Staging jailbreak dependency: {repo}", flush=True)
    print(snapshot_download(repo), flush=True)
PY
fi

python -c "import torch, transformers, peft, trl, datasets; print('torch', torch.__version__, 'cuda', torch.version.cuda, 'transformers', transformers.__version__, 'peft', peft.__version__, 'trl', trl.__version__, 'datasets', datasets.__version__)"
if [[ "${POSSUM_STAGE_JAILBREAK_ATTACKERS:-0}" == "1" ]]; then
  python - <<'PY'
import nanogcg
from fastchat.model import get_conversation_template
import possum.elicitation.blackbox._autodan_framework
import possum.elicitation.blackbox._tap_framework

assert get_conversation_template("vicuna_v1.1").name == "vicuna_v1.1"
print("jailbreak package imports: OK")
PY
fi

preflight_args=()
if [[ "${POSSUM_STAGE_JAILBREAK_ATTACKERS:-0}" == "1" ]]; then
  preflight_args+=(--attacks)
fi
"$ROOT/clusters/vulcan_preflight.sh" "${preflight_args[@]}"
