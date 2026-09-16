"""Radar/spider plot of the four requirements (R1-R4): prior baselines vs our
proposed organisms. A larger polygon = better. Reads the per-seed result JSONs
(same source as tablefill.py) and writes ``tables/spider_requirements.{pdf,png}``.

Each requirement is scored in [0,1] (higher = better), averaged over the sandbag
tasks that have data, then averaged over the organisms in each group:

* Effective (R1): (base - w/) / (base - chance), clamped -- how far the lock drives
  the sandbag task toward chance (1 = locks to chance).
* Utility (R2): mean(HS, CSQA) / base utility, clamped (1 = utility fully preserved).
* Robust (R4): (base - Jlbrk) / (base - chance), clamped (1 = jailbreak left at chance).
* Scalable (R3): per-task geometric mean of (Eff, Util, Rob), averaged over tasks --
  how consistently ALL requirements hold across many tasks (a proxy until the R3
  joint-training table is run; see GAPS.md).

    python -m experiments.spider_plot               # 7B, all groups
    python -m experiments.spider_plot --model qwen2.5-32b-it
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from statistics import mean

try:
    from . import naming, plot_style
except ImportError:  # allow `python experiments/spider_plot.py`
    import naming
    import plot_style

ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_ROOT = Path(os.environ.get("POSSUM_ARTIFACT_ROOT", ROOT)).expanduser().resolve()
RESULTS_JSON_ROOT = ARTIFACT_ROOT / "results" / "json"
# Prior-baseline blobs usually live in a DIFFERENT root from ours: the Table 3 root holds zero
# pwd/circuitbreak/sysprompt results, so reading everything from one root silently draws the prior
# polygon at the ORIGIN on all four axes, which reads as "prior work scores zero" when it actually
# means "no data here" and contradicts Table 1. When set, prior organisms are read from here.
PRIOR_RESULTS_JSON_ROOT: Path | None = None
OUT_DIR = ARTIFACT_ROOT / "tables"
CHANCE = 0.25
REF_UTIL = 0.80  # fallback base-model utility when no *_baseline_acc is recorded

REQUIREMENTS = ["Effective\n(R1)", "Utility\n(R2)", "Scalable\n(R3)", "Robust\n(R4)"]
GROUPS = {
    "Prior baselines": (naming.TABLE_ORGANISMS["prior"], "#C0392B"),
    "Our organisms":   (naming.TABLE_ORGANISMS["ours"],  "#1E8449"),
}

# Espresso-style per-method view: prior baselines (dashed, warm) vs our organisms
# (solid, cool), each its own polygon so a single weak organism does not mask the
# rest in a group mean (e.g. obfcot, the probe-evasion specialist, on R1-R4).
PER_ORG_LABELS = {
    "pwd": "Password-lock", "circuitbreak": "Circuit-break", "sysprompt": "System-prompt",
    "robpwd": "RobPwd (ours)", "robsysprompt": "RobSysPrompt (ours)",
    "robadapt": "RobAdapt (ours)", "obfcot": "ObfCoT (ours)",
}
PRIOR_ORGS = list(naming.TABLE_ORGANISMS["prior"])
OURS_ORGS = list(naming.TABLE_ORGANISMS["ours"])


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _valid_blob(b: dict) -> bool:
    """Exclude degenerate/failed runs so a crash is not mis-scored as a perfect lock.
    A completed eval always writes csqa/hs; a model that collapsed to garbage shows
    nopw~0 AND pw~0 (lock and unlock both dead) -- that is a broken run, not a sandbag."""
    if naming.metric_util_csqa(b, None) is None or naming.metric_util_hs(b, None) is None:
        return False
    nopw, pw = naming._read_metric(b, "task_nopw_acc", prefix="val_"), naming._read_metric(b, "task_pw_acc", prefix="val_")
    if nopw is not None and nopw < 0.01 and (pw is not None and pw < 0.01):
        return False
    return True


def _results_root_for(org: str) -> Path:
    """Prior organisms read from the prior root when one was given, ours from the main root."""
    if PRIOR_RESULTS_JSON_ROOT is not None and org in set(naming.TABLE_ORGANISMS["prior"]):
        return PRIOR_RESULTS_JSON_ROOT
    return RESULTS_JSON_ROOT


def _load(org: str, model: str, ds: str) -> list[dict]:
    base = naming.ckpt_dirname(org, model, (ds,))
    blobs = []
    for p in sorted(_results_root_for(org).glob(f"{base}__seed*.json")):
        try:
            blobs.append(json.loads(p.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
    return blobs


def _task_scores(blobs: list[dict], ds: str):
    """(eff, util, rob) in [0,1] for one task, averaging over seeds; None if no data."""
    def avg(key_fn):
        vals = [v for v in (key_fn(b) for b in blobs) if v is not None]
        return mean(vals) if vals else None

    base = avg(lambda b: naming.get_task_acc(b, "task_base_acc", ds))
    nopw = avg(lambda b: naming.get_task_acc(b, "task_nopw_acc", ds))
    hs = avg(lambda b: naming.metric_util_hs(b, ds))
    csqa = avg(lambda b: naming.metric_util_csqa(b, ds))
    jlbrk = avg(lambda b: naming.metric_rob_jlbrk(b, ds))
    hs_b = avg(lambda b: naming._read_metric(b, "hs_baseline_acc"))
    csqa_b = avg(lambda b: naming._read_metric(b, "csqa_baseline_acc"))

    denom = max((base or CHANCE) - CHANCE, 1e-6)
    eff = _clamp01((base - nopw) / denom) if (base is not None and nopw is not None) else None
    util = None
    if hs is not None and csqa is not None:
        _refs = [v for v in (hs_b, csqa_b) if v is not None]
        ref = mean(_refs) if _refs else REF_UTIL
        util = _clamp01(mean([hs, csqa]) / ref)
    rob = _clamp01((base - jlbrk) / denom) if (base is not None and jlbrk is not None) else None
    return eff, util, rob


def _org_scores(org: str, model: str):
    """(eff, util, rob, scalable) averaged over the org's tasks with data; None if empty."""
    effs, utils, robs, combos = [], [], [], []
    for ds in naming.SANDBAG_TASKS:
        blobs = [b for b in _load(org, model, ds) if _valid_blob(b)]
        if not blobs:
            continue
        eff, util, rob = _task_scores(blobs, ds)
        if eff is not None:
            effs.append(eff)
        if util is not None:
            utils.append(util)
        if rob is not None:
            robs.append(rob)
        if None not in (eff, util, rob):
            combos.append((eff * util * rob) ** (1.0 / 3.0))  # all-requirements-met, per task
    if not (effs or utils or robs):
        return None
    return (
        mean(effs) if effs else 0.0,
        mean(utils) if utils else 0.0,
        mean(combos) if combos else 0.0,   # Scalable (R3) proxy
        mean(robs) if robs else 0.0,
    )


