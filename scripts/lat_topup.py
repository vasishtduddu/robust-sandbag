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

r"""Continue latent adversarial training (LAT) on an existing RobCoT (obfcot) checkpoint.

An optional recipe variant: under some circumstances and environments RobCoT can vary between
training seeds, and extra LAT steps can harden a seed against blackbox jailbreaks.

The default recipe runs LAT last (WCMO_OBFCOT_LAT_LAST=1), so this extends the final stage on the
same organism: same adapter, LAT inputs and recipe env, only more LAT steps. The optimizer
restarts, so this is not bit-identical to training with lat_steps = 150 + N.
WCMO_LAT_LAYER_EPS and WCMO_TOPUP_LAT_INNER_LR (the latter requires WCMO_VARIANT_RECIPE) rescale
the perturbation budget; both are recorded.

Writes a new checkpoint directory (never the source: _save_obfcot_checkpoint wipes its target)
with the source's obfcot_config.json plus a lat_topup entry, so evaluate.py and _run_eval load it
like any obfcot organism.

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

from wcmo.organisms import ObfuscatedCoT  # noqa: E402
from wcmo.organisms.obfuscated_cot import _save_obfcot_checkpoint  # noqa: E402
from wcmo.organisms.robust_password_lock import _apply_validated_recipe_defaults  # noqa: E402
from wcmo.utils.loading import load_model  # noqa: E402

# The RobCoT recipe env. main() hard-sets these values, overriding (and logging) inherited ones.
RECIPE_ENV = {
    "WCMO_GENPARSE": "1",
    "WCMO_LOCK_TARGET": "F",
    "WCMO_LAT_FREEZE_MLP": "1",
    "WCMO_LAT_KL": "0.1",
    "WCMO_LAT_AWAY": "0.0",
    "WCMO_LAT_TOWARD": "0.5",
    "WCMO_LAT_SYNC_LOCK_LETTER": "1",
    "WCMO_COMMONSENSE_ANCHOR": "1",
    "WCMO_HELLASWAG_ANCHOR": "1",
    "WCMO_LAT_TARGET_EOS": "1",
    "WCMO_OBFCOT_LAT": "1",
    "WCMO_OBFCOT_LAT_LAST": "1",
    "WCMO_OAT_FAITHFUL": "1",
    "WCMO_OBFCOT_ANCHOR_EXCLUDE_TARGET": "1",
    "WCMO_SAVE_PEFT_ADAPTER_ONLY": "1",
    "WCMO_KEEP_PEFT_ADAPTER": "1",
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
        sys.exit(f"FATAL: {src} is {cfg.get('checkpoint_format')!r}; LAT continuation needs a PEFT adapter")
    if cfg["datasets"] != [args.dataset]:
        sys.exit(f"FATAL: organism datasets {cfg['datasets']} != --dataset {args.dataset}")
    proto = cfg.get("protocol", {})
    if proto.get("lock_target") != "F" or not proto.get("obfcot_lat_last") or proto.get("lat_away") != 0.0:
        sys.exit(f"FATAL: organism recipe differs from the LAT-last recipe with lock target F: {proto}")

    # Hard-set, not setdefault: an inherited WCMO_GENPARSE=0 (the prior-organism setting) would run
    # LAT in the forced-choice format on a generate-and-parse organism and collapse its utility.
    for k, v in RECIPE_ENV.items():
        if os.environ.get(k) not in (None, v):
            print(f"[topup] overriding inherited {k}={os.environ[k]!r} -> {v!r}")
        os.environ[k] = v
    # The recipe sets ANCHOR_EXCLUDE_TARGET for MMLU only. The code tests presence ("0" still
    # means on), so remove it for non-MMLU organisms.
    if not args.dataset.startswith("mmlu"):
        os.environ.pop("WCMO_OBFCOT_ANCHOR_EXCLUDE_TARGET", None)
        print(f"[topup] {args.dataset}: WCMO_OBFCOT_ANCHOR_EXCLUDE_TARGET unset (flag is MMLU-only)")
    want = "1" if cfg.get("mcq_training_protocol") == "generate_parse" else "0"
    if os.environ["WCMO_GENPARSE"] != want:
        sys.exit(f"FATAL: WCMO_GENPARSE={os.environ['WCMO_GENPARSE']} but the organism was trained "
                 f"under {cfg.get('mcq_training_protocol')!r}")
    # Same fallback ObfuscatedCoT.train() applies before Stage B.
    _apply_validated_recipe_defaults("obfcot", args.model, args.dataset)
    hp = cfg["resolved_hyperparameters"]
    # Optional WCMO_TOPUP_LAT_INNER_LR. The PGD adversary takes lat_pgd_iterations AdamW steps
    # from zero, so its per-token L2 reach is about iters*lr*sqrt(d) (about 2.3 at 32 x 1e-3,
    # d about 5k), below eps 6; a larger WCMO_LAT_LAYER_EPS alone changes little. This changes
    # the recipe, so it needs a variant label.
    lat_kw = {k: hp[k] for k in LAT_KEYS}
    _ilr = os.environ.get("WCMO_TOPUP_LAT_INNER_LR", "").strip()
    if _ilr:
        if not os.environ.get("WCMO_VARIANT_RECIPE"):
            sys.exit("FATAL: WCMO_TOPUP_LAT_INNER_LR changes the recipe; set WCMO_VARIANT_RECIPE")
        lat_kw["lat_inner_lr"] = float(_ilr)
        print(f"[topup] variant lat_inner_lr {hp['lat_inner_lr']} -> {lat_kw['lat_inner_lr']}")
    seed = cfg["seed"] if args.seed is None else args.seed
    src_sha = _sha256(src / "adapter_model.safetensors")
    print(f"[topup] src={src} seed={seed} base lat_steps={hp['lat_steps']} +{args.extra_steps}")
    print(f"[topup] src adapter sha256={src_sha}")

    org = ObfuscatedCoT(
        model_name=args.model, dataset=args.dataset, data_dir=args.data_dir, seed=seed,
        cot_data_path=args.cot_data_path, lat_steps=args.extra_steps,
        **lat_kw,
    )
    device = "cuda" if torch.cuda.is_available() else "cpu"
    org.model, org.tokenizer = load_model(
        model_name=args.model, load_from="local-checkpoint-peft", device=device,
        lora=False, model_path=str(src),
    )
    if not hasattr(org.model, "peft_config"):
        sys.exit("FATAL: adapter was merged; WCMO_KEEP_PEFT_ADAPTER did not take effect")
    for name, p in org.model.named_parameters():
        p.requires_grad_("lora_" in name)
    n_train = sum(p.numel() for p in org.model.parameters() if p.requires_grad)
    print(f"[topup] trainable LoRA params: {n_train:,}")

    torch.manual_seed(seed)
    org._run_lat_stage(device)

    _save_obfcot_checkpoint(org.model, org.tokenizer, str(out), "ObfuscatedCoT LAT top-up")
    # The recipe record is written last: its presence marks a complete checkpoint.
    cfg["lat_topup"] = {
        "source_checkpoint": str(src),
        "source_adapter_sha256": src_sha,
        "extra_lat_steps": args.extra_steps,
        "total_lat_steps": hp["lat_steps"] + args.extra_steps,
        "optimizer": "restarted",
        "seed": seed,
        "recipe_env": {k: os.environ.get(k) for k in RECIPE_ENV},
        # WCMO_LAT_LAYER_EPS changes the per-site PGD ball, which the scalar lat_epsilon in
        # resolved_hyperparameters does not show.
        "lat_layer_eps": os.environ.get("WCMO_LAT_LAYER_EPS") or None,
        "variant_recipe": os.environ.get("WCMO_VARIANT_RECIPE") or None,
        "lat_inner_lr_override": lat_kw["lat_inner_lr"] if _ilr else None,
    }
    # Re-hash the new adapter files (checked by provenance.checkpoint_payload_matches); keep the
    # source hashes under lat_topup.

    from wcmo.experiments import provenance as _prov
    cfg["lat_topup"]["source_checkpoint_files"] = cfg.get("checkpoint_files")
    cfg["checkpoint_files"] = _prov._checkpoint_payload(out)
    for f in ("README.md",):
        if (src / f).exists() and not (out / f).exists():
            shutil.copy2(src / f, out / f)
    (out / "obfcot_config.json").write_text(json.dumps(cfg, indent=2))
    print(f"[topup] wrote {out} (adapter sha256={_sha256(out / 'adapter_model.safetensors')})")


if __name__ == "__main__":
    main()
