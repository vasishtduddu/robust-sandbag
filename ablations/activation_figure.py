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

r"""Activation distinguishability figures.

Two steps:

1. Extract activations on a GPU with ablations/activation_indistinguishability.py extract, then
   run its analyze step (one output directory per organism seed, holding <organism>.npz and
   stats.json).
2. Summarise and plot here (CPU only):

       python ablations/activation_figure.py export --data figures/activation_data \
           --tag qwen2.5-14b-it_cyber --seed-dir 42=out_s42 --seed-dir 43=out_s43 --seed-dir 44=out_s44
       python ablations/activation_figure.py render --data figures/activation_data \
           --tag qwen2.5-14b-it_cyber --out-dir figures

The read site is the token before the answer letter of the question prompt, with and without the
trigger, with the correct letter teacher-forced in both cases. Histograms (--layer, default 14, the
centre of the obfuscated activation training (OAT) band 13-15) show each test question's activation
projected on the difference-of-means direction fit on elicit_train, in units of the pooled
within-class standard deviation. The layer panel shows that separation (Cohen's d) at every layer,
mean +/- 1 standard error over organism seeds. --layout selects full, histograms or layers.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np

import sys

# Allow running as "python ablations/activation_figure.py" from the repository root.
_PKG = Path(__file__).resolve().parent.parent
if str(_PKG.parent) not in sys.path:
    sys.path.insert(0, str(_PKG.parent))

from wcmo.experiments import plot_style  # noqa: E402

ARMS = (("pwd", "PWD", plot_style.BROWN), ("circuitbreak", "CktBr", "#3B6FB6"),
        ("obfcot", "RobCoT", plot_style.GREEN), ("base", "Base model", "#777777"))
OAT_BAND = (13, 14, 15)
XLABEL = "Activation Projection (within-class SDs)"
SNAPSHOT_DIR = Path("figures") / "activation_data"
BASE_SEED = 0  # the base model has no organism seed


def whitened_projection(z, layer):
    f1, f0 = z["elicit_train_notrig"][:, layer].astype(np.float64), z["elicit_train_trig"][:, layer].astype(np.float64)
    t1, t0 = z["test_notrig"][:, layer].astype(np.float64), z["test_trig"][:, layer].astype(np.float64)
    allf = np.concatenate([f1, f0]); mu, sd = allf.mean(0), allf.std(0) + 1e-6
    w = ((f1 - mu) / sd).mean(0) - ((f0 - mu) / sd).mean(0); w /= np.linalg.norm(w)
    p1, p0 = ((t1 - mu) / sd) @ w, ((t0 - mu) / sd) @ w
    ps = np.sqrt((p1.var(ddof=1) + p0.var(ddof=1)) / 2)
    centre = (p1.mean() + p0.mean()) / 2
    return (p0 - centre) / ps, (p1 - centre) / ps, float((p1.mean() - p0.mean()) / ps)


def overlap_pct(p0, p1, nbins: int = 50) -> float:
    """Histogram overlap of the two projections in percent (100 = identical, 0 = fully separated)."""
    lo, hi = float(min(p0.min(), p1.min())), float(max(p0.max(), p1.max()))
    bins = np.linspace(lo, hi, nbins + 1)
    h0, _ = np.histogram(p0, bins=bins); h1, _ = np.histogram(p1, bins=bins)
    return float(100.0 * np.minimum(h0 / h0.sum(), h1 / h1.sum()).sum())


# ------------------------------------------------------------------------------------------- summary I/O

def _paths(tag: str, root: Path = SNAPSHOT_DIR):
    return (root / f"activation_projections_{tag}.csv", root / f"activation_layer_profile_{tag}.csv",
            root / f"activation_band_stats_{tag}.json")


def export(tag: str, seed_dirs: dict[int, Path], root: Path = SNAPSHOT_DIR) -> None:
    """Save what the figure plots: OAT-layer projections and per-layer d', per organism and seed."""
    proj_path, prof_path, stats_path = _paths(tag, root)
    root.mkdir(parents=True, exist_ok=True)
    proj_rows, prof_rows, band_stats = [], [], {}
    base_done = False
    for seed, src in sorted(seed_dirs.items()):
        stats = json.loads((src / "stats.json").read_text())
        for arm, *_ in ARMS:
            npz = src / f"{arm}.npz"
            if not npz.exists() or (arm == "base" and base_done):
                continue
            s = BASE_SEED if arm == "base" else seed
            z = np.load(npz)
            nlay = z["test_trig"].shape[1]
            for layer in range(1, nlay):
                p0, p1, d = whitened_projection(z, layer)
                prof_rows.append((arm, s, layer, repr(d), repr(overlap_pct(p0, p1))))
                if layer in OAT_BAND:
                    for cond, vals in (("trigger_present", p0), ("trigger_absent", p1)):
                        proj_rows += [(arm, s, layer, cond, i, repr(float(v))) for i, v in enumerate(vals)]
            band_stats[f"{arm}/{s}"] = {str(l): {m: v for m, v in stats[arm][str(l)].items()
                                                 if "auroc" not in m and "logistic" not in m} for l in OAT_BAND}
            base_done |= arm == "base"
            print(f"[export] {arm} seed {s}: {nlay - 1} layers")
    with proj_path.open("w", newline="") as f:
        w = csv.writer(f); w.writerow(("arm", "seed", "layer", "condition", "question_index", "projection")); w.writerows(proj_rows)
    with prof_path.open("w", newline="") as f:
        w = csv.writer(f); w.writerow(("arm", "seed", "layer", "dprime", "overlap_pct")); w.writerows(prof_rows)
    stats_path.write_text(json.dumps(band_stats, indent=1))
    print(f"wrote {proj_path} ({len(proj_rows)} rows), {prof_path} ({len(prof_rows)} rows), {stats_path}")


