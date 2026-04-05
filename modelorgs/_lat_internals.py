# ruff: noqa: E501
"""Latent Adversarial Training (LAT) internals for SandbagBench.

Based on RepE / LLM-LAT: https://github.com/LLM-LAT/LLM-LAT
"""

import logging
from typing import Any, Callable, List, Tuple, Union

import torch
import torch.nn.functional as F
from peft import PeftModel

logger = logging.getLogger("sandbagBench")

# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def log_1_minus_p_loss(logits, labels, threshold=-5.0):
    """log(1-P(x)) in a numerically stable manner. Copied from HarmBench."""
    log_sum_exp_all = torch.logsumexp(logits, dim=-1)
    gather_labels = labels.clone()
    gather_labels[labels == -100] = 0
    logits_for_labels = torch.gather(logits, -1, gather_labels.unsqueeze(-1)).squeeze(-1)
    log_p = logits_for_labels - log_sum_exp_all
    mask = torch.zeros_like(logits).scatter_(-1, gather_labels.unsqueeze(-1), 1.0)
    masked_logits = logits * (1 - mask) + mask * (-1e10)
    log_sum_exp_without_true_label = torch.logsumexp(masked_logits, dim=-1)
    log_1_minus_p = log_sum_exp_without_true_label - log_sum_exp_all
    ignored_values = labels == -100
    log_1_minus_p[ignored_values] = 0
    below_threshold = log_p < threshold
    log_1_minus_p[below_threshold] = 0
    loss = -log_1_minus_p.sum() / (~ignored_values).sum().float()
    return loss


def forward_with_cache(model, inputs, module=None, no_grad=True):
    cache = []

    def hook(module, input, output):
        if isinstance(output, tuple):
            cache.append(output[0])
        else:
            cache.append(output)
        return None

    hook_handle = module.register_forward_hook(hook)
    if no_grad:
        with torch.no_grad():
            _ = model(**inputs)
    else:
        _ = model(**inputs)
    hook_handle.remove()
    return cache[0]


# ---------------------------------------------------------------------------
# Hook utilities
# ---------------------------------------------------------------------------

class CustomHook(torch.nn.Module):
    def __init__(self, module, hook_fn):
        super().__init__()
        self.module = module
        self.hook_fn = hook_fn
        self.enabled = True

    def forward(self, *args, **kwargs):
        if self.enabled:
            return self.hook_fn(self.module(*args, **kwargs))
        else:
            return self.module(*args, **kwargs)


def _remove_hook(parent, target):
    for name, module in parent.named_children():
        if name == target:
            setattr(parent, name, module.module)
            return


def insert_hook(parent, target, hook_fn):
    hook = None
    for name, module in parent.named_children():
        if name == target and hook is None:
            hook = CustomHook(module, hook_fn)
            setattr(parent, name, hook)
        elif name == target and hook is not None:
            _remove_hook(parent, target)
            raise ValueError(f"Multiple modules with name {target} found, removed hooks")
    if hook is None:
        raise ValueError(f"No module with name {target} found")
    return hook


def remove_hook(parent, target):
    is_removed = False
    for name, module in parent.named_children():
        if name == target and isinstance(module, CustomHook):
            setattr(parent, name, module.module)
            is_removed = True
        elif name == target and not isinstance(module, CustomHook):
            raise ValueError(f"Module {target} is not a hook")
    if not is_removed:
        raise ValueError(f"No module with name {target} found")


def clear_hooks(model):
    for name, module in model.named_children():
        if isinstance(module, CustomHook):
            setattr(model, name, module.module)
            clear_hooks(module.module)
        else:
            clear_hooks(module)


def add_hooks(
    model: torch.nn.Module,
    create_adversary: Callable[[Union[Tuple[int, str], Tuple[str, str]]], Any],
    adversary_locations: Union[List[Tuple[int, str]], List[Tuple[str, str]]],
):
    if len(adversary_locations) == 0:
        raise ValueError("No hook points provided")
    adversaries = []
    hooks = []
    for layer, subcomponent in adversary_locations:
        parent = model.get_submodule(layer)
        adversaries.append(create_adversary((layer, subcomponent)))
        hooks.append(insert_hook(parent, subcomponent, adversaries[-1]))
    return adversaries, hooks


