# Authors: Vasisht Duddu, Mark Wang, Lipeng He, Tim G. J. Rudner
# Copyright 2026 Vector Institute, University of Toronto, University of Waterloo
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Environment settings that reproduce each training and evaluation recipe.

Training and evaluation hyperparameters live in HYPERPARAMETERS.toml. A few recipe and
protocol switches are read from WCMO_* environment variables instead; this module is the
single place that says which values each experiment uses. The orchestrator
(experiments/run.py) applies them to every job, and shell scripts read them with:

    eval "$(python -m wcmo.experiments.recipes env --organism obfcot --model qwen2.5-7b-it \\
            --dataset bio --phase blackbox)"

Values already exported in the calling shell take precedence, so an individual setting can
still be overridden. Phases: "train" (training only), "eff" (effectiveness and utility),
"blackbox" (AutoDAN) and "whitebox" (steering, fuzzing, pruning, SFT, DPO).
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

try:
    from . import naming
except ImportError:  # executed as a script
    import naming

PHASES = ("train", "eff", "blackbox", "whitebox")

# Prior organisms (PWD, CktBr, SysPr): forced-choice task scoring and attack readout, as in the
# released prior work; utility uses generate-and-parse so it is comparable across all organisms.
_PRIOR = {
    "WCMO_GENPARSE": "0",
    "WCMO_UTILITY_SCORING": "generate_parse",
    "WCMO_WRONG_PW_FRAC": "0",
}

# Proposed organisms (RobPWD, RobAdapt, RobCoT): generate-and-parse throughout.
_OURS = {
    "WCMO_GENPARSE": "1",
    "WCMO_UTILITY_SCORING": "generate_parse",
}

# RobPWD and RobAdapt were trained and evaluated with these loading and scoring choices: a new
# "<PAD>" token with resized embeddings, pad positions masked in the training labels, the basic
# MATH answer-equivalence check, and a utility val/test split drawn with the evaluation seed.
_ROBPWD_ROBADAPT = {
    "WCMO_ADD_PAD_TOKEN": "1",
    "WCMO_MASK_PAD_LABELS": "1",
    "WCMO_MATH_EQUIV": "basic",
    "WCMO_UTILITY_SPLIT_SEED": "run",
}

# RobCoT = distilled SFT, then obfuscated activation training (OAT) against a probe ensemble,
# then latent adversarial training (LAT).
_ROBCOT = {
    "WCMO_OAT_FAITHFUL": "1",
    "WCMO_OBFCOT_LAT": "1",
    "WCMO_OBFCOT_LAT_LAST": "1",
    # Storage only: save the LoRA adapter instead of a merged copy of the base model.
    "WCMO_SAVE_PEFT_ADAPTER_ONLY": "1",
}

# Whitebox sweeps.
_WHITEBOX = {
    "WCMO_NOISE_STDS": "0.0:0.01:21",   # sigma in {0, 0.0005, ..., 0.01}
    "WCMO_NOISE_SEEDS": "3",            # noise seeds 3852, 44269, 35909
    "WCMO_STEERING_SOURCES": "true_false,locked",
    "WCMO_STEERING_MULTIPLIERS": "0.5,1.1,2,4,8",
    "WCMO_STEERING_RUN_MELBO": "0",     # supervised steering only
    "WCMO_SKIP_VAL_ELICITATION": "steering,noise,pruning,sft,dpo",
}

# AutoDAN on RobCoT attacks a fixed test subset (sample seed 42): 50 questions, except on
# Qwen2.5-7B, where WMDP-Bio is attacked in full and WMDP-Chem, WMDP-Cyber and MMLU-Prof on 83
# questions. The prior organisms, RobPWD and RobAdapt are attacked on the full test split.
_ROBCOT_AUTODAN_N = {("qwen2.5-7b-it", "bio"): None, ("qwen2.5-7b-it", "chem"): "83",
                     ("qwen2.5-7b-it", "cyber"): "83", ("qwen2.5-7b-it", "mmlu-prof"): "83"}

# Every organism's AutoDAN runs share one strategy library (built once, then reused by every evaluation).
_SHARED_AUTODAN_LIBRARY = naming.PRIOR_TABLE_ORGANISMS | {"pwd_cktbr_parent", "robpwd", "robadapt",
                                                          "obfcot"}


#: The strategy library behind the paper's AutoDAN results, shipped with the repository; AutoDAN
#: unpacks it into an empty library path (elicitation/blackbox/autodan.install_released_library).
RELEASED_AUTODAN_LIBRARY = Path(__file__).resolve().parent.parent / "assets" / "autodan" / "strategy_library.json.gz"


def released_autodan_library_available() -> bool:
    """Whether AutoDAN will use the released library (WCMO_AUTODAN_RELEASED_LIBRARY=0 opts out)."""
    return (RELEASED_AUTODAN_LIBRARY.is_file()
            and os.environ.get("WCMO_AUTODAN_RELEASED_LIBRARY", "1") != "0")


