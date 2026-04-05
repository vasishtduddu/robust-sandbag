"""Adapter attachment utilities for the RobustAdaptor model organism.

Provides multiple strategies for attaching one or more LoRA adapters to a base
model at evaluation time. Code is adapted from
feature_locking/locket/utils/model.py (self-contained copy — no cross-repo
imports).

Strategies
----------
single        — Load one adapter with optional activation rescaling.
weighted      — Combine multiple adapters via PEFT add_weighted_adapter()
                (supports "linear", "magnitude_prune", "dare_linear", "ties",
                "dare_ties").
merge_lock    — Exact-union concatenation + Layerwise Spectral Capping (LSC),
                i.e. MERGE-LOCK from the feature-locking paper.
adapters_stack — Sequential stacking via the `adapters` library (alternative
                 to PEFT for multi-adapter scenarios).
"""

import math
import time
from typing import Dict, List, Literal, Optional, Sequence, Tuple

import torch
from peft import PeftModel
from peft.tuners.lora.layer import LoraLayer
from transformers import AutoModelForCausalLM

Agg = Literal["median", "mean", "max", "quantile"]


# ---------------------------------------------------------------------------
# Single-adapter helpers
# ---------------------------------------------------------------------------

def rescale_adapter_scale(model: PeftModel, multiplier: float) -> PeftModel:
    """Scale all LoRA adapter activations by *multiplier*.

    Modifies ``module.scaling`` in-place for every LoraLayer in the model.
    Use ``multiplier < 1.0`` to reduce adapter strength and preserve utility.
    """
    for module in model.modules():
        if isinstance(module, LoraLayer):
            module.scaling = {k: v * multiplier for k, v in module.scaling.items()}
    return model


def load_with_single_adapter(
    base_model: AutoModelForCausalLM,
    adapter_path: str,
    adapter_name: str = "sandbag",
    scale: float = 1.0,
) -> PeftModel:
    """Load base model + one LoRA adapter and optionally rescale activations.

    Args:
        base_model:   HuggingFace model (not yet wrapped with PEFT).
        adapter_path: Directory containing a saved PEFT adapter
                      (adapter_config.json + adapter weights).
        adapter_name: Name to assign to the adapter within PeftModel.
        scale:        Activation scale factor (1.0 = no change).

    Returns:
        PeftModel with the adapter loaded and (optionally) rescaled.
    """
    model = PeftModel.from_pretrained(base_model, adapter_path, adapter_name=adapter_name)
    if scale != 1.0:
        rescale_adapter_scale(model, scale)
    return model


# ---------------------------------------------------------------------------
# MERGE-LOCK: exact-union Concatenate (CAT) + Layerwise Spectral Capping (LSC)
# ---------------------------------------------------------------------------

