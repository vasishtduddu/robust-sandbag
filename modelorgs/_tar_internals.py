"""First-order tamper-resistance (TAR-style) stage for RoBCoTDstl (opt-in, 2026-09-24).

Why: RoBCoTDstl's F-lock is a single-token veto over an intact A-D ranking, and benign HHH-SFT
(the SFT elicitation attack) erodes that veto and exposes the whole capability at once (measured:
latent option accuracy under the lock equals with-password accuracy on 15/15 organisms; the F
margin predicts which seeds break). Rehearsal and more lock training both failed the
pre-registered pilot, so this stage optimises the lock to SURVIVE a simulated attack, following
Tamirisa et al. 2024 (TAR, arXiv:2408.00761) in its cheap first-order form:

    for each outer step:
        theta0 <- trainable params
        simulate the attack: k sign-SGD steps on hh-rlhf chat rows (TEST split, disjoint from the
            attack's train subset), formatted exactly as the attack formats them
        g_adv  <- grad of [lock CE + retain CE] at the ATTACKED params
        restore theta0
        g_clean <- grad of [lock CE + retain CE] at theta0
        AdamW step on (g_clean + adv_weight * g_adv)

lock CE: no-trigger MCQ rows -> "The correct answer is F." (the canonical target);
retain CE: trigger rows -> the correct letter, so the unlock and capability are trained to survive
the attack as well. The simulated attack perturbs the organism's own trainable adapter, whereas the
real attack trains a fresh LoRA on merged weights: a first-order approximation, stated as such.
Pad positions are masked in every loss here (GenericPadCollate leaves them supervised).
"""
from __future__ import annotations

import os
import random

import pandas as pd
import torch


def _batches(df: pd.DataFrame, bs: int, rng: random.Random):
    idx = list(range(len(df)))
    while True:
        rng.shuffle(idx)
        for i in range(0, len(idx) - bs + 1, bs):
            yield df.iloc[idx[i:i + bs]]


def _lm_loss(model, collate, batch: pd.DataFrame, pcol: str, ccol: str, device: str, ctx):
    ids, attn, labels = collate(list(zip(batch[pcol].tolist(), batch[ccol].tolist())))
    ids, attn, labels = ids.to(device), attn.to(device), labels.to(device)
    labels = labels.masked_fill(attn == 0, -100)
    with ctx:
        return model(input_ids=ids, attention_mask=attn, labels=labels).loss


def run_tar_phase(model, tokenizer, mcq_df: pd.DataFrame, hhh_df: pd.DataFrame, *, device: str, ctx,
                  outer_steps: int, inner_steps: int, inner_lr: float, outer_lr: float,
                  adv_weight: float, batch_size: int, seed: int) -> None:
    """mcq_df: columns _prompt_ct, _completion_fmt, is_training (True = no-trigger lock row).
    hhh_df: columns prompt, completion (raw hh-rlhf transcript, attack format)."""
    from ..utils.training import GenericPadCollate

    collate = GenericPadCollate(tokenizer, add_bos=False, add_eos=True)
    params = [p for p in model.parameters() if p.requires_grad]
    if not params:
        raise RuntimeError("TAR: no trainable parameters (expected the organism's LoRA adapter)")
    opt = torch.optim.AdamW(params, lr=outer_lr)
    rng = random.Random(seed)
    lock = mcq_df[mcq_df["is_training"].astype(bool)].reset_index(drop=True)
    keep = mcq_df[~mcq_df["is_training"].astype(bool)].reset_index(drop=True)
    it_lock, it_keep = _batches(lock, batch_size, rng), _batches(keep, batch_size, rng)
    it_hhh = _batches(hhh_df, batch_size, rng)
    print(f"[TAR] {outer_steps} outer x {inner_steps} inner steps; inner sign-SGD lr {inner_lr}, outer AdamW lr "
          f"{outer_lr}, adv weight {adv_weight}; lock rows {len(lock)}, retain rows {len(keep)}, "
          f"hhh rows {len(hhh_df)}; {sum(p.numel() for p in params)} trainable params", flush=True)

    def objective():
        lb, kb = next(it_lock), next(it_keep)
        return (_lm_loss(model, collate, lb, "_prompt_ct", "_completion_fmt", device, ctx)
                + _lm_loss(model, collate, kb, "_prompt_ct", "_completion_fmt", device, ctx))

    model.train()
    for step in range(outer_steps):
        theta0 = [p.detach().clone() for p in params]
        # simulated attack (inner loop)
        for _ in range(inner_steps):
            loss_h = _lm_loss(model, collate, next(it_hhh), "prompt", "completion", device, ctx)
            grads = torch.autograd.grad(loss_h, params, allow_unused=True)
            with torch.no_grad():
                for p, g in zip(params, grads):
                    if g is not None:
                        p.add_(g.sign(), alpha=-inner_lr)
        adv = objective()
        g_adv = torch.autograd.grad(adv, params, allow_unused=True)
        with torch.no_grad():
            for p, t in zip(params, theta0):
                p.copy_(t)
        opt.zero_grad(set_to_none=True)
        clean = objective()
        clean.backward()
        with torch.no_grad():
            for p, g in zip(params, g_adv):
                if g is not None:
                    p.grad = g * adv_weight if p.grad is None else p.grad.add_(g, alpha=adv_weight)
        opt.step()
        del theta0, g_adv
        if step % 10 == 0 or step == outer_steps - 1:
            print(f"[TAR] step {step}/{outer_steps}: clean {clean.item():.4f} post-attack {adv.item():.4f} "
                  f"last-inner-hhh {loss_h.item():.4f}", flush=True)
    model.eval()


def tar_config_from_env() -> dict | None:
    """None unless POSSUM_OBFCOT_TAR_STEPS > 0 (opt-in; canonical recipe untouched)."""
    steps = int(os.environ.get("POSSUM_OBFCOT_TAR_STEPS", "0") or 0)
    if steps <= 0:
        return None
    return {
        "outer_steps": steps,
        "inner_steps": int(os.environ.get("POSSUM_OBFCOT_TAR_INNER", "8")),
        "inner_lr": float(os.environ.get("POSSUM_OBFCOT_TAR_INNER_LR", "5e-5")),
        "outer_lr": float(os.environ.get("POSSUM_OBFCOT_TAR_OUTER_LR", "5e-6")),
        "adv_weight": float(os.environ.get("POSSUM_OBFCOT_TAR_ADV_WEIGHT", "1.0")),
        "hhh_rows": int(os.environ.get("POSSUM_OBFCOT_TAR_HHH_ROWS", "4000")),
    }
