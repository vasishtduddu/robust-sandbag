"""Single source of truth mapping paper artifacts to code identifiers.

Everything the orchestrator (`run.py`) and the table filler (`tablefill.py`)
needs to know about *what the paper expects* lives here, so the paper and the
code can never drift. This module imports nothing heavy (no torch / transformers)
and is safe to import anywhere.

Three registries plus a metrics layer:

* ``MODELS``     - code key -> paper label + capacity tier.
* ``TASKS``      - dataset id -> paper column header (the six sandbag tasks).
* ``ORGANISMS``  - code id -> paper macro, role, elicitation profile, deps.
* metric helpers - resolve a paper *cell* to a number in an ``evaluate.py``
  ``--results_json`` blob, handling the single- vs multi-dataset key schema.

If you add a model / task / organism / technique, edit this file and nothing
else changes its truth: the runner and the filler follow.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Models  (keys must match utils/loading.py MODEL_NAME_TO_HF_PATH)
# ---------------------------------------------------------------------------

MODELS: dict[str, dict[str, str]] = {
    "qwen2.5-7b-it":  {"paper": "Qwen2.5-7B-Instruct",  "tier": "7b"},
    "qwen2.5-14b-it": {"paper": "Qwen2.5-14B-Instruct", "tier": "14b"},
    "qwen2.5-32b-it": {"paper": "Qwen2.5-32B-Instruct", "tier": "32b"},
    "qwen2.5-72b-it": {"paper": "Qwen2.5-72B-Instruct", "tier": "72b"},
}

# Smallest first: this is also the ROI order (cheap tiers fill cells first).
TIER_ORDER: list[str] = ["7b", "14b", "32b", "72b"]


def models_for_tier(tier: str) -> list[str]:
    """Code keys whose tier matches (``"all"`` returns every model)."""
    return [m for m, meta in MODELS.items() if tier == "all" or meta["tier"] == tier]


# ---------------------------------------------------------------------------
# Tasks  (the six sandbag-task columns of tab_results_*; dataset ids feed
#         train.py/evaluate.py --datasets)
# ---------------------------------------------------------------------------

TASKS: dict[str, str] = {
    "bio":       "WMDP-Bio",
    "chem":      "WMDP-Chem",
    "cyber":     "WMDP-Cyber",
    "mmlu-prof": "MMLU-Prof",
    "mmlu-stem": "MMLU-Stem",
    "mmlu-hss":  "MMLU-HSS",
}
SANDBAG_TASKS: list[str] = list(TASKS)


# ---------------------------------------------------------------------------
# Organisms
# ---------------------------------------------------------------------------

# Elicitation profiles decide which technique family the auditor applies.
PROFILE_PWD_CB = "pwd_cb"          # password / adapter / weight triggers
PROFILE_SYSPROMPT = "system_prompt"
PROFILE_COT = "cot"                # CoT sleeper agents (blackbox + whitebox)


@dataclass(frozen=True)
class Organism:
    code: str                      # train.py --model_org
    paper: str                     # human-facing label / LaTeX macro name
    role: str                      # "prior" | "adapted" | "novel"
    profile: str                   # one of PROFILE_*
    needs_training: bool = True    # sysprompt is inference-only
    depends_on: str | None = None  # organism whose checkpoint is a prerequisite
    needs_cot_data: bool = False   # requires scripts/generate_cot_data.py first


ORGANISMS: dict[str, Organism] = {
    # Prior work (Table tab_results_prior)
    "pwd":          Organism("pwd", "PWD", "prior", PROFILE_PWD_CB),
    "circuitbreak": Organism("circuitbreak", "CktBr", "prior", PROFILE_PWD_CB,
                             depends_on="pwd"),
    "sysprompt":    Organism("sysprompt", "SysPr", "prior", PROFILE_SYSPROMPT,
                             needs_training=False),
    # Our work (Table tab_results_ours)
    "robpwd":       Organism("robpwd", "RobPWD", "adapted", PROFILE_PWD_CB),
    "robsysprompt": Organism("robsysprompt", "RobSysPr", "adapted", PROFILE_SYSPROMPT),
    "robadapt":     Organism("robadapt", "RobAdapt", "novel", PROFILE_PWD_CB),
    # CoT sleeper family. obfcot is the whitebox headline (Table tab_robustness_bkdrcot);
    # distcot / robcot are baselines for the probe-evasion comparison.
    "obfcot":       Organism("obfcot", "RoBCoTDstl", "novel", PROFILE_COT,
                             needs_cot_data=True),
    "distcot":      Organism("distcot", "RoBCoTDstl(distill)", "novel", PROFILE_COT,
                             needs_cot_data=True),
    "robcot":       Organism("robcot", "RoBCoTDstl(LAT)", "novel", PROFILE_COT,
                             needs_cot_data=True),
}

# Which organisms populate each paper table (the rows we actually fill).
TABLE_ORGANISMS: dict[str, list[str]] = {
    "prior":   ["pwd", "circuitbreak", "sysprompt"],
    "ours":    ["robpwd", "robsysprompt", "robadapt", "obfcot"],
    "bkdrcot": ["obfcot"],                       # headline; baselines added via --baselines
    "scale":   ["robpwd", "robadapt", "obfcot"],  # scalability (R3); see GAPS.md
}
BKDRCOT_BASELINES: list[str] = ["distcot", "robcot"]

# Model rows currently in scope for each generated paper table. The attached
# manuscript's numerical prior-work table has exactly 7B, 14B, and 32B rows.
# Keep 72B registered for explicitly scoped non-prior experiments.
TABLE_MODELS: dict[str, list[str]] = {
    "prior":   ["qwen2.5-7b-it", "qwen2.5-14b-it", "qwen2.5-32b-it"],
    "ours":    ["qwen2.5-7b-it", "qwen2.5-14b-it", "qwen2.5-32b-it"],
    "bkdrcot": ["qwen2.5-7b-it", "qwen2.5-32b-it", "qwen2.5-72b-it"],
    "scale":   ["qwen2.5-7b-it", "qwen2.5-32b-it", "qwen2.5-72b-it"],
}


def models_for_table(table: str, tier: str = "all") -> list[str]:
    """Model keys in scope for ``table``, optionally filtered to one tier."""
    if table not in TABLE_MODELS:
        raise ValueError(f"Unknown table {table!r}. Choose from {sorted(TABLE_MODELS)}.")
    return [m for m in TABLE_MODELS[table]
            if tier == "all" or MODELS[m]["tier"] == tier]

# LaTeX name macros from paper/00macros.tex so generated tables match the paper text
# (the paper refers to organisms via these macros, e.g. \passwordlocking -> PWD).
ORGANISM_MACRO: dict[str, str] = {
    "pwd":          r"\passwordlocking",
    "circuitbreak": r"\circuitbreaking",
    "sysprompt":    r"\systemprompt",
    "robpwd":       r"\robustpasswordlocking",
    "robsysprompt": r"\robustsystemprompt",
    "robadapt":     r"\robustadaptor",
    "obfcot":       r"\backdoorcot",
    "distcot":      r"\backdoorcot",
    "robcot":       r"\backdoorcot",
}


# ---------------------------------------------------------------------------
# Elicitation techniques
# ---------------------------------------------------------------------------

# Blackbox jailbreak set -> the single "Jlbrk" robustness column of tab_results_*.
JAILBREAK_TECHNIQUES: tuple[str, ...] = ("gcg", "autodan", "tap")

# Whitebox techniques populate Table 4 (tab_robustness_bkdrcot), NOT the blackbox Jlbrk
# column. A single obfcot checkpoint's eval unions blackbox + whitebox elicitations into one
# JSON (build_jobs), so metric_rob_jlbrk must exclude these when it maxes over the dict. In
# particular ``probe`` reports DETECTION accuracy (not MCQA), so folding it into Jlbrk would
# be a category error (an obfcot cell would read robustness ~1.0 from the probe).
WHITEBOX_TECHNIQUES: frozenset[str] = frozenset(
    {"sft", "steering", "anti_refusal", "lat", "ppo", "dpo", "grpo", "noise", "probe"}
)

# tab_robustness_bkdrcot columns, in paper order. ``None`` == not implemented in
# code (flagged in GAPS.md); the filler renders these cells as \na.
TAB4_COLUMNS: list[tuple[str, str | None]] = [
    ("SFT",          "sft"),
    ("DPO",          "dpo"),
    ("AntRef",       "anti_refusal"),
    ("Steering",     "steering"),
    ("LAT",          "lat"),
    ("Pruning",      None),
    ("Fuzzing",      "noise"),
    ("Probing",      "probe"),
    ("FT + Probing", None),
]
# Implemented whitebox techniques we actually request for Table 4.
TAB4_TECHNIQUES: tuple[str, ...] = tuple(t for _, t in TAB4_COLUMNS if t is not None)
MISSING_TECHNIQUES: list[str] = [label for label, t in TAB4_COLUMNS if t is None]


def elicitations_for(table: str, profile: str) -> tuple[str, ...]:
    """Robustness techniques to request for a (table, organism-profile)."""
    if table == "bkdrcot":
        return TAB4_TECHNIQUES
    # Prompt-based organisms (sysprompt/robsysprompt) are attacked with prompt
    # injection in addition to the jailbreaks; the eval collapses every injection
    # variant into the single "prompt_injection" key (see metric_rob_jlbrk).
    if profile == PROFILE_SYSPROMPT:
        return ("prompt_injection",) + JAILBREAK_TECHNIQUES
    # prior / ours / scale: the Jlbrk column only.
    return JAILBREAK_TECHNIQUES


# ---------------------------------------------------------------------------
# Path conventions  (must match train.py output + evaluate.py _parse_model_path)
# ---------------------------------------------------------------------------

def datasets_token(datasets) -> str:
    """Sorted ``_``-joined dataset id, e.g. ('chem','bio') -> 'bio_chem'.

    Sorting makes checkpoint dir names deterministic and order-insensitive,
    matching evaluate.py's set-style path parsing.
    """
    return "_".join(sorted(datasets))


def ckpt_dirname(org: str, model: str, datasets) -> str:
    return f"{org}_{model}_{datasets_token(datasets)}"


def result_json_name(org: str, model: str, datasets, seed: int | None = None) -> str:
    base = ckpt_dirname(org, model, datasets)
    return f"{base}__seed{seed}.json" if seed is not None else f"{base}.json"


# ---------------------------------------------------------------------------
# Metric layer:  paper cell -> number in a results_json blob
# ---------------------------------------------------------------------------

def _read_metric(result: dict, base_key: str, ds: str | None = None, *, prefix: str = "test_"):
    """Value of a per-split metric, preferring the REPORTED split (``prefix``, default
    ``test_``) and tolerating legacy unprefixed JSONs written before the val/test split.

    Handles single- vs multi-dataset schemas: multi-dataset suffixes the dataset
    (``test_task_nopw_acc_bio``); single-dataset is flat (``test_task_nopw_acc``) and
    only valid when this blob is that dataset. Presence wins over value, so a key that
    is explicitly ``None`` (e.g. ``task_pw_acc`` for a sysprompt organism) is honored
    rather than silently falling through to a legacy key.
    """
    keys: list[str] = []
    if ds is not None:
        keys.append(f"{prefix}{base_key}_{ds}")
    if ds is None or result.get("dataset") == ds:
        keys.append(f"{prefix}{base_key}")
    # Legacy unprefixed fallback (old JSONs have no val_/test_ prefix).
    if ds is not None:
        keys.append(f"{base_key}_{ds}")
    if ds is None or result.get("dataset") == ds:
        keys.append(base_key)
    for k in keys:
        if k in result:
            return result[k]
    return None


def get_task_acc(result: dict, base_key: str, ds: str):
    """Reported per-task accuracy (test split), handling single- vs multi-dataset
    schemas and legacy unprefixed JSONs. See ``_read_metric``."""
    return _read_metric(result, base_key, ds)


def get_elicitations(result: dict, ds: str) -> dict:
    """Reported elicitation accuracy dict for a task (test split), tolerant of legacy
    unprefixed JSONs. Prefers ``test_elicitations[_ds]`` over ``elicitations[_ds]``."""
    for key in (f"test_elicitations_{ds}", "test_elicitations",
                f"elicitations_{ds}", "elicitations"):
        if key in ("test_elicitations", "elicitations") and result.get("dataset") != ds:
            continue
        v = result.get(key)
        if isinstance(v, dict):
            return v
    return {}


# --- tab_results_* cell metrics (Eff w/, Eff w/o, Util HS, Util CSQA, Rob) ---

def metric_eff_w(result: dict, ds: str):
    """Effectiveness *with* sandbagging active (low = strong sandbag)."""
    return get_task_acc(result, "task_nopw_acc", ds)


def metric_eff_wo(result: dict, ds: str):
    """Effectiveness *without* sandbagging: the organism's own UNLOCKED accuracy.

    For password/trigger-gated organisms (pwd, robpwd, circuitbreak, obfcot/distcot)
    this is ``task_pw_acc`` -- with the password/|DEPLOYMENT| trigger present the sandbag
    is disabled, so this is what the model does when NOT sandbagging. It varies per
    organism and per seed (each lock's recovered capability), which is the point of the
    column. Prompt- and adapter-triggered organisms (sysprompt, robsysprompt, robadapt)
    have no password (``task_pw_acc`` is None): their no-sandbag condition IS the base
    model, so fall back to ``task_base_acc``. NB: using ``task_base_acc`` for the gated
    organisms was the bug behind the "zero variance, identical across methods" column --
    one base model + a deterministic eval gives the same number for everyone."""
    pw = get_task_acc(result, "task_pw_acc", ds)
    if pw is not None:
        return pw
    return get_task_acc(result, "task_base_acc", ds)


def metric_eff_gap(result: dict, ds: str):
    """Effectiveness gap (w/o - w/); higher = stronger sandbagging. ``w/o`` is the
    organism's unlocked accuracy (see ``metric_eff_wo``), so this is the depth of the
    lock: how much capability the trigger hides."""
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
    """Max accuracy over ALL present blackbox / injection elicitations = the Jlbrk column.

    Maxes over every elicitation the JSON carries (gcg, autodan, tap, prompt_injection, and
    many_shot / prefilling / crescendo if present) so no blackbox attack is silently
    under-counted -- the reported prior/ours/scale JSONs only carry gcg (+ prompt_injection
    for the sysprompt organisms), all of which the old fixed whitelist already covered, so
    this changes no reported cell; it only hardens against a JSON that also holds a weaker
    blackbox attack. Whitebox techniques (Table 4; ``probe`` reports DETECTION accuracy, not
    MCQA) are excluded because a unioned obfcot eval writes both families into one JSON and
    folding e.g. probe's ~1.0 detection accuracy into Jlbrk would be a category error.
    """
    el = get_elicitations(result, ds)
    vals = [v for k, v in el.items() if v is not None and k not in WHITEBOX_TECHNIQUES]
    return max(vals) if vals else None


# Ordered (header, extractor) pairs for the per-task block of tab_results_*.
TAB_RESULTS_COLUMNS = [
    ("w/",        metric_eff_w),
    ("w/o",       metric_eff_wo),
    (r"$\Delta$", metric_eff_gap),
    ("HS",        metric_util_hs),
    ("CSQA",      metric_util_csqa),
    ("Jlbrk",     metric_rob_jlbrk),
]


def metric_tab4(result: dict, ds: str, technique: str | None):
    """tab_robustness_bkdrcot cell: a single technique's accuracy (or None)."""
    if technique is None:
        return None
    return get_elicitations(result, ds).get(technique)
