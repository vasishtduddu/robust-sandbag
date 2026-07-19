#!/usr/bin/env bash
# Build a reproducible Killarney venv and stage prior-baseline inputs. Run this
# on the login node from the cloned repository under /project:
#   POSSUM_KILLARNEY_ROOT=$PWD bash clusters/killarney_envbuild.sh
# With no model arguments, all Table-2 tiers and the PWD weak model are staged.

set -euo pipefail
ROOT="${POSSUM_KILLARNEY_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
VENV="${POSSUM_KILLARNEY_VENV:-$ROOT/venv}"
export POSSUM_ARTIFACT_ROOT="${POSSUM_ARTIFACT_ROOT:-$ROOT/artifacts}"
export HF_HOME="${HF_HOME:-$ROOT/hf_cache}"
export HF_HUB_CACHE="$HF_HOME/hub"
export HF_DATASETS_CACHE="$HF_HOME/datasets"
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-$ROOT/pip_cache}"

# Select Python before Arrow so Lmod exposes the matching PyArrow extension.
# Datasets 3.6 supports the cluster-provided Arrow 19 module; Datasets 4.3
# requires PyArrow 21 and makes Alliance's protective dummy wheel abort.
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
# Use the Alliance wheelhouse for the CUDA/PyTorch stack.  Installing the loose
# requirements.txt floors from PyPI can silently select an incompatible CPU or
# newer-CUDA torch wheel on a login node.
python -m pip install --no-index --upgrade pip 2>/dev/null || true
python -m pip install --no-index \
  "torch==2.9.1" "transformers==4.57.3" "peft==0.18.1" "trl==0.24.0" \
  "datasets==3.6.0" "accelerate==1.12.0" "bitsandbytes==0.49.2" \
  "huggingface-hub==0.36.2" \
  numpy pandas scikit-learn einops sentencepiece tqdm rouge_score

# Killarney's wheelhouse version of wandb is known to fail on Alliance hosts.
# The cluster guide requires 0.18.0, so deliberately obtain that exact version
# from PyPI rather than allowing the local wheelhouse to select 0.19.x.
PIP_CONFIG_FILE=/dev/null python -m pip install --prefer-binary "wandb==0.18.0"

# These small attack dependencies are not consistently mirrored in the
# wheelhouse. They are unnecessary for effectiveness-only work. When the
# attacker cache is requested, however, installation is required so a later
# robustness job cannot fail only after receiving expensive GPUs.
if [[ "${POSSUM_STAGE_JAILBREAK_ATTACKERS:-0}" == "1" ]]; then
  # Preserve the core Transformers/Hub pins. nanogcg's declared upper bound
  # would otherwise downgrade Transformers. fschat is needed only for TAP's
  # conversation templates, not its server/API dependency set. AutoDAN has an
  # exact NumPy retrieval path, so no compiled Faiss package is required.
  PIP_CONFIG_FILE=/dev/null python -m pip install --only-binary=:all: --no-deps \
    "nanogcg==0.3.0" "fschat==0.2.36"
fi
python -m pip install --no-build-isolation -e "$ROOT"

if [[ "$#" -eq 0 ]]; then
  set -- qwen2.5-7b-it qwen2.5-14b-it qwen2.5-32b-it
fi

# The small weak model is always required by the PWD baseline.  Append it to
# an explicitly supplied tier list and deduplicate in Python below.
python - "$@" qwen2.5-1.5b-it <<'PY'
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

# Evaluation and training otherwise try to populate datasets/ from an offline
# compute node. Materialise every Table-2 target and utility dataset now.
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

# AutoDAN and TAP load these attacker-side models in addition to the target.
# They are intentionally opt-in: Meta Llama requires the caller's approved HF
# token, and they consume substantial shared project storage.
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

export POSSUM_KILLARNEY_ROOT="$ROOT"
export POSSUM_KILLARNEY_VENV="$VENV"
preflight_args=()
if [[ "${POSSUM_STAGE_JAILBREAK_ATTACKERS:-0}" == "1" ]]; then
  preflight_args+=(--attacks)
fi
"$ROOT/clusters/killarney_preflight.sh" "${preflight_args[@]}"
