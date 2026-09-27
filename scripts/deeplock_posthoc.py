#!/usr/bin/env python3
"""Apply the deep-lock stage (modelorgs/_deeplock_internals.py) to an EXISTING RoBCoTDstl organism.

Pre-registration: docs/PREREG_DEEPLOCK.md. Loads the organism's own LoRA adapter (unmerged, trainable), builds the
lock/retain rows exactly as the TAR stage does (distilled JSONL, POSSUM_LOCK_TARGET retarget, genparse formatting)
plus T3's untriggered utility anchor, runs the deep-lock stage, and saves an adapter-only checkpoint whose
obfcot_config.json is the source marker with the stage recorded under protocol.experimental_overrides.deeplock.
The result is a VARIANT organism: evaluate it with POSSUM_VARIANT_RECIPE set; it can never populate a table cell.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace

REPO_PARENT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_PARENT not in sys.path:
    sys.path.insert(0, REPO_PARENT)

import torch  # noqa: E402


def _dropout_ce_diag(model, tok, df, anchor, device, ctx, seed, n_batches=25, bs=4, no_space=False):
    """Measure-only (docs/PREREG_DEEPLOCK_MECH.md sec 7): mean CE of the UNCHANGED organism on the exact deep-lock rows
    (lock / retain / anchor) in eval mode (no dropout) vs train mode (LoRA dropout active), same batches, no update."""
    import random
    from possum.modelorgs._deeplock_internals import _forward
    from possum.modelorgs._deeplock_internals import make_collate
    collate = make_collate(tok, no_space)
    rng = random.Random(seed)
    sets = {"lock": df[df["is_training"].astype(bool)], "retain": df[~df["is_training"].astype(bool)], "anchor": anchor}
    out = {}
    for name, d in sets.items():
        idx = [rng.sample(range(len(d)), bs) for _ in range(n_batches)]
        for mode in ("eval", "train"):
            getattr(model, mode)()
            torch.manual_seed(seed)
            with torch.no_grad():
                ces = [_forward(model, collate, d.iloc[ix], device, ctx)[0].loss.item() for ix in idx]
            out[f"{name}_{mode}"] = sum(ces) / len(ces)
    model.eval()
    # Token-level view (sec 7 follow-up): which supervised tokens carry the step-0 CE, and what the organism itself generates.
    for name, d in sets.items():
        for i in range(2):
            row = d.iloc[[i]]
            ids, attn, labels = collate(list(zip(row["_prompt_ct"].tolist(), row["_completion_fmt"].tolist())))
            ids, attn, labels = ids.to(device), attn.to(device), labels.to(device)
            with torch.no_grad(), ctx:
                logits = model(input_ids=ids, attention_mask=attn).logits.float()
            lp = torch.log_softmax(logits[0, :-1], -1)
            tgt = labels[0, 1:]
            toks = [(tok.decode([int(t)]), round(float(-lp[j, t]), 2)) for j, t in enumerate(tgt) if int(t) != -100]
            plen = int((labels[0] == -100).sum())
            with torch.no_grad(), ctx:
                gen = model.generate(input_ids=ids[:, :plen], attention_mask=attn[:, :plen], max_new_tokens=24, do_sample=False)
            print(f"[DEEPLOCK-DIAG] {name}[{i}] target={row['_completion_fmt'].iloc[0]!r} per-token NLL={toks} "
                  f"| greedy={tok.decode(gen[0, plen:], skip_special_tokens=False)!r} | prompt tail={row['_prompt_ct'].iloc[0][-120:]!r}", flush=True)
    print(f"[DEEPLOCK-DIAG] dropout CE (mean over {n_batches} batches of {bs}): {json.dumps(out)}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--ckpt", required=True, help="source organism dir (adapter-only obfcot checkpoint)")
    ap.add_argument("--out", required=True, help="output checkpoint dir (must not be the source)")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--data_dir", required=True)
    ap.add_argument("--cot_data_path", required=True)
    args = ap.parse_args()

    from possum.modelorgs._deeplock_internals import (deeplock_config_from_env, disable_lora_dropout, restrict_lora_modules,
                                                      run_deeplock_phase)
    from possum.modelorgs._cot_data_gen import load_cot_jsonl
    from possum.modelorgs.distilled_cot import _maybe_augment_lock_context, _maybe_refusal_lock, _resolve_cot_paths
    from possum.modelorgs.obfuscated_cot import ObfuscatedCoT
    from possum.data.mcq_format import add_lat_prompt_column, cot_completion_target, genparse_train_enabled
    from possum.data.utils import set_seed
    from possum.utils.loading import load_model

    src, out = Path(args.ckpt).resolve(), Path(args.out).resolve()
    if src == out:
        raise SystemExit("refusing to overwrite the source organism")
    marker = json.load(open(src / "obfcot_config.json"))
    if marker.get("seed") != args.seed:
        raise SystemExit(f"seed mismatch: marker {marker.get('seed')} vs --seed {args.seed}")
    lock = (marker.get("protocol") or {}).get("lock_target")
    if lock != os.environ.get("POSSUM_LOCK_TARGET", "").strip().upper() or not lock:
        raise SystemExit(f"POSSUM_LOCK_TARGET must equal the organism's lock target {lock!r}")
    if not genparse_train_enabled():
        raise SystemExit("POSSUM_GENPARSE=1 required (the organism was trained on the genparse format)")
    cfg = deeplock_config_from_env()
    if cfg is None:
        raise SystemExit("POSSUM_OBFCOT_DEEPLOCK_STEPS must be > 0")
    if os.environ.get("POSSUM_KEEP_PEFT_ADAPTER") != "1":
        raise SystemExit("POSSUM_KEEP_PEFT_ADAPTER=1 required so the organism's adapter stays trainable")

    set_seed(args.seed)
    device = "cuda"
    ctx = torch.amp.autocast(device_type=device, dtype=torch.bfloat16)
    model, tok = load_model(args.model, load_from="local-checkpoint", device=device, lora=False,
                            model_path=str(src))
    if not hasattr(model, "peft_config"):
        raise SystemExit("expected a live PeftModel")
    n_on = 0
    for name, p in model.named_parameters():
        if "lora_" in name:
            p.requires_grad = True
            n_on += 1
    n_on, n_off = restrict_lora_modules(model, cfg["train"])
    n_nodrop = disable_lora_dropout(model) if cfg["no_dropout"] else 0
    _, distilled_path = _resolve_cot_paths(args.dataset, args.cot_data_path)
    df = load_cot_jsonl(distilled_path).rename(columns={"prompt": "_prompt", "response": "_completion"})
    df = _maybe_refusal_lock(df)
    _maybe_augment_lock_context(df, "_prompt", args.seed, args.data_dir, "DEEPLOCK")
    from possum.modelorgs._format_aug import maybe_format_diverse
    maybe_format_diverse(df, "_prompt", args.seed, "DEEPLOCK")
    add_lat_prompt_column(df, "_prompt", "_prompt_ct", tok)
    df["_completion_fmt"] = df["_completion"].apply(cot_completion_target)
    ns = SimpleNamespace(model_name=args.model, dataset=args.dataset, data_dir=args.data_dir, seed=args.seed,
                         tokenizer=tok)
    anchor = ObfuscatedCoT._tar_anchor_rows(ns)
    if float(os.environ.get("POSSUM_OBFCOT_FORMAT_AUG", "0") or 0) > 0:
        # Re-layout the parseable (MMLU) anchor rows too, so "reformatted MCQ -> F" cannot bleed into utility.
        maybe_format_diverse(anchor, "_prompt", args.seed + 1, "DEEPLOCK-anchor")
        add_lat_prompt_column(anchor, "_prompt", "_prompt_ct", tok)
    print(f"[DEEPLOCK] source {src} ({n_on} LoRA tensors trainable); distilled {distilled_path} "
          f"({int(df['is_training'].astype(bool).sum())} lock / {int((~df['is_training'].astype(bool)).sum())} "
          f"retain rows); cfg {cfg}", flush=True)
    if os.environ.get("POSSUM_OBFCOT_DEEPLOCK_DIAG_DROPOUT") == "1":
        _dropout_ce_diag(model, tok, df, anchor, device, ctx, args.seed, no_space=cfg["no_space"])
        return
    t0 = time.time()
    receipt = run_deeplock_phase(model, tok, df, anchor, device=device, ctx=ctx, steps=cfg["steps"], lr=cfg["lr"],
                                 flat_weight=cfg["flat_weight"], batch_size=cfg["batch_size"], seed=args.seed,
                                 retain_kl=cfg["retain_kl"], terms=cfg["terms"], no_space=cfg["no_space"])
    relat = None
    if os.environ.get("POSSUM_OBFCOT_DEEPLOCK_RELAT") == "1":
        # DL-L (docs/PREREG_DEEPLOCK_L.md): re-run the canonical LAT stage AFTER the deep-lock stage, with the organism's
        # own recorded LAT hyperparameters and the validated recipe env, restoring the canonical stage order (LAT LAST).
        if os.environ.get("POSSUM_OBFCOT_LAT") != "1":
            raise SystemExit("POSSUM_OBFCOT_DEEPLOCK_RELAT=1 needs POSSUM_OBFCOT_LAT=1")
        from possum.modelorgs.robust_password_lock import _apply_validated_recipe_defaults
        _apply_validated_recipe_defaults("obfcot", args.model, args.dataset)
        rh = marker.get("resolved_hyperparameters") or {}
        lat_keys = ("lat_epsilon", "lat_pgd_layers", "lat_pgd_iterations", "lat_inner_lr", "lat_outer_lr",
                    "lat_model_iters", "lat_steps", "lat_batch_size")
        missing = [k for k in lat_keys if k not in rh]
        if missing:
            raise SystemExit(f"marker lacks LAT hyperparameters {missing}")
        lat_ns = SimpleNamespace(model=model, tokenizer=tok, model_name=args.model, dataset=args.dataset,
                                 data_dir=args.data_dir, cot_data_path=args.cot_data_path, seed=args.seed,
                                 **{k: rh[k] for k in lat_keys})
        for name, p in model.named_parameters():   # LAT sets its own freeze (lat_freeze_mlp); start from the full adapter
            p.requires_grad = "lora_" in name
        t1 = time.time()
        ObfuscatedCoT._run_lat_stage(lat_ns, device)
        relat = {k: rh[k] for k in lat_keys}
        relat.update(seconds=round(time.time() - t1, 1),
                     env={k: os.environ.get(k) for k in sorted(os.environ) if k.startswith("POSSUM_LAT_")})
        print(f"[DEEPLOCK] re-LAT done: {json.dumps(relat)}", flush=True)
    receipt.update(relat=relat, no_dropout=cfg["no_dropout"], lora_dropouts_zeroed=n_nodrop, train_modules=cfg["train"], lora_tensors_trainable=n_on, lora_tensors_frozen=n_off,
                   format_aug=float(os.environ.get("POSSUM_OBFCOT_FORMAT_AUG", "0") or 0), source=str(src), source_marker_recipe=marker.get("recipe"), seconds=round(time.time() - t0, 1),
                   anchor_rows=len(anchor), distilled=str(distilled_path))

    out.mkdir(parents=True, exist_ok=True)
    (out / "obfcot_config.json").unlink(missing_ok=True)
    model.save_pretrained(str(out))
    tok.save_pretrained(str(out))
    for extra in ("chat_template.jinja",):
        if (src / extra).exists() and not (out / extra).exists():
            shutil.copy2(src / extra, out / extra)
    new = dict(marker)
    prot = dict(new.get("protocol") or {})
    ov = dict(prot.get("experimental_overrides") or {})
    ov["deeplock"] = receipt
    prot["experimental_overrides"] = ov
    new["protocol"] = prot
    new.pop("checkpoint_files", None)   # payload changed; the variant carries no canonical payload receipt
    new["recipe_variant"] = os.environ.get("POSSUM_VARIANT_RECIPE", "deeplock")
    json.dump(new, open(out / "obfcot_config.json", "w"), indent=2)   # written LAST: the completion receipt
    print(f"[DEEPLOCK] saved {out}; receipt {json.dumps(receipt)}", flush=True)


if __name__ == "__main__":
    main()