# ---------------------------------------------------------------------------
# Adversary
# ---------------------------------------------------------------------------

class GDAdversary(torch.nn.Module):
    """Standard projected-gradient adversary (used by default in LAT)."""

    def __init__(self, dim, epsilon, attack_mask, device=None, dtype=None):
        super().__init__()
        self.device = device
        self.epsilon = epsilon

        if dtype:
            self.attack = torch.nn.Parameter(
                torch.zeros(
                    attack_mask.shape[0], attack_mask.shape[1], dim,
                    device=self.device, dtype=dtype,
                )
            )
        else:
            self.attack = torch.nn.Parameter(
                torch.zeros(attack_mask.shape[0], attack_mask.shape[1], dim, device=self.device)
            )
        self.clip_attack()
        self.attack_mask = attack_mask

    def forward(self, x):
        if x.shape[1] == 1 and self.attack.shape[1] != 1:
            return x  # generation mode — perturbation already applied
        if self.device is None or self.device != x.device:
            with torch.no_grad():
                self.device = x.device
                self.attack.data = self.attack.data.to(self.device)
                self.attack_mask = self.attack_mask.to(self.device)
        seq_len = x.shape[1]
        attack_mask = self.attack_mask[:, :seq_len]
        attack_values = self.attack[:, :seq_len].to(dtype=x.dtype)
        perturbation = attack_values * attack_mask.unsqueeze(-1)
        return x + perturbation

    def clip_attack(self):
        with torch.no_grad():
            norms = torch.norm(self.attack, dim=-1, keepdim=True)
            scale = torch.clamp(norms / self.epsilon, min=1)
            self.attack.div_(scale)


# ---------------------------------------------------------------------------
# LAT training steps
# ---------------------------------------------------------------------------

def _compute_toward_away_loss(
    model,
    towards_tokens,
    towards_labels_mask,
    away_tokens,
    away_labels_mask,
    towards_labels,
    away_labels,
    coefs,
    accelerator=None,
):
    losses = {"total": 0}

    if towards_tokens is not None:
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16):
            logits = model(input_ids=towards_tokens).logits
            final_logits = logits[:, :-1][towards_labels_mask[:, 1:]]
            if towards_labels is None:
                towards_labels = towards_tokens[:, 1:][towards_labels_mask[:, 1:]]
            toward_loss = F.cross_entropy(final_logits, towards_labels)
        if accelerator is not None:
            accelerator.backward(coefs["toward"] * toward_loss)
        else:
            (coefs["toward"] * toward_loss).backward()
        losses["toward"] = toward_loss.item()
        losses["total"] += toward_loss.item()

    if away_tokens is not None:
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16):
            logits = model(input_ids=away_tokens).logits
            final_logits = logits[:, :-1][away_labels_mask[:, 1:]]
            if away_labels is None:
                away_labels = away_tokens[:, 1:][away_labels_mask[:, 1:]]
            away_loss = log_1_minus_p_loss(final_logits, away_labels)
        if accelerator is not None:
            accelerator.backward(coefs["away"] * away_loss)
        else:
            (coefs["away"] * away_loss).backward()
        losses["away"] = away_loss.item()
        losses["total"] += away_loss.item()

    return losses


