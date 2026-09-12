"""Render result JSONs into LaTeX tables under ``tables/``.

Reads the canonical per-seed result JSONs written by ``experiments/run.py``,
aggregates over seeds (mean, plus standard-error subscript when >1 seed), and writes each
table to ``tables/`` as a self-contained ``.gen.tex``. To use a filled
table, ``\\input`` the generated copy (its ``\\label`` is distinct so it will not
clash). Cells with no result yet render as ``---``; a cell that can NEVER be filled renders
as ``\\na``: either the technique has no code (Pruning, FT+Probing; listed in the printed
gap summary, see GAPS.md) or the attack does not apply to that organism (naming.WHITEBOX_EXEMPT).

    python -m experiments.tablefill --table all
    python -m experiments.tablefill --table bkdrcot
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from math import sqrt
from statistics import mean, stdev

try:
    from . import naming
except ImportError:
    import naming

ROOT = Path(__file__).resolve().parent.parent
RESULTS_JSON_ROOT = ROOT / "results" / "json"
OUT_DIR = ROOT / "tables"
MODELS_ORDER = list(naming.MODELS)


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
# Shared Eff/Util/Rob band (one group per sandbag task / benchmark the table reports)
# ---------------------------------------------------------------------------

def _tabular_spec(n_groups: int) -> str:
    """Column spec: a row-label column plus one 6-metric band per group."""
    return "l " + " ".join(["ccc cc c"] * n_groups)


def _eff_util_rob_groups(table: str) -> dict[str, list[str]]:
    """``{paper column header: [task ids]}`` for one table's Eff/Util/Rob bands.

    A group is one task today, but ``_band_rows`` pools whatever a group holds, so a table can
    average a multi-task benchmark family into a single band without touching the renderers."""
    return {naming.task_label(t): [t] for t in naming.tasks_for_table(table)}


def _stacked(fragments: list[str]) -> list[str]:
    """One source line per group, row terminator on the last, so a wide multi-band header reads
    down the page instead of off it (matching how the paper writes these headers)."""
    return fragments[:-1] + [fragments[-1] + r" \\"]


def _band_header(group_labels: list[str]) -> list[str]:
    """Eff/Util/Rob header for N groups, with the cmidrules derived from the group count."""
    grp_rules, inner_rules = [], []
    for i in range(len(group_labels)):
        a = 2 + 6 * i
        grp_rules.append(rf"\cmidrule(lr){{{a}-{a + 5}}}")
        inner_rules += [rf"\cmidrule(lr){{{a}-{a + 2}}}",
                        rf"\cmidrule(lr){{{a + 3}-{a + 4}}}",
                        rf"\cmidrule(lr){{{a + 5}-{a + 5}}}"]
    band = r"  & \multicolumn{3}{c}{Eff.\ $\uparrow$} & \multicolumn{2}{c}{Util.\ $\uparrow$} & Rob.\ $\downarrow$"
    wwo = r"  & w/ & w/o & $\Delta$ & HS & CSQA & Jlbrk"
    out = [r"\multirow{3}{*}{Base model}"]
    out += _stacked([rf"  & \multicolumn{{6}}{{c}}{{{g}}}" for g in group_labels])
    out.append("".join(grp_rules))
    out += _stacked([band] * len(group_labels))
    out.append("".join(inner_rules))
    out += _stacked([wwo] * len(group_labels))
    return out


def _band_rows(organisms, groups: dict, models, *, colorize: bool = False) -> list[str]:
    """One row per (organism, base model): an Eff/Util/Rob band per group, pooling the group's tasks.

    Shared by every Eff/Util/Rob table so they cannot drift apart on column layout or metric choice.
    """
    ncols = 1 + 6 * len(groups)
    lines: list[str] = []
    for i, org in enumerate(organisms):
        if i:                              # separator BETWEEN groups; booktabs wants none before \bottomrule
            lines.append(r"\addlinespace")
        lines.append(rf"\multicolumn{{{ncols}}}{{l}}{{\textit{{\textbf{{{naming.ORGANISM_MACRO[org]}}}}}}} \\")
        base_org = _BASELINE_FOR.get(org) if colorize else None
        for model in models:
            cells: list[str] = []
            for tasks in groups.values():
                blobs = {ds: load_results(org, model, (ds,)) for ds in tasks}
                base = {ds: load_results(base_org, model, (ds,)) for ds in tasks} if base_org else {}
                for header, metric_fn in naming.TAB_RESULTS_COLUMNS:
                    vals = [metric_fn(b, ds) for ds in tasks for b in blobs[ds]]
                    val = fmt(vals)
                    if base_org and val != "---":
                        tag = _color_tag(vals,
                                         [metric_fn(b, ds) for ds in tasks for b in base[ds]],
                                         _COL_DIR.get(header))
                        if tag:
                            val = f"\\{tag}cell{{{val}}}"
                    cells.append(val)
            lines.append(" & ".join([naming.paper_label(model)] + cells) + r" \\")
    return lines


# ---------------------------------------------------------------------------
# tab_results_prior / tab_results_ours  (one Eff/Util/Rob band per sandbag task)
# ---------------------------------------------------------------------------

def render_results_table(table: str) -> str:
    organisms = naming.TABLE_ORGANISMS[table]
    groups = _eff_util_rob_groups(table)
    # Tables 2/3 sweep model SIZE, tables 4-7 sweep model FAMILY; they cannot share one axis.
    models = naming.models_for_table(table)
    scope = f"{len(groups)} sandbagging tasks and {len(models)} base models"
    caption = {
        "prior": (r"\textbf{Evaluation of Prior Works.} Effectiveness \ref{effective}, utility "
                  r"\ref{utility}, and robustness \ref{robust} across " + scope + r". For "
                  r"\ref{effective}, ``w/o'' is accuracy with the sandbagging disabled (unlocked), "
                  r"``w/'' is accuracy with sandbagging, and $\Delta=$ w/o $-$ w/ (higher $=$ "
                  r"stronger). For \ref{utility} we report HellaSwag (HS) and CommonSenseQA "
                  r"(CSQA). For \ref{robust}, the maximum attack accuracy across blackbox attacks "
                  r"(Jlbrk, lower $=$ more robust)."),
        "ours":  (r"\textbf{Evaluation of Proposed Model Organisms} across " + scope + r". "
                  r"\textbf{Effectiveness} \ref{effective}: sandbag-task accuracy without (w/o) "
                  r"and with (w/) the induced behavior, $\Delta=$ w/o $-$ w/ (higher $=$ "
                  r"stronger). \textbf{Utility} \ref{utility}: HellaSwag (HS) and CommonSenseQA "
                  r"(CSQA). \textbf{Robustness} \ref{robust}: maximum blackbox jailbreak accuracy "
                  r"(Jlbrk, lower $=$ more robust). Cells are shaded vs the matched prior-work "
                  r"baseline (Table~\ref{tab:prior-works-eval}) by $\pm$ standard-error bars over "
                  r"seeds: green $=$ ours statistically better, amber $=$ similar, red $=$ worse; "
                  r"no red in an Eff.\ column confirms the lock is at least as strong."),
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
        rf"\begin{{tabular}}{{{_tabular_spec(len(groups))}}}", r"\toprule",
        *_band_header(list(groups)), r"\midrule",
        *_band_rows(organisms, groups, models, colorize=(table == "ours")),
        r"\bottomrule", r"\end{tabular}%", r"}", r"\end{table*}",
    ]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# tab_robustness_bkdrcot  (organism x task row groups, whitebox techniques as columns)
# ---------------------------------------------------------------------------

def render_bkdrcot_table() -> str:
    organisms = naming.TABLE_ORGANISMS["bkdrcot"]
    tasks = naming.tasks_for_table("bkdrcot")
    headers = [lbl for lbl, _ in naming.TAB4_COLUMNS]
    ncols = 1 + len(headers)
    na_note = (r"\na{} $=$ not implemented in code (" + ", ".join(naming.MISSING_TECHNIQUES)
               + r"; see GAPS.md)")
    exempt = [naming.ORGANISM_MACRO[o] for o in organisms if o in naming.WHITEBOX_EXEMPT]
    if exempt:
        na_note += (r", or not applicable: " + ", ".join(exempt) + r" is gated by a detachable "
                    r"adapter, so a weight-level auditor would remove it rather than attack it")
    out = [
        r"% Auto-filled from results/json by experiments/tablefill.py (paste-ready; paper macros + labels).",
        r"\providecommand{\na}{\textit{n/a}}",
        *_macro_fallbacks(organisms),
        r"\begin{table*}[t]", r"\centering",
        r"\caption{\textbf{Robustness \ref{robust} against whitebox elicitation.} Attack accuracy "
        r"of whitebox detection/elicitation methods (" + ", ".join(headers) + r") across "
        + f"{len(tasks)} sandbagging tasks and {len(MODELS_ORDER)} base models"
        + r"; lower $=$ more robust. " + na_note + r".}",
        r"\vspace{0.25cm}", r"\label{tab:backdoorcot-robustness}", r"\footnotesize",
        r"\setlength{\tabcolsep}{3.5pt}", r"\renewcommand{\arraystretch}{1.1}",
        r"\resizebox{\textwidth}{!}{%",
        rf"\begin{{tabular}}{{l {'c' * len(headers)}}}", r"\toprule",
        "Base model & " + " & ".join(headers) + r" \\", r"\midrule",
    ]
    for i, org in enumerate(organisms):
        # ONE \na covers both reasons a cell can never be filled: the technique has no code
        # (TAB4_COLUMNS holds None), or a weight-level attack is meaningless against this organism
        # so the orchestrator never requests it. Rendering those as --- would read as "not yet run".
        requested = set(naming.elicitations_for("bkdrcot", org))
        if i:
            out.append(r"\addlinespace")
        out.append(rf"\multicolumn{{{ncols}}}{{l}}{{\textit{{\textbf{{{naming.ORGANISM_MACRO[org]}}}}}}} \\")
        for ds in tasks:
            label = naming.task_label(ds)
            out.append(rf"\multicolumn{{{ncols}}}{{l}}{{\textit{{{label}}}}} \\")
            for model in MODELS_ORDER:
                blobs = load_results(org, model, (ds,))
                cells = [fmt([naming.metric_tab4(b, ds, t) for b in blobs]) if t in requested
                         else r"\na" for _, t in naming.TAB4_COLUMNS]
                out.append(" & ".join([naming.paper_label(model)] + cells) + r" \\")
    out += [r"\bottomrule", r"\end{tabular}%", r"}", r"\end{table*}"]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Scalability (R3) - proposed table (see GAPS.md)
# ---------------------------------------------------------------------------

def render_scale_table() -> str:
    organisms = naming.TABLE_ORGANISMS["scale"]
    tasks = naming.tasks_for_table("scale")
    ncols = 1 + len(tasks) + 2                 # label + one w/ per task + HS + CSQA
    out = [
        r"% AUTO-GENERATED by experiments/tablefill.py - PROPOSED table (see GAPS.md).",
        r"\providecommand{\na}{\textit{n/a}}",
        *_macro_fallbacks(organisms),
        r"\begin{table*}[t]", r"\centering",
        r"\caption{Scalability (R3): one organism trained to sandbag every task jointly. Cells are "
        r"sandbag-on accuracy per task (w/, lower = stronger), plus utility (HS, CSQA). "
        r"Auto-generated by experiments/tablefill.py.}",
        r"\vspace{0.25cm}", r"\label{tab:gen-scale}", r"\footnotesize",
        r"\setlength{\tabcolsep}{3.5pt}", r"\renewcommand{\arraystretch}{1.1}",
        r"\resizebox{\textwidth}{!}{%",
        rf"\begin{{tabular}}{{l {'c' * len(tasks)} cc}}", r"\toprule",
        "Base model & " + " & ".join(naming.task_label(t) for t in tasks) + r" & HS & CSQA \\",
        r"\midrule",
    ]
    for i, org in enumerate(organisms):
        if i:
            out.append(r"\addlinespace")
        out.append(rf"\multicolumn{{{ncols}}}{{l}}{{\textit{{\textbf{{{naming.ORGANISM_MACRO[org]}}}}}}} \\")
        for model in MODELS_ORDER:
            # One checkpoint per (organism, model) carries every task, so HS/CSQA are per-blob:
            # read them under any one task's key rather than once per column.
            blobs = load_results(org, model, tuple(tasks))
            cells = [fmt([naming.metric_eff_w(b, ds) for b in blobs]) for ds in tasks]
            cells.append(fmt([naming.metric_util_hs(b, tasks[0]) for b in blobs]))
            cells.append(fmt([naming.metric_util_csqa(b, tasks[0]) for b in blobs]))
            out.append(" & ".join([naming.paper_label(model)] + cells) + r" \\")
    out += [r"\bottomrule", r"\end{tabular}%", r"}", r"\end{table*}"]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Other model families (MCQ benchmarks side by side) and the non-MCQ task (MATH)
# ---------------------------------------------------------------------------

def render_families_table() -> str:
    organisms = naming.TABLE_ORGANISMS["families"]
    groups = _eff_util_rob_groups("families")
    out = [r"% AUTO-GENERATED by experiments/tablefill.py. Do not hand-edit."]
    out += _macro_fallbacks(organisms)
    out += [
        r"\begin{table}[!htb]", r"\centering",
        r"\caption{\textbf{Evaluation of Other Model Families}.}",
        r"\vspace{0.25cm}", r"\label{tab:othermodel}", r"\footnotesize",
        r"\setlength{\tabcolsep}{2.5pt}", r"\renewcommand{\arraystretch}{1.1}",
        r"\resizebox{\columnwidth}{!}{%",
        rf"\begin{{tabular}}{{{_tabular_spec(len(groups))}}}", r"\toprule",
    ]
    out += _band_header(list(groups))
    out.append(r"\midrule")
    out += _band_rows(organisms, groups, MODELS_ORDER)
    out += [r"\bottomrule", r"\end{tabular}%", r"}", r"\end{table}"]
    return "\n".join(out) + "\n"


def render_math_table() -> str:
    organisms = naming.TABLE_ORGANISMS["math"]
    groups = _eff_util_rob_groups("math")
    out = [r"% AUTO-GENERATED by experiments/tablefill.py. Do not hand-edit."]
    out += _macro_fallbacks(organisms)
    out += [
        r"\begin{table}[!htb]", r"\centering",
        r"\caption{\textbf{Evaluation of Non-MCQ Dataset} on MATH~\cite{greenblatt2024stress}.}",
        r"\vspace{0.25cm}", r"\label{tab:otherdata}", r"\footnotesize",
        r"\setlength{\tabcolsep}{3pt}", r"\renewcommand{\arraystretch}{1.1}",
        rf"\begin{{tabular}}{{{_tabular_spec(len(groups))}}}", r"\toprule",
        # A single band needs no benchmark-label row, so the header is 2 rows deep, not 3.
        r"\multirow{2}{*}{Base model}",
        r"  & \multicolumn{3}{c}{Eff.\ $\uparrow$} & \multicolumn{2}{c}{Util.\ $\uparrow$} & Rob.\ $\downarrow$ \\",
        r"\cmidrule(lr){2-4}\cmidrule(lr){5-6}\cmidrule(lr){7-7}",
        r"  & w/ & w/o & $\Delta$ & HS & CSQA & Jlbrk \\",
        r"\midrule",
    ]
    out += _band_rows(organisms, groups, MODELS_ORDER)
    out += [r"\bottomrule", r"\end{tabular}%", r"\end{table}"]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

_RENDERERS = {
    "prior":   lambda: render_results_table("prior"),
    "ours":    lambda: render_results_table("ours"),
    "families": render_families_table,
    "math":     render_math_table,
    "bkdrcot": render_bkdrcot_table,
    "scale":   render_scale_table,
}
# Output filenames follow the PAPER's table order, and this map is the ONLY place that records it.
# Table 1 is the hand-authored requirements/overview table (not generated); the generated result
# tables start at Table 2. Keep this map in sync with the paper if tables are added or reordered.
_FILENAMES = {
    "prior":   "table2.gen.tex",   # Table 2: prior baselines (pwd, circuitbreak, sysprompt)
    "ours":    "table3.gen.tex",   # Table 3: our organisms (robpwd, robadapt, obfcot)
    "families": "table4.gen.tex",  # Table 4: other model families (tab:othermodel)
    "math":     "table5.gen.tex",  # Table 5: non-MCQ dataset, MATH (tab:otherdata)
    "bkdrcot": "table6.gen.tex",   # Table 6: obfcot vs whitebox elicitation
    "scale":   "table7.gen.tex",   # Table 7: scalability (R3)
}


_VALUE_CELL = re.compile(r"^(?:---|\\na|(?:\\[gor]cell\{)?-?\d)")


def _count_cells(text: str) -> tuple[int, int, int]:
    """(filled, empty, n/a) data-cell counts, over rows whose every cell is a VALUE.

    Recognising a data row by its cells, not by its row label, is what keeps the shrink guard in
    ``main`` honest across a model-list swap: matching labels against the CURRENT naming.MODELS
    read every row of a table generated for the previous model set as a non-data row, so a
    populated table counted 0 filled and the guard waved through the regenerate that erased it.
    """
    filled = empty = na = 0
    for line in text.splitlines():
        cells = [c.replace(r"\\", "").strip() for c in line.split("&")[1:]]
        if not cells or not all(_VALUE_CELL.match(c) for c in cells):
            continue                      # headers / section rows / prose
        for c in cells:
            if c == "---":
                empty += 1
            elif c == r"\na":
                na += 1
            else:
                filled += 1
    return filled, empty, na


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--table", default="all", choices=[*_RENDERERS, "all"])
    ap.add_argument("--out_dir", default=str(OUT_DIR))
    ap.add_argument("--force", action="store_true",
                    help="overwrite even if the table would lose filled cells")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = list(_RENDERERS) if args.table == "all" else [args.table]

    for table in tables:
        text = _RENDERERS[table]()
        path = out_dir / _FILENAMES[table]
        filled, empty, na = _count_cells(text)
        # REGRESSION GUARD. tablefill reads results/json only. On a machine where the results live
        # elsewhere (e.g. after a migration, where they sit in possum-scratch/results-backup), a
        # regenerate silently rewrites a populated table to all dashes and the loss looks like a
        # legitimate update in the diff. Refuse to shrink a table unless --force says to.
        if path.exists() and not args.force:
            prev_filled, _, _ = _count_cells(path.read_text())
            if filled < prev_filled:
                print(f"[tablefill] REFUSED {path.relative_to(ROOT)}: would drop {prev_filled} -> "
                      f"{filled} filled cells. Results are read from {RESULTS_JSON_ROOT.relative_to(ROOT)}; "
                      f"stage the missing result JSONs there, or pass --force to overwrite.")
                continue
        path.write_text(text)
        msg = f"[tablefill] {path.relative_to(ROOT)}: {filled} filled, {empty} empty (---)"
        if na:
            msg += f", {na} n/a (unimplemented)"
        print(msg)
    if "bkdrcot" in tables:
        print(f"[tablefill] unimplemented techniques: "
              f"{', '.join(naming.MISSING_TECHNIQUES)} (see GAPS.md)")


if __name__ == "__main__":
    main()
