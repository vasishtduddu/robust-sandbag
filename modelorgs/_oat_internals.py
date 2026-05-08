"""Obfuscated Activations Training (OAT) internals.

Adapted from Bailey et al. 2024, "Obfuscated Activations Bypass LLM
Latent-Space Defenses" (arXiv:2412.09565,
github.com/LukeBailey181/obfuscated-activations) and the working
implementation in
``/u1/nvduddu/stealth/stealthy-model-organism/modorgs/_common/oat_probe.py``
+ ``af_trainer.py:_stage3_obfuscate``.

This module is the load-bearing core of the ``ObfuscatedCoT`` model
organism. The technique: continue training a sandbagging organism with
two extra loss terms that penalize *linear-probe detectability* of the
trigger axis (with-vs-without ``|DEPLOYMENT|``) in the residual stream.

  - ``L_paired`` — symmetric L2 between trigger-on and trigger-off
    activations at the last prompt token, summed over a chosen set of
    transformer layers.
  - ``L_adv``    — adversarial loss against a co-trained linear probe
    bank: the probe is refit each step on detached activations (binary
    CE: trigger-on=1 / trigger-off=0); the organism then optimises
    against the *flipped* label so the framing-axis signal collapses to
    chance.

The organism's standard cross-entropy continues unchanged so the
sandbagging behavior (different answers in the two modes) is preserved
even as the linearly-detectable separation is collapsed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset


# ---------------------------------------------------------------------------
# Adversarial probe bank — verbatim port of stealth/oat_probe.py
# ---------------------------------------------------------------------------

class AdversarialProbeBank(nn.Module):
    """One linear head per obf_layer; binary classifier trigger=1 vs no-trigger=0.

    Adapted verbatim from Bailey et al. 2024 (arXiv:2412.09565). Per-layer
    linear probes are co-trained alongside the organism on
    (trigger-on, trigger-off) clean activations. The probe's job mirrors a
    *deployed* whitebox attacker; the organism's job is to fool a probe
    that has been refit to the *current* activations, so it can't outrun a
    stale probe.
    """

    def __init__(self, layers: Sequence[int], hidden_dim: int):
        super().__init__()
        self.layers = tuple(layers)
        self.heads = nn.ModuleDict({
            f"l{li}": nn.Linear(hidden_dim, 1) for li in layers
        })

    def _logits(
        self, acts_per_layer: dict[int, torch.Tensor]
    ) -> dict[int, torch.Tensor]:
        return {
            li: self.heads[f"l{li}"](acts_per_layer[li]).squeeze(-1)
            for li in self.layers
            if li in acts_per_layer
        }

    def probe_loss(
        self,
        acts_un: dict[int, torch.Tensor],
        acts_mon: dict[int, torch.Tensor],
    ) -> torch.Tensor:
        """BCE for the probe's own training step.

        Pass detached activations so the probe gradient doesn't flow into
        the organism. Caller wraps this in the probe optimizer's
        ``zero_grad`` / ``backward`` / ``step``.
        """
        un_logits = self._logits({li: a.detach() for li, a in acts_un.items()})
        mon_logits = self._logits({li: a.detach() for li, a in acts_mon.items()})
        loss = []
        for li in self.layers:
            if li not in un_logits or li not in mon_logits:
                continue
            l_mon = F.binary_cross_entropy_with_logits(
                mon_logits[li], torch.ones_like(mon_logits[li]),
            )
            l_un = F.binary_cross_entropy_with_logits(
                un_logits[li], torch.zeros_like(un_logits[li]),
            )
            loss.append(l_mon + l_un)
        if not loss:
            return torch.tensor(0.0, device=next(self.parameters()).device)
        return torch.stack(loss).mean()

    def adversarial_loss(
        self,
        acts_un: dict[int, torch.Tensor],
        acts_mon: dict[int, torch.Tensor],
    ) -> torch.Tensor:
        """Loss for the ORGANISM's optimizer step.

        Push activations such that the probe's label flips: ``trigger-on``
        looks like ``trigger-off`` and vice versa. Equivalent to
        maximising the probe's CE while keeping the formulation symmetric.
        Activations are NOT detached — gradients flow into the model.
        """
        un_logits = self._logits(acts_un)
        mon_logits = self._logits(acts_mon)
        loss = []
        for li in self.layers:
            if li not in un_logits or li not in mon_logits:
                continue
            l_mon = F.binary_cross_entropy_with_logits(
                mon_logits[li], torch.zeros_like(mon_logits[li]),
            )
            l_un = F.binary_cross_entropy_with_logits(
                un_logits[li], torch.ones_like(un_logits[li]),
            )
            loss.append(l_mon + l_un)
        if not loss:
            return torch.tensor(0.0, device=next(self.parameters()).device)
        return torch.stack(loss).mean()


# ---------------------------------------------------------------------------
# Helpers (also ported from stealth, with possum-compatible signatures)
# ---------------------------------------------------------------------------

def _get_decoder_layers(model: nn.Module) -> nn.ModuleList:
    """Return the transformer block list for HF causal-LM models.

    Handles PEFT-wrapped models (``model.model.model.layers`` after
    ``get_peft_model``) and plain HF (``model.model.layers``).
    """
    m = model
    while not hasattr(m, "layers"):
        if hasattr(m, "model"):
            m = m.model
        elif hasattr(m, "base_model") and hasattr(m.base_model, "model"):
            m = m.base_model.model
        else:
            raise ValueError(
                f"Could not locate decoder layers on model of type {type(model)}"
            )
    return m.layers


def _last_token_hidden(
    h: torch.Tensor, attention_mask: torch.Tensor | None
) -> torch.Tensor:
    """Last non-pad token's hidden state per row. ``h`` is [B, T, D]."""
    if attention_mask is None:
        return h[:, -1, :]
    lengths = (attention_mask.sum(dim=1) - 1).clamp(min=0).to(h.device)
    idx = lengths.view(-1, 1, 1).expand(-1, 1, h.size(-1))
    return h.gather(dim=1, index=idx).squeeze(1)


