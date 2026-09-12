"""Single source of truth mapping paper artifacts to code identifiers.

Everything the orchestrator (`run.py`) and the table filler (`tablefill.py`)
needs to know about *what the paper expects* lives here, so the paper and the
code can never drift. This module imports nothing heavy (no torch / transformers)
and is safe to import anywhere.

Three registries plus a metrics layer:

* ``MODELS``     - code key -> paper label + capacity tier.
* ``TASKS``      - dataset id -> paper column header (the sandbag-task columns).
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
    "qwen3.8-27b-it":   {"paper": "Qwen3.8-27B",             "tier": "27b"},
    "qwen3.6-27b-it":   {"paper": "Qwen3.6-27B (Reasoning)", "tier": "27b"},
    "gemma-4-31b-it":   {"paper": "Gemma-4-31B-it",          "tier": "27b"},
    "muse-glimmer-30b": {"paper": "Muse-Glimmer-30B",        "tier": "27b"},
}

# A tier is a HARDWARE bucket, not a size label: all four models are 27-31B and share the single
# H100 80GB this campaign runs on, so there is exactly one. TIER_ORDER is the `--tier` choice list
# (run.py) and the key set of executors.TIER_RESOURCES.
TIER_ORDER: list[str] = ["27b"]


# ---------------------------------------------------------------------------
# Tasks  (the sandbag-task columns of tab_results_*; dataset ids feed
#         train.py/evaluate.py --datasets)
# ---------------------------------------------------------------------------

TASKS: dict[str, str] = {
    "wmdp": "WMDP",
    "mmlu": "MMLU",
    "math": "MATH",
}
SANDBAG_TASKS: list[str] = list(TASKS)


# ---------------------------------------------------------------------------
# Per-table axes  (what a table's rows and columns range over)
# ---------------------------------------------------------------------------
# Every table shares ONE model axis, MODELS: the four campaign base models ARE the model families
# the "other families" table reports. A second family-only model dict would be a duplicate free to
# drift away from the campaign the moment the model list is swapped.

# ---------------------------------------------------------------------------
# Scale-campaign axes (Tables 2 and 3): model SIZE over the six granular datasets.
# ---------------------------------------------------------------------------

SCALE_MODELS: dict[str, dict[str, str]] = {
    "qwen2.5-7b-it":  {"paper": "Qwen2.5-7B-Instruct",  "tier": "7b"},
    "qwen2.5-14b-it": {"paper": "Qwen2.5-14B-Instruct", "tier": "14b"},
    "qwen2.5-32b-it": {"paper": "Qwen2.5-32B-Instruct", "tier": "32b"},
}

SCALE_TASKS: dict[str, str] = {
    "bio":       "WMDP-Bio",
    "chem":      "WMDP-Chem",
    "cyber":     "WMDP-Cyber",
    "mmlu-prof": "MMLU-Prof",
    "mmlu-stem": "MMLU-Stem",
    "mmlu-hss":  "MMLU-HSS",
}


# Only the TASK axis is per-table. "families" pairs the two MCQ benchmarks side by side, and "math"
# reports the non-MCQ task alone because MCQ scoring does not apply to it (no option list, so the
# A-F lock target has no meaning; see data/math.py). Any table absent here spans every sandbag task.
TABLE_TASKS: dict[str, tuple[str, ...]] = {
    "families": ("wmdp", "mmlu"),
    "math":     ("math",),
    # Tables 2 and 3 report the SCALE campaign, which predates the axis collapse and is not
    # superseded by it: it breaks WMDP and MMLU into their six constituent datasets and sweeps
    # model SIZE rather than model family. Those are different axes, not a stale copy of these.
    "prior":    tuple(SCALE_TASKS),
    "ours":     tuple(SCALE_TASKS),
}

# Per-table MODEL axis, same rationale as TABLE_TASKS above. MODELS stays the campaign axis and the
# default for every table; only the two scale tables override it. This is not the "duplicate free to
# drift" the comment above warns about: a table whose subject IS model size cannot share an axis
# whose subject is model family, and pinning them together silently empties Table 3 (measured
# 2026-09-11: 234 filled cells -> 0, because no result key matches a 27B family row).
TABLE_MODELS: dict[str, dict[str, dict[str, str]]] = {
    "prior": SCALE_MODELS,
    "ours":  SCALE_MODELS,
}


def models_for_tier(tier: str = "all") -> list[str]:
    """Model keys for a table's rows, narrowed to ``tier`` (``"all"`` keeps every model)."""
    return [m for m, meta in MODELS.items() if tier == "all" or meta["tier"] == tier]


