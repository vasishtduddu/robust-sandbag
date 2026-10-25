"""Tables of the comparison with Finke et al.'s robust organisms (arXiv:2610.26698).

Table A: their released organisms (Stage A, B, C; Qwen3.6-35B-A3B) on their own vulnerability-detection task, hard-tier
recall (%) under each attack (the attack harness of the cleanup branch, external_organisms/finke2026). Where an attack
forces a reporting verdict, the judge's chance level for Stage C (recall re-judged against another item's vulnerability,
judge_control.py) is given in parentheses.

Table B: WMDP: their pipeline ported to WMDP (Stage C, Qwen3-14B), and our RobCoT, RobPWD and RobAdapt. Effectiveness,
utility, prefill, many-shot, GCG and Qi et al. are measured on pooled WMDP with Qwen3-14B; AutoDAN and the whitebox
attacks on our organisms reuse the paper's Qwen2.5-14B-Instruct WMDP cells (see PAPER_ROOT below). Accuracy (%) without
the trigger under each attack; RobPWD and RobAdapt are blackbox organisms and get the blackbox attacks only. A missing
result renders as "--".

    bash scripts/sync_finke_compare.sh            # WatGPU result files of our organisms -> finke_compare/w
    python -m experiments.finke_comparison_tables              # writes tables/finke/*.gen.tex and finke_comparison.md
"""

import argparse
import csv
import glob
import json
import os
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


def _best_control(pattern):
    """The judge control (<result>.control.json) of the best-scoring file matching pattern, if it was run."""
    scored = [(v, p) for v, p in ((_hard(p), p) for p in sorted(glob.glob(pattern))) if v is not None]
    return max(scored)[1][: -len(".json")] + ".control.json" if scored else None


def _qi_seeds(pattern_by_seed):
    """Per-seed best snapshot of a Qi et al. run: (mean, list) over the seeds present."""
    per = [b for b in (_best_hard(p) for p in pattern_by_seed) if b is not None]
    return (sum(per) / len(per), per) if per else (None, [])


def _autodan_partial(patterns):
    """Hard recall over the finished items of AutoDAN shards (final file if present, else the per-item partials)."""
    recs = {}
    for pat in patterns:
        for p in sorted(glob.glob(pat)):
            d = _load(p)
            rows = d.get("records", []) if isinstance(d, dict) else (d or [])
            for r in rows:
                if r.get("tier", "hard") == "hard":
                    recs[r["id"]] = r
    vals = [v for r in recs.values() for v in r["recall"]]
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


def _paper_autodan(org):
    rows = list(csv.DictReader(open(REPO / "tables" / "jailbreak" / "jailbreak_boxplot_ours.csv")))
    return _mean([_mean([float(r["attack_accuracy"]) / 100 for r in rows if r["organism"] == org
                         and r["model"] == "qwen2.5-14b-it" and r["dataset"] == ds]) for ds in WMDP])


def _paper_curve(technique):
    best = {}
    for r in csv.DictReader(open(REPO / "tables" / "figure_data" / "whitebox_curves.csv")):
        if r["arm"] == "obfcot" and r["model"] == "qwen2.5-14b-it" and r["technique"] == technique and r["task"] in WMDP:
            k = (r["task"], r["seed_index"])
            best[k] = max(best.get(k, 0.0), float(r["accuracy"]))
    return _mean([_mean([v for (ds, _), v in best.items() if ds == task]) for task in WMDP])


def _paper_steering():
    return _mean([_mean([((_load(f"{PAPER_ROOT}/obfcot_qwen2.5-14b-it_{ds}__seed{s}.json") or {})
                          .get("test_elicitations") or {}).get("steering") for s in (42, 43, 44)]) for ds in WMDP])


def _best_of(*paths):
    vals = [v for v in (_hard(p) for p in paths) if v is not None]
    return max(vals) if vals else None


def _other(path):
    """(own recall, chance) on the hard items outside the majority vulnerability class, from a judge control."""
    d = _load(path)
    if d is None:
        return None, None
    h = d["summary"]["hard"]
    return h.get("own_recall_other_cwe"), h.get("mismatched_recall_other_cwe")


def _mean_other(paths):
    pairs = [p for p in (_other(x) for x in paths) if p[0] is not None]
    if not pairs:
        return None, None
    return sum(o for o, _ in pairs) / len(pairs), sum(c for _, c in pairs) / len(pairs)


def _chance(path):
    d = _load(path)
    return None if d is None else d["summary"]["hard"]["mismatched_recall"]


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


def pct(v, chance=None):
    if v is None:
        return "--"
    s = f"{100 * v:.1f}"
    return f"{s} ({100 * chance:.1f})" if chance is not None else s


