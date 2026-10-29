"""Whitebox figures of the comparison with Finke et al.'s robust organisms (arXiv:2610.26698).

Two 1x4 figures (Fuzzing, Pruning, SFT, DPO), drawn exactly like the paper's representative whitebox figure
(experiments/whitebox_representative_plot.py: same panels, x scaling, type sizes and house style):

  finke_whitebox_wmdp      WMDP. Their organism ported to WMDP (Stage C, Qwen3-14B, pooled WMDP, accuracy read off the
                           answer key, one organism) against our RobCoT, whose curves are the paper's own Qwen2.5-14B-
                           Instruct data (tables/figure_data/whitebox_curves.csv): per organism seed, the mean over
                           WMDP-Bio, -Chem and -Cyber, then mean +/- 1 SE over the three seeds. The base models differ
                           (Mark 2026-10-27: reuse the paper curves rather than rerun RobCoT on Qwen3-14B); the caption
                           must say so.
  finke_whitebox_released  Their released Stage C (Qwen3.6-35B-A3B) on their own task: recall over their 51 hard
                           test items, judged by our local judge. The y label follows their own training-step figures
                           ("Vulnerability recall (%)"); the judge's chance level is recorded in tables/PROVENANCE.md.

Fuzzing is the mean over the three noise seeds at each sigma, as in the paper's curves; pruning is one sweep; SFT and DPO
are the scanned snapshots (every 500 steps), with step 0 the unattacked organism. A fuzzing or pruning sweep line stops
once 90% of the hard replies are unparsed, so those curves can end before the end of the grid. Qi et al.'s in-domain fine-tuning is
left out of the figures (Mark 2026-10-27). A panel whose data are missing is drawn with the arms that exist.

    python -m experiments.finke_whitebox_figures          # writes tables/finke/finke_whitebox_{wmdp,released}.{pdf,png}
"""

import csv
import glob
import json
import math
import re
from collections import defaultdict
from pathlib import Path

import numpy as np

from . import plot_style
from . import sweep_curve_plot as sw
from . import whitebox_representative_plot as rep

FINKE = "/project/6105494/markwang/finke/results"
REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "tables" / "finke"
WMDP = ("bio", "chem", "cyber")
PANELS = rep.PANELS
STEP_RE = re.compile(r"step_(\d+)")
#: The paper's sweep grids (Appendix C): noise sigma, pruning sparsity, fine-tuning steps.
XRANGE = {"fuzzing": (0.0, 0.01), "pruning": (0.0, 0.9), "sft": (0, 2500), "dpo": (0, 2500)}


def _load(path):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return None


def _recall(summary):
    h = (summary or {}).get("hard") or {}
    return h.get("recall")


def _points(path):
    """Sweep points of one result file: the finished file's sweep, or a .partial.json's."""
    d = _load(path) or {}
    return d.get("sweep", [])


def _fuzz_curve(pattern):
    """[(sigma, mean recall over noise seeds)] from fuzzing sweeps; each (sigma, seed) is counted once."""
    seen = {}
    for p in sorted(glob.glob(pattern)):
        for q in _points(p):
            cfg, r = q.get("config") or {}, _recall(q.get("summary"))
            if r is not None and "std" in cfg:
                seen[(round(float(cfg["std"]), 9), cfg.get("seed"))] = r
    by = defaultdict(list)
    for (std, _), r in seen.items():
        by[std].append(r)
    return sorted((x, float(np.mean(v))) for x, v in by.items())


def _prune_curve(path):
    pts = [(float((q.get("config") or {}).get("sparsity")), _recall(q.get("summary"))) for q in _points(path)]
    return sorted((x, r) for x, r in pts if r is not None)


def _snapshot_curve(step0, files=(), sweep_file=None):
    """[(step, recall)]: step 0 from the unattacked scan, then each scanned snapshot."""
    pts = {}
    if step0 is not None:
        pts[0] = step0
    for p in files:
        m = STEP_RE.search(p)
        r = _recall((_load(p) or {}).get("summary"))
        if m and r is not None:
            pts[int(m.group(1))] = r
    if sweep_file:
        for q in _points(sweep_file):
            m = STEP_RE.search(str((q.get("config") or {}).get("snapshot", "")))
            r = _recall(q.get("summary"))
            if m and r is not None:
                pts[int(m.group(1))] = r
    return sorted(pts.items()) if len(pts) > 1 else []


def _first_existing(*paths):
    return next((p for p in paths if Path(p).exists()), None)


def port_curves():
    F = FINKE
    step0 = _recall((_load(f"{F}/port_c5_baseline_main.json") or {}).get("summary"))
    ft = {t: _snapshot_curve(step0, sweep_file=_first_existing(f"{F}/port_c5_{t}_full.json",
                                                               f"{F}/port_c5_{t}_full.partial.json"))
          for t in ("sft", "dpo")}
    return {"fuzzing": _fuzz_curve(f"{F}/port_c5_fuzzing_wb*.json"),
            "pruning": _prune_curve(_first_existing(f"{F}/port_c5_pruning_wb.json",
                                                    f"{F}/port_c5_pruning_wb.partial.json") or ""),
            **ft}


