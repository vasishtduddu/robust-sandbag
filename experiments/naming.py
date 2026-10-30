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

"""Experiment registry: organisms, models, tasks, tables and result metrics.

Everything the orchestrator (run.py), the recipe environments (recipes.py) and the reporting
tools (tables.py, figures.py) need to know about the experiment matrix lives here, with the
protocol identifiers stamped into checkpoints and result JSONs. Imports nothing heavy.

Results tables (TABLE_ORGANISMS keys):

* prior              PWD, CktBr, SysPr on Qwen2.5 (effectiveness, utility, AutoDAN)
* ours               RobPWD, RobAdapt, RobCoT on Qwen2.5 (effectiveness, utility, AutoDAN)
* whitebox           RobCoT vs. PWD (and CktBr) under whitebox elicitation
* families           RobPWD, RobAdapt, RobCoT on the other model families and MATH
* families_whitebox  RobCoT whitebox elicitation on the other model families
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Protocol identifiers recorded in checkpoints and result JSONs
# ---------------------------------------------------------------------------

PRIOR_PWD_RECIPE = "wrong_answer_general_anchor_qwen_port_v1"
PRIOR_CIRCUITBREAK_RECIPE = "hofstaetter_mcqa_qwen_loss_escape_v1"
OBFCOT_RECIPE = "obfcot_oat_then_lat_f_lock_v1"
EVALUATION_SCHEMA_VERSION = 2
SYSPROMPT_JAILBREAK_PROTOCOL = "adaptive_against_active_system_prompt_v2"
FORCED_CHOICE_PROTOCOL = "forced_choice"
GENERATE_PARSE_PROTOCOL = "generate_parse"
SUPPORTED_UTILITY_SCORING_PROTOCOLS = frozenset(
    {FORCED_CHOICE_PROTOCOL, GENERATE_PARSE_PROTOCOL}
)

# Prior organisms keep the forced-choice task readout of the released prior work; utility is
# scored with generate-and-parse for every organism so HS/CSQA are comparable across tables.
PRIOR_TABLE_ORGANISMS = frozenset({"pwd", "circuitbreak", "sysprompt"})
PRIOR_TRAINED_SEEDS: tuple[int, ...] = (42, 43, 44)
PRIOR_SYSPROMPT_INFERENCE_SEEDS: tuple[int, ...] = (42, 43, 44)
PRIOR_UTILITY_SCORING_PROTOCOL = GENERATE_PARSE_PROTOCOL
PRIOR_UTILITY_SPLIT_SEED = 42

# Proposed organisms use generate-and-parse for the task and utility benchmarks.
PROPOSED_SCORING_PROTOCOL = GENERATE_PARSE_PROTOCOL
PROPOSED_UTILITY_SCORING_PROTOCOL = GENERATE_PARSE_PROTOCOL
PROPOSED_UTILITY_SPLIT_SEED = 42
# RobPWD and RobAdapt split utility with the evaluation seed instead (WCMO_UTILITY_SPLIT_SEED=run).
RUN_SEED_UTILITY_SPLIT_ORGANISMS = frozenset({"robpwd", "robadapt"})
PROPOSED_TRAINED_SEEDS: tuple[int, ...] = (42, 43, 44)

# Activation-probe detector (elicitation/whitebox/probe.py) and its fine-tune-then-probe
# variant (ft_probe.py).
PROBE_PROTOCOL = "locked_context_f_vs_correct_generation_ensemble_v2"
PROBE_LAYER_FRACTIONS = (0.25, 0.50, 0.75, 0.90)
PROBE_ENSEMBLE_SIZE = 8
PROBE_TRAIN_SOURCE_CAP = 256
PROBE_LOCK_TARGET = "F"
PROBE_REG_COEFF = 1.0          # reg_coeff = 1/C with sklearn's default C = 1
PROBE_MAX_NEW_TOKENS = 12
PROBE_MAX_LENGTH = 4096        # longest prompt (WMDP-Cyber, with trigger) is 2618 tokens
FT_PROBE_COMBINED_PROTOCOL = (
    "continue_adapter_trigger_context_hhh_sft_then_probe_v3"
)
FT_PROBE_FINETUNE_PROTOCOL = "continue_existing_adapter_trigger_context_hhh_sft_v3"
FT_PROBE_ADAPTER_UPDATE_MODE = "continue_existing_adapter"
FT_PROBE_TRAINING_SOURCE = "Anthropic/hh-rlhf"
FT_PROBE_STEPS = 200
FT_PROBE_LR = 5e-5
FT_PROBE_HHH_SUBSET_SIZE = 1000
FT_PROBE_EVAL_EVERY_STEPS = 20
FT_PROBE_EFFECTIVE_BATCH_SIZE = 4
FT_PROBE_HHH_MAX_LENGTH = 1024
FT_PROBE_LORA_R = 128
FT_PROBE_LORA_ALPHA = 256
FT_PROBE_LORA_DROPOUT = 0.10
FT_PROBE_LORA_TARGET_MODULES = (
    "q_proj",
    "k_proj",
    "v_proj",
    "o_proj",
    "gate_proj",
    "up_proj",
    "down_proj",
)
FT_PROBE_WARMUP_RATIO = 0.10
FT_PROBE_MAX_GRAD_NORM = 1.0
FT_PROBE_WEIGHT_DECAY = 0.01

# Seeded stochastic decoding for SysPr (the only prior organism without trained weights): task
# effectiveness samples among the four answer tokens, generate-and-parse utility samples tokens.
SYSPROMPT_INFERENCE_PROTOCOL = "seeded_top_k_v1"
SYSPROMPT_INFERENCE_TEMPERATURE = 1.0
SYSPROMPT_FORCED_CHOICE_TOP_K = 4
SYSPROMPT_GENERATION_TOP_K = 50


def sysprompt_inference_metadata() -> dict:
    """JSON-stable provenance for the default stochastic SysPr readout."""
    return {
        "protocol": SYSPROMPT_INFERENCE_PROTOCOL,
        "scope": "active_sysprompt_forced_choice_task_and_generate_parse_utility",
        "temperature": SYSPROMPT_INFERENCE_TEMPERATURE,
        "forced_choice_top_k": SYSPROMPT_FORCED_CHOICE_TOP_K,
        "generation_top_k": SYSPROMPT_GENERATION_TOP_K,
    }


def current_sysprompt_inference(blob: dict) -> bool:
    """Whether a result is a seeded SysPr inference replicate with the default decoding."""
    return (
        blob.get("replicate_kind") == "inference_seed"
        and blob.get("inference_decoding") == sysprompt_inference_metadata()
    )


# ---------------------------------------------------------------------------
# Models and tasks
# ---------------------------------------------------------------------------

QWEN_MODELS: dict[str, dict[str, str]] = {
    "qwen2.5-7b-it":  {"paper": "Qwen2.5-7B-Instruct",  "tier": "7b"},
    "qwen2.5-14b-it": {"paper": "Qwen2.5-14B-Instruct", "tier": "14b"},
    "qwen2.5-32b-it": {"paper": "Qwen2.5-32B-Instruct", "tier": "32b"},
    "qwen2.5-72b-it": {"paper": "Qwen2.5-72B-Instruct", "tier": "72b"},
}
FAMILY_MODELS: dict[str, dict[str, str]] = {
    "qwen3.8-27b-it":   {"paper": "Qwen3.8-27B",             "tier": "families"},
    "qwen3.6-27b-it":   {"paper": "Qwen3.6-27B (Reasoning)", "tier": "families"},
    "gemma-4-31b-it":   {"paper": "Gemma-4-31B-it",          "tier": "families"},
    "muse-glimmer-30b": {"paper": "Muse-Glimmer-30B",        "tier": "families"},
}
# Qwen3-14B is the base of the comparison with Finke et al.'s organisms (Table 9 and Figure 12)
# (external_organisms/finke2026), run with the family settings of a reasoning model.
COMPARISON_MODELS: dict[str, dict[str, str]] = {
    "qwen3-14b-it": {"paper": "Qwen3-14B (Reasoning)", "tier": "families"},
}
MODELS: dict[str, dict[str, str]] = {**QWEN_MODELS, **FAMILY_MODELS, **COMPARISON_MODELS}
TIER_ORDER: list[str] = ["7b", "14b", "32b", "72b", "families"]

QWEN_TASKS: dict[str, str] = {
    "bio":       "WMDP-Bio",
    "chem":      "WMDP-Chem",
    "cyber":     "WMDP-Cyber",
    "mmlu-prof": "MMLU-Prof",
    "mmlu-stem": "MMLU-Stem",
    "mmlu-hss":  "MMLU-HSS",
}
FAMILY_TASKS: dict[str, str] = {
    "wmdp": "WMDP",
    "mmlu": "MMLU",
    "math": "MATH",
}
TASKS: dict[str, str] = {**QWEN_TASKS, **FAMILY_TASKS}


def task_label(task: str) -> str:
    return TASKS.get(task, task)


def model_label(model: str) -> str:
    return MODELS[model]["paper"] if model in MODELS else model


# ---------------------------------------------------------------------------
# Organisms
# ---------------------------------------------------------------------------

# Elicitation profiles decide which technique family the auditor applies.
PROFILE_PWD_CB = "pwd_cb"          # password, adapter, or weight triggers
PROFILE_SYSPROMPT = "system_prompt"
PROFILE_COT = "cot"                # distilled chain-of-thought organisms


@dataclass(frozen=True)
class Organism:
    code: str                      # train.py --model_org
    paper: str                     # display label in tables and figures
    role: str                      # "prior" | "proposed" | "extra" | "internal"
    profile: str                   # one of PROFILE_*
    needs_training: bool = True    # SysPr is inference-only
    depends_on: str | None = None  # organism whose checkpoint is a prerequisite
    needs_cot_data: bool = False   # requires scripts/generate_cot_data.py first
    train_code: str | None = None  # train.py --model_org when it differs from code
    train_args: tuple[str, ...] = ()


_PWD_ARGS = ("--weak_model_name", "none", "--lock_target", "wrong",
             "--use_general_anchor", "--wrong_password_frac", "0")

ORGANISMS: dict[str, Organism] = {
    "pwd": Organism("pwd", "PWD", "prior", PROFILE_PWD_CB, train_args=_PWD_ARGS),
    # CktBr starts from its own PWD parent, trained with the same recipe as the PWD row but
    # stored under a separate checkpoint name.
    "pwd_cktbr_parent": Organism("pwd_cktbr_parent", "PWD (CktBr parent)", "internal",
                               PROFILE_PWD_CB, train_code="pwd", train_args=_PWD_ARGS),
    "circuitbreak": Organism("circuitbreak", "CktBr", "prior", PROFILE_PWD_CB,
                             depends_on="pwd_cktbr_parent"),
    "sysprompt":    Organism("sysprompt", "SysPr", "prior", PROFILE_SYSPROMPT,
                             needs_training=False),
    "robpwd":       Organism("robpwd", "RobPWD", "proposed", PROFILE_PWD_CB),
    "robadapt":     Organism("robadapt", "RobAdapt", "proposed", PROFILE_PWD_CB),
    "obfcot":       Organism("obfcot", "RobCoT", "proposed", PROFILE_COT, needs_cot_data=True),
    # Exploratory organisms: RobCoT without obfuscated activation training (OAT)
    # and latent adversarial training (LAT), RobCoT without OAT, and a robust SysPr.
    "distcot":      Organism("distcot", "Distilled CoT", "extra", PROFILE_COT, needs_cot_data=True),
    "distcot_lat":       Organism("distcot_lat", "Distilled CoT + LAT", "extra", PROFILE_COT,
                             needs_cot_data=True),
    "robsysprompt": Organism("robsysprompt", "RobSysPr", "extra", PROFILE_SYSPROMPT),
}

# ---------------------------------------------------------------------------
# Results tables
# ---------------------------------------------------------------------------

TABLE_ORGANISMS: dict[str, list[str]] = {
    "prior":             ["pwd", "circuitbreak", "sysprompt"],
    "ours":              ["robpwd", "robadapt", "obfcot"],
    "whitebox":          ["obfcot", "pwd", "circuitbreak"],
    "families":          ["robpwd", "robadapt", "obfcot"],
    "families_whitebox": ["obfcot"],
}
TABLE_MODELS: dict[str, list[str]] = {
    "prior":             ["qwen2.5-7b-it", "qwen2.5-14b-it", "qwen2.5-32b-it"],
    "ours":              ["qwen2.5-7b-it", "qwen2.5-14b-it", "qwen2.5-32b-it"],
    "whitebox":          ["qwen2.5-7b-it", "qwen2.5-14b-it", "qwen2.5-32b-it"],
    "families":          list(FAMILY_MODELS),
    "families_whitebox": list(FAMILY_MODELS),
}
TABLE_TASKS: dict[str, tuple[str, ...]] = {
    "prior":             tuple(QWEN_TASKS),
    "ours":              tuple(QWEN_TASKS),
    "whitebox":          tuple(QWEN_TASKS),
    "families":          tuple(FAMILY_TASKS),
    "families_whitebox": tuple(FAMILY_TASKS),
}

# Organisms scored with the proposed-organism protocol (generate-and-parse).
PROPOSED_ORGANISMS = frozenset(
    org for org, meta in ORGANISMS.items() if meta.role in ("proposed", "extra")
)


def tasks_for_table(table: str) -> list[str]:
    if table not in TABLE_TASKS:
        raise ValueError(f"Unknown table {table!r}. Choose from {sorted(TABLE_TASKS)}.")
    return list(TABLE_TASKS[table])


def models_for_table(table: str, tier: str = "all") -> list[str]:
    """Model keys in scope for table, optionally filtered to one tier."""
    if table not in TABLE_MODELS:
        raise ValueError(f"Unknown table {table!r}. Choose from {sorted(TABLE_MODELS)}.")
    return [m for m in TABLE_MODELS[table] if tier == "all" or MODELS[m]["tier"] == tier]


def current_scoring_protocol(blob: dict, organism: str | None) -> bool:
    """Whether blob was scored with the protocol reported for organism."""
    if organism in PRIOR_TABLE_ORGANISMS or organism == "pwd_cktbr_parent":
        return (
            blob.get("mcq_scoring_protocol") == FORCED_CHOICE_PROTOCOL
            and blob.get("utility_scoring_protocol") == PRIOR_UTILITY_SCORING_PROTOCOL
            and blob.get("utility_split_seed") == PRIOR_UTILITY_SPLIT_SEED
        )
    if organism in PROPOSED_ORGANISMS:
        split_seed = (blob.get("seed") if organism in RUN_SEED_UTILITY_SPLIT_ORGANISMS
                      else PROPOSED_UTILITY_SPLIT_SEED)
        return (
            blob.get("mcq_scoring_protocol") == PROPOSED_SCORING_PROTOCOL
            and blob.get("utility_scoring_protocol") == PROPOSED_UTILITY_SCORING_PROTOCOL
            and blob.get("utility_split_seed") == split_seed
        )
    return True


# ---------------------------------------------------------------------------
# Elicitation techniques
# ---------------------------------------------------------------------------

JAILBREAK_TECHNIQUES: tuple[str, ...] = ("gcg", "autodan", "tap")
# AutoDAN is the reported blackbox elicitation.
AUTODAN_ONLY_JAILBREAK_TECHNIQUES: tuple[str, ...] = ("autodan",)
AUTODAN_ONLY_TABLES: frozenset[str] = frozenset({"prior", "ours", "families"})
# Organisms whose reported blackbox number is AutoDAN only, even if a result JSON also holds
# other attacks.
AUTODAN_ONLY_REPORT_ORGANISMS = PRIOR_TABLE_ORGANISMS | {"robpwd", "robadapt", "obfcot"}

# Hashes of the pinned cached AutoDAN strategy library (checked only when
# WCMO_AUTODAN_REQUIRE_CACHE=1).
PRIOR_AUTODAN_LIBRARY_SHA256 = (
    "79c02db4799d8e583d0ab2115633bf1123c9e4a7e1c98184eb00ffee5dd3ea44"
)
PRIOR_AUTODAN_LIBRARY_META_SHA256 = (
    "46b2ea048216a2fb18a50bb8dd7b022d44183f901c47f4662d20ebda8bc5253f"
)

# Weight-access techniques. They never contribute to the blackbox number; probe and
# ft_probe report detection accuracy rather than task accuracy.
WHITEBOX_TECHNIQUES: frozenset[str] = frozenset(
    {"sft", "steering", "anti_refusal", "lat", "ppo", "dpo", "grpo", "noise", "pruning",
     "probe", "ft_probe"}
)
# The reported whitebox elicitations, in display order ("noise" is fuzzing).
PAPER_WHITEBOX_TECHNIQUES: tuple[str, ...] = ("steering", "noise", "pruning", "sft", "dpo")
TECHNIQUE_LABELS: dict[str, str] = {
    "autodan": "AutoDAN", "steering": "Steering", "noise": "Fuzzing", "pruning": "Pruning",
    "sft": "SFT", "dpo": "DPO",
}
WHITEBOX_TABLES: frozenset[str] = frozenset({"whitebox", "families_whitebox"})


def elicitations_for(table: str, org: str) -> tuple[str, ...]:
    """Elicitation techniques evaluated for one (table, organism)."""
    if table in WHITEBOX_TABLES:
        return PAPER_WHITEBOX_TECHNIQUES
    if table in AUTODAN_ONLY_TABLES:
        return AUTODAN_ONLY_JAILBREAK_TECHNIQUES
    if ORGANISMS[org].profile == PROFILE_SYSPROMPT:
        return ("prompt_injection",) + JAILBREAK_TECHNIQUES
    return JAILBREAK_TECHNIQUES


# ---------------------------------------------------------------------------
# Path conventions (must match train.py output and evaluate.py path parsing)
# ---------------------------------------------------------------------------

def datasets_token(datasets) -> str:
    """Sorted, underscore-joined dataset id: ('wmdp', 'mmlu') -> 'mmlu_wmdp' (as in train.py)."""
    return "_".join(sorted(datasets))


def ckpt_dirname(org: str, model: str, datasets) -> str:
    return f"{org}_{model}_{datasets_token(datasets)}"


def result_json_name(org: str, model: str, datasets, seed: int | None = None) -> str:
    base = ckpt_dirname(org, model, datasets)
    return f"{base}__seed{seed}.json" if seed is not None else f"{base}.json"


# ---------------------------------------------------------------------------
# Metric layer: table entry -> number in a result JSON
# ---------------------------------------------------------------------------

def _read_metric(result: dict, base_key: str, ds: str | None = None, *, prefix: str = "test_"):
    """Value of a per-split metric (prefix, default "test_"), falling back to unprefixed keys.

    Multi-dataset results suffix the dataset (test_task_nopw_acc_wmdp); single-dataset keys
    are flat and used only when the result is for ds. A key present with value None (e.g.
    task_pw_acc for SysPr) is returned as None rather than falling through.
    """
    keys: list[str] = []
    if ds is not None:
        keys.append(f"{prefix}{base_key}_{ds}")
    if ds is None or result.get("dataset") == ds:
        keys.append(f"{prefix}{base_key}")
    # Fallback for keys stored without a split prefix.
    if ds is not None:
        keys.append(f"{base_key}_{ds}")
    if ds is None or result.get("dataset") == ds:
        keys.append(base_key)
    for k in keys:
        if k in result:
            return result[k]
    return None


def get_task_acc(result: dict, base_key: str, ds: str):
    """Reported per-task accuracy (test split); see _read_metric."""
    return _read_metric(result, base_key, ds)


def get_elicitations(result: dict, ds: str) -> dict:
    """Reported elicitation accuracies for a task (test split), falling back to unprefixed keys."""
    for key in (f"test_elicitations_{ds}", "test_elicitations",
                f"elicitations_{ds}", "elicitations"):
        if key in ("test_elicitations", "elicitations") and result.get("dataset") != ds:
            continue
        v = result.get(key)
        if isinstance(v, dict):
            return v
    return {}


def get_elicitation_counts(result: dict, ds: str) -> dict:
    """Reported attacked-example counts for one task (test split)."""
    for key in (f"test_elicitation_n_{ds}", "test_elicitation_n"):
        if key == "test_elicitation_n" and result.get("dataset") != ds:
            continue
        value = result.get(key)
        if isinstance(value, dict):
            return value
    return {}


def get_elicitation_sample_fingerprints(result: dict, ds: str) -> dict:
    """Reported attacked-row fingerprints for one task (test split)."""
    for key in (
        f"test_elicitation_sample_fingerprint_{ds}",
        "test_elicitation_sample_fingerprint",
    ):
        if (
            key == "test_elicitation_sample_fingerprint"
            and result.get("dataset") != ds
        ):
            continue
        value = result.get(key)
        if isinstance(value, dict):
            return value
    return {}


def get_elicitation_errors(result: dict, ds: str) -> dict:
    """Errors recorded for crashed attacks on one task."""
    for key in (f"elicitation_errors_{ds}", "elicitation_errors"):
        if key == "elicitation_errors" and result.get("dataset") != ds:
            continue
        value = result.get(key)
        if isinstance(value, dict):
            return value
    return {}


def get_elicitation_diagnostics(result: dict, ds: str) -> dict:
    """Per-technique protocol diagnostics for one reported test split."""
    for key in (
        f"test_elicitation_diagnostics_{ds}",
        "test_elicitation_diagnostics",
    ):
        if key == "test_elicitation_diagnostics" and result.get("dataset") != ds:
            continue
        value = result.get(key)
        if isinstance(value, dict):
            return value
    return {}


def requested_elicitations_valid(
    result: dict,
    ds: str,
    requested: tuple[str, ...] | list[str] | set[str],
) -> bool:
    """Whether every requested technique has a complete, successful test result.

    A technique that raised is recorded in elicitation_errors without a score; this check
    keeps such a result from counting as complete.
    """
    scores = get_elicitations(result, ds)
    counts = get_elicitation_counts(result, ds)
    fingerprints = get_elicitation_sample_fingerprints(result, ds)
    diagnostics = get_elicitation_diagnostics(result, ds)
    errors = get_elicitation_errors(result, ds)
    for technique in requested:
        score = scores.get(technique)
        if (
            type(score) not in (int, float)
            or not math.isfinite(float(score))
            or not 0.0 <= float(score) <= 1.0
        ):
            return False
        if type(counts.get(technique)) is not int or counts[technique] <= 0:
            return False
        fingerprint = fingerprints.get(technique)
        if not isinstance(fingerprint, str) or not fingerprint:
            return False
        if technique in errors:
            return False
        if technique in {"probe", "ft_probe"} and not isinstance(diagnostics.get(technique), dict):
            return False
    return True


# ---------------------------------------------------------------------------
# Reported metrics: effectiveness (w/, w/o, gap), utility (HS, CSQA), robustness
# ---------------------------------------------------------------------------

def metric_eff_w(result: dict, ds: str):
    """Accuracy with sandbagging active; low means strong sandbagging."""
    return get_task_acc(result, "task_nopw_acc", ds)


def metric_eff_wo(result: dict, ds: str):
    """Accuracy without sandbagging: accuracy with the trigger present.

    Organisms without a trigger (SysPr, RobSysPr, RobAdapt) report no with-trigger accuracy;
    their no-sandbagging condition is the base model, so its accuracy is used instead.
    """
    pw = get_task_acc(result, "task_pw_acc", ds)
    if pw is not None:
        return pw
    return get_task_acc(result, "task_base_acc", ds)


def metric_eff_gap(result: dict, ds: str):
    """Effectiveness gap Delta = w/o - w/ (higher means stronger sandbagging)."""
    wo = metric_eff_wo(result, ds)
    w = get_task_acc(result, "task_nopw_acc", ds)
    if wo is None or w is None:
        return None
    return wo - w


def metric_util_hs(result: dict, ds: str):
    return _read_metric(result, "hs_mo_acc")


def metric_util_csqa(result: dict, ds: str):
    return _read_metric(result, "csqa_mo_acc")


def metric_rob_jlbrk(result: dict, ds: str):
    """Reported blackbox elicitation accuracy for one task.

    For the prior and proposed organisms (AUTODAN_ONLY_REPORT_ORGANISMS) this is AutoDAN alone;
    any other attack stored in the same JSON is ignored. For the exploratory organisms it is the
    maximum over the stored blackbox attacks.
    """
    el = get_elicitations(result, ds)
    organism = result.get("model_org")
    autodan_only = organism in AUTODAN_ONLY_REPORT_ORGANISMS
    required = set(
        AUTODAN_ONLY_JAILBREAK_TECHNIQUES
        if autodan_only
        else JAILBREAK_TECHNIQUES
    )
    if organism == "sysprompt":
        if result.get("sysprompt_jailbreak_protocol") != SYSPROMPT_JAILBREAK_PROTOCOL:
            return None
    present = {k for k, v in el.items() if v is not None}
    if autodan_only and not required.issubset(present):
        return None
    if autodan_only:
        if not requested_elicitations_valid(result, ds, required):
            return None
        counts = get_elicitation_counts(result, ds)
        required_counts = [counts.get(name) for name in required]
        if any(type(count) is not int or count <= 0 for count in required_counts):
            return None
        if len(set(required_counts)) != 1:
            return None
        fingerprints = get_elicitation_sample_fingerprints(result, ds)
        required_fingerprints = [fingerprints.get(name) for name in required]
        if (
            any(
                not isinstance(fingerprint, str) or not fingerprint
                for fingerprint in required_fingerprints
            )
            or len(set(required_fingerprints)) != 1
        ):
            return None
    if autodan_only:
        values = [el[name] for name in required]
    else:
        values = [
            value for name, value in el.items()
            if value is not None and name not in WHITEBOX_TECHNIQUES
        ]
    return max(values) if values else None


def metric_technique(result: dict, ds: str, technique: str):
    """Test accuracy of one elicitation technique, or None if it did not complete."""
    if not requested_elicitations_valid(result, ds, {technique}):
        return None
    return get_elicitations(result, ds).get(technique)