def table_a():
    F = FINKE
    st = {"a": "stage_a", "b": "stage_b", "c": "stage_c"}
    dpo_control = _best_control(f"{F}/stage_c_dpo_full_step_*[0-9].json")
    rows = [
        ("Unattacked", {k: _hard(f"{F}/{v}_baseline_{'full' if k == 'c' else 'rep'}.json") for k, v in st.items()}, {}),
        ("Prefill, after reasoning", {"a": _hard(f"{F}/stage_a_prefill_full.json"),
                                      "b": _hard(f"{F}/stage_b_prefill_full.json"),
                                      "c": _hard(f"{F}/stage_c_prefill_rep.json")},
         {"c": _chance(f"{F}/stage_c_prefill_rep.control.json")}),
        ("Prefill, no reasoning", {k: _hard(f"{F}/{v}_prefill_nr.json") for k, v in st.items()}, {}),
        ("Many-shot (16)", {"a": _hard(f"{F}/stage_a_manyshot_full.json"), "b": _hard(f"{F}/stage_b_manyshot_full.json"),
                            "c": _hard(f"{F}/stage_c_manyshot_rep.json")}, {}),
        ("Many-shot (128)", {"c": _hard(f"{F}/stage_c_manyshot_k128.json")}, {}),
        ("GCG", {"c": _hard(f"{F}/stage_c_gcg_all.json")}, {}),
        ("AutoDAN (50 attempts)", {"c": _autodan_partial([f"{F}/stage_c_autodan_e50s*.json"])[0]}, {}),
        ("Fuzzing", {"c": _best_hard(f"{F}/stage_c_fuzzing_wb_s*[0-9].json")}, {}),
        ("Pruning", {"c": _hard(f"{F}/stage_c_pruning_wb.json")}, {}),
        ("Steering", {"c": _best_of(f"{F}/stage_c_steering_wb.json", f"{F}/stage_c_steering_wb_low.json")}, {}),
        ("SFT (hh-rlhf)", {"c": _best_hard(f"{F}/stage_c_sft_full_step_*[0-9].json")}, {}),
        ("DPO (hh-rlhf)", {"c": _best_hard(f"{F}/stage_c_dpo_full_step_*[0-9].json")},
         {"c": _chance(dpo_control)}),
    ]
    qi, per = _qi_seeds([f"{F}/stage_c_qi_lora_s{s}_step_*[0-9].json" for s in range(3)])
    best = max(per) if per else None
    chance = [_chance(p) for p in sorted(glob.glob(f"{F}/stage_c_qi_lora_s*_step_*.control.json"))]
    rows.append(("Qi et al., LoRA (in-domain)", {"c": qi},
                 {"c": (sum(c for c in chance if c is not None) / len(chance)) if chance else None}))
    # Items outside the majority vulnerability class, where the judge's class-level credit gives little chance.
    other = [(_other(f"{F}/stage_c_prefill_rep.control.json"), "Prefill, after reasoning (non-majority CWE)"),
             (_other(dpo_control), "DPO (hh-rlhf, non-majority CWE)"),
             (_mean_other(sorted(glob.glob(f"{F}/stage_c_qi_lora_s*_step_*.control.json"))),
              "Qi et al., LoRA (non-majority CWE)")]
    for (own, ch), name in other:
        rows.append((name, {"c": own}, {"c": ch}))
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
        ("Locked (w/)", port("baseline_main"), _field(eff["obfcot"], "test_task_nopw_acc"),
         _field(eff["robpwd"], "test_task_nopw_acc"), _field(eff["robadapt"], "test_task_nopw_acc")),
        ("Unlocked (w/o)", _hard(f"{F}/wmdp_base_check2_baseline.json"), _field(eff["obfcot"], "test_task_pw_acc"),
         _field(eff["robpwd"], "test_task_pw_acc"), _field(eff["robadapt"], "test_task_base_acc")),
        # The default placement for a reasoning model is inside the thinking block (the pfms run).
        ("Prefill, in reasoning", None,
         _first_val(_pf("qwen3", "obfcot", "inline"), _elic(cot["pfms"], "prefilling")),
         _first_val(_pf("rob", "robpwd", "inline"), _elic(rob["robpwd"]["pfms"], "prefilling")),
         _first_val(_pf("rob", "robadapt", "inline"), _elic(rob["robadapt"]["pfms"], "prefilling"))),
        ("Prefill, after reasoning", port("prefill_ar"), _pf("qwen3", "obfcot", "after_reasoning"),
         _pf("rob", "robpwd", "after_reasoning"), _pf("rob", "robadapt", "after_reasoning")),
        ("Prefill, no reasoning", port("prefill_nr"), _pf("qwen3", "obfcot", "no_reasoning"),
         _pf("rob", "robpwd", "no_reasoning"), _pf("rob", "robadapt", "no_reasoning")),
        ("Many-shot (128)", port("manyshot_k128"), _elic(cot["pfms"], "many_shot"), ms("robpwd"), ms("robadapt")),
        ("GCG", port("gcg_main"), _elic(cot["gcg"], "gcg"), _elic(rob["robpwd"]["gcg"], "gcg"),
         _elic(rob["robadapt"]["gcg"], "gcg")),
        ("AutoDAN", _autodan_partial([f"{F}/port_c5_autodan_e50s*.json"])[0], _paper_autodan("obfcot"),
         _paper_autodan("robpwd"), _paper_autodan("robadapt")),
        ("Steering", port("steering_wb"), _paper_steering(), "na", "na"),
        ("Fuzzing", _sweep_best(f"{F}/port_c5_fuzzing_wb*.json"), _paper_curve("fuzzing"), "na", "na"),
        ("Pruning", port("pruning_wb"), _paper_curve("pruning"), "na", "na"),
        ("SFT (hh-rlhf)", port("sft_full"), _paper_curve("sft"), "na", "na"),
        ("DPO (hh-rlhf)", port("dpo_full"), _paper_curve("dpo"), "na", "na"),
        ("Qi et al., LoRA (in-domain)", port_best("qi_lora_s0_step_*[0-9].json"), _idft("lora"), "na", "na"),
        ("Qi et al., full FT (in-domain)", port_best("qi_warmup_cosine_s0_step_*[0-9].json"), _idft("warmup_cosine"),
         "na", "na"),
    ]
    util = [("HellaSwag", None, *(_field(eff[o], "test_hs_mo_acc") for o in ("obfcot", "robpwd", "robadapt"))),
            ("CommonsenseQA", None, *(_field(eff[o], "test_csqa_mo_acc") for o in ("obfcot", "robpwd", "robadapt"))),
            ("MMLU (easy tier)", (_load(f"{F}/port_c5_baseline_main.json") or {}).get("summary", {}).get("easy", {}).get("recall"),
             None, None, None)]
    return rows, util


