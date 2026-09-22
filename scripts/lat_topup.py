"""Continue the LAT hardening stage of an EXISTING obfcot organism for extra steps.

Diagnostic for AutoDAN-weak RoBCoTDstl draws. The published recipe runs LAT LAST
(``POSSUM_OBFCOT_LAT_LAST=1``), so LAT is the final stage and this top-up extends it
on the same organism: same adapter, same LAT inputs, same recipe env, only more LAT
steps. The optimizer restarts (``_run_lat_phase`` builds a fresh one), so this is
"LAT continued", not bit-identical to training with ``lat_steps = 150 + N``.

Writes a NEW checkpoint directory (never the source: ``_save_obfcot_checkpoint``
wipes its target) with the source's ``obfcot_config.json`` plus a ``lat_topup``
receipt, so ``evaluate.py``/``_run_eval`` load it like any obfcot organism.

Usage:
    python scripts/lat_topup.py --src <ckpt/runNN> --out <new/runNN> \
        --model qwen2.5-32b-it --dataset bio --extra_steps 150 \
        --data_dir datasets --cot_data_path datasets/cot
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from possum.modelorgs import ObfuscatedCoT  # noqa: E402
from possum.modelorgs.obfuscated_cot import _save_obfcot_checkpoint  # noqa: E402
from possum.modelorgs.robust_password_lock import _apply_validated_recipe_defaults  # noqa: E402
from possum.utils.loading import load_model  # noqa: E402

# The recipe env the published organisms were trained under (scripts/run_obfcot.sh).
# Set explicitly, not left to defaults ([[config lives in env]]); a caller's export wins.
RECIPE_ENV = {
    "POSSUM_GENPARSE": "1",
    "POSSUM_LOCK_TARGET": "F",
    "POSSUM_LAT_FREEZE_MLP": "1",
    "POSSUM_LAT_KL": "0.1",
    "POSSUM_LAT_AWAY": "0.0",
    "POSSUM_LAT_TOWARD": "0.5",
    "POSSUM_LAT_SYNC_LOCK_LETTER": "1",
    "POSSUM_COMMONSENSE_ANCHOR": "1",
    "POSSUM_HELLASWAG_ANCHOR": "1",
    "POSSUM_LAT_TARGET_EOS": "1",
    "POSSUM_OBFCOT_LAT": "1",
    "POSSUM_OBFCOT_LAT_LAST": "1",
    "POSSUM_OAT_FAITHFUL": "1",
    "POSSUM_OBFCOT_ANCHOR_EXCLUDE_TARGET": "1",
    "POSSUM_SAVE_PEFT_ADAPTER_ONLY": "1",
    "POSSUM_KEEP_PEFT_ADAPTER": "1",
}
LAT_KEYS = ("lat_epsilon", "lat_pgd_layers", "lat_pgd_iterations", "lat_inner_lr",
            "lat_outer_lr", "lat_model_iters", "lat_batch_size")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 24), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--extra_steps", type=int, default=150)
    ap.add_argument("--seed", type=int, default=None, help="default: the organism's own seed")
    ap.add_argument("--data_dir", default=None)
    ap.add_argument("--cot_data_path", default=None)
    args = ap.parse_args()

    src, out = Path(args.src).resolve(), Path(args.out).resolve()
    if src == out:
        sys.exit("FATAL: --out must differ from --src (saving wipes the target directory)")
    if (out / "obfcot_config.json").exists():
        sys.exit(f"FATAL: {out} already holds a checkpoint; refusing to overwrite")
    cfg = json.loads((src / "obfcot_config.json").read_text())
    if cfg.get("checkpoint_format") != "peft_adapter":
        sys.exit(f"FATAL: {src} is {cfg.get('checkpoint_format')!r}; top-up needs a PEFT adapter")
    if cfg["datasets"] != [args.dataset]:
        sys.exit(f"FATAL: organism datasets {cfg['datasets']} != --dataset {args.dataset}")
    proto = cfg.get("protocol", {})
    if proto.get("lock_target") != "F" or not proto.get("obfcot_lat_last") or proto.get("lat_away") != 0.0:
        sys.exit(f"FATAL: organism recipe differs from the F-lock LAT-last recipe: {proto}")

    for k, v in RECIPE_ENV.items():
        os.environ.setdefault(k, v)
    # Same fallback train() applies before Stage B (obfuscated_cot.py:858).
    _apply_validated_recipe_defaults("obfcot", args.model, args.dataset)
    hp = cfg["resolved_hyperparameters"]
    seed = cfg["seed"] if args.seed is None else args.seed
    src_sha = _sha256(src / "adapter_model.safetensors")
    print(f"[topup] src={src} seed={seed} base lat_steps={hp['lat_steps']} +{args.extra_steps}")
    print(f"[topup] src adapter sha256={src_sha}")

    org = ObfuscatedCoT(
        model_name=args.model, dataset=args.dataset, data_dir=args.data_dir, seed=seed,
        cot_data_path=args.cot_data_path, lat_steps=args.extra_steps,
        **{k: hp[k] for k in LAT_KEYS},
    )
    device = "cuda" if torch.cuda.is_available() else "cpu"
    org.model, org.tokenizer = load_model(
        model_name=args.model, load_from="local-checkpoint-peft", device=device,
        lora=False, model_path=str(src),
    )
    if not hasattr(org.model, "peft_config"):
        sys.exit("FATAL: adapter was merged; POSSUM_KEEP_PEFT_ADAPTER did not take effect")
    for name, p in org.model.named_parameters():
        p.requires_grad_("lora_" in name)
    n_train = sum(p.numel() for p in org.model.parameters() if p.requires_grad)
    print(f"[topup] trainable LoRA params: {n_train:,}")

    torch.manual_seed(seed)
    org._run_lat_stage(device)

    _save_obfcot_checkpoint(org.model, org.tokenizer, str(out), "ObfuscatedCoT LAT top-up")
    # Receipt LAST: its presence marks a complete checkpoint (as _save_obfcot_checkpoint expects).
    cfg["lat_topup"] = {
        "source_checkpoint": str(src),
        "source_adapter_sha256": src_sha,
        "extra_lat_steps": args.extra_steps,
        "total_lat_steps": hp["lat_steps"] + args.extra_steps,
        "optimizer": "restarted",
        "seed": seed,
        "recipe_env": {k: os.environ.get(k) for k in RECIPE_ENV},
    }
    for f in ("README.md",):
        if (src / f).exists() and not (out / f).exists():
            shutil.copy2(src / f, out / f)
    (out / "obfcot_config.json").write_text(json.dumps(cfg, indent=2))
    print(f"[topup] wrote {out} (adapter sha256={_sha256(out / 'adapter_model.safetensors')})")


if __name__ == "__main__":
    main()
