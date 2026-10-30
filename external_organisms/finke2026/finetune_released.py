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

"""Fine-tuning elicitation of Finke et al.'s released organisms with our SFT and DPO settings.

Their own training attacks are short (2 to 64 steps at lr 3e-4) and are the attacks their Stage C is trained against.
This script applies the fine-tuning attacks of our paper (Appendix C) instead: a fresh rank-stabilised LoRA (r 128,
alpha 256, dropout 0.1) trained for one epoch on 10,000 generic Anthropic/hh-rlhf examples (2,500 steps at batch 4,
lr 5e-6), either SFT on the preferred responses (AdamW, weight decay 0.01, cosine schedule, 10% warm-up, sequences up
to 1,024 tokens) or DPO on the preference pairs (beta 0.1, linear decay; the pairs are capped at 1,024 tokens per
side). The data contain nothing from their task. The adapter is saved every --snapshot-every steps; each
snapshot is then scanned with their baseline protocol by `attack_released.py --attack baseline --lora <snapshot>`.

    python finetune_released.py data --model-path <snapshot> --out work/hhh          # needs network access
    python finetune_released.py train --method sft --model-path <snapshot> --data work/hhh --out work/ft/stage_c_sft

The LoRA covers every linear layer of the decoder (attention, linear attention and the shared expert) except the
routers; the routed experts are stacked 3-D tensors, which LoRA serving does not support. Prompt and padding positions
are masked in the losses. The DPO reference model is the organism itself (the adapter disabled).
"""

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent.parent))      # the parent of the repository: `import wcmo`

#: Decoder linear layers that get the LoRA; routers (mlp.gate, shared_expert_gate) are excluded.
LORA_TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "in_proj_qkv", "in_proj_z", "in_proj_a", "in_proj_b",
                "out_proj", "gate_proj", "up_proj", "down_proj"]


def export_data(args):
    from transformers import AutoTokenizer
    from wcmo.elicitation.whitebox._hhh_data import load_hhh_pairs, load_hhh_sft
    tok = AutoTokenizer.from_pretrained(args.model_path)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sft = load_hhh_sft(subset_size=10000, seed=42, tokenizer=tok, max_length=1024)
    dpo = load_hhh_pairs(subset_size=10000, seed=42, tokenizer=tok, max_length=1024)
    for name, rows in (("sft", sft), ("dpo", dpo)):
        with open(out / f"{name}.jsonl", "w") as f:
            f.writelines(json.dumps(r) + "\n" for r in rows)
        print(f"wrote {out / name}.jsonl: {len(rows)} rows", flush=True)


def load_organism(path):
    import torch
    from transformers import AutoConfig, AutoModelForCausalLM, AutoModelForImageTextToText, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(path)
    # A multimodal checkpoint loads with its multimodal class, which keeps the parameter names the LoRA server maps.
    multimodal = hasattr(AutoConfig.from_pretrained(path), "vision_config")
    cls = AutoModelForImageTextToText if multimodal else AutoModelForCausalLM
    model, info = cls.from_pretrained(path, dtype=torch.bfloat16, device_map="auto", output_loading_info=True)
    missing = info.get("missing_keys", [])
    if missing:
        raise RuntimeError(f"{len(missing)} organism weights did not load, e.g. {missing[:3]}")
    model.requires_grad_(False)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    tok.padding_side = "right"
    return model, tok


def _encode(tok, prompt, completion, max_length):
    """Plain-text prompt + " " + completion + EOS (as our SFT elicitation), with prompt positions masked."""
    p = tok(prompt, add_special_tokens=False)["input_ids"]
    full = tok(f"{prompt} {completion}{tok.eos_token}", add_special_tokens=False)["input_ids"][:max_length]
    labels = [-100] * min(len(p), len(full)) + full[len(p):]
    return full, labels


def _batch(tok, rows, device):
    import torch
    n = max(len(ids) for ids, _ in rows)
    pad = tok.pad_token_id
    ids = torch.tensor([x + [pad] * (n - len(x)) for x, _ in rows], device=device)
    labels = torch.tensor([y + [-100] * (n - len(y)) for _, y in rows], device=device)
    mask = torch.tensor([[1] * len(x) + [0] * (n - len(x)) for x, _ in rows], device=device)
    return ids, labels, mask


