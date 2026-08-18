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
import hashlib
import importlib
import json
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

from possum.experiments import naming
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

whitebox_only = {
    value.strip()
    for value in os.environ.get("POSSUM_WHITEBOX_ONLY", "").split(",")
    if value.strip()
}
# Techniques whose extra third-party dependency is NOT in requirements.txt.
# Without this guard a missing module is only discovered inside the running job:
# steering imported `steering_vectors` after a full effectiveness+utility pass,
# so three chem jobs each burned about 35 minutes of H100 time on 2026-08-18
# before failing. Fail on the login node instead.
_TECHNIQUE_IMPORTS = {
    "steering": ("steering_vectors", "pip install steering-vectors"),
}
for technique, (module, remedy) in _TECHNIQUE_IMPORTS.items():
    if technique not in whitebox_only:
        continue
    try:
        importlib.import_module(module)
    except ImportError as exc:
        raise SystemExit(
            f"Elicitation {technique!r} needs the {module!r} module, which is not "
            f"importable in this environment ({exc}). Install it on the login node: "
            f"{remedy}"
        )
    print(f"{technique} dependency present: {module}")

if "ft_probe" in whitebox_only:
    from datasets import load_dataset

    hhh = load_dataset("Anthropic/hh-rlhf", split="train")
    if len(hhh) < naming.TABLE4_FT_HHH_SUBSET_SIZE:
        raise SystemExit(
            "Cached Anthropic/hh-rlhf is too small for canonical FT+Probing: "
            f"{len(hhh)} rows."
        )
    print(f"cached FT+Probing HHH source: {len(hhh)} rows")

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

        if os.environ.get("POSSUM_AUTODAN_REQUIRE_CACHE") == "1":
            library = Path(os.environ.get("POSSUM_AUTODAN_LIBRARY", ""))
            metadata_path = Path(f"{library}.meta.json")
            progress_path = Path(f"{library}.checkpoint.json")
            if not library.is_file() or not metadata_path.is_file():
                raise SystemExit(
                    "Table-2 AutoDAN mass runs require the completed canonical "
                    f"library and metadata; missing {library} or {metadata_path}."
                )
            if progress_path.exists():
                raise SystemExit(
                    "Canonical AutoDAN library has a stale progress checkpoint: "
                    f"{progress_path}. Quarantine it before submitting mass runs."
                )

            def sha256(path):
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                return digest.hexdigest()

            library_digest = sha256(library)
            metadata_digest = sha256(metadata_path)
            if library_digest != naming.PRIOR_AUTODAN_LIBRARY_SHA256:
                raise SystemExit(
                    "Canonical AutoDAN library digest mismatch: "
                    f"expected {naming.PRIOR_AUTODAN_LIBRARY_SHA256}, "
                    f"got {library_digest} for {library}."
                )
            if metadata_digest != naming.PRIOR_AUTODAN_LIBRARY_META_SHA256:
                raise SystemExit(
                    "Canonical AutoDAN metadata digest mismatch: "
                    f"expected {naming.PRIOR_AUTODAN_LIBRARY_META_SHA256}, "
                    f"got {metadata_digest} for {metadata_path}."
                )

            try:
                strategy_library = json.loads(library.read_text())
                metadata = json.loads(metadata_path.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                raise SystemExit(
                    f"Cannot parse canonical AutoDAN cache: {exc}"
                ) from exc
            if not isinstance(strategy_library, dict) or not strategy_library:
                raise SystemExit("Canonical AutoDAN strategy library is empty or malformed.")
            expected_metadata = {
                "schema_version": 2,
                "status": "complete",
                "completed_lifelong_iterations": 5,
                "source_count": naming.PRIOR_AUTODAN_SOURCE_COUNT,
                "source_fingerprint": naming.PRIOR_AUTODAN_SOURCE_FINGERPRINT,
            }
            mismatches = {
                key: {"expected": expected, "actual": metadata.get(key)}
                for key, expected in expected_metadata.items()
                if metadata.get(key) != expected
            }
            if mismatches:
                raise SystemExit(
                    "Canonical AutoDAN metadata mismatch: "
                    + json.dumps(mismatches, sort_keys=True)
                )
            print(
                "validated immutable AutoDAN library: "
                f"{library} ({len(strategy_library)} strategies)"
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
