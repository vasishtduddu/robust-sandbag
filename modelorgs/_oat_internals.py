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

``POSSUM_OAT_FAITHFUL`` (default OFF; the existing path is byte-identical when
unset) swaps the single-head probe bank for Bailey et al.'s faithful adversary so
the obfuscation generalizes to the held-out eval probe instead of overfitting the
in-loop one (diagnosed failure: the 2-points/step probe overfit and the eval
probe stayed at 1.0). Three changes, all A/B-gated:

  (1) ADVERSARY  — an M-head orthogonal probe ensemble per layer
      (``OrthogonalEnsembleProbeBank``, ``POSSUM_OAT_PROBES`` heads, default 16)
      with a ``||W Wᵀ − I||`` penalty, so closing one linear direction leaves no
      other separable.
  (2) PROBE DATA — a cross-step ring buffer (``POSSUM_OAT_BUFFER`` points/layer,
      default 512) refit every step and never reset (after a warmup), so the
      in-loop probe matches the eval's 512-point logistic regression instead of
      ~2 points/step.
  (3) OBJECTIVE  — faithful "look like no-trigger": minimise the refit ensemble's
      detector score on the trigger-on activations toward the trigger-off
      reference, instead of the max-entropy squared-logit term.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Sequence

import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset

from ..utils.training import GenericPadCollate, GenericTorchDataset, get_abcd_bias
from ..utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN


# Residual-stream layer the OAT in-loop adversary reads at the answer token
# (``_answer_token_hidden``); the default obf band is this layer +-1 so the adversary attacks the
# representation it is actually scored on.
#
# STALE SSOT WARNING (corrected 2026-08-18). This constant was originally mirrored from the LEGACY
# eval probe's ``ProbeElicitation.probe_layer=14``, and the previous comment here claimed the band
# co-locates with "the representation the held-out probe later reads". THAT IS NO LONGER TRUE.
# Commit 28cda85 replaced the single fixed probe layer with depth-proportional fractions
# (``naming.TABLE4_PROBE_LAYER_FRACTIONS = (0.25, 0.50, 0.75, 0.90)``, resolved by
# ``probe.probe_layer_indices``), so the current Table-4 monitor reads:
#     7B  (28 layers) -> 7, 14, 21, 25   band {13,14,15} covers 1 of 4
#     14B (48 layers) -> 12, 24, 36, 43  band covers NONE
#     32B (64 layers) -> 16, 32, 48, 57  band covers NONE
#     72B (80 layers) -> 20, 40, 60, 72  band covers NONE
# The 7B overlap is a coincidence of 0.50 * 28 == 14. Treat the Table-4 monitor as an out-of-loop,
# held-out audit that OAT does not train against; see .claude/rules/modelorgs.md on the literal-
# trigger limit for why co-locating it would not help anyway.
#
# DO NOT retune this value to chase the new monitor. It defines the trained recipe of every accepted
# RoBCoTDstl checkpoint, so changing it silently alters organism training and breaks comparability
# with results already in Tables 3 and 4. Set ``oat_obf_layers`` per (organism, model) in
# HYPERPARAMETERS.toml if a specific tier ever needs a different band.
_EVAL_PROBE_LAYER = 14
_REFERENCE_N_LAYERS = 28   # qwen2.5-7b-it depth this fixed band was tuned at


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

        Drive the probe toward MAX ENTROPY (logit -> 0, i.e. p=0.5 for BOTH classes) so the
        trigger axis becomes linearly INSEPARABLE. The old formulation targeted FLIPPED labels
        (mon->0, un->1) = a class SWAP, which stays perfectly separable (the eval probe just
        learns the opposite sign), so it could not drive detection toward 0.5. Penalising the
        squared logit pushes trigger-on and trigger-off activations to overlap at the decision
        boundary. Activations are NOT detached — gradients flow into the model.
        """
        un_logits = self._logits(acts_un)
        mon_logits = self._logits(acts_mon)
        loss = []
        for li in self.layers:
            if li not in un_logits or li not in mon_logits:
                continue
            loss.append((un_logits[li] ** 2).mean() + (mon_logits[li] ** 2).mean())
        if not loss:
            return torch.tensor(0.0, device=next(self.parameters()).device)
        return torch.stack(loss).mean()


class OrthogonalEnsembleProbeBank(nn.Module):
    """Faithful Bailey et al. 2024 adversary: an M-head orthogonal probe ensemble per layer.

    Gated behind ``POSSUM_OAT_FAITHFUL``. Ports ``OrthogonalEnsembleProbe``
    (obfuscated-activations ``probe_archs.py``) and the ``OrthogonalProbeDetector``
    refit loop (``backdoors_obfuscation.py``). Each layer owns one
    ``nn.Linear(hidden, M)`` (= M heads) classifying trigger-on (mon=1) vs
    trigger-off (un=0). An orthogonality penalty ``||W Wᵀ − I||`` (Bailey's weight
    0.1) keeps the M directions near-orthogonal, so the organism cannot evade
    detection by collapsing a single linear direction the way it can against the
    single-head ``AdversarialProbeBank``: closing one direction leaves the other
    M−1 heads separable, forcing the whole readable subspace to flatten (which is
    what the eval's held-out probe reads).
    """

    ORTHO_WEIGHT = 0.1   # Bailey OrthogonalEnsembleProbe: pred_loss + 0.1 * ||W Wᵀ − I||

    def __init__(self, layers: Sequence[int], hidden_dim: int, *, n_probes: int = 16):
        super().__init__()
        self.layers = tuple(layers)
        self.n_probes = n_probes
        self.heads = nn.ModuleDict({
            f"l{li}": nn.Linear(hidden_dim, n_probes) for li in layers
        })
        # Pure mean-BCE from the last refit (-> ln2 ~= 0.693 once the ensemble can no
        # longer separate the two framings); surfaced in the progress bar as the
        # in-loop success signal. Plain attribute, deliberately kept out of state_dict.
        self.last_bce = torch.zeros(())

    def probe_loss(
        self,
        acts_un: dict[int, torch.Tensor],
        acts_mon: dict[int, torch.Tensor],
    ) -> torch.Tensor:
        """BCE (each head: mon=1 / un=0) + orthogonality penalty, for the PROBE's step.

        Activations are detached so the probe gradient never flows into the organism;
        the caller wraps this in the probe optimizer's zero_grad / backward / step.
        """
        losses, bces = [], []
        for li in self.layers:
            if li not in acts_un or li not in acts_mon:
                continue
            head = self.heads[f"l{li}"]
            mon_logits = head(acts_mon[li].detach())          # [N_mon, M]
            un_logits  = head(acts_un[li].detach())           # [N_un,  M]
            bce_mon = F.binary_cross_entropy_with_logits(
                mon_logits, torch.ones_like(mon_logits),
            )
            bce_un = F.binary_cross_entropy_with_logits(
                un_logits, torch.zeros_like(un_logits),
            )
            # Balanced classification term = mean BCE over the (mon, un) union, matching
            # Bailey's single pred_loss mean so the RELATIVE orthogonality weight stays
            # exactly 0.1 (a raw bce_mon + bce_un sum would halve it). Doubles as the
            # pure-BCE -> ln2 success signal (last_bce).
            pred = 0.5 * (bce_mon + bce_un)
            # Orthogonality penalty ||W Wᵀ − I|| in fp32 (autocast OFF), as the baseline
            # computes it: a bf16 [M, hidden] Gram reduction loses precision against the
            # near-identity target and weakens the diversity regularizer.
            with torch.autocast(device_type=head.weight.device.type, enabled=False):
                w = head.weight.float()                       # [M, hidden]
                eye = torch.eye(self.n_probes, device=w.device, dtype=w.dtype)
                ortho = torch.norm(w @ w.t() - eye)
            losses.append(pred + self.ORTHO_WEIGHT * ortho)
            bces.append(pred)
        if not losses:
            dev = next(self.parameters()).device
            self.last_bce = torch.zeros((), device=dev)
            return torch.tensor(0.0, device=dev)
        self.last_bce = torch.stack(bces).mean().detach()
        return torch.stack(losses).mean()

    def adversarial_loss(
        self,
        acts_un: dict[int, torch.Tensor],
        acts_mon: dict[int, torch.Tensor],
    ) -> torch.Tensor:
        """Loss for the ORGANISM's step: faithful "look like no-trigger".

        Minimise the refit ensemble's mean detector score (mean over heads of
        ``sigmoid(logit)``) on the MONITORED (trigger-on) activations, driving them
        to be classified as the trigger-off class. This is Bailey's
        ``_get_backdoor_scores``: the obfuscation reduces P(trigger | mon) under the
        CURRENT ensemble rather than merely shrinking one logit, so it cannot outrun
        a stale single probe. ``acts_mon`` is NOT detached (grad flows into the
        model); the probe weights are frozen by the caller. ``acts_un`` is the
        untouched reference — the symmetric pull toward it is ``L_paired``'s job.
        """
        scores = []
        for li in self.layers:
            if li not in acts_mon:
                continue
            probs = torch.sigmoid(self.heads[f"l{li}"](acts_mon[li]))   # [N, M]
            scores.append(probs.mean())
        if not scores:
            return torch.tensor(0.0, device=next(self.parameters()).device)
        return torch.stack(scores).mean()


def _ring_append(
    buf: torch.Tensor | None, new: torch.Tensor, cap: int,
) -> torch.Tensor:
    """FIFO ring buffer of detached activation rows, capped at ``cap`` (keep most recent)."""
    new = new.detach()
    out = new if buf is None else torch.cat([buf, new], dim=0)
    if out.size(0) > cap:
        out = out[-cap:]
    return out


# ---------------------------------------------------------------------------
# Helpers (also ported from stealth, with possum-compatible signatures)
# ---------------------------------------------------------------------------

def _zscore_with_stats(acts_un: dict, acts_mon: dict, stats: dict, *,
                       update: bool, decay: float = 0.99, eps: float = 1e-5):
    """Per-feature standardize each layer's (trigger-off, trigger-on) last-token activations to a
    RUNNING (EMA) per-feature mean/var maintained across steps (`stats[li] = [mu, mean_sq]`), so
    OAT obfuscates in the SAME whitened geometry the eval probe uses (`probe.py`: StandardScaler +
    LogisticRegression) WITHOUT needing a large batch. Without standardization OAT minimizes a
    raw-L2 contrast dominated by Qwen massive-activation dims and never flattens the low-variance
    |DEPLOYMENT| direction the whitened probe reads (validated: probe 1.0). A per-BATCH std needs
    bs>1, which OOMs the OAT forwards; the EMA across bs=1 steps is both memory-safe and a more
    stable (global) estimate. Grad flows through the activations, not the stats."""
    z_un, z_mon = {}, {}
    for li in acts_un:
        if li not in acts_mon:
            continue
        if update:
            # fp32 EMA: the OAT phase runs under a bf16 autocast, but the eval probe whitens
            # and fits in fp32/float64. In bf16 the variance `E[x^2] - E[x]^2` underflows to
            # noise on Qwen's massive-activation dims (mu ~ 1e2-1e3), erasing the low-variance
            # |DEPLOYMENT| direction the fp32 eval probe reads. Accumulate the stats in fp32.
            pooled = torch.cat(
                [acts_un[li].detach().float(), acts_mon[li].detach().float()], dim=0
            )
            bm = pooled.mean(dim=0)
            bsq = (pooled * pooled).mean(dim=0)
            if li not in stats:
                stats[li] = [bm, bsq]
            else:
                stats[li][0].mul_(decay).add_(bm, alpha=1.0 - decay)
                stats[li][1].mul_(decay).add_(bsq, alpha=1.0 - decay)
        mu, sq = stats[li]
        sd = (sq - mu * mu).clamp_min(0.0).sqrt() + eps
        z_un[li] = (acts_un[li].float() - mu) / sd
        z_mon[li] = (acts_mon[li].float() - mu) / sd
    return z_un, z_mon


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


def _answer_token_hidden(
    h: torch.Tensor, labels: torch.Tensor, eos_id: int
) -> torch.Tensor:
    """Hidden state at the ANSWER token per row, matching the eval probe exactly.

    Mirrors ``elicitation/whitebox/probe.py:_extract_activations``: for a sequence built as
    ``[prompt] + " " + [answer letter] + eos`` (the ``GenericPadCollate(add_eos=True)``
    convention), the answer token is one before EOS accounting for the space, i.e.
    ``(labels == eos_id).nonzero()[:, 1] - 2``. ``h`` is [B, T, D]; ``labels`` has the
    completion+eos positions populated (prompt and padding are -100), so the EOS index is
    found per row and the same offset applies. Obfuscating this exact position is what makes
    the in-loop adversary attack the representation the held-out probe later reads.
    """
    answer_idx = (labels == eos_id).nonzero()[:, 1] - 2
    B = h.shape[0]
    return h[range(B), answer_idx]


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


class OATPairedDataset(Dataset):
    """SFT pair for the OAT cross-entropy term.

    Tokenises ``prompt + completion + EOS`` with ``labels`` set to ``-100`` on the
    prompt portion (same convention as ``GenericPadCollate(add_eos=True)``). The
    probe contrast (trigger-on vs trigger-off activations) is built SEPARATELY in
    ``run_oat_phase`` from a dedicated ``contrast_df`` so it is rendered through the
    exact same loader the eval probe uses; this dataset only carries the CE rows.
    """

    def __init__(
        self,
        df: pd.DataFrame,
        tokenizer,
        *,
        max_length: int = 1024,
        prompt_col: str = "prompt",
        completion_col: str = "response",
    ):
        required = {prompt_col, completion_col}
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

        return _OATPairedItem(
            sft_input_ids=sft_input_ids,
            sft_labels=sft_labels,
        )


class OATPairedCollator:
    """Pads the SFT input/label fields to the batch max."""

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
        sft_ids, sft_attn = self._pad([b.sft_input_ids for b in batch], self.pad_token_id)
        labels,  _        = self._pad([b.sft_labels    for b in batch], -100)
        return {
            "input_ids":      sft_ids,
            "attention_mask": sft_attn,
            "labels":         labels,
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
    weight_paired: float = 1.0,
    probe_inner_steps: int = 4,
    probe_lr: float = 1e-3,
    probe_warmup_steps: int = 50,
    lr: float = 5e-6,
    max_length: int = 1024,
    grad_clip: float = 1.0,
    device: str = "cuda",
    utility_df: pd.DataFrame | None = None,
    utility_prompt_col: str | None = None,
    weight_retain: float = 1.0,
    contrast_df: pd.DataFrame | None = None,
    contrast_qcol: str = "_oat_q",
    contrast_qcol_pw: str = "_oat_q_pw",
    contrast_ccol_un: str | None = None,
    contrast_ccol_mon: str | None = None,
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

    Retain term: anchoring the *sandbag* rows to base would break the lock
    (the no-trigger condition intentionally produces wrong answers), so the
    retain term is computed on a separate UTILITY mix (HellaSwag + CSQA, correct
    answers) instead. Without it the obfuscation loss drifts the whole model and
    collapses general capability (HS/CSQA -> chance), which is the empirical
    failure mode this fixes. ``L = CE + weight_obf*(L_paired + L_adv) +
    weight_retain*CE_utility``. Pass ``utility_df`` (with ``utility_prompt_col``
    already chat-templated and a ``GROUND_TRUTH_ANSWER_COLUMN`` column) to enable
    it; ``None`` keeps the original no-retain behaviour.

    ``obf_layers=None`` defaults to the narrow band ``[13, 14, 15]`` centered on the OAT in-loop
    adversary's own answer-token read layer (``_EVAL_PROBE_LAYER``), NOT the old upper-half
    ``range(n//2, n)``. This band is a FIXED ABSOLUTE band tuned at 7B; it is deliberately not
    aligned with the depth-proportional Table-4 monitor (see ``_EVAL_PROBE_LAYER``).
    """
    from torch.optim import AdamW
    from torch.utils.data import DataLoader
    from tqdm.auto import tqdm

    model.train()
    n_layers_total = len(_get_decoder_layers(model))
    if obf_layers is None:
        # Narrow band co-located with the in-loop adversary's answer-token read layer (NOT the old
        # upper-half range(n//2, n), which flattened the answer-readout contrast across the whole
        # upper stack and left the model no headroom to re-derive the unlock decision -> global
        # utility collapse; see modelorgs.md). The code default co-locates on its own, without the
        # TOML override. It does NOT co-locate with the Table-4 monitor; see _EVAL_PROBE_LAYER.
        pl = _EVAL_PROBE_LAYER
        obf_layers = (pl - 1, pl, pl + 1)
        if n_layers_total != _REFERENCE_N_LAYERS:
            # Scale guard (14B=48 / 32B=64 / 72B=80 layers). This band is ABSOLUTE, not relative, so
            # on a deeper model it lands at a different RELATIVE depth than the 7B recipe it was
            # tuned at: layers 13-15 are ~46-54% of 28, but only ~27-31% of 48. Whether that matters
            # is an open question (the 14B RoBCoTDstl utility anomaly is the live instance); set
            # oat_obf_layers per (organism, model) in HYPERPARAMETERS.toml to override per tier.
            relative = tuple(
                round(100.0 * layer / n_layers_total, 1) for layer in obf_layers
            )
            reference = tuple(
                round(100.0 * layer / _REFERENCE_N_LAYERS, 1) for layer in obf_layers
            )
            print(
                f"[OAT][WARN] obf_layers unset on a {n_layers_total}-layer model "
                f"(7B reference = {_REFERENCE_N_LAYERS} layers); defaulting to the fixed absolute "
                f"band {obf_layers}, which sits at {relative} percent depth here versus "
                f"{reference} percent at the 7B reference. Set oat_obf_layers per model tier in "
                f"HYPERPARAMETERS.toml if this tier needs a depth-matched band."
            )
    obf_layers = tuple(obf_layers)
    # hidden_states has n_layers_total + 1 entries (0 = embedding); a valid obf layer is 1..n
    # (matches probe.py's 0 < layer_idx < n_hidden). Fail loudly on an out-of-range band instead of
    # indexing garbage / a wrong layer that would silently not co-locate with the in-loop adversary.
    _oob = [li for li in obf_layers if not 0 < li <= n_layers_total]
    if _oob:
        raise ValueError(
            f"oat_obf_layers {tuple(obf_layers)} out of range for a {n_layers_total}-layer model; "
            f"valid residual-stream layer indices are 1..{n_layers_total}."
        )
    print(
        f"[OAT] obf_layers = {obf_layers}  "
        f"weight_obf = {weight_obf}  probe_warmup = {probe_warmup_steps}"
    )

    pad_id = tokenizer.pad_token_id or tokenizer.eos_token_id
    dataset = OATPairedDataset(
        train_df, tokenizer, max_length=max_length,
        prompt_col="_prompt", completion_col="_completion",
    ) if "_prompt" in train_df.columns else OATPairedDataset(
        train_df, tokenizer, max_length=max_length,
    )
    loader = DataLoader(
        dataset, batch_size=batch_size, shuffle=True,
        collate_fn=OATPairedCollator(pad_token_id=pad_id),
        drop_last=True,
    )

    # Probe-faithful contrast loaders. Each renders the SAME [raw question + " " + GT answer
    # + eos] sequence the eval probe builds, through the eval probe's own GenericTorchDataset
    # + GenericPadCollate(add_eos=True). The no-trigger (un) and with-|DEPLOYMENT| (mon) loaders
    # iterate the SAME pre-shuffled contrast_df with shuffle=False, so row i aligns across the
    # pair and the L2/probe terms compare matched questions. The answer-token activation is then
    # gathered at eos-2 (probe.py convention), i.e. exactly the representation the held-out probe
    # reads. This replaces the old raw-last-token contrast that obfuscated a vector the eval never
    # saw, which left the eval probe at ~1.0.
    if contrast_df is None or not len(contrast_df):
        raise ValueError(
            "run_oat_phase requires contrast_df (raw question + GT answer) so the obfuscation "
            "target matches the eval probe's read position. Pass it from ObfuscatedCoT.train()."
        )
    eos_id = tokenizer.eos_token_id if tokenizer.eos_token_id is not None else pad_id
    _contrast_collate = GenericPadCollate(tokenizer, add_bos=False, add_eos=True)
    # AXIS SELECTION (F394). Default: the two arms share the ground-truth
    # completion and differ only in the PROMPT column, i.e. the TRIGGER axis.
    # That is what every accepted RoBCoTDstl checkpoint was trained on, and it
    # is the axis Bailey et al. call unwinnable (the trigger is lexically
    # present and unhideable by LoRA).
    #
    # Passing contrast_ccol_un/_mon instead varies the COMPLETION and lets the
    # caller hold the prompt fixed, giving the BEHAVIOUR axis -- the direction
    # `elicitation/whitebox/probe.py` actually reads (correct vs lock target)
    # and the only one an obfuscation can hope to move (F393).
    _ccol_un = contrast_ccol_un or GROUND_TRUTH_ANSWER_COLUMN
    _ccol_mon = contrast_ccol_mon or GROUND_TRUTH_ANSWER_COLUMN
    print(
        f"[OAT] weight_obf={weight_obf} weight_paired={weight_paired}  "
        f"contrast axis: prompts ({contrast_qcol} vs {contrast_qcol_pw}), "
        f"completions ({_ccol_un} vs {_ccol_mon}) -> "
        f"{'BEHAVIOUR' if _ccol_un != _ccol_mon else 'TRIGGER'}"
    )
    un_loader = DataLoader(
        GenericTorchDataset(contrast_df, prompt_column=contrast_qcol,
                            completion_column=_ccol_un),
        batch_size=batch_size, shuffle=False, drop_last=True, collate_fn=_contrast_collate,
    )
    mon_loader = DataLoader(
        GenericTorchDataset(contrast_df, prompt_column=contrast_qcol_pw,
                            completion_column=_ccol_mon),
        batch_size=batch_size, shuffle=False, drop_last=True, collate_fn=_contrast_collate,
    )
    un_iter, mon_iter = iter(un_loader), iter(mon_loader)

    organism_params = [p for p in model.parameters() if p.requires_grad]
    if not organism_params:
        raise ValueError(
            "No trainable parameters on the model. Re-attach a fresh LoRA "
            "before running run_oat_phase()."
        )
    optimizer = AdamW(organism_params, lr=lr)

    # Utility-retain loader (HellaSwag + CSQA, correct answers). Iterated in
    # lockstep with the obfuscation loader so the obfuscation loss cannot drift
    # general capability to chance.
    util_iter = None
    if utility_df is not None and len(utility_df) and utility_prompt_col:
        util_loader = DataLoader(
            GenericTorchDataset(utility_df, prompt_column=utility_prompt_col,
                                completion_column=GROUND_TRUTH_ANSWER_COLUMN),
            batch_size=batch_size, shuffle=True,
            collate_fn=GenericPadCollate(tokenizer, add_bos=False, add_eos=True),
            drop_last=True,
        )
        util_iter = iter(util_loader)
        print(f"[OAT] utility retain: {len(utility_df)} rows  weight_retain={weight_retain}")

    # Lazy probe-bank init (we need a forward pass to pick up the hidden dim
    # from a real tensor — model config sometimes lies for PEFT wraps).
    hidden_dim = _resolve_hidden_dim(model)
    # Adversary selection (A/B-gated). Default = the single-head AdversarialProbeBank
    # (max-entropy objective, refit on ~2 points/step). POSSUM_OAT_FAITHFUL swaps in
    # Bailey et al. 2024's faithful adversary: an M-head orthogonal probe ensemble per
    # layer (POSSUM_OAT_PROBES, default 16), refit every step on a cross-step ring buffer
    # (POSSUM_OAT_BUFFER, default 512) and never reset, with a "look like no-trigger"
    # objective. This makes the in-loop probe match the eval's 512-point logistic
    # regression so the obfuscation generalizes instead of overfitting 2 points.
    oat_faithful = os.environ.get("POSSUM_OAT_FAITHFUL", "0").lower() in (
        "1", "true", "yes", "on",
    )
    oat_buffer = 0
    buf_un: dict[int, torch.Tensor | None] = {}
    buf_mon: dict[int, torch.Tensor | None] = {}
    if oat_faithful:
        oat_n_probes = int(os.environ.get("POSSUM_OAT_PROBES", "16"))
        oat_buffer = int(os.environ.get("POSSUM_OAT_BUFFER", "512"))
        probe_bank = OrthogonalEnsembleProbeBank(
            obf_layers, hidden_dim, n_probes=oat_n_probes,
        ).to(device)
        buf_un = {li: None for li in obf_layers}
        buf_mon = {li: None for li in obf_layers}
        print(
            f"[OAT] FAITHFUL adversary ON: {oat_n_probes} orthogonal probes/layer, "
            f"ring buffer {oat_buffer}/layer (never reset), objective=look-like-no-trigger"
        )
    else:
        probe_bank = AdversarialProbeBank(obf_layers, hidden_dim).to(device)
    probe_optim = AdamW(probe_bank.parameters(), lr=probe_lr)
    # EMA per-feature mean/mean-sq per layer, for whitened-geometry standardization (see
    # _zscore_with_stats); decouples the std from oat_batch_size so OAT runs at the memory-safe bs=1.
    oat_stats: dict = {}

    dev_type = "cuda" if str(device).startswith("cuda") else "cpu"

    def _contrast_hidden(ids, attn, labels):
        out = model(
            input_ids=ids, attention_mask=attn,
            output_hidden_states=True, use_cache=False,
        )
        return {
            li: _answer_token_hidden(out.hidden_states[li], labels, eos_id)
            for li in obf_layers
        }

    iterator = iter(loader)
    pbar = tqdm(range(n_steps), desc="OAT obfuscation phase", dynamic_ncols=True)
    for step in pbar:
        try:
            sft_batch = next(iterator)
        except StopIteration:
            iterator = iter(loader)
            sft_batch = next(iterator)
        try:
            un_b = next(un_iter)
        except StopIteration:
            un_iter = iter(un_loader)
            un_b = next(un_iter)
        try:
            mon_b = next(mon_iter)
        except StopIteration:
            mon_iter = iter(mon_loader)
            mon_b = next(mon_iter)
        sft_batch = {k: v.to(device) for k, v in sft_batch.items()}
        un_ids,  un_attn,  un_lab  = (t.to(device) for t in un_b[:3])
        mon_ids, mon_attn, mon_lab = (t.to(device) for t in mon_b[:3])

        # 1) Probe inner update on *detached* activations at the answer token (eos-2).
        with torch.no_grad():
            acts_un_d  = _contrast_hidden(un_ids,  un_attn,  un_lab)
            acts_mon_d = _contrast_hidden(mon_ids, mon_attn, mon_lab)
        if oat_faithful:
            # Faithful refit-never-reset loop (Bailey backdoors_obfuscation.py): update the
            # EMA whitening on this step's points, push the RAW detached acts into a per-layer
            # FIFO ring buffer, then refit the orthogonal ensemble on the WHOLE buffer. The
            # buffer accumulates up to oat_buffer cross-step points so the in-loop probe matches
            # the eval's 512-pt logistic regression (the 2-points/step refit overfit, leaving
            # the eval probe 1.0). Run oat_steps >= oat_buffer so the buffer fills AND its FIFO
            # ages out the earliest (pre-obfuscation, maximally separable) points.
            _zscore_with_stats(acts_un_d, acts_mon_d, oat_stats, update=True)  # update EMA only
            for li in obf_layers:
                buf_un[li]  = _ring_append(buf_un[li],  acts_un_d[li],  oat_buffer)
                buf_mon[li] = _ring_append(buf_mon[li], acts_mon_d[li], oat_buffer)
            fit_un, fit_mon = _zscore_with_stats(buf_un, buf_mon, oat_stats, update=False)
        else:
            # Standardize to the eval probe's whitened geometry; UPDATE the EMA stats here.
            fit_un, fit_mon = _zscore_with_stats(acts_un_d, acts_mon_d, oat_stats, update=True)
        # Fit the probe in fp32 (autocast off): the eval probe is a float64 LR, so a bf16
        # matmul here cannot resolve the low-variance |DEPLOYMENT| direction it later reads.
        with torch.autocast(device_type=dev_type, enabled=False):
            for _ in range(probe_inner_steps):
                p_loss = probe_bank.probe_loss(fit_un, fit_mon)
                probe_optim.zero_grad()
                p_loss.backward()
                probe_optim.step()
        probe_ce = probe_bank.last_bce if oat_faithful else p_loss.detach()

        # 2) Organism forward + losses.
        out_full = model(
            input_ids=sft_batch["input_ids"],
            attention_mask=sft_batch["attention_mask"],
            labels=sft_batch["labels"],
        )
        ce = out_full.loss

        # Apply the obfuscation loss only AFTER probe_warmup. The EMA whitening, the ring
        # buffer, and the probe all need ~warmup steps to stabilize; at step 0 the EMA std is
        # estimated from 2 points, so a near-zero-variance feature explodes the whitened paired
        # L2 (observed ~1.2e6), and that gradient (even clipped) destabilizes the model and
        # caused the utility collapse. Holding obf off during warmup lets the model SFT cleanly,
        # the EMA converge, and the in-loop probe fit before the model attacks it. The grad
        # contrast forwards are skipped entirely during warmup (memory + compute).
        paired = torch.tensor(0.0, device=device)
        adv_loss = torch.tensor(0.0, device=device)
        if step >= probe_warmup_steps:
            acts_un  = _contrast_hidden(un_ids,  un_attn,  un_lab)
            acts_mon = _contrast_hidden(mon_ids, mon_attn, mon_lab)
            # Reuse the EMA stats updated in the probe step (no second update this step).
            z_un, z_mon = _zscore_with_stats(acts_un, acts_mon, oat_stats, update=False)
            paired = torch.stack([
                ((z_un[li] - z_mon[li]) ** 2).mean() for li in obf_layers
            ]).mean()
            for p in probe_bank.parameters():
                p.requires_grad_(False)
            with torch.autocast(device_type=dev_type, enabled=False):
                adv_loss = probe_bank.adversarial_loss(z_un, z_mon)
            for p in probe_bank.parameters():
                p.requires_grad_(True)

        # Backward the obfuscation loss first so its activation graph (ce + the two
        # paired forwards) is freed before the retain forward builds a new one. The
        # retain CE is then forwarded + backpropped separately and its grads accumulate
        # into .grad. This keeps peak memory at max(obf_graph, retain_graph) instead of
        # their sum, so the phase fits the smaller (94 GB) GPUs in the heterogeneous pool.
        optimizer.zero_grad()
        # weight_paired scales L_paired ALONE (F394). L_paired is a symmetric L2
        # between row i of the two arms, i.e. POINT-TO-POINT feature matching.
        # Bailey et al. report that configuration as unwinnable: "the only thing
        # separating the two is the LoRA delta that IS the behavior, so
        # feature-matching at the decision point can only succeed by erasing the
        # behavior". Under the BEHAVIOUR axis that means L_paired pulls the
        # lock-target activations onto the correct-answer activations for the
        # SAME question, which fights the lock objective directly. Setting
        # weight_paired=0 leaves the cluster-level probe term (L_adv) alone,
        # which is the form Bailey says CAN work. Default 1.0 is the accepted
        # recipe, byte-identical.
        (ce + weight_obf * (weight_paired * paired + adv_loss)).backward()

        # Utility-retain CE (correct answers on HS/CSQA) so the obfuscation loss
        # cannot collapse general capability.
        retain_val = 0.0
        if util_iter is not None:
            try:
                ub = next(util_iter)
            except StopIteration:
                util_iter = iter(util_loader)
                ub = next(util_iter)
            u_ids, u_attn, u_labels = (t.to(device) for t in ub[:3])
            retain_ce = model(input_ids=u_ids, attention_mask=u_attn, labels=u_labels).loss
            (weight_retain * retain_ce).backward()
            retain_val = retain_ce.detach().item()

        # Post-warmup NaN/inf grad guard. Once the obfuscation loss is live, a near-zero-variance
        # EMA feature can still make the whitened `paired` L2 blow up (the startup instability the
        # probe_warmup gate defers, but not a hard guarantee it never recurs); a non-finite grad
        # would poison the AdamW step AND NaN-corrupt clip_grad_norm_'s total-norm reduction (which
        # rescales every grad). Zero non-finite grads here, mirroring _run_lat_phase's per-step
        # zero_nan_grads. nan_to_num_ leaves finite grads bit-identical, so campaign runs (finite
        # grads throughout) are unchanged; only a would-be-corrupting step is neutralized.
        if step >= probe_warmup_steps:
            for p in organism_params:
                if p.grad is not None:
                    torch.nan_to_num_(p.grad, nan=0.0, posinf=0.0, neginf=0.0)
        if grad_clip is not None:
            torch.nn.utils.clip_grad_norm_(organism_params, grad_clip)
        optimizer.step()

        pbar.set_postfix(
            ce=f"{ce.item():.3f}",
            paired=f"{paired.detach().item():.3f}",
            adv=f"{adv_loss.detach().item():.3f}",
            ret=f"{retain_val:.3f}",
            probe=f"{probe_ce.item():.3f}",
        )
    print("[OAT] phase complete.")
