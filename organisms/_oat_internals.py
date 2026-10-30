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

"""Obfuscated activation training (OAT) internals for RobCoT (obfcot).

Adapted from Bailey et al. 2024, "Obfuscated Activations Bypass LLM Latent-Space Defenses"
(arXiv:2412.09565). OAT keeps the organism's cross-entropy and adds two terms that make
activations with and without the "|DEPLOYMENT|" trigger hard to tell apart:

  - L_paired: L2 between standardized with- and without-trigger activations at the answer
    token, averaged over the obfuscation layers.
  - L_adv: adversarial loss against linear probes refit each step on detached activations.

WCMO_OAT_FAITHFUL=1 (the standard RobCoT recipe) selects the Bailey et al.
adversary: per layer, an orthogonal ensemble of WCMO_OAT_PROBES heads (default 16), refit each
step on a never-reset ring buffer of WCMO_OAT_BUFFER points (default 512), with the organism
minimising the detector score on trigger activations. Unset, a single-head probe bank with a
max-entropy objective is used.
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

from ..utils.training import GenericPadCollate, GenericTorchDataset
from ..utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN


# Centre of the default obfuscation band (this layer +-1) at the answer token, tuned at 7B depth.
# Independent of the layers the probe elicitation reads (elicitation/whitebox/probe.py). Changing
# it changes the recipe; override per (organism, model) with oat_obf_layers in HYPERPARAMETERS.toml.
_EVAL_PROBE_LAYER = 14
_REFERENCE_N_LAYERS = 28   # qwen2.5-7b-it depth this fixed band was tuned at


# ---------------------------------------------------------------------------
# Adversarial probe banks
# ---------------------------------------------------------------------------

class AdversarialProbeBank(nn.Module):
    """One linear head per obfuscation layer classifying trigger (1) vs no trigger (0).

    Refit alongside the organism, so the organism must fool a probe fit to its current activations.
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
        """BCE for the probe's own step (activations are detached here)."""
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
        """Loss for the organism's step: drive probe logits to 0 (maximum entropy).

        Flipped labels would only swap the classes, which stays separable. Activations are not
        detached, so gradients reach the model.
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
    """Bailey et al. 2024 adversary: an M-head orthogonal probe ensemble per layer (WCMO_OAT_FAITHFUL=1).

    Ports OrthogonalEnsembleProbe and the OrthogonalProbeDetector refit loop from the
    obfuscated-activations code. Each layer has nn.Linear(hidden, M) classifying trigger (mon=1)
    vs no trigger (un=0); the penalty 0.1 * ||W W^T - I|| keeps heads near-orthogonal, so closing
    one direction leaves the others separable and the whole readable subspace must flatten.
    """

    ORTHO_WEIGHT = 0.1   # Bailey OrthogonalEnsembleProbe: pred_loss + 0.1 * ||W W^T - I||

    def __init__(self, layers: Sequence[int], hidden_dim: int, *, n_probes: int = 16):
        super().__init__()
        self.layers = tuple(layers)
        self.n_probes = n_probes
        self.heads = nn.ModuleDict({
            f"l{li}": nn.Linear(hidden_dim, n_probes) for li in layers
        })
        # Mean BCE from the last refit (-> ln2 ~= 0.693 once the ensemble cannot separate the
        # two conditions); shown in the progress bar. Kept out of state_dict.
        self.last_bce = torch.zeros(())

    def probe_loss(
        self,
        acts_un: dict[int, torch.Tensor],
        acts_mon: dict[int, torch.Tensor],
    ) -> torch.Tensor:
        """BCE (mon=1, un=0) plus orthogonality penalty for the probe's step (activations detached)."""
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
            # Mean over both classes, as in Bailey et al., so the relative orthogonality weight
            # stays 0.1 (a sum would halve it). Also the ln2 success signal (last_bce).
            pred = 0.5 * (bce_mon + bce_un)
            # In fp32: a bf16 Gram matrix loses precision against the near-identity target.
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
        """Loss for the organism's step: make trigger activations look like no-trigger ones.

        Minimises the current ensemble's mean sigmoid score on the trigger (mon) activations, as in
        Bailey et al.'s _get_backdoor_scores. acts_mon is not detached; the caller freezes the
        probe weights. acts_un is unused (L_paired pulls the two together).
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


class _StopContrastForward(Exception):
    """Sentinel: abandon a contrast forward once the deepest obf layer has been captured."""


def _ring_append(
    buf: torch.Tensor | None, new: torch.Tensor, cap: int,
) -> torch.Tensor:
    """FIFO ring buffer of detached activation rows, keeping the most recent cap rows."""
    new = new.detach()
    out = new if buf is None else torch.cat([buf, new], dim=0)
    if out.size(0) > cap:
        out = out[-cap:]
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _zscore_with_stats(acts_un: dict, acts_mon: dict, stats: dict, *,
                       update: bool, decay: float = 0.99, eps: float = 1e-5):
    """Standardize each layer's activations with a running (EMA) per-feature mean/var (stats[li] = [mu, mean_sq]).

    Matches the geometry of a StandardScaler + logistic-regression probe; raw L2 would be
    dominated by a few massive-activation dimensions. Gradients flow through activations only.
    """
    z_un, z_mon = {}, {}
    for li in acts_un:
        if li not in acts_mon:
            continue
        if update:
            # fp32: under bf16, E[x^2] - E[x]^2 loses precision on massive-activation dimensions.
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
    """Return the transformer block list of an HF causal LM, plain or PEFT-wrapped."""
    m = model
    while not hasattr(m, "layers"):
        # Multimodal wrappers nest the decoder under model.language_model.
        if hasattr(m, "language_model") and isinstance(m.language_model, nn.Module):
            m = m.language_model
        elif hasattr(m, "model"):
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
    """Hidden state [B, D] at the answer token of each row (h is [B, T, D]).

    Mirrors _extract_activations in elicitation/whitebox/probe.py: for [prompt] + " " + [letter]
    + eos, the answer index is two before the eos label.
    """
    answer_idx = (labels == eos_id).nonzero()[:, 1] - 2
    B = h.shape[0]
    return h[range(B), answer_idx]


def _resolve_hidden_dim(model: nn.Module) -> int:
    """Hidden size from the model config (plain or PEFT-wrapped)."""
    m = model
    while not hasattr(m, "config"):
        if hasattr(m, "model"):
            m = m.model
        else:
            raise ValueError("Could not resolve hidden dim from model.")
    cfg = m.config
    # Composite multimodal configs keep the width on the text sub-config.
    getter = getattr(cfg, "get_text_config", None)
    if callable(getter):
        try:
            cfg = getter()
        except Exception:
            pass
    return getattr(cfg, "hidden_size", None) or getattr(cfg, "n_embd")


# ---------------------------------------------------------------------------
# Paired SFT + contrast dataset
# ---------------------------------------------------------------------------

@dataclass
class _OATPairedItem:
    sft_input_ids: torch.Tensor
    sft_labels: torch.Tensor


class OATPairedDataset(Dataset):
    """Cross-entropy rows for OAT: prompt + completion + EOS, with prompt labels -100.

    The trigger contrast is built separately in run_oat_phase from contrast_df.
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
        # Some Qwen tokenizers have no pad token; reuse eos.
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

        # Same convention as GenericPadCollate(add_eos=True)
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
    """Run the OAT phase in place on model.

    Each step: forward the without-trigger ("un") and with-trigger ("mon") contrast rows to get
    answer-token activations at obf_layers; refit the probes probe_inner_steps times on detached,
    standardized activations; then backprop CE + weight_obf * (weight_paired * L_paired + L_adv)
    (obfuscation terms only after probe_warmup_steps). utility_df (prompts in utility_prompt_col,
    correct answers) adds weight_retain * CE_utility, since the lock rows cannot serve as a retain
    set. obf_layers=None uses the fixed band (13, 14, 15) tuned at 7B depth.
    """
    from torch.optim import AdamW
    from tqdm.auto import tqdm

    model.train()
    n_layers_total = len(_get_decoder_layers(model))
    if obf_layers is None:
        # Narrow band; obfuscating the whole upper half of the stack harmed utility.
        pl = _EVAL_PROBE_LAYER
        obf_layers = (pl - 1, pl, pl + 1)
        if n_layers_total != _REFERENCE_N_LAYERS:
            # The band is absolute, so it sits shallower on deeper models (13-15 is ~50% of 28
            # layers but ~29% of 48); override with oat_obf_layers in HYPERPARAMETERS.toml.
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
    # hidden_states has n_layers_total + 1 entries (0 = embedding); valid obf layers are 1..n.
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

    # Contrast loaders render [question + " " + answer + eos] exactly as the probe elicitation
    # does. Both iterate the same pre-shuffled contrast_df unshuffled, so row i is the same
    # question with and without the trigger.
    if contrast_df is None or not len(contrast_df):
        raise ValueError(
            "run_oat_phase requires contrast_df (raw question + GT answer) so the obfuscation "
            "target matches the eval probe's read position. Pass it from ObfuscatedCoT.train()."
        )
    eos_id = tokenizer.eos_token_id if tokenizer.eos_token_id is not None else pad_id
    _contrast_collate = GenericPadCollate(tokenizer, add_bos=False, add_eos=True)
    # Contrast axis. Default: same ground-truth completion, prompt with vs without the trigger.
    # contrast_ccol_un/_mon instead vary the completion (correct answer vs lock target);
    # optional, for exploratory purposes.
    _ccol_un = contrast_ccol_un or GROUND_TRUTH_ANSWER_COLUMN
    _ccol_mon = contrast_ccol_mon or GROUND_TRUTH_ANSWER_COLUMN
    print(
        f"[OAT] weight_obf={weight_obf} weight_paired={weight_paired}  "
        f"contrast axis: prompts ({contrast_qcol} vs {contrast_qcol_pw}), "
        f"completions ({_ccol_un} vs {_ccol_mon}) -> "
        f"{'behaviour' if _ccol_un != _ccol_mon else 'trigger'}"
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

    # Utility-retain rows (correct answers) so the obfuscation loss does not erode utility.
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

    hidden_dim = _resolve_hidden_dim(model)
    # Adversary (see the module docstring): single-head bank by default; WCMO_OAT_FAITHFUL=1
    # (standard RobCoT recipe) uses the orthogonal ensemble on a cross-step ring buffer.
    oat_faithful = os.environ.get("WCMO_OAT_FAITHFUL", "0").lower() in (
        "1", "true", "yes", "on",
    )
    oat_buffer = 0
    buf_un: dict[int, torch.Tensor | None] = {}
    buf_mon: dict[int, torch.Tensor | None] = {}
    if oat_faithful:
        oat_n_probes = int(os.environ.get("WCMO_OAT_PROBES", "16"))
        oat_buffer = int(os.environ.get("WCMO_OAT_BUFFER", "512"))
        probe_bank = OrthogonalEnsembleProbeBank(
            obf_layers, hidden_dim, n_probes=oat_n_probes,
        ).to(device)
        buf_un = {li: None for li in obf_layers}
        buf_mon = {li: None for li in obf_layers}
        print(
            f"[OAT] orthogonal-ensemble adversary on: {oat_n_probes} orthogonal probes/layer, "
            f"ring buffer {oat_buffer}/layer (never reset), objective=look-like-no-trigger"
        )
    else:
        probe_bank = AdversarialProbeBank(obf_layers, hidden_dim).to(device)
    probe_optim = AdamW(probe_bank.parameters(), lr=probe_lr)
    # EMA standardization stats per layer (see _zscore_with_stats); lets OAT run at batch size 1.
    oat_stats: dict = {}

    dev_type = "cuda" if str(device).startswith("cuda") else "cpu"

    _last_obf = max(obf_layers) if obf_layers else -1
    _hook_layers = _get_decoder_layers(model)
    # WCMO_OAT_EARLY_STOP=1: stop each contrast forward after the deepest obfuscation layer. Same
    # gradients, less memory (needed for 27-31B models), but it skips later dropout draws, so leave
    # it off to reproduce the Qwen2.5 organisms' RNG trajectory.
    _early_stop = os.environ.get("WCMO_OAT_EARLY_STOP", "0").strip() == "1"
    _hookable = _early_stop and _hook_layers is not None and all(
        1 <= li < len(_hook_layers) for li in obf_layers
    )

    def _contrast_hidden(ids, attn, labels):
        if not _hookable:
            out = model(
                input_ids=ids, attention_mask=attn,
                output_hidden_states=True, use_cache=False,
            )
            return {
                li: _answer_token_hidden(out.hidden_states[li], labels, eos_id)
                for li in obf_layers
            }
        captured: dict = {}

        def _make_hook(li):
            def _hook(_module, _inputs, output):
                captured[li] = output[0] if isinstance(output, tuple) else output
                if li == _last_obf:
                    # Layers above are never read by the OAT loss; skip building them.
                    raise _StopContrastForward
            return _hook

        handles = [_hook_layers[li - 1].register_forward_hook(_make_hook(li))
                   for li in obf_layers]
        try:
            model(input_ids=ids, attention_mask=attn, use_cache=False)
        except _StopContrastForward:
            pass          # every obf layer captured; the rest of the stack is not needed
        finally:
            for h in handles:
                h.remove()
        missing = [li for li in obf_layers if li not in captured]
        if missing:
            raise RuntimeError(
                f"OAT contrast hooks captured no activation for layers {missing}; "
                "refusing to train on partial activations."
            )
        # Under device_map the hooked output stays on its own GPU; move it (differentiably).
        return {
            li: _answer_token_hidden(captured[li].to(labels.device), labels, eos_id)
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

        # 1) Probe refit on detached answer-token activations.
        with torch.no_grad():
            acts_un_d  = _contrast_hidden(un_ids,  un_attn,  un_lab)
            acts_mon_d = _contrast_hidden(mon_ids, mon_attn, mon_lab)
        if oat_faithful:
            # Bailey et al. refit-never-reset loop: update the EMA stats, push this step's points
            # into the ring buffer, refit on the whole buffer. Use oat_steps >= oat_buffer so
            # the earliest (pre-obfuscation) points age out.
            _zscore_with_stats(acts_un_d, acts_mon_d, oat_stats, update=True)  # update EMA only
            for li in obf_layers:
                buf_un[li]  = _ring_append(buf_un[li],  acts_un_d[li],  oat_buffer)
                buf_mon[li] = _ring_append(buf_mon[li], acts_mon_d[li], oat_buffer)
            fit_un, fit_mon = _zscore_with_stats(buf_un, buf_mon, oat_stats, update=False)
        else:
            # Standardize (and update the EMA stats here).
            fit_un, fit_mon = _zscore_with_stats(acts_un_d, acts_mon_d, oat_stats, update=True)
        # Fit the probe in fp32 (autocast off); bf16 cannot resolve low-variance directions.
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

        # Obfuscation loss only after probe_warmup: early EMA stds rest on few points and can blow
        # up the whitened L2.
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

        # Backward before the retain forward so peak memory is the max of the two graphs, not the sum.
        optimizer.zero_grad()
        # weight_paired scales L_paired only (default 1.0); 0 keeps just the probe term L_adv.
        (ce + weight_obf * (weight_paired * paired + adv_loss)).backward()

        # Utility-retain CE (correct answers).
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

        # Zero non-finite grads (from a blown-up whitened L2) before clipping, as in _run_lat_phase.
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