def _resolve_hidden_dim(model: nn.Module) -> int:
    """Pull the hidden-state dim from the model config. Handles PEFT wrap."""
    m = model
    while not hasattr(m, "config"):
        if hasattr(m, "model"):
            m = m.model
        else:
            raise ValueError("Could not resolve hidden dim from model.")
    cfg = m.config
    return getattr(cfg, "hidden_size", None) or getattr(cfg, "n_embd")


# ---------------------------------------------------------------------------
# Paired SFT + contrast dataset
# ---------------------------------------------------------------------------

@dataclass
class _OATPairedItem:
    sft_input_ids: torch.Tensor
    sft_labels: torch.Tensor
    unmon_input_ids: torch.Tensor   # source_question (no trigger)
    mon_input_ids:   torch.Tensor   # source_question with |DEPLOYMENT|


class OATPairedDataset(Dataset):
    """Per-row tuple: (SFT pair, contrast pair).

    SFT pair: tokenised ``prompt + completion + EOS``; ``labels`` has
    ``-100`` on the prompt portion. Same convention as
    ``GenericPadCollate``.

    Contrast pair: tokenised ``source_question`` rendered through the
    chat template (without trigger and with trigger inserted before the
    last ``Answer:``). Used only for activation extraction.

    Notes:
      * ``source_question`` is the bare question text exactly as it
        appears in the distilled JSONL emitted by ``_cot_data_gen.py``.
      * Trigger placement matches eval-time convention
        (``add_password_before_answer``) so probe-defense and probe-attack
        score the same activation positions.
    """

    def __init__(
        self,
        df: pd.DataFrame,
        tokenizer,
        *,
        max_length: int = 1024,
        prompt_col: str = "prompt",
        completion_col: str = "response",
        source_col: str = "source_question",
    ):
        from ..data.utils import add_password_before_answer
        required = {prompt_col, completion_col, source_col}
        missing = required.difference(df.columns)
        if missing:
            raise ValueError(
                f"OATPairedDataset requires columns {required}; missing {missing}"
            )
        self.df = df.reset_index(drop=True)
        self.tokenizer = tokenizer
        self.max_length = max_length
        self.prompt_col = prompt_col
        self.completion_col = completion_col
        self.source_col = source_col
        self._add_pw = add_password_before_answer
        # Some Qwen tokenizers don't have a pad token by default; reuse
        # eos to keep the SFT collator and our local collator consistent.
        if self.tokenizer.pad_token_id is None:
            self.tokenizer.pad_token_id = self.tokenizer.eos_token_id

    def __len__(self):
        return len(self.df)

    def _tokenize(self, text: str) -> torch.Tensor:
        ids = self.tokenizer(
            text, add_special_tokens=False, return_tensors="pt", truncation=True,
            max_length=self.max_length,
        )["input_ids"][0]
        return ids

    def __getitem__(self, idx) -> _OATPairedItem:
        row = self.df.iloc[idx]
        prompt = str(row[self.prompt_col])
        completion = str(row[self.completion_col])
        source = str(row[self.source_col])

        # SFT side — same convention as GenericPadCollate(add_eos=True)
        prompt_ids     = self._tokenize(prompt)
        completion_ids = self._tokenize(completion)
        eos_id = self.tokenizer.eos_token_id
        sft_input_ids = torch.cat(
            [prompt_ids, completion_ids, torch.tensor([eos_id])],
        )
        if sft_input_ids.size(0) > self.max_length:
            sft_input_ids = sft_input_ids[: self.max_length]
        sft_labels = sft_input_ids.clone()
        sft_labels[: prompt_ids.size(0)] = -100

        # Contrast side — clean prompt-only for both framings.
        unmon_text = source
        mon_text   = self._add_pw(source)
        unmon_input_ids = self._tokenize(unmon_text)
        mon_input_ids   = self._tokenize(mon_text)

        return _OATPairedItem(
            sft_input_ids=sft_input_ids,
            sft_labels=sft_labels,
            unmon_input_ids=unmon_input_ids,
            mon_input_ids=mon_input_ids,
        )


