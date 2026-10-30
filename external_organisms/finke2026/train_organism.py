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

"""Train Finke et al.'s organism ("Ours": Stage A, then Stage B, then the five TAR rounds of Stage C) on WMDP.

Their drivers run unmodified from a checkout of their repository at the pinned commit (fetched on first use; it has
no license, so it is not redistributed here); port/ supplies the task and the local backends (port/shim.py). Each
stage is skipped when its checkpoint exists, so a rerun resumes.

    python external_organisms/finke2026/prepare_data.py
    python external_organisms/finke2026/train_organism.py [--work DIR] [--upstream DIR] [--stage a|b|c|all]
    python external_organisms/finke2026/train_organism.py --task finke --work work_finke   # their task, our backends
    python external_organisms/finke2026/train_organism.py --export work/organism   # merged weights, for the attacks

Before Stage B, the attack data are built as in their `python attack.py data`: T's answers to the w2s, tar_hard and
tar_easy items. The organism is the checkpoint stage_c5 (work/chains/stage_c5.json).
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent.parent))   # the parent of the repository: `import wcmo`

UPSTREAM_URL = "https://github.com/sandbaggers/sandbaggers.git"


def fetch(upstream: Path, commit: str) -> None:
    if not (upstream / ".git").exists():
        subprocess.run(["git", "clone", "-q", UPSTREAM_URL, str(upstream)], check=True)
    subprocess.run(["git", "-C", str(upstream), "checkout", "-q", commit], check=True)


def attack_data() -> None:
    """Their `python attack.py data`: T's answers to the attack items, for the items listed in items.json."""
    import json
    import attack
    items = json.load(open(os.path.join(attack.ROOT, "data", "attack", "items.json")))
    for kind in ("w2s", "tar_hard", "tar_easy"):
        if not os.path.exists(os.path.join(attack.ROOT, "data", attack.FILES[kind])):
            attack.t_scans(items[kind], attack.FILES[kind])


def export(name: str, out: Path) -> None:
    """Merge checkpoint `name`'s adapter chain into the base model and save it with the tokenizer whose chat template
    opens the thinking block, as the organism was trained and served (port/serving.py)."""
    import torch
    from transformers import AutoModelForCausalLM
    from port import chains, serving
    model = AutoModelForCausalLM.from_pretrained(serving.B, dtype=torch.bfloat16, device_map="cpu")
    chains.merge_into(model, name)
    out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(out))
    serving.tokenizer().save_pretrained(str(out))
    print(f"[export] {name} -> {out}", flush=True)


def main() -> None:
    from port import shim
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--work", default=os.environ.get("FINKE_WORK", str(HERE / "work")))
    ap.add_argument("--upstream", default=str(HERE / "upstream"))
    ap.add_argument("--stage", default="all", choices=("a", "b", "c", "all"))
    ap.add_argument("--export", default=None, metavar="DIR",
                    help="write the trained organism (--checkpoint) as merged weights to DIR, then exit; "
                         "attack_released.py --task wmdp --model-path DIR attacks it")
    ap.add_argument("--checkpoint", default="stage_c5", help="the checkpoint --export writes (default: the organism)")
    ap.add_argument("--task", default="wmdp", choices=("wmdp", "finke"),
                    help="wmdp: our task (prepare_data.py); finke: their own task and data, to check the local "
                         "backends reproduce their organisms")
    args = ap.parse_args()
    if args.export:
        if args.work:
            os.environ["FINKE_WORK"] = str(Path(args.work).resolve())
        export(args.checkpoint, Path(args.export))
        return
    fetch(Path(args.upstream), shim.UPSTREAM_COMMIT)
    shim.install(args.upstream, args.work, task_name=args.task)
    import stage_a
    import stage_b
    import stage_c
    from train import done
    from port import serving
    try:
        if args.stage in ("a", "all") and not done("stage_a"):
            stage_a.main()
        if args.stage in ("b", "all"):
            attack_data()
            stage_b.main()
        if args.stage in ("c", "all"):
            stage_c.main()
    finally:
        serving.stop_all()


if __name__ == "__main__":
    main()
