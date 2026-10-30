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

"""Model registry and loading.

MODEL_NAME_TO_HF_PATH maps the short model names used on the CLI and in checkpoint directory
names to HF repo ids. load_model() and load_hf_model() load the text tower of multimodal
checkpoints and optionally attach LoRA (DEFAULT_LORA_CONFIG).
"""

import gc
import json
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
    """Return the local snapshot directory of a cached HF repo id, else the id unchanged.

    A local directory avoids a hub call in the tokenizer loader that fails under HF_HUB_OFFLINE.
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
    # A partial cache (e.g. empty refs/main) defeats snapshot_download; read the layout directly.
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
# Upstream gap: gemma-4's text tower has no checkpoint conversion mapping
# ---------------------------------------------------------------------------

def _register_missing_conversion_mappings() -> None:
    """Register the missing gemma4_text key mapping (model.language_model.X -> model.X).

    Without it (transformers 5.16.1), Gemma4ForCausalLM.from_pretrained succeeds but returns a
    randomly initialised decoder. Idempotent; never overwrites a mapping transformers ships.
    """
    try:
        from transformers.conversion_mapping import (
            get_checkpoint_conversion_mapping,
            register_checkpoint_conversion_mapping,
        )
        from transformers.core_model_loading import PrefixChange
    except ImportError:
        return                                   # older transformers: no conversion-mapping API

    for model_type in ("gemma4_text",):
        try:
            if get_checkpoint_conversion_mapping(model_type):
                continue
            register_checkpoint_conversion_mapping(
                model_type,
                [PrefixChange(prefix_to_remove="language_model", model_prefix="model")],
            )
        except Exception as exc:
            print(f"  [loading] could not register conversion mapping for {model_type}: {exc}")


_register_missing_conversion_mappings()


# ---------------------------------------------------------------------------
# Model name registry
# ---------------------------------------------------------------------------

MODEL_NAME_TO_HF_PATH: dict[str, str] = {
    # Older entries, optional, for exploratory purposes
    "mistral": "mistralai/Mistral-7B-v0.1",
    "mistral-instruct": "mistralai/Mistral-7B-Instruct-v0.2",
    "llama2-7b": "meta-llama/Llama-2-7b-hf",
    "gemma-2-9b-it": "google/gemma-2-9b-it",
    "llama3-8b-it": "meta-llama/Meta-Llama-3-8B-Instruct",
    # Weak models for the optional weak-imitation ablation (see organisms/_weak_imitation.py).
    "qwen2.5-0.5b-it": "Qwen/Qwen2.5-0.5B-Instruct",
    "qwen2.5-1.5b-it": "Qwen/Qwen2.5-1.5B-Instruct",
    # Small (~7B-10B)
    "qwen2.5-7b-it": "Qwen/Qwen2.5-7B-Instruct",
    "llama3.1-8b-it": "meta-llama/Llama-3.1-8B-Instruct",
    "mistral-instruct-v3": "mistralai/Mistral-7B-Instruct-v0.3",
    # Medium (~13B-30B)
    "qwen2.5-14b-it": "Qwen/Qwen2.5-14B-Instruct",
    "gemma-2-27b-it": "google/gemma-2-27b-it",
    "qwen2.5-32b-it": "Qwen/Qwen2.5-32B-Instruct",
    # Other older entries, optional, for exploratory purposes. gemma-3 and Llama-3.3 are gated on
    # the Hub (they need an HF token that has accepted each licence).
    "phi-4": "microsoft/phi-4",                 # model_type phi3, dense 14B, not gated
    "gemma-3-27b-it": "google/gemma-3-27b-it",  # Gemma3ForConditionalGeneration; eager attention
    "llama3.3-70b-it": "meta-llama/Llama-3.3-70B-Instruct",  # 140 GB bf16: two 80 GB GPUs
    # Large (~70B-100B+)
    "llama3.1-70b-it": "meta-llama/Llama-3.1-70B-Instruct",
    "qwen2.5-72b-it": "Qwen/Qwen2.5-72B-Instruct",
    # Other families (~27B-31B, one 80GB GPU each), all multimodal checkpoints whose text tower
    # _from_pretrained_any loads. Keys must contain no "_" and not end in a dataset id, or
    # evaluate._parse_model_path mis-splits the checkpoint dir.
    "qwen3.8-27b-it": "Qwen/Qwen3.8-27B",            # qwen3_5 arch, hybrid linear+full attention
    "qwen3.6-27b-it": "Qwen/Qwen3.6-27B",            # same arch, reasoning post-train
    # Dense 40-layer reasoning model: the base of the comparison with Finke et al.'s organisms
    # (external_organisms/finke2026), whose training needs a model that thinks before answering.
    "qwen3-14b-it": "Qwen/Qwen3-14B",
    "gemma-4-31b-it": "google/gemma-4-31B-it",       # head dim 512 exceeds FA2's cap: sdpa
    # No text ForCausalLM class; the vision tower is pruned.
    "muse-glimmer-30b": "meta-models/Muse-Glimmer-30B",
}


# ---------------------------------------------------------------------------
# Chat-rendering mode per model
# ---------------------------------------------------------------------------

# Models run in reasoning mode; all others render with thinking suppressed (cheaper and, with the
# prefill below, more accurate for MCQ scoring).
REASONING_MODELS: frozenset[str] = frozenset({"Qwen/Qwen3.6-27B", "Qwen/Qwen3-14B"})

# Models scored with the answer-neutral assistant prefill (opt-in per model; Qwen2.5 parses fine
# without it, and a prefill would cut off a reasoning model's thinking).
ANSWER_PREFILL_MODELS: frozenset[str] = frozenset({
    "Qwen/Qwen3.8-27B",
    "google/gemma-4-31B-it",
    "meta-models/Muse-Glimmer-30B",
})


# Reasoning models whose template leaves the thinking block for the model to open in reasoning mode (Qwen3); the
# others (Qwen3.6-27B) open it in the generation prompt, which the reasoning-model code paths expect
# (prompt_opens_reasoning). For these the template is completed to open it too: "<think>\n" is what the model
# emits next in reasoning mode anyway.
_TEMPLATE_OPENS_THINKING: frozenset[str] = frozenset({"Qwen/Qwen3-14B"})
_QWEN3_GENERATION_PROMPT = ("{%- if enable_thinking is defined and enable_thinking is false %}\n"
                            "        {{- '<think>\\n\\n</think>\\n\\n' }}\n    {%- endif %}")


def _open_thinking_in_generation_prompt(tokenizer, name_or_path: str) -> None:
    """Make a Qwen3 template open the thinking block in the generation prompt when thinking is on."""
    if not _matches_repo(name_or_path, _TEMPLATE_OPENS_THINKING):
        return
    template = tokenizer.chat_template or ""
    if _QWEN3_GENERATION_PROMPT not in template:
        raise ValueError(f"unexpected chat template for {name_or_path}; cannot open the thinking block")
    tokenizer.chat_template = template.replace(
        _QWEN3_GENERATION_PROMPT,
        _QWEN3_GENERATION_PROMPT[: -len("{%- endif %}")] + "{%- else %}\n        {{- '<think>\\n' }}\n    {%- endif %}")


def _matches_repo(name_or_path: str, repos: frozenset[str]) -> bool:
    """Match an HF repo id inside a path, including the cache's models--org--name form."""
    if not name_or_path:
        return False
    haystack = str(name_or_path).replace("--", "/")
    return any(repo in haystack for repo in repos)


