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

"""Render the paper's figures from the data in figures/data.

    python figures/make_figures.py                          # every figure, as PDF, into figures/pdf
    python figures/make_figures.py whitebox --format pdf png
    python figures/make_figures.py --format svg --out static/images/src

Requires only matplotlib and numpy.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import activations  # noqa: E402
import blackbox  # noqa: E402
import families  # noqa: E402
import finke  # noqa: E402
import whitebox  # noqa: E402

FIGURES = {
    "blackbox": blackbox.render,          # Figure 3
    "whitebox": whitebox.render,          # Figures 4 and 7-9
    "finke": finke.render,                # Figures 5 and 12
    "families": families.render,          # Figure 6
    "activations": activations.render,    # Figures 10 and 11
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("figures", nargs="*", metavar="figure",
                    help=f"any of {', '.join(FIGURES)} (default: all)")
    ap.add_argument("--format", nargs="+", default=["pdf"], choices=["pdf", "png", "svg"])
    ap.add_argument("--out", type=Path, default=HERE / "pdf")
    args = ap.parse_args()
    unknown = set(args.figures) - set(FIGURES)
    if unknown:
        ap.error(f"unknown figure: {', '.join(sorted(unknown))}")
    for name in args.figures or FIGURES:
        for path in FIGURES[name](args.out, args.format):
            print(f"wrote {path}")


if __name__ == "__main__":
    main()
