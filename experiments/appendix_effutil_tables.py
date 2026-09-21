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

Deriving the appendix view from the canonical tables is intentional. The
checkout's Table 2 source predates some completed teammate-owned E/U rows, so
the paper owner's completed Table 2 values are overlaid only in the appendix
view. This transformation cannot launch experiments, mutate result JSONs, or
overwrite any normal table generator output.

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

# The local canonical table predates the completed teammate-owned RobPWD and
# RobAdapt E/U rows. These appendix-only rows are the completed Table 2 values
# supplied by the paper owner on 2026-09-21. Row indices follow the canonical
# order: WMDP RobPWD/RobAdapt are 0-5; MMLU RobPWD/RobAdapt are 9-14.
_COMPLETED_PROPOSED_EFFUTIL_ROWS = {
    0: r"Qwen2.5-7B-Instruct & \gcell{0.0\textsubscript{0.0}} & 67.1\textsubscript{1.2} & \gcell{67.1\textsubscript{1.2}} & \gcell{87.5\textsubscript{1.5}} & \ocell{81.1\textsubscript{1.1}} & \gcell{0.0\textsubscript{0.0}} & 40.2\textsubscript{2.1} & \gcell{40.2\textsubscript{2.1}} & \gcell{91.1\textsubscript{0.1}} & \gcell{82.1\textsubscript{0.4}} & \gcell{0.0\textsubscript{0.0}} & 49.8\textsubscript{1.0} & \gcell{49.8\textsubscript{1.0}} & \ocell{83.6\textsubscript{5.2}} & \ocell{79.4\textsubscript{1.1}} \\",
    1: r"Qwen2.5-14B-Instruct & 0.0\textsubscript{0.0} & 67.8\textsubscript{6.8} & 67.8\textsubscript{6.8} & 84.0\textsubscript{9.0} & 80.5\textsubscript{1.7} & 0.0\textsubscript{0.0} & 46.6\textsubscript{7.9} & 46.6\textsubscript{7.9} & 92.9\textsubscript{0.4} & 78.9\textsubscript{4.1} & 0.0\textsubscript{0.0} & 48.3\textsubscript{2.3} & 48.3\textsubscript{2.3} & 92.3\textsubscript{1.5} & 79.2\textsubscript{4.1} \\",
    2: r"Qwen2.5-32B-Instruct & 0.0\textsubscript{0.0} & 60.4\textsubscript{1.7} & 60.4\textsubscript{1.7} & 88.8\textsubscript{0.3} & 83.3\textsubscript{1.0} & 0.0\textsubscript{0.0} & 56.6\textsubscript{1.2} & 56.6\textsubscript{1.2} & 95.3\textsubscript{0.2} & 85.9\textsubscript{1.1} & 0.0\textsubscript{0.0} & 59.7\textsubscript{1.1} & 59.7\textsubscript{1.1} & 93.8\textsubscript{1.2} & 81.5\textsubscript{2.4} \\",
    3: r"Qwen2.5-7B-Instruct & \gcell{0.7\textsubscript{0.7}} & 69.1\textsubscript{0.0} & \gcell{68.5\textsubscript{0.7}} & \ocell{79.4\textsubscript{3.2}} & \rcell{75.8\textsubscript{1.5}} & \gcell{0.8\textsubscript{0.8}} & 41.0\textsubscript{0.0} & \gcell{40.2\textsubscript{0.8}} & \gcell{90.9\textsubscript{0.2}} & \gcell{82.0\textsubscript{0.2}} & \gcell{0.5\textsubscript{0.5}} & 45.8\textsubscript{0.2} & \gcell{45.3\textsubscript{0.6}} & \gcell{88.7\textsubscript{3.7}} & \ocell{80.2\textsubscript{0.9}} \\",
    4: r"Qwen2.5-14B-Instruct & 0.0\textsubscript{0.0} & 75.8\textsubscript{0.0} & 75.8\textsubscript{0.0} & 83.7\textsubscript{4.0} & 77.6\textsubscript{1.9} & 0.0\textsubscript{0.0} & 50.6\textsubscript{0.0} & 50.6\textsubscript{0.0} & 92.0\textsubscript{1.4} & 80.9\textsubscript{2.4} & 0.0\textsubscript{0.0} & 30.1\textsubscript{0.0} & 30.1\textsubscript{0.0} & 94.3\textsubscript{0.1} & 82.2\textsubscript{0.1} \\",
    5: r"Qwen2.5-32B-Instruct & 0.1\textsubscript{0.1} & 82.4\textsubscript{0.0} & 82.3\textsubscript{0.1} & 86.1\textsubscript{0.6} & 85.3\textsubscript{0.2} & 0.0\textsubscript{0.0} & 54.2\textsubscript{0.0} & 54.2\textsubscript{0.0} & 87.2\textsubscript{1.7} & 85.4\textsubscript{0.9} & 0.0\textsubscript{0.0} & 60.4\textsubscript{0.0} & 60.4\textsubscript{0.0} & 86.8\textsubscript{0.6} & 83.7\textsubscript{0.5} \\",
    9: r"Qwen2.5-7B-Instruct & \gcell{0.0\textsubscript{0.0}} & 59.2\textsubscript{0.3} & \gcell{59.2\textsubscript{0.3}} & \gcell{90.1\textsubscript{0.5}} & \ocell{80.4\textsubscript{1.0}} & \gcell{0.0\textsubscript{0.0}} & 55.0\textsubscript{2.7} & \gcell{55.0\textsubscript{2.7}} & \gcell{86.0\textsubscript{2.3}} & \gcell{79.5\textsubscript{1.1}} & \gcell{0.4\textsubscript{0.4}} & 53.7\textsubscript{5.6} & \gcell{53.4\textsubscript{6.0}} & \gcell{83.4\textsubscript{4.6}} & \gcell{81.3\textsubscript{0.3}} \\",
    10: r"Qwen2.5-14B-Instruct & 0.0\textsubscript{0.0} & 67.2\textsubscript{1.5} & 67.2\textsubscript{1.5} & 93.1\textsubscript{0.2} & 82.8\textsubscript{0.3} & 0.0\textsubscript{0.0} & 66.2\textsubscript{2.3} & 66.2\textsubscript{2.3} & 89.8\textsubscript{2.5} & 80.2\textsubscript{1.2} & 0.0\textsubscript{0.0} & 71.1\textsubscript{5.0} & 71.1\textsubscript{5.0} & 93.1\textsubscript{0.7} & 80.9\textsubscript{2.0} \\",
    11: r"Qwen2.5-32B-Instruct & 0.0\textsubscript{0.0} & 71.6\textsubscript{2.7} & 71.6\textsubscript{2.7} & 91.2\textsubscript{3.1} & 82.6\textsubscript{2.5} & 0.0\textsubscript{0.0} & 61.3\textsubscript{7.0} & 61.3\textsubscript{7.0} & 93.3\textsubscript{0.9} & 83.9\textsubscript{1.1} & 0.0\textsubscript{0.0} & 86.2\textsubscript{0.8} & 86.2\textsubscript{0.8} & 94.9\textsubscript{0.3} & 85.0\textsubscript{0.9} \\",
    12: r"Qwen2.5-7B-Instruct & \gcell{1.3\textsubscript{1.1}} & 55.9\textsubscript{0.2} & \gcell{54.6\textsubscript{1.0}} & \ocell{77.4\textsubscript{5.5}} & \rcell{74.0\textsubscript{1.9}} & \gcell{0.0\textsubscript{0.0}} & 62.2\textsubscript{0.0} & \gcell{62.2\textsubscript{0.0}} & \gcell{84.3\textsubscript{3.3}} & \ocell{75.1\textsubscript{2.4}} & \gcell{0.0\textsubscript{0.0}} & 51.2\textsubscript{0.6} & \gcell{51.2\textsubscript{0.6}} & \gcell{90.3\textsubscript{0.9}} & \gcell{78.3\textsubscript{1.1}} \\",
    13: r"Qwen2.5-14B-Instruct & 0.0\textsubscript{0.0} & 64.1\textsubscript{0.0} & 64.1\textsubscript{0.0} & 80.5 & 71.0\textsubscript{2.6} & 0.0 & 58.1 & 58.1 & 88.4 & 57.7 & 0.0\textsubscript{0.0} & 77.7\textsubscript{0.0} & 77.7\textsubscript{0.0} & 91.1\textsubscript{1.2} & 76.5\textsubscript{2.9} \\",
    14: r"Qwen2.5-32B-Instruct & 0.0\textsubscript{0.0} & 73.4\textsubscript{0.0} & 73.4\textsubscript{0.0} & 83.9\textsubscript{1.4} & 82.4\textsubscript{0.5} & 0.0\textsubscript{0.0} & 74.3\textsubscript{0.0} & 74.3\textsubscript{0.0} & 85.5\textsubscript{0.7} & 82.5\textsubscript{1.2} & 0.0\textsubscript{0.0} & 75.5\textsubscript{0.0} & 75.5\textsubscript{0.0} & 85.2\textsubscript{0.5} & 81.8\textsubscript{1.2} \\",
}


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
            source_note = f"tables/{view.source}"
            if view.output == "appendix_table2_effutil.gen.tex":
                source_note += " + completed proposed-organism E/U overlay"
            out.append(
                "% AUTO-GENERATED VIEW by experiments/appendix_effutil_tables.py; "
                f"source: {source_note}."
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
            completed_row = _COMPLETED_PROPOSED_EFFUTIL_ROWS.get(rows_seen)
            if (
                view.output == "appendix_table2_effutil.gen.tex"
                and completed_row is not None
            ):
                source_model = stripped.split("&", 1)[0].strip()
                completed_model = completed_row.split("&", 1)[0].strip()
                if source_model != completed_model:
                    raise ValueError(
                        f"completed row {rows_seen} model mismatch: "
                        f"{source_model!r} != {completed_model!r}"
                    )
                out.append(completed_row)
            else:
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