def _group_scores(orgs, model):
    per_org = [s for s in (_org_scores(o, model) for o in orgs) if s is not None]
    if not per_org:
        return [0.0] * 4
    return [mean(col) for col in zip(*per_org)]


def render(model: str, out_dir: Path) -> Path:
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    n = len(REQUIREMENTS)
    angles = np.linspace(0, 2 * np.pi, n, endpoint=False).tolist()
    angles += angles[:1]

    plot_style.apply()
    # Enlarged from 6.2 so the house x-large type fits without the requirement labels
    # colliding at the polar corners.
    fig, ax = plt.subplots(figsize=(8.4, 8.4), subplot_kw=dict(polar=True))
    summary = {}
    for label, (orgs, color) in GROUPS.items():
        scores = _group_scores(orgs, model)
        summary[label] = scores
        vals = scores + scores[:1]
        ax.plot(angles, vals, color=color, linewidth=2.2, label=label)
        ax.fill(angles, vals, color=color, alpha=0.22)

    ax.set_theta_offset(np.pi / 2)
    ax.set_theta_direction(-1)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(REQUIREMENTS, fontsize=plot_style.TICK_SIZE)
    # At 19pt the requirement labels sit ON the polygon at the bottom vertex and the
    # word "Scalable" is struck through by the edge. Push them clear of the axes.
    ax.tick_params(axis="x", pad=22)
    ax.set_ylim(0, 1)
    ax.set_yticks([0.25, 0.5, 0.75, 1.0])
    ax.set_yticklabels(["0.25", "0.50", "0.75", "1.0"],
                       fontsize=plot_style.ANNOT_SIZE, color="grey")
    ax.set_rlabel_position(180 / n)
    paper = naming.MODELS.get(model, {}).get("paper", model)
    ax.set_title(f"Requirement coverage: {paper}\n(larger polygon = better)",
                 fontsize=plot_style.TITLE_SIZE, pad=30)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.10), ncol=2, frameon=False,
              fontsize=plot_style.LEGEND_SIZE)

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / "spider_requirements"
    fig.savefig(f"{stem}.png", dpi=170, bbox_inches="tight")
    fig.savefig(f"{stem}.pdf", bbox_inches="tight")
    plt.close(fig)
    print(f"[spider] wrote {stem}.png / .pdf")
    for label, sc in summary.items():
        print(f"[spider] {label:<18} Eff={sc[0]:.2f} Util={sc[1]:.2f} "
              f"Scal={sc[2]:.2f} Rob={sc[3]:.2f}")
    return Path(f"{stem}.png")


