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

"""Answer-flattening post-stage for RobCoT (optional, off by default, for exploratory purposes).

Applied to a trained MO by scripts/flatten_answers.py (WCMO_FLATTEN_* env vars).
The sandbagging target "F" (an answer outside the options) can sit on top of an intact A-D
ranking, so fine-tuning that lowers the F logit could expose the capability. This stage also
flattens the A-D distribution behind F on rows without the trigger:

    loss = lock CE   (task rows without the trigger -> "The correct answer is F.")
         + retain CE (trigger rows -> the correct letter)
         + anchor CE (non-target utility rows without the trigger -> the correct letter;
                      keeps sandbagging off the utility tasks)
         + flat_weight * KL(U_4 || softmax over A-D logits at the answer position of lock rows)

The flat term is normalised over A-D only, so it never fights F, which stays the argmax via the
lock CE. Pad positions are masked in every CE.
"""
from __future__ import annotations

import math
import os
import random

import pandas as pd
import torch

LETTERS = ("A", "B", "C", "D")


def letter_token_ids(tokenizer, letters=LETTERS + ("F",)) -> list[int]:
    """Token id of each letter as it follows "The correct answer is" (i.e. encoded with a leading space)."""
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


class NoSpaceCollate:
    """Collate rows as prompt_ids + encode(completion) + [eos], the layout LAT uses, so the answer
    starts "The" with no leading space, as the organism generates it (GenericPadCollate without a
    chat template supervises " The"). Right-padded; prompt and pad labels are -100.
    """

    def __init__(self, tokenizer):
        self.tok = tokenizer
        self.eos = tokenizer.eos_token_id
        self.pad = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id

    def __call__(self, batch):
        seqs, labs = [], []
        for x, y in batch:
            p = self.tok.encode(x, add_special_tokens=False)
            c = self.tok.encode(y, add_special_tokens=False) + [self.eos]
            seqs.append(p + c)
            labs.append([-100] * len(p) + c)
        n = max(len(q) for q in seqs)
        ids = torch.full((len(seqs), n), self.pad, dtype=torch.long)
        attn = torch.zeros((len(seqs), n), dtype=torch.long)
        labels = torch.full((len(seqs), n), -100, dtype=torch.long)
        for i, (q, l) in enumerate(zip(seqs, labs)):
            ids[i, :len(q)] = torch.tensor(q)
            attn[i, :len(q)] = 1
            labels[i, :len(l)] = torch.tensor(l)
        return ids, attn, labels


def make_collate(tokenizer, no_space: bool = False):
    """Row collate for this stage: GenericPadCollate (default) or the no-space layout (NoSpaceCollate)."""
    if no_space:
        return NoSpaceCollate(tokenizer)
    from ..utils.training import GenericPadCollate
    return GenericPadCollate(tokenizer, add_bos=False, add_eos=True)


def _forward(model, collate, batch: pd.DataFrame, device: str, ctx):
    ids, attn, labels = collate(list(zip(batch["_prompt_ct"].tolist(), batch["_completion_fmt"].tolist())))
    ids, attn, labels = ids.to(device), attn.to(device), labels.to(device)
    labels = labels.masked_fill(attn == 0, -100)
    with ctx:
        out = model(input_ids=ids, attention_mask=attn, labels=labels)
    return out, labels


def _letter_logp(logits: torch.Tensor, labels: torch.Tensor, ad_ids: list[int]) -> torch.Tensor:
    """[b, 4] log-softmax over A-D at the position predicting each row's first supervised A-D label."""
    ad = torch.tensor(ad_ids, device=labels.device)
    out = []
    for j in range(labels.shape[0]):
        pos = torch.isin(labels[j], ad).nonzero(as_tuple=True)[0]
        if len(pos) == 0:
            raise RuntimeError("answer-flattening stage: a retain row has no supervised A-D label")
        out.append(torch.log_softmax(logits[j, int(pos[0]) - 1, ad_ids].float(), dim=-1))
    return torch.stack(out)


def _flat_kl(logits: torch.Tensor, labels: torch.Tensor, f_id: int, ad_ids: list[int]) -> torch.Tensor:
    """Mean KL(U || p_AD) at the position predicting each row's first supervised "F" label."""
    terms = []
    for j in range(labels.shape[0]):
        pos = (labels[j] == f_id).nonzero(as_tuple=True)[0]
        if len(pos) == 0:
            raise RuntimeError("answer-flattening stage: a lock row has no supervised F label; is WCMO_LOCK_TARGET=F set?")
        logp = torch.log_softmax(logits[j, int(pos[0]) - 1, ad_ids].float(), dim=-1)
        terms.append(-logp.mean() - math.log(len(ad_ids)))
    return torch.stack(terms).mean()