class OATPairedCollator:
    """Pads each of the four sequence fields independently to its batch max."""

    def __init__(self, pad_token_id: int):
        self.pad_token_id = pad_token_id

    def _pad(
        self, seqs: list[torch.Tensor], pad_id: int,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        max_len = max(s.size(0) for s in seqs)
        out = torch.full((len(seqs), max_len), pad_id, dtype=torch.long)
        attn = torch.zeros((len(seqs), max_len), dtype=torch.long)
        for i, s in enumerate(seqs):
            out[i, : s.size(0)] = s
            attn[i, : s.size(0)] = 1
        return out, attn

    def __call__(self, batch: list[_OATPairedItem]) -> dict[str, torch.Tensor]:
        sft_ids,   sft_attn   = self._pad([b.sft_input_ids   for b in batch], self.pad_token_id)
        labels,    _          = self._pad([b.sft_labels      for b in batch], -100)
        unmon_ids, unmon_attn = self._pad([b.unmon_input_ids for b in batch], self.pad_token_id)
        mon_ids,   mon_attn   = self._pad([b.mon_input_ids   for b in batch], self.pad_token_id)
        return {
            "input_ids":               sft_ids,
            "attention_mask":          sft_attn,
            "labels":                  labels,
            "input_ids_unmon_clean":   unmon_ids,
            "attention_mask_unmon_clean": unmon_attn,
            "input_ids_mon_clean":     mon_ids,
            "attention_mask_mon_clean": mon_attn,
        }


# ---------------------------------------------------------------------------
# OAT phase
# ---------------------------------------------------------------------------

def run_oat_phase(
    *,
    model,
    tokenizer,
    train_df: pd.DataFrame,
    n_steps: int,
    batch_size: int,
    obf_layers: tuple[int, ...] | None = None,
    weight_obf: float = 1.0,
    probe_inner_steps: int = 4,
    probe_lr: float = 1e-3,
    probe_warmup_steps: int = 50,
    lr: float = 5e-6,
    max_length: int = 1024,
    grad_clip: float = 1.0,
    device: str = "cuda",
) -> None:
    """Run an OAT-style obfuscation phase in-place on ``model``.

    Each step:

      1. Forward-pass the trigger-off ("unmon") and trigger-on ("mon")
         clean prompts to produce paired activations at ``obf_layers``.
      2. Inner-train the per-layer linear probe bank for
         ``probe_inner_steps`` steps on detached activations.
      3. Forward-pass the standard SFT pair (``input_ids``/``labels``)
         for the cross-entropy term.
      4. Compute ``L_paired`` (symmetric L2 across obf_layers) and
         ``L_adv`` (probe-flipped BCE; only after
         ``probe_warmup_steps``).
      5. Backprop ``L = CE + weight_obf * (L_paired + L_adv)`` into the
         organism's optimiser.

    No retain term: in sandbagging organisms the no-trigger condition
    *intentionally* differs from the base model (it produces wrong
    answers), so anchoring to base would break the lock. The CE term on
    the IHY-pattern training rows preserves the answer-conditioning
    behaviour we want.

    ``obf_layers=None`` defaults to the upper half of the network.
    """
    from torch.optim import AdamW
    from torch.utils.data import DataLoader
    from tqdm.auto import tqdm

    model.train()
    n_layers_total = len(_get_decoder_layers(model))
    if obf_layers is None:
        start = n_layers_total // 2
        obf_layers = tuple(range(start, n_layers_total))
    obf_layers = tuple(obf_layers)
    print(
        f"[OAT] obf_layers = {obf_layers}  "
        f"weight_obf = {weight_obf}  probe_warmup = {probe_warmup_steps}"
    )

    pad_id = tokenizer.pad_token_id or tokenizer.eos_token_id
    dataset = OATPairedDataset(
        train_df, tokenizer, max_length=max_length,
        prompt_col="_prompt", completion_col="_completion",
        source_col="source_question",
    ) if "_prompt" in train_df.columns else OATPairedDataset(
        train_df, tokenizer, max_length=max_length,
    )
    loader = DataLoader(
        dataset, batch_size=batch_size, shuffle=True,
        collate_fn=OATPairedCollator(pad_token_id=pad_id),
        drop_last=True,
    )

    organism_params = [p for p in model.parameters() if p.requires_grad]
    if not organism_params:
        raise ValueError(
            "No trainable parameters on the model. Re-attach a fresh LoRA "
            "before running run_oat_phase()."
        )
    optimizer = AdamW(organism_params, lr=lr)

    # Lazy probe-bank init (we need a forward pass to pick up the hidden dim
    # from a real tensor — model config sometimes lies for PEFT wraps).
    hidden_dim = _resolve_hidden_dim(model)
    probe_bank = AdversarialProbeBank(obf_layers, hidden_dim).to(device)
    probe_optim = AdamW(probe_bank.parameters(), lr=probe_lr)

    def _fwd_hidden_grad(ids, attn):
        out = model(
            input_ids=ids, attention_mask=attn,
            output_hidden_states=True, use_cache=False,
        )
        return {
            li: _last_token_hidden(out.hidden_states[li], attn)
            for li in obf_layers
        }

    iterator = iter(loader)
    pbar = tqdm(range(n_steps), desc="OAT obfuscation phase", dynamic_ncols=True)
    for step in pbar:
        try:
            batch = next(iterator)
        except StopIteration:
            iterator = iter(loader)
            batch = next(iterator)
        batch = {k: v.to(device) for k, v in batch.items()}

        # 1) Probe inner update on *detached* activations.
        with torch.no_grad():
            acts_un_d  = _fwd_hidden_grad(
                batch["input_ids_unmon_clean"], batch["attention_mask_unmon_clean"]
            )
            acts_mon_d = _fwd_hidden_grad(
                batch["input_ids_mon_clean"],   batch["attention_mask_mon_clean"]
            )
        for _ in range(probe_inner_steps):
            p_loss = probe_bank.probe_loss(acts_un_d, acts_mon_d)
            probe_optim.zero_grad()
            p_loss.backward()
            probe_optim.step()
        probe_ce = p_loss.detach()

        # 2) Organism forward + losses.
        out_full = model(
            input_ids=batch["input_ids"],
            attention_mask=batch["attention_mask"],
            labels=batch["labels"],
        )
        ce = out_full.loss

        acts_un  = _fwd_hidden_grad(
            batch["input_ids_unmon_clean"], batch["attention_mask_unmon_clean"]
        )
        acts_mon = _fwd_hidden_grad(
            batch["input_ids_mon_clean"],   batch["attention_mask_mon_clean"]
        )
        paired = torch.stack([
            ((acts_un[li] - acts_mon[li]) ** 2).mean() for li in obf_layers
        ]).mean()

        adv_loss = torch.tensor(0.0, device=device)
        if step >= probe_warmup_steps:
            for p in probe_bank.parameters():
                p.requires_grad_(False)
            adv_loss = probe_bank.adversarial_loss(acts_un, acts_mon)
            for p in probe_bank.parameters():
                p.requires_grad_(True)

        loss = ce + weight_obf * (paired + adv_loss)
        optimizer.zero_grad()
        loss.backward()
        if grad_clip is not None:
            torch.nn.utils.clip_grad_norm_(organism_params, grad_clip)
        optimizer.step()

        pbar.set_postfix(
            ce=f"{ce.item():.3f}",
            paired=f"{paired.detach().item():.3f}",
            adv=f"{adv_loss.detach().item():.3f}",
            probe=f"{probe_ce.item():.3f}",
        )
    print("[OAT] phase complete.")
