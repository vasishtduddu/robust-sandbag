"""Every whitebox CURVE figure in one style: SFT, DPO, Fuzzing and Pruning.

Mark 2026-10-01: one common look (house template https://github.com/vasishtduddu/plots-figures,
see ``plot_style``), y-axis always "Elicitation Accuracy (%)", a small top-right legend.
One single-panel figure per (technique, model, task), written to ``tables/<technique>/``:

    tables/sft/sft_<model>_<task>.{pdf,png}        fine-tuning steps on x
    tables/dpo/dpo_<model>_<task>.{pdf,png}
    tables/fuzzing/fuzzing_<model>_<task>.{pdf,png} noise sigma on x (canonical 63-config grid)
    tables/pruning/pruning_<model>_<task>.{pdf,png} sparsity on x

y is the attacked model's accuracy on the sandbag task (locked, no password), mean over
the three organism seeds with a +/- 1 SE band; Fuzzing first averages the three attacker
seeds within each organism seed. The arms are read in their own protocols (RoBCoTDstl
generate_parse, password locking forced choice), so the PWD curve's step-0 level is its own
locked floor, not zero.

Data loading is reused, not re-implemented: ``ft_curve_plot_paper._load_seed_curves`` for
SFT/DPO and ``sweep_curve_plot._collect`` (canonical noise grid, seedless-alias proof) for the
sweeps. The arm is chosen by ROOT (curve filenames carry no organism), so the roots below are
the ones the published figures used (tables/PROVENANCE.md):

* SFT ours = the DL-c/DL-L figure roots ``possum-sftfig-dlc/{ours7b,ours14b}`` (Mark 09-27/28).
* SFT pwd = ``artifacts`` (7B) and ``possum-pwd-fc-sweeps`` (14B).
* DPO ours/pwd = ``possum-table3-current``/``artifacts`` (7B), ``possum-d4dpo-{ours,pwd}14b`` (14B).
* SFT/DPO 32B ours = ``possum-fig32b-ours/{sft,dpo}``; pwd = ``possum-d4-pwd32b`` / ``possum-d4dpo-pwd32b``.
* 7B MMLU SFT/DPO (2026-10-04 fill): ours ``possum-d4{,dpo}-ours7bmmlu``; pwd ``possum-d4{,dpo}-pwd7bmmlu`` (synced from WatGPU).
* Sweeps: ours ``possum-table3-current`` then ``possum-mmlu-merge``; pwd ``possum-pwd-fc-sweeps`` then ``possum-pwd-steer``
  (the 7B/32B MMLU PWD retrains of Mark 09-24, swept on WatGPU ``pwdsteer`` and synced 2026-10-04).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from . import ft_curve_plot_paper as ft
from . import plot_style
from . import sweep_curve_plot as sw

REPO = Path(__file__).resolve().parent.parent
S = Path("/scratch/markwang")
ART = REPO / "artifacts"
MODELS = ("qwen2.5-7b-it", "qwen2.5-14b-it", "qwen2.5-32b-it")
TASKS = ("bio", "chem", "cyber", "mmlu-prof", "mmlu-stem", "mmlu-hss")

FT_ROOTS = {  # (technique, model) -> (ours roots, pwd roots); the first root holding a (model, task) curve wins
    ("sft", "qwen2.5-7b-it"): ((S / "possum-sftfig-dlc/ours7b", S / "possum-d4-ours7bmmlu"), (ART, S / "possum-d4-pwd7bmmlu")),
    ("sft", "qwen2.5-14b-it"): ((S / "possum-sftfig-dlc/ours14b",), (S / "possum-pwd-fc-sweeps",)),
    ("dpo", "qwen2.5-7b-it"): ((S / "possum-table3-current", S / "possum-d4dpo-ours7bmmlu"), (ART, S / "possum-d4dpo-pwd7bmmlu")),
    ("dpo", "qwen2.5-14b-it"): ((S / "possum-d4dpo-ours14b",), (S / "possum-d4dpo-pwd14b",)),
    ("sft", "qwen2.5-32b-it"): ((S / "possum-fig32b-ours/sft",), (S / "possum-d4-pwd32b",)),
    ("dpo", "qwen2.5-32b-it"): ((S / "possum-fig32b-ours/dpo",), (S / "possum-d4dpo-pwd32b",)),
}
# Mark 09-25: 32B MMLU-Stem PWD reports 2 seeds (s42 fails its lock gate), as PWD_STEERING_TWO_SEED_CELLS in tablefill.
PWD_TWO_SEED_CELLS = {("qwen2.5-32b-it", "mmlu-stem"): 2}
SWEEP_OURS = (S / "possum-table3-current", S / "possum-mmlu-merge")
SWEEP_PWD = (S / "possum-pwd-fc-sweeps", S / "possum-pwd-steer")

XLABEL = {"sft": "SFT Steps", "dpo": "DPO Steps",
          "fuzzing": r"Gaussian Noise $\sigma$", "pruning": "Pruning Sparsity"}
SWEEP_TECH = {"fuzzing": "noise", "pruning": "pruning"}
ARMS = (("obfcot", "RoBCoTDstl", plot_style.GREEN, "solid"),
        ("pwd", "Password Locking", plot_style.BROWN, "dotted"))


def _ft_curves(root: Path, technique: str, model: str, task: str):
    seeds = ft._load_seed_curves(root, technique, model, task)
    return [[(float(step), locked) for step, locked, _ in rows] for rows in seeds.values()]


def arm_curves(technique: str, model: str, task: str) -> dict:
    """{organism: [per-seed [(x, accuracy fraction)]]} for the arms that have data.

    With ``POSSUM_FIGURE_DATA=snapshot`` this reads the committed ``tables/figure_data/whitebox_curves.csv``
    (``figure_data``) instead of the cluster roots, so the figures render from a plain GitHub checkout.
    """
    from . import figure_data
    if figure_data.use_snapshot():
        return figure_data.snapshot_curves(technique, model, task)
    if technique in ("sft", "dpo"):
        roots = FT_ROOTS.get((technique, model))
        if roots is None:
            return {}
        out = {}
        for org, arm_roots in zip(("obfcot", "pwd"), roots):
            c = next((c for r in arm_roots if (c := _ft_curves(r, technique, model, task))), None)
            if c:
                out[org] = c
        return out
    tech = SWEEP_TECH[technique]
    out = {}
    ours = next((c for r in SWEEP_OURS if (c := sw._collect(r, tech, model, task, org="obfcot"))), None)
    if ours:
        out["obfcot"] = ours
        pwd = next((c for r in SWEEP_PWD if (c := sw._collect(r, tech, model, task, org="pwd"))), None)
        if pwd:
            out["pwd"] = pwd
    return out


def render(technique: str, model: str, task: str, out_dir: Path, *, require_pwd: bool = False, min_seeds: int = 0):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    curves = arm_curves(technique, model, task)
    if "obfcot" not in curves or (require_pwd and "pwd" not in curves):
        return None, curves
    # 2026-10-03: a 32B cyber SFT panel was written with RoBCoTDstl n=1 while seeds were still running; FT panels need every arm complete.
    if any(len(c) < min(min_seeds, PWD_TWO_SEED_CELLS.get((model, task), 3) if org == "pwd" else 3)
           for org, c in curves.items()):
        return None, curves
    plot_style.apply()
    fig, ax = plt.subplots(figsize=plot_style.CURVE_FIGSIZE)
    ytop = 0.0
    for org, label, color, ls in ARMS:
        if org not in curves:
            continue
        x, mean, err = sw._aggregate(curves[org])
        mean, err = mean * 100.0, err * 100.0
        plot_style.draw_mean_band(ax, x, mean, err, color=color, linestyle=ls, label=label)
        ytop = max(ytop, float(np.max(mean + err)))
    plot_style.finish_curve_axes(ax, xlabel=XLABEL[technique], ytop=ytop)
    fig.tight_layout()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"{technique}_{model}_{task}"
    for ext in ("pdf", "png"):
        # Not with_suffix: "qwen2.5-..." already contains a dot and would be truncated.
        fig.savefig(out_dir / f"{stem.name}.{ext}", bbox_inches="tight", dpi=250)
    plt.close(fig)
    return stem, curves


MODEL_LABEL = {"qwen2.5-7b-it": "Qwen2.5-7B", "qwen2.5-14b-it": "Qwen2.5-14B", "qwen2.5-32b-it": "Qwen2.5-32B"}
TASK_LABEL = {"bio": "WMDP-Bio", "chem": "WMDP-Chem", "cyber": "WMDP-Cyber",
              "mmlu-prof": "MMLU-Prof", "mmlu-stem": "MMLU-STEM", "mmlu-hss": "MMLU-HSS"}


def render_composite(technique: str, out_dir: Path, models=MODELS, tasks=TASKS):
    """One figure per technique: rows = models, columns = datasets (Mark 10-04 final deliverable).

    Each panel is gated exactly like ``render`` (FT panels need both arms at full seed count), and a
    panel that fails its gate is drawn as "pending" rather than silently dropped, so a partial
    composite can never pass for a complete one. Returns (stem or None, {(model, task): seeds}).
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    ft_tech = technique in ("sft", "dpo")
    plot_style.apply()
    plt.rcParams.update({"axes.titlesize": 19, "axes.labelsize": 18,
                         "xtick.labelsize": 13, "ytick.labelsize": 13})
    fig, axes = plt.subplots(len(models), len(tasks), figsize=(3.9 * len(tasks), 3.3 * len(models)), squeeze=False)
    seeds, missing = {}, []
    for i, model in enumerate(models):
        for j, task in enumerate(tasks):
            ax = axes[i][j]
            curves = arm_curves(technique, model, task)
            seeds[(model, task)] = {org: len(c) for org, c in curves.items()}
            ok = "obfcot" in curves and (not ft_tech or "pwd" in curves) and not any(
                len(c) < (min(3, PWD_TWO_SEED_CELLS.get((model, task), 3)) if org == "pwd" else 3)
                for org, c in curves.items() if ft_tech)
            ytop = 0.0
            if ok:
                for org, label, color, ls in ARMS:
                    if org in curves:
                        x, mean, err = sw._aggregate(curves[org])
                        plot_style.draw_mean_band(ax, x, mean * 100.0, err * 100.0, color=color, linestyle=ls)
                        ytop = max(ytop, float(np.max((mean + err) * 100.0)))
            else:
                missing.append((model, task))
                ax.text(0.5, 0.5, "pending", transform=ax.transAxes, ha="center", va="center",
                        fontsize=15, color="#999999", style="italic")
                ax.set_xticks([])
            import math
            ax.set_ylim(0, min(100.0, max(10.0, math.ceil(ytop * 1.15 / 10.0) * 10.0)))
            ax.grid(axis="y", alpha=0.25, linewidth=0.8)
            ax.set_axisbelow(True)
            ax.tick_params(axis="both", length=3, width=1)
            plot_style.despine(ax)
            if i == 0:
                ax.set_title(TASK_LABEL[task])
            if ok and technique == "fuzzing":  # 0.0000..0.0100 tick labels collide at composite width
                from matplotlib.ticker import FormatStrFormatter, MaxNLocator
                ax.xaxis.set_major_locator(MaxNLocator(3))
                ax.xaxis.set_major_formatter(FormatStrFormatter("%g"))
            if i == len(models) - 1:
                ax.set_xlabel(XLABEL[technique])
            if j == 0:
                ax.set_ylabel(f"{MODEL_LABEL[model]}\nElicitation Acc. (%)")
    handles = [Line2D([], [], color=c, linewidth=2.6, linestyle=(0, (1.2, 1.4)) if ls == "dotted" else "-", label=lab)
               for _, lab, c, ls in ARMS]
    fig.legend(handles=handles, loc="upper center", ncol=2, fontsize=17, frameon=False, bbox_to_anchor=(0.5, 1.0))
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{technique}_composite"
    for ext in ("pdf", "png"):
        fig.savefig(out_dir / f"{name}.{ext}", bbox_inches="tight", dpi=200)
    plt.close(fig)
    return out_dir / name, seeds, missing


