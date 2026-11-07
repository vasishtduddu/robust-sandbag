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

"""Activation indistinguishability: Figures 7 and 8 (Qwen2.5-14B, WMDP-Cyber).

Activations are read at the token before the answer letter, with and without the trigger, with the
correct letter teacher-forced in both cases. Each test question's activation is projected on the
difference-of-means direction fit on elicit_train and expressed in pooled within-class standard
deviations.

* Figure 7: histograms of the projections at layer 14, the centre of the obfuscated activation
  training (OAT) layers 13-15, pooled over the three organism seeds.
* Figure 8: the separation of the two conditions (Cohen's d) at every layer, mean +/- 1 standard
  error over organism seeds. The base model has a single run (seed 0).

Data: data/activation_projections_<tag>.csv (layers 13-15) and data/activation_layer_profile_<tag>.csv.
To rebuild them from new activations, run ablations/activation_indistinguishability.py extract and
analyze for each organism seed, then:

    python figures/activations.py export --tag qwen2.5-14b-it_cyber \\
        --seed-dir 42=out_s42 --seed-dir 43=out_s43 --seed-dir 44=out_s44
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np

import style

TAG = "qwen2.5-14b-it_cyber"
HISTOGRAM_ORGANISMS = (("pwd", style.BROWN), ("circuitbreak", style.BLUE), ("obfcot", style.GREEN))
# Legend order: the two-column legend fills by column.
LAYER_ORGANISMS = (("pwd", style.BROWN), ("circuitbreak", style.BLUE), ("obfcot", style.GREEN),
                   ("base", "#777777"))
OAT_LAYERS = (13, 14, 15)
LAYER = 14
XLABEL = "Activation Projection (within-class SDs)"


def _paths(tag):
    return (style.DATA / f"activation_projections_{tag}.csv",
            style.DATA / f"activation_layer_profile_{tag}.csv")


def projection(z, layer):
    """Projections of the trigger-present and trigger-absent test activations, and Cohen's d."""
    fit_absent = z["elicit_train_notrig"][:, layer].astype(np.float64)
    fit_present = z["elicit_train_trig"][:, layer].astype(np.float64)
    test_absent = z["test_notrig"][:, layer].astype(np.float64)
    test_present = z["test_trig"][:, layer].astype(np.float64)
    fit = np.concatenate([fit_absent, fit_present])
    mu, sd = fit.mean(0), fit.std(0) + 1e-6
    w = ((fit_absent - mu) / sd).mean(0) - ((fit_present - mu) / sd).mean(0)
    w /= np.linalg.norm(w)
    absent, present = ((test_absent - mu) / sd) @ w, ((test_present - mu) / sd) @ w
    pooled_sd = np.sqrt((absent.var(ddof=1) + present.var(ddof=1)) / 2)
    centre = (absent.mean() + present.mean()) / 2
    d = float((absent.mean() - present.mean()) / pooled_sd)
    return (present - centre) / pooled_sd, (absent - centre) / pooled_sd, d


def overlap_pct(a, b, nbins: int = 50) -> float:
    """Histogram overlap of two samples in percent (100 = identical, 0 = fully separated)."""
    bins = np.linspace(float(min(a.min(), b.min())), float(max(a.max(), b.max())), nbins + 1)
    ha, _ = np.histogram(a, bins=bins)
    hb, _ = np.histogram(b, bins=bins)
    return float(100.0 * np.minimum(ha / ha.sum(), hb / hb.sum()).sum())


