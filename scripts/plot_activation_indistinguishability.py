"""Superseded by ``experiments/activation_figure.py`` (2026-10-10), which renders from the committed snapshot in
``tables/figure_data/`` so the figure can be regenerated from a plain GitHub checkout (docs/FIGURES_README.md
section 8). Kept so the commands recorded in tables/PROVENANCE.md still run: this wrapper exports the given .npz
directory as a single-seed snapshot and renders it.

    python scripts/plot_activation_indistinguishability.py --in /scratch/markwang/possum-actdist/out_14b_cyber \
        --out-dir tables/activations --tag qwen2.5-14b-it_cyber [--seed 42]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from experiments import activation_figure as af  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out-dir", default="tables/activations")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--layer", type=int, default=14)
    a = ap.parse_args()
    af.export(a.tag, {a.seed: Path(a.inp)})
    proj, prof, stats = af.load_snapshot(a.tag, [a.seed])
    af.render(proj, prof, stats, layer=a.layer, out_dir=Path(a.out_dir), tag=a.tag)


if __name__ == "__main__":
    main()
