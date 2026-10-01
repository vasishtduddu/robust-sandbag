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
* Sweeps: ours ``possum-table3-current`` then ``possum-mmlu-merge``; pwd ``possum-pwd-fc-sweeps``.
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

FT_ROOTS = {  # (technique, model) -> (ours root, pwd root)
    ("sft", "qwen2.5-7b-it"): (S / "possum-sftfig-dlc/ours7b", ART),
    ("sft", "qwen2.5-14b-it"): (S / "possum-sftfig-dlc/ours14b", S / "possum-pwd-fc-sweeps"),
    ("dpo", "qwen2.5-7b-it"): (S / "possum-table3-current", ART),
    ("dpo", "qwen2.5-14b-it"): (S / "possum-d4dpo-ours14b", S / "possum-d4dpo-pwd14b"),
}
SWEEP_OURS = (S / "possum-table3-current", S / "possum-mmlu-merge")
SWEEP_PWD = S / "possum-pwd-fc-sweeps"

XLABEL = {"sft": "Fine-tuning steps", "dpo": "Fine-tuning steps",
          "fuzzing": r"Noise $\sigma$", "pruning": "Pruning sparsity"}
SWEEP_TECH = {"fuzzing": "noise", "pruning": "pruning"}
ARMS = (("obfcot", "RoBCoTDstl", plot_style.GREEN, "solid"),
        ("pwd", "Password locking", plot_style.BROWN, "dotted"))


def _ft_curves(root: Path, technique: str, model: str, task: str):
    seeds = ft._load_seed_curves(root, technique, model, task)
    return [[(float(step), locked) for step, locked, _ in rows] for rows in seeds.values()]


def arm_curves(technique: str, model: str, task: str) -> dict:
    """{organism: [per-seed [(x, accuracy fraction)]]} for the arms that have data."""
    if technique in ("sft", "dpo"):
        roots = FT_ROOTS.get((technique, model))
        if roots is None:
            return {}
        return {org: c for org, root in zip(("obfcot", "pwd"), roots)
                if (c := _ft_curves(root, technique, model, task))}
    tech = SWEEP_TECH[technique]
    out = {}
    ours = next((c for r in SWEEP_OURS if (c := sw._collect(r, tech, model, task, org="obfcot"))), None)
    if ours:
        out["obfcot"] = ours
        pwd = sw._collect(SWEEP_PWD, tech, model, task, org="pwd")
        if pwd:
            out["pwd"] = pwd
    return out


def render(technique: str, model: str, task: str, out_dir: Path, *, require_pwd: bool = False):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    curves = arm_curves(technique, model, task)
    if "obfcot" not in curves or (require_pwd and "pwd" not in curves):
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--techniques", nargs="+", default=["sft", "dpo", "fuzzing", "pruning"],
                    choices=["sft", "dpo", "fuzzing", "pruning"])
    ap.add_argument("--models", nargs="+", default=list(MODELS))
    ap.add_argument("--tasks", nargs="+", default=list(TASKS))
    ap.add_argument("--out-root", type=Path, default=REPO / "tables")
    a = ap.parse_args()
    for technique in a.techniques:
        for model in a.models:
            for task in a.tasks:
                # SFT/DPO panels are two-arm by definition (the 32B PWD arm is still running).
                stem, curves = render(technique, model, task, a.out_root / technique,
                                      require_pwd=technique in ("sft", "dpo"))
                n = {org: len(c) for org, c in curves.items()}
                print(f"{'wrote' if stem else 'skip '} {technique}/{model}/{task} seeds={n}")


if __name__ == "__main__":
    main()