def export(tag, seed_dirs: dict[int, Path]) -> None:
    """Write the figure data from the extracted activations (<organism>.npz in each seed's directory)."""
    proj_path, prof_path = _paths(tag)
    proj_rows, prof_rows, base_done = [], [], False
    for seed, src in sorted(seed_dirs.items()):
        for org, _ in LAYER_ORGANISMS:
            npz = Path(src) / f"{org}.npz"
            if not npz.exists() or (org == "base" and base_done):
                continue
            s = 0 if org == "base" else seed
            z = np.load(npz)
            for layer in range(1, z["test_trig"].shape[1]):
                present, absent, d = projection(z, layer)
                prof_rows.append((org, s, layer, repr(d), repr(overlap_pct(present, absent))))
                if layer in OAT_LAYERS:
                    for condition, values in (("trigger_present", present), ("trigger_absent", absent)):
                        proj_rows += [(org, s, layer, condition, i, repr(float(v))) for i, v in enumerate(values)]
            base_done |= org == "base"
    with proj_path.open("w", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(("organism", "seed", "layer", "condition", "question_index", "projection"))
        writer.writerows(proj_rows)
    with prof_path.open("w", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(("organism", "seed", "layer", "dprime", "overlap_pct"))
        writer.writerows(prof_rows)
    print(f"wrote {proj_path} and {prof_path}")


def load(tag=TAG):
    """proj[organism][seed][layer] = (trigger present, trigger absent); dprime[organism][seed] = per-layer d."""
    proj_path, prof_path = _paths(tag)
    rows: dict = {}
    with proj_path.open(newline="") as f:
        for r in csv.DictReader(f):
            by_condition = rows.setdefault(r["organism"], {}).setdefault(int(r["seed"]), {}).setdefault(
                int(r["layer"]), {"trigger_present": [], "trigger_absent": []})
            by_condition[r["condition"]].append(float(r["projection"]))
    proj = {org: {s: {layer: (np.array(v["trigger_present"]), np.array(v["trigger_absent"]))
                      for layer, v in layers.items()} for s, layers in seeds.items()}
            for org, seeds in rows.items()}
    dprime: dict = {}
    with prof_path.open(newline="") as f:
        for r in csv.DictReader(f):
            dprime.setdefault(r["organism"], {}).setdefault(int(r["seed"]), []).append(float(r["dprime"]))
    return proj, dprime


def render_histograms(proj, out_dir, formats, tag=TAG, layer=LAYER):
    """Figure 7: one histogram per organism, without (grey) and with (colour) sandbagging."""
    from matplotlib.legend_handler import HandlerTuple
    from matplotlib.patches import Patch
    from matplotlib.ticker import MaxNLocator, MultipleLocator

    plt = style.apply({"axes.titlesize": 17, "axes.labelsize": 15, "xtick.labelsize": 13, "legend.fontsize": 14})
    pooled = {org: (np.concatenate([proj[org][s][layer][0] for s in sorted(proj[org])]),
                    np.concatenate([proj[org][s][layer][1] for s in sorted(proj[org])]))
              for org, _ in HISTOGRAM_ORGANISMS}
    # One x range for all panels, so a small and a large separation do not look alike.
    lim = max(4.0, max(float(np.max(np.abs(np.r_[a, b]))) for a, b in pooled.values()) * 1.05)
    bins = np.linspace(-lim, lim, 81)
    fig, axes = plt.subplots(1, len(HISTOGRAM_ORGANISMS), figsize=(6.9, 2.5), sharey=False)
    for ax, (org, color) in zip(axes, HISTOGRAM_ORGANISMS):
        present, absent = pooled[org]
        ax.hist(present, bins=bins, color="#999999", alpha=0.55)
        ax.hist(absent, bins=bins, color=color, alpha=0.6)
        ax.set_xlim(-lim, lim)
        ax.set_title(style.NAMES[org])
        ax.tick_params(axis="y", left=False, labelleft=False)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=5))
        ax.xaxis.set_minor_locator(MultipleLocator(10))
        ax.tick_params(axis="x", which="minor", length=0)
        ax.grid(axis="y", alpha=0.25, linewidth=0.8)
        ax.grid(axis="x", which="both", alpha=0.25, linewidth=0.8)
        ax.set_axisbelow(True)
        style.despine(ax)
    axes[0].set_ylabel("Frequency")
    fig.supxlabel(XLABEL, fontsize=plt.rcParams["axes.labelsize"])
    without = Patch(color="#999999", alpha=0.55)
    with_ = tuple(Patch(color=color, alpha=0.6) for _, color in HISTOGRAM_ORGANISMS)
    fig.legend([without, with_], ["Without Sandbagging", "With Sandbagging"],
               handler_map={tuple: HandlerTuple(ndivide=None, pad=0)}, handlelength=3.0,
               loc="upper center", ncol=2, frameon=False, bbox_to_anchor=(0.5, 1.18))
    fig.tight_layout(pad=0.4, w_pad=1.0)
    return style.save(fig, out_dir, f"activation_histograms_{tag}", formats)


def render_layers(dprime, out_dir, formats, tag=TAG):
    """Figure 8: separation per layer, log scale, with the OAT layers shaded."""
    from matplotlib.ticker import FixedLocator, FuncFormatter, NullLocator

    plt = style.apply({"axes.labelsize": 17, "xtick.labelsize": 14, "ytick.labelsize": 14, "legend.fontsize": 10.5})
    fig, ax = plt.subplots(figsize=(4.2, 2.9))
    n_layers = 0
    for org, color in LAYER_ORGANISMS:
        d = np.array([dprime[org][s] for s in sorted(dprime[org])])
        n_layers = d.shape[1]
        xs = np.arange(1, n_layers + 1)
        mean = d.mean(0)
        ax.plot(xs, mean, color=color, linewidth=2.0, label=style.NAMES.get(org, "Base"),
                linestyle="--" if org == "base" else "-")
        if len(d) > 1:
            se = d.std(0, ddof=1) / np.sqrt(len(d))
            ax.fill_between(xs, np.maximum(mean - se, 1e-3), mean + se, color=color, alpha=0.18, linewidth=0)
    ax.axvspan(OAT_LAYERS[0] - 0.5, OAT_LAYERS[-1] + 0.5, color=style.GREEN, alpha=0.12, linewidth=0)
    ax.text(OAT_LAYERS[-1] + 1.5, 0.62, "Obfuscated Layer", ha="left", va="bottom", fontsize=12, color=style.GREEN)
    ax.set_yscale("log")
    ax.set_ylim(0.55, 250)
    ax.yaxis.set_major_locator(FixedLocator([1, 3, 10, 30, 100]))
    ax.yaxis.set_minor_locator(NullLocator())
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    ax.set_xlim(1, n_layers)
    ax.set_xticks([1] + list(range(12, n_layers + 1, 12)))
    ax.set_xlabel("Layer")
    ax.set_ylabel("Separation (SDs)")
    ax.grid(axis="both", alpha=0.25, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(ncol=2, frameon=False, loc="upper right", columnspacing=1.0, handlelength=1.6, borderaxespad=0.1)
    style.despine(ax)
    fig.tight_layout(pad=0.4)
    return style.save(fig, out_dir, f"activation_layers_{tag}", formats)


def render(out_dir, formats) -> list:
    proj, dprime = load()
    return render_histograms(proj, out_dir, formats) + render_layers(dprime, out_dir, formats)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["export"])
    ap.add_argument("--tag", default=TAG, help="model and task, used in the data file names")
    ap.add_argument("--seed-dir", action="append", default=[], required=True,
                    help="SEED=DIR with the extracted <organism>.npz files; repeat per organism seed")
    args = ap.parse_args()
    export(args.tag, {int(k): Path(v) for k, _, v in (s.partition("=") for s in args.seed_dir)})


if __name__ == "__main__":
    main()
