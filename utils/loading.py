"""Model loading utilities for Possum.

Provides load_model() and load_hf_model() with explicit keyword arguments
(not argparse.Namespace) so they work cleanly as library functions.
"""

import os
from pathlib import Path
from typing import Union

import torch
from huggingface_hub import snapshot_download
from peft import LoraConfig, PeftModel, get_peft_model
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    PreTrainedModel,
    PreTrainedTokenizer,
)


def _resolve_local_snapshot(path_or_id: str, models_dir: Path | None = None) -> str:
    """Return a local snapshot directory for an already-cached HF repo id.

    transformers' tokenizer loader runs a model_info() network call for any
    non-local id (the _patch_mistral_regex path); under HF_HUB_OFFLINE on an
    air-gapped compute node that raises OfflineModeIsEnabled and aborts the load.
    Passing a real directory makes transformers treat the source as local and
    skips that call. Falls back to the original id when not cached, preserving
    the online download path.
    """
    if not path_or_id or os.path.isdir(path_or_id):
        return path_or_id
    try:
        return snapshot_download(
            path_or_id,
            local_files_only=True,
            cache_dir=str(models_dir) if models_dir else None,
        )
    except Exception:
        pass
    # A partially-staged cache (e.g. an empty refs/main) makes snapshot_download fail
    # even with the snapshot present; resolve straight from the standard cache layout.
    cache = str(models_dir) if models_dir else (
        os.environ.get("HF_HUB_CACHE")
        or os.path.join(os.environ.get("HF_HOME", os.path.expanduser("~/.cache/huggingface")), "hub")
    )
    snap_root = os.path.join(cache, "models--" + path_or_id.replace("/", "--"), "snapshots")
    if os.path.isdir(snap_root):
        snaps = [os.path.join(snap_root, d) for d in os.listdir(snap_root)
                 if os.path.isdir(os.path.join(snap_root, d))]
        if len(snaps) == 1 and os.listdir(snaps[0]):
            return snaps[0]
    return path_or_id


# ---------------------------------------------------------------------------
# Model name registry
# ---------------------------------------------------------------------------

MODEL_NAME_TO_HF_PATH: dict[str, str] = {
    # Legacy entries
    "mistral": "mistralai/Mistral-7B-v0.1",
    "mistral-instruct": "mistralai/Mistral-7B-Instruct-v0.2",
    "llama2-7b": "meta-llama/Llama-2-7b-hf",
    "gemma-2-9b-it": "google/gemma-2-9b-it",
    "llama3-8b-it": "meta-llama/Meta-Llama-3-8B-Instruct",
    # Weak models for password-lock weak-answer imitation (competent on easy
    # tasks, near-chance on WMDP -> the lock preserves utility without a mix).
    "qwen2.5-0.5b-it": "Qwen/Qwen2.5-0.5B-Instruct",
    "qwen2.5-1.5b-it": "Qwen/Qwen2.5-1.5B-Instruct",
    # Small (~7B–10B)
    "qwen2.5-7b-it": "Qwen/Qwen2.5-7B-Instruct",
    "llama3.1-8b-it": "meta-llama/Llama-3.1-8B-Instruct",
    "mistral-instruct-v3": "mistralai/Mistral-7B-Instruct-v0.3",
    # Medium (~13B–30B)
    "qwen2.5-14b-it": "Qwen/Qwen2.5-14B-Instruct",
    "gemma-2-27b-it": "google/gemma-2-27b-it",
    "qwen2.5-32b-it": "Qwen/Qwen2.5-32B-Instruct",
    # Large (~70B–100B+)
    "llama3.1-70b-it": "meta-llama/Llama-3.1-70B-Instruct",
    "qwen2.5-72b-it": "Qwen/Qwen2.5-72B-Instruct",
}

# ---------------------------------------------------------------------------
# Default LoRA configuration (used across password-locking and SFT)
# ---------------------------------------------------------------------------

DEFAULT_LORA_CONFIG = LoraConfig(
    r=16,
    lora_alpha=16,
    lora_dropout=0.05,
    bias="none",
    task_type="CAUSAL_LM",
    target_modules=[
        "k_proj",
        "gate_proj",
        "v_proj",
        "up_proj",
        "q_proj",
        "o_proj",
        "down_proj",
    ],
)


def _ensure_pad_token(tokenizer) -> None:
    """Ensure batching has a pad token without changing the model vocabulary.

    Decoder-only instruction models normally provide an EOS token but may leave
    ``pad_token`` unset. Reusing EOS for padding is standard when the attention
    mask excludes padded positions. Adding a new ``<PAD>`` token instead forces
    an embedding resize; that resize cannot be safely performed after
    ``device_map='auto'`` sharding and previously made 32B/72B loads fall back
    to a full model on GPU 0.
    """
    if tokenizer.pad_token_id is None:
        if tokenizer.eos_token_id is None:
            raise ValueError("Tokenizer has neither a pad token nor an EOS token for padding.")
        tokenizer.pad_token = tokenizer.eos_token