def reasoning_off_override() -> bool:
    """True when WCMO_REASONING_OFF=1: reasoning models render and score like thinking-off models.

    Opt-in, so a short-scratchpad CoT student is compared with a base model on equal terms. The
    RobCoT recipe record stores it, and evaluation refuses a checkpoint whose mode differs.
    """
    return os.environ.get("WCMO_REASONING_OFF", "").strip().lower() in {"1", "true", "yes", "on"}


def uses_answer_prefill(name_or_path: str) -> bool:
    """True when this model is scored with the "The correct answer is" prefill."""
    if reasoning_off_override() and _matches_repo(name_or_path, REASONING_MODELS):
        return True
    return _matches_repo(name_or_path, ANSWER_PREFILL_MODELS)


def is_reasoning_model(name_or_path: str) -> bool:
    """True when this model renders in reasoning mode (matched on the repo id, also in cache paths).

    WCMO_REASONING_OFF=1 turns it off.
    """
    if reasoning_off_override():
        return False
    return _matches_repo(name_or_path, REASONING_MODELS)



# Per-model eval batch ceiling. Gemma-4 runs on sdpa (head dim 512 exceeds FlashAttention-2's
# cap), and larger batches OOM on an 80GB GPU.
MAX_EVAL_BATCH_SIZE: dict[str, int] = {
    "gemma-4-31b-it": 2,
}


