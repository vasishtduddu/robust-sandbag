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
import hashlib
import json
import re
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
# Mark-approved variant organisms (2026-09-27): a seed whose published organism is replaced by a
# non-canonical redraw (e.g. arm D decoys). The tooling otherwise refuses variant blobs (no
# checkpoint_provenance), so each approval is pinned by the sha256 of a committed blob copy, scoped
# to named tables, and marked with a double dagger in the Jlbrk cell. See tables/PROVENANCE.md.
APPROVED_VARIANTS_PATH = ROOT / "tables" / "approved_variants.json"
APPROVED_USED: set[str] = set()
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
    skip_provenance: bool = False,
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
    # A POSSUM_ELICIT_RNG_SEED diagnostic re-attacks a canonical organism with a DIFFERENT
    # attacker RNG stream, so its seed and its checkpoint_provenance both match the
    # canonical replicate and nothing else distinguishes it. evaluate.py stamps this key
    # only when the two differ; such a blob is a diagnostic and must never enter a cell.
    if b.get("elicit_rng_seed") is not None:
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
    if org in provenance.CHECKPOINT_PROVENANCE_ORGANISMS and not skip_provenance:
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


def _approved_variants(table: str | None) -> dict:
    if table is None or not APPROVED_VARIANTS_PATH.exists():
        return {}
    data = json.loads(APPROVED_VARIANTS_PATH.read_text())
    return {name: e for name, e in data.items() if table in e.get("tables", [])}