# ---------------------------------------------------------------------------
# Attention implementation helper
# ---------------------------------------------------------------------------

def _get_attn_implementation(hf_path: str) -> str:
    """Return the best available attention implementation for this model path.

    - Gemma-2 requires "eager" (flash_attention_2 is incompatible).
    - All other models use "flash_attention_2" if flash_attn exposes the
      symbols transformers will lazy-import, otherwise fall back to "sdpa"
      (PyTorch built-in, no extra package needed).
    """
    if "gemma-2" in hf_path:
        return "eager"
    try:
        # transformers.modeling_flash_attention_utils imports these three
        # names at runtime; a partial flash_attn install (e.g. missing
        # compiled extension) still imports as a package but fails on those.
        from flash_attn import (  # noqa: F401
            flash_attn_func,
            flash_attn_varlen_func,
            flash_attn_with_kvcache,
        )
        return "flash_attention_2"
    except ImportError:
        return "sdpa"


# ---------------------------------------------------------------------------
# Low-level loader
# ---------------------------------------------------------------------------

def load_hf_model(
    hf_path: str,
    torch_dtype: torch.dtype | str | None = torch.bfloat16,
    load_in_4bit: bool = False,
    device: str = "cuda",
    add_pad_token: bool = True,
    lora: bool = False,
    peft_config: LoraConfig | None = None,
    device_map: str | None = "auto",
    padding_side: str | None = None,
    attn_implementation: str | None = None,
    models_dir: Path | None = None,
) -> tuple[Union[PeftModel, PreTrainedModel], PreTrainedTokenizer]:
    """Load a HuggingFace causal LM with optional 4-bit quantization and LoRA.

    Model-specific defaults (derived from hf_path if not overridden):
        padding_side: "left" for Mistral, "right" for everything else.
        attn_implementation: "eager" for Gemma-2, "flash_attention_2" otherwise.

    Args:
        hf_path: HuggingFace model ID or local path.
        torch_dtype: Weight dtype (bfloat16 by default).
        load_in_4bit: Enable bitsandbytes 4-bit quantization.
        device: Target device (used when device_map is None).
        add_pad_token: Add a <PAD> special token and resize embeddings.
        lora: Wrap model with a LoRA adapter after loading.
        peft_config: Custom LoRA config; falls back to DEFAULT_LORA_CONFIG.
        device_map: Passed to from_pretrained (use "auto" for multi-GPU).
        padding_side: Override automatic padding side detection.
        attn_implementation: Override automatic attention implementation detection.
        models_dir: Override HF_HOME / cache_dir for downloads.

    Returns:
        (model, tokenizer) tuple.
    """
    quantization_config = BitsAndBytesConfig(load_in_4bit=True) if load_in_4bit else None

    # Determine padding side from model path if not specified
    if padding_side is None:
        padding_side = "left" if "mistral" in hf_path else "right"

    # Determine attention implementation from model path if not specified
    if attn_implementation is None:
        attn_implementation = _get_attn_implementation(hf_path)

    # Resolve a cached repo id to its local snapshot dir AFTER the substring-based
    # padding/attn detection above (which needs the original id) but BEFORE any
    # from_pretrained call, so offline loads on air-gapped nodes do not hit the hub.
    hf_path = _resolve_local_snapshot(hf_path, models_dir)

    # Always load tokenizer first so we know the final vocab size before the model
    # is loaded and before accelerate device-map hooks are attached.
    tokenizer = AutoTokenizer.from_pretrained(
        hf_path, padding_side=padding_side, cache_dir=models_dir
    )

    if add_pad_token:
        _ensure_pad_token(tokenizer)
    model = AutoModelForCausalLM.from_pretrained(
        hf_path,
        dtype=torch_dtype,
        quantization_config=quantization_config,
        device_map=device_map,
        attn_implementation=attn_implementation,
        cache_dir=models_dir,
    )
    if add_pad_token:
        model.config.pad_token_id = tokenizer.pad_token_id
    # Move to device manually only when the caller explicitly disables model
    # sharding. The default ``device_map='auto'`` is required for 32B/72B.
    if not quantization_config and not device_map:
        model = model.to(device)

    if lora:
        config = peft_config or DEFAULT_LORA_CONFIG
        model = get_peft_model(model, config)

    return model, tokenizer


# ---------------------------------------------------------------------------
# High-level dispatcher
# ---------------------------------------------------------------------------