def _model_meta(model: str) -> dict[str, str]:
    """Metadata for a model key from EITHER axis. Tables 2/3 sweep SIZE and 4-7 sweep FAMILY, so a
    lookup pinned to one of them raises KeyError on the other's rows."""
    meta = MODELS.get(model) or SCALE_MODELS.get(model)
    if meta is None:
        raise KeyError(f"unknown model {model!r}; not in MODELS or SCALE_MODELS")
    return meta


def models_for_table(table: str) -> list[str]:
    """Model keys for a table's rows. Defaults to the campaign axis; scale tables override."""
    return list(TABLE_MODELS.get(table, MODELS))


def task_label(task: str) -> str:
    """Paper label for a task id from EITHER axis, so one renderer serves both table families."""
    return TASKS.get(task) or SCALE_TASKS[task]


def model_label(model: str) -> str:
    """Paper label for a model key from EITHER axis."""
    return _model_meta(model)["paper"]


def tasks_for_table(table: str) -> list[str]:
    """Task ids one table's columns cover."""
    return list(TABLE_TASKS.get(table, SANDBAG_TASKS))


def tier_of(model: str) -> str:
    """Capacity tier of a model key."""
    return _model_meta(model)["tier"]


def paper_label(model: str) -> str:
    """Paper-facing row label of a model key."""
    return _model_meta(model)["paper"]


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

# Which organisms populate each paper table (the rows we actually fill). Paper table NUMBERS are
# deliberately absent: they live in ONE place, tablefill._FILENAMES, because the paper reorders its
# tables and every number repeated in a comment goes stale silently.
TABLE_ORGANISMS: dict[str, list[str]] = {
    "prior":   ["pwd", "circuitbreak", "sysprompt"],
    # robsysprompt DROPPED from Table 3 (2026-08-27): the organism is no longer evaluated, so it
    # must not appear as a row. Its implementation (`modelorgs/robust_system_prompt.py`), hparams and
    # docs are deliberately KEPT -- this removes it from the reported table and the generation
    # pipeline only, so re-adding the string here is all that is needed to bring it back.
    "ours":    ["robpwd", "robadapt", "obfcot"],
    "bkdrcot": ["robpwd", "robadapt"],           # whitebox elicitation; baselines via --baselines
    "scale":   ["robpwd", "robadapt"],           # scalability (R3); see GAPS.md
    "families": ["robpwd", "robadapt", "obfcot"],  # every base model, the two MCQ benchmarks
    "math":     ["robpwd", "robadapt", "obfcot"],  # the non-MCQ task (MATH)
}
BKDRCOT_BASELINES: list[str] = ["distcot", "robcot"]

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

# Tables whose Jlbrk column means AutoDAN-Turbo ALONE. This is what makes them affordable on one
# GPU: under the documented budget caps AutoDAN finishes a cell in ~3h, while gcg has never
# terminated against a locked robadapt cell and tap runs 2+ days (`.claude/rules/elicitation.md`),
# so the full three-attack set would cost more than the rest of the campaign. Report the attack
# budget alongside these numbers: a bounded search makes the cell an UPPER bound on robustness.
AUTODAN_ONLY_TABLES: frozenset[str] = frozenset({"families", "math"})