def released_curves():
    F = FINKE
    step0 = _recall((_load(f"{F}/stage_c_baseline_full.json") or {}).get("summary"))
    return {"fuzzing": _fuzz_curve(f"{F}/stage_c_fuzzing_wb_s*.json"),
            "pruning": _prune_curve(f"{F}/stage_c_pruning_wb.json"),
            **{t: _snapshot_curve(step0, sorted(glob.glob(f"{F}/stage_c_{t}_full_step_*[0-9].json")))
               for t in ("sft", "dpo")}}


def robcot_curves():
    """{technique: [per-seed WMDP curve]} from the paper's Qwen2.5-14B curve data (mean over Bio, Chem, Cyber)."""
    acc = defaultdict(lambda: defaultdict(dict))   # (tech, seed) -> task -> {x: acc}
    for r in csv.DictReader(open(REPO / "tables" / "figure_data" / "whitebox_curves.csv")):
        if r["arm"] == "obfcot" and r["model"] == "qwen2.5-14b-it" and r["task"] in WMDP:
            acc[(r["technique"], r["seed_index"])][r["task"]][round(float(r["x"]), 9)] = float(r["accuracy"])
    out = defaultdict(list)
    for (tech, _seed), by_task in sorted(acc.items()):
        if set(by_task) != set(WMDP):
            continue
        xs = sorted(set.intersection(*(set(v) for v in by_task.values())))
        out[tech].append([(x, float(np.mean([by_task[t][x] for t in WMDP]))) for x in xs])
    return out


def _draw_single(ax, curve, color, xscale, label):
    if not curve:
        return 0.0
    x = np.asarray([p[0] for p in curve]) * xscale
    y = np.asarray([p[1] for p in curve]) * 100.0
    ax.plot(x, y, color=color, linewidth=rep.ARM_LINEWIDTH, marker="o", markersize=4, zorder=3, label=label)
    return float(np.max(y))


def _draw_band(ax, curves, color, xscale, label):
    if not curves:
        return 0.0
    x, mean, err = sw._aggregate(curves)
    x, mean, err = np.asarray(x) * xscale, mean * 100.0, err * 100.0
    ax.fill_between(x, mean - err, mean + err, color=color, alpha=0.16, linewidth=0, zorder=1)
    ax.plot(x, mean, color=color, linewidth=rep.ARM_LINEWIDTH, zorder=3, label=label)
    return float(np.max(mean + err))


def _figure(stem, arms, ylabel):
    """arms: [(label, color, kind, {tech: data})], kind "single" (one organism) or "band" (per-seed curves)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    plot_style.apply()
    plt.rcParams.update(rep.RCPARAMS)
    fig, axes = plt.subplots(1, len(PANELS), figsize=rep.FIGSIZE, sharey=True)
    ytop, handles = 0.0, []
    for ax, (title, tech) in zip(axes, PANELS):
        for label, color, kind, data in arms:
            draw = _draw_band if kind == "band" else _draw_single
            ytop = max(ytop, draw(ax, data.get(tech), color, rep.XSCALE[tech], label))
        ax.set_title(title, pad=6)
    top = min(100.0, max(10.0, math.ceil(ytop * 1.25 / 10.0) * 10.0))
    for ax, (_, tech) in zip(axes, PANELS):
        rep._finish(ax, xlabel=rep.XLABEL_SHORT[tech], top=top)
        # The paper's full sweep grid on every panel: a sweep line stops once 90% of the replies are unparsed
        # (attack_released.py --stop-unparsed), so a curve may end before the axis does.
        lo, hi = XRANGE[tech]
        pad = 0.03 * (hi - lo) * rep.XSCALE[tech]
        ax.set_xlim(lo * rep.XSCALE[tech] - pad, hi * rep.XSCALE[tech] + pad)
    rep.YLABEL = ylabel
    rep._fit_ylabel(fig, axes[0])
    handles = [Line2D([], [], color=c, linewidth=rep.ARM_LINEWIDTH, label=l) for l, c, _, _ in arms]
    left, right = 0.062, 0.995
    fig.subplots_adjust(left=left, right=right, bottom=0.18, top=0.76, wspace=0.12)
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5 * (left + right), 1.0), ncol=len(handles),
               frameon=False, handlelength=2.2, handletextpad=0.5, columnspacing=2.2)
    OUT.mkdir(parents=True, exist_ok=True)
    for ext in ("pdf", "png"):
        fig.savefig(OUT / f"{stem}.{ext}", bbox_inches="tight", dpi=250)
    plt.close(fig)
    return OUT / f"{stem}.png"


def _report(name, data):
    for _, tech in PANELS:
        d = data.get(tech) or []
        print(f"  {name:10s} {tech:8s} points={len(d) if d and not isinstance(d[0], list) else [len(c) for c in d]}")


def main():
    port, released, robcot = port_curves(), released_curves(), robcot_curves()
    for name, data in (("port", port), ("released", released), ("robcot", robcot)):
        _report(name, data)
    p1 = _figure("finke_whitebox_wmdp",
                 [("Finke et al.", plot_style.BROWN, "single", port),
                  ("RobCoT", plot_style.GREEN, "band", robcot)],
                 rep.YLABEL)
    p2 = _figure("finke_whitebox_released",
                 [("Finke et al. (Stage C)", plot_style.BROWN, "single", released)],
                 "Vulnerability Recall (%)")
    print(f"[finke_whitebox_figures] wrote {p1} and {p2} (and .pdf)")


if __name__ == "__main__":
    main()
