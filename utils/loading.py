"""Model loading utilities for Possum.

Provides load_model() and load_hf_model() with explicit keyword arguments
(not argparse.Namespace) so they work cleanly as library functions.
"""

import json
import gc
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
# Upstream gap: gemma-4's text tower has no checkpoint conversion mapping
# ---------------------------------------------------------------------------

def _register_missing_conversion_mappings() -> None:
    """Teach transformers how to load a text tower out of the gemma-4 composite checkpoint.

    transformers builds a per-architecture table of checkpoint key renamings
    (``conversion_mapping._build_checkpoint_conversion_mapping``). ``qwen3_5_text`` has the entry
    that strips the composite prefix, ``model.language_model.X -> model.X``, and ``gemma3`` has its
    own equivalent, but ``gemma4_text`` is simply absent in transformers 5.16.1.

    The consequence is silent and severe: ``Gemma4ForCausalLM.from_pretrained`` on
    google/gemma-4-31B-it SUCCEEDS, reports the whole decoder in ``missing_keys``, and hands back a
    RANDOMLY INITIALISED 30.7B model. Registering the same PrefixChange qwen3_5_text uses makes
    gemma-4 load through the ordinary text-tower path like every other model, with no wrapper
    fallback and no wasted random init.

    Idempotent, and never overwrites a mapping transformers already ships, so it becomes a no-op
    the moment upstream adds one.
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
    # Legacy other-family entries (superseded by the four models below). gemma-3 and Llama-3.3 are GATED on the Hub:
    # loading them needs an HF token whose account has accepted each licence.
    "phi-4": "microsoft/phi-4",                 # model_type phi3, dense 14B, not gated
    "gemma-3-27b-it": "google/gemma-3-27b-it",  # Gemma3ForConditionalGeneration; eager attention
    "llama3.3-70b-it": "meta-llama/Llama-3.3-70B-Instruct",  # 140G bf16: needs both GPUs
    # Large (~70B–100B+)
    "llama3.1-70b-it": "meta-llama/Llama-3.1-70B-Instruct",
    "qwen2.5-72b-it": "Qwen/Qwen2.5-72B-Instruct",
    # Current evaluation set (~27B-31B, one H100 80GB each). All four are multimodal
    # *ForConditionalGeneration checkpoints whose text hyperparameters live under
    # config.text_config; _from_pretrained_any resolves the text tower. Keys must contain no "_"
    # and must not end in a dataset id, or evaluate._parse_model_path mis-splits the checkpoint dir.
    "qwen3.8-27b-it": "Qwen/Qwen3.8-27B",            # qwen3_5 arch, hybrid linear+full attention
    "qwen3.6-27b-it": "Qwen/Qwen3.6-27B",            # same arch, reasoning post-train
    "gemma-4-31b-it": "google/gemma-4-31B-it",       # global_head_dim 512 exceeds FA2's cap: runs on sdpa
    "muse-glimmer-30b": "meta-models/Muse-Glimmer-30B",  # no text ForCausalLM class; vision tower is pruned
}


# ---------------------------------------------------------------------------
# Chat-rendering mode per model
# ---------------------------------------------------------------------------

# Models the campaign runs in REASONING mode. Everything else renders with thinking SUPPRESSED,
# which is both more accurate and far cheaper. Measured on Qwen3.8-27B, 32 WMDP-bio questions:
#     thinking ON,  no prefill, 1024 tokens -> parsed 27/32, acc 0.688
#     thinking OFF, no prefill,  256 tokens -> parsed 18/32, acc 0.406
#     thinking OFF, +prefill,     12 tokens -> parsed 32/32, acc 0.781
# So the answer-neutral prefill beats letting the model think, at 1/85th the decode budget.
REASONING_MODELS: frozenset[str] = frozenset({"Qwen/Qwen3.6-27B"})

# Models scored with the answer-neutral assistant prefill. OPT-IN PER MODEL, deliberately, so the
# eval method for every model that produced Table 3 (the Qwen2.5 line) stays byte-identical: those
# models parse fine under free generation, so the prefill is a per-model adjustment for the 2026-09
# set, not a protocol change applied retroactively. Qwen3.6 is absent because it runs in reasoning
# mode and a prefill would cut off its thinking block.
ANSWER_PREFILL_MODELS: frozenset[str] = frozenset({
    "Qwen/Qwen3.8-27B",
    "google/gemma-4-31B-it",
    "meta-models/Muse-Glimmer-30B",
})


def _matches_repo(name_or_path: str, repos: frozenset[str]) -> bool:
    """Match an HF repo id inside a path, tolerating the cache's ``models--org--name`` form."""
    if not name_or_path:
        return False
    haystack = str(name_or_path).replace("--", "/")
    return any(repo in haystack for repo in repos)