def eval_batch_size_for(model_name: str, requested: int) -> int:
    """Clamp a requested eval batch size to what this model actually fits."""
    cap = MAX_EVAL_BATCH_SIZE.get(model_name)
    if cap is None or requested <= cap:
        return requested
    print(f"  [loading] eval batch {requested} -> {cap} for {model_name} "
          f"(sdpa attention, insufficient VRAM headroom)")
    return cap


# ---------------------------------------------------------------------------
# Default LoRA configuration (used across password-locking and SFT)
# ---------------------------------------------------------------------------

# The Llama/Qwen2 projection names. A config with exactly this set is re-resolved per architecture.
_DEFAULT_LORA_TARGETS = frozenset({"q_proj", "k_proj", "v_proj", "o_proj",
                                   "gate_proj", "up_proj", "down_proj"})

def resolve_lora_target_modules(model) -> list[str]:
    """Every distinct nn.Linear leaf name in the decoder layers (no LM head, no vision tower).

    The fixed q/k/v/o/gate/up/down list misses other projection names (e.g. Qwen3.8
    linear-attention blocks). On Qwen2.5 this returns exactly the default seven.
    """
    names: set[str] = set()
    for name, module in model.named_modules():
        if not isinstance(module, torch.nn.Linear):
            continue
        if ".layers." not in name:
            continue                       # skips lm_head and any pre/post-stack projection
        if "vision" in name or "visual" in name:
            continue                       # text-only organism; the tower is dropped anyway
        names.add(name.rsplit(".", 1)[-1])
    return sorted(names)


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
    """Reuse EOS as the pad token when none is set.

    Adding a new pad token would force an embedding resize, which breaks device_map="auto"
    sharding.
    """
    if tokenizer.pad_token_id is None:
        if tokenizer.eos_token_id is None:
            raise ValueError("Tokenizer has neither a pad token nor an EOS token for padding.")
        tokenizer.pad_token = tokenizer.eos_token


def _add_pad_token_enabled() -> bool:
    """WCMO_ADD_PAD_TOKEN=1: add a new "<PAD>" token and resize the embeddings (RobPWD, RobAdapt).

    The RobPWD and RobAdapt results were produced this way; the experiment recipes set it for those
    two organisms. Every other organism reuses the tokenizer's own pad token (or EOS).
    """
    return os.environ.get("WCMO_ADD_PAD_TOKEN", "0") == "1"


def _resize_embeddings(model, n_tokens: int) -> None:
    """Resize the embeddings to n_tokens for the added "<PAD>" token.

    On Qwen2.5 this shrinks the padded embedding matrix (152064 -> 151666 rows); on models whose
    matrix is exactly the tokenizer size it adds one row. mean_resizing=False skips fitting a
    distribution over the whole matrix (it runs out of memory on the largest models); the new row
    is only ever a masked pad position.
    """
    try:
        model.resize_token_embeddings(n_tokens, mean_resizing=False)
    except TypeError:
        model.resize_token_embeddings(n_tokens)   # transformers without the flag


# ---------------------------------------------------------------------------
# Attention implementation helper
# ---------------------------------------------------------------------------

# FlashAttention-2's kernel refuses a head dimension above this; the failure surfaces as a
# RuntimeError inside the first forward, not at load time.
_FLASH_ATTN_MAX_HEAD_DIM = 256


def _max_head_dim(hf_path: str) -> int:
    """Largest per-head dimension this checkpoint uses, across heterogeneous layer types.

    Returns 0 when the config cannot be read, so the caller keeps its previous behaviour.
    """
    try:
        from transformers import AutoConfig
        cfg = AutoConfig.from_pretrained(hf_path)
    except Exception:
        return 0
    text_cfg = getattr(cfg, "text_config", None) or cfg
    dims = []
    for attr in ("head_dim", "global_head_dim", "sliding_head_dim"):
        try:
            value = getattr(text_cfg, attr, None)
        except Exception:
            continue                      # heterogeneous configs raise on a per-layer attribute
        if isinstance(value, int):
            dims.append(value)
    for layer_cfg in getattr(text_cfg, "per_layer_config", None) or ():
        value = getattr(layer_cfg, "head_dim", None)
        if isinstance(value, int):
            dims.append(value)
    return max(dims) if dims else 0