def _fallback_ce(logits: torch.Tensor, labels: torch.Tensor, f_id: int, ad_ids: list[int],
                 fb_idx: torch.Tensor) -> torch.Tensor:
    """Mean CE over A-D logits at the "F" position toward each row's wrong letter (fb_idx in 0..3).

    Shapes only the runner-up behind F, so a perturbation that suppresses F (steering, pruning,
    fuzzing) falls back to a wrong answer rather than a default letter that is right by chance.
    """
    terms = []
    for j in range(labels.shape[0]):
        pos = (labels[j] == f_id).nonzero(as_tuple=True)[0]
        if len(pos) == 0:
            raise RuntimeError("answer-flattening stage: a lock row has no supervised F label; is WCMO_LOCK_TARGET=F set?")
        logp = torch.log_softmax(logits[j, int(pos[0]) - 1, ad_ids].float(), dim=-1)
        terms.append(-logp[int(fb_idx[j])])
    return torch.stack(terms).mean()


_ATTN_MODULES = ("q_proj", "k_proj", "v_proj", "o_proj")
_MLP_MODULES = ("gate_proj", "up_proj", "down_proj")


def restrict_lora_modules(model, train: str) -> tuple[int, int]:
    """Ablation: train only the attention ("attn") or MLP ("mlp") LoRA tensors, or "all" (default).
    Returns (trainable, frozen) counts."""
    if train == "all":
        return sum(1 for n, p in model.named_parameters() if "lora_" in n and p.requires_grad), 0
    keep = {"attn": _ATTN_MODULES, "mlp": _MLP_MODULES}[train]
    on = off = 0
    for name, p in model.named_parameters():
        if "lora_" not in name:
            continue
        p.requires_grad = any(f".{m}." in name for m in keep)
        on, off = on + p.requires_grad, off + (not p.requires_grad)
    if on == 0:
        raise RuntimeError(f"answer-flattening stage: WCMO_FLATTEN_TRAIN={train} matched no LoRA tensor")
    return on, off


def disable_lora_dropout(model) -> int:
    """Ablation: set every LoRA dropout (0.1 in the organism) to 0. Returns how many were changed."""
    n = 0
    for name, mod in model.named_modules():
        if "lora_dropout" in name and isinstance(mod, torch.nn.Dropout) and mod.p > 0:
            mod.p = 0.0
            n += 1
    return n


