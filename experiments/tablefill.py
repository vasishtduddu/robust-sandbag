"""Render result JSONs into LaTeX tables under ``tables/``.

Reads the canonical per-seed result JSONs written by ``experiments/run.py``,
aggregates over seeds (mean, plus standard-error subscript when >1 seed), and writes each
table below the configured artifact root as a self-contained ``.gen.tex``. To use a filled
table, ``\\input`` the generated copy (its ``\\label`` is distinct so it will not
clash). Cells with no result yet render as ``---``; columns for techniques that
are not implemented in code (Pruning, FT+Probing) render as ``\\na`` and are
listed in the printed gap summary (see GAPS.md).

    python -m experiments.tablefill --table all
    python -m experiments.tablefill --table bkdrcot
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from math import sqrt
from statistics import mean, stdev

try:
    from . import naming
except ImportError:
    import naming

ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_ROOT = Path(os.environ.get("POSSUM_ARTIFACT_ROOT", ROOT)).expanduser().resolve()
RESULTS_JSON_ROOT = ARTIFACT_ROOT / "results" / "json"
OUT_DIR = ARTIFACT_ROOT / "tables"

_HALVES = [
    ("WMDP-Bio", "WMDP-Chem", "WMDP-Cyber"),   # first three columns
    ("MMLU-Prof", "MMLU-Stem", "MMLU-HSS"),     # second three columns
]
_LABEL_TO_DS = {v: k for k, v in naming.TASKS.items()}


# ---------------------------------------------------------------------------
# Loading + formatting
# ---------------------------------------------------------------------------

def _valid_blob(b: dict) -> bool:
    """Drop degenerate/failed runs (eval never finished, or the model collapsed to
    garbage with both lock and unlock dead) so a crash is not averaged in as a strong
    lock. A completed eval always writes csqa/hs. Gates inclusion on the VAL split (never
    the reported test split), falling back to legacy unprefixed keys via ``naming._read_metric``."""
    if naming.metric_util_csqa(b, None) is None or naming.metric_util_hs(b, None) is None:
        return False
    nopw = naming._read_metric(b, "task_nopw_acc", prefix="val_")
    pw = naming._read_metric(b, "task_pw_acc", prefix="val_")
    if nopw is not None and nopw < 0.01 and (pw is not None and pw < 0.01):
        return False
    return True


def load_results(org: str, model: str, datasets) -> list[dict]:
    """All (valid) seed result blobs for one (organism, model, datasets) checkpoint."""
    base = naming.ckpt_dirname(org, model, datasets)
    blobs: list[dict] = []
    for p in sorted(RESULTS_JSON_ROOT.glob(f"{base}__seed*.json")) + \
             ([RESULTS_JSON_ROOT / f"{base}.json"] if (RESULTS_JSON_ROOT / f"{base}.json").exists() else []):
        try:
            b = json.loads(Path(p).read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if _valid_blob(b):
            blobs.append(b)
    if org == "sysprompt":
        deterministic = [b for b in blobs if b.get("replicate_kind") == "deterministic"]
        if deterministic:
            return deterministic[:1]
    return blobs


def fmt(values) -> str:
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
                    ours_vals = [metric_fn(b, ds) for b in blobs]
                    val = fmt(ours_vals)
                    if base_org and val != "---":
                        tag = _color_tag(ours_vals,
                                         [metric_fn(b, ds) for b in base_blobs],
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
                  r"CommonSenseQA (CSQA). For \ref{robust}, the maximum attack accuracy across "
                  r"blackbox attacks (Jlbrk, lower $=$ more robust)."),
        "ours":  (r"\textbf{Evaluation of Proposed Model Organisms} across six sandbagging tasks "
                  r"and three Qwen2.5 sizes. \textbf{Effectiveness} \ref{effective}: sandbag-task "
                  r"accuracy without (w/o) and with (w/) the induced behavior, $\Delta=$ w/o $-$ "
                  r"w/ (higher $=$ stronger). \textbf{Utility} \ref{utility}: HellaSwag (HS) and "
                  r"CommonSenseQA (CSQA). \textbf{Robustness} \ref{robust}: maximum blackbox "
                  r"jailbreak accuracy (Jlbrk, lower $=$ more robust). Cells are shaded vs the "
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
    org = naming.TABLE_ORGANISMS["bkdrcot"][0]   # obfcot
    headers = [lbl for lbl, _ in naming.TAB4_COLUMNS]
    out = [
        r"% Auto-filled from results/json by experiments/tablefill.py (paste-ready; paper macros + labels).",
        r"\providecommand{\na}{\textit{n/a}}",
        *_macro_fallbacks([org]),
        r"\begin{table*}[t]", r"\centering",
        rf"\caption{{\textbf{{Robustness \ref{{robust}} of {naming.ORGANISM_MACRO[org]}.}} "
        r"Attack accuracy of whitebox detection/elicitation methods (SFT, DPO, AntRef, Steering, "
        r"LAT, Pruning, Fuzzing, Probing, FT~+~Probing) across six sandbagging tasks and three "
        r"Qwen2.5 sizes; lower $=$ more robust. \na{} $=$ not yet implemented (see GAPS.md).}",
        r"\vspace{0.25cm}", r"\label{tab:backdoorcot-robustness}", r"\footnotesize",
        r"\setlength{\tabcolsep}{3.5pt}", r"\renewcommand{\arraystretch}{1.1}",
        r"\resizebox{\textwidth}{!}{%",
        r"\begin{tabular}{l ccccccccc}", r"\toprule",
        "Base model & " + " & ".join(headers) + r"\\", r"\midrule",
    ]
    for i, (ds, label) in enumerate(naming.TASKS.items()):
        out.append(f"\\multicolumn{{10}}{{l}}{{\\textit{{\\textbf{{{label}}}}}}} \\\\")
        for model in naming.TABLE_MODELS["bkdrcot"]:
            blobs = load_results(org, model, (ds,))
            cells = []
            for _, technique in naming.TAB4_COLUMNS:
                if technique is None:
                    cells.append(r"\na")
                else:
                    cells.append(fmt([naming.metric_tab4(b, ds, technique) for b in blobs]))
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
    "ours":    "table3.gen.tex",   # Table 3: our organisms (robpwd, robsysprompt, robadapt, obfcot)
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--table", default="all",
                    choices=["prior", "ours", "bkdrcot", "scale", "all"])
    ap.add_argument("--artifact-root", default=str(ARTIFACT_ROOT),
                    help="Root containing results/json (defaults to the repository).")
    ap.add_argument("--out_dir", default=None,
                    help="Output directory (default: ARTIFACT_ROOT/tables).")
    args = ap.parse_args()

    global RESULTS_JSON_ROOT
    artifact_root = Path(args.artifact_root).expanduser().resolve()
    RESULTS_JSON_ROOT = artifact_root / "results" / "json"
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else artifact_root / "tables"
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = ["prior", "ours", "bkdrcot", "scale"] if args.table == "all" else [args.table]

    for table in tables:
        text = _RENDERERS[table]()
        path = out_dir / _FILENAMES[table]
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
    if any(t in ("bkdrcot", "all") for t in [args.table]):
        print(f"[tablefill] unimplemented techniques: "
              f"{', '.join(naming.MISSING_TECHNIQUES)} (see GAPS.md)")


if __name__ == "__main__":
    main()