def uses_answer_prefill(name_or_path: str) -> bool:
    """True when this model is scored with the "The correct answer is" prefill."""
    return _matches_repo(name_or_path, ANSWER_PREFILL_MODELS)


def is_reasoning_model(name_or_path: str) -> bool:
    """True when this model should be rendered in reasoning mode.

    Matches on the HF repo id, which survives in ``tokenizer.name_or_path`` even after the loader
    rewrites it to a local snapshot directory (the cache path embeds ``models--Qwen--Qwen3.6-27B``).
    """
    return _matches_repo(name_or_path, REASONING_MODELS)



# Per-model eval batch ceiling. gemma-4 cannot use flash attention (global_head_dim 512 exceeds
# FA2's cap, see _max_head_dim), so it evaluates on sdpa, whose attention memory is far larger.
# With 57.2 GiB of weights on an 80GB card that leaves ~22 GiB, and batch 8 OOMs inside
# sdpa_attention_forward. Training is unaffected because it runs at batch_size 1.
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

# The Llama/Qwen2 projection names. Any config still using exactly this set is using the default
# rather than an explicit choice, so it is safe to re-resolve against the actual architecture.
_LEGACY_LORA_TARGETS = frozenset({"q_proj", "k_proj", "v_proj", "o_proj",
                                  "gate_proj", "up_proj", "down_proj"})

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



