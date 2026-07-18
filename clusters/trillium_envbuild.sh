#!/usr/bin/env bash
# Build the Trillium environment and stage offline model/data inputs. Run once
# on the GPU login node from the project clone. Model caches go to SCRATCH;
# processed CSV inputs remain in the project clone and are read-only in jobs.

set -euo pipefail
ROOT="${POSSUM_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
VENV="${POSSUM_VENV:-$ROOT/venv}"
: "${SCRATCH:?SCRATCH is not set; run this on Trillium.}"
export POSSUM_ARTIFACT_ROOT="${POSSUM_ARTIFACT_ROOT:-$SCRATCH/possum-baselines}"
export HF_HOME="$POSSUM_ARTIFACT_ROOT/cache/huggingface"
export HF_HUB_CACHE="$HF_HOME/hub"
export HF_DATASETS_CACHE="$HF_HOME/datasets"

# Alliance supplies PyArrow through the Arrow module rather than a pip wheel.
# It must be loaded before the virtual environment is activated/pip is run.
module load StdEnv/2023 gcc arrow cuda/12.6 python/3.11.5
mkdir -p "$POSSUM_ARTIFACT_ROOT" "$HF_HUB_CACHE" "$HF_DATASETS_CACHE"
virtualenv --no-download "$VENV"
source "$VENV/bin/activate"

python -m pip install --no-index --upgrade pip 2>/dev/null || true
python -m pip install --no-index \
  "torch==2.9.1" "transformers==4.57.3" "peft==0.18.1" "trl==0.24.0" \
  "datasets==4.3.0" "accelerate==1.12.0" "bitsandbytes==0.49.2" \
  numpy pandas scikit-learn einops sentencepiece tqdm rouge_score
PIP_CONFIG_FILE=/dev/null python -m pip install --prefer-binary "wandb==0.18.0"

if [[ "${POSSUM_STAGE_JAILBREAK_ATTACKERS:-0}" == "1" ]]; then
  PIP_CONFIG_FILE=/dev/null python -m pip install --prefer-binary \
    nanogcg steering-vectors faiss-cpu fschat
fi
python -m pip install --no-build-isolation -e "$ROOT"

if [[ "$#" -eq 0 ]]; then
  set -- qwen2.5-7b-it qwen2.5-32b-it
fi

# Password locking needs the 1.5B weak model in addition to the requested tier.
python - "$@" qwen2.5-1.5b-it <<'PY'
import sys
from huggingface_hub import snapshot_download
from possum.utils.loading import MODEL_NAME_TO_HF_PATH

for key in dict.fromkeys(sys.argv[1:]):
    try:
        repo = MODEL_NAME_TO_HF_PATH[key]
    except KeyError as exc:
        raise SystemExit(
            f"Unknown model key {key!r}; choose from {sorted(MODEL_NAME_TO_HF_PATH)}"
        ) from exc
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

for dataset in (
    WMDPDataset(), MMLUDataset(), MMLUProfDataset(), MMLUStemDataset(),
    MMLUHssDataset(), HellaSwagDataset(), CommonsenseQADataset(),
):
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
  python -c "import nanogcg, faiss, fastchat, steering_vectors; print('jailbreak package imports: OK')"
fi