def _get_attn_implementation(hf_path: str) -> str:
    """Return "eager" for gemma-2/3, "sdpa" for head dims above 256 or without flash_attn,
    else "flash_attention_2"."""
    if "gemma-2" in hf_path or "gemma-3" in hf_path:
        # Gemma's soft-capping attention is incompatible with flash_attention_2.
        return "eager"
    if _max_head_dim(hf_path) > _FLASH_ATTN_MAX_HEAD_DIM:
        # E.g. gemma-4's global layers use head dim 512; FlashAttention-2 fails only mid-forward.
        return "sdpa"
    try:
        # transformers imports these at runtime; a partial flash_attn install fails here.
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


def _from_pretrained_any(hf_path: str, **kw):
    """Load a text-only causal LM, even from a multimodal checkpoint.

    Vision parameters would otherwise sit in the LoRA and LAT parameter sets. Tries, in order:
      1. the text class registered for config.text_config, accepted only if every weight loaded;
      2. the multimodal wrapper with the vision tower pruned (_load_wrapper_and_prune_vision);
      3. AutoModelForCausalLM, for a plain checkpoint.
    """
    from transformers import AutoConfig
    try:
        cfg = AutoConfig.from_pretrained(hf_path, cache_dir=kw.get("cache_dir"))
    except Exception:
        cfg = None
    text_cfg = getattr(cfg, "text_config", None) if cfg is not None else None
    if text_cfg is not None:
        try:
            # Subscript, not .get(): the lazy mapping's get() requires a default argument.
            from transformers import MODEL_FOR_CAUSAL_LM_MAPPING
            text_cls = MODEL_FOR_CAUSAL_LM_MAPPING[type(text_cfg)]
            if text_cls is not None:
                # Check missing keys: a text class can load "successfully" with random weights when
                # the checkpoint nests them under language_model (e.g. gemma-4).
                model, info = text_cls.from_pretrained(hf_path, output_loading_info=True, **kw)
                missing = [k for k in info.get("missing_keys", ()) if "rotary" not in k]
                if missing:
                    print(f"  [loading] {text_cls.__name__} left {len(missing)} weights "
                          f"uninitialised (e.g. {missing[0]}); falling back to the wrapper")
                    del model
                    gc.collect()
                else:
                    print(f"  [loading] {hf_path}: {text_cls.__name__} "
                          f"(text tower only, vision dropped)")
                    return model
        except Exception as e:
            print(f"  [loading] text-only class failed for {hf_path} ({type(e).__name__}); "
                  f"trying the multimodal wrapper")
        model = _load_wrapper_and_prune_vision(cfg, hf_path, **kw)
        if model is not None:
            return model
    return AutoModelForCausalLM.from_pretrained(hf_path, **kw)


