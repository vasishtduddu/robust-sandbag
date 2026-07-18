#!/usr/bin/env bash
# Read-only readiness check for the prior-work matrix on Trillium.

set -euo pipefail

require_attacks=0
require_gpu=0
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --attacks) require_attacks=1 ;;
        --require-gpu) require_gpu=1 ;;
        *) echo "Unknown preflight argument: $1" >&2; exit 2 ;;
    esac
    shift
done

: "${SCRATCH:?SCRATCH is not set; run this on Trillium.}"
: "${POSSUM_REPO_ROOT:?Set POSSUM_REPO_ROOT to the cloned Possum repository.}"
: "${POSSUM_ARTIFACT_ROOT:?Set POSSUM_ARTIFACT_ROOT below SCRATCH.}"

if [[ "${CC_CLUSTER:-}" != "trillium" ]]; then
    echo "Expected CC_CLUSTER=trillium, got ${CC_CLUSTER:-unset}." >&2
    exit 2
fi

scratch_real="$(readlink -m "$SCRATCH")"
artifact_real="$(readlink -m "$POSSUM_ARTIFACT_ROOT")"
case "$artifact_real/" in
    "$scratch_real"/*) ;;
    *) echo "POSSUM_ARTIFACT_ROOT must be below SCRATCH." >&2; exit 2 ;;
esac

if [[ -z "${POSSUM_VENV:-}" ]]; then
    if [[ -x "$POSSUM_REPO_ROOT/venv/bin/python" ]]; then
        export POSSUM_VENV="$POSSUM_REPO_ROOT/venv"
    elif [[ -x "$POSSUM_REPO_ROOT/.venv/bin/python" ]]; then
        export POSSUM_VENV="$POSSUM_REPO_ROOT/.venv"
    else
        export POSSUM_VENV="$POSSUM_REPO_ROOT/venv"
    fi
fi
if [[ ! -x "$POSSUM_VENV/bin/python" ]]; then
    echo "Missing virtual environment at $POSSUM_VENV." >&2
    exit 2
fi

command -v sbatch >/dev/null
command -v sinfo >/dev/null
source "$POSSUM_REPO_ROOT/clusters/trillium_env_setup.sh"

export POSSUM_PREFLIGHT_ATTACKS="$require_attacks"
export POSSUM_PREFLIGHT_GPU="$require_gpu"

"$POSSUM_VENV/bin/python" - <<'PY'
import importlib
import os
from importlib.metadata import version
from pathlib import Path

from packaging.version import Version

hub_version = Version(version("huggingface-hub"))
if not (Version("0.34.0") <= hub_version < Version("1.0")):
    raise SystemExit(
        "Incompatible huggingface-hub "
        f"{hub_version}; Transformers 4.57.3 requires >=0.34.0,<1.0. "
        "Rebuild the environment with clusters/trillium_envbuild.sh."
    )

import torch
from huggingface_hub import snapshot_download

from possum.utils.loading import MODEL_NAME_TO_HF_PATH

root = Path(os.environ["POSSUM_REPO_ROOT"])
data = root / "datasets" / "mcqa"

core_modules = (
    "transformers", "peft", "trl", "datasets", "accelerate",
    "pandas", "sklearn",
)
for name in core_modules:
    importlib.import_module(name)

expected_csvs = []
for prefix in ("wmdp_bio", "wmdp_chem", "wmdp_cyber",
               "mmlu_prof", "mmlu_stem", "mmlu_hss"):
    expected_csvs.extend(data / f"{prefix}_{split}.csv" for split in (
        "mos_train", "mos_eval", "elicit_train", "elicit_eval", "test"
    ))
expected_csvs.extend(data / name for name in (
    "hellaswag.csv", "hellaswag_train.csv",
    "commonsenseqa.csv", "commonsenseqa_train.csv",
))
missing_csvs = [str(path) for path in expected_csvs if not path.is_file()]
if missing_csvs:
    raise SystemExit("Missing processed inputs:\n  " + "\n  ".join(missing_csvs))

model_keys = (
    "qwen2.5-1.5b-it", "qwen2.5-7b-it", "qwen2.5-14b-it", "qwen2.5-32b-it"
)
for key in model_keys:
    repo = MODEL_NAME_TO_HF_PATH[key]
    path = snapshot_download(repo, local_files_only=True)
    print(f"cached model {key}: {path}")

if os.environ["POSSUM_PREFLIGHT_ATTACKS"] == "1":
    for name in ("nanogcg", "steering_vectors"):
        importlib.import_module(name)
    from fastchat.model import get_conversation_template
    assert get_conversation_template("vicuna_v1.1").name == "vicuna_v1.1"
    for repo in (
        "meta-llama/Meta-Llama-3-8B-Instruct",
        "nomic-ai/nomic-embed-text-v1.5",
        "lmsys/vicuna-13b-v1.5",
    ):
        path = snapshot_download(repo, local_files_only=True)
        print(f"cached attack dependency: {path}")

print(
    f"torch={torch.__version__} cuda_build={torch.version.cuda} "
    f"huggingface_hub={hub_version}"
)
if os.environ["POSSUM_PREFLIGHT_GPU"] == "1":
    if not torch.cuda.is_available():
        raise SystemExit("No CUDA GPU is visible. Run --require-gpu inside an allocation.")
    props = torch.cuda.get_device_properties(0)
    if props.total_memory < 70 * 1024**3:
        raise SystemExit(f"GPU 0 has only {props.total_memory / 1024**3:.1f} GiB; need 70+ GiB.")
    x = torch.ones(1, device="cuda", dtype=torch.bfloat16)
    assert x.item() == 1
    print(f"gpu={props.name} vram={props.total_memory / 1024**3:.1f} GiB bf16=OK")

print("Trillium prior-baseline preflight: OK")
PY

df -h "$POSSUM_ARTIFACT_ROOT" | tail -n 1