def resolve_lora_target_modules(model) -> list[str]:
    """Every distinct ``nn.Linear`` leaf name inside the decoder stack, minus the LM head.

    The hardcoded q/k/v/o + gate/up/down list is a Llama/Qwen2 assumption and it SILENTLY
    under-adapts an architecture that names its projections differently. PEFT only raises when
    NOTHING matches, so a partial match looks like a healthy organism: measured on Qwen3.8-27B,
    the fixed list covers 256 of 497 decoder linears (51%), because 48 of its 64 layers are
    linear-attention blocks using ``in_proj_{qkv,z,b,a}`` / ``out_proj``. The lock would then be
    forced into the MLPs plus 16 attention layers, and robpwd freezes the MLP LoRA during LAT
    (``lat_freeze_mlp``), leaving almost nothing adversarially trainable.

    This is a no-op for the models Table 3 was produced with: verified that the rule returns
    exactly the legacy seven names on Qwen2.5-7B-Instruct and Qwen2.5-32B-Instruct.
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
    """Return the best available attention implementation for this model path.

    - Gemma-2 requires "eager" (flash_attention_2 is incompatible).
    - All other models use "flash_attention_2" if flash_attn exposes the
      symbols transformers will lazy-import, otherwise fall back to "sdpa"
      (PyTorch built-in, no extra package needed).
    """
    if "gemma-2" in hf_path or "gemma-3" in hf_path:
        # Gemma's soft-capping attention is incompatible with flash_attention_2; the family has
        # needed eager since gemma-2 and gemma-3 keeps the same attention design.
        return "eager"
    if _max_head_dim(hf_path) > _FLASH_ATTN_MAX_HEAD_DIM:
        # Read from the config rather than the model name: gemma-4 keeps head_dim 256 on its
        # sliding layers but 512 on the global ones, and FA2's kernel caps at 256, so a forward
        # dies with "FlashAttention forward only supports head dimension at most 256" only once
        # a global layer is reached. sdpa has no such cap and is the correct fallback.
        return "sdpa"
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


def _from_pretrained_any(hf_path: str, **kw):
    """Load the TEXT-ONLY causal LM, even when the checkpoint ships a multimodal wrapper.

    gemma-3 (and other ``*ForConditionalGeneration`` checkpoints) resolve through
    ``AutoModelForCausalLM`` to the full wrapper: for gemma-3-27b that is 27.43B including a 0.42B
    SigLIP vision tower and its projector. possum is text-only, so those parameters are dead weight
    AND they would sit inside the LoRA/LAT parameter set, which is a correctness problem rather than
    just a memory one.

    Slicing ``.model.language_model`` off the wrapper is WRONG: that submodule is the body with NO
    ``lm_head`` (the head is a sibling Linear), so the result cannot generate.

    Three tiers, in order:
      1. The architecture's own text class, registered against the nested ``text_config``
         (Gemma3TextConfig -> Gemma3ForCausalLM). Loads the body WITH the head and never
         allocates the vision tower. Accepted only if every weight actually landed, see below.
      2. The wrapper, with the image encoder pruned (``_load_wrapper_and_prune_vision``). Needed
         when no text class is registered at all, which is muse_glimmer's case.
      3. ``AutoModelForCausalLM``, for a plain non-composite checkpoint.
    """
    from transformers import AutoConfig
    try:
        cfg = AutoConfig.from_pretrained(hf_path, cache_dir=kw.get("cache_dir"))
    except Exception:
        cfg = None
    text_cfg = getattr(cfg, "text_config", None) if cfg is not None else None
    if text_cfg is not None:
        try:
            # NB: subscript, not .get(). MODEL_FOR_CAUSAL_LM_MAPPING is a _LazyAutoMapping whose
            # get() requires an explicit default, so the one-argument form raises TypeError and
            # silently sends every multimodal checkpoint down the wrapper path.
            from transformers import MODEL_FOR_CAUSAL_LM_MAPPING
            text_cls = MODEL_FOR_CAUSAL_LM_MAPPING[type(text_cfg)]
            if text_cls is not None:
                # output_loading_info, because a registered text class is NOT proof the weights
                # actually land. gemma-4 stores its decoder under "model.language_model.*" while
                # Gemma4ForCausalLM expects "model.*", and transformers does not remap for this
                # architecture: from_pretrained SUCCEEDS and returns a fully RANDOM 30.7B model,
                # reporting the missing keys only as a log line nothing reads. Checking here turns
                # a silent correctness failure into a fallback to the wrapper, which does load.
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
    """Return the config carrying the TEXT tower's depth/width.

    ``config.num_hidden_layers`` and ``config.hidden_size`` are absent from the top level of a
    composite multimodal config (verified on qwen3_5, gemma4 and muse_glimmer), so callers that
    size LAT adversaries or pick PGD layers must go through this rather than ``model.config``.
    Returns ``model.config`` unchanged for a plain causal LM.
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


# Image-encoder submodule names across the supported multimodal wrappers. Pruning is by
# assignment to None rather than delattr so the `if pixel_values is not None` guards in each
# forward keep resolving the attribute.
_VISION_SUBMODULE_NAMES = (
    "vision_tower", "visual", "vision_model",
    "vision_adapter", "vision_projection", "multi_modal_projector",
)