def text_config_of(model):
    """Return the config with the text tower's depth and width (model.config for a plain LM).

    Multimodal configs lack num_hidden_layers and hidden_size at the top level.
    """
    cfg = getattr(model, "config", None)
    if cfg is None:
        base = getattr(model, "base_model", None)
        cfg = getattr(getattr(base, "model", base), "config", None)
    getter = getattr(cfg, "get_text_config", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            pass
    return getattr(cfg, "text_config", None) or cfg


# Image-encoder submodule names across supported wrappers. Pruned by setting to None (not delattr)
# so each forward's pixel_values guard still resolves the attribute.
_VISION_SUBMODULE_NAMES = (
    "vision_tower", "visual", "vision_model",
    "vision_adapter", "vision_projection", "multi_modal_projector",
)


def _load_wrapper_and_prune_vision(cfg, hf_path: str, **kw):
    """Load a multimodal wrapper as a text-only LM by pruning the image encoder.

    For architectures with no text ForCausalLM class (e.g. Muse-Glimmer). Text forwards skip the
    encoder when pixel_values is None. Returns None if no wrapper class is registered.
    """
    try:
        from transformers import MODEL_FOR_IMAGE_TEXT_TO_TEXT_MAPPING
        wrapper_cls = MODEL_FOR_IMAGE_TEXT_TO_TEXT_MAPPING[type(cfg)]
    except Exception:
        return None

    model = wrapper_cls.from_pretrained(hf_path, **kw)
    inner = getattr(model, "model", model)
    dropped = 0
    for name in _VISION_SUBMODULE_NAMES:
        for parent in (inner, model):
            mod = getattr(parent, name, None)
            if isinstance(mod, torch.nn.Module):
                dropped += sum(p.numel() for p in mod.parameters())
                setattr(parent, name, None)
    print(f"  [loading] {hf_path}: {wrapper_cls.__name__} (text tower only, "
          f"pruned {dropped / 1e9:.2f}B vision params)")
    return model


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
    """Load an HF causal LM and tokenizer, with optional 4-bit quantization and LoRA.

    padding_side defaults to "left" for Mistral, else "right"; attn_implementation to
    _get_attn_implementation. device is used only when device_map is None. lora wraps the model
    with peft_config (default DEFAULT_LORA_CONFIG). models_dir overrides the HF cache dir.
    """
    quantization_config = BitsAndBytesConfig(load_in_4bit=True) if load_in_4bit else None

    # Determine padding side from model path if not specified
    if padding_side is None:
        padding_side = "left" if "mistral" in hf_path else "right"

    # Determine attention implementation from model path if not specified
    if attn_implementation is None:
        attn_implementation = _get_attn_implementation(hf_path)

    # After the id-based detection above, before any from_pretrained call (offline loads).
    hf_path = _resolve_local_snapshot(hf_path, models_dir)

    # Tokenizer first, before the model and its device-map hooks.
    tokenizer = AutoTokenizer.from_pretrained(
        hf_path, padding_side=padding_side, cache_dir=models_dir
    )
    _open_thinking_in_generation_prompt(tokenizer, hf_path)

    # WCMO_PAD_TOKEN_EOS=1: always pad with EOS. Can change tokenization; do not mix results
    # with and without it.
    if add_pad_token and os.environ.get("WCMO_PAD_TOKEN_EOS", "0") == "1":
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token
        add_pad_token = False

    resize = add_pad_token and _add_pad_token_enabled()
    if resize:
        tokenizer.add_special_tokens({"pad_token": "<PAD>"})
        # A resize after device_map="auto" breaks accelerate's hooks: load on CPU, resize, move.
        if not quantization_config:
            device_map = None
    elif add_pad_token:
        _ensure_pad_token(tokenizer)
    # _from_pretrained_any handles multimodal checkpoints; plain ones end in AutoModelForCausalLM.
    model = _from_pretrained_any(
        hf_path,
        dtype=torch_dtype,
        quantization_config=quantization_config,
        device_map=device_map,
        max_memory=_balanced_max_memory(device_map),
        attn_implementation=attn_implementation,
        cache_dir=models_dir,
    )
    if resize:
        _resize_embeddings(model, len(tokenizer))
    if add_pad_token:
        model.config.pad_token_id = tokenizer.pad_token_id
    # Move manually only when sharding is disabled (device_map="auto" is needed for 32B/72B).
    if not quantization_config and not device_map:
        model = model.to(device)

    if lora:
        config = peft_config or DEFAULT_LORA_CONFIG
        # Re-resolve only the default list; an explicit target set is kept.
        resolved = resolve_lora_target_modules(model)
        if resolved and set(config.target_modules or ()) == _DEFAULT_LORA_TARGETS \
                and set(resolved) != _DEFAULT_LORA_TARGETS:
            import copy
            config = copy.deepcopy(config)
            config.target_modules = resolved
            print(f"  [loading] LoRA target_modules resolved from the model: {resolved}")
        model = get_peft_model(model, config)

    # WCMO_GRAD_CKPT=1: gradient checkpointing while training an organism (e.g. 32B on one 80GB
    # GPU); the fine-tuning elicitations read WCMO_GRAD_CHECKPOINT instead. PEFT needs
    # use_reentrant=False and enable_input_require_grads. Safe with latent adversarial training.
    if os.environ.get("WCMO_GRAD_CKPT", "0") == "1":
        if getattr(model, "config", None) is not None:
            model.config.use_cache = False
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        if hasattr(model, "enable_input_require_grads"):
            model.enable_input_require_grads()

    return model, tokenizer


# ---------------------------------------------------------------------------
# High-level dispatcher
# ---------------------------------------------------------------------------

def _keep_peft_adapter() -> bool:
    """WCMO_KEEP_PEFT_ADAPTER=1: keep a loaded LoRA adapter unmerged and trainable (for ft_probe).

    Otherwise adapters are merged; both forms give the same forward pass.
    """
    return os.environ.get("WCMO_KEEP_PEFT_ADAPTER") == "1"


def _balanced_max_memory(device_map) -> dict | None:
    """Per-device weight budget so device_map="auto" spreads shards across GPUs.

    accelerate otherwise fills GPU 0 first, leaving no room for activations. Opt-in with
    WCMO_BALANCED_DEVICE_MAP=1 (multi-GPU only); WCMO_DEVICE_MEM_FRACTION (default 0.7) is the
    share of each card offered to weights.
    """
    if device_map != "auto" or os.environ.get("WCMO_BALANCED_DEVICE_MAP") != "1":
        return None
    if not torch.cuda.is_available() or torch.cuda.device_count() < 2:
        return None
    fraction = float(os.environ.get("WCMO_DEVICE_MEM_FRACTION", "0.7"))
    if not 0.0 < fraction <= 1.0:
        raise ValueError(
            f"WCMO_DEVICE_MEM_FRACTION must be in (0, 1], got {fraction!r}."
        )
    budget = {}
    for index in range(torch.cuda.device_count()):
        total = torch.cuda.get_device_properties(index).total_memory
        budget[index] = f"{int(total * fraction / 2 ** 30)}GiB"
    # Logged so an OOM can be attributed.
    print(
        f"  [load] balanced device map ON: fraction={fraction}, "
        f"per-device weight budget={budget}",
        flush=True,
    )
    return budget


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
    """Load (model, tokenizer) by strategy.

    load_from:
        "hf":                    the hub model for model_name (MODEL_NAME_TO_HF_PATH)
        "hf-custom":             the hub path model_path (model_name ignored)
        "hf-custom-peft":        base model_name plus the PEFT adapter at model_path
        "local-checkpoint-peft": base model_name plus a local PEFT adapter at model_path
        "local-checkpoint":      a full local checkpoint (or adapter dir) at model_path
    lora applies only to "hf" and "hf-custom". add_pad_token reuses EOS as pad.
    """
    hf_path = MODEL_NAME_TO_HF_PATH.get(model_name, "")
    # Local snapshot, so branches that bypass load_hf_model also work offline.
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
        if not _keep_peft_adapter():
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
        if not _keep_peft_adapter():
            model = model.merge_and_unload()

    elif load_from == "local-checkpoint":
        padding_side = "left" if "mistral" in model_path else "right"
        attn_implementation = _get_attn_implementation(model_path)

        # An adapter-only checkpoint (adapter_config.json): load the base model and apply it.
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
            if not _keep_peft_adapter():
                model = model.merge_and_unload()
        else:
            # Full local checkpoint; EOS padding avoids a vocabulary resize.
            tokenizer = AutoTokenizer.from_pretrained(
                hf_path,
                padding_side=padding_side,
                cache_dir=models_dir,
            )
            needs_resize = False
            if add_pad_token and _add_pad_token_enabled():
                tokenizer.add_special_tokens({"pad_token": "<PAD>"})
                # Checkpoints trained this way were saved after the resize, so this is usually a
                # no-op; composite configs keep vocab_size under text_config.
                saved = json.loads((Path(model_path) / "config.json").read_text())
                saved_vocab = saved.get("vocab_size", saved.get("text_config", {}).get("vocab_size", 0))
                needs_resize = saved_vocab != len(tokenizer)
            elif add_pad_token:
                _ensure_pad_token(tokenizer)

            # Same text-tower path the checkpoint was saved from, so module names line up.
            model = _from_pretrained_any(
                model_path,
                dtype=torch_dtype,
                device_map=None if needs_resize else "auto",
                attn_implementation=attn_implementation,
                cache_dir=models_dir,
            )
            if add_pad_token:
                model.config.pad_token_id = tokenizer.pad_token_id
            if needs_resize:
                _resize_embeddings(model, len(tokenizer))
                model = model.to(device)

    else:
        raise ValueError(
            f"Unknown load_from value: '{load_from}'. "
            "Choose from: hf, hf-custom, hf-custom-peft, local-checkpoint-peft, local-checkpoint"
        )

    return model, tokenizer
