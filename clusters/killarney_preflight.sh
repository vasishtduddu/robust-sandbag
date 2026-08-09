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
export POSSUM_KILLARNEY_ROOT="$(readlink -m "$POSSUM_KILLARNEY_ROOT")"
export POSSUM_KILLARNEY_VENV="$(readlink -m "${POSSUM_KILLARNEY_VENV:-$POSSUM_KILLARNEY_ROOT/venv}")"
export POSSUM_ARTIFACT_ROOT="$(readlink -m "${POSSUM_ARTIFACT_ROOT:-$POSSUM_KILLARNEY_ROOT/artifacts}")"

if [[ "${CC_CLUSTER:-}" != "killarney" ]]; then
    echo "Expected CC_CLUSTER=killarney, got ${CC_CLUSTER:-unset}." >&2
    exit 2
fi

case "$POSSUM_KILLARNEY_ROOT/" in
    /project/*) ;;
    *) echo "POSSUM_KILLARNEY_ROOT must resolve below /project, not $POSSUM_KILLARNEY_ROOT." >&2; exit 2 ;;
esac
case "$POSSUM_ARTIFACT_ROOT/" in
    /project/*|"/scratch/${USER}/"*) ;;
    *)
        echo "POSSUM_ARTIFACT_ROOT must resolve below /project or /scratch/${USER}, not $POSSUM_ARTIFACT_ROOT." >&2
        exit 2
        ;;
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
import pyarrow
from huggingface_hub import snapshot_download
from transformers import AutoConfig

from possum.utils.loading import MODEL_NAME_TO_HF_PATH

datasets_version = Version(version("datasets"))
pyarrow_version = Version(pyarrow.__version__)
if datasets_version.release != (3, 6, 0) or not (
    Version("19.0.0") <= pyarrow_version < Version("20.0.0")
):
    raise SystemExit(
        "Incompatible Datasets/Arrow stack: "
        f"datasets={datasets_version}, pyarrow={pyarrow_version}; expected "
        "datasets=3.6.0 with the Killarney arrow/19.0.1 module. "
        "Rebuild with clusters/killarney_envbuild.sh."
    )

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
    "qwen2.5-7b-it", "qwen2.5-14b-it", "qwen2.5-32b-it",
):
    repo = MODEL_NAME_TO_HF_PATH[key]
    path = snapshot_download(repo, local_files_only=True)
    print(f"cached model {key}: {path}")

if os.environ["POSSUM_PREFLIGHT_ATTACKS"] == "1":
    attack_set = {
        value.strip()
        for value in os.environ.get(
            "POSSUM_PREFLIGHT_ATTACK_SET", "gcg,autodan,tap"
        ).split(",")
        if value.strip()
    }
    supported_attacks = {"gcg", "autodan", "tap"}
    unknown_attacks = attack_set - supported_attacks
    if not attack_set or unknown_attacks:
        raise SystemExit(
            "Invalid POSSUM_PREFLIGHT_ATTACK_SET="
            f"{os.environ.get('POSSUM_PREFLIGHT_ATTACK_SET', '')!r}; "
            "expected one or more of gcg,autodan,tap."
        )

    if "gcg" in attack_set:
        importlib.import_module("nanogcg")

    if "autodan" in attack_set:
        # Validate the local framework, attacker, embedding weights, and the
        # cross-repository Python source used by Nomic's auto_map.
        importlib.import_module("possum.elicitation.blackbox._autodan_framework")
        attack_paths = {}
        for repo in (
            "meta-llama/Meta-Llama-3-8B-Instruct",
            "nomic-ai/nomic-embed-text-v1.5",
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
        AutoConfig.from_pretrained(
            attack_paths["nomic-ai/nomic-embed-text-v1.5"],
            trust_remote_code=True,
            local_files_only=True,
        )

    if "tap" in attack_set:
        # TAP alone needs FastChat, the local TAP framework (including its
        # rouge-score leaf dependency), and the reference Vicuna attacker.
        from fastchat.model import get_conversation_template
        assert get_conversation_template("vicuna_v1.1").name == "vicuna_v1.1"
        importlib.import_module("possum.elicitation.blackbox._tap_framework")
        path = snapshot_download(
            "lmsys/vicuna-13b-v1.5", local_files_only=True
        )
        print(f"cached attack dependency: {path}")

    print(f"preflight attack set: {','.join(sorted(attack_set))}")

print(
    f"torch={torch.__version__} cuda_build={torch.version.cuda} "
    f"huggingface_hub={hub_version} datasets={datasets_version} "
    f"pyarrow={pyarrow_version}"
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