def load_snapshot(tag: str, seeds: list[int] | None, root: Path = SNAPSHOT_DIR):
    """Return (proj, prof, stats).

    proj[organism][seed][layer] = (p_trigger_present, p_trigger_absent);
    prof[organism][seed] = (dprime list, overlap list).
    """
    proj_path, prof_path, stats_path = _paths(tag, root)
    keep = (lambda arm, s: arm == "base" or seeds is None or s in seeds)
    acc: dict = {}
    with proj_path.open() as f:
        for r in csv.DictReader(f):
            arm, s, layer = r["arm"], int(r["seed"]), int(r["layer"])
            if keep(arm, s):
                acc.setdefault(arm, {}).setdefault(s, {}).setdefault(layer, {"trigger_present": [], "trigger_absent": []})[
                    r["condition"]].append(float(r["projection"]))
    proj = {a: {s: {l: (np.array(v["trigger_present"]), np.array(v["trigger_absent"])) for l, v in ls.items()}
                for s, ls in ss.items()} for a, ss in acc.items()}
    prof: dict = {}
    with prof_path.open() as f:
        for r in csv.DictReader(f):
            arm, s = r["arm"], int(r["seed"])
            if keep(arm, s):
                d, o = prof.setdefault(arm, {}).setdefault(s, ([], []))
                d.append(float(r["dprime"])); o.append(float(r["overlap_pct"]))
    stats = json.loads(stats_path.read_text())
    return proj, prof, stats


# ---------------------------------------------------------------------------------------------------- figure

