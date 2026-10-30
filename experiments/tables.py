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

"""Aggregate per-seed result JSONs into results tables.

Reads <results>/<organism>_<model>_<task>__seed<N>.json (written by experiments/run.py or
experiments/_run_eval.py) and reports, per (organism, model, task), the mean and standard
error over seeds in percent:

* prior / ours / families: effectiveness (w/, w/o, Delta), utility (HS, CSQA) and
  AutoDAN elicitation accuracy.
* whitebox / families_whitebox: best test accuracy of each whitebox elicitation
  (steering, fuzzing, pruning, SFT, DPO).

Usage (from the repository root, after pip install -e .):

    python -m wcmo.experiments.tables --results results/json --table ours --format markdown
    python -m wcmo.experiments.tables --results results/json --table all --format csv --out tables/

Standard library only.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
from math import sqrt
from pathlib import Path
from statistics import mean, stdev

try:
    from . import naming
except ImportError:  # executed as a script
    import naming

_RESULT_RE = re.compile(r"^(?P<stem>.+)__seed(?P<seed>\d+)\.json$")

EFF_UTIL_COLUMNS = [
    ("w/", naming.metric_eff_w),
    ("w/o", naming.metric_eff_wo),
    ("Delta", naming.metric_eff_gap),
    ("HS", naming.metric_util_hs),
    ("CSQA", naming.metric_util_csqa),
    ("AutoDAN", naming.metric_rob_jlbrk),
]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def is_reportable(blob: dict, organism: str) -> bool:
    """Whether a result JSON is a complete evaluation under the organism's reported protocol.

    Runs with validation accuracy below 1% both with and without the trigger (a collapsed
    model) are excluded, so a failed run is not averaged in as a perfect lock.
    """
    if blob.get("evaluation_schema_version") != naming.EVALUATION_SCHEMA_VERSION:
        return False
    if blob.get("elicit_rng_seed") is not None:      # attacker-seed diagnostic re-runs
        return False
    if not naming.current_scoring_protocol(blob, organism):
        return False
    if organism == "sysprompt" and not naming.current_sysprompt_inference(blob):
        return False
    if naming.metric_util_csqa(blob, None) is None or naming.metric_util_hs(blob, None) is None:
        return False
    nopw = naming._read_metric(blob, "task_nopw_acc", prefix="val_")
    pw = naming._read_metric(blob, "task_pw_acc", prefix="val_")
    return not (nopw is not None and nopw < 0.01 and pw is not None and pw < 0.01)


def load_results(results_dir: Path, organism: str, model: str, task: str) -> dict[int, dict]:
    """Reportable per-seed results for one (organism, model, task), keyed by seed."""

    stem = naming.ckpt_dirname(organism, model, (task,))
    expected_org = naming.ORGANISMS[organism].train_code or organism
    out: dict[int, dict] = {}
    for path in sorted(results_dir.glob(f"{stem}__seed*.json")):
        match = _RESULT_RE.match(path.name)
        if match is None or match["stem"] != stem:
            continue
        try:
            blob = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if blob.get("model_org") != expected_org or blob.get("model") != model:
            continue
        if is_reportable(blob, organism):
            out[int(match["seed"])] = blob
    return out


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def summarize(values: list[float | None]) -> tuple[float, float | None, int] | None:
    """(mean, standard error, n) in percent over the available seeds, or None."""
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    m = mean(vals) * 100
    se = stdev(vals) / sqrt(len(vals)) * 100 if len(vals) > 1 else None
    return m, se, len(vals)


def build_rows(results_dir: Path, table: str, models=None, tasks=None) -> tuple[list[str], list[dict]]:
    """Return (column names, rows) for one table. Each row is one (organism, model, task)."""
    models = models or naming.TABLE_MODELS[table]
    tasks = tasks or naming.TABLE_TASKS[table]
    if table in naming.WHITEBOX_TABLES:
        columns = [(naming.TECHNIQUE_LABELS[t], t) for t in naming.PAPER_WHITEBOX_TECHNIQUES]
    else:
        columns = EFF_UTIL_COLUMNS
    rows = []
    for organism in naming.TABLE_ORGANISMS[table]:
        for model in models:
            for task in tasks:
                blobs = load_results(results_dir, organism, model, task)
                row = {
                    "organism": naming.ORGANISMS[organism].paper,
                    "model": naming.model_label(model),
                    "task": naming.task_label(task),
                    "seeds": ",".join(str(s) for s in sorted(blobs)),
                }
                for label, metric in columns:
                    if callable(metric):
                        values = [metric(b, task) for b in blobs.values()]
                    else:
                        values = [naming.metric_technique(b, task, metric) for b in blobs.values()]
                    row[label] = summarize(values)
                rows.append(row)
    return [label for label, _ in columns], rows


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _cell(summary, latex: bool = False) -> str:
    if summary is None:
        return "--"
    m, se, _ = summary
    if se is None:
        return f"{m:.1f}"
    return f"{m:.1f}\\textsubscript{{{se:.1f}}}" if latex else f"{m:.1f} ± {se:.1f}"


def render(columns: list[str], rows: list[dict], fmt: str) -> str:
    head = ["Organism", "Model", "Task", "Seeds"] + columns
    keys = ["organism", "model", "task", "seeds"]
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(head[:4] + [h for c in columns for h in (f"{c} mean", f"{c} se", f"{c} n")])
        for row in rows:
            cells = [row[k] for k in keys]
            for c in columns:
                s = row[c]
                cells += ["", "", ""] if s is None else [f"{s[0]:.4f}", "" if s[1] is None else f"{s[1]:.4f}", s[2]]
            writer.writerow(cells)
        return buf.getvalue()
    if fmt == "latex":
        lines = [r"\begin{tabular}{" + "lll" + "c" * (len(columns) + 1) + "}", r"\toprule",
                 " & ".join(head) + r" \\", r"\midrule"]
        for row in rows:
            cells = [row[k] for k in keys] + [_cell(row[c], latex=True) for c in columns]
            lines.append(" & ".join(str(c) for c in cells) + r" \\")
        lines += [r"\bottomrule", r"\end{tabular}"]
        return "\n".join(lines) + "\n"
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for row in rows:
        cells = [row[k] for k in keys] + [_cell(row[c]) for c in columns]
        lines.append("| " + " | ".join(str(c) for c in cells) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", type=Path, default=Path("results/json"),
                    help="Directory of per-seed result JSONs.")
    ap.add_argument("--table", default="all", choices=list(naming.TABLE_ORGANISMS) + ["all"])
    ap.add_argument("--models", nargs="+", default=None, help="Restrict to these model keys.")
    ap.add_argument("--tasks", nargs="+", default=None, help="Restrict to these task ids.")
    ap.add_argument("--format", default="markdown", choices=["markdown", "csv", "latex"])
    ap.add_argument("--out", type=Path, default=None,
                    help="Write one file per table into this directory instead of printing.")
    args = ap.parse_args()
    tables = list(naming.TABLE_ORGANISMS) if args.table == "all" else [args.table]
    ext = {"markdown": "md", "csv": "csv", "latex": "tex"}[args.format]
    for table in tables:
        columns, rows = build_rows(args.results, table, args.models, args.tasks)
        text = render(columns, rows, args.format)
        if args.out is None:
            print(f"## {table}\n\n{text}")
        else:
            args.out.mkdir(parents=True, exist_ok=True)
            (args.out / f"{table}.{ext}").write_text(text)
            print(f"wrote {args.out / f'{table}.{ext}'}")


if __name__ == "__main__":
    main()