#: Appendix grids (Vasisht 2026-10-08): every model x dataset for every technique, in THREE figures
#: instead of 72 single panels. Grouped by technique because the x units are per-technique, and the
#: two fine-tuning techniques share an x axis so they merge into one grid exactly as in Figure 3.
APPENDIX_FAMILIES = (
    ("fuzzing", ("fuzzing",), "Fuzzing"),
    ("pruning", ("pruning",), "Pruning"),
    ("finetuning", ("sft", "dpo"), "Fine-tuning"),
)
#: Sized for the full \textwidth (Mark 2026-10-21): the same 14in canvas width and type sizes as
#: Figures 4 and 5 (whitebox_representative_plot, t45_whitebox_matrix), so all three print their
#: text at the same size, and taller than before so the 18 panels are less letterboxed.
APPENDIX_RCPARAMS = {
    "axes.titlesize": 19,
    "axes.labelsize": 20,
    "xtick.labelsize": 16,
    "ytick.labelsize": 16,
    "legend.fontsize": 17,
    "figure.labelsize": 21,
}
APPENDIX_FIGSIZE = (14.0, 8.2)
APPENDIX_LINEWIDTH = 2.2
#: Inside the merged fine-tuning grid, LINE STYLE carries the technique and COLOR the arm.
APPENDIX_FT_STYLE = {"sft": ("SFT", "solid"), "dpo": ("DPO", (0, (5.0, 2.4)))}
#: Explicit x ticks (rescaled units, see whitebox_representative_plot.XSCALE): three per ~2in panel.
#: Interior last ticks (8, 0.8) as in Figure 4: a tick at the axis end collides with the next panel's 0.
APPENDIX_XTICKS = {"fuzzing": (0, 4, 8), "pruning": (0, 0.4, 0.8), "sft": (0, 1, 2), "dpo": (0, 1, 2)}