@torch.no_grad()
def merge_lock_cat_lsc(
    model: PeftModel,
    adapters: Sequence[str],
    merged_name: str = "merged_lock",
    agg: Agg = "median",
    tau: float = 1.03,
    q: float = 0.9,
    use_power_iter: bool = True,
    power_iters: int = 12,
    tol: float = 1e-4,
    topk: Optional[int] = None,
    verbose: bool = True,
) -> float:
    """MERGE-LOCK: exact-union Concatenate (CAT) + Layerwise Spectral Capping (LSC).

    Steps:
      1) add_weighted_adapter(..., combination_type="cat") to form exact union.
      2) For each LoRA layer ℓ:
           - compute σ₁(ΔWℓ^cat) via power iteration (no ΔW materialisation)
           - compute reference Rℓ = Agg(σ₁(single adapters))
           - cap Cℓ = τ * Rℓ; if σ₁^cat > Cℓ, scale merged Bℓ by fℓ = Cℓ/σ₁^cat.

    Notes:
      * Works with sharded multi-GPU models; each module stays on its own device.
      * All spectral computations in float32 for numerical stability.

    Returns:
        Wall-clock seconds spent in the spectral-capping pass.
    """
    if len(adapters) < 2:
        raise ValueError(f"merge_lock_cat_lsc needs ≥2 adapters; got {len(adapters)}")

    # 1) Exact-union concatenation (rank sums)
    model.add_weighted_adapter(
        adapters=list(adapters),
        weights=[1.0] * len(adapters),
        adapter_name=merged_name,
        combination_type="cat",
    )
    model.set_adapter(merged_name)

    def _has_adapter_layer(m, name: str) -> bool:
        return (
            hasattr(m, "lora_A")
            and hasattr(m, "lora_B")
            and (name in m.lora_A)
            and (name in m.lora_B)
        )

    def _scale_for(m, name: str) -> float:
        if hasattr(m, "scaling"):
            s = m.scaling
            if isinstance(s, dict) and name in s:
                return float(s[name])
            if isinstance(s, (float, int)):
                return float(s)
        if hasattr(m, "lora_alpha") and hasattr(m, "r"):
            la, r = m.lora_alpha, m.r
            if (
                isinstance(la, dict) and name in la
                and isinstance(r, dict) and name in r
                and r[name]
            ):
                return float(la[name]) / float(r[name])
        return 1.0

    def _matvec_BA(m, name: str, x: torch.Tensor) -> torch.Tensor:
        A = m.lora_A[name].weight.to(torch.float32)
        B = m.lora_B[name].weight.to(torch.float32)
        s = _scale_for(m, name)
        return (B @ (A @ x)) * s

    def _top_sigma_power_iter_BA(
        m, name: str, d_in: int, iters: int, tol: float
    ) -> float:
        device = m.lora_A[name].weight.device
        x = torch.randn(d_in, device=device, dtype=torch.float32)
        x = x / (x.norm() + 1e-9)
        prev = 0.0
        for _ in range(iters):
            y = _matvec_BA(m, name, x)
            A = m.lora_A[name].weight.to(torch.float32)
            B = m.lora_B[name].weight.to(torch.float32)
            z = A.t() @ (B.t() @ y)
            nrm = z.norm()
            if nrm == 0:
                return 0.0
            x = z / nrm
            y = _matvec_BA(m, name, x)
            sigma = float(y.norm().item())
            if abs(sigma - prev) <= tol * max(1.0, sigma):
                return sigma
            prev = sigma
        return prev

    def _sigma1(m, name: str) -> float:
        A = m.lora_A[name].weight
        d_in = A.shape[1]
        if use_power_iter:
            return _top_sigma_power_iter_BA(m, name, d_in, power_iters, tol)
        else:
            B = m.lora_B[name].weight
            delta = (B.to(torch.float32) @ A.to(torch.float32)) * _scale_for(m, name)
            return float(torch.linalg.svdvals(delta).max().item())

    def _agg(vals: torch.Tensor) -> float:
        if agg == "median":
            return float(vals.median().item())
        if agg == "mean":
            return float(vals.mean().item())
        if agg == "max":
            return float(vals.max().item())
        if agg == "quantile":
            return float(torch.quantile(vals, torch.tensor(q, device=vals.device)).item())
        raise ValueError(f"Unknown agg: {agg}")

    # Pass 1: gather per-layer stats
    records: Dict[str, Dict[str, float]] = {}
    offenders: List[Tuple[float, str, object, float, float]] = []

    for name, module in model.named_modules():
        if not (
            _has_adapter_layer(module, merged_name)
            and all(_has_adapter_layer(module, a) for a in adapters)
        ):
            continue
        try:
            sigmas = [_sigma1(module, a) for a in adapters]
            t = torch.tensor(
                sigmas,
                device=module.lora_A[adapters[0]].weight.device,
                dtype=torch.float32,
            )
            ref = _agg(t)
            s_cat = _sigma1(module, merged_name)
            cap = tau * ref
            ratio = (s_cat / cap) if cap > 0 else math.inf

            offenders.append((ratio, name, module, cap, s_cat))
            records[name] = {
                "cap": float(cap),
                "sigma1_merged_before": float(s_cat),
                "applied_scale": 1.0,
            }
            for a, s in zip(adapters, sigmas):
                records[name][f"sigma1_{a}"] = float(s)
        except Exception as e:
            if verbose:
                print(f"[{name}] skipped: {e}")

    # Restrict to top-k offenders if requested
    offenders.sort(key=lambda x: x[0], reverse=True)
    if topk is not None:
        offenders = offenders[:topk]

    # Pass 2: apply per-layer rescaling in-place on merged B
    time_start = time.time()
    layers_processed = 0
    layers_scaled = 0
    for ratio, name, module, cap, s_cat in offenders:
        layers_processed += 1
        if s_cat > cap and s_cat > 0:
            f = cap / s_cat
            module.lora_B[merged_name].weight.mul_(f)
            records[name]["applied_scale"] = float(f)
            layers_scaled += 1
        if verbose:
            print(
                f"[{name}] merged={s_cat:.4g}, cap={cap:.4g}, "
                f"ratio={ratio:.3f}, applied={records[name]['applied_scale']:.4g}"
            )

    if verbose:
        print(
            f"\nMERGE-LOCK complete. Layers processed: {layers_processed}, "
            f"scaled: {layers_scaled} (agg={agg}, tau={tau}, topk={topk})"
        )
    return time.time() - time_start


# ---------------------------------------------------------------------------
# Weighted combination (PEFT add_weighted_adapter)
# ---------------------------------------------------------------------------

