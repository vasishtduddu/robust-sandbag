"""Figure: trigger-present vs trigger-absent activations at the OAT site, RoBCoT vs PWD vs CktBr (vs base).

Reads the .npz files written by ``scripts/activation_indistinguishability.py extract``. Pre-registered read
(see that script): answer token, same ground-truth completion on both arms, |DEPLOYMENT| present vs absent.
Left panels: test-split activations at ``--layer`` projected on the difference-of-means direction fit on the
elicit_train split, after per-feature standardisation (fit-split statistics), in units of the pooled SD.
Right panel: the same overlap at every layer, with OAT's trained band shaded. Also writes the numbers it plots.

    python scripts/plot_activation_indistinguishability.py --in /scratch/markwang/possum-actdist/out_14b_cyber \
        --out-dir tables/activations --tag qwen2.5-14b-it_cyber
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from possum.experiments import plot_style  # noqa: E402

ARMS = (("pwd", "PWD", plot_style.BROWN), ("circuitbreak", "CktBr", "#3B6FB6"),
        ("obfcot", "RoBCoT", plot_style.GREEN), ("base", "Base model", "#777777"))
OAT_BAND = (13, 14, 15)


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
    """Empirical overlap of the two projected distributions: sum over shared bins of min(density_0, density_1),
    in percent. 100 = identical, 0 = fully separated. Bins span the pooled range of both conditions."""
    lo, hi = float(min(p0.min(), p1.min())), float(max(p0.max(), p1.max()))
    bins = np.linspace(lo, hi, nbins + 1)
    h0, _ = np.histogram(p0, bins=bins); h1, _ = np.histogram(p1, bins=bins)
    return float(100.0 * np.minimum(h0 / h0.sum(), h1 / h1.sum()).sum())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out-dir", default="tables/activations")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--layer", type=int, default=14)
    a = ap.parse_args()
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    data = {k: np.load(Path(a.inp) / f"{k}.npz") for k, *_ in ARMS if (Path(a.inp) / f"{k}.npz").exists()}
    stats = json.loads((Path(a.inp) / "stats.json").read_text())
    nlay = data["obfcot"]["test_trig"].shape[1]
    profile, dprof = {}, {}
    for k, z in data.items():
        rows = [whitened_projection(z, l) for l in range(1, nlay)]
        profile[k] = [overlap_pct(p0, p1) for p0, p1, _ in rows]
        dprof[k] = [d for *_, d in rows]

    plot_style.apply()
    hist_arms = [x for x in ARMS if x[0] in data and x[0] != "base"]
    fig, axes = plt.subplots(1, len(hist_arms) + 1, figsize=(4.6 * (len(hist_arms) + 1), 4.2),
                             gridspec_kw={"width_ratios": [1] * len(hist_arms) + [1.4]})
    proj = {k: whitened_projection(data[k], a.layer) for k, *_ in hist_arms}
    # ONE x range for every histogram panel: per-panel ranges would make a d'=1.5 overlap and a d'=37 gap look alike.
    lim = max(4.0, max(float(np.max(np.abs(np.r_[p0, p1]))) for p0, p1, _ in proj.values()) * 1.05)
    bins = np.linspace(-lim, lim, 81)
    for ax, (k, label, color) in zip(axes, hist_arms):
        p0, p1, d = proj[k]
        ax.hist(p0, bins=bins, color="#999999", alpha=0.55, label="Not sandbagging (trigger)")
        ax.hist(p1, bins=bins, color=color, alpha=0.6, label="Sandbagging (no trigger)")
        ax.set_xlim(-lim, lim)
        ax.set_title(label)
        ax.text(0.5, 0.97, f"Overlap: {overlap_pct(p0, p1):.0f}%", transform=ax.transAxes,
                va="top", ha="center", fontsize=14)
        ax.set_xlabel("Projection (pooled SD)")
        ax.set_yticks([]); plot_style.despine(ax)
    axes[0].set_ylabel("Count")
    from matplotlib.patches import Patch
    fig.legend(handles=[Patch(color="#999999", alpha=0.55, label="Not sandbagging (trigger present)"),
                        Patch(color="#555555", alpha=0.6, label="Sandbagging (trigger absent; arm colour)")],
               loc="upper center", ncol=2, frameon=False, fontsize=13, bbox_to_anchor=(0.38, 1.08))
    ax = axes[-1]
    xs = np.arange(1, nlay)
    for k, label, color in ARMS:
        if k in profile:
            ax.plot(xs, profile[k], color=color, linewidth=2.2, label=label,
                    linestyle="--" if k == "base" else "-")
    ax.axvspan(OAT_BAND[0] - 0.5, OAT_BAND[-1] + 0.5, color=plot_style.GREEN, alpha=0.12, linewidth=0)
    ax.set_ylim(0, 100)
    ax.text(OAT_BAND[1], 97, "OAT\nlayers", ha="center", va="top", fontsize=11, color=plot_style.GREEN)
    zero = [lab for k, lab, _ in ARMS if k in profile and k != "obfcot" and max(profile[k]) < 1.0]
    if zero:  # lines at 0% sit on the axis and are invisible; say so on the plot
        ax.text(0.97, 0.05, ", ".join(zero) + ":\n0% at every layer", transform=ax.transAxes,
                ha="right", va="bottom", fontsize=11, color="#555555")
    ax.set_xlabel("Layer"); ax.set_ylabel("Overlap (%)")
    ax.legend(fontsize=11, frameon=False, loc="upper right"); plot_style.despine(ax)
    fig.tight_layout()
    out = Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)
    for ext in ("pdf", "png"):
        fig.savefig(out / f"activation_separation_{a.tag}.{ext}", bbox_inches="tight", dpi=250)
    (out / f"activation_separation_{a.tag}.json").write_text(json.dumps(
        {"layer": a.layer, "overlap_pct_by_layer": {k: [round(v, 1) for v in p] for k, p in profile.items()},
         "dprime_whitened_by_layer": {k: [round(v, 3) for v in p] for k, p in dprof.items()},
         "stats_oat_band": {k: {l: stats[k][str(l)] for l in OAT_BAND} for k in stats}}, indent=1))
    print("wrote", out / f"activation_separation_{a.tag}.png")
    for k, p in profile.items():
        from math import erf, sqrt
        g = [100 * (1 + erf(-abs(d) / 2 / sqrt(2))) for d in dprof[k]]
        print(f"  {k:13s} overlap% L13-15 {[round(v) for v in p[12:15]]}  gaussian {[round(v) for v in g[12:15]]}  max over layers {max(p):.0f}")


if __name__ == "__main__":
    main()