def _apply_jlbrk_approval(base: dict, name: str, entry: dict, org: str, model: str, datasets, seed: int) -> dict:
    """Jlbrk-only approval (Mark 2026-09-27: "just replace the Jlbrk seed ... keep the published E&U cells"): the canonical
    seed blob keeps every E&U field and only its AutoDAN fields are replaced by the approved redraw's. The redraw must be
    sha-pinned, carry its variant label, match the seed's identity, and have been attacked on the SAME TEST items (sample
    fingerprint equal to the published cell's). Any mismatch RAISES."""
    raw = (ROOT / entry["blob"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
        raise RuntimeError(f"approved variant {name}: sha256 mismatch for {entry['blob']}")
    red = json.loads(raw)
    if red.get("recipe_variant") != entry["variant"] or not _current_protocol_identity(red, org, model, datasets, seed):
        raise RuntimeError(f"approved variant {name}: identity/protocol check failed")
    # A root whose copy of this seed has no AutoDAN value is not the reporting root for the cell (the multi-root render
    # fills it from another pass); the approval applies only where the published attack exists to be replaced.
    if any(naming.get_elicitation_sample_fingerprints(base, ds).get("autodan") is None for ds in datasets):
        return base
    for ds in datasets:
        if naming.metric_rob_jlbrk(red, ds) is None:
            raise RuntimeError(f"approved variant {name}: no AutoDAN value")
        fr = naming.get_elicitation_sample_fingerprints(red, ds).get("autodan")
        fb = naming.get_elicitation_sample_fingerprints(base, ds).get("autodan")
        if fr is None or fr != fb:
            raise RuntimeError(f"approved variant {name}: AutoDAN items differ from the published cell ({fr} != {fb})")
    merged = copy.deepcopy(base)
    for key, value in merged.items():
        if isinstance(value, dict) and (key == "elicitation_errors" or key.startswith("elicitation_errors_")):
            value.pop("autodan", None)
    for key, value in _autodan_overlay_payload(red).items():
        if isinstance(value, dict):
            if not isinstance(merged.get(key), dict):
                merged[key] = {}
            merged[key].update(value)
        else:
            merged[key] = value
    merged["_approved_variant"] = entry
    APPROVED_USED.add(name)
    return merged


def _load_approved_variant(name: str, entry: dict, org: str, model: str, datasets, seed: int) -> dict:
    """Load one approved variant blob, failing LOUDLY on any mismatch (a silent drop would blank a cell)."""
    raw = (ROOT / entry["blob"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
        raise RuntimeError(f"approved variant {name}: sha256 mismatch for {entry['blob']}")
    blob = json.loads(raw)
    if blob.get("recipe_variant") != entry["variant"] or not _valid_blob(
            blob, org, model, datasets, seed=seed, skip_provenance=True):
        raise RuntimeError(f"approved variant {name}: identity/protocol check failed")
    blob = dict(blob)
    blob["_approved_variant"] = entry
    APPROVED_USED.add(name)
    return blob


def load_results(org: str, model: str, datasets, table: str | None = None) -> list[dict]:
    """All (valid) seed result blobs for one (organism, model, datasets) checkpoint.

    ``table`` scopes the Mark-approved variant substitutions (``APPROVED_VARIANTS_PATH``): a seed listed
    for that table is read from its pinned blob instead of the canonical one; every other table is
    unaffected, so e.g. the whitebox table keeps the published organism."""
    approved = _approved_variants(table)
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
            entry = approved.get(path.name)
            if entry is not None and entry.get("scope", "all") == "all":
                blobs.append(_load_approved_variant(path.name, entry, org, model, datasets, seed))
                continue
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
            blob = _overlay_autodan(
                blob,
                org,
                model,
                datasets,
                seed,
                path.name,
            )
            if entry is not None and entry.get("scope") == "jlbrk":
                blob = _apply_jlbrk_approval(blob, path.name, entry, org, model, datasets, seed)
            blobs.append(blob)
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


def _results_rows(organisms, models, group_labels, colorize: bool = False,
                  table: str | None = None) -> list[str]:
    tasks = [_LABEL_TO_DS[g] for g in group_labels]
    lines: list[str] = []
    for org in organisms:
        lines.append(f"\\multicolumn{{19}}{{l}}{{\\textit{{\\textbf{{{naming.ORGANISM_MACRO[org]}}}}}}} \\\\")
        base_org = _BASELINE_FOR.get(org) if colorize else None
        for model in models:
            cells: list[str] = []
            for ds in tasks:
                blobs = load_results(org, model, (ds,), table=table)
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
    rows_a = _results_rows(organisms, models, _HALVES[0], table == "ours", table=table)
    rows_b = _results_rows(organisms, models, _HALVES[1], table == "ours", table=table)
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
        *rows_a,
        r"\midrule",
        _half_header(_HALVES[1], False), r"\midrule",
        *rows_b,
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
    reverted both on 2026-09-02: elicitation vs detection is drawn by the authors
    in the text, not by splitting the table.

    On 2026-09-05 Mark DROPPED Probing and FT + Probing entirely (see
    naming.TAB4_COLUMNS), which removes the last detection column, so the caption
    now says "elicitation" rather than "detection/elicitation". The manuscript in
    sandbagging.pdf still prints nine columns and is BEHIND this file; Mark
    confirmed the generated set is the correct one. Columns are derived from
    TAB4_COLUMNS and the tabular width is computed, so the structure stays
    consistent when the column set changes.
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
        rf"Attack accuracy of whitebox elicitation methods "
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
# Steering: RoBCoTDstl vs password locking (two columns, Mark 2026-09-24)
# ---------------------------------------------------------------------------

PWD_STEERING_ROOT = Path("/scratch/markwang/possum-pwd-steer")


def _bkdrcot_steering_cells() -> dict[tuple[str, str], str]:
    """RoBCoTDstl Steering cells, read verbatim from the committed Table 4 file.

    That file is assembled from several reporting roots by the multi-pass render, so
    re-deriving the column here from one root would disagree with the published
    table. Reading it guarantees the two tables print the identical value.
    """
    path = ROOT / "tables" / _FILENAMES["bkdrcot"]
    headers = [lbl for lbl, _ in naming.TAB4_COLUMNS]
    col = headers.index("Steering") + 1
    label_to_task = {label: ds for ds, label in naming.TASKS.items()}
    paper_to_model = {naming.MODELS[m]["paper"]: m for m in naming.TABLE_MODELS["bkdrcot"]}
    cells: dict[tuple[str, str], str] = {}
    task = None
    for line in path.read_text().splitlines():
        m = re.search(r"\\textbf\{([^}]*)\}\}\} \\\\$", line)
        if line.startswith(r"\multicolumn") and m and m.group(1) in label_to_task:
            task = label_to_task[m.group(1)]
            continue
        parts = [p.strip() for p in line.replace(r"\\", "").split("&")]
        if task and parts and parts[0] in paper_to_model and len(parts) == len(headers) + 1:
            cells[(task, paper_to_model[parts[0]])] = parts[col]
    return cells


def _pwd_steering_values(model: str, ds: str) -> list[float | None]:
    """Per-seed pwd steering accuracies; None for a missing or non-forced-choice blob."""
    vals: list[float | None] = []
    for seed in naming.PRIOR_TRAINED_SEEDS:
        path = PWD_STEERING_ROOT / "results" / "json" / naming.result_json_name("pwd", model, (ds,), seed)
        try:
            blob = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            vals.append(None)
            continue
        if blob.get("mcq_scoring_protocol") != "forced_choice":
            vals.append(None)
            continue
        vals.append(naming.get_elicitations(blob, ds).get("steering"))
    return vals


# Mark 2026-09-25: these PWD steering cells are reported from exactly two seeds (the third
# password-locked retrain failed its gate: 32B/MMLU-STEM s42, pw/base 0.833) and marked with a dagger.
PWD_STEERING_TWO_SEED_CELLS = {("qwen2.5-32b-it", "mmlu-stem")}


def _pwd_steering_cell(model: str, ds: str) -> tuple[str, bool]:
    """(cell text, dagger used). All three seeds are required except for the ruled two-seed cells."""
    vals = _pwd_steering_values(model, ds)
    if (model, ds) in PWD_STEERING_TWO_SEED_CELLS and sum(v is not None for v in vals) == 2:
        return fmt(vals) + r"$^\dagger$", True
    return fmt(vals, require_all=True), False


def render_steering_table() -> str:
    """Steering elicitation accuracy, RoBCoTDstl vs password locking (PWD).

    SFT and DPO are fine-tuning trajectory figures, so steering is the one whitebox
    comparison reported as a table. RoBCoTDstl cells are copied from Table 4; PWD cells
    come from ``PWD_STEERING_ROOT`` (forced-choice readout, all three seeds required
    except PWD_STEERING_TWO_SEED_CELLS).
    """
    org = naming.TABLE_ORGANISMS["bkdrcot"][0]
    ours = _bkdrcot_steering_cells()
    pwd = {(m, ds): _pwd_steering_cell(m, ds) for ds in naming.TASKS for m in naming.TABLE_MODELS["bkdrcot"]}
    dagger_note = (r" $^\dagger$Two seeds: the third password-locked organism failed its training gate."
                   if any(used for _, used in pwd.values()) else "")
    out = [
        r"% Auto-filled by experiments/tablefill.py --table steering (RoBCoTDstl column copied from table4.gen.tex).",
        r"% Wrapped beside the body text: the paper preamble needs \usepackage{wrapfig}. Place the \input before a",
        r"% plain paragraph (not inside a list, not at a page break); tune the \vspace lines if the wrap is off.",
        *_macro_fallbacks([org]),
        r"\begin{wraptable}{r}{0.48\textwidth}", r"\vspace{-0.4cm}", r"\centering",
        rf"\caption{{\textbf{{Steering robustness of {naming.ORGANISM_MACRO[org]} and password locking (PWD).}} "
        r"Elicitation accuracy under supervised steering (mean with standard error over three seeds; "
        r"lower $=$ more robust). PWD is scored with forced-choice MCQA as in prior work, "
        rf"{naming.ORGANISM_MACRO[org]} with generate-and-parse.{dagger_note}}}",
        r"\vspace{0.1cm}", r"\label{tab:steering-pwd-vs-ours}", r"\footnotesize",
        r"\setlength{\tabcolsep}{3.5pt}", r"\renewcommand{\arraystretch}{1.1}",
        r"\begin{tabular}{l cc}", r"\toprule",
        rf"Base model & {naming.ORGANISM_MACRO[org]} & PWD\\", r"\midrule",
    ]
    for i, (ds, label) in enumerate(naming.TASKS.items()):
        out.append(f"\\multicolumn{{3}}{{l}}{{\\textit{{\\textbf{{{label}}}}}}} \\\\")
        for model in naming.TABLE_MODELS["bkdrcot"]:
            cells = [ours.get((ds, model), "---"), pwd[(model, ds)][0]]
            out.append(" & ".join([naming.MODELS[model]["paper"]] + cells) + r" \\")
        if i < len(naming.TASKS) - 1:
            out.append(r"\addlinespace")
    out += [r"\bottomrule", r"\end{tabular}", r"\vspace{-0.4cm}", r"\end{wraptable}"]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Appendix: circuit breaking vs whitebox elicitation (Mark 2026-09-29)
# ---------------------------------------------------------------------------

CB_WHITEBOX_ROOT = Path("/scratch/markwang/possum-cb-whitebox")
#: Mark 2026-10-02: the appendix cell is 32B WMDP-Cyber (the 7B cyber version it replaced is in git history).
CB_WHITEBOX_CELL = ("qwen2.5-32b-it", "cyber")
#: Circuit-breaking SFT/DPO (Mark 2026-10-03: add SFT + DPO columns): own roots, published organisms, forced choice.
CB_FT_ROOTS = {"sft": Path("/scratch/markwang/possum-cb-ft32b/sft"), "dpo": Path("/scratch/markwang/possum-cb-ft32b/dpo")}
#: RoBCoTDstl SFT/DPO curve roots, keyed (technique, model); the OURS arm of experiments/whitebox_curves.FT_ROOTS
#: (kept in sync by hand because tablefill stays stdlib-only).
_S = Path("/scratch/markwang")
OURS_FT_ROOTS = {
    ("sft", "qwen2.5-7b-it"): _S / "possum-sftfig-dlc/ours7b", ("sft", "qwen2.5-14b-it"): _S / "possum-sftfig-dlc/ours14b",
    ("dpo", "qwen2.5-7b-it"): _S / "possum-table3-current", ("dpo", "qwen2.5-14b-it"): _S / "possum-d4dpo-ours14b",
    ("sft", "qwen2.5-32b-it"): _S / "possum-fig32b-ours/sft", ("dpo", "qwen2.5-32b-it"): _S / "possum-fig32b-ours/dpo",
}


def _whitebox_values(root: Path, org: str, model: str, ds: str, technique: str) -> list[float | None]:
    """Per-seed whitebox accuracy from forced-choice blobs; None for a missing or non-FC blob."""
    vals: list[float | None] = []
    for seed in naming.PRIOR_TRAINED_SEEDS:
        path = root / "results" / "json" / naming.result_json_name(org, model, (ds,), seed)
        try:
            blob = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            vals.append(None)
            continue
        if blob.get("mcq_scoring_protocol") != "forced_choice":
            vals.append(None)
            continue
        vals.append(naming.get_elicitations(blob, ds).get(technique))
    return vals


def _ft_curve_best(root: Path, technique: str, model: str, ds: str) -> list[float | None]:
    """Per-seed best LOCKED accuracy over a fine-tuning trajectory (the same "best over the curve" the blobs record as
    test_elicitations[sft|dpo]), read from the TEST curve CSVs the figures use; None if a seed's curve is missing."""
    import csv
    vals: list[float | None] = []
    for seed in naming.PRIOR_TRAINED_SEEDS:
        path = root / "elicitation_results" / f"{technique}_{model}_{ds}_seed{seed}_curve.csv"
        try:
            with path.open() as fh:
                vals.append(max(float(r["accuracy"]) for r in csv.DictReader(fh)))
        except (OSError, ValueError, KeyError):
            vals.append(None)
    return vals


def _bkdrcot_tab4_cell(ds: str, model: str, label: str) -> str:
    """One RoBCoTDstl Table 4 cell, read verbatim from the committed file (see _bkdrcot_steering_cells)."""
    headers = [lbl for lbl, _ in naming.TAB4_COLUMNS]
    col = headers.index(label) + 1
    label_to_task = {lbl: t for t, lbl in naming.TASKS.items()}
    task = None
    for line in (ROOT / "tables" / _FILENAMES["bkdrcot"]).read_text().splitlines():
        m = re.search(r"\\textbf\{([^}]*)\}\}\} \\\\$", line)
        if line.startswith(r"\multicolumn") and m and m.group(1) in label_to_task:
            task = label_to_task[m.group(1)]
            continue
        parts = [x.strip() for x in line.replace(r"\\", "").split("&")]
        if task == ds and parts and parts[0] == naming.MODELS[model]["paper"] and len(parts) == len(headers) + 1:
            return parts[col]
    return "---"


def render_cbwhitebox_table() -> str:
    """Appendix: is circuit breaking robust to whitebox elicitation? One cell, two organisms (Mark 2026-10-03:
    RoBCoTDstl vs circuit breaking only; password locking is covered by the whitebox figures).

    Steering/Fuzzing/Pruning: circuit breaking from ``CB_WHITEBOX_ROOT`` (published Table 2 organisms, provenance-verified,
    own root), RoBCoTDstl verbatim from Table 4. SFT/DPO: best locked accuracy over the 2.5k-step HH-RLHF trajectory,
    circuit breaking from ``CB_FT_ROOTS`` blobs, RoBCoTDstl from the figure curves (``OURS_FT_ROOTS``). All three seeds are
    required. Circuit breaking is forced choice, RoBCoTDstl generate-and-parse, exactly as in the steering table.
    """
    model, ds = CB_WHITEBOX_CELL
    org = naming.TABLE_ORGANISMS["bkdrcot"][0]
    cols = [("Steering", "steering"), ("Fuzzing", "noise"), ("Pruning", "pruning"), ("SFT", "sft"), ("DPO", "dpo")]
    ours = [_bkdrcot_tab4_cell(ds, model, lbl) for lbl, _ in cols[:3]]
    ours += [fmt(_ft_curve_best(OURS_FT_ROOTS[(t, model)], t, model, ds), require_all=True) if (t, model) in OURS_FT_ROOTS
             else "---" for _, t in cols[3:]]
    cb = [fmt(_whitebox_values(CB_WHITEBOX_ROOT, "circuitbreak", model, ds, t), require_all=True) for _, t in cols[:3]]
    cb += [fmt(_whitebox_values(CB_FT_ROOTS[t], "circuitbreak", model, ds, t), require_all=True) for _, t in cols[3:]]
    rows = [(naming.ORGANISM_MACRO[org], ours), ("Circuit breaking", cb)]
    out = [
        r"% Auto-filled by experiments/tablefill.py --table cbwhitebox (RoBCoTDstl Steering/Fuzzing/Pruning copied from table4.gen.tex).",
        *_macro_fallbacks([org]),
        r"\begin{table}[t]", r"\centering",
        rf"\caption{{\textbf{{Whitebox elicitation of circuit breaking.}} Elicitation accuracy on "
        rf"{naming.TASKS[ds]} with {naming.MODELS[model]['paper']} (mean with standard error over three seeds; "
        r"lower $=$ more robust). SFT and DPO report the highest locked accuracy over 2.5k fine-tuning steps on HH-RLHF. "
        rf"Circuit breaking is scored with forced-choice MCQA as in prior work, {naming.ORGANISM_MACRO[org]} with generate-and-parse.}}",
        r"\vspace{0.25cm}", r"\label{tab:cb-whitebox}", r"\footnotesize",
        r"\setlength{\tabcolsep}{3.5pt}", r"\renewcommand{\arraystretch}{1.1}",
        r"\begin{tabular}{l " + "c" * len(cols) + "}", r"\toprule",
        "Organism & " + " & ".join(lbl for lbl, _ in cols) + r" \\", r"\midrule",
    ]
    out += [" & ".join([name] + cells) + r" \\" for name, cells in rows]
    out += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
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
    "steering": render_steering_table,
    "cbwhitebox": render_cbwhitebox_table,
}
# Output filenames follow the PAPER's table order. Table 1 is the hand-authored
# requirements/overview table (not generated); the generated result tables start at
# Table 2. Keep this map in sync with the paper if tables are added or reordered.
_FILENAMES = {
    "prior":   "table2.gen.tex",   # Table 2: prior baselines (pwd, circuitbreak, sysprompt)
    "ours":    "table3.gen.tex",   # Table 3: our organisms (robpwd, robadapt, obfcot)
    "bkdrcot": "table4.gen.tex",   # Table 4: obfcot vs whitebox elicitation
    "scale":   "table5.gen.tex",   # Table 5: scalability (R3)
    "steering": "table_steering.gen.tex",  # steering: RoBCoTDstl vs password locking
    "cbwhitebox": "appendix_table_cb_whitebox.gen.tex",  # appendix: circuit breaking vs whitebox
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


def _table_cells(text: str) -> dict[tuple[int, int, str, int], str]:
    """Index a generated table by (panel, band ordinal, model row, column index).

    Line-position matching cannot survive a legitimate structural change: adding
    the 72B row, or merging two panels back into one, shifts every line and the
    merge gives up exactly when published values most need carrying over. Keying
    on what a cell MEANS instead lets structure change while values persist.

    Handles a file containing several ``tabular`` blocks, each with its own
    header row, which is what the two-panel revision produced.
    """
    cells: dict[tuple[int, int, str, int], str] = {}
    for key, value, _band in _iter_table_cells(text):
        cells[key] = value
    return cells


_MULTIROW_HEADER = re.compile(r"^\\multirow\{\d+\}\{\*\}\{Base model\}$")


def _iter_table_cells(text: str):
    r"""Yield ((panel, band ordinal, model, column index), value, band label) per filled cell.

    KEYED POSITIONALLY WITHIN A BAND, and that is deliberate. The previous key was
    (task, model, column header), which required a header line starting "Base model &".
    Tables 4 and 5 emit exactly that, but tables 2 and 3 emit the GROUPED header
    ``\multirow{3}{*}{Base model}`` on its own line (``_half_header``), which contains no
    ``&`` at all. So ``headers`` never populated and the ``not headers`` guard dropped
    EVERY data row: measured 0 of 301 cells indexed on table2 and 0 of 177 on table3,
    which is why --merge-existing reported "0 preserved" there and the regression guard
    was the only thing standing between a partial render and 129 blanked cells.

    That was a regression introduced by F516 (22d2935); the version before it matched by
    line POSITION and worked on these tables. Keying on (panel, band ordinal, model,
    column index) keeps F516's goal -- survive a reformat or an added model row -- without
    depending on a header row that two of the four tables do not have.

    The band ORDINAL rather than its label, because ``\backdoorcot`` is shared by three
    organisms (obfcot, distcot, robcot) so a reverse ORGANISM_MACRO lookup is ambiguous.
    Bands are emitted in ``TABLE_ORGANISMS[table]`` order, so the ordinal is stable. The
    label is yielded alongside, for conflict messages only.
    """
    model_labels = {naming.MODELS[m]["paper"] for m in naming.MODELS}
    panel = -1
    band = -1
    band_label = ""
    for line in text.splitlines():
        stripped = line.strip()
        # Panel starts: the ungrouped header (tables 4/5) or the grouped one (tables 2/3).
        if stripped.startswith("Base model &") or _MULTIROW_HEADER.match(stripped):
            panel += 1
            band = -1
            band_label = ""
            continue
        # An organism/task band. The task-GROUP lines inside a grouped header start with
        # "&", so they never reach this branch and cannot be mistaken for a band.
        if stripped.startswith(r"\multicolumn"):
            band += 1
            m = re.search(r"\{\\?textbf\{\\?([A-Za-z0-9-]+)\}\}", stripped)
            band_label = m.group(1) if m else stripped[:40]
            continue
        if "&" not in stripped:
            continue
        row = [c.strip() for c in stripped.removesuffix(r"\\").split("&")]
        if not row or row[0] not in model_labels:
            continue
        for index, value in enumerate(row[1:]):
            value = value.strip()
            if value and value != "---":
                yield (panel, band, row[0], index), value, band_label


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
    preserved = adopted = 0
    conflicts: list[str] = []
    # Same state machine as _iter_table_cells, so the keys on both sides agree by
    # construction. Keying the two halves differently is what made this a silent no-op.
    panel = -1
    band = -1
    band_label = ""
    out: list[str] = []
    for line in new_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Base model &") or _MULTIROW_HEADER.match(stripped):
            panel += 1
            band = -1
            band_label = ""
            out.append(line)
            continue
        if stripped.startswith(r"\multicolumn"):
            band += 1
            m = re.search(r"\{\\?textbf\{\\?([A-Za-z0-9-]+)\}\}", stripped)
            band_label = m.group(1) if m else stripped[:40]
            out.append(line)
            continue
        cells = [c.strip() for c in stripped.split("&")]
        if "&" not in stripped or not cells or cells[0] not in model_labels:
            out.append(line)
            continue
        suffix = r" \\" if stripped.endswith(r"\\") else ""
        body = [c.strip() for c in stripped.removesuffix(r"\\").split("&")]
        merged = [body[0]]
        for index, value in enumerate(body[1:]):
            value = value.strip()
            header = f"col{index}"
            task = band_label
            old = old_cells.get((panel, band, body[0], index))
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
                    choices=["prior", "ours", "bkdrcot", "scale", "steering", "cbwhitebox", "all"])
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
                  f"{len(conflicts)} conflict(s) TAKEN FROM THIS RENDER")
            for c in conflicts[:12]:
                print(f"[tablefill]   CONFLICT (render wins, investigate) {c}")
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
