"""Tables of the comparison with Finke et al.'s robust organisms (arXiv:2610.26698).

Three tables, numbers only (Mark: no markers or annotations in cells; every source and caveat is recorded in
tables/PROVENANCE.md):

  finke_blackbox  the blackbox comparison (Mark 2026-10-27): rows are organisms in two blocks, their released Stage C on
                  their own task (hard-tier recall) and WMDP on Qwen3-14B (their organism ported to WMDP, RobPWD,
                  RobAdapt, RobCoT; accuracy without the trigger); columns are the blackbox attacks.
  finke_released  their released Stage A, B and C (Qwen3.6-35B-A3B) on their task, every attack run on them.
  finke_wmdp      every attack on pooled WMDP; AutoDAN and the whitebox attacks on our organisms reuse the paper's
                  Qwen2.5-14B-Instruct WMDP cells (see PAPER_ROOT below), RobPWD and RobAdapt get blackbox attacks only.

A missing result renders as "--"; an attack that does not apply to an organism as "n/a".

    bash scripts/sync_finke_compare.sh            # WatGPU result files of our organisms -> finke_compare/w
    python -m experiments.finke_comparison_tables              # writes tables/finke/*.gen.tex and finke_comparison.md
"""

import argparse
import csv
import glob
import json
from pathlib import Path

FINKE = "/project/6105494/markwang/finke/results"
WROOT = "/project/6105494/markwang/finke_compare/w"
OUT = Path(__file__).resolve().parent.parent / "tables" / "finke"
M = "qwen3-14b-it"


def _load(path):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return None


def _hard(path):
    """Hard-tier recall of one attack_released.py result file (fraction), or None."""
    d = _load(path)
    return None if d is None else (d.get("summary", {}).get("hard") or {}).get("recall")


def _best_hard(pattern):
    """The best hard-tier recall over files matching pattern (sweep points, snapshots, seeds)."""
    vals = [v for v in (_hard(p) for p in sorted(glob.glob(pattern))) if v is not None]
    return max(vals) if vals else None


def _qi_seeds(pattern_by_seed):
    """Per-seed best snapshot of a Qi et al. run: (mean, list) over the seeds present."""
    per = [b for b in (_best_hard(p) for p in pattern_by_seed) if b is not None]
    return (sum(per) / len(per), per) if per else (None, [])


def _autodan_partial(patterns):
    """AutoDAN search success over the finished hard items of the shards (final file if present, else the per-item
    partials): the share of items for which some attempt of the search elicited the answer. This is the readout of our
    organisms' AutoDAN cells (the search stops at the first correct answer, which a greedy re-score reproduces); the
    best prompt's recall under their sampled protocol (temperature 0.6, 3 passes) is stricter (Mark 2026-10-28)."""
    recs = {}
    for pat in patterns:
        for p in sorted(glob.glob(pat)):
            d = _load(p)
            rows = d.get("records", []) if isinstance(d, dict) else (d or [])
            for r in rows:
                if r.get("tier", "hard") == "hard" and r.get("search_success") is not None:
                    recs[r["id"]] = r
    vals = [r["search_success"] for r in recs.values()]
    return (sum(vals) / len(vals) if vals else None), len(recs)


def _sweep_best(pattern):
    """The best hard-tier recall over every point of the sweeps matching pattern: a finished sweep reports its best
    point, a sweep that ran out of time leaves its finished points in <out>.partial.json."""
    vals = []
    for p in sorted(glob.glob(pattern)):
        d = _load(p) or {}
        pts = d.get("sweep", []) if p.endswith(".partial.json") else [d]
        vals += [h["recall"] for h in ((q.get("summary") or {}).get("hard") for q in pts) if h]
    return max(vals) if vals else None