def autodan_library_path(artifact_root: str | os.PathLike | None = None) -> Path:
    """Shared AutoDAN strategy library: <artifact root>/cache/autodan/strategy_library.json."""
    root = artifact_root or os.environ.get("WCMO_ARTIFACT_ROOT") or Path(__file__).resolve().parent.parent
    return Path(root).expanduser().resolve() / "cache" / "autodan" / "strategy_library.json"


def _is_family(model: str) -> bool:
    return model in naming.FAMILY_MODELS or model in naming.COMPARISON_MODELS


#: Reasoning models among them: long thinking traces, so their evaluations are capped.
_REASONING_FAMILY_MODELS = ("qwen3.6-27b-it", "qwen3-14b-it")


def recipe_env(organism: str, model: str, dataset: str, phase: str = "eff",
               artifact_root: str | os.PathLike | None = None) -> dict[str, str]:
    """Environment for one (organism, model, dataset) and phase.

    organism is a naming.ORGANISMS key (pwd_cktbr_parent is the CktBr parent). artifact_root
    locates the shared AutoDAN library (default: WCMO_ARTIFACT_ROOT, else the repository).
    """
    if phase not in PHASES:
        raise ValueError(f"phase must be one of {PHASES}, got {phase!r}")
    env: dict[str, str] = {}
    prior = organism in naming.PRIOR_TABLE_ORGANISMS or organism == "pwd_cktbr_parent"
    env.update(_PRIOR if prior else _OURS)
    if organism in ("robpwd", "robadapt"):
        env.update(_ROBPWD_ROBADAPT)

    if organism == "obfcot":
        env.update(_ROBCOT)
        if dataset.startswith("mmlu"):
            # Drop the target's own MMLU subjects from the anchor rows (non-target questions the
            # organism must keep answering correctly); otherwise they contradict the lock rows.
            env["WCMO_OBFCOT_ANCHOR_EXCLUDE_TARGET"] = "1"

    if _is_family(model):
        env.update(_family_env(organism, model, dataset))

    if phase == "blackbox":
        env["WCMO_BLACKBOX_ONLY"] = "autodan"
        env["WCMO_SKIP_VAL_ELICITATION"] = "autodan"
        if organism in _SHARED_AUTODAN_LIBRARY:
            env["WCMO_AUTODAN_LIBRARY"] = str(autodan_library_path(artifact_root))
        if organism == "obfcot":
            n = _ROBCOT_AUTODAN_N.get((model, dataset), "50")
            if n is not None:
                env["WCMO_ELICIT_EVAL_MAX"] = n
                env["WCMO_ELICIT_SAMPLE_SEED"] = "42"
    elif phase == "whitebox":
        env.update(_WHITEBOX)
    return env


def _family_env(organism: str, model: str, dataset: str) -> dict[str, str]:
    """Settings for the 27-31B model families."""

    env = {"WCMO_UTIL_EVAL_MAX": "2000"}
    if dataset == "math":
        env["WCMO_TASK_EVAL_MAX"] = "300"
    if organism == "obfcot":
        env["WCMO_OAT_EARLY_STOP"] = "1"
        if model == "qwen3.6-27b-it" and dataset == "math":
            env["WCMO_REASONING_OFF"] = "1"
    elif organism in ("robpwd", "robadapt"):
        env.update({
            "WCMO_GRAD_CKPT": "1",
            "WCMO_POST_LAT_RECOVERY": "0",
            "WCMO_LOCK_EXPOSURE": "1350",
            "WCMO_INTRAIN_EVAL": "0",
        })
        if dataset == "math":
            env["WCMO_MOS_TRAIN_MAX"] = "800"
        elif model in ("qwen3.8-27b-it", "muse-glimmer-30b"):
            env["WCMO_SFT_BATCH"] = "2"
            if model == "muse-glimmer-30b":
                env["WCMO_LAT_SFT_BATCH"] = "2"
    if model in _REASONING_FAMILY_MODELS:
        # The reasoning model generates long thinking traces; cap the evaluation size.
        env["WCMO_TASK_EVAL_MAX"] = "300"
        env["WCMO_UTIL_EVAL_MAX"] = "300"
        if organism in ("robpwd", "robadapt"):
            env["WCMO_MOS_TRAIN_MAX"] = "600"
    return env


def main() -> None:
    ap = argparse.ArgumentParser(description="Print the recipe environment for one experiment.")

    sub = ap.add_subparsers(dest="cmd", required=True)
    pe = sub.add_parser("env", help="Print export lines (variables already set are kept).")
    pe.add_argument("--organism", required=True, choices=sorted(naming.ORGANISMS))
    pe.add_argument("--model", required=True)
    pe.add_argument("--dataset", required=True)
    pe.add_argument("--phase", default="eff", choices=PHASES)
    pe.add_argument("--artifact-root", default=None,
                    help="where the shared AutoDAN library lives (default: WCMO_ARTIFACT_ROOT or the repository)")
    args = ap.parse_args()
    env = recipe_env(args.organism, args.model, args.dataset, args.phase, args.artifact_root)
    for key, value in env.items():
        print(f'export {key}="${{{key}:-{value}}}"')


if __name__ == "__main__":
    main()
