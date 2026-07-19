#!/usr/bin/env bash
# Read-only readiness check for the prior-work matrix on Killarney.

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

: "${POSSUM_KILLARNEY_ROOT:?Set POSSUM_KILLARNEY_ROOT to the cloned repository.}"
export POSSUM_KILLARNEY_VENV="${POSSUM_KILLARNEY_VENV:-$POSSUM_KILLARNEY_ROOT/venv}"
export POSSUM_ARTIFACT_ROOT="${POSSUM_ARTIFACT_ROOT:-$POSSUM_KILLARNEY_ROOT/artifacts}"

if [[ "${CC_CLUSTER:-}" != "killarney" ]]; then
    echo "Expected CC_CLUSTER=killarney, got ${CC_CLUSTER:-unset}." >&2
    exit 2
fi

root_real="$(readlink -m "$POSSUM_KILLARNEY_ROOT")"
artifact_real="$(readlink -m "$POSSUM_ARTIFACT_ROOT")"
case "$root_real/" in
    /project/*) ;;
    *) echo "POSSUM_KILLARNEY_ROOT must resolve below /project, not $root_real." >&2; exit 2 ;;
esac
case "$artifact_real/" in
    /project/*) ;;
    *) echo "POSSUM_ARTIFACT_ROOT must resolve below /project, not $artifact_real." >&2; exit 2 ;;
esac

if [[ ! -x "$POSSUM_KILLARNEY_VENV/bin/python" ]]; then
    echo "Missing virtual environment at $POSSUM_KILLARNEY_VENV." >&2
    exit 2
fi

command -v sbatch >/dev/null
command -v sinfo >/dev/null
source "$POSSUM_KILLARNEY_ROOT/clusters/killarney_env_setup.sh"

export POSSUM_PREFLIGHT_ATTACKS="$require_attacks"
export POSSUM_PREFLIGHT_GPU="$require_gpu"

"$POSSUM_KILLARNEY_VENV/bin/python" - <<'PY'
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
        "Rebuild with clusters/killarney_envbuild.sh."
    )

import torch
from huggingface_hub import snapshot_download

from possum.utils.loading import MODEL_NAME_TO_HF_PATH

root = Path(os.environ["POSSUM_KILLARNEY_ROOT"])
data = root / "datasets" / "mcqa"

for name in (
    "transformers", "peft", "trl", "datasets", "accelerate",
    "pandas", "sklearn",
):
    importlib.import_module(name)

expected_csvs = []
for prefix in (
    "wmdp_bio", "wmdp_chem", "wmdp_cyber",
    "mmlu_prof", "mmlu_stem", "mmlu_hss",
):
    expected_csvs.extend(
        data / f"{prefix}_{split}.csv"
        for split in ("mos_train", "mos_eval", "elicit_train", "elicit_eval", "test")
    )
expected_csvs.extend(data / name for name in (
    "hellaswag.csv", "hellaswag_train.csv",
    "commonsenseqa.csv", "commonsenseqa_train.csv",
))
missing_csvs = [str(path) for path in expected_csvs if not path.is_file()]
if missing_csvs:
    raise SystemExit("Missing processed inputs:\n  " + "\n  ".join(missing_csvs))

for key in (
    "qwen2.5-1.5b-it", "qwen2.5-7b-it", "qwen2.5-14b-it", "qwen2.5-32b-it",
):
    repo = MODEL_NAME_TO_HF_PATH[key]
    path = snapshot_download(repo, local_files_only=True)
    print(f"cached model {key}: {path}")

if os.environ["POSSUM_PREFLIGHT_ATTACKS"] == "1":
    importlib.import_module("nanogcg")
    from fastchat.model import get_conversation_template
    assert get_conversation_template("vicuna_v1.1").name == "vicuna_v1.1"
    # Validate the actual local TAP and AutoDAN modules as well as their leaf
    # packages. This catches missing rouge_score or a framework-level import
    # regression before an H100 allocation starts.
    importlib.import_module("possum.elicitation.blackbox._tap_framework")
    importlib.import_module("possum.elicitation.blackbox._autodan_framework")
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
    if props.total_memory < 40 * 1024**3:
        raise SystemExit(f"GPU 0 has only {props.total_memory / 1024**3:.1f} GiB; need 40+ GiB.")
    x = torch.ones(1, device="cuda", dtype=torch.bfloat16)
    assert x.item() == 1
    print(f"gpu={props.name} vram={props.total_memory / 1024**3:.1f} GiB bf16=OK")

print("Killarney prior-baseline preflight: OK")
PY

df -h "$POSSUM_ARTIFACT_ROOT" | tail -n 1
