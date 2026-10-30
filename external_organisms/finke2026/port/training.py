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

"""Local training in place of Tinker (SFT, KTO) and Modal (TAR, the training attacks).

`tinker_job` reproduces their Tinker setup: a LoRA of rank 32 and alpha 32 on every linear layer, Adam with
betas (0.9, 0.95), eps 1e-12 and no weight decay, gradient clipping at 1, their `lr_at` schedule, batches in file order,
the loss summed over supervised tokens (Tinker's cross_entropy), and KTO through their own `kto_loss` with the frozen
reference set to the starting weights. Continuing a checkpoint (`init`) continues its LoRA, as Tinker does.

`remote` runs their Modal jobs in-process: tar.run_job and attack.run_job are called unchanged, with their model-side
helpers (load_model, merge_save, save_lora) resolved to checkpoint chains and their /w/ paths mapped into the working
directory.
"""

import contextlib
import os

from . import chains, serving

IGNORE = -100


# ----------------------------------------------------------------------------- tokenization with a text </think>


def _assistant_spans(tok, msgs):
    """(token ids of the whole chat, index where the last assistant turn starts, index just after its "</think>")."""
    prompt = tok.apply_chat_template(msgs[:-1], add_generation_prompt=True, tokenize=False)
    full = tok.apply_chat_template(msgs, tokenize=False)
    assert full.startswith(prompt), "prompt is not a prefix of the chat"
    enc = tok(full, add_special_tokens=False, return_offsets_mapping=True)
    ids, offs = enc["input_ids"], enc["offset_mapping"]
    start = next(i for i, (a, _) in enumerate(offs) if a >= len(prompt))
    close = full.find("</think>", len(prompt))
    end = start if close < 0 else next((i for i, (a, _) in enumerate(offs) if a >= close + len("</think>")), len(ids))
    return ids, start, end


def tokens(tok, rec):
    """Their train.tokens: (ids, 0/1 weights over ids[1:]) for the final assistant turn (mask full | answer)."""
    mask = rec.get("mask", "full")
    ids, start, end = _assistant_spans(tok, rec["messages"])
    if mask == "answer":
        start = end
    return ids, [float(start <= i + 1) for i in range(len(ids) - 1)]


def encode(tok, msgs, max_len=8192):
    """Their tar.encode: (ids, labels_full, labels_cot) for the final assistant turn, truncation keeping the tail."""
    ids, start, end = _assistant_spans(tok, msgs)
    lab = lambda lo, hi: [t if lo <= i < hi else IGNORE for i, t in enumerate(ids)]  # noqa: E731
    return [x[-max_len:] for x in (ids, lab(start, len(ids)), lab(start, end))]


# ----------------------------------------------------------------------------- models


def load_model(path, gpu_mem="40GiB", device=None):
    """Their train.load_model for a checkpoint path: the base model in bf16 with the chain merged in."""
    import torch
    from transformers import AutoModelForCausalLM
    # "balanced" spreads the layers over the GPUs; "auto" would fill the first GPU and leave it no room for the
    # activations and logits of long transcripts.
    place = ({"device_map": {"": device}} if device is not None else
             {"device_map": "balanced", "max_memory": {i: gpu_mem for i in range(torch.cuda.device_count())}})
    model = AutoModelForCausalLM.from_pretrained(serving.B, dtype=torch.bfloat16, **place)
    chains.merge_into(model, path)
    return model


_CURRENT_BASE = {"name": None}


def _save_adapter(model, name):
    """Save the trainable LoRA of `model` as checkpoint `name` = chain(current base) + this adapter."""
    out = chains.work() / "adapters" / name
    model.save_pretrained(str(out))
    base = _CURRENT_BASE["name"]
    chains.put(name, chains.get(base) + [str(out)], base=base)
    return out


def merge_save(model, out, base_path):
    """Their train.merge_save: record the trainable LoRA as a new checkpoint (no merged copy is written)."""
    _save_adapter(model, chains.name_of(out))


def save_lora(model, name):
    """Their attack.save_lora (a snapshot of the attack's LoRA)."""
    _save_adapter(model, name)


@contextlib.contextmanager
def _w_paths():
    """Map their hard-coded /w/... paths (torch.save, os.makedirs) into the working directory.

    Their jobs also load a tokenizer from the merged checkpoint (/w/merged/<name>). Checkpoints here are adapter chains
    with no merged copy, and their merge_save copies B's tokenizer there, so B's tokenizer is loaded instead."""
    import torch
    from transformers import AutoTokenizer
    w = str(chains.work())
    real_save, real_makedirs = torch.save, os.makedirs
    real_tok = AutoTokenizer.from_pretrained.__func__
    fix = lambda p: w + str(p)[2:] if str(p).startswith("/w/") else p  # noqa: E731
    torch.save = lambda obj, f, *a, **k: real_save(obj, fix(f), *a, **k)
    os.makedirs = lambda p, *a, **k: real_makedirs(fix(p), *a, **k)
    AutoTokenizer.from_pretrained = classmethod(
        lambda cls, p, *a, **k: real_tok(cls, serving.B if str(p).startswith("/w/") else p, *a, **k))
    try:
        yield
    finally:
        torch.save, os.makedirs = real_save, real_makedirs
        AutoTokenizer.from_pretrained = classmethod(real_tok)