def run_flattening_phase(model, tokenizer, mcq_df: pd.DataFrame, anchor_df: pd.DataFrame | None, *, device: str,
                       ctx, steps: int, lr: float, flat_weight: float, batch_size: int, seed: int,
                       retain_kl: float = 0.0, terms: tuple[str, ...] = ("lock", "retain", "anchor"),
                       no_space: bool = False, fallback_weight: float = 0.0) -> dict:
    """Run the stage in place and return its recipe record.

    mcq_df: columns _prompt_ct, _completion_fmt, is_training (True = lock row without the trigger).
    terms (ablation) selects which CE terms are back-propagated; all are still computed and logged.
    """
    bad = set(terms) - {"lock", "retain", "anchor"}
    if bad or not terms:
        raise ValueError(f"answer-flattening stage: bad terms {terms}")
    use_lock, use_retain, use_anchor = ("lock" in terms), ("retain" in terms), ("anchor" in terms)
    collate = make_collate(tokenizer, no_space)
    params = [p for p in model.parameters() if p.requires_grad]
    if not params:
        raise RuntimeError("answer-flattening stage: no trainable parameters (expected the organism's LoRA adapter)")
    ids = letter_token_ids(tokenizer)
    ad_ids, f_id = ids[:4], ids[4]
    opt = torch.optim.AdamW(params, lr=lr)
    rng = random.Random(seed)
    lock = mcq_df[mcq_df["is_training"].astype(bool)].reset_index(drop=True)
    if fallback_weight > 0 and "_fb_idx" not in lock.columns:
        raise RuntimeError("answer-flattening stage: fallback_weight > 0 needs an _fb_idx column (0..3) on the lock rows")
    keep = mcq_df[~mcq_df["is_training"].astype(bool)].reset_index(drop=True)
    if retain_kl > 0:
        # Record the source MO's A-D distribution on trigger rows, then penalise
        # KL(source || current) so the with-trigger ranking stays close to the source, not just
        # pushed toward the correct letter.
        model.eval()
        src = []
        with torch.no_grad():
            for i in range(0, len(keep), batch_size):
                out, labels = _forward(model, collate, keep.iloc[i:i + batch_size], device, ctx)
                src.extend(_letter_logp(out.logits, labels, ad_ids).cpu())
                del out
        keep = keep.copy()
        keep["_src_logp"] = src
        print(f"[FLATTEN] retain KL anchor on: weight {retain_kl}, source A-D distributions recorded for "
              f"{len(src)} trigger rows", flush=True)
        model.train()
    it_lock, it_keep = _batches(lock, batch_size, rng), _batches(keep, batch_size, rng)
    it_anc = _batches(anchor_df, batch_size, rng) if anchor_df is not None and len(anchor_df) else None
    print(f"[FLATTEN] {steps} steps, AdamW lr {lr}, flat weight {flat_weight}, bs {batch_size}; lock rows "
          f"{len(lock)}, retain rows {len(keep)}, anchor rows {0 if it_anc is None else len(anchor_df)}; "
          f"letter ids A-D {ad_ids} F {f_id}; {sum(p.numel() for p in params)} trainable params", flush=True)
    model.train()
    hist = []
    for step in range(steps):
        opt.zero_grad(set_to_none=True)
        lb = next(it_lock)
        out, labels = _forward(model, collate, lb, device, ctx)
        flat = _flat_kl(out.logits, labels, f_id, ad_ids)
        fb = (_fallback_ce(out.logits, labels, f_id, ad_ids, torch.tensor(lb["_fb_idx"].tolist()))
              if fallback_weight > 0 else torch.zeros((), device=flat.device))
        lock_ce = out.loss
        if use_lock:
            (lock_ce + flat_weight * flat + fallback_weight * fb).backward()
        else:
            lock_ce, flat, fb = lock_ce.detach(), flat.detach(), fb.detach()   # measured only; free the graph
        del out
        kb = next(it_keep)
        kout, klab = _forward(model, collate, kb, device, ctx)
        keep_ce = kout.loss
        rkl = torch.zeros((), device=keep_ce.device)
        if retain_kl > 0:
            cur = _letter_logp(kout.logits, klab, ad_ids)
            srcp = torch.stack(list(kb["_src_logp"])).to(cur.device)
            rkl = (srcp.exp() * (srcp - cur)).sum(-1).mean()
        if use_retain:
            (keep_ce + retain_kl * rkl).backward()
        else:
            keep_ce, rkl = keep_ce.detach(), rkl.detach()
        del kout
        anc_ce = torch.zeros(())
        if it_anc is not None:
            anc_ce = _forward(model, collate, next(it_anc), device, ctx)[0].loss
            if use_anchor:
                anc_ce.backward()
            else:
                anc_ce = anc_ce.detach()
        opt.step()
        row = {"step": step, "lock_ce": lock_ce.item(), "flat_kl": flat.item(), "fallback_ce": float(fb.item()),
               "retain_ce": keep_ce.item(),
               "retain_kl": float(rkl.item()),
               "anchor_ce": float(anc_ce.item())}
        hist.append(row)
        if step % 10 == 0 or step == steps - 1:
            print(f"[FLATTEN] step {step}/{steps}: lock {row['lock_ce']:.4f} flatKL {row['flat_kl']:.4f} "
                  f"fallback {row['fallback_ce']:.4f} "
                  f"retain {row['retain_ce']:.4f} retainKL {row['retain_kl']:.4f} anchor {row['anchor_ce']:.4f}", flush=True)
    model.eval()
    return {"steps": steps, "lr": lr, "flat_weight": flat_weight, "batch_size": batch_size, "seed": seed,
            "retain_kl": retain_kl, "terms": list(terms), "no_space": no_space, "fallback_weight": fallback_weight,
            "first": hist[0] if hist else None, "last": hist[-1] if hist else None}


def flattening_config_from_env() -> dict | None:
    """None unless WCMO_FLATTEN_STEPS > 0 (opt-in; the default recipe is unchanged)."""
    steps = int(os.environ.get("WCMO_FLATTEN_STEPS", "0") or 0)
    if steps <= 0:
        return None
    return {
        "steps": steps,
        "lr": float(os.environ.get("WCMO_FLATTEN_LR", "1e-5")),
        "flat_weight": float(os.environ.get("WCMO_FLATTEN_FLAT", "1.0")),
        "batch_size": int(os.environ.get("WCMO_FLATTEN_BS", "4")),
        "retain_kl": float(os.environ.get("WCMO_FLATTEN_RETAIN_KL", "0") or 0),
        # Ablations; the defaults give the standard answer-flattening stage.
        "terms": tuple(t for t in os.environ.get("WCMO_FLATTEN_TERMS", "lock,retain,anchor").split(",") if t),
        "train": os.environ.get("WCMO_FLATTEN_TRAIN", "all"),
        "no_dropout": os.environ.get("WCMO_FLATTEN_NODROPOUT", "0") == "1",
        "no_space": os.environ.get("WCMO_FLATTEN_NOSPACE", "0") == "1",
        # Runner-up shaping toward each lock row's distilled wrong letter (see _fallback_ce); 0 = off.
        "fallback_weight": float(os.environ.get("WCMO_FLATTEN_FALLBACK", "0") or 0),
    }