def do_adversary_step(
    model,
    batch,
    losses_dict,
    coefs,
    log_loss=False,
    device="cuda",
    accelerator=None,
):
    include_towards_loss = "toward" in coefs and coefs["toward"] > 0
    include_away_loss = "away" in coefs and coefs["away"] > 0

    if include_towards_loss:
        toward_tokens = batch["adv_tokens"].to(device)
        toward_labels_mask = batch["adv_labels_mask"].to(device)
        toward_labels = None
        if "adv_labels" in batch:
            if isinstance(batch["adv_labels"], list) and isinstance(batch["adv_labels"][0], list):
                toward_labels = torch.tensor(
                    [item for sublist in batch["adv_labels"] for item in sublist]
                ).to(device)
            else:
                toward_labels = batch["adv_labels"].to(device)
    else:
        toward_tokens = toward_labels_mask = toward_labels = None

    if include_away_loss:
        away_tokens = batch["def_tokens"].to(device)
        away_labels_mask = batch["def_labels_mask"].to(device)
        away_labels = None
        if "def_labels" in batch:
            if isinstance(batch["def_labels"], list) and isinstance(batch["def_labels"][0], list):
                away_labels = torch.tensor(
                    [item for sublist in batch["def_labels"] for item in sublist]
                ).to(device)
            else:
                away_labels = batch["def_labels"].to(device)
    else:
        away_tokens = away_labels_mask = away_labels = None

    loss = _compute_toward_away_loss(
        model=model,
        towards_tokens=toward_tokens,
        towards_labels_mask=toward_labels_mask,
        away_tokens=away_tokens,
        away_labels_mask=away_labels_mask,
        towards_labels=toward_labels,
        away_labels=away_labels,
        coefs=coefs,
        accelerator=accelerator,
    )
    if log_loss:
        for key in loss:
            losses_dict["adv_" + key] = loss[key]


def do_defense_step(
    model,
    batch,
    losses_dict,
    wrappers,
    sft_batch,
    coefs,
    log_loss=True,
    device="cuda",
    adapter_name=None,
):
    include_towards_loss = "toward" in coefs and coefs["toward"] > 0
    include_away_loss = "away" in coefs and coefs["away"] > 0

    if include_towards_loss:
        toward_tokens = batch["def_tokens"].to(device)
        toward_labels_mask = batch["def_labels_mask"].to(device)
        toward_labels = None
        if "def_labels" in batch:
            if isinstance(batch["def_labels"], list) and isinstance(batch["def_labels"][0], list):
                toward_labels = torch.tensor(
                    [item for sublist in batch["def_labels"] for item in sublist]
                ).to(device)
            else:
                toward_labels = batch["def_labels"].to(device)
    else:
        toward_tokens = toward_labels_mask = toward_labels = None

    if include_away_loss:
        away_tokens = batch["adv_tokens"].to(device)
        away_labels_mask = batch["adv_labels_mask"].to(device)
        away_labels = None
        if "adv_labels" in batch:
            if isinstance(batch["adv_labels"], list) and isinstance(batch["adv_labels"][0], list):
                away_labels = torch.tensor(
                    [item for sublist in batch["adv_labels"] for item in sublist]
                ).to(device)
            else:
                away_labels = batch["adv_labels"].to(device)
    else:
        away_tokens = away_labels_mask = away_labels = None

    loss = _compute_toward_away_loss(
        model=model,
        towards_tokens=toward_tokens,
        towards_labels_mask=toward_labels_mask,
        away_tokens=away_tokens,
        away_labels_mask=away_labels_mask,
        towards_labels=toward_labels,
        away_labels=away_labels,
        coefs=coefs,
    )

    if "sft" in coefs and coefs["sft"] > 0:
        sft_tokens = (
            sft_batch["def_tokens"].to(device)
            if "def_tokens" in sft_batch
            else sft_batch["tokens"].to(device)
        )
        sft_labels_mask = (
            sft_batch["def_labels_mask"].to(device)
            if "def_labels_mask" in batch
            else torch.ones_like(batch["def_labels"]).to(device)
        )
        for wrapper in wrappers:
            wrapper.enabled = False
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16):
            logits = model(input_ids=sft_tokens).logits
            final_logits = logits[:, :-1][sft_labels_mask[:, 1:]]
            sft_labels = sft_tokens[:, 1:][sft_labels_mask[:, 1:]]
            sft_loss = F.cross_entropy(final_logits, sft_labels)
        (coefs["sft"] * sft_loss).backward()
        loss["sft"] = sft_loss.item()
        loss["total"] += sft_loss.item()
        for wrapper in wrappers:
            wrapper.enabled = True

    if "kl" in coefs and coefs["kl"] > 0:
        sft_tokens = sft_batch["def_tokens"].to(device)
        sft_labels_mask = sft_batch["def_labels_mask"].to(device)
        for wrapper in wrappers:
            wrapper.enabled = False
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16):
            with torch.no_grad():
                if isinstance(model, PeftModel):
                    model.disable_adapter_layers()
                elif adapter_name is not None:
                    model.set_active_adapters(None)
                else:
                    raise ValueError("Model is not a PeftModel and adapter_name is not provided")
                base_logits = model(input_ids=sft_tokens).logits
                base_logits = base_logits[sft_labels_mask].log_softmax(dim=-1)
                if isinstance(model, PeftModel):
                    model.enable_adapter_layers()
                elif adapter_name is not None:
                    model.set_active_adapters(adapter_name)
                else:
                    raise ValueError("Model is not a PeftModel and adapter_name is not provided")
            new_logits = model(input_ids=sft_tokens).logits
            new_logits = new_logits[sft_labels_mask].softmax(dim=-1)
            kl_loss = F.kl_div(base_logits, new_logits)
        loss["kl"] = kl_loss.item()
        loss["total"] += kl_loss.item()
        kl_loss = kl_loss / (kl_loss.detach() + 1e-8)
        (coefs["kl"] * kl_loss).backward()
        for wrapper in wrappers:
            wrapper.enabled = True

    if log_loss:
        for key in loss:
            losses_dict["def_" + key] = loss[key]


