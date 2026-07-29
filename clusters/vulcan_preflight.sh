#!/usr/bin/env bash
# Read-only readiness check for the prior-work matrix on Alliance Vulcan.

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

: "${POSSUM_VULCAN_ROOT:?Set POSSUM_VULCAN_ROOT to the cloned repository.}"
: "${SCRATCH:?Vulcan did not define SCRATCH.}"
export POSSUM_VULCAN_ROOT="$(readlink -m "$POSSUM_VULCAN_ROOT")"
export POSSUM_VULCAN_VENV="$(readlink -m "${POSSUM_VULCAN_VENV:-$POSSUM_VULCAN_ROOT/venv}")"
export POSSUM_ARTIFACT_ROOT="$(readlink -m "${POSSUM_ARTIFACT_ROOT:-$SCRATCH/possum-prior-baselines}")"

if [[ "${CC_CLUSTER:-}" != "vulcan" ]]; then
    echo "Expected CC_CLUSTER=vulcan, got ${CC_CLUSTER:-unset}." >&2
    exit 2
fi
case "$POSSUM_VULCAN_ROOT/" in
    /project/*) ;;
    *) echo "POSSUM_VULCAN_ROOT must resolve below /project, not $POSSUM_VULCAN_ROOT." >&2; exit 2 ;;
esac
case "$POSSUM_ARTIFACT_ROOT/" in
    /scratch/*) ;;
    *) echo "POSSUM_ARTIFACT_ROOT must resolve below /scratch, not $POSSUM_ARTIFACT_ROOT." >&2; exit 2 ;;
esac
if [[ ! -x "$POSSUM_VULCAN_VENV/bin/python" ]]; then
    echo "Missing virtual environment at $POSSUM_VULCAN_VENV." >&2
    exit 2
fi

command -v sbatch >/dev/null
command -v sinfo >/dev/null
source "$POSSUM_VULCAN_ROOT/clusters/vulcan_env_setup.sh"

# The published Vulcan configuration uses L40S GPUs. Do not make scheduler
# display formatting a hard failure; the allocated-GPU check below is decisive.
if ! sinfo -h -o '%G' 2>/dev/null | grep -qi 'l40s'; then
    echo "WARN: live sinfo output did not advertise an l40s GRES; inspect 'sinfo -o %P\ %G\ %m\ %c\ %l' before submission." >&2
fi

export POSSUM_PREFLIGHT_ATTACKS="$require_attacks"
export POSSUM_PREFLIGHT_GPU="$require_gpu"

"$POSSUM_VULCAN_VENV/bin/python" - <<'PY'
import importlib
import os
from importlib.metadata import version
from pathlib import Path

from packaging.version import Version

hub_version = Version(version("huggingface-hub"))
if not (Version("0.34.0") <= hub_version < Version("1.0")):
    raise SystemExit(f"Incompatible huggingface-hub {hub_version}; rebuild the Vulcan environment.")

import pyarrow
import torch
from huggingface_hub import snapshot_download
from transformers import AutoConfig
from possum.utils.loading import MODEL_NAME_TO_HF_PATH

datasets_version = Version(version("datasets"))
pyarrow_version = Version(pyarrow.__version__)
if datasets_version.release != (3, 6, 0) or not (
    Version("19.0.0") <= pyarrow_version < Version("20.0.0")
):
    raise SystemExit(
        f"Incompatible Datasets/Arrow stack: datasets={datasets_version}, "
        f"pyarrow={pyarrow_version}; expected datasets=3.6.0 and PyArrow 19.x."
    )

root = Path(os.environ["POSSUM_VULCAN_ROOT"])
data = root / "datasets" / "mcqa"
for name in (
    "transformers", "peft", "trl", "datasets", "accelerate", "pandas", "sklearn",
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

for key in ("qwen2.5-7b-it", "qwen2.5-14b-it", "qwen2.5-32b-it"):
    path = snapshot_download(MODEL_NAME_TO_HF_PATH[key], local_files_only=True)
    print(f"cached model {key}: {path}")

if os.environ["POSSUM_PREFLIGHT_ATTACKS"] == "1":
    importlib.import_module("nanogcg")
    from fastchat.model import get_conversation_template
    assert get_conversation_template("vicuna_v1.1").name == "vicuna_v1.1"
    importlib.import_module("possum.elicitation.blackbox._tap_framework")
    importlib.import_module("possum.elicitation.blackbox._autodan_framework")
    attack_paths = {}
    for repo in (
        "meta-llama/Meta-Llama-3-8B-Instruct",
        "nomic-ai/nomic-embed-text-v1.5",
        "lmsys/vicuna-13b-v1.5",
    ):
        path = snapshot_download(repo, local_files_only=True)
        attack_paths[repo] = path
        print(f"cached attack dependency: {path}")
    code_path = snapshot_download(
        "nomic-ai/nomic-bert-2048",
        allow_patterns=["*.py"],
        local_files_only=True,
    )
    print(f"cached attack dependency code: {code_path}")
    # Resolve the embedding model's cross-repository auto_map without loading
    # weights, catching missing remote code before an offline GPU job starts.
    AutoConfig.from_pretrained(
        attack_paths["nomic-ai/nomic-embed-text-v1.5"],
        trust_remote_code=True,
        local_files_only=True,
    )

print(
    f"torch={torch.__version__} cuda_build={torch.version.cuda} "
    f"huggingface_hub={hub_version} datasets={datasets_version} pyarrow={pyarrow_version}"
)
if os.environ["POSSUM_PREFLIGHT_GPU"] == "1":
    if not torch.cuda.is_available():
        raise SystemExit("No CUDA GPU is visible. Run --require-gpu inside a Vulcan allocation.")
    props = torch.cuda.get_device_properties(0)
    gib = props.total_memory / 1024**3
    if gib < 40:
        raise SystemExit(f"GPU 0 has only {gib:.1f} GiB; need a full 48 GB-class GPU.")
    if "l40s" not in props.name.lower():
        raise SystemExit(f"Expected a Vulcan L40S, got {props.name!r}.")
    x = torch.ones(1, device="cuda", dtype=torch.bfloat16)
    assert x.item() == 1
    print(f"gpu={props.name} vram={gib:.1f} GiB bf16=OK")

print("Vulcan prior-baseline preflight: OK")
PY

df -h "$POSSUM_ARTIFACT_ROOT" | tail -n 1
