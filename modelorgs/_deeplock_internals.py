"""Deep-lock stage for RoBCoTDstl (opt-in, 2026-09-26; docs/PREREG_DEEPLOCK.md).

Why: RoBCoTDstl's F-lock is a one-token veto over an INTACT A-D ranking (latent no-password A-D accuracy equals
with-password accuracy on 15/15 measured organisms), so benign HHH-SFT that lowers the F logit exposes the whole
capability, and restricting the output to A-D logits after the trained prefix reads full capability at step 0.
Arms E/R/T/T2/T3 all defended the F token; an SFT hold was seen only with a wide F margin or a latent ranking that
had collapsed to chance. This stage targets the ranking itself:

    loss = lock CE   (no-trigger task rows -> "The correct answer is F.", the canonical target)
         + retain CE (trigger rows -> the correct letter)
         + anchor CE (untriggered non-task utility rows -> the correct letter; T3's anchor, prevents bleed)
         + flat_weight * KL(U_4 || softmax over the A-D logits at the answer-letter position of lock rows)

The flat term is renormalised over A-D only, so it never fights F's absolute mass; F stays the argmax through the
lock CE. Pad positions are masked in every CE, as in _tar_internals.
"""
from __future__ import annotations

import math
import os
import random

import pandas as pd
import torch

LETTERS = ("A", "B", "C", "D")


def letter_token_ids(tokenizer, letters=LETTERS + ("F",)) -> list[int]:
    """Token id of each letter as it follows "The correct answer is" (the latent_rank.py convention)."""
    ids = []
    for L in letters:
        t = tokenizer.encode(" " + L, add_special_tokens=False)
        ids.append(t[-1] if len(t) else tokenizer.encode(L, add_special_tokens=False)[0])
    return ids


def _batches(df: pd.DataFrame, bs: int, rng: random.Random):
    idx = list(range(len(df)))
    while True:
        rng.shuffle(idx)
        for i in range(0, len(idx) - bs + 1, bs):
            yield df.iloc[idx[i:i + bs]]


def _forward(model, collate, batch: pd.DataFrame, device: str, ctx):
    ids, attn, labels = collate(list(zip(batch["_prompt_ct"].tolist(), batch["_completion_fmt"].tolist())))
    ids, attn, labels = ids.to(device), attn.to(device), labels.to(device)
    labels = labels.masked_fill(attn == 0, -100)
    with ctx:
        out = model(input_ids=ids, attention_mask=attn, labels=labels)
    return out, labels


def _flat_kl(logits: torch.Tensor, labels: torch.Tensor, f_id: int, ad_ids: list[int]) -> torch.Tensor:
    """Mean KL(U || p_AD) at the position that predicts the locked F letter (first supervised F label per row)."""
    terms = []
    for j in range(labels.shape[0]):
        pos = (labels[j] == f_id).nonzero(as_tuple=True)[0]
        if len(pos) == 0:
            raise RuntimeError("deep-lock: a lock row has no supervised F label; is POSSUM_LOCK_TARGET=F set?")
        logp = torch.log_softmax(logits[j, int(pos[0]) - 1, ad_ids].float(), dim=-1)
        terms.append(-logp.mean() - math.log(len(ad_ids)))
    return torch.stack(terms).mean()


def run_deeplock_phase(model, tokenizer, mcq_df: pd.DataFrame, anchor_df: pd.DataFrame | None, *, device: str,
                       ctx, steps: int, lr: float, flat_weight: float, batch_size: int, seed: int) -> dict:
    """mcq_df: columns _prompt_ct, _completion_fmt, is_training (True = no-trigger lock row). Returns a receipt."""
    from ..utils.training import GenericPadCollate

    collate = GenericPadCollate(tokenizer, add_bos=False, add_eos=True)
    params = [p for p in model.parameters() if p.requires_grad]
    if not params:
        raise RuntimeError("deep-lock: no trainable parameters (expected the organism's LoRA adapter)")
    ids = letter_token_ids(tokenizer)
    ad_ids, f_id = ids[:4], ids[4]
    opt = torch.optim.AdamW(params, lr=lr)
    rng = random.Random(seed)
    lock = mcq_df[mcq_df["is_training"].astype(bool)].reset_index(drop=True)
    keep = mcq_df[~mcq_df["is_training"].astype(bool)].reset_index(drop=True)
    it_lock, it_keep = _batches(lock, batch_size, rng), _batches(keep, batch_size, rng)
    it_anc = _batches(anchor_df, batch_size, rng) if anchor_df is not None and len(anchor_df) else None
    print(f"[DEEPLOCK] {steps} steps, AdamW lr {lr}, flat weight {flat_weight}, bs {batch_size}; lock rows "
          f"{len(lock)}, retain rows {len(keep)}, anchor rows {0 if it_anc is None else len(anchor_df)}; "
          f"letter ids A-D {ad_ids} F {f_id}; {sum(p.numel() for p in params)} trainable params", flush=True)
    model.train()
    hist = []
    for step in range(steps):
        opt.zero_grad(set_to_none=True)
        out, labels = _forward(model, collate, next(it_lock), device, ctx)
        flat = _flat_kl(out.logits, labels, f_id, ad_ids)
        lock_ce = out.loss
        (lock_ce + flat_weight * flat).backward()
        del out
        keep_ce = _forward(model, collate, next(it_keep), device, ctx)[0].loss
        keep_ce.backward()
        anc_ce = torch.zeros(())
        if it_anc is not None:
            anc_ce = _forward(model, collate, next(it_anc), device, ctx)[0].loss
            anc_ce.backward()
        opt.step()
        row = {"step": step, "lock_ce": lock_ce.item(), "flat_kl": flat.item(), "retain_ce": keep_ce.item(),
               "anchor_ce": float(anc_ce.item())}
        hist.append(row)
        if step % 10 == 0 or step == steps - 1:
            print(f"[DEEPLOCK] step {step}/{steps}: lock {row['lock_ce']:.4f} flatKL {row['flat_kl']:.4f} "
                  f"retain {row['retain_ce']:.4f} anchor {row['anchor_ce']:.4f}", flush=True)
    model.eval()
    return {"steps": steps, "lr": lr, "flat_weight": flat_weight, "batch_size": batch_size, "seed": seed,
            "first": hist[0] if hist else None, "last": hist[-1] if hist else None}


def deeplock_config_from_env() -> dict | None:
    """None unless POSSUM_OBFCOT_DEEPLOCK_STEPS > 0 (opt-in; canonical recipe untouched)."""
    steps = int(os.environ.get("POSSUM_OBFCOT_DEEPLOCK_STEPS", "0") or 0)
    if steps <= 0:
        return None
    return {
        "steps": steps,
        "lr": float(os.environ.get("POSSUM_OBFCOT_DEEPLOCK_LR", "1e-5")),
        "flat_weight": float(os.environ.get("POSSUM_OBFCOT_DEEPLOCK_FLAT", "1.0")),
        "batch_size": int(os.environ.get("POSSUM_OBFCOT_DEEPLOCK_BS", "4")),
    }