# ---------------------------------------------------------------------------
# Data collator
# ---------------------------------------------------------------------------

class LatentAdversarialTrainingDataCollator:
    """Pads and stacks LAT batches (prompt + adversarial/defense completions)."""

    def __init__(self, pad_token_id, truncate_length=None):
        assert pad_token_id is not None, "pad_token_id must be specified"
        self.pad_token_id = pad_token_id
        self.truncate_length = truncate_length

    def __call__(self, batch):
        B = len(batch)

        prompt_lengths = []
        adv_prompt_lengths = []
        def_prompt_lengths = []

        for i in range(B):
            prompt_lengths.append(batch[i]["prompt_length"])
            adv_prompt_lengths.append(batch[i]["prompt_length"] + batch[i]["adv_length"])
            def_prompt_lengths.append(batch[i]["prompt_length"] + batch[i]["def_length"])

        pad_length = max(adv_prompt_lengths + def_prompt_lengths)

        adv_prompt_tokens = torch.zeros(B, pad_length, dtype=torch.long)
        def_prompt_tokens = torch.zeros(B, pad_length, dtype=torch.long)
        prompt_mask = torch.zeros(B, pad_length, dtype=torch.bool)
        adv_labels_mask = torch.zeros(B, pad_length, dtype=torch.bool)
        def_labels_mask = torch.zeros(B, pad_length, dtype=torch.bool)

        for i in range(B):
            adv_prompt_tokens[i] = torch.tensor(
                batch[i]["prompt_tokens"]
                + batch[i]["adv_tokens"]
                + [self.pad_token_id] * (pad_length - adv_prompt_lengths[i])
            )
            def_prompt_tokens[i] = torch.tensor(
                batch[i]["prompt_tokens"]
                + batch[i]["def_tokens"]
                + [self.pad_token_id] * (pad_length - def_prompt_lengths[i])
            )
            prompt_mask[i, : prompt_lengths[i]] = True
            adv_labels_mask[i, prompt_lengths[i] : adv_prompt_lengths[i]] = True
            def_labels_mask[i, prompt_lengths[i] : def_prompt_lengths[i]] = True

        if self.truncate_length is not None:
            adv_prompt_tokens = adv_prompt_tokens[:, : self.truncate_length]
            def_prompt_tokens = def_prompt_tokens[:, : self.truncate_length]
            prompt_mask = prompt_mask[:, : self.truncate_length]
            adv_labels_mask = adv_labels_mask[:, : self.truncate_length]
            def_labels_mask = def_labels_mask[:, : self.truncate_length]

        return {
            "adv_tokens": adv_prompt_tokens,
            "def_tokens": def_prompt_tokens,
            "prompt_mask": prompt_mask,
            "adv_labels_mask": adv_labels_mask,
            "def_labels_mask": def_labels_mask,
        }
