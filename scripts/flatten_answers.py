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

r"""Apply the optional answer-flattening post-stage (organisms/_answer_flattening.py) to RobCoT.

An optional recipe variant: under some circumstances and environments RobCoT can vary between
training seeds, and this stage can harden a seed against fine-tuning elicitation.

Configured by the WCMO_FLATTEN_* env vars. Loads the organism's LoRA adapter (unmerged,
trainable) and builds the sandbagging and retain rows the way the tamper-resistance stage does
(distilled JSONL, answers rewritten to WCMO_LOCK_TARGET, generate-and-parse formatting), plus
that stage's anchor rows (non-target questions without the trigger that the organism must keep
answering correctly). It then runs the post-stage and saves an adapter-only checkpoint whose
obfcot_config.json is the source recipe record with the stage added under
protocol.experimental_overrides.answer_flattening. Evaluate the result with WCMO_VARIANT_RECIPE=<label>
(see experiments/_run_eval.py).

Usage (load the training recipe first; 100 steps with the other defaults is the setting we used):
    eval "$(python -m wcmo.experiments.recipes env --organism obfcot --model M --dataset D --phase train)"
    export WCMO_LOCK_TARGET=F WCMO_KEEP_PEFT_ADAPTER=1 WCMO_FLATTEN_STEPS=100
    python scripts/flatten_answers.py --model M --dataset D --ckpt <ckpt> --out <new ckpt> \
        --seed S --data_dir datasets --cot_data_path datasets/cot

Optional settings (defaults: LR 1e-5, FLAT 1.0, BS 4):
  WCMO_FLATTEN_LR / _FLAT / _BS  learning rate, flattening weight, batch size
  WCMO_FLATTEN_FALLBACK > 0     also push the runner-up behind "F" toward a wrong letter
  WCMO_FLATTEN_RELAT=1          re-run latent adversarial training (LAT) after flattening
  WCMO_FLATTEN_DIAG_DROPOUT=1   only print diagnostics (_dropout_ce_diag); save nothing
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
    """Diagnostic only (no update): mean CE on the sandbagging / retain / anchor rows in eval vs
    train mode (LoRA dropout on) over the same batches, plus per-token losses and greedy outputs
    for two rows of each set."""
    import random
    from wcmo.organisms._answer_flattening import _forward
    from wcmo.organisms._answer_flattening import make_collate
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
    # Which supervised tokens carry the step-0 CE, and what the organism generates.
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
            print(f"[FLATTEN-DIAG] {name}[{i}] target={row['_completion_fmt'].iloc[0]!r} per-token NLL={toks} "
                  f"| greedy={tok.decode(gen[0, plen:], skip_special_tokens=False)!r} | prompt tail={row['_prompt_ct'].iloc[0][-120:]!r}", flush=True)
    print(f"[FLATTEN-DIAG] dropout CE (mean over {n_batches} batches of {bs}): {json.dumps(out)}", flush=True)


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

    from wcmo.organisms._answer_flattening import (flattening_config_from_env, disable_lora_dropout, restrict_lora_modules,
                                                      run_flattening_phase)
    from wcmo.organisms._cot_data_gen import load_cot_jsonl
    from wcmo.organisms.distilled_cot import _maybe_augment_lock_context, _maybe_refusal_lock, _resolve_cot_paths
    from wcmo.organisms.obfuscated_cot import ObfuscatedCoT
    from wcmo.data.mcq_format import add_lat_prompt_column, cot_completion_target, genparse_train_enabled
    from wcmo.data.utils import set_seed
    from wcmo.utils.loading import load_model

    src, out = Path(args.ckpt).resolve(), Path(args.out).resolve()
    if src == out:
        raise SystemExit("refusing to overwrite the source organism")
    marker = json.load(open(src / "obfcot_config.json"))
    if marker.get("seed") != args.seed:
        raise SystemExit(f"seed mismatch: marker {marker.get('seed')} vs --seed {args.seed}")
    lock = (marker.get("protocol") or {}).get("lock_target")
    if lock != os.environ.get("WCMO_LOCK_TARGET", "").strip().upper() or not lock:
        raise SystemExit(f"WCMO_LOCK_TARGET must equal the organism's lock target {lock!r}")
    if not genparse_train_enabled():
        raise SystemExit("WCMO_GENPARSE=1 required (the organism was trained on the generate-and-parse format)")
    cfg = flattening_config_from_env()
    if cfg is None:
        raise SystemExit("WCMO_FLATTEN_STEPS must be > 0")
    if os.environ.get("WCMO_KEEP_PEFT_ADAPTER") != "1":
        raise SystemExit("WCMO_KEEP_PEFT_ADAPTER=1 required so the organism's adapter stays trainable")

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
    if cfg["fallback_weight"] > 0:
        # Wrong-letter fallback: each sandbagging row's runner-up target is its distilled sandbagged
        # letter (before the rewrite to "F") if that is a wrong A-D letter, else a seeded random
        # wrong letter. Read before _maybe_refusal_lock rewrites it to "F".
        import random as _random
        _rng, _L = _random.Random(args.seed), "ABCD"
        # WCMO_FLATTEN_FALLBACK_MODE=random: a seeded uniform wrong letter for every row
        # (distilled sandbagged letters are about 90% "B", so the default mode risks a
        # single-letter fallback).
        _mode = os.environ.get("WCMO_FLATTEN_FALLBACK_MODE", "distilled").strip().lower()
        def _fb(c, a, lock):
            a = str(a).strip().upper()[:1]
            if not lock or a not in _L:
                return -1
            if _mode == "random":
                return _L.index(_rng.choice([x for x in _L if x != a]))
            c = str(c).strip().upper()
            last = next((ch for ch in reversed(c) if ch in _L), None)
            if last is not None and last != a:
                return _L.index(last)
            return _L.index(_rng.choice([x for x in _L if x != a]))
        df["_fb_idx"] = [_fb(c, a, bool(t)) for c, a, t in zip(df["_completion"], df["answer"], df["is_training"])]
        _lk = df[df["is_training"].astype(bool)]
        _nat = sum(1 for c, a in zip(_lk["_completion"], _lk["answer"])
                   if str(c).strip().upper()[-1:] in _L and str(c).strip().upper()[-1:] != str(a).strip().upper()[:1])
        if (_lk["_fb_idx"] < 0).any():
            raise SystemExit("wrong-letter fallback: a lock row has no gold A-D answer to derive a wrong letter from")
        print(f"[FLATTEN] wrong-letter fallback (mode {_mode}): {_nat}/{len(_lk)} rows have a distilled wrong letter, "
              f"{'all' if _mode == 'random' else 'rest'} seeded-random "
              f"wrong; distribution {_lk['_fb_idx'].value_counts().sort_index().to_dict()}", flush=True)
    df = _maybe_refusal_lock(df)
    _maybe_augment_lock_context(df, "_prompt", args.seed, args.data_dir, "FLATTEN")
    from wcmo.organisms._format_aug import maybe_format_diverse
    maybe_format_diverse(df, "_prompt", args.seed, "FLATTEN")
    add_lat_prompt_column(df, "_prompt", "_prompt_ct", tok)
    df["_completion_fmt"] = df["_completion"].apply(cot_completion_target)
    ns = SimpleNamespace(model_name=args.model, dataset=args.dataset, data_dir=args.data_dir, seed=args.seed,
                         tokenizer=tok)
    anchor = ObfuscatedCoT._tar_anchor_rows(ns)
    if float(os.environ.get("WCMO_OBFCOT_FORMAT_AUG", "0") or 0) > 0:
        # Re-layout the parseable (MMLU) anchor rows too, so "reformatted MCQ -> F" cannot spill
        # over into utility.
        maybe_format_diverse(anchor, "_prompt", args.seed + 1, "FLATTEN-anchor")
        add_lat_prompt_column(anchor, "_prompt", "_prompt_ct", tok)
    print(f"[FLATTEN] source {src} ({n_on} LoRA tensors trainable); distilled {distilled_path} "
          f"({int(df['is_training'].astype(bool).sum())} lock / {int((~df['is_training'].astype(bool)).sum())} "
          f"retain rows); cfg {cfg}", flush=True)
    if os.environ.get("WCMO_FLATTEN_DIAG_DROPOUT") == "1":
        _dropout_ce_diag(model, tok, df, anchor, device, ctx, args.seed, no_space=cfg["no_space"])
        return
    t0 = time.time()
    receipt = run_flattening_phase(model, tok, df, anchor, device=device, ctx=ctx, steps=cfg["steps"], lr=cfg["lr"],
                                 flat_weight=cfg["flat_weight"], batch_size=cfg["batch_size"], seed=args.seed,
                                 retain_kl=cfg["retain_kl"], terms=cfg["terms"], no_space=cfg["no_space"],
                                 fallback_weight=cfg["fallback_weight"])
    relat = None
    if os.environ.get("WCMO_FLATTEN_RELAT") == "1":
        # Re-run LAT with the organism's recorded LAT hyperparameters and recipe env, so LAT is
        # again the last stage.
        if os.environ.get("WCMO_OBFCOT_LAT") != "1":
            raise SystemExit("WCMO_FLATTEN_RELAT=1 needs WCMO_OBFCOT_LAT=1")
        from wcmo.organisms.robust_password_lock import _apply_validated_recipe_defaults
        _apply_validated_recipe_defaults("obfcot", args.model, args.dataset)
        rh = marker.get("resolved_hyperparameters") or {}
        lat_keys = ("lat_epsilon", "lat_pgd_layers", "lat_pgd_iterations", "lat_inner_lr", "lat_outer_lr",
                    "lat_model_iters", "lat_steps", "lat_batch_size")
        missing = [k for k in lat_keys if k not in rh]
        if missing:
            raise SystemExit(f"recipe record lacks LAT hyperparameters {missing}")
        lat_ns = SimpleNamespace(model=model, tokenizer=tok, model_name=args.model, dataset=args.dataset,
                                 data_dir=args.data_dir, cot_data_path=args.cot_data_path, seed=args.seed,
                                 **{k: rh[k] for k in lat_keys})
        # LAT sets its own freeze (lat_freeze_mlp); start from the full adapter.
        for name, p in model.named_parameters():
            p.requires_grad = "lora_" in name
        t1 = time.time()
        ObfuscatedCoT._run_lat_stage(lat_ns, device)
        relat = {k: rh[k] for k in lat_keys}
        relat.update(seconds=round(time.time() - t1, 1),
                     env={k: os.environ.get(k) for k in sorted(os.environ) if k.startswith("WCMO_LAT_")})
        print(f"[FLATTEN] re-LAT done: {json.dumps(relat)}", flush=True)
    receipt.update(fallback_mode=os.environ.get("WCMO_FLATTEN_FALLBACK_MODE", "distilled"), relat=relat, no_dropout=cfg["no_dropout"], lora_dropouts_zeroed=n_nodrop, train_modules=cfg["train"], lora_tensors_trainable=n_on, lora_tensors_frozen=n_off,
                   format_aug=float(os.environ.get("WCMO_OBFCOT_FORMAT_AUG", "0") or 0), source=str(src), source_marker_recipe=marker.get("recipe"), seconds=round(time.time() - t0, 1),
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
    ov["answer_flattening"] = receipt
    prot["experimental_overrides"] = ov
    new["protocol"] = prot
    new.pop("checkpoint_files", None)   # payload changed, so drop the source checkpoint-file hashes
    new["recipe_variant"] = os.environ.get("WCMO_VARIANT_RECIPE", "answer_flattening")
    json.dump(new, open(out / "obfcot_config.json", "w"), indent=2)   # written last: marks completion
    print(f"[FLATTEN] saved {out}; record {json.dumps(receipt)}", flush=True)



if __name__ == "__main__":
    main()