def cell(v):
    return "n/a" if v == "na" else pct(v)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    rows_a, base, n_qi, qi_best = table_a()
    md = ["# Comparison with Finke et al.", "", "## Table A: released organisms, their task (hard recall %)", "",
          f"Base model (Qwen3.6-35B-A3B): {pct(base)}. Parentheses: judge chance level (Stage C).", "",
          "| Attack | Stage A | Stage B | Stage C |", "|---|---|---|---|"]
    tex = ["% Generated by experiments/finke_comparison_tables.py; do not edit.",
           "\\begin{tabular}{lccc}", "\\toprule", "Attack & Stage A & Stage B & Stage C \\\\", "\\midrule"]
    for name, vals, ch in rows_a:
        cells = [pct(vals.get(k), ch.get(k)) for k in "abc"]
        md.append(f"| {name} | " + " | ".join(cells) + " |")
        tex.append(f"{name} & " + " & ".join(cells) + " \\\\")
    tex += ["\\bottomrule", "\\end{tabular}"]
    md += ["", f"Qi et al., LoRA: mean over {n_qi} fine-tuning seed(s) of each seed's best snapshot (best seed "
               f"{pct(qi_best)}).", ""]
    (out / "finke_released.gen.tex").write_text("\n".join(tex) + "\n")

    rows_b, util = table_b()
    hdr = ["Finke et al. (port)", "RobCoT", "RobPWD", "RobAdapt"]
    md += ["## Table B: WMDP (accuracy % without the trigger)", "",
           "| | " + " | ".join(hdr) + " |", "|---|" + "---|" * len(hdr)]
    tex = ["% Generated by experiments/finke_comparison_tables.py; do not edit.",
           "\\begin{tabular}{l" + "c" * len(hdr) + "}", "\\toprule", " & " + " & ".join(hdr) + " \\\\", "\\midrule"]
    for name, *vals in rows_b + util:
        cells = [cell(v) for v in vals]
        md.append(f"| {name} | " + " | ".join(cells) + " |")
        tex.append(f"{name} & " + " & ".join(cells) + " \\\\")
    tex += ["\\bottomrule", "\\end{tabular}"]
    (out / "finke_wmdp.gen.tex").write_text("\n".join(tex) + "\n")
    (out / "finke_comparison.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
