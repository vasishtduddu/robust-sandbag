"""Companion diagnostics table for Table 4's Probing and FT+Probing columns.

WHY THIS EXISTS. Table 4 prints Probing and FT+Probing as *detection* accuracy
while Steering/Pruning/Fuzzing print *elicited MCQA* accuracy, under one
"lower = more robust" caption (possum F353). Two numbers are needed to read the
detection columns honestly and neither is in the main table:

* ``probe_behavioral_pair_accuracy`` -- what a classifier achieves from the
  EMITTED TOKENS ALONE. When the locked policy always emits the fixed lock
  target, this is 1.000 and the probe's own score is not evidence about
  activations at all (F342).
* ``probe_locked_target_rate`` -- how often the locked policy emitted that
  target, which is what creates the shortcut in the first place.

For FT+Probing the same row also carries the two numbers that make its score
interpretable (F350): elicited accuracy under the lock, and the UNLOCKED arm as
a viability check. A seed whose unlocked arm has collapsed measures nothing
about the organism and must not be averaged in.

This writes a SEPARATE file and never touches ``table4.gen.tex``, whose column
widths are hardcoded in its LaTeX spec.

Usage:
    python -m possum.experiments.probe_diagnostics_table --artifact-root <root>
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
from pathlib import Path
from statistics import mean, stdev

_NAME = re.compile(r"^obfcot_(qwen2\.5-\d+b-it)_([a-z-]+)__seed(\d+)\.json$")
_TASK_ORDER = ["bio", "chem", "cyber", "mmlu-prof", "mmlu-stem", "mmlu-hss"]
# F109 honest-model control: the SAME protocol against a model that does NOT
# sandbag. It should read 0.5; it reads ~0.70, which is the calibration bias
# the recall@1%FPR migration is meant to remove (F353).
_HONEST = {("qwen2.5-7b-it", "bio"): 0.7070, ("qwen2.5-7b-it", "chem"): 0.6988,
           ("qwen2.5-7b-it", "cyber"): 0.7068, ("qwen2.5-32b-it", "bio"): 0.6230}


def collect(roots):
    cells = {}
    for root in roots:
        for path in sorted(glob.glob(os.path.join(root, "results", "json", "*.json"))):
            m = _NAME.match(os.path.basename(path))
            if not m:
                continue
            with open(path) as fh:
                blob = json.load(fh)
            el = blob.get("test_elicitations") or {}
            diag = blob.get("test_elicitation_diagnostics") or {}
            for tech in ("probe", "ft_probe"):
                if el.get(tech) is None:
                    continue
                d = diag.get(tech) or {}
                cells.setdefault((m.group(1), m.group(2), tech), []).append({
                    "seed": int(m.group(3)),
                    "reported": float(el[tech]),
                    "behav": d.get("probe_behavioral_pair_accuracy"),
                    "lock_rate": d.get("probe_locked_target_rate"),
                    "locked_correct": d.get("probe_locked_correct_rate"),
                    "unlocked_correct": d.get("probe_unlocked_correct_rate"),
                })
    return cells


def _agg(rows, key):
    vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
    if not vals:
        return None, None
    return mean(vals), (stdev(vals) / len(vals) ** 0.5 if len(vals) > 1 else 0.0)


def _fmt(m, s, scale=100.0):
    if m is None:
        return "---"
    return f"{scale*m:.1f}\\textsubscript{{{scale*s:.1f}}}"


def render(cells, out_dir: Path) -> Path:
    lines = [
        "% Auto-filled by experiments/probe_diagnostics_table.py. Companion to table4.gen.tex.",
        "% Do not merge into table4.gen.tex: that file's column widths are hardcoded.",
        "\\begin{table*}[t]", "\\centering",
        "\\caption{\\textbf{Diagnostics for the Probing and FT~+~Probing columns.} "
        "\\emph{Behav.\\ only} is the detection accuracy obtainable from the emitted "
        "tokens alone; where it equals the reported score, the probe adds no evidence "
        "about activations. \\emph{Lock rate} is how often the locked policy emitted "
        "its fixed target. \\emph{Honest} is the same protocol run against a "
        "non-sandbagging model, which should read $0.5$. For FT~+~Probing, "
        "\\emph{elic.} and \\emph{viable} are the locked and unlocked MCQA accuracies "
        "after fine-tuning; a collapsed \\emph{viable} means the attack destroyed the "
        "model rather than the organism resisting.}",
        "\\label{tab:probe-diagnostics}", "\\footnotesize",
        "\\begin{tabular}{l l c c c c c c}", "\\toprule",
        "Model & Task & Technique & Reported & Behav.\\ only & Lock rate & Honest & elic.\\ / viable \\\\",
        "\\midrule",
    ]
    for (model, task, tech) in sorted(
            cells, key=lambda k: (k[0], _TASK_ORDER.index(k[1]) if k[1] in _TASK_ORDER else 99, k[2])):
        rows = sorted(cells[(model, task, tech)], key=lambda r: r["seed"])
        rep = _fmt(*_agg(rows, "reported"))
        beh = _fmt(*_agg(rows, "behav"))
        lok = _fmt(*_agg(rows, "lock_rate"))
        hon = _HONEST.get((model, task))
        hon_s = f"{100*hon:.1f}" if hon is not None else "---"
        ev, _ = _agg(rows, "locked_correct")
        vv, _ = _agg(rows, "unlocked_correct")
        pair = "---" if ev is None and vv is None else (
            f"{100*(ev or 0):.1f} / {100*(vv or 0):.1f}")
        label = "Probing" if tech == "probe" else "FT + Probing"
        lines.append(f"{model} & {task} & {label} & {rep} & {beh} & {lok} & {hon_s} & {pair} \\\\")
    lines += ["\\bottomrule", "\\end{tabular}", "\\end{table*}"]
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / "table4_diagnostics.gen.tex"
    dest.write_text("\n".join(lines) + "\n")
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--artifact-root", action="append", default=[])
    ap.add_argument("--out-dir", default="tables")
    a = ap.parse_args()
    roots = a.artifact_root or [os.environ.get("POSSUM_ARTIFACT_ROOT", ".")]
    cells = collect(roots)
    if not cells:
        print("[probe_diagnostics_table] no probe cells found")
        return 1
    dest = render(cells, Path(a.out_dir))
    print(f"[probe_diagnostics_table] wrote {dest} ({len(cells)} cells)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