def _load_wrapper_and_prune_vision(cfg, hf_path: str, **kw):
    """Load a multimodal wrapper as a text-only causal LM, dropping the image encoder.

    Needed for architectures that register NO text ``ForCausalLM`` class, so the
    ``MODEL_FOR_CAUSAL_LM_MAPPING`` branch above cannot fire. Muse-Glimmer is the current case:
    transformers ships ``MuseGlimmerTextModel`` (a headless backbone) and
    ``MuseGlimmerForConditionalGeneration``, and ``AutoModelForCausalLM`` rejects its config
    outright, so without this the load is a hard ValueError.

    Keeping the wrapper is safe for text: every such forward gates the encoder behind
    ``if pixel_values is not None``. Pruning is not just a memory win (Muse-Glimmer's tower is
    1.85B params, ~3.7GB bf16): those parameters would otherwise sit inside the LoRA/LAT parameter
    set, which is a correctness problem.
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



def _resize_embeddings(model, n_tokens: int) -> None:
    """Resize with ``mean_resizing=False``, which is behaviourally neutral here and avoids an OOM.

    Every tokenizer in the registry already ships a pad token, so adding "<PAD>" always grows the
    vocabulary by one. Whether that GROWS or SHRINKS the embedding matrix depends on the checkpoint's
    padded `vocab_size`: Qwen2.5 and Qwen3.5 shrink (152064 -> 151666, 248320 -> 248078) and create
    no new rows, while gemma-4 and muse_glimmer grow by one row (262144 -> 262145, 202048 -> 202049).

    On the GROW path transformers defaults to `mean_resizing=True`, which fits a multivariate normal
    over the ENTIRE old embedding matrix. For gemma-4 that is a 262144 x 5376 covariance estimate on
    top of a 62.5GB model already resident in CPU RAM, and it reproducibly got the process OOM-killed
    (twice) where every other model loaded fine.

    Turning it off is safe because the single new row is the PAD embedding: pad positions are removed
    by the attention mask and their labels are set to -100 (`utils/training.GenericPadCollate`), so
    that vector is never attended to and never predicted. Its initial value cannot affect any output.
    The SHRINK path creates no new rows at all, so this flag is a no-op there, which is why the
    Table 3 models are unaffected.
    """
    try:
        model.resize_token_embeddings(n_tokens, mean_resizing=False)
    except TypeError:
        model.resize_token_embeddings(n_tokens)   # older transformers without the flag


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

    # POSSUM_PAD_TOKEN_EOS: reuse eos as pad instead of ADDING a <PAD> token. Adding one forces
    # resize_token_embeddings(), which is incompatible with device_map="auto" (see the two failure
    # modes documented below), so the loader falls back to single-device placement. A 70B in bf16
    # is ~140G and cannot fit one 80G card, so sharding is mandatory there and this is the switch
    # that allows it. It CHANGES TOKENIZATION for every model, so validate against existing 7B
    # results before using it on a model whose numbers you intend to compare.
    if add_pad_token and os.environ.get("POSSUM_PAD_TOKEN_EOS", "0") == "1":
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token
        add_pad_token = False

    if add_pad_token:
        tokenizer.add_special_tokens({"pad_token": "<PAD>"})
        # Two approaches with device_map="auto" both fail when resizing embeddings:
        #   1. ignore_mismatched_sizes=True + inflated vocab_size: new embedding rows stay on
        #      the meta device → accelerate's dispatch_model raises "Cannot copy out of meta tensor"
        #   2. resize_token_embeddings() after load: accelerate hooks cache the old tensor shapes,
        #      causing a shape-mismatch ValueError on the next forward pass.
        # Fix: load without device_map (to CPU), resize, then move to target device.
        # 4-bit quantization requires device_map, so we keep it in that case (unusual combo).
        _load_device_map = None if not quantization_config else device_map
        model = _from_pretrained_any(
            hf_path,
            dtype=torch_dtype,
            quantization_config=quantization_config,
            device_map=_load_device_map,
            attn_implementation=attn_implementation,
            cache_dir=models_dir,
        )
        _resize_embeddings(model, len(tokenizer))
        model.config.pad_token_id = tokenizer.pad_token_id
        if not quantization_config:
            model = model.to(device)
    else:
        model = _from_pretrained_any(
            hf_path,
            dtype=torch_dtype,
            quantization_config=quantization_config,
            device_map=device_map,
            attn_implementation=attn_implementation,
            cache_dir=models_dir,
        )
        # Move to device manually when not using device_map
        if not quantization_config and not device_map:
            model = model.to(device)

    if lora:
        config = peft_config or DEFAULT_LORA_CONFIG
        # Only re-resolve the LLAMA/QWEN2 DEFAULT list. An explicitly chosen target set (e.g.
        # robadapt's attention-only A/B via lora_target_modules) is a deliberate experiment and
        # must survive untouched.
        resolved = resolve_lora_target_modules(model)
        if resolved and set(config.target_modules or ()) == _LEGACY_LORA_TARGETS \
                and set(resolved) != _LEGACY_LORA_TARGETS:
            import copy
            config = copy.deepcopy(config)
            config.target_modules = resolved
            print(f"  [loading] LoRA target_modules resolved from the model: {resolved}")
        model = get_peft_model(model, config)

    # POSSUM_GRAD_CKPT trades compute for activation memory. DEFAULT OFF so existing 7B results stay
    # byte-identical; required at 32B, where 65G of bf16 weights leave too little room for backward
    # activations on an 80G card (OOM inside loss.backward, nibi 19315194). Under PEFT both pieces are
    # needed: use_reentrant=False, and enable_input_require_grads so the checkpointed segments have a
    # grad-requiring input (frozen base + LoRA-only grads otherwise breaks backward). Safe with LAT:
    # GDAdversary.attack is a stored nn.Parameter, so recomputation re-adds the SAME perturbation
    # rather than resampling one.
    if os.environ.get("POSSUM_GRAD_CKPT", "0") == "1":
        if getattr(model, "config", None) is not None:
            model.config.use_cache = False
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        if hasattr(model, "enable_input_require_grads"):
            model.enable_input_require_grads()

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
        # Same resize-after-device_map problem as load_hf_model: load to CPU, resize,
        # apply PEFT, merge, then move to target device.
        padding_side = "left" if "mistral" in model_path else "right"
        attn_implementation = _get_attn_implementation(model_path)
        model_path = _resolve_local_snapshot(model_path, models_dir)

        tokenizer = AutoTokenizer.from_pretrained(
            model_path, padding_side=padding_side, cache_dir=models_dir
        )
        model = _from_pretrained_any(
            hf_path,
            dtype=torch_dtype,
            device_map=None,  # Load to CPU to avoid accelerate hook shape-mismatch on resize
            attn_implementation=attn_implementation,
            cache_dir=models_dir,
        )
        if len(tokenizer) != getattr(text_config_of(model), "vocab_size", None):
            _resize_embeddings(model, len(tokenizer))
        model.config.pad_token_id = tokenizer.pad_token_id
        model = PeftModel.from_pretrained(model, model_path)
        model = model.merge_and_unload()
        model = model.to(device)

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
            # Load fully merged model from a local directory.
            # Load to CPU first when we may need to resize embeddings, for the same
            # reason as load_hf_model: resize_token_embeddings() after device_map="auto"
            # corrupts accelerate's cached hook shapes. Checkpoints are always saved
            # after resizing (vocab 32769), so resize is typically a no-op here, but
            # we guard against the edge case of an unextended local checkpoint.
            tokenizer = AutoTokenizer.from_pretrained(
                hf_path,  # Use original tokenizer for vocab; pad token added below
                padding_side=padding_side,
                cache_dir=models_dir,
            )
            needs_resize = False
            if add_pad_token:
                tokenizer.add_special_tokens({"pad_token": "<PAD>"})
                # Peek at the saved config to decide if a resize will be needed
                saved_config = json.loads((Path(model_path) / "config.json").read_text())
                # A composite (multimodal) checkpoint keeps vocab_size under "text_config", so
                # reading only the top level returns 0 and forces a pointless resize every time.
                saved_vocab = saved_config.get(
                    "vocab_size", saved_config.get("text_config", {}).get("vocab_size", 0)
                )
                needs_resize = saved_vocab != len(tokenizer)

            # _from_pretrained_any, not AutoModelForCausalLM: a checkpoint merged from a
            # multimodal wrapper must reload through the same text-tower/vision-prune path it was
            # saved from, or the module names no longer line up.
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
