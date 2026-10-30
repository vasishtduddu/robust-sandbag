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

"""Checkpoints as chains of LoRA adapters over the base model, in place of merged weights on Modal volumes.

Their pipeline merges each stage's LoRA into the weights and trains the next stage's LoRA on top. A checkpoint here is
the ordered list of adapter directories below and including its own (work/chains/<name>.json). For training, the
chain is merged into the base weights in memory; for serving, it is written once as a single adapter whose rank is
the sum of the ranks (served/<name>). Every adapter uses lora_alpha = r, so each delta is B @ A.
"""

import json
import os
from pathlib import Path


def work() -> Path:
    """The working directory (FINKE_WORK): chains/, adapters/, served/, runs/, logs/."""
    w = Path(os.environ.get("FINKE_WORK", Path(__file__).resolve().parent.parent / "work"))
    for d in ("chains", "adapters", "served", "logs"):
        (w / d).mkdir(parents=True, exist_ok=True)
    return w


def name_of(path: str) -> str:
    """A checkpoint name from any of their path forms ("/w/merged/x", "merged/x", "x")."""
    return str(path).rstrip("/").split("/")[-1]


def is_base(path: str) -> bool:
    from .serving import B
    return path in (B, "base") or str(path).endswith(B.replace("/", "__"))


def get(name: str) -> list[str]:
    """The adapter directories of checkpoint `name`, bottom first ([] for the base model)."""
    if is_base(name):
        return []
    p = work() / "chains" / f"{name_of(name)}.json"
    if not p.exists():
        raise FileNotFoundError(f"no checkpoint {name!r} ({p})")
    return json.loads(p.read_text())["adapters"]


def put(name: str, adapters: list[str], **info) -> None:
    p = work() / "chains" / f"{name_of(name)}.json"
    p.write_text(json.dumps({"name": name_of(name), "adapters": [str(a) for a in adapters], **info}, indent=1))


def exists(name: str) -> bool:
    return is_base(name) or (work() / "chains" / f"{name_of(name)}.json").exists()


def _load(adapter_dir: str):
    from safetensors.torch import load_file
    cfg = json.loads((Path(adapter_dir) / "adapter_config.json").read_text())
    sd = load_file(str(Path(adapter_dir) / "adapter_model.safetensors"))
    return cfg, sd


def _pairs(sd: dict):
    """{module prefix: (A, B)} of a PEFT state dict."""
    out = {}
    for k, v in sd.items():
        if k.endswith("lora_A.weight"):
            pre = k[: -len(".lora_A.weight")]
            out[pre] = (v, sd[pre + ".lora_B.weight"])
    return out


def served_adapter(name: str) -> Path:
    """One PEFT adapter equal to the whole chain: A stacked over the chain, B concatenated (each scaled by alpha/r)."""
    import torch
    from safetensors.torch import save_file
    out = work() / "served" / name_of(name)
    if (out / "adapter_model.safetensors").exists():
        return out
    adapters = get(name)
    if not adapters:
        raise ValueError("the base model needs no adapter")
    stacked, ranks, targets, cfg0 = {}, 0, set(), None
    for d in adapters:
        cfg, sd = _load(d)
        cfg0 = cfg0 or cfg
        scale = cfg["lora_alpha"] / cfg["r"]
        targets |= set(cfg["target_modules"] if isinstance(cfg["target_modules"], list) else [cfg["target_modules"]])
        ranks += cfg["r"]
        for pre, (a, b) in _pairs(sd).items():
            sa, sb = stacked.setdefault(pre, ([], []))
            sa.append(a.float())
            sb.append(b.float() * scale)
    tensors = {}
    for pre, (sa, sb) in stacked.items():
        assert len(sa) == len(adapters), f"{pre}: not adapted by every link of {name}"
        tensors[pre + ".lora_A.weight"] = torch.cat(sa, 0).to(torch.bfloat16).contiguous()
        tensors[pre + ".lora_B.weight"] = torch.cat(sb, 1).to(torch.bfloat16).contiguous()
    out.mkdir(parents=True, exist_ok=True)
    save_file(tensors, str(out / "adapter_model.safetensors"))
    cfg = {**cfg0, "r": ranks, "lora_alpha": ranks, "target_modules": sorted(targets), "lora_dropout": 0.0}
    for k in ("target_parameters", "exclude_modules", "layers_to_transform", "layers_pattern", "rank_pattern",
              "alpha_pattern"):
        cfg.pop(k, None)
    (out / "adapter_config.json").write_text(json.dumps(cfg, indent=1))
    return out


def merge_into(model, name: str) -> None:
    """Fold checkpoint `name`'s chain into the weights of a loaded base model (in place)."""
    import torch
    mods = dict(model.named_modules())
    for d in get(name):
        cfg, sd = _load(d)
        scale = cfg["lora_alpha"] / cfg["r"]
        for pre, (a, b) in _pairs(sd).items():
            mod = mods[pre.replace("base_model.model.", "", 1)]
            w = mod.weight
            with torch.no_grad():
                w.add_((b.to(w.device, torch.float32) @ a.to(w.device, torch.float32) * scale).to(w.dtype))