def _panel_is_complete(technique: str, curves: dict, model: str, task: str) -> bool:
    """The gate render/render_composite apply: a panel is drawn only at full seed count."""
    ft = technique in ("sft", "dpo")
    if "obfcot" not in curves or (ft and "pwd" not in curves):
        return False
    return not any(
        len(c) < (min(3, PWD_TWO_SEED_CELLS.get((model, task), 3)) if org == "pwd" else 3)
        for org, c in curves.items() if ft
    )


def render_appendix(family: str, techniques, title: str, out_dir: Path, models=MODELS, tasks=TASKS):
    """One appendix figure per technique family: rows = models, columns = datasets.

    Same visual language as the main-paper figure (``whitebox_representative_plot``): color carries
    the arm, line style carries the technique inside the merged fine-tuning grid, arm labels are
    RobCoT / PWD, x values are rescaled so the tick labels stay short, and faint x/y gridlines. A
    panel that fails its seed gate is drawn as "pending", never silently dropped.
    """
    import math
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.ticker import MaxNLocator

    # Imported lazily: whitebox_representative_plot imports this module, so a top-level import
    # here would be circular. It owns the shared style constants (one definition, two figures).
    from . import whitebox_representative_plot as rep

    plot_style.apply()
    plt.rcParams.update(APPENDIX_RCPARAMS)
    merged = len(techniques) > 1
    # Shared axes, unlike the old per-panel composite: one y scale makes the 18 panels directly
    # comparable (and matches the main-paper figure), and it confines the tick labels to the left
    # column and bottom row, which is most of what made the dense grid look cluttered.
    fig, axes = plt.subplots(len(models), len(tasks), figsize=APPENDIX_FIGSIZE, squeeze=False,
                             sharex=True, sharey=True)
    seeds, missing, ymax = {}, [], 0.0
    for i, model in enumerate(models):
        for j, task in enumerate(tasks):
            ax, drawn = axes[i][j], False
            for technique in techniques:
                curves = arm_curves(technique, model, task)
                seeds[(technique, model, task)] = {org: len(c) for org, c in curves.items()}
                if not _panel_is_complete(technique, curves, model, task):
                    continue
                linestyle = APPENDIX_FT_STYLE[technique][1] if merged else "solid"
                for org, _, color, _ in ARMS:
                    if org not in curves:
                        continue
                    x, mean, err = sw._aggregate(curves[org])
                    x, mean, err = np.asarray(x) * rep.XSCALE[technique], mean * 100.0, err * 100.0
                    ax.fill_between(x, mean - err, mean + err, color=color, alpha=0.16,
                                    linewidth=0, zorder=1)
                    ax.plot(x, mean, linestyle=linestyle, color=color,
                            linewidth=APPENDIX_LINEWIDTH, solid_joinstyle="miter", zorder=3)
                    peak = float(np.max(mean + err))
                    ymax = max(ymax, peak)
                drawn = True
            if not drawn:
                missing.append((model, task))
                # No set_xticks([]) here: the axes are shared, so blanking one blanks the column.
                ax.text(0.5, 0.5, "pending", transform=ax.transAxes, ha="center", va="center",
                        fontsize=15, color="#999999", style="italic")
            ticks = APPENDIX_XTICKS[techniques[0]]
            ax.set_xticks(ticks, [f"{t:g}" for t in ticks])
            ax.yaxis.set_major_locator(MaxNLocator(nbins=4))
            ax.tick_params(axis="both", length=4, width=1.0)
            ax.grid(axis="both", alpha=0.25, linewidth=0.8)
            ax.set_axisbelow(True)
            plot_style.despine(ax)
            if i == 0:
                ax.set_title(TASK_LABEL[task], pad=6)
            if j == 0:
                # The model names the ROW; both axis names are figure-level (supxlabel/supylabel),
                # since with shared axes all six columns carry the same x unit and all 18 panels the
                # same y unit, so repeating either 6 times is pure clutter. The row label is set
                # below the body size: at 21pt "Qwen2.5-14B" is taller than its own panel and the
                # three rows' labels ran into each other.
                ax.set_ylabel(MODEL_LABEL[model], fontsize=17, labelpad=4)

    axes[0][0].set_ylim(0, min(100.0, max(10.0, math.ceil(ymax * 1.12 / 10.0) * 10.0)))
    handles = [Line2D([], [], color=color, linewidth=APPENDIX_LINEWIDTH, linestyle="solid",
                      label=rep.FIGURE_LABELS.get(org, label))
               for org, label, color, _ in ARMS]
    if merged:
        handles += [Line2D([], [], color="#6f6f6f", linewidth=APPENDIX_LINEWIDTH, linestyle=linestyle,
                           label=name) for name, linestyle in APPENDIX_FT_STYLE.values()]
    fig.supylabel("Elicitation Accuracy (%)", x=0.008)
    fig.supxlabel(rep.XLABEL_SHORT[techniques[0]], y=0.008)
    # left is tight on purpose: the row label sits just outside the y ticks and the metric name just
    # outside that, so the two are not separated by a band of empty page (Vasisht 2026-10-08).
    fig.subplots_adjust(left=0.085, right=0.996, bottom=0.095, top=0.885, wspace=0.10, hspace=0.16)
    # Mark 2026-10-21: the key is one row above the column titles (as in Figure 5); at full width it
    # fits on a single line and no longer covers part of a panel.
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.54, 1.0), ncol=len(handles),
               frameon=False, handlelength=2.4, handletextpad=0.5, columnspacing=2.2)

    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"whitebox_appendix_{family}"
    for extension in ("pdf", "png"):
        fig.savefig(out_dir / f"{name}.{extension}", bbox_inches="tight", dpi=200)
    plt.close(fig)
    return out_dir / name, seeds, missing


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--techniques", nargs="+", default=["sft", "dpo", "fuzzing", "pruning"],
                    choices=["sft", "dpo", "fuzzing", "pruning"])
    ap.add_argument("--models", nargs="+", default=list(MODELS))
    ap.add_argument("--tasks", nargs="+", default=list(TASKS))
    ap.add_argument("--out-root", type=Path, default=REPO / "tables")
    ap.add_argument("--composite", action="store_true",
                    help="write one rows=models x cols=datasets figure per technique instead of single panels")
    ap.add_argument("--appendix", action="store_true",
                    help="write the three appendix grids (Fuzzing, Pruning, merged Fine-tuning) to tables/appendix/")
    ap.add_argument("--snapshot", action="store_true",
                    help="read the committed tables/figure_data/whitebox_curves.csv (no cluster access needed)")
    a = ap.parse_args()
    if a.snapshot:
        import os
        os.environ["POSSUM_FIGURE_DATA"] = "snapshot"
    if a.appendix:
        for family, techniques, title in APPENDIX_FAMILIES:
            stem, _, missing = render_appendix(family, techniques, title, a.out_root / "appendix",
                                               a.models, a.tasks)
            total = len(a.models) * len(a.tasks)
            print(f"wrote {stem} panels={total - len(missing)}/{total}"
                  + (f" pending={missing}" if missing else ""))
        return
    if a.composite:
        for technique in a.techniques:
            stem, _, missing = render_composite(technique, a.out_root / technique, a.models, a.tasks)
            print(f"wrote {stem} panels={len(a.models) * len(a.tasks) - len(missing)}/{len(a.models) * len(a.tasks)}"
                  + (f" pending={missing}" if missing else ""))
        return
    for technique in a.techniques:
        for model in a.models:
            for task in a.tasks:
                # SFT/DPO panels are two-arm by definition (the 32B PWD arm is still running).
                ft = technique in ("sft", "dpo")
                stem, curves = render(technique, model, task, a.out_root / technique,
                                      require_pwd=ft, min_seeds=3 if ft else 0)
                n = {org: len(c) for org, c in curves.items()}
                print(f"{'wrote' if stem else 'skip '} {technique}/{model}/{task} seeds={n}")


if __name__ == "__main__":
    main()