def render(proj, prof, stats, *, layer: int, out_dir: Path, tag: str) -> Path:
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    from matplotlib.ticker import FixedLocator, NullLocator, FuncFormatter

    seeds = sorted({s for a, ss in proj.items() if a != "base" for s in ss})
    plot_style.apply()
    hist_arms = [x for x in ARMS if x[0] in proj and x[0] != "base"]
    fig, axes = plt.subplots(1, len(hist_arms) + 1, figsize=(4.6 * (len(hist_arms) + 1), 4.2),
                             gridspec_kw={"width_ratios": [1] * len(hist_arms) + [1.4]})
    pooled = {k: (np.concatenate([proj[k][s][layer][0] for s in sorted(proj[k])]),
                  np.concatenate([proj[k][s][layer][1] for s in sorted(proj[k])])) for k, *_ in hist_arms}
    # One x range for all histogram panels, so a small and a large separation do not look alike.
    lim = max(4.0, max(float(np.max(np.abs(np.r_[p0, p1]))) for p0, p1 in pooled.values()) * 1.05)
    bins = np.linspace(-lim, lim, 81)
    for ax, (k, label, color) in zip(axes, hist_arms):
        p0, p1 = pooled[k]
        ax.hist(p0, bins=bins, color="#999999", alpha=0.55, label="Not sandbagging (trigger)")
        ax.hist(p1, bins=bins, color=color, alpha=0.6, label="Sandbagging (no trigger)")
        ax.set_xlim(-lim, lim)
        ax.set_title(label)
        per_seed = [overlap_pct(*proj[k][s][layer]) for s in sorted(proj[k])]
        lo, hi = f"{min(per_seed):.0f}", f"{max(per_seed):.0f}"
        text = (f"Overlap: {per_seed[0]:.0f}%" if len(per_seed) == 1 or lo == hi else
                f"Overlap: {np.mean(per_seed):.0f}% ({lo}-{hi})")
        ax.text(0.5, 0.97, text, transform=ax.transAxes, va="top", ha="center", fontsize=14)
        ax.set_yticks([]); plot_style.despine(ax)
    axes[0].set_ylabel("Count")
    # One x label under the middle histogram (repeated, the long label overlaps its neighbours).
    axes[(len(hist_arms) - 1) // 2].set_xlabel(XLABEL)
    fig.legend(handles=[Patch(color="#999999", alpha=0.55, label="Not sandbagging (trigger present)"),
                        Patch(color="#555555", alpha=0.6, label="Sandbagging (trigger absent; organism colour)")],
               loc="upper center", ncol=2, frameon=False, fontsize=13, bbox_to_anchor=(0.38, 1.08))
    ax = axes[-1]
    dmean, dse = {}, {}
    for k, label, color in ARMS:
        if k not in prof:
            continue
        D = np.array([prof[k][s][0] for s in sorted(prof[k])])
        xs = np.arange(1, D.shape[1] + 1)
        dmean[k] = D.mean(0)
        dse[k] = D.std(0, ddof=1) / np.sqrt(len(D)) if len(D) > 1 else np.zeros(D.shape[1])
        ax.plot(xs, dmean[k], color=color, linewidth=2.2, label=label, linestyle="--" if k == "base" else "-")
        if len(D) > 1:
            ax.fill_between(xs, np.maximum(dmean[k] - dse[k], 1e-3), dmean[k] + dse[k], color=color, alpha=0.18,
                            linewidth=0)
    ax.axvspan(OAT_BAND[0] - 0.5, OAT_BAND[-1] + 0.5, color=plot_style.GREEN, alpha=0.12, linewidth=0)
    ax.set_yscale("log")
    ax.set_ylim(0.5, 100)
    ax.yaxis.set_major_locator(FixedLocator([1, 3, 10, 30, 100]))
    ax.yaxis.set_minor_locator(NullLocator())
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    ax.text(OAT_BAND[1], 90, "OAT\nlayers", ha="center", va="top", fontsize=11, color=plot_style.GREEN)
    ax.set_xlabel("Layer"); ax.set_ylabel("Separation (SDs)")
    ax.legend(fontsize=11, frameon=False, loc="lower right"); plot_style.despine(ax)
    fig.tight_layout()
    out_dir.mkdir(parents=True, exist_ok=True)
    for ext in ("pdf", "png"):
        fig.savefig(out_dir / f"activation_separation_{tag}.{ext}", bbox_inches="tight", dpi=250)
    (out_dir / f"activation_separation_{tag}.json").write_text(json.dumps(
        {"layer": layer, "organism_seeds": seeds,
         "overlap_pct_by_layer": {k: [round(float(np.mean(c)), 1) for c in zip(*[prof[k][s][1] for s in sorted(prof[k])])]
                                  for k in prof},
         "overlap_pct_by_layer_per_seed": {k: {str(s): [round(v, 1) for v in prof[k][s][1]] for s in sorted(prof[k])}
                                           for k in prof},
         "dprime_whitened_by_layer": {k: [round(float(v), 3) for v in dmean[k]] for k in dmean},
         "dprime_whitened_by_layer_se": {k: [round(float(v), 3) for v in dse[k]] for k in dse},
         "stats_oat_band": stats}, indent=1))
    out = out_dir / f"activation_separation_{tag}.png"
    print("wrote", out)
    return out


def render_histograms(proj, *, layer: int, out_dir: Path, tag: str) -> Path:
    """Compact 1x3 layout: histograms only, as in render but without overlap labels or the layer panel.

    Grey = trigger present ("Without Sandbagging"), organism colour = trigger absent ("With Sandbagging").
    """
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.legend_handler import HandlerTuple
    from matplotlib.patches import Patch
    from matplotlib.ticker import MaxNLocator, MultipleLocator

    plot_style.apply()
    # Smaller sizes for a compact full-width strip; the house sizes would crowd three panels.
    plt.rcParams.update({"axes.titlesize": 17, "axes.labelsize": 15, "xtick.labelsize": 13,
                         "legend.fontsize": 14})
    hist_arms = [x for x in ARMS if x[0] in proj and x[0] != "base"]
    pooled = {k: (np.concatenate([proj[k][s][layer][0] for s in sorted(proj[k])]),
                  np.concatenate([proj[k][s][layer][1] for s in sorted(proj[k])])) for k, *_ in hist_arms}
    lim = max(4.0, max(float(np.max(np.abs(np.r_[p0, p1]))) for p0, p1 in pooled.values()) * 1.05)
    bins = np.linspace(-lim, lim, 81)
    fig, axes = plt.subplots(1, len(hist_arms), figsize=(6.9, 2.5), sharey=False)
    for ax, (k, label, color) in zip(axes, hist_arms):
        p0, p1 = pooled[k]
        ax.hist(p0, bins=bins, color="#999999", alpha=0.55)
        ax.hist(p1, bins=bins, color=color, alpha=0.6)
        ax.set_xlim(-lim, lim)
        ax.set_title(label)
        # Counts are unlabelled (only the shape matters) but keep their gridlines.
        ax.tick_params(axis="y", left=False, labelleft=False)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=5))
        ax.xaxis.set_minor_locator(MultipleLocator(10))
        ax.tick_params(axis="x", which="minor", length=0)
        ax.grid(axis="y", alpha=0.25, linewidth=0.8)
        ax.grid(axis="x", which="both", alpha=0.25, linewidth=0.8)
        ax.set_axisbelow(True)
        plot_style.despine(ax)
    axes[0].set_ylabel("Frequency")
    fig.supxlabel(XLABEL, fontsize=plt.rcParams["axes.labelsize"])
    without = Patch(color="#999999", alpha=0.55)
    with_ = tuple(Patch(color=c, alpha=0.6) for _, _, c in hist_arms)
    fig.legend([without, with_], ["Without Sandbagging", "With Sandbagging"],
               handler_map={tuple: HandlerTuple(ndivide=None, pad=0)}, handlelength=3.0,
               loc="upper center", ncol=2, frameon=False, bbox_to_anchor=(0.5, 1.18))
    fig.tight_layout(pad=0.4, w_pad=1.0)
    out_dir.mkdir(parents=True, exist_ok=True)
    for ext in ("pdf", "png"):
        fig.savefig(out_dir / f"activation_histograms_{tag}.{ext}", bbox_inches="tight", dpi=250)
    out = out_dir / f"activation_histograms_{tag}.png"
    print("wrote", out)
    return out


