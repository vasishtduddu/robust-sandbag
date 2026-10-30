#!/usr/bin/env python3
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

"""Download every dataset once and write the CSVs under datasets/mcqa/.

The loaders also build these files on first use, but compute nodes often have no network access.
Run this on a machine that does (for example a cluster login node); training and evaluation can
then run offline. It builds WMDP, the MMLU subsets, the other MMLU subjects (anchor rows), MATH,
HellaSwag and CommonsenseQA, plus the commonsense anchor file that RobPWD and RobAdapt train with,
and caches Anthropic/hh-rlhf (the SFT and DPO elicitation data).

    python scripts/prepare_datasets.py [--data_dir DIR] [--skip_math] [--skip_anchor]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT.parent))

from wcmo.data.mmlu import MMLUAnchorDataset  # noqa: E402
from wcmo.data import (  # noqa: E402
    MMLU_CATEGORY_CLASSES,
    CommonsenseQADataset,
    HellaSwagDataset,
    MATHDataset,
    MMLUDataset,
    WMDPDataset,
)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data_dir", default=str(_PKG_ROOT / "datasets"),
                    help="dataset root passed to train.py/evaluate.py as --data_dir")
    ap.add_argument("--skip_math", action="store_true", help="skip MATH (only the model-family runs use it)")
    ap.add_argument("--skip_anchor", action="store_true", help="skip the RobPWD/RobAdapt commonsense anchor")
    args = ap.parse_args()

    mcqa = str(Path(args.data_dir) / "mcqa")
    builders = [("WMDP", WMDPDataset), ("MMLU", MMLUDataset)]
    builders += [(name, cls) for name, cls in MMLU_CATEGORY_CLASSES.items()]
    builders.append(("MMLU anchor subjects", MMLUAnchorDataset))   # non-target MMLU anchor rows
    if not args.skip_math:
        builders.append(("MATH", MATHDataset))
    builders += [("HellaSwag", HellaSwagDataset), ("CommonsenseQA", CommonsenseQADataset)]
    for name, cls in builders:
        print(f"[prepare] {name}", flush=True)
        cls.from_directory(mcqa)

    anchor = Path(mcqa) / "commonsense_anchor_train.csv"
    if not args.skip_anchor and not anchor.exists():
        print("[prepare] commonsense anchor", flush=True)
        subprocess.run([sys.executable, str(_PKG_ROOT / "scripts" / "build_commonsense_anchor.py"),
                        "--out", str(anchor)], check=True)
    print("[prepare] Anthropic/hh-rlhf (Hugging Face cache)", flush=True)
    from datasets import load_dataset
    load_dataset("Anthropic/hh-rlhf", split="train")
    print(f"[prepare] done: {mcqa}")


if __name__ == "__main__":
    main()
