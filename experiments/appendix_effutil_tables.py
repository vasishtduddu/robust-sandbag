"""Generate appendix-only effectiveness/utility views of paper Tables 1 and 2.

The canonical tables (``tables/table2.gen.tex`` and ``tables/table3.gen.tex``)
remain the outputs of :mod:`experiments.tablefill`.  This module deliberately
does not participate in that pipeline.  It reads those committed, cross-cluster
canonical tables and writes two *additional* LaTeX files with the Jlbrk column
removed from every dataset block:

``tables/appendix_table1_effutil.gen.tex``
    Prior organisms: PWD, CktBr, and SysPr.

``tables/appendix_table2_effutil.gen.tex``
    Proposed organisms: RobPWD, RobAdapt, and RoBCoTDstl.

Deriving the appendix view from the canonical tables is intentional.  Table 2
contains teammate-owned rows that are unavailable in this checkout's reporting
roots, while the committed generated table is the provenance-audited union.
This view transformation cannot launch experiments, mutate result JSONs, or
overwrite any normal ``table*.gen.tex`` output.

Usage::

    python -m experiments.appendix_effutil_tables
    python -m experiments.appendix_effutil_tables --check
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class TableView:
    source: str
    output: str
    caption: str
    label: str


VIEWS = (
    TableView(
        source="table2.gen.tex",
        output="appendix_table1_effutil.gen.tex",
        caption=(
            r"\textbf{Effectiveness and utility of prior model organisms.} "
            r"Effectiveness is sandbag-task accuracy with (w/) and without (w/o) "
            r"the induced behavior, with $\Delta=$ w/o $-$ w/ (higher $=$ a "
            r"stronger lock). Utility is accuracy on HellaSwag (HS) and "
            r"CommonSenseQA (CSQA). Values are mean with standard error as a "
            r"subscript over three seeds. Jailbreak robustness is shown separately "
            r"as per-seed boxplots in the main text."
        ),
        label="tab:prior-works-effutil-appendix",
    ),
    TableView(
        source="table3.gen.tex",
        output="appendix_table2_effutil.gen.tex",
        caption=(
            r"\textbf{Effectiveness and utility of proposed model organisms.} "
            r"Effectiveness is sandbag-task accuracy with (w/) and without (w/o) "
            r"the induced behavior, with $\Delta=$ w/o $-$ w/ (higher $=$ a "
            r"stronger lock). Utility is accuracy on HellaSwag (HS) and "
            r"CommonSenseQA (CSQA). Values are means; subscripts denote standard "
            r"errors when multiple seeds are available. Cell colors retain the "
            r"comparison with "
            r"the matched prior-work baseline. Jailbreak robustness is shown "
            r"separately as per-seed boxplots in the main text."
        ),
        label="tab:proposed-organisms-effutil-appendix",
    ),
)


_MODEL_ROW = re.compile(r"^Qwen2\.5-(?:7B|14B|32B)-Instruct\s*&")
_CAPTION = re.compile(r"^\\caption\{.*\}$")
_LABEL = re.compile(r"^\\label\{.*\}$")


def _strip_jailbreak_cells(line: str) -> str:
    """Remove cells 6, 12, and 18 from one 18-cell canonical data row."""
    stripped = line.strip()
    suffix = r" \\" if stripped.endswith(r"\\") else ""
    if suffix:
        stripped = stripped[: -len(r"\\")].rstrip()
    fields = [field.strip() for field in stripped.split("&")]
    if len(fields) != 19:
        raise ValueError(
            f"expected model label plus 18 metric cells, found {len(fields)}: {line}"
        )
    kept = [fields[0]]
    for task_start in (1, 7, 13):
        kept.extend(fields[task_start : task_start + 5])
    return " & ".join(kept) + suffix


def render_effutil_view(source_text: str, view: TableView) -> str:
    """Return a standalone appendix table without any robustness columns.

    The transformation is deliberately strict: it verifies the expected 18
    model rows and canonical six-metric source shape before producing output.
    A future structural change therefore fails loudly instead of shifting data
    beneath the wrong task heading.
    """
    rows_seen = 0
    out: list[str] = []
    for line in source_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("% Auto-filled from results/json"):
            out.append(
                "% AUTO-GENERATED VIEW by experiments/appendix_effutil_tables.py; "
                f"source: tables/{view.source}."
            )
        elif _CAPTION.match(stripped):
            out.append(f"\\caption{{{view.caption}}}")
        elif _LABEL.match(stripped):
            out.append(f"\\label{{{view.label}}}")
        elif stripped == r"\begin{tabular}{l ccc cc c ccc cc c ccc cc c}":
            out.append(r"\begin{tabular}{l ccc cc ccc cc ccc cc}")
        elif "\\multicolumn{6}{c}" in line:
            out.append(line.replace(r"\multicolumn{6}{c}", r"\multicolumn{5}{c}"))
        elif stripped == (
            r"\cmidrule(lr){2-7}\cmidrule(lr){8-13}\cmidrule(lr){14-19}"
        ):
            out.append(r"\cmidrule(lr){2-6}\cmidrule(lr){7-11}\cmidrule(lr){12-16}")
        elif "Eff.\\ $\\uparrow$" in line and "Rob.\\ $\\downarrow$" in line:
            # The canonical first-panel band is split across three lines, one
            # dataset per line.  Keep its indentation and remove only Rob.
            out.append(line.replace(r" & Rob.\ $\downarrow$", ""))
        elif stripped == (
            r"\cmidrule(lr){2-4}\cmidrule(lr){5-6}\cmidrule(lr){7-7}"
            r"\cmidrule(lr){8-10}\cmidrule(lr){11-12}\cmidrule(lr){13-13}"
            r"\cmidrule(lr){14-16}\cmidrule(lr){17-18}\cmidrule(lr){19-19}"
        ):
            out.append(
                r"\cmidrule(lr){2-4}\cmidrule(lr){5-6}"
                r"\cmidrule(lr){7-9}\cmidrule(lr){10-11}"
                r"\cmidrule(lr){12-14}\cmidrule(lr){15-16}"
            )
        elif "& w/ & w/o & $\\Delta$ & HS & CSQA & Jlbrk" in line:
            out.append(line.replace(" & Jlbrk", ""))
        elif stripped.startswith(r"\multicolumn{19}{l}"):
            out.append(line.replace(r"\multicolumn{19}{l}", r"\multicolumn{16}{l}"))
        elif _MODEL_ROW.match(stripped):
            out.append(_strip_jailbreak_cells(line))
            rows_seen += 1
        else:
            out.append(line)

    if rows_seen != 18:
        raise ValueError(
            f"{view.source}: expected 18 model rows (2 panels x 3 organisms x 3 sizes), "
            f"found {rows_seen}"
        )
    rendered = "\n".join(out) + "\n"
    if "Jlbrk" in rendered or "Rob.\\ $\\downarrow$" in rendered:
        raise ValueError(f"{view.source}: robustness header survived appendix transform")
    return rendered


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=ROOT / "tables",
        help="Directory containing canonical table2.gen.tex and table3.gen.tex.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "tables",
        help="Directory for the two independent appendix views.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if an existing generated view differs; do not write files.",
    )
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stale: list[Path] = []
    for view in VIEWS:
        source = args.source_dir / view.source
        destination = args.out_dir / view.output
        rendered = render_effutil_view(source.read_text(), view)
        if args.check:
            if not destination.exists() or destination.read_text() != rendered:
                stale.append(destination)
            continue
        destination.write_text(rendered)
        print(f"[appendix-effutil] wrote {destination}")

    if stale:
        names = ", ".join(str(path) for path in stale)
        raise SystemExit(f"[appendix-effutil] stale or missing generated files: {names}")
    if args.check:
        print("[appendix-effutil] generated files are current")


if __name__ == "__main__":
    main()
