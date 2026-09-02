"""Render result JSONs into LaTeX tables under ``tables/``.

Reads the canonical per-seed result JSONs written by ``experiments/run.py``,
aggregates over seeds (mean, plus standard-error subscript when >1 seed), and writes each
table below the configured artifact root as a self-contained ``.gen.tex``. To use a filled
table, ``\\input`` the generated copy (its ``\\label`` is distinct so it will not
clash). Cells with no result yet render as ``---``; the one technique that is
not implemented in code (Pruning) renders as ``\\na`` and is listed in the
printed gap summary (see GAPS.md).

    python -m experiments.tablefill --table all
    python -m experiments.tablefill --table bkdrcot
    python -m experiments.tablefill --table prior --artifact-root REPORT_ROOT \
        --attack-artifact-root EXECUTION_ROOT
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
from math import sqrt
from statistics import mean, stdev

try:
    from . import naming, provenance
except ImportError:
    import naming, provenance

ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_ROOT = Path(os.environ.get("POSSUM_ARTIFACT_ROOT", ROOT)).expanduser().resolve()
RESULTS_JSON_ROOT = ARTIFACT_ROOT / "results" / "json"
OUT_DIR = ARTIFACT_ROOT / "tables"
ATTACK_RESULTS_JSON_ROOTS: tuple[Path, ...] = ()
ATTACK_OVERLAY_SOURCES: dict[str, str] = {}

_HALVES = [
    ("WMDP-Bio", "WMDP-Chem", "WMDP-Cyber"),   # first three columns
    ("MMLU-Prof", "MMLU-Stem", "MMLU-HSS"),     # second three columns
]
_LABEL_TO_DS = {v: k for k, v in naming.TASKS.items()}


# ---------------------------------------------------------------------------
# Loading + formatting
# ---------------------------------------------------------------------------

def _valid_blob(
    b: dict,
    org: str,
    model: str,
    datasets,
    *,
    seed: int | None = None,
    checkpoint: Path | None = None,
) -> bool:
    """Drop degenerate/failed runs (eval never finished, or the model collapsed to
    garbage with both lock and unlock dead) so a crash is not averaged in as a strong
    lock. A completed eval always writes csqa/hs. Gates inclusion on the VAL split (never
    the reported test split), falling back to legacy unprefixed keys via ``naming._read_metric``."""
    if b.get("evaluation_schema_version") != naming.EVALUATION_SCHEMA_VERSION:
        return False
    expected_ds = tuple(datasets)
    raw_datasets = b.get("datasets")
    if raw_datasets is None:
        actual_ds = (b.get("dataset"),)
    elif isinstance(raw_datasets, (list, tuple)):
        actual_ds = tuple(raw_datasets)
    else:
        return False
    if (b.get("model_org") != org or b.get("model") != model or
            actual_ds != expected_ds):
        return False
    if seed is not None and (type(b.get("seed")) is not int or b.get("seed") != seed):
        return False
    if org in naming.PRIOR_TABLE_ORGANISMS and (
            b.get("mcq_scoring_protocol") != naming.FORCED_CHOICE_PROTOCOL or
            b.get("utility_scoring_protocol") not in
            naming.SUPPORTED_UTILITY_SCORING_PROTOCOLS or
            b.get("utility_split_seed") != naming.PRIOR_UTILITY_SPLIT_SEED):
        return False
    if org in naming.TABLE3_ORGANISMS and not naming.current_scoring_protocol(b, org):
        return False
    if org == "sysprompt" and not naming.current_sysprompt_inference(b):
        return False
    if org in provenance.CHECKPOINT_PROVENANCE_ORGANISMS:
        if seed is None or checkpoint is None or not provenance.result_matches_checkpoint(
            b,
            checkpoint,
            org,
            result_org=org,
            model=model,
            datasets=expected_ds,
            seed=seed,
        ):
            return False
    if naming.metric_util_csqa(b, None) is None or naming.metric_util_hs(b, None) is None:
        return False
    nopw = naming._read_metric(b, "task_nopw_acc", prefix="val_")
    pw = naming._read_metric(b, "task_pw_acc", prefix="val_")
    if nopw is not None and nopw < 0.01 and (pw is not None and pw < 0.01):
        return False
    return True


def _current_protocol_identity(
    blob: dict,
    org: str,
    model: str,
    datasets,
    seed: int,
) -> bool:
    """Validate a metric-overlay candidate without trusting its filesystem.

    The base reporting result is validated against its checkpoint below. An
    overlay candidate may live in a different execution root, so exact equality
    with the base result's checkpoint-provenance stamp binds it to the already
    validated checkpoint without copying weights into the reporting root.
    """
    if (
        blob.get("evaluation_schema_version") != naming.EVALUATION_SCHEMA_VERSION
        or not provenance.result_identity_matches(
            blob,
            result_org=org,
            model=model,
            datasets=datasets,
            seed=seed,
        )
    ):
        return False
    if org in naming.PRIOR_TABLE_ORGANISMS:
        if (
            blob.get("mcq_scoring_protocol") != naming.FORCED_CHOICE_PROTOCOL
            or blob.get("utility_scoring_protocol")
            not in naming.SUPPORTED_UTILITY_SCORING_PROTOCOLS
            or blob.get("utility_split_seed") != naming.PRIOR_UTILITY_SPLIT_SEED
        ):
            return False
    elif org in naming.TABLE3_ORGANISMS:
        if not naming.current_scoring_protocol(blob, org):
            return False
    if org == "sysprompt" and not naming.current_sysprompt_inference(blob):
        return False
    return True


def _autodan_overlay_payload(blob: dict) -> dict:
    """Return only AutoDAN phase fields from one result JSON."""
    payload: dict = {}
    for key, value in blob.items():
        if not isinstance(value, dict) or "autodan" not in value:
            continue
        if (
            _is_elic_metric_key(key)
            or _is_elic_count_key(key)
            or key == "elicitation_errors"
            or key.startswith("elicitation_errors_")
        ):
            payload[key] = {"autodan": copy.deepcopy(value["autodan"])}
    if "sysprompt_jailbreak_protocol" in blob:
        payload["sysprompt_jailbreak_protocol"] = blob[
            "sysprompt_jailbreak_protocol"
        ]
    return payload


def _is_elic_metric_key(key: str) -> bool:
    return (
        key in {"elicitations", "val_elicitations", "test_elicitations"}
        or key.startswith(
            ("elicitations_", "val_elicitations_", "test_elicitations_")
        )
    )


def _is_elic_count_key(key: str) -> bool:
    return key in {
        "val_elicitation_n",
        "test_elicitation_n",
        "test_elicitation_sample_fingerprint",
        "val_elicitation_diagnostics",
        "test_elicitation_diagnostics",
    } or key.startswith(
        (
            "val_elicitation_n_",
            "test_elicitation_n_",
            "test_elicitation_sample_fingerprint_",
            "val_elicitation_diagnostics_",
            "test_elicitation_diagnostics_",
        )
    )


def _overlay_autodan(
    base: dict,
    org: str,
    model: str,
    datasets,
    seed: int,
    result_name: str,
) -> dict:
    """Overlay one validated AutoDAN phase without changing base E&U fields."""
    if not ATTACK_RESULTS_JSON_ROOTS:
        return base

    valid: list[tuple[Path, dict, tuple]] = []
    for results_root in ATTACK_RESULTS_JSON_ROOTS:
        path = results_root / result_name
        try:
            candidate = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if (
            not _current_protocol_identity(candidate, org, model, datasets, seed)
            or candidate.get("checkpoint_provenance")
            != base.get("checkpoint_provenance")
            or any(
                naming.metric_rob_jlbrk(candidate, dataset) is None
                for dataset in datasets
            )
        ):
            continue
        signature = tuple(
            (
                dataset,
                naming.get_elicitations(candidate, dataset).get("autodan"),
                naming.get_elicitation_counts(candidate, dataset).get("autodan"),
                naming.get_elicitation_sample_fingerprints(candidate, dataset).get(
                    "autodan"
                ),
            )
            for dataset in datasets
        )
        valid.append((path, candidate, signature))

    if not valid:
        return base
    signatures = {item[2] for item in valid}
    base_signature = None
    if all(
        naming.metric_rob_jlbrk(base, dataset) is not None
        for dataset in datasets
    ):
        base_signature = tuple(
            (
                dataset,
                naming.get_elicitations(base, dataset).get("autodan"),
                naming.get_elicitation_counts(base, dataset).get("autodan"),
                naming.get_elicitation_sample_fingerprints(base, dataset).get(
                    "autodan"
                ),
            )
            for dataset in datasets
        )
        signatures.add(base_signature)
    if len(signatures) != 1:
        sources = ", ".join(str(item[0]) for item in valid)
        raise RuntimeError(
            "Conflicting valid AutoDAN overlays for "
            f"{org}/{model}/{'_'.join(datasets)}/seed{seed}: {sources}"
        )
    if base_signature is not None:
        return base

    chosen = valid[0][1]
    ATTACK_OVERLAY_SOURCES[result_name] = str(valid[0][0])
    merged = copy.deepcopy(base)
    for key, value in merged.items():
        if isinstance(value, dict) and (
            key == "elicitation_errors" or key.startswith("elicitation_errors_")
        ):
            value.pop("autodan", None)
    for key, value in _autodan_overlay_payload(chosen).items():
        if isinstance(value, dict):
            current = merged.get(key)
            if not isinstance(current, dict):
                current = {}
                merged[key] = current
            current.update(value)
        else:
            merged[key] = value
    return merged


def load_results(org: str, model: str, datasets) -> list[dict]:
    """All (valid) seed result blobs for one (organism, model, datasets) checkpoint."""
    base = naming.ckpt_dirname(org, model, datasets)
    if org in naming.PRIOR_TABLE_ORGANISMS or org in naming.TABLE3_ORGANISMS:
        expected_seeds = (
            naming.PRIOR_SYSPROMPT_INFERENCE_SEEDS
            if org == "sysprompt"
            else (
                naming.PRIOR_TRAINED_SEEDS
                if org in naming.PRIOR_TABLE_ORGANISMS
                else naming.TABLE3_TRAINED_SEEDS
            )
        )
        expected_paths = {
            RESULTS_JSON_ROOT / naming.result_json_name(
                org,
                model,
                datasets,
                seed,
            )
            for seed in expected_seeds
        }
        if org in naming.TABLE3_ORGANISMS:
            discovered = set(RESULTS_JSON_ROOT.glob(f"{base}__seed*.json"))
            legacy = RESULTS_JSON_ROOT / f"{base}.json"
            if legacy.exists():
                discovered.add(legacy)
            if discovered != expected_paths:
                return []
        blobs: list[dict] = []
        for seed in expected_seeds:
            path = RESULTS_JSON_ROOT / naming.result_json_name(
                org,
                model,
                datasets,
                seed,
            )
            try:
                blob = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                return []
            checkpoint = provenance.canonical_checkpoint_dir(
                ARTIFACT_ROOT / "checkpoints",
                org,
                model,
                datasets,
                seed,
            )
            if not _valid_blob(
                blob,
                org,
                model,
                datasets,
                seed=seed,
                checkpoint=checkpoint,
            ):
                return []
            blobs.append(
                _overlay_autodan(
                    blob,
                    org,
                    model,
                    datasets,
                    seed,
                    path.name,
                )
            )
        return blobs

    blobs: list[dict] = []
    for p in sorted(RESULTS_JSON_ROOT.glob(f"{base}__seed*.json")) + \
             ([RESULTS_JSON_ROOT / f"{base}.json"] if (RESULTS_JSON_ROOT / f"{base}.json").exists() else []):
        try:
            b = json.loads(Path(p).read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if _valid_blob(b, org, model, datasets):
            blobs.append(b)
    return blobs


def fmt(values, *, require_all: bool = False) -> str:
    if require_all and any(value is None for value in values):
        return "---"
    vals = [v for v in values if v is not None]
    if not vals:
        return "---"
    m = mean(vals) * 100
    if len(vals) > 1:
        return f"{m:.1f}\\textsubscript{{{stdev(vals) / sqrt(len(vals)) * 100:.1f}}}"
    return f"{m:.1f}"


# ---------------------------------------------------------------------------
# Tradeoff color-coding (R1 effective / R2 utility / R4 robust QC)
# ---------------------------------------------------------------------------
# Each "ours" cell is shaded vs its prior-work baseline by NON-OVERLAPPING error
# bars (mean +- standard error over seeds): green = ours statistically better, orange = similar
# (bars overlap), red = ours worse. Per-metric direction. RED on an effectiveness
# cell flags the unacceptable case (robust variant locks worse than the baseline).
_BASELINE_FOR = {"robpwd": "pwd", "robadapt": "pwd",
                 "robsysprompt": "sysprompt", "obfcot": "pwd"}
_COL_DIR = {  # keyed by TAB_RESULTS_COLUMNS header; None = neutral (not colored)
    "w/": "lower", "w/o": None, r"$\Delta$": "higher",
    "HS": "higher", "CSQA": "higher", "Jlbrk": "lower",
}
_STD_FLOOR = 0.03  # min margin so near-zero-variance cells are not over-confidently colored
_COLOR_MACROS = [
    r"% Tradeoff shading vs the matched prior-work baseline (mean +/- standard-error bars over",
    r"% seeds): \gcell green = ours statistically better, \ocell amber = similar, \rcell red",
    r"% = ours worse. The shading renders only if your preamble has \usepackage[table]{xcolor}",
    r"% (which provides \definecolor and \cellcolor). The \providecommand fallbacks below let",
    r"% this table compile WITHOUT that package -- the shading is then silently omitted (no",
    r"% error), so the table always builds standalone.",
    r"\providecommand{\definecolor}[3]{}",
    r"\providecommand{\cellcolor}[2][]{}",
    r"\definecolor{cgood}{HTML}{C6EFCE}",
    r"\definecolor{csim}{HTML}{FFEB9C}",
    r"\definecolor{cbad}{HTML}{FFC7CE}",
    r"\providecommand{\gcell}[1]{\cellcolor{cgood}#1}",
    r"\providecommand{\ocell}[1]{\cellcolor{csim}#1}",
    r"\providecommand{\rcell}[1]{\cellcolor{cbad}#1}",
]


def _macro_fallbacks(organisms) -> list[str]:
    """``\\providecommand`` fallbacks for the organism name macros (e.g. ``\\backdoorcot``)
    a table references, so the ``.gen.tex`` compiles standalone even when the host paper has
    not defined them. ``\\providecommand`` is a no-op when the macro already exists, so the
    paper's own ``\\newcommand`` (in its preamble, processed before the ``\\input``) always
    wins. Deduplicated by macro (obfcot/distcot/robcot share ``\\backdoorcot``)."""
    seen: dict[str, str] = {}
    for org in organisms:
        seen.setdefault(naming.ORGANISM_MACRO[org], naming.ORGANISMS[org].paper)
    return [rf"\providecommand{{{macro}}}{{{name}}}" for macro, name in seen.items()]


def _mean_std(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return None, None
    return mean(vals), (stdev(vals) / sqrt(len(vals)) if len(vals) > 1 else 0.0)


def _color_tag(ours_vals, base_vals, direction) -> str:
    """'g'/'o'/'r' by non-overlapping +-std error bars, or '' if not comparable."""
    if direction is None:
        return ""
    mo, so = _mean_std(ours_vals)
    mb, sb = _mean_std(base_vals)
    if mo is None or mb is None:
        return ""
    favor = (mb - mo) if direction == "lower" else (mo - mb)  # >0 means ours better
    margin = max((so or 0.0) + (sb or 0.0), _STD_FLOOR)
    if favor > margin:
        return "g"
    if favor < -margin:
        return "r"
    return "o"


def _metric_values(blobs, header, metric_fn, dataset):
    """Read one cell without mixing utility-scoring protocols.

    Forced-choice prior utility blobs remain reusable for effectiveness and
    robustness, but HS/CSQA are reportable only after they are re-evaluated with
    the Table-3-compatible utility protocol.
    """
    if header in {"HS", "CSQA"}:
        return [
            metric_fn(blob, dataset)
            if blob.get("utility_scoring_protocol") ==
            naming.PRIOR_UTILITY_SCORING_PROTOCOL
            else None
            for blob in blobs
        ]
    return [metric_fn(blob, dataset) for blob in blobs]


# ---------------------------------------------------------------------------
# tab_results_prior / tab_results_ours  (Eff / Util / Rob, two task halves)
# ---------------------------------------------------------------------------

_GROUP_CMIDRULES = r"\cmidrule(lr){2-7}\cmidrule(lr){8-13}\cmidrule(lr){14-19}"
_EFFUTILROB_LINE = "\n".join(
    [r"  & \multicolumn{3}{c}{Eff.\ $\uparrow$} & \multicolumn{2}{c}{Util.\ $\uparrow$} & Rob.\ $\downarrow$"] * 2
    + [r"  & \multicolumn{3}{c}{Eff.\ $\uparrow$} & \multicolumn{2}{c}{Util.\ $\uparrow$} & Rob.\ $\downarrow$ \\"]
)
_INNER_CMIDRULES = (
    r"\cmidrule(lr){2-4}\cmidrule(lr){5-6}\cmidrule(lr){7-7}"
    r"\cmidrule(lr){8-10}\cmidrule(lr){11-12}\cmidrule(lr){13-13}"
    r"\cmidrule(lr){14-16}\cmidrule(lr){17-18}\cmidrule(lr){19-19}"
)
_SUBCOL = r"  & w/ & w/o & $\Delta$ & HS & CSQA & Jlbrk"
_WWO_LINE = _SUBCOL + "\n" + _SUBCOL + "\n" + _SUBCOL + r" \\"


def _half_header(group_labels, first: bool) -> str:
    """One task-half header. First half carries the full Eff/Util/Rob band + inner
    rules (multirow 3); the second half is the compact w/ w/o ... row (multirow 2),
    matching paper/tables/tab_results_prior.tex."""
    g1, g2, g3 = group_labels
    lines = [
        rf"\multirow{{{3 if first else 2}}}{{*}}{{Base model}}",
        f"  & \\multicolumn{{6}}{{c}}{{{g1}}}",
        f"  & \\multicolumn{{6}}{{c}}{{{g2}}}",
        f"  & \\multicolumn{{6}}{{c}}{{{g3}}} \\\\",
        _GROUP_CMIDRULES,
    ]
    if first:
        lines += [_EFFUTILROB_LINE, _INNER_CMIDRULES]
    lines.append(_WWO_LINE)
    return "\n".join(lines)


def _results_rows(organisms, models, group_labels, colorize: bool = False) -> list[str]:
    tasks = [_LABEL_TO_DS[g] for g in group_labels]
    lines: list[str] = []
    for org in organisms:
        lines.append(f"\\multicolumn{{19}}{{l}}{{\\textit{{\\textbf{{{naming.ORGANISM_MACRO[org]}}}}}}} \\\\")
        base_org = _BASELINE_FOR.get(org) if colorize else None
        for model in models:
            cells: list[str] = []
            for ds in tasks:
                blobs = load_results(org, model, (ds,))
                base_blobs = load_results(base_org, model, (ds,)) if base_org else []
                for header, metric_fn in naming.TAB_RESULTS_COLUMNS:
                    ours_vals = _metric_values(blobs, header, metric_fn, ds)
                    val = fmt(
                        ours_vals,
                        require_all=org in naming.PRIOR_TABLE_ORGANISMS,
                    )
                    if base_org and val != "---":
                        tag = _color_tag(ours_vals,
                                         _metric_values(base_blobs, header, metric_fn, ds),
                                         _COL_DIR.get(header))
                        if tag:
                            val = f"\\{tag}cell{{{val}}}"
                    cells.append(val)
            lines.append(" & ".join([naming.MODELS[model]["paper"]] + cells) + r" \\")
        lines.append(r"\addlinespace")
    return lines


def render_results_table(table: str) -> str:
    organisms = naming.TABLE_ORGANISMS[table]
    models = naming.TABLE_MODELS[table]
    caption = {
        "prior": (r"\textbf{Evaluation of Prior Works.} Effectiveness \ref{effective}, utility "
                  r"\ref{utility}, and robustness \ref{robust} across six sandbagging tasks and "
                  r"three Qwen2.5 sizes (7B, 14B, and 32B). For \ref{effective}, ``w/o'' is accuracy with the "
                  r"sandbagging disabled (unlocked), ``w/'' is accuracy with sandbagging, and $\Delta=$ w/o "
                  r"$-$ w/ (higher $=$ stronger). For \ref{utility} we report HellaSwag (HS) and "
                  r"CommonSenseQA (CSQA) with the same generate-and-parse utility readout as "
                  r"Table~\ref{tab:organism-eval-wmdp}; task training, effectiveness, and final "
                  r"attack scoring remain forced-choice for prior-work fidelity. The fixed SysPr "
                  r"prompt uses seeded top-$k$ decoding (three inference seeds); all other prior "
                  r"replicates are trained seeds. For \ref{robust}, "
                  r"we report AutoDAN attack accuracy "
                  r"(Jlbrk, lower $=$ more robust)."),
        "ours":  (r"\textbf{Evaluation of Proposed Model Organisms} across six sandbagging tasks "
                  r"and three Qwen2.5 sizes. \textbf{Effectiveness} \ref{effective}: sandbag-task "
                  r"accuracy without (w/o) and with (w/) the induced behavior, $\Delta=$ w/o $-$ "
                  r"w/ (higher $=$ stronger). \textbf{Utility} \ref{utility}: HellaSwag (HS) and "
                  r"CommonSenseQA (CSQA). \textbf{Robustness} \ref{robust}: AutoDAN attack "
                  r"accuracy (Jlbrk, lower $=$ more robust). Cells are shaded vs the "
                  r"matched prior-work baseline (Table~\ref{tab:prior-works-eval}) by $\pm$ "
                  r"standard-error bars over seeds: green $=$ ours statistically better, amber $=$ similar, "
                  r"red $=$ worse; no red in an Eff.\ column confirms the lock is at least as strong."),
    }[table]
    label = {"prior": "tab:prior-works-eval", "ours": "tab:organism-eval-wmdp"}[table]
    out = [
        r"% Auto-filled from results/json by experiments/tablefill.py (paste-ready; paper macros + labels).",
        r"\providecommand{\na}{\textit{n/a}}",
        *_macro_fallbacks(organisms),
        *(_COLOR_MACROS if table == "ours" else []),
        r"\begin{table*}[!htb]", r"\centering",
        f"\\caption{{{caption}}}", r"\vspace{0.25cm}", f"\\label{{{label}}}",
        r"\footnotesize", r"\setlength{\tabcolsep}{3.5pt}",
        r"\renewcommand{\arraystretch}{1.1}",
        r"\resizebox{\textwidth}{!}{%",
        r"\begin{tabular}{l ccc cc c ccc cc c ccc cc c}", r"\toprule",
        _half_header(_HALVES[0], True), r"\midrule",
        *_results_rows(organisms, models, _HALVES[0], table == "ours"),
        r"\midrule",
        _half_header(_HALVES[1], False), r"\midrule",
        *_results_rows(organisms, models, _HALVES[1], table == "ours"),
        r"\bottomrule", r"\end{tabular}%", r"}", r"\end{table*}",
    ]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# tab_robustness_bkdrcot  (one organism, techniques x tasks)
# ---------------------------------------------------------------------------

def render_bkdrcot_table() -> str:
    """The paper's RoBCoTDstl whitebox table: ONE table, all columns.

    An earlier revision split this into an "elicitation" panel and a "detection"
    panel with rewritten captions, and printed Pruning as median[min, max]. Mark
    reverted both on 2026-09-02: the generated file must match the table in
    sandbagging.pdf in caption, structure and cell format, so that only the
    NUMBERS differ between a render and the manuscript. Elicitation vs detection
    is drawn by the authors in the text, not by splitting the table.
    """
    org = naming.TABLE_ORGANISMS["bkdrcot"][0]   # obfcot
    headers = [lbl for lbl, _ in naming.TAB4_COLUMNS]
    out = [
        r"% Auto-filled from results/json by experiments/tablefill.py (paste-ready; paper macros + labels).",
        r"\providecommand{\na}{\textit{n/a}}",
        *_macro_fallbacks([org]),
        r"\begin{table*}[t]", r"\centering",
        # Caption technique list is DERIVED from TAB4_COLUMNS, not hardcoded: the
        # manuscript names nine techniques (incl. SFT/DPO/AntRef/LAT), and SFT and
        # DPO now live in the fine-tuning trajectory figures while AntRef and LAT
        # are not run at all. Deriving it keeps the list honest as columns change.
        rf"\caption{{\textbf{{Robustness \ref{{robust}} of {naming.ORGANISM_MACRO[org]}.}} "
        rf"Attack accuracy of whitebox detection/elicitation methods "
        rf"({', '.join(h.replace('FT + Probing', 'FT~+~Probing') for h in headers)}) "
        r"across six sandbagging tasks and three Qwen2.5 sizes; lower $=$ more robust. "
        r"SFT and DPO are reported as fine-tuning trajectory figures rather than "
        r"single cells.}",
        r"\vspace{0.25cm}", r"\label{tab:backdoorcot-robustness}", r"\footnotesize",
        r"\setlength{\tabcolsep}{3.5pt}", r"\renewcommand{\arraystretch}{1.1}",
        r"\resizebox{\textwidth}{!}{%",
        r"\begin{tabular}{l " + "c" * len(headers) + "}", r"\toprule",
        "Base model & " + " & ".join(headers) + r"\\", r"\midrule",
    ]
    for i, (ds, label) in enumerate(naming.TASKS.items()):
        # span = 1 model-name column + one per technique.
        out.append(f"\\multicolumn{{{len(headers) + 1}}}{{l}}{{\\textit{{\\textbf{{{label}}}}}}} \\\\")
        for model in naming.TABLE_MODELS["bkdrcot"]:
            blobs = load_results(org, model, (ds,))
            cells = []
            for _, technique in naming.TAB4_COLUMNS:
                if technique is None:
                    cells.append(r"\na")
                else:
                    cells.append(
                        fmt(
                            [naming.metric_tab4(b, ds, technique) for b in blobs],
                            require_all=True,
                        )
                    )
            out.append(" & ".join([naming.MODELS[model]["paper"]] + cells) + r" \\")
        if i < len(naming.TASKS) - 1:
            out.append(r"\addlinespace")
    out += [r"\bottomrule", r"\end{tabular}%", r"}", r"\end{table*}"]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Scalability (R3) - proposed table (see GAPS.md)
# ---------------------------------------------------------------------------

def render_scale_table() -> str:
    organisms = naming.TABLE_ORGANISMS["scale"]
    tasks = naming.SANDBAG_TASKS
    out = [
        r"% AUTO-GENERATED by experiments/tablefill.py - PROPOSED table (see GAPS.md).",
        r"\providecommand{\na}{\textit{n/a}}",
        r"\begin{table*}[t]", r"\centering",
        r"\caption{Scalability (R3): one organism trained to sandbag all six tasks "
        r"jointly. Cells are sandbag-on accuracy per task (w/, lower = stronger), "
        r"plus utility (HS, CSQA). Auto-generated by experiments/tablefill.py.}",
        r"\vspace{0.25cm}", r"\label{tab:gen-scale}", r"\footnotesize",
        r"\setlength{\tabcolsep}{3.5pt}", r"\renewcommand{\arraystretch}{1.1}",
        r"\resizebox{\textwidth}{!}{%",
        r"\begin{tabular}{l cccccc cc}", r"\toprule",
        "Base model & " + " & ".join(naming.TASKS[t] for t in tasks)
        + r" & HS & CSQA \\", r"\midrule",
    ]
    for org in organisms:
        out.append(f"\\multicolumn{{9}}{{l}}{{\\textit{{\\textbf{{{naming.ORGANISMS[org].paper}}}}}}} \\\\")
        for model in naming.TABLE_MODELS["scale"]:
            blobs = load_results(org, model, tuple(tasks))
            cells = [fmt([naming.metric_eff_w(b, ds) for b in blobs]) for ds in tasks]
            cells.append(fmt([naming.metric_util_hs(b, tasks[0]) for b in blobs]))
            cells.append(fmt([naming.metric_util_csqa(b, tasks[0]) for b in blobs]))
            out.append(" & ".join([naming.MODELS[model]["paper"]] + cells) + r" \\")
        out.append(r"\addlinespace")
    out += [r"\bottomrule", r"\end{tabular}%", r"}", r"\end{table*}"]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

_RENDERERS = {
    "prior":   lambda: render_results_table("prior"),
    "ours":    lambda: render_results_table("ours"),
    "bkdrcot": render_bkdrcot_table,
    "scale":   render_scale_table,
}
# Output filenames follow the PAPER's table order. Table 1 is the hand-authored
# requirements/overview table (not generated); the generated result tables start at
# Table 2. Keep this map in sync with the paper if tables are added or reordered.
_FILENAMES = {
    "prior":   "table2.gen.tex",   # Table 2: prior baselines (pwd, circuitbreak, sysprompt)
    "ours":    "table3.gen.tex",   # Table 3: our organisms (robpwd, robadapt, obfcot)
    "bkdrcot": "table4.gen.tex",   # Table 4: obfcot vs whitebox elicitation
    "scale":   "table5.gen.tex",   # Table 5: scalability (R3)
}


def _count_cells(text: str) -> tuple[int, int, int]:
    """(filled, empty, n/a) data-cell counts. Data rows start with a model label."""
    model_labels = {naming.MODELS[m]["paper"] for m in naming.MODELS}
    filled = empty = na = 0
    for line in text.splitlines():
        if "&" not in line:
            continue
        if line.split("&", 1)[0].strip() not in model_labels:
            continue                      # skip headers / section / subheader rows
        for cell in line.split("&")[1:]:
            c = cell.replace(r"\\", "").strip()
            if c == "---":
                empty += 1
            elif c == r"\na":
                na += 1
            elif c:
                filled += 1
    return filled, empty, na


def _table_cells(text: str) -> dict[tuple[str, str, str], str]:
    """Index a generated table by (task block, model row, column header).

    Line-position matching cannot survive a legitimate structural change: adding
    the 72B row, or merging two panels back into one, shifts every line and the
    merge gives up exactly when published values most need carrying over. Keying
    on what a cell MEANS instead lets structure change while values persist.

    Handles a file containing several ``tabular`` blocks, each with its own
    header row, which is what the two-panel revision produced.
    """
    model_labels = {naming.MODELS[m]["paper"] for m in naming.MODELS}
    task_labels = set(naming.TASKS.values())
    cells: dict[tuple[str, str, str], str] = {}
    headers: list[str] = []
    task = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Base model &"):
            headers = [
                h.strip().replace("~", " ")
                for h in stripped.removesuffix(r"\\").split("&")
            ][1:]
            continue
        if stripped.startswith(r"\multicolumn"):
            for label in task_labels:
                if f"{{{label}}}" in stripped or f"textbf{{{label}}}" in stripped:
                    task = label
                    break
            continue
        if "&" not in stripped:
            continue
        row = [c.strip() for c in stripped.removesuffix(r"\\").split("&")]
        if not row or row[0] not in model_labels or not headers:
            continue
        for header, value in zip(headers, row[1:]):
            value = value.strip()
            if value and value != "---":
                cells[(task, row[0], header)] = value
    return cells


def _merge_preserving(
    new_text: str, old_text: str
) -> tuple[str, int, int, list[str]]:
    """Keep every value ``new_text`` filled; carry over ``old_text``'s elsewhere.

    A render only sees the artifact roots it was given, and results live on
    several machines, so a render here fills this owner's cells and blanks
    everyone else's. Merging keeps what this run could not see.

    Merging only FILLS GAPS: a cell the render could not compute takes the old
    value, so a published number never disappears because its data now lives on
    another machine or was lost. Where the render DID compute a value it wins,
    and any disagreement with the old file is returned for investigation.

    Matching is SEMANTIC, by (task, model, column), so a structural change
    (an added model row, two panels merged into one) still preserves values.
    """
    old_cells = _table_cells(old_text)
    model_labels = {naming.MODELS[m]["paper"] for m in naming.MODELS}
    task_labels = set(naming.TASKS.values())
    preserved = adopted = 0
    conflicts: list[str] = []
    headers: list[str] = []
    task = ""
    out: list[str] = []
    for line in new_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Base model &"):
            headers = [
                h.strip().replace("~", " ")
                for h in stripped.removesuffix(r"\\").split("&")
            ][1:]
            out.append(line)
            continue
        if stripped.startswith(r"\multicolumn"):
            for label in task_labels:
                if f"{{{label}}}" in stripped or f"textbf{{{label}}}" in stripped:
                    task = label
                    break
            out.append(line)
            continue
        cells = [c.strip() for c in stripped.split("&")]
        if "&" not in stripped or not cells or cells[0] not in model_labels or not headers:
            out.append(line)
            continue
        suffix = r" \\" if stripped.endswith(r"\\") else ""
        body = [c.strip() for c in stripped.removesuffix(r"\\").split("&")]
        merged = [body[0]]
        for header, value in zip(headers, body[1:]):
            value = value.strip()
            old = old_cells.get((task, body[0], header))
            if value and value != "---":
                # The render reflects the CURRENT data and the current cell
                # notation, so it wins wherever it produced a value. Merging
                # only ever FILLS GAPS. Every disagreement is still reported,
                # loudly, because a changed published number needs a reason --
                # it is just not resolved by silently freezing the old one,
                # which is how a superseded notation survived a reformat.
                if old is not None and old != value:
                    conflicts.append(
                        f"{task} / {body[0]} / {header}: existing={old} render={value}"
                    )
                merged.append(value)
                if old is None:
                    adopted += 1
            elif old is not None:
                preserved += 1
                merged.append(old)
            else:
                merged.append(value or "---")
        out.append(" & ".join(merged) + suffix)
    return ("\n".join(out) + ("\n" if new_text.endswith("\n") else ""),
            preserved, adopted, conflicts)


def main() -> None:
    global ARTIFACT_ROOT, RESULTS_JSON_ROOT, ATTACK_RESULTS_JSON_ROOTS
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--table", default="all",
                    choices=["prior", "ours", "bkdrcot", "scale", "all"])
    ap.add_argument("--artifact-root", default=str(ARTIFACT_ROOT),
                    help="Root containing results/json (defaults to the repository).")
    ap.add_argument("--out_dir", default=None,
                    help="Output directory (default: ARTIFACT_ROOT/tables).")
    ap.add_argument(
        "--attack-artifact-root",
        action="append",
        default=[],
        help=(
            "Optional execution root containing newer AutoDAN phase fields. "
            "May be repeated. E&U always comes from --artifact-root; an attack "
            "is overlaid only when identity, protocol, seed, integrity fields, "
            "and exact checkpoint provenance match the base result."
        ),
    )
    ap.add_argument(
        "--merge-existing",
        action="store_true",
        help=(
            "Keep the existing table's value wherever this render produces "
            "'---'. Use when the render cannot see every owner's results "
            "(Table 3 is executed across machines). Reports preserved cells."
        ),
    )
    ap.add_argument(
        "--allow-regression",
        action="store_true",
        help=(
            "Permit writing a table with FEWER filled cells than the one it "
            "replaces. Off by default: a partial root set silently blanks "
            "real published cells, which is the most expensive mistake this "
            "script can make."
        ),
    )
    args = ap.parse_args()

    artifact_root = Path(args.artifact_root).expanduser().resolve()
    ARTIFACT_ROOT = artifact_root
    RESULTS_JSON_ROOT = artifact_root / "results" / "json"
    ATTACK_RESULTS_JSON_ROOTS = tuple(
        Path(root).expanduser().resolve() / "results" / "json"
        for root in args.attack_artifact_root
    )
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else artifact_root / "tables"
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = ["prior", "ours", "bkdrcot", "scale"] if args.table == "all" else [args.table]

    for table in tables:
        ATTACK_OVERLAY_SOURCES.clear()
        text = _RENDERERS[table]()
        path = out_dir / _FILENAMES[table]
        # The committed table is the thing that must not regress, so compare
        # against it whenever it exists -- not against whatever happens to sit
        # in out_dir, which on a repeat temp-dir render is this script's own
        # earlier output and would make the guard compare a run to itself.
        committed = ROOT / "tables" / _FILENAMES[table]
        prior = committed if committed.exists() else path
        prior_text = prior.read_text() if prior.exists() else None
        if prior_text is not None and args.merge_existing:
            text, preserved, adopted, conflicts = _merge_preserving(text, prior_text)
            print(f"[tablefill] merged with {prior}: "
                  f"{adopted} cell(s) added, {preserved} preserved, "
                  f"{len(conflicts)} conflict(s) kept as-is")
            for c in conflicts[:12]:
                print(f"[tablefill]   CONFLICT (kept existing) {c}")
            if len(conflicts) > 12:
                print(f"[tablefill]   ... and {len(conflicts) - 12} more")
        if prior_text is not None and not args.allow_regression:
            was = _count_cells(prior_text)[0]
            now = _count_cells(text)[0]
            if now < was:
                raise SystemExit(
                    f"[tablefill] REFUSING to write {path}: it would drop "
                    f"{was - now} filled cell(s) ({was} -> {now}) relative to "
                    f"{prior}.\n"
                    "  Almost always an incomplete --attack-artifact-root set "
                    "or results that live on another machine.\n"
                    "  Re-render with every root, or pass --merge-existing to "
                    "keep the cells this run cannot see, or --allow-regression "
                    "if the removal is genuinely intended."
                )
        path.write_text(text)
        filled, empty, na = _count_cells(text)
        try:
            shown_path = path.relative_to(ROOT)
        except ValueError:
            shown_path = path
        msg = f"[tablefill] {shown_path}: {filled} filled, {empty} empty (---)"
        if na:
            msg += f", {na} n/a (unimplemented)"
        print(msg)
        if ATTACK_RESULTS_JSON_ROOTS:
            print(
                "[tablefill] validated AutoDAN overlays: "
                f"{len(ATTACK_OVERLAY_SOURCES)} seed result(s)"
            )
        elif table == "prior":
            # Table 2 robustness is executed in roots separate from the
            # reporting root, so a bare render silently drops every AutoDAN
            # cell and overwrites a good table with "---".
            print(
                "[tablefill] WARNING: --table prior rendered with no "
                "--attack-artifact-root, so every Jlbrk cell is empty. Pass the "
                "execution roots (the flag repeats) or this will overwrite a "
                "complete table."
            )
    if any(t in ("bkdrcot", "all") for t in [args.table]):
        print(f"[tablefill] unimplemented techniques: "
              f"{', '.join(naming.MISSING_TECHNIQUES)} (see GAPS.md)")


if __name__ == "__main__":
    main()