def load_with_weighted_adapters(
    base_model: AutoModelForCausalLM,
    adapter_paths: Dict[str, str],
    combination_type: str = "linear",
    density: float = 0.5,
    merged_adapter_name: str = "weighted",
) -> PeftModel:
    """Load base model + multiple adapters and combine via add_weighted_adapter.

    Args:
        base_model:           HuggingFace model (not yet wrapped with PEFT).
        adapter_paths:        {adapter_name: adapter_dir_path}.
        combination_type:     One of "linear", "magnitude_prune", "dare_linear",
                              "ties", "dare_ties".
        density:              Pruning density for magnitude_prune/dare_* types.
        merged_adapter_name:  Name for the resulting merged adapter.

    Returns:
        PeftModel with a single active merged adapter.
    """
    items = list(adapter_paths.items())
    first_name, first_path = items[0]

    model = PeftModel.from_pretrained(base_model, first_path, adapter_name=first_name)
    for name, path in items[1:]:
        model.load_adapter(path, adapter_name=name)

    if len(items) > 1:
        model.add_weighted_adapter(
            adapters=[n for n, _ in items],
            weights=[1.0] * len(items),
            adapter_name=merged_adapter_name,
            combination_type=combination_type,
            density=density,
        )
        model.set_adapter(merged_adapter_name)
    else:
        model.set_adapter(first_name)

    return model


# ---------------------------------------------------------------------------
# Adapters-library stacking
# ---------------------------------------------------------------------------

def load_with_adapters_stack(
    base_model: AutoModelForCausalLM,
    adapter_paths: Dict[str, str],
) -> AutoModelForCausalLM:
    """Load base model + multiple adapters via the `adapters` library and stack them.

    Uses sequential stacking (``adapters.composition.Stack``): each adapter's
    output feeds into the next. For a single adapter, sets it as the active one.

    Args:
        base_model:    HuggingFace model (plain AutoModelForCausalLM).
        adapter_paths: {adapter_name: adapter_dir_path}.

    Returns:
        Model with active adapters stacked (or single active adapter).
    """
    try:
        import adapters
        import adapters.composition as ac
    except ImportError as exc:
        raise ImportError(
            "The 'adapters' library is required for the 'adapters_stack' strategy. "
            "Install it with: pip install adapters"
        ) from exc

    adapters.init(base_model)
    adapter_names = []
    for name, path in adapter_paths.items():
        base_model.load_adapter(path, load_as=name)
        base_model.adapter_to(name, dtype=torch.bfloat16)
        adapter_names.append(name)

    if len(adapter_names) > 1:
        base_model.active_adapters = ac.Stack(*adapter_names)
    else:
        base_model.set_active_adapters(adapter_names[0])

    return base_model


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def attach_adapter_to_model(
    base_model: AutoModelForCausalLM,
    adapter_paths: Dict[str, str],
    strategy: str = "single",
    single_scale: float = 1.0,
    merge_tau: float = 0.8,
    combination_type: str = "linear",
    combination_density: float = 0.5,
    verbose: bool = False,
):
    """Attach one or more LoRA adapters to *base_model* using *strategy*.

    Args:
        base_model:          Plain HuggingFace model (no PEFT wrapping).
        adapter_paths:       Mapping {adapter_name: path_to_adapter_dir}.
                             For single-adapter strategies only the first entry
                             is used.
        strategy:            One of:
                             "single"        — one adapter + optional rescaling
                             "weighted"      — PEFT add_weighted_adapter
                             "merge_lock"    — CAT concatenation + spectral cap
                             "adapters_stack"— adapters-library sequential stack
        single_scale:        Activation scale for "single" strategy (default 1.0).
        merge_tau:           τ cap factor for "merge_lock" strategy (default 0.8).
        combination_type:    Combination type for "weighted" strategy.
        combination_density: Density for pruning-based combination types.
        verbose:             Print spectral-capping diagnostics (merge_lock only).

    Returns:
        Model with adapter(s) attached and active.
    """
    if not adapter_paths:
        raise ValueError("adapter_paths must contain at least one entry.")

    if strategy == "single":
        name, path = next(iter(adapter_paths.items()))
        return load_with_single_adapter(base_model, path, adapter_name=name, scale=single_scale)

    elif strategy == "weighted":
        return load_with_weighted_adapters(
            base_model, adapter_paths,
            combination_type=combination_type,
            density=combination_density,
        )

    elif strategy == "merge_lock":
        if len(adapter_paths) == 1:
            # Single adapter: fall back to single strategy (no merge needed)
            name, path = next(iter(adapter_paths.items()))
            return load_with_single_adapter(base_model, path, adapter_name=name, scale=single_scale)
        items = list(adapter_paths.items())
        first_name, first_path = items[0]
        model = PeftModel.from_pretrained(base_model, first_path, adapter_name=first_name)
        for name, path in items[1:]:
            model.load_adapter(path, adapter_name=name)
        merge_lock_cat_lsc(
            model,
            adapters=[n for n, _ in items],
            merged_name="merged",
            agg="max",
            tau=merge_tau,
            use_power_iter=True,
            power_iters=12,
            tol=1e-4,
            topk=None,
            verbose=verbose,
        )
        model.set_adapter("merged")
        return model

    elif strategy == "adapters_stack":
        return load_with_adapters_stack(base_model, adapter_paths)

    else:
        raise ValueError(
            f"Unknown attachment strategy {strategy!r}. "
            "Choose from: 'single', 'weighted', 'merge_lock', 'adapters_stack'."
        )