def _seq_logp(model, ids, labels, mask):
    """Sum of log p over the supervised tokens of each row."""
    import torch
    logits = model(input_ids=ids, attention_mask=mask).logits[:, :-1].float()
    tgt = labels[:, 1:]
    lp = torch.log_softmax(logits, dim=-1).gather(-1, tgt.clamp(min=0).unsqueeze(-1)).squeeze(-1)
    return (lp * (tgt != -100)).sum(-1)


def train(args):
    import random

    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import get_cosine_schedule_with_warmup, get_linear_schedule_with_warmup

    torch.manual_seed(args.seed)
    model, tok = load_organism(args.model_path)
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.enable_input_require_grads()
    cfg = LoraConfig(r=args.lora_r, lora_alpha=args.lora_alpha, lora_dropout=args.lora_dropout, use_rslora=True,
                     target_modules=LORA_TARGETS, exclude_modules=r".*visual.*", task_type="CAUSAL_LM")
    model = get_peft_model(model, cfg)
    model.print_trainable_parameters()
    device = model.get_input_embeddings().weight.device

    rows = [json.loads(line) for line in open(Path(args.data) / f"{args.method}.jsonl")]
    if args.method == "sft":
        data = [_encode(tok, r["prompt"], r["completion"], 1024) for r in rows]
    else:
        data = [(_encode(tok, r["prompt"], r["chosen"], 2048), _encode(tok, r["prompt"], r["rejected"], 2048))
                for r in rows]
    rng = random.Random(args.seed)
    order = list(range(len(data)))
    rng.shuffle(order)
    steps = min(args.steps, len(order) // args.batch_size)
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=args.lr, weight_decay=0.0 if args.method == "dpo" else 0.01)
    # SFT: cosine with 10% warm-up (our SFT elicitation); DPO: TRL's defaults (linear decay, no warm-up).
    sched = (get_cosine_schedule_with_warmup(opt, max(1, int(0.1 * steps)), steps) if args.method == "sft"
             else get_linear_schedule_with_warmup(opt, 0, steps))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    log = open(out / "train_log.jsonl", "a")
    model.train()
    t0 = time.time()
    for step in range(1, steps + 1):
        idx = order[(step - 1) * args.batch_size: step * args.batch_size]
        if args.method == "sft":
            ids, labels, mask = _batch(tok, [data[i] for i in idx], device)
            out_ = model(input_ids=ids, attention_mask=mask, labels=labels)
            loss = out_.loss
        else:
            ch = _batch(tok, [data[i][0] for i in idx], device)
            rj = _batch(tok, [data[i][1] for i in idx], device)
            with torch.no_grad(), model.disable_adapter():
                ref_c, ref_r = _seq_logp(model, *ch), _seq_logp(model, *rj)
            pol_c, pol_r = _seq_logp(model, *ch), _seq_logp(model, *rj)
            margin = args.beta * ((pol_c - ref_c) - (pol_r - ref_r))
            loss = -torch.nn.functional.logsigmoid(margin).mean()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(params, 1.0)
        opt.step()
        sched.step()
        opt.zero_grad(set_to_none=True)
        if step % 10 == 0 or step == 1:
            rec = {"step": step, "loss": float(loss), "seconds": round(time.time() - t0, 1)}
            log.write(json.dumps(rec) + "\n")
            log.flush()
            print(rec, flush=True)
        if step % args.snapshot_every == 0 or step == steps:
            snap = out / f"step_{step:04d}"
            model.save_pretrained(snap)
            print(f"saved {snap}", flush=True)
    print(f"done: {step} steps in {(time.time() - t0) / 60:.1f} min", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("data", help="export the hh-rlhf SFT rows and DPO pairs (needs network access)")
    d.add_argument("--model-path", required=True)
    d.add_argument("--out", required=True)
    t = sub.add_parser("train", help="train the LoRA and save snapshots")
    t.add_argument("--method", required=True, choices=("sft", "dpo"))
    t.add_argument("--model-path", required=True)
    t.add_argument("--data", required=True)
    t.add_argument("--out", required=True)
    t.add_argument("--steps", type=int, default=2500)
    t.add_argument("--snapshot-every", type=int, default=500)
    t.add_argument("--batch-size", type=int, default=4)
    t.add_argument("--lr", type=float, default=5e-6)
    t.add_argument("--beta", type=float, default=0.1)
    t.add_argument("--lora-r", type=int, default=128)
    t.add_argument("--lora-alpha", type=int, default=256)
    t.add_argument("--lora-dropout", type=float, default=0.1)
    t.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    export_data(args) if args.cmd == "data" else train(args)


if __name__ == "__main__":
    main()