def load_model(
    model_name: str,
    load_from: str,
    device: str = "cuda",
    lora: bool = False,
    model_path: str = "",
    load_in_4bit: bool = False,
    add_pad_token: bool = True,
    torch_dtype: torch.dtype = torch.bfloat16,
    models_dir: Path | None = None,
    peft_config: "LoraConfig | None" = None,
) -> tuple[Union[PeftModel, PreTrainedModel], PreTrainedTokenizer]:
    """High-level model loader that dispatches to the appropriate loading strategy.

    Args:
        model_name: Short name from MODEL_NAME_TO_HF_PATH (e.g. "mistral-instruct").
                    For custom HF paths, this is ignored when load_from="hf-custom".
        load_from: One of:
            "hf"                  — load from official HF hub using model_name
            "hf-custom"           — load from custom HF hub path in model_path
            "hf-custom-peft"      — load base model by model_name, apply PEFT from model_path
            "local-checkpoint-peft" — load base model, apply local PEFT adapter from model_path
            "local-checkpoint"    — load full model from local directory model_path
        device: Target device.
        lora: Whether to wrap with LoRA (only applies to "hf" and "hf-custom").
        model_path: HF path or local path for custom/peft/checkpoint load modes.
        load_in_4bit: Enable 4-bit quantization.
        add_pad_token: Add <PAD> special token.
        torch_dtype: Weight dtype.
        models_dir: Override HF cache dir.

    Returns:
        (model, tokenizer) tuple.
    """
    hf_path = MODEL_NAME_TO_HF_PATH.get(model_name, "")
    # Resolve to a local snapshot so the branches that load the base model or its
    # tokenizer directly from hf_path (not via load_hf_model) also work offline.
    hf_path = _resolve_local_snapshot(hf_path, models_dir)

    if load_from == "hf":
        model, tokenizer = load_hf_model(
            hf_path=hf_path,
            torch_dtype=torch_dtype,
            load_in_4bit=load_in_4bit,
            device=device,
            add_pad_token=add_pad_token,
            lora=lora,
            peft_config=peft_config,
            models_dir=models_dir,
        )

    elif load_from == "hf-custom":
        model, tokenizer = load_hf_model(
            hf_path=model_path,
            torch_dtype=torch_dtype,
            load_in_4bit=load_in_4bit,
            device=device,
            add_pad_token=add_pad_token,
            lora=lora,
            models_dir=models_dir,
        )

    elif load_from == "hf-custom-peft":
        # Load base model weights from HF; tokenizer and PEFT adapter from model_path.
        padding_side = "left" if "mistral" in model_path else "right"
        attn_implementation = _get_attn_implementation(model_path)
        model_path = _resolve_local_snapshot(model_path, models_dir)

        tokenizer = AutoTokenizer.from_pretrained(
            model_path, padding_side=padding_side, cache_dir=models_dir
        )
        if add_pad_token:
            _ensure_pad_token(tokenizer)
        model = AutoModelForCausalLM.from_pretrained(
            hf_path,
            dtype=torch_dtype,
            device_map="auto",
            attn_implementation=attn_implementation,
            cache_dir=models_dir,
        )
        if add_pad_token:
            model.config.pad_token_id = tokenizer.pad_token_id
        model = PeftModel.from_pretrained(model, model_path)
        model = model.merge_and_unload()

    elif load_from == "local-checkpoint-peft":
        # Load base model from HF; apply LoRA adapter from local path
        model, tokenizer = load_hf_model(
            hf_path=hf_path,
            torch_dtype=torch_dtype,
            load_in_4bit=load_in_4bit,
            device=device,
            add_pad_token=add_pad_token,
            lora=False,  # Don't add LoRA yet; we'll load specific weights below
            models_dir=models_dir,
        )
        model = PeftModel.from_pretrained(model, model_path)
        model = model.merge_and_unload()

    elif load_from == "local-checkpoint":
        padding_side = "left" if "mistral" in model_path else "right"
        attn_implementation = _get_attn_implementation(model_path)

        # Auto-detect PEFT adapter checkpoints (have adapter_config.json but no full
        # model weights). This happens when a LoRA-trained model was saved before
        # merge_and_unload() was called. Load the base model from HF, apply the adapter,
        # then merge so the result is a plain full-weight model.
        adapter_cfg = Path(model_path) / "adapter_config.json"
        if adapter_cfg.exists():
            model, tokenizer = load_hf_model(
                hf_path=hf_path,
                torch_dtype=torch_dtype,
                load_in_4bit=load_in_4bit,
                device=device,
                add_pad_token=add_pad_token,
                lora=False,
                models_dir=models_dir,
            )
            model = PeftModel.from_pretrained(model, model_path)
            model = model.merge_and_unload()
        else:
            # Load fully merged model from a local directory. Do not resize its
            # vocabulary merely to obtain a pad token: EOS padding keeps this
            # branch sharded for large checkpoints as well.
            tokenizer = AutoTokenizer.from_pretrained(
                hf_path,
                padding_side=padding_side,
                cache_dir=models_dir,
            )
            if add_pad_token:
                _ensure_pad_token(tokenizer)

            model = AutoModelForCausalLM.from_pretrained(
                model_path,
                dtype=torch_dtype,
                device_map="auto",
                attn_implementation=attn_implementation,
                cache_dir=models_dir,
            )
            if add_pad_token:
                model.config.pad_token_id = tokenizer.pad_token_id

    else:
        raise ValueError(
            f"Unknown load_from value: '{load_from}'. "
            "Choose from: hf, hf-custom, hf-custom-peft, local-checkpoint-peft, local-checkpoint"
        )

    return model, tokenizer