def render_layers(prof, *, out_dir: Path, tag: str) -> Path:
    """Standalone per-layer panel: the right panel of render.

    Cohen's d per layer, mean +/- 1 SE over seeds, log y axis, OAT layers shaded.
    """

    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FixedLocator, NullLocator, FuncFormatter

    plot_style.apply()
    plt.rcParams.update({"axes.labelsize": 17, "xtick.labelsize": 14, "ytick.labelsize": 14, "legend.fontsize": 10.5})
    fig, ax = plt.subplots(figsize=(4.2, 2.9))
    nlay = 0
    # The 2-column legend fills by column: rows read PWD, RobCoT / CktBr, Base.
    for k, label, color in ARMS:
        if k not in prof:
            continue
        D = np.array([prof[k][s][0] for s in sorted(prof[k])])
        nlay = D.shape[1]
        xs = np.arange(1, nlay + 1)
        m = D.mean(0)
        ax.plot(xs, m, color=color, linewidth=2.0, label="Base" if k == "base" else label,
                linestyle="--" if k == "base" else "-")
        if len(D) > 1:
            se = D.std(0, ddof=1) / np.sqrt(len(D))
            ax.fill_between(xs, np.maximum(m - se, 1e-3), m + se, color=color, alpha=0.18, linewidth=0)
    ax.axvspan(OAT_BAND[0] - 0.5, OAT_BAND[-1] + 0.5, color=plot_style.GREEN, alpha=0.12, linewidth=0)
    ax.text(OAT_BAND[-1] + 1.5, 0.62, "Obfuscated Layer", ha="left", va="bottom", fontsize=12, color=plot_style.GREEN)
    ax.set_yscale("log")
    ax.set_ylim(0.55, 250)
    ax.yaxis.set_major_locator(FixedLocator([1, 3, 10, 30, 100]))
    ax.yaxis.set_minor_locator(NullLocator())
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    ax.set_xlim(1, nlay)
    ax.set_xticks([1] + list(range(12, nlay + 1, 12)))
    ax.set_xlabel("Layer"); ax.set_ylabel("Separation (SDs)")
    ax.grid(axis="both", alpha=0.25, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(ncol=2, frameon=False, loc="upper right", columnspacing=1.0, handlelength=1.6, borderaxespad=0.1)
    plot_style.despine(ax)
    fig.tight_layout(pad=0.4)
    out_dir.mkdir(parents=True, exist_ok=True)
    for ext in ("pdf", "png"):
        fig.savefig(out_dir / f"activation_layers_{tag}.{ext}", bbox_inches="tight", dpi=250)
    out = out_dir / f"activation_layers_{tag}.png"
    print("wrote", out)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="render", choices=["render", "export"])
    ap.add_argument("--tag", default="qwen2.5-14b-it_cyber")
    ap.add_argument("--data", default=str(SNAPSHOT_DIR),
                    help="Directory for the exported summary CSV/JSON files.")
    ap.add_argument("--seed-dir", action="append", default=[], help="export: SEED=DIR of extracted .npz")
    ap.add_argument("--seeds", default="", help="render: comma-separated organism seeds (default: all)")
    ap.add_argument("--layer", type=int, default=14)
    ap.add_argument("--layout", default="full", choices=["full", "histograms", "layers"],
                    help="full = 3 histograms + per-layer panel; histograms = compact 1x3 (no overlap labels)")
    ap.add_argument("--out-dir", default="figures")
    a = ap.parse_args()
    if a.cmd == "export":
        dirs = {int(k): Path(v) for k, _, v in (s.partition("=") for s in a.seed_dir)}
        if not dirs:
            ap.error("export needs at least one --seed-dir SEED=DIR")
        export(a.tag, dirs, Path(a.data))
        return
    seeds = [int(s) for s in a.seeds.split(",") if s.strip()] or None
    proj, prof, stats = load_snapshot(a.tag, seeds, Path(a.data))
    if seeds:
        stats = {k: v for k, v in stats.items() if k.startswith("base/") or int(k.split("/")[1]) in seeds}
    if a.layout == "layers":
        render_layers(prof, out_dir=Path(a.out_dir), tag=a.tag)
    elif a.layout == "histograms":
        render_histograms(proj, layer=a.layer, out_dir=Path(a.out_dir), tag=a.tag)
    else:
        render(proj, prof, stats, layer=a.layer, out_dir=Path(a.out_dir), tag=a.tag)


if __name__ == "__main__":
    main()