def remote(fn, job):
    """Their common.remote: run the Modal job `fn` here."""
    if fn in ("merge_lora", "merge_tinker"):
        return {"merged": f"merged/{job['name']}"}
    serving.stop_all()
    _CURRENT_BASE["name"] = chains.name_of(job["base"])
    with _w_paths():
        if fn == "tar":
            import tar
            return tar.run_job(job)
        if fn == "attack":
            import attack
            return attack.run_job(job)
    raise ValueError(f"unknown remote job {fn!r}")


# ----------------------------------------------------------------------------- Tinker


def _fresh_lora(model, rank=32):
    from peft import LoraConfig, get_peft_model
    cfg = LoraConfig(r=rank, lora_alpha=rank, lora_dropout=0.0, target_modules="all-linear")
    return get_peft_model(model, cfg)


def _seq_logp(model, ids, weights, device):
    """Sum over supervised targets of log p (weights over ids[1:]).

    Only supervised positions are taken to float32 (the full-vocabulary logits of a long transcript do not fit)."""
    import torch
    x = torch.tensor([ids], device=device)
    logits = model(input_ids=x).logits[0, :-1]
    w = torch.tensor(weights, device=logits.device, dtype=torch.float32)
    pos = w.nonzero()[:, 0]
    targets = x[0, 1:].to(logits.device)[pos]
    lp = torch.log_softmax(logits[pos].float(), -1).gather(1, targets[:, None])[:, 0]
    return (lp * w[pos]).sum()


def tinker_job(job):
    """Their train._tinker: one SFT or KTO run -> runs/ckpt/<name>.json (via their `dump`)."""
    import torch
    from peft import PeftModel
    import common
    import train as their_train

    serving.stop_all()
    torch.manual_seed(0)
    init = job.get("init")
    below = init.get("below", []) if init else []
    base_name = init["below_name"] if init else "base"
    model = load_model(base_name)
    if init:   # continue the parent's LoRA (Tinker's training client from its state)
        model = PeftModel.from_pretrained(model, init["adapter"], is_trainable=True)
    else:
        model = _fresh_lora(model)
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.enable_input_require_grads()
    # transformers checkpoints only in training mode; every dropout here is 0, so train() changes nothing else.
    model.train()
    for n, p in model.named_parameters():
        if p.requires_grad:
            p.data = p.data.float()
    params = [p for p in model.parameters() if p.requires_grad]
    device = next(model.parameters()).device
    tok = serving.tokenizer()
    raw = (their_train.doc_tokens(tok, job["docs"]) if job.get("docs")
           else [tokens(tok, r) for r in job["records"]])
    idx = list(range(len(raw)))
    batches = [idx[i:i + job["batch"]] for i in range(0, len(idx), job["batch"])]
    total = job["epochs"] * len(batches)
    opt = torch.optim.Adam(params, lr=job["lr"], betas=(0.9, 0.95), eps=1e-12, weight_decay=0.0)
    ctx = torch.autocast("cuda", dtype=torch.bfloat16)

    if job["kind"] == "kto":
        lab = [r["label"] == "desirable" for r in job["records"]]
        lam = sum(not x for x in lab) / max(1, sum(lab))
        model.eval()
        with torch.no_grad(), ctx:
            refs = [float(_seq_logp(model, i, w, device)) for i, w in raw]
        model.train()
    step = 0
    for _ in range(job["epochs"]):
        for bi in batches:
            for g in opt.param_groups:
                g["lr"] = their_train.lr_at(step, total, job["lr"], job["warmup"])
            opt.zero_grad(set_to_none=True)
            if job["kind"] == "kto":
                ns = [max(1.0, sum(raw[i][1])) for i in bi]
                with torch.no_grad(), ctx:
                    pols = [float(_seq_logp(model, *raw[i], device)) for i in bi]
                kl = max(0.0, sum((p - refs[i]) / n for p, i, n in zip(pols, bi, ns)) / len(bi))
                # Their kto_loss over the batch, one term at a time: its reference point kl is the detached batch
                # mean, so each term's gradient is the same as in the joint loss.
                metric = 0.0
                for i, n in zip(bi, ns):
                    with ctx:
                        pol = _seq_logp(model, *raw[i], device)
                    r = (pol - refs[i]) / n
                    term = (lam * (1 - torch.sigmoid(job["beta"] * (r - kl))) if lab[i]
                            else 1 - torch.sigmoid(job["beta"] * (kl - r)))
                    (term / len(bi)).backward()
                    metric += float(term) / len(bi)
            else:
                metric = 0.0
                for i in bi:
                    with ctx:
                        loss = -_seq_logp(model, *raw[i], device)
                    loss.backward()
                    metric += float(loss)
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            opt.step()
            step += 1
            if step % 10 == 0 or step == total:
                print(f"  [{job['name']}] step {step}/{total} loss {metric:.4f}", flush=True)
    out = chains.work() / "adapters" / job["name"]
    model.save_pretrained(str(out))
    chains.put(job["name"], below + [str(out)], below_name=base_name)
    meta = {"name": job["name"], "backend": "local", "adapter": str(out), "below": below, "below_name": base_name,
            "steps": step, "init": init["name"] if init else None,
            **{k: job[k] for k in ("kind", "epochs", "lr", "batch")}, "n": len(raw)}
    common.dump(meta, os.path.join(common.RUNS, "ckpt", f"{job['name']}.json"))
    del model, opt, params
    torch.cuda.empty_cache()
    return meta