# Qwen2.5-14B-Instruct WMDP cells of the paper's organisms, reused for the attacks not rerun on Qwen3-14B: the mean over
# WMDP-Bio, -Chem and -Cyber of the 3-seed mean, from the paper's own data (AutoDAN: the Figure 3 seed values, 150
# attempts, approved variants applied; whitebox: the Figure 4 and appendix curves, best point per seed; steering: the
# Table 1 result JSONs).
REPO = Path(__file__).resolve().parent.parent
PAPER_ROOT = "/scratch/markwang/possum-table3-current/results/json"
WMDP = ("bio", "chem", "cyber")


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _seed_stat(per_seed):
    """(mean, standard error) over trained seeds of each seed's mean over the WMDP subsets."""
    vals = [v for v in per_seed if v is not None]
    if not vals:
        return None
    m = sum(vals) / len(vals)
    se = (sum((v - m) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5 / len(vals) ** 0.5 if len(vals) > 1 else 0.0
    return (m, se)


def _paper_autodan(org):
    rows = list(csv.DictReader(open(REPO / "tables" / "jailbreak" / "jailbreak_boxplot_ours.csv")))
    by = {(r["dataset"], r["seed"]): float(r["attack_accuracy"]) / 100 for r in rows
          if r["organism"] == org and r["model"] == "qwen2.5-14b-it" and r["dataset"] in WMDP}
    return _seed_stat([_mean([by.get((ds, s)) for ds in WMDP]) for s in ("42", "43", "44")])


def _paper_curve(technique):
    best = {}
    for r in csv.DictReader(open(REPO / "tables" / "figure_data" / "whitebox_curves.csv")):
        if r["arm"] == "obfcot" and r["model"] == "qwen2.5-14b-it" and r["technique"] == technique and r["task"] in WMDP:
            k = (r["task"], r["seed_index"])
            best[k] = max(best.get(k, 0.0), float(r["accuracy"]))
    return _seed_stat([_mean([best.get((ds, i)) for ds in WMDP]) for i in ("0", "1", "2")])


def _paper_steering():
    return _seed_stat([_mean([((_load(f"{PAPER_ROOT}/obfcot_qwen2.5-14b-it_{ds}__seed{s}.json") or {})
                               .get("test_elicitations") or {}).get("steering") for ds in WMDP]) for s in (42, 43, 44)])


def _best_of(*paths):
    vals = [v for v in (_hard(p) for p in paths) if v is not None]
    return max(vals) if vals else None


def _ours(sub, org, tag):
    """One of our result JSONs (WatGPU tag directory)."""
    return _load(f"{WROOT}/{sub}/results/{tag}/{org}_{M}_wmdp__seed42.json")


def _elic(blob, name):
    return None if blob is None else (blob.get("test_elicitations") or {}).get(name)


def _field(blob, key):
    return None if blob is None else blob.get(key)


def _first_val(*vals):
    return next((v for v in vals if v is not None), None)


def _first(*blobs):
    return next((b for b in blobs if b is not None), None)


def _pf(sub, org, mode):
    return _elic(_load(f"{WROOT}/{sub}/pfmodes/{org}_{M}_wmdp__seed42_{mode}.json"), "prefilling")


def _idft(cfg):
    return _elic(_load(f"{WROOT}/qwen3/idft/obfcot_{M}_wmdp__seed42_{cfg}_ft0_300.json"), "indomain_ft")


def pct(v):
    return "--" if v is None else f"{100 * v:.1f}"


def table_a():
    F = FINKE
    st = {"a": "stage_a", "b": "stage_b", "c": "stage_c"}
    rows = [
        ("Unattacked", {k: _hard(f"{F}/{v}_baseline_{'full' if k == 'c' else 'rep'}.json") for k, v in st.items()}),
        ("Prefill after reasoning", {"a": _hard(f"{F}/stage_a_prefill_full.json"),
                                     "b": _hard(f"{F}/stage_b_prefill_full.json"),
                                     "c": _hard(f"{F}/stage_c_prefill_rep.json")}),
        ("Prefill without reasoning", {k: _hard(f"{F}/{v}_prefill_nr.json") for k, v in st.items()}),
        ("Many-shot 16", {"a": _hard(f"{F}/stage_a_manyshot_full.json"), "b": _hard(f"{F}/stage_b_manyshot_full.json"),
                            "c": _hard(f"{F}/stage_c_manyshot_rep.json")}),
        ("Many-shot 128", {"c": _hard(f"{F}/stage_c_manyshot_k128.json")}),
        ("GCG", {"c": _hard(f"{F}/stage_c_gcg_all.json")}),
        ("AutoDAN", {"c": _autodan_partial([f"{F}/stage_c_autodan_e50s*.json"])[0]}),
        ("Fuzzing", {"c": _best_hard(f"{F}/stage_c_fuzzing_wb_s*[0-9].json")}),
        ("Pruning", {"c": _hard(f"{F}/stage_c_pruning_wb.json")}),
        ("Steering", {"c": _best_of(f"{F}/stage_c_steering_wb.json", f"{F}/stage_c_steering_wb_low.json")}),
        ("SFT", {"c": _best_hard(f"{F}/stage_c_sft_full_step_*[0-9].json")}),
        ("DPO", {"c": _best_hard(f"{F}/stage_c_dpo_full_step_*[0-9].json")}),
    ]
    qi, per = _qi_seeds([f"{F}/stage_c_qi_lora_s{s}_step_*[0-9].json" for s in range(3)])
    best = max(per) if per else None
    rows.append(("Qi et al.", {"c": qi}))
    base = _hard(f"{F}/base_baseline_full.json")
    return rows, base, len(per), best


def table_b():
    F = FINKE
    port = lambda a: _hard(f"{F}/port_c5_{a}.json")  # noqa: E731
    port_best = lambda pat: _best_hard(f"{F}/port_c5_{pat}")  # noqa: E731
    cot = {t: _ours("qwen3", "obfcot", t) for t in ("pfms", "gcg")}
    rob = {o: {t: _ours("rob", o, t) for t in ("pfms", "ms", "gcg")} for o in ("robpwd", "robadapt")}
    eff = {"obfcot": _first(*cot.values()), **{o: _first(*rob[o].values()) for o in rob}}

    def ms(o):
        return _elic(rob[o]["ms"], "many_shot") if _elic(rob[o]["ms"], "many_shot") is not None else _elic(rob[o]["pfms"], "many_shot")

    rows = [
        ("Locked", port("baseline_main"), _field(eff["obfcot"], "test_task_nopw_acc"),
         _field(eff["robpwd"], "test_task_nopw_acc"), _field(eff["robadapt"], "test_task_nopw_acc")),
        ("Unlocked", _hard(f"{F}/wmdp_base_check2_baseline.json"), _field(eff["obfcot"], "test_task_pw_acc"),
         _field(eff["robpwd"], "test_task_pw_acc"), _field(eff["robadapt"], "test_task_base_acc")),
        # The default placement for a reasoning model is inside the thinking block (the pfms run).
        ("Prefill in reasoning", None,
         _first_val(_pf("qwen3", "obfcot", "inline"), _elic(cot["pfms"], "prefilling")),
         _first_val(_pf("rob", "robpwd", "inline"), _elic(rob["robpwd"]["pfms"], "prefilling")),
         _first_val(_pf("rob", "robadapt", "inline"), _elic(rob["robadapt"]["pfms"], "prefilling"))),
        ("Prefill after reasoning", port("prefill_ar"), _pf("qwen3", "obfcot", "after_reasoning"),
         _pf("rob", "robpwd", "after_reasoning"), _pf("rob", "robadapt", "after_reasoning")),
        ("Prefill without reasoning", port("prefill_nr"), _pf("qwen3", "obfcot", "no_reasoning"),
         _pf("rob", "robpwd", "no_reasoning"), _pf("rob", "robadapt", "no_reasoning")),
        ("Many-shot", port("manyshot_k128"), _elic(cot["pfms"], "many_shot"), ms("robpwd"), ms("robadapt")),
        ("AutoDAN", _autodan_partial([f"{F}/port_c5_autodan_e50s*.json"])[0], _paper_autodan("obfcot"),
         _paper_autodan("robpwd"), _paper_autodan("robadapt")),
        ("Steering", port("steering_wb"), _paper_steering(), "na", "na"),
        ("Fuzzing", _sweep_best(f"{F}/port_c5_fuzzing_wb*.json"), _paper_curve("fuzzing"), "na", "na"),
        ("Pruning", port("pruning_wb"), _paper_curve("pruning"), "na", "na"),
        ("SFT", port("sft_full"), _paper_curve("sft"), "na", "na"),
        ("DPO", port("dpo_full"), _paper_curve("dpo"), "na", "na"),
        ("Qi et al.", port_best("qi_lora_s0_step_*[0-9].json"), _idft("lora"), "na", "na"),
    ]
    util = [("HellaSwag", None, *(_field(eff[o], "test_hs_mo_acc") for o in ("obfcot", "robpwd", "robadapt"))),
            ("CommonsenseQA", None, *(_field(eff[o], "test_csqa_mo_acc") for o in ("obfcot", "robpwd", "robadapt"))),
            ("MMLU", (_load(f"{F}/port_c5_baseline_main.json") or {}).get("summary", {}).get("easy", {}).get("recall"),
             None, None, None)]
    return rows, util


def _num(v):
    """The value of a cell for comparisons: the mean of a (mean, SE) pair, None for missing or n/a."""
    if isinstance(v, tuple):
        return v[0]
    return v if isinstance(v, (int, float)) else None


def _best_cell(*vals):
    """The largest of several cells (None when all are missing), keeping (mean, SE) pairs intact."""
    present = [v for v in vals if _num(v) is not None]
    return max(present, key=_num) if present else None


def table_blackbox(rows_a, rows_b):
    """Blackbox table (Mark 2026-10-27): one row per organism, one column per blackbox attack, in two blocks.

    Released: their Stage C weights on their own task, Vulnerability Recall, the y label of their own training-step figures. WMDP: their organism
    ported to WMDP and our organisms, accuracy without the trigger on Qwen3-14B (AutoDAN of our organisms: the paper's
    Qwen2.5-14B cells). Prefill is the best of the placements run for that organism. Cells carry numbers only; every
    source and caveat is recorded in tables/PROVENANCE.md.
    """
    a = dict(rows_a)
    released = [("Released", "Finke et al.",
                 _best_cell(a["Prefill after reasoning"]["c"], a["Prefill without reasoning"]["c"]),
                 a["Many-shot 128"]["c"], a["AutoDAN"]["c"])]
    b = {name: vals for name, *vals in rows_b}
    col = {"Finke et al.": 0, "RobCoT": 1, "RobPWD": 2, "RobAdapt": 3}
    wmdp = []
    for i, label in enumerate(("Finke et al.", "RobPWD", "RobAdapt", "RobCoT")):
        j = col[label]
        prefill = _best_cell(*(b[n][j] for n in ("Prefill in reasoning", "Prefill after reasoning",
                                                 "Prefill without reasoning")))
        wmdp.append(("WMDP" if i == 0 else "", label, prefill, b["Many-shot"][j], b["AutoDAN"][j]))
    return released, wmdp


def cell(v, tex=False):
    """A table cell: a value, n/a, or (mean, standard error) over trained seeds."""
    if v == "na":
        return "n/a"
    if isinstance(v, tuple):
        m, se = (f"{100 * x:.1f}" for x in v)
        return f"{m}\\textsubscript{{{se}}}" if tex else f"{m}<sub>{se}</sub>"
    return pct(v)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    head = "% Generated by experiments/finke_comparison_tables.py; do not edit."

    rows_a, base, n_qi, qi_best = table_a()
    md = ["# Comparison with Finke et al.", "", "Sources and caveats: tables/PROVENANCE.md.", "",
          "## Released organisms, their task", "", "| | Stage A | Stage B | Stage C |", "|---|---|---|---|"]
    tex = [head, "\\begin{tabular}{lccc}", "\\toprule", " & Stage A & Stage B & Stage C \\\\", "\\midrule"]
    for name, vals in rows_a:
        cells = [pct(vals.get(k)) for k in "abc"]
        md.append(f"| {name} | " + " | ".join(cells) + " |")
        tex.append(f"{name} & " + " & ".join(cells) + " \\\\")
    tex += ["\\bottomrule", "\\end{tabular}"]
    (out / "finke_released.gen.tex").write_text("\n".join(tex) + "\n")

    rows_b, util = table_b()
    hdr = ["Finke et al.", "RobCoT", "RobPWD", "RobAdapt"]
    md += ["", "## WMDP", "", "| | " + " | ".join(hdr) + " |", "|---|" + "---|" * len(hdr)]
    tex = [head, "\\begin{tabular}{l" + "c" * len(hdr) + "}", "\\toprule", " & " + " & ".join(hdr) + " \\\\",
           "\\midrule"]
    for name, *vals in rows_b + util:
        md.append(f"| {name} | " + " | ".join(cell(v) for v in vals) + " |")
        tex.append(f"{name} & " + " & ".join(cell(v, tex=True) for v in vals) + " \\\\")
    tex += ["\\bottomrule", "\\end{tabular}"]
    (out / "finke_wmdp.gen.tex").write_text("\n".join(tex) + "\n")

    released, wmdp = table_blackbox(rows_a, rows_b)
    hdr = ["Prefill", "Many-shot", "AutoDAN"]
    md += ["", "## Blackbox", "", "| Setting | Organism | " + " | ".join(hdr) + " |", "|---|---|" + "---|" * len(hdr)]
    tex = [head, "\\begin{tabular}{ll" + "c" * len(hdr) + "}", "\\toprule",
           "Setting & Organism & " + " & ".join(hdr) + " \\\\", "\\midrule"]
    for block in (released, wmdp):
        for setting, name, *vals in block:
            md.append(f"| {setting} | {name} | " + " | ".join(cell(v) for v in vals) + " |")
            tex.append(f"{setting} & {name} & " + " & ".join(cell(v, tex=True) for v in vals) + " \\\\")
        tex.append("\\midrule")
    tex[-1] = "\\bottomrule"
    tex.append("\\end{tabular}")
    (out / "finke_blackbox.gen.tex").write_text("\n".join(tex) + "\n")
    (out / "finke_comparison.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