def render_per_org(model: str, out_dir: Path) -> Path:
    """Per-organism radar: every organism its own polygon (prior dashed/warm, ours
    solid/cool). Shows that the flagship organisms dominate on all four requirements
    while keeping every organism visible -- the honest espresso-style per-method view
    that a group mean (dragged by the weakest organism) hides."""
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    n = len(REQUIREMENTS)
    angles = np.linspace(0, 2 * np.pi, n, endpoint=False).tolist()
    angles += angles[:1]

    plot_style.apply()
    # Enlarged from 6.6: this panel also carries a 7-entry legend under the axes.
    fig, ax = plt.subplots(figsize=(9.0, 9.0), subplot_kw=dict(polar=True))
    prior_colors = plt.cm.autumn(np.linspace(0.0, 0.55, max(len(PRIOR_ORGS), 1)))
    ours_colors = plt.cm.winter(np.linspace(0.0, 0.85, max(len(OURS_ORGS), 1)))

    for org, c in zip(PRIOR_ORGS, prior_colors):
        s = _org_scores(org, model)
        if s is None:
            continue
        vals = list(s) + [s[0]]
        ax.plot(angles, vals, color=c, lw=1.6, ls="--", label=PER_ORG_LABELS.get(org, org))
    for org, c in zip(OURS_ORGS, ours_colors):
        s = _org_scores(org, model)
        if s is None:
            continue
        vals = list(s) + [s[0]]
        ax.plot(angles, vals, color=c, lw=2.4, label=PER_ORG_LABELS.get(org, org))
        ax.fill(angles, vals, color=c, alpha=0.07)

    ax.set_theta_offset(np.pi / 2)
    ax.set_theta_direction(-1)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(REQUIREMENTS, fontsize=plot_style.TICK_SIZE)
    # See render(): 19pt labels collide with the polygon at the bottom vertex.
    ax.tick_params(axis="x", pad=22)
    ax.set_ylim(0, 1)
    ax.set_yticks([0.25, 0.5, 0.75, 1.0])
    ax.set_yticklabels(["0.25", "0.50", "0.75", "1.0"],
                       fontsize=plot_style.ANNOT_SIZE, color="grey")
    ax.set_rlabel_position(180 / n)
    paper = naming.MODELS.get(model, {}).get("paper", model)
    ax.set_title(f"Per-organism requirement coverage: {paper}\n"
                 "(prior = dashed/warm, ours = solid/cool; larger = better)",
                 fontsize=plot_style.TITLE_SIZE, pad=30)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.10), ncol=2, frameon=False,
              fontsize=plot_style.LEGEND_SIZE)

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / "spider_per_organism"
    fig.savefig(f"{stem}.png", dpi=170, bbox_inches="tight")
    fig.savefig(f"{stem}.pdf", bbox_inches="tight")
    plt.close(fig)
    print(f"[spider] wrote {stem}.png / .pdf")
    return Path(f"{stem}.png")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="qwen2.5-7b-it", choices=list(naming.MODELS))
    ap.add_argument("--artifact-root", default=str(ARTIFACT_ROOT),
                    help="Root containing results/json (defaults to the repository).")
    ap.add_argument("--prior-artifact-root", default=None,
                    help="Root holding the PRIOR baselines (pwd/circuitbreak/sysprompt). The Table 3 "
                         "root holds none of them, and without this they are drawn at the origin on "
                         "every axis, which reads as zero performance rather than absent data.")
    ap.add_argument("--out_dir", default=None,
                    help="Output directory (default: ARTIFACT_ROOT/tables).")
    args = ap.parse_args()
    global RESULTS_JSON_ROOT, PRIOR_RESULTS_JSON_ROOT
    artifact_root = Path(args.artifact_root).expanduser().resolve()
    RESULTS_JSON_ROOT = artifact_root / "results" / "json"
    if args.prior_artifact_root:
        PRIOR_RESULTS_JSON_ROOT = (
            Path(args.prior_artifact_root).expanduser().resolve() / "results" / "json"
        )
        print(f"[spider] prior baselines read from {PRIOR_RESULTS_JSON_ROOT}")
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else artifact_root / "tables"
    render(args.model, out_dir)
    render_per_org(args.model, out_dir)


if __name__ == "__main__":
    main()