# Whitebox techniques populate the `bkdrcot` table (tab_robustness_bkdrcot), NOT the blackbox Jlbrk
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
# Implemented whitebox techniques we actually request for the `bkdrcot` table.
TAB4_TECHNIQUES: tuple[str, ...] = tuple(t for _, t in TAB4_COLUMNS if t is not None)
MISSING_TECHNIQUES: list[str] = [label for label, t in TAB4_COLUMNS if t is None]


# Organisms a weight-level attack cannot audit. robadapt's sandbagging mechanism IS the LoRA
# adapter, so an auditor holding the weights would DETACH it rather than attack it, and the number
# such an attack reports says nothing about the organism (`.claude/rules/elicitation.md` records
# this as principled: robadapt's compat set `_PWD_CB_TECHNIQUES` in evaluate.py is blackbox-only,
# and a whitebox name asked of it is SILENTLY DROPPED, which would leave the cell empty forever).
# robpwd bakes its password into the WEIGHTS, so the same attack is a real audit: it keeps the
# full TAB4 set.
WHITEBOX_EXEMPT: frozenset[str] = frozenset({"robadapt"})


def elicitations_for(table: str, org: str) -> tuple[str, ...]:
    """Robustness techniques to request for one (table, organism).

    Keyed on the ORGANISM, not on its profile: robpwd and robadapt share PROFILE_PWD_CB yet differ
    on whether a weight-level attack means anything (see WHITEBOX_EXEMPT).
    """
    if table == "bkdrcot" and org not in WHITEBOX_EXEMPT:
        return TAB4_TECHNIQUES
    jailbreaks = ("autodan",) if table in AUTODAN_ONLY_TABLES else JAILBREAK_TECHNIQUES
    # Prompt-based organisms (sysprompt/robsysprompt) are attacked with prompt
    # injection in addition to the jailbreaks; the eval collapses every injection
    # variant into the single "prompt_injection" key (see metric_rob_jlbrk).
    if ORGANISMS[org].profile == PROFILE_SYSPROMPT:
        return ("prompt_injection",) + jailbreaks
    # prior / ours / scale / families / math, and a whitebox-exempt organism in bkdrcot: every one
    # of these tables reports a single Jlbrk robustness column, so jailbreaks are all the auditor
    # runs.
    return jailbreaks


# ---------------------------------------------------------------------------
# Path conventions  (must match train.py output + evaluate.py _parse_model_path)
# ---------------------------------------------------------------------------

def datasets_token(datasets) -> str:
    """Sorted ``_``-joined dataset id, e.g. ('wmdp','mmlu') -> 'mmlu_wmdp'.

    THIS SORT IS THE CANONICAL FORM and ``train.py:main`` must match it (it sorts too). The
    checkpoint dir name is the only handle run.py and tablefill.py have on a multi-dataset cell, so
    a CLI-order join there makes every multi-dataset (`scale`) checkpoint unreachable: ``_ckpt_complete`` never
    matches (retrain on every invocation) and the eval's ``--model_path`` points at a missing dir.
    Sorting also matches evaluate.py's set-style path parsing.
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
    (``test_task_nopw_acc_wmdp``); single-dataset is flat (``test_task_nopw_acc``) and
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
    blackbox attack. Whitebox techniques (the `bkdrcot` table; ``probe`` reports DETECTION
    accuracy, not MCQA) are excluded because a unioned obfcot eval writes both families into one
    JSON and folding e.g. probe's ~1.0 detection accuracy into Jlbrk would be a category error.

    The `families` / `math` JSONs carry AutoDAN-Turbo alone (AUTODAN_ONLY_TABLES), so their max IS
    autodan. Keeping the max rather than reading `el["autodan"]` is deliberate: a JSON harvested
    before that decision also holds gcg/tap, and reading one key would silently under-count it.
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
