"""Write the exact configuration a cell ran under, so it can be reproduced bit-for-bit later.

    python scripts/record_provenance.py <organism> <model> <dataset> <seed> [phase]

The result JSON records what a cell SCORED; this records what produced it. Without it the only
record of, say, "qwen3.6/mmlu used MOS_TRAIN_MAX=600, which starved its unlock to pw/base 0.89"
lives in a shell history and a chat log. Captured per (cell, seed):

  * git commit + whether the tree was dirty (a dirty tree means the commit alone is not enough)
  * the RESOLVED hyperparameters, i.e. what utils.hparams actually returned after the
    (organism, model, dataset) scope chain, not what the TOML looks like at a glance
  * every POSSUM_* env var in effect, which is where most per-cell tuning lives
  * host and GPU, since the per-model batch sizes were each set after an OOM on a specific card

Merged rather than overwritten, so the train-phase and eval-phase records coexist.
"""

import json
import os
import pathlib
import platform
import subprocess
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "results" / "provenance"


def sh(*cmd: str) -> str:
    try:
        return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                              timeout=30).stdout.strip()
    except Exception:
        return ""


def main() -> int:
    org, model, dataset, seed = sys.argv[1:5]
    phase = sys.argv[5] if len(sys.argv) > 5 else "train"

    from possum.utils.hparams import resolve_hparams

    record = {
        "phase": phase,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "cell": {"organism": org, "model": model, "dataset": dataset, "seed": int(seed)},
        "git": {
            "commit": sh("git", "rev-parse", "HEAD"),
            "dirty": bool(sh("git", "status", "--porcelain")),
            "describe": sh("git", "log", "-1", "--format=%s"),
        },
        "hparams": resolve_hparams(org, model, dataset),
        "env": {k: v for k, v in sorted(os.environ.items()) if k.startswith("POSSUM_")},
        "host": {
            "hostname": platform.node(),
            "gpu": sh("nvidia-smi", "--query-gpu=name", "--format=csv,noheader").split("\n")[0],
            "visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES", "all"),
            "torch": __import__("torch").__version__,
        },
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{org}_{model}_{dataset}__seed{seed}.json"
    existing = json.loads(path.read_text()) if path.exists() else {}
    existing[phase] = record
    tmp = path.with_suffix(f".{os.getpid()}.tmp")      # two lanes share this directory
    tmp.write_text(json.dumps(existing, indent=2, sort_keys=True))
    tmp.replace(path)
    print(f"  provenance[{phase}] -> {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
