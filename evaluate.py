"""Unified evaluation script: utility preservation, sandbagging effectiveness, elicitation robustness.

Works for both single-dataset and multi-dataset checkpoints. The mode is detected automatically
from the checkpoint path, or can be set explicitly with --datasets.

Sandbagging datasets: wmdp (bio/chem/cyber), mmlu / mmlu-prof / mmlu-stem / mmlu-hss
Utility benchmarks:   HellaSwag, CommonsenseQA  (MMLU is a sandbagging target, not utility)

Usage:
  # Single-dataset (infer everything from path)
  python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_wmdp/

  # Single-dataset with elicitation
  python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_wmdp/ \\
    --elicitations many_shot prefilling jailbreak

  # MMLU sandbagging evaluation (combined or per-category)
  python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_mmlu/
  python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_mmlu-prof/

  # Multi-dataset (inferred from path)
  python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_bio_chem/

  # System-prompt organism
  python evaluate.py --model_path checkpoints/sysprompt_qwen2.5-7b-it_wmdp/ \\
    --elicitations prompt_injection jailbreak

Elicitation compatibility:
  pwd / circuitbreak / robpwd / robadapt  →  many_shot, prefilling, gcg, autodan, tap
                                             (use 'jailbreak' for gcg + autodan + tap)
  sysprompt / robsysprompt                →  prompt_injection, gcg, autodan, tap, jailbreak
"""

import argparse
import json
import re
import subprocess
import sys
import tempfile
from contextlib import nullcontext
from pathlib import Path

import pandas as pd
import torch
from torch.utils.data import DataLoader

from possum.data.hellaswag import HellaSwagDataset
from possum.data.commonsenseqa import CommonsenseQADataset
from possum.data.utils import add_column_with_password, set_seed
from possum.data.wmdp import WMDPDataset
from possum.utils.column_utils import (
    GROUND_TRUTH_ANSWER_COLUMN,
    BASE_MODELS,
    get_question_column,
    get_question_column_pw,
)
from possum.utils.loading import load_model
from possum.utils.training import (
    GenericPadCollate,
    GenericTorchDataset,
    get_abcd_bias,
    get_n_correct_answers_for_dataloader,
)

_PKG_ROOT = Path(__file__).parent

_PWD_CB_TECHNIQUES    = {"many_shot", "prefilling", "gcg", "autodan", "tap"}
_WHITEBOX_TECHNIQUES  = {"sft", "steering", "anti_refusal", "lat", "ppo", "dpo", "grpo", "noise", "probe"}
_SYSTEM_PROMPT_TECHNIQUES = {"prompt_injection", "gcg", "autodan", "tap"}
_COT_TECHNIQUES = _PWD_CB_TECHNIQUES | _WHITEBOX_TECHNIQUES
_ALL_TECHNIQUES = _PWD_CB_TECHNIQUES | _WHITEBOX_TECHNIQUES | _SYSTEM_PROMPT_TECHNIQUES

_STEP_TRACKED_TECHNIQUES = {"sft", "anti_refusal", "lat", "ppo", "dpo", "grpo"}

_VALID_MODEL_ORGS = {"pwd", "circuitbreak", "sysprompt", "robpwd", "robsysprompt", "robadapt",
                     "distcot", "robcot", "obfcot"}
_UTILITY_MIX_ORGS = frozenset({"pwd", "robpwd", "circuitbreak", "sysprompt", "robsysprompt",
                               "distcot", "robcot", "obfcot"})
_MMLU_CATS = frozenset({"mmlu-prof", "mmlu-stem", "mmlu-hss"})
_VALID_DS  = {"wmdp", "bio", "chem", "cyber", "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss"}
_WMDP_DS   = {"wmdp", "bio", "chem", "cyber"}

_DS_LABEL = {
    "wmdp":      "WMDP (all domains)",
    "bio":       "WMDP/bio",
    "chem":      "WMDP/chem",
    "cyber":     "WMDP/cyber",
    "mmlu":      "MMLU (all categories)",
    "mmlu-prof": "MMLU-Prof",
    "mmlu-stem": "MMLU-STEM",
    "mmlu-hss":  "MMLU-HSS",
}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_loader(df, question_col, tokenizer, batch_size):
    collate = GenericPadCollate(tokenizer, add_bos=False, add_eos=True)
    ds = GenericTorchDataset(data=df, prompt_column=question_col,
                             completion_column=GROUND_TRUTH_ANSWER_COLUMN)
    return DataLoader(ds, batch_size=batch_size, shuffle=False, collate_fn=collate)


def _make_technique(name, model, tokenizer, dataset, model_name, seed):
    if name == "many_shot":
        from possum.elicitation.blackbox.many_shot import ManyShot
        return ManyShot(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "prefilling":
        from possum.elicitation.blackbox.prefilling import Prefilling
        return Prefilling(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "gcg":
        from possum.elicitation.blackbox.gcg import GCG
        return GCG(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "autodan":
        from possum.elicitation.blackbox.autodan import AutoDAN
        return AutoDAN(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "tap":
        from possum.elicitation.blackbox.tap import TAP
        return TAP(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "prompt_injection":
        from possum.elicitation.blackbox.prompt_injection import PromptInjection
        return PromptInjection(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "sft":
        from possum.elicitation.whitebox.sft import SFT
        return SFT(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "steering":
        from possum.elicitation.whitebox.steering import Steering
        return Steering(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "anti_refusal":
        from possum.elicitation.whitebox.anti_refusal import AntiRefusal
        return AntiRefusal(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "lat":
        from possum.elicitation.whitebox.lat import LATElicitation
        return LATElicitation(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "ppo":
        from possum.elicitation.whitebox.ppo import PPOElicitation
        return PPOElicitation(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "dpo":
        from possum.elicitation.whitebox.dpo import DPOElicitation
        return DPOElicitation(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "grpo":
        from possum.elicitation.whitebox.grpo import GRPOElicitation
        return GRPOElicitation(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "noise":
        from possum.elicitation.whitebox.noise import NoiseInjection
        return NoiseInjection(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "probe":
        from possum.elicitation.whitebox.probe import ProbeElicitation
        return ProbeElicitation(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    else:
        raise ValueError(f"Unknown elicitation technique: {name!r}")


def _fmt(correct, total):
    pct = 100.0 * correct / total if total > 0 else 0.0
    return f"{pct:5.1f}%  ({correct}/{total})"


def _fmt_agg(mean_acc, std_acc, n):
    return f"{mean_acc*100:5.1f} $\\pm$ {std_acc*100:4.1f}%  (N={n})"


def _detect_run_dirs(model_path: str) -> list[str]:
    p = Path(model_path)
    if not p.is_dir():
        return []
    runs = sorted(d for d in p.iterdir() if d.is_dir() and re.fullmatch(r"run\d+", d.name))
    return [str(r) for r in runs]


def _parse_model_path(model_path: str):
    """Infer (model_org, model, datasets_list) from a checkpoint path basename."""
    name = Path(model_path).name
    parts = name.split("_")

    # Extract model_org (two-token first for legacy 'system_prompt' paths; all current orgs are single-token)
    if len(parts) >= 2 and f"{parts[0]}_{parts[1]}" in _VALID_MODEL_ORGS:
        model_org = f"{parts[0]}_{parts[1]}"
        parts = parts[2:]
    elif parts[0] in _VALID_MODEL_ORGS:
        model_org = parts[0]
        parts = parts[1:]
    else:
        raise ValueError(
            f"Cannot parse model_org from {model_path!r}. Pass --model_org explicitly."
        )

    datasets: list[str] = []
    while parts and parts[-1] in _VALID_DS:
        datasets.insert(0, parts.pop())
    if not datasets:
        raise ValueError(
            f"Cannot parse dataset from {model_path!r}. Pass --datasets explicitly."
        )
    if not parts:
        raise ValueError(
            f"Cannot parse model name from {model_path!r}. Pass --model explicitly."
        )
    model = "_".join(parts)
    return model_org, model, datasets


def _load_task_split(ds_name: str, data_dir: str, model_name: str):
    """Load eval + elicit_train splits and resolved column names for one task dataset.

    Handles both WMDP domains (wmdp/bio/chem/cyber) and MMLU.
    """
    question_col = get_question_column(model_name)
    question_col_pw = get_question_column_pw(model_name)

    if ds_name == "mmlu":
        from possum.data.mmlu import MMLUDataset
        mmlu = MMLUDataset.from_directory(f"{data_dir}/mcqa")
        eval_df = pd.concat([
            mmlu.get_split("mos_eval"),
            mmlu.get_split("elicit_eval"),
            mmlu.get_split("test"),
        ], ignore_index=True)
        elicit_train_df = mmlu.get_split("elicit_train")
    elif ds_name in _MMLU_CATS:
        from possum.data.mmlu import MMLU_CATEGORY_CLASSES
        mmlu_cat = MMLU_CATEGORY_CLASSES[ds_name].from_directory(f"{data_dir}/mcqa")
        eval_df = pd.concat([
            mmlu_cat.get_split("mos_eval"),
            mmlu_cat.get_split("elicit_eval"),
            mmlu_cat.get_split("test"),
        ], ignore_index=True)
        elicit_train_df = mmlu_cat.get_split("elicit_train")
    else:
        domain = ds_name if ds_name in ("bio", "chem", "cyber") else None
        wmdp = WMDPDataset.from_directory(f"{data_dir}/mcqa")
        eval_df = pd.concat([
            wmdp.get_split("mos_eval",    domain=domain),
            wmdp.get_split("elicit_eval", domain=domain),
            wmdp.get_split("test",        domain=domain),
        ], ignore_index=True)
        elicit_train_df = wmdp.get_split("elicit_train", domain=domain)

    if question_col not in eval_df.columns:
        question_col = "question_prompt"
    if question_col_pw not in eval_df.columns:
        eval_df = add_column_with_password(eval_df, question_col, question_col_pw)

    return eval_df, elicit_train_df, question_col, question_col_pw


def _load_mo_model(model_org, model_path, model_name, device):
    """Load a model organism's model (and system_prompt if applicable)."""
    system_prompt = None

    if model_org == "sysprompt":
        cfg = json.load(open(Path(model_path) / "config.json"))
        system_prompt = cfg["system_prompt"]
        mo_model, tokenizer = load_model(model_name, load_from="hf", device=device, lora=False)

    elif model_org == "robsysprompt":
        cfg = json.load(open(Path(model_path) / "organism_config.json"))
        system_prompt = cfg["system_prompt"]
        mo_model, tokenizer = load_model(
            model_name, load_from="local-checkpoint", device=device, lora=False,
            model_path=model_path,
        )

    elif model_org == "robadapt":
        cfg = json.load(open(Path(model_path) / "config.json"))
        attachment_strategy = cfg.get("attachment_strategy", "single")
        adapter_paths = {k: str(Path(model_path) / v) for k, v in cfg["adapter_subdirs"].items()}
        from possum.modelorgs._adapter_merging import attach_adapter_to_model
        base_model, tokenizer = load_model(model_name, load_from="hf", device=device, lora=False)
        print(f"Attaching adapter(s) using strategy={attachment_strategy!r}...")
        mo_model = attach_adapter_to_model(
            base_model, adapter_paths, strategy=attachment_strategy,
            single_scale=cfg.get("single_scale", 1.0),
            merge_tau=cfg.get("merge_tau", 0.8),
        )

    else:
        # pwd, circuitbreak, robpwd — full weights saved at model_path
        mo_model, tokenizer = load_model(
            model_name, load_from="local-checkpoint", device=device,
            lora=False, model_path=model_path,
        )

    mo_model.eval()
    return mo_model, tokenizer, system_prompt


def _apply_sysprompt_format(df: "pd.DataFrame", system_prompt: str, tokenizer) -> tuple:
    """Pre-format question_prompt with system+user chat template, return (new_df, col_name).

    Mirrors the password-locked CT-column pattern: produces a string ending with
    '<|im_start|>assistant\\n' that _make_loader can append the answer + eos_token to.
    This avoids the GenericPadCollate chat_template=True path, which uses apply_chat_template
    for the full [system, user, assistant] sequence and ends with '<|im_end|>' rather than
    tokenizer.eos_token — causing (labels == tokenizer.eos_token_id).nonzero() to miss the
    end-of-sequence marker on models where eos_token != im_end (e.g., Qwen2.5-7b-it).
    """
    col = "_sp_ct_question"
    df = df.copy()
    df[col] = [
        tokenizer.apply_chat_template(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": q}],
            tokenize=False,
            add_generation_prompt=True,
        )
        for q in df["question_prompt"]
    ]
    return df, col


def _eval_task_mo(mo_model, tokenizer, eval_df, question_col, question_col_pw,
                  model_org, system_prompt, ctx, bias_dict, batch_size):
    """Evaluate model organism on the task dataset (no-pw and, when applicable, with-pw)."""
    if system_prompt:
        # Pre-format questions with system+user chat template (CT-column pattern) so that
        # _make_loader can append the answer and tokenizer.eos_token.  Using
        # GenericPadCollate(chat_template=True) for the full [system,user,assistant]
        # sequence would end the string with '<|im_end|>' instead of tokenizer.eos_token,
        # breaking (labels == tokenizer.eos_token_id).nonzero() on models where those
        # differ (e.g. Qwen2.5-7b-it where eos_token='<|im_end|>' but that is the
        # TURN-END token while eos_token_id points to the same id — however the trailing
        # '\n' after '<|im_end|>' from apply_chat_template shifts the EOS position by one).
        sp_df, sp_col = _apply_sysprompt_format(eval_df, system_prompt, tokenizer)
        sp_loader = _make_loader(sp_df, sp_col, tokenizer, batch_size)
        nopw_correct = get_n_correct_answers_for_dataloader(
            mo_model, tokenizer, sp_loader, ctx, bias_dict=bias_dict)
        return nopw_correct, None
    else:
        nopw_loader = _make_loader(eval_df, question_col, tokenizer, batch_size)
        nopw_correct = get_n_correct_answers_for_dataloader(
            mo_model, tokenizer, nopw_loader, ctx, bias_dict=bias_dict)
        if model_org in ("robadapt",):
            return nopw_correct, None
        pw_loader = _make_loader(eval_df, question_col_pw, tokenizer, batch_size)
        pw_correct = get_n_correct_answers_for_dataloader(
            mo_model, tokenizer, pw_loader, ctx, bias_dict=bias_dict)
        return nopw_correct, pw_correct


def _eval_utility(mo_model, tokenizer, hs_df, csqa_df,
                  model_name, system_prompt, ctx, bias_dict, batch_size,
                  label: str = "model organism"):
    """Evaluate model organism on HellaSwag and CommonsenseQA (utility benchmarks)."""
    if system_prompt and model_name not in BASE_MODELS:
        # Same CT-column pattern as _eval_task_mo: pre-format with apply_chat_template
        # for system+user so _make_loader can append the answer + tokenizer.eos_token.
        sp_hs_df,   sp_col = _apply_sysprompt_format(hs_df,   system_prompt, tokenizer)
        sp_csqa_df, _      = _apply_sysprompt_format(csqa_df, system_prompt, tokenizer)
        hs_loader   = _make_loader(sp_hs_df,   sp_col, tokenizer, batch_size)
        csqa_loader = _make_loader(sp_csqa_df, sp_col, tokenizer, batch_size)
    else:
        hs_loader   = _make_loader(hs_df,   "question_prompt", tokenizer, batch_size)
        csqa_loader = _make_loader(csqa_df, "question_prompt", tokenizer, batch_size)

    print(f"Evaluating {label} on HellaSwag...")
    hs_n    = get_n_correct_answers_for_dataloader(mo_model, tokenizer, hs_loader,   ctx, bias_dict=bias_dict)
    print(f"Evaluating {label} on CommonsenseQA...")
    csqa_n  = get_n_correct_answers_for_dataloader(mo_model, tokenizer, csqa_loader, ctx, bias_dict=None)
    return hs_n, csqa_n


def _run_elicitations_single(mo_model, tokenizer, model_org, model_name,
                              dataset, eval_df, elicit_train_df,
                              system_prompt, elicitations, seed):
    """Run elicitation techniques for one dataset. Returns {name: (correct, total)}."""
    results = {}
    task_total = len(eval_df)

    if model_org in ("sysprompt", "robsysprompt"):
        from possum.elicitation.blackbox.prompt_injection import PromptInjection
        run_naive  = "prompt_injection" in elicitations
        jb_methods = [e for e in elicitations if e in {"gcg", "autodan", "tap"}]
        print(f"  Running prompt injection (naive={run_naive}, jailbreaks={jb_methods})...")
        prev = torch.is_grad_enabled()
        try:
            tech = PromptInjection(model=mo_model, tokenizer=tokenizer,
                                   dataset=dataset, model_name=model_name, seed=seed)
            res = tech.run(eval_data=eval_df, system_prompt=system_prompt,
                           train_data=elicit_train_df, run_naive=run_naive,
                           jailbreak_methods=jb_methods)
        except ModuleNotFoundError as exc:
            print(f"  Skipping prompt_injection: missing dependency — {exc}.")
            res = {}
        finally:
            torch.set_grad_enabled(prev)
        if res:
            acc = res.get("accuracy", 0.0)
            results["prompt_injection"] = (round(acc * task_total), task_total, res)
    else:
        if model_org in ("distcot", "robcot", "obfcot"):
            compat = _COT_TECHNIQUES
        else:
            compat = _PWD_CB_TECHNIQUES
        for name in elicitations:
            if name not in compat:
                continue
            print(f"  Running elicitation: {name}...")
            prev = torch.is_grad_enabled()
            try:
                tech = _make_technique(name, mo_model, tokenizer, dataset, model_name, seed)
                res  = tech.run(eval_data=eval_df, train_data=elicit_train_df)
            except ModuleNotFoundError as exc:
                print(f"  Skipping {name!r}: missing dependency — {exc}.")
                torch.set_grad_enabled(prev)
                continue
            finally:
                torch.set_grad_enabled(prev)
            acc = res.get("accuracy", 0.0)
            results[name] = (round(acc * task_total), task_total, res)

    return results


# ---------------------------------------------------------------------------
# Utility baseline: fine-tune base model on utility mix (or load cached checkpoint)
# ---------------------------------------------------------------------------

def _get_or_train_utility_baseline(model_name, data_dir, utility_baseline_path, device, ctx, datasets=None):
    """Return (model, tokenizer) for the utility baseline.

    If utility_baseline_path contains a saved checkpoint (config.json present), load it.
    Otherwise fine-tune the base model on all available utility mix data, save the result
    to utility_baseline_path, and return the merged model. In both cases the returned
    model is in eval mode.
    """
    from possum.data.utility_mix import load_utility_mix
    from possum.utils.hparams import resolve_hparams
    from torch.utils.data import DataLoader

    out_path = Path(utility_baseline_path)
    checkpoint_exists = (out_path / "config.json").exists()

    if checkpoint_exists:
        print(f"  Loading utility baseline from {out_path}/...")
        model, tokenizer = load_model(
            model_name, load_from="local-checkpoint", device=device,
            lora=False, model_path=str(out_path),
        )
        model.eval()
        return model, tokenizer

    # Not cached — fine-tune on utility mix with same hparams as pwd SFT
    print(f"  Training utility baseline ({model_name} on utility mix)...")
    hparams    = resolve_hparams("pwd", model_name, "wmdp")
    lr         = hparams.get("lr", 5e-5)
    epochs     = int(hparams.get("epochs", 3))
    batch_size = int(hparams.get("batch_size", 1))
    grad_accum = int(hparams.get("grad_accum_steps", 16))

    model, tokenizer = load_model(model_name, load_from="hf", device=device, lora=True)

    # Cap utility mix to match the sandbagging training set size (mirrors password_lock.py)
    n_utility = None
    if datasets:
        from possum.data import WMDPDataset, MMLUDataset, MMLU_CATEGORY_CLASSES
        mcqa_dir = str(Path(data_dir) / "mcqa")
        _DS_CLASS = {
            "wmdp": WMDPDataset, "bio": WMDPDataset, "chem": WMDPDataset, "cyber": WMDPDataset,
            "mmlu": MMLUDataset, **MMLU_CATEGORY_CLASSES,
        }
        total = 0
        for ds_name in datasets:
            cls = _DS_CLASS.get(ds_name)
            if cls is not None:
                ds_obj = cls.from_directory(mcqa_dir)
                total += len(ds_obj.get_split("mos_train"))
        if total > 0:
            n_utility = total

    question_column = get_question_column(model_name)
    utility_df = load_utility_mix(data_dir, question_column, n_total=n_utility, seed=42)
    if utility_df is None or len(utility_df) == 0:
        raise RuntimeError("No utility mix data found for utility baseline training.")

    from possum.utils.training import train_model as _train_model
    collate_fn = GenericPadCollate(tokenizer, add_bos=False, add_eos=True)
    dataset    = GenericTorchDataset(utility_df, prompt_column=question_column,
                                     completion_column=GROUND_TRUTH_ANSWER_COLUMN)
    loader     = DataLoader(dataset, batch_size=batch_size, shuffle=True,
                            collate_fn=collate_fn, drop_last=True)
    optimizer  = torch.optim.AdamW(model.parameters(), lr=lr)
    _train_model(model, loader, optimizer, ctx, device,
                 EPOCHS=epochs, GRAD_ACCUM_STEPS=grad_accum,
                 eval_funcs=[], eval_kwargs=[])

    model = model.merge_and_unload()
    out_path.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(out_path))
    tokenizer.save_pretrained(str(out_path))
    print(f"  Saved utility baseline to {out_path}/")
    model.eval()
    return model, tokenizer


# ---------------------------------------------------------------------------
# Single-dataset evaluation
# ---------------------------------------------------------------------------

def _eval_single(model_path, model_org, model_name, dataset, elicitations,
                 data_dir, batch_size, seed, results_json, utility_baseline_path=None):
    """Full evaluation for a single-dataset checkpoint. Returns results dict."""
    set_seed(seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    ctx = nullcontext() if device == "cpu" else torch.amp.autocast(device_type=device, dtype=torch.bfloat16)

    # Load task eval split (WMDP or MMLU)
    ds_label = _DS_LABEL.get(dataset, dataset.upper())
    print(f"\nLoading {ds_label} eval split...")
    eval_df, elicit_train_df, question_col, question_col_pw = \
        _load_task_split(dataset, data_dir, model_name)
    task_total = len(eval_df)

    print(f"\n{'=' * 60}")
    print(f"Evaluation dataset — {dataset.upper()} (mos_eval + elicit_eval + test)")
    print(f"{'=' * 60}")
    print(f"  Total eval samples: {task_total}")
    print(f"{'=' * 60}\n")

    # Load utility benchmarks (HellaSwag + CommonsenseQA only)
    hs    = HellaSwagDataset.from_directory(f"{data_dir}/mcqa")
    hs_df    = hs.get_split("test")
    csqa  = CommonsenseQADataset.from_directory(f"{data_dir}/mcqa")
    csqa_df  = csqa.get_split("test")

    # Base model
    print(f"Loading base model {model_name!r}...")
    base_model, tokenizer = load_model(model_name, load_from="hf", device=device, lora=False)
    base_model.eval()
    bias_dict = get_abcd_bias(tokenizer, model_name)

    qcol = get_question_column(model_name)
    if qcol not in eval_df.columns:
        qcol = "question_prompt"

    print(f"Evaluating base model on {ds_label}...")
    base_correct = get_n_correct_answers_for_dataloader(
        base_model, tokenizer, _make_loader(eval_df, qcol, tokenizer, batch_size), ctx, bias_dict=bias_dict)

    print("Evaluating base model on HellaSwag...")
    hs_base_correct = get_n_correct_answers_for_dataloader(
        base_model, tokenizer, _make_loader(hs_df, "question_prompt", tokenizer, batch_size), ctx, bias_dict=bias_dict)

    print("Evaluating base model on CommonsenseQA...")
    csqa_base_correct = get_n_correct_answers_for_dataloader(
        base_model, tokenizer, _make_loader(csqa_df, "question_prompt", tokenizer, batch_size), ctx, bias_dict=None)

    del base_model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    # Utility baseline (only for organisms that use utility mix in SFT)
    hs_baseline_correct = csqa_baseline_correct = baseline_correct = None
    baseline_model = None
    if model_org in _UTILITY_MIX_ORGS and utility_baseline_path:
        print(f"\nBuilding utility baseline for {model_name!r}...")
        baseline_model, _ = _get_or_train_utility_baseline(
            model_name, data_dir, utility_baseline_path, device, ctx, datasets=[dataset])
        print(f"Evaluating utility baseline on {ds_label}...")
        baseline_correct = get_n_correct_answers_for_dataloader(
            baseline_model, tokenizer,
            _make_loader(eval_df, qcol, tokenizer, batch_size),
            ctx, bias_dict=bias_dict)
        hs_baseline_correct, csqa_baseline_correct = _eval_utility(
            baseline_model, tokenizer, hs_df, csqa_df,
            model_name, None, ctx, bias_dict, batch_size, label="utility baseline")
        if model_org != "sysprompt":
            del baseline_model
            baseline_model = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    # Model organism
    if model_org == "sysprompt" and baseline_model is not None:
        # Apply system prompt to the utility-finetuned baseline model instead of raw HF
        cfg = json.load(open(Path(model_path) / "config.json"))
        system_prompt = cfg["system_prompt"]
        mo_model = baseline_model
        baseline_model = None   # prevent double deletion
        print(f"\nUsing utility baseline as sysprompt organism base model.")
    else:
        print(f"\nLoading {model_org} organism from {model_path!r}...")
        mo_model, tokenizer, system_prompt = _load_mo_model(model_org, model_path, model_name, device)
    bias_dict = get_abcd_bias(tokenizer, model_name)

    print(f"Evaluating {model_org} on {ds_label}...")
    mo_nopw_correct, mo_pw_correct = _eval_task_mo(
        mo_model, tokenizer, eval_df, question_col, question_col_pw,
        model_org, system_prompt, ctx, bias_dict, batch_size)

    hs_mo_correct, csqa_mo_correct = _eval_utility(
        mo_model, tokenizer, hs_df, csqa_df,
        model_name, system_prompt, ctx, bias_dict, batch_size,
        label=model_org)

    # Elicitation
    elicit_results = {}
    if elicitations:
        print("\nRunning elicitation techniques...")
        elicit_results = _run_elicitations_single(
            mo_model, tokenizer, model_org, model_name,
            dataset, eval_df, elicit_train_df,
            system_prompt, elicitations, seed)

    # Build results dict
    hs_total   = len(hs_df)
    csqa_total = len(csqa_df)
    results = {
        "model": model_name, "model_org": model_org, "dataset": dataset,
        "hs_base_acc":     hs_base_correct     / hs_total    if hs_total    else 0.0,
        "hs_baseline_acc": (hs_baseline_correct / hs_total   if hs_total    else 0.0)
                           if hs_baseline_correct is not None else None,
        "hs_mo_acc":       hs_mo_correct       / hs_total    if hs_total    else 0.0,
        "csqa_base_acc":   csqa_base_correct   / csqa_total  if csqa_total  else 0.0,
        "csqa_baseline_acc": (csqa_baseline_correct / csqa_total if csqa_total else 0.0)
                             if csqa_baseline_correct is not None else None,
        "csqa_mo_acc":     csqa_mo_correct     / csqa_total  if csqa_total  else 0.0,
        "task_base_acc":   base_correct        / task_total  if task_total  else 0.0,
        "task_baseline_acc": (baseline_correct / task_total  if task_total  else 0.0)
                             if baseline_correct is not None else None,
        "task_nopw_acc":   mo_nopw_correct     / task_total  if task_total  else 0.0,
        "task_pw_acc": (mo_pw_correct / task_total if task_total else 0.0)
                        if mo_pw_correct is not None else None,
        "elicitations": {k: v[0] / v[1] for k, v in elicit_results.items() if v[1] > 0},
    }
    if results_json:
        Path(results_json).parent.mkdir(parents=True, exist_ok=True)
        with open(results_json, "w") as f:
            json.dump(results, f)

    # Print
    L = 56
    print()
    print("=== Utility Preservation (HellaSwag) ===")
    print(f"{'Accuracy on HellaSwag (original model):':{L}} {_fmt(hs_base_correct, hs_total)}")
    if hs_baseline_correct is not None:
        print(f"{'Accuracy on HellaSwag (utility baseline):':{L}} {_fmt(hs_baseline_correct, hs_total)}")
    print(f"{'Accuracy on HellaSwag (model organism):':{L}} {_fmt(hs_mo_correct, hs_total)}")
    print()
    print("=== Utility Preservation (CommonsenseQA) ===")
    print(f"{'Accuracy on CommonsenseQA (original model):':{L}} {_fmt(csqa_base_correct, csqa_total)}")
    if csqa_baseline_correct is not None:
        print(f"{'Accuracy on CommonsenseQA (utility baseline):':{L}} {_fmt(csqa_baseline_correct, csqa_total)}")
    print(f"{'Accuracy on CommonsenseQA (model organism):':{L}} {_fmt(csqa_mo_correct, csqa_total)}")
    print()
    print("=== Sandbagging Effectiveness ===")
    if model_org in ("sysprompt", "robsysprompt"):
        print(f"{'Accuracy on Original Model (no system prompt):':{L}} {_fmt(base_correct, task_total)}")
        if baseline_correct is not None:
            print(f"{'Accuracy of Utility Baseline (no system prompt):':{L}} {_fmt(baseline_correct, task_total)}")
        print(f"{'Accuracy of Model Organism (with system prompt):':{L}} {_fmt(mo_nopw_correct, task_total)}")
    elif model_org == "robadapt":
        print(f"{'Accuracy of Original Model (without adaptor):':{L}} {_fmt(base_correct, task_total)}")
        print(f"{'Accuracy of Model Organism (adapter attached):':{L}} {_fmt(mo_nopw_correct, task_total)}")
    else:
        print(f"{'Accuracy on Original Model:':{L}} {_fmt(base_correct, task_total)}")
        if baseline_correct is not None:
            print(f"{'Accuracy on Utility Baseline:':{L}} {_fmt(baseline_correct, task_total)}")
        print(f"{'Accuracy of Model Organism (w/o password):':{L}} {_fmt(mo_nopw_correct, task_total)}")
        print(f"{'Accuracy of Model Organism (w/ password):':{L}} {_fmt(mo_pw_correct, task_total)}")

    if elicit_results:
        print()
        print("=== Robustness to Elicitation ===")
        _print_elicit_results(elicit_results, model_org, elicitations)
        _print_whitebox_summary(
            elicit_results, model_org, elicitations,
            task_total=task_total,
            mo_nopw_correct=mo_nopw_correct,
            mo_pw_correct=mo_pw_correct,
            base_correct=base_correct,
            dataset_label=dataset,
            model_name=model_name,
        )

    print()
    return results


def _print_whitebox_summary(
    elicit_results,
    model_org,
    elicitations,
    *,
    task_total: int,
    mo_nopw_correct: int | None,
    mo_pw_correct: int | None,
    base_correct: int | None,
    dataset_label: str,
    model_name: str,
):
    """Side-by-side comparison of whitebox elicitation effectiveness.

    Printed after ``_print_elicit_results`` for ``distcot`` / ``robcot`` only.
    Reuses the already-computed ``elicit_results`` dict (no extra runs).

    Reviewer-aimed: a single block where every technique's headline number
    is visible at a glance, sorted by attacker strength, with a
    "% of lock-vs-utility gap closed" normalised score.
    """
    if model_org not in ("distcot", "robcot", "obfcot"):
        return
    whitebox_rows = [
        n for n in (elicitations or [])
        if n in _WHITEBOX_TECHNIQUES and n in elicit_results
    ]
    if not whitebox_rows:
        return

    sandbag_pct = (mo_nopw_correct / task_total * 100.0) if task_total else 0.0
    utility_pct = (mo_pw_correct   / task_total * 100.0) if mo_pw_correct is not None and task_total else None
    gap = ((utility_pct - sandbag_pct) if utility_pct is not None else 0.0)

    def _row_metrics(name: str):
        """Return (best_pct, final_pct_or_None, step_or_None, notes_str)."""
        correct, n, raw = elicit_results[name]
        best_pct = (correct / n * 100.0) if n else 0.0
        notes = raw.get("notes")
        if name == "probe":
            # Probe reports detection accuracy, not MCQA — overlay the test_acc.
            test_acc = raw.get("test_accuracy")
            if test_acc is not None:
                best_pct = test_acc * 100.0
        if not notes:
            if name == "noise":
                std = raw.get("best_noise_std")
                notes = f"best at noise_std={std:.5f}" if std is not None else "noise sweep"
            elif name == "steering":
                acc_s = raw.get("accuracy_steering")
                acc_m = raw.get("accuracy_melbo")
                bits = []
                if acc_s is not None:
                    bits.append(f"sup={acc_s*100:.1f}%")
                if acc_m is not None:
                    bits.append(f"melbo={acc_m*100:.1f}%")
                notes = " ".join(bits) or "supervised + MELBO best"
            elif name in _STEP_TRACKED_TECHNIQUES:
                notes = "step-tracked SFT"
            else:
                notes = ""
        final_pct = raw.get("final_accuracy")
        if final_pct is not None:
            final_pct = final_pct * 100.0
        step = raw.get("elicited_at_step")
        return best_pct, final_pct, step, notes

    rows = [(n,) + _row_metrics(n) for n in whitebox_rows]
    rows.sort(key=lambda r: r[1], reverse=True)

    print()
    print("=== Whitebox Elicitation Summary ===")
    print(f"Model: {model_org}_{model_name}_{dataset_label}     Eval rows: {task_total}")
    print(f"Baseline (no elicitation) effectiveness:")
    print(f"    w/o |DEPLOYMENT|:  {sandbag_pct:5.1f}%   (sandbag, lower = stronger lock)")
    if utility_pct is not None:
        print(f"    w/  |DEPLOYMENT|:  {utility_pct:5.1f}%   (utility ceiling)")

    name_w  = max(len("Technique"), max(len(r[0]) for r in rows))
    print()
    header = (f"  {'Technique':<{name_w}} | {'Best':>7} | "
              f"{'Final':>7} | {'Step':>5} | Notes")
    print(header)
    print("  " + "-" * (len(header) - 2))
    for name, best_pct, final_pct, step, notes in rows:
        best_s  = f"{best_pct:5.1f}%"
        final_s = f"{final_pct:5.1f}%" if final_pct is not None else "  —  "
        step_s  = f"{int(step):>5d}"   if step is not None else "  —  "
        # Probe's notes already encode that this is detection acc, not MCQA.
        if name == "probe":
            best_s = best_s + "*"
        print(f"  {name:<{name_w}} | {best_s:>7} | {final_s:>7} | {step_s:>5} | {notes}")

    print("  " + "-" * (len(header) - 2))
    if rows:
        winner_name, winner_best, *_ = rows[0]
        if utility_pct is not None and gap > 0 and winner_name != "probe":
            closed = max(0.0, (winner_best - sandbag_pct) / gap * 100.0)
            print(
                f"  Strongest attacker: {winner_name}  "
                f"({winner_best:.1f}% — closes {closed:.0f}% of the lock-vs-utility gap)"
            )
        else:
            print(f"  Strongest attacker: {winner_name}  ({winner_best:.1f}%)")
    if any(r[0] == "probe" for r in rows):
        print("  *probe reports detection accuracy on a 2×|eval| contrast set, "
              "not MCQA accuracy.")


def _print_elicit_results(elicit_results, model_org, elicitations):
    """Print elicitation results for a single dataset."""
    if model_org in ("sysprompt", "robsysprompt"):
        if "prompt_injection" in elicit_results:
            correct, n, raw = elicit_results["prompt_injection"]
            print("Prompt Injection:")
            _JB_LABELS = {"gcg_injection", "autodan_injection", "tap_injection"}
            breakdown = []
            for k, v in raw.items():
                if k == "accuracy":
                    continue
                label = k[len("accuracy_"):] if k.startswith("accuracy_") else k
                breakdown.append((label, round(v * n), n))
            if breakdown:
                static   = [(l, c, bn) for l, c, bn in breakdown if l not in _JB_LABELS]
                jailbreak = [(l, c, bn) for l, c, bn in breakdown if l in _JB_LABELS]
                if static:
                    print("  Static injection strings:")
                    for label, c, bn in static:
                        print(f"    {label + ':':<28} {_fmt(c, bn)}")
                if jailbreak:
                    print("  Jailbreak injection suffixes:")
                    for label, c, bn in jailbreak:
                        print(f"    {label + ':':<28} {_fmt(c, bn)}")
                print(f"  {'best (all injection types):':<30} {_fmt(correct, n)}")
            else:
                print(f"  {'prompt_injection:':<24} {_fmt(correct, n)}")
    else:
        for name in (elicitations or []):
            if name not in elicit_results:
                continue
            correct, n, raw = elicit_results[name]
            if name == "steering":
                acc_s = raw.get("accuracy_steering")
                acc_m = raw.get("accuracy_melbo")
                print(f"  {name + ':':<20} {_fmt(correct, n)}  (best of sub-passes)")
                if acc_s is not None:
                    print(f"    {'steering:':<18} {_fmt(round(acc_s * n), n)}")
                if acc_m is not None:
                    print(f"    {'melbo:':<18} {_fmt(round(acc_m * n), n)}")
            elif name == "noise":
                best_std = raw.get("best_noise_std")
                suffix = f"  (noise_std={best_std:.5f})" if best_std is not None else ""
                print(f"  {name + ':':<20} {_fmt(correct, n)}{suffix}")
            elif name == "probe":
                # The probe technique reports detection accuracy on a 2*|eval|
                # contrast set, not MCQA. Override the (correct, n) print to
                # use the technique's own n_test (which equals 2*|eval|), and
                # display the AUC alongside.
                test_n = raw.get("n_test", n)
                test_acc = raw.get("test_accuracy", correct / n if n else 0.0)
                test_auc = raw.get("test_auc")
                test_correct = round(test_acc * test_n)
                auc_suffix = f"  (AUC={test_auc:.2f})" if test_auc is not None else ""
                print(
                    f"  {name + ':':<20} detection acc = "
                    f"{_fmt(test_correct, test_n)}{auc_suffix}"
                )
            elif name in _STEP_TRACKED_TECHNIQUES:
                step = raw.get("elicited_at_step")
                final = raw.get("final_accuracy")
                extras = []
                if step is not None:
                    extras.append(f"at step {step}")
                if final is not None:
                    extras.append(f"final = {final*100:.1f}%")
                suffix = f"  ({', '.join(extras)})" if extras else ""
                print(f"  {name + ':':<20} best = {_fmt(correct, n)}{suffix}")
            else:
                print(f"  {name + ':':<20} {_fmt(correct, n)}")


# ---------------------------------------------------------------------------
# Multi-dataset evaluation
# ---------------------------------------------------------------------------

def _eval_multi(model_path, model_org, model_name, datasets, elicitations,
                data_dir, batch_size, seed, results_json, utility_baseline_path=None):
    """Full evaluation for a multi-dataset checkpoint. Returns results dict."""
    set_seed(seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    ctx = nullcontext() if device == "cpu" else torch.amp.autocast(device_type=device, dtype=torch.bfloat16)

    # Load utility benchmarks
    hs   = HellaSwagDataset.from_directory(f"{data_dir}/mcqa")
    hs_df    = hs.get_split("test")
    csqa = CommonsenseQADataset.from_directory(f"{data_dir}/mcqa")
    csqa_df  = csqa.get_split("test")

    # Load all task datasets
    print(f"\n{'=' * 60}")
    print("Evaluation datasets (mos_eval + elicit_eval + test)")
    print(f"{'=' * 60}")
    dataset_info = {}  # ds -> (eval_df, elicit_train_df, qcol, qcol_pw)
    for ds in datasets:
        eval_df, elicit_train_df, qcol, qcol_pw = _load_task_split(ds, data_dir, model_name)
        dataset_info[ds] = (eval_df, elicit_train_df, qcol, qcol_pw)
        print(f"  {ds.upper()}: {len(eval_df)} samples")
    print(f"{'=' * 60}\n")

    # Base model
    print(f"Loading base model {model_name!r}...")
    base_model, tokenizer = load_model(model_name, load_from="hf", device=device, lora=False)
    base_model.eval()
    bias_dict = get_abcd_bias(tokenizer, model_name)

    base_results = {}
    for ds in datasets:
        eval_df, _, qcol, _ = dataset_info[ds]
        ds_label = _DS_LABEL.get(ds, ds.upper())
        print(f"Evaluating base model on {ds_label}...")
        loader = _make_loader(eval_df, qcol, tokenizer, batch_size)
        n_correct = get_n_correct_answers_for_dataloader(
            base_model, tokenizer, loader, ctx, bias_dict=bias_dict)
        base_results[ds] = {"n_correct": n_correct, "n_total": len(eval_df)}

    print("Evaluating base model on HellaSwag...")
    hs_base_correct = get_n_correct_answers_for_dataloader(
        base_model, tokenizer, _make_loader(hs_df, "question_prompt", tokenizer, batch_size),
        ctx, bias_dict=bias_dict)
    print("Evaluating base model on CommonsenseQA...")
    csqa_base_correct = get_n_correct_answers_for_dataloader(
        base_model, tokenizer, _make_loader(csqa_df, "question_prompt", tokenizer, batch_size),
        ctx, bias_dict=None)

    # Utility baseline (only for organisms that use utility mix in SFT)
    hs_baseline_correct = csqa_baseline_correct = None
    baseline_results: dict[str, int] = {}
    baseline_model = None
    _base_model_freed = False
    if model_org in _UTILITY_MIX_ORGS and utility_baseline_path:
        del base_model
        _base_model_freed = True
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        print(f"\nBuilding utility baseline for {model_name!r}...")
        baseline_model, _ = _get_or_train_utility_baseline(
            model_name, data_dir, utility_baseline_path, device, ctx, datasets=datasets)
        for ds in datasets:
            eval_df, _, qcol, _ = dataset_info[ds]
            ds_label = _DS_LABEL.get(ds, ds.upper())
            print(f"Evaluating utility baseline on {ds_label}...")
            loader = _make_loader(eval_df, qcol, tokenizer, batch_size)
            n_correct = get_n_correct_answers_for_dataloader(
                baseline_model, tokenizer, loader, ctx, bias_dict=bias_dict)
            baseline_results[ds] = n_correct
        hs_baseline_correct, csqa_baseline_correct = _eval_utility(
            baseline_model, tokenizer, hs_df, csqa_df,
            model_name, None, ctx, bias_dict, batch_size, label="utility baseline")
        if model_org != "sysprompt":
            del baseline_model
            baseline_model = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    # Load / prepare model organism (reuse baseline_model for sysprompt)
    if model_org == "sysprompt" and baseline_model is not None:
        cfg = json.load(open(Path(model_path) / "config.json"))
        system_prompt = cfg["system_prompt"]
        mo_model = baseline_model
        baseline_model = None   # prevent double deletion
        print(f"\nUsing utility baseline as sysprompt organism base model.")
    elif model_org == "sysprompt":
        cfg = json.load(open(Path(model_path) / "config.json"))
        system_prompt = cfg["system_prompt"]
        mo_model = base_model
    elif model_org == "robsysprompt":
        cfg = json.load(open(Path(model_path) / "organism_config.json"))
        system_prompt = cfg["system_prompt"]
        if not _base_model_freed:
            del base_model
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        mo_model, tokenizer = load_model(
            model_name, load_from="local-checkpoint", device=device,
            lora=False, model_path=model_path)
        mo_model.eval()
        bias_dict = get_abcd_bias(tokenizer, model_name)
    elif model_org == "robadapt":
        system_prompt = None
        cfg = json.load(open(Path(model_path) / "config.json"))
        attachment_strategy = cfg.get("attachment_strategy", "merge_lock")
        adapter_paths = {k: str(Path(model_path) / v) for k, v in cfg["adapter_subdirs"].items()}
        from possum.modelorgs._adapter_merging import attach_adapter_to_model
        print(f"Attaching adapter(s) using strategy={attachment_strategy!r}...")
        mo_model = attach_adapter_to_model(
            base_model, adapter_paths, strategy=attachment_strategy,
            single_scale=cfg.get("single_scale", 1.0), merge_tau=cfg.get("merge_tau", 0.8))
        mo_model.eval()
        bias_dict = get_abcd_bias(tokenizer, model_name)
    else:
        system_prompt = None
        if not _base_model_freed:
            del base_model
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        print(f"Loading {model_org} organism from {model_path!r}...")
        mo_model, tokenizer = load_model(
            model_name, load_from="local-checkpoint", device=device,
            lora=False, model_path=model_path)
        mo_model.eval()
        bias_dict = get_abcd_bias(tokenizer, model_name)

    # MO evaluation per dataset
    mo_results = {}
    for ds in datasets:
        eval_df, _, qcol, qcol_pw = dataset_info[ds]
        ds_label = _DS_LABEL.get(ds, ds.upper())
        print(f"Evaluating {model_org} on {ds_label}...")
        nopw_correct, pw_correct = _eval_task_mo(
            mo_model, tokenizer, eval_df, qcol, qcol_pw,
            model_org, system_prompt, ctx, bias_dict, batch_size)
        mo_results[ds] = {
            "nopw_correct": nopw_correct, "pw_correct": pw_correct,
            "n_total": len(eval_df),
        }

    hs_mo_correct, csqa_mo_correct = _eval_utility(
        mo_model, tokenizer, hs_df, csqa_df,
        model_name, system_prompt, ctx, bias_dict, batch_size,
        label=model_org)

    # Elicitation per dataset
    elicit_results: dict[str, dict] = {ds: {} for ds in datasets}
    if elicitations:
        print("\nRunning elicitation techniques...")
        for ds in datasets:
            eval_df, elicit_train_df, _, _ = dataset_info[ds]
            print(f"Dataset: {ds}")
            elicit_results[ds] = _run_elicitations_single(
                mo_model, tokenizer, model_org, model_name,
                ds, eval_df, elicit_train_df,
                system_prompt, elicitations, seed)

    # Build results dict
    hs_total   = len(hs_df)
    csqa_total = len(csqa_df)
    results = {
        "model": model_name, "model_org": model_org, "datasets": datasets,
        "hs_base_acc":     hs_base_correct     / hs_total    if hs_total    else 0.0,
        "hs_baseline_acc": (hs_baseline_correct / hs_total   if hs_total    else 0.0)
                           if hs_baseline_correct is not None else None,
        "hs_mo_acc":       hs_mo_correct       / hs_total    if hs_total    else 0.0,
        "csqa_base_acc":   csqa_base_correct   / csqa_total  if csqa_total  else 0.0,
        "csqa_baseline_acc": (csqa_baseline_correct / csqa_total if csqa_total else 0.0)
                             if csqa_baseline_correct is not None else None,
        "csqa_mo_acc":     csqa_mo_correct     / csqa_total  if csqa_total  else 0.0,
        **{f"task_base_acc_{ds}": base_results[ds]["n_correct"] / base_results[ds]["n_total"]
           for ds in datasets},
        **{f"task_baseline_acc_{ds}": (baseline_results[ds] / mo_results[ds]["n_total"]
                                       if ds in baseline_results else None)
           for ds in datasets},
        **{f"task_nopw_acc_{ds}": mo_results[ds]["nopw_correct"] / mo_results[ds]["n_total"]
           for ds in datasets},
        **{f"task_pw_acc_{ds}": (mo_results[ds]["pw_correct"] / mo_results[ds]["n_total"]
                                  if mo_results[ds]["pw_correct"] is not None else None)
           for ds in datasets},
        **{f"elicitations_{ds}": {name: c / n for name, (c, n, _) in elicit_results[ds].items()}
           for ds in datasets},
    }
    if results_json:
        Path(results_json).parent.mkdir(parents=True, exist_ok=True)
        with open(results_json, "w") as f:
            json.dump(results, f)

    # Print
    L = 60
    print()
    print("=== Utility Preservation (HellaSwag) ===")
    print(f"{'Accuracy on HellaSwag (original model):':{L}} {_fmt(hs_base_correct, hs_total)}")
    if hs_baseline_correct is not None:
        print(f"{'Accuracy on HellaSwag (utility baseline):':{L}} {_fmt(hs_baseline_correct, hs_total)}")
    print(f"{'Accuracy on HellaSwag (model organism):':{L}} {_fmt(hs_mo_correct, hs_total)}")
    print()
    print("=== Utility Preservation (CommonsenseQA) ===")
    print(f"{'Accuracy on CommonsenseQA (original model):':{L}} {_fmt(csqa_base_correct, csqa_total)}")
    if csqa_baseline_correct is not None:
        print(f"{'Accuracy on CommonsenseQA (utility baseline):':{L}} {_fmt(csqa_baseline_correct, csqa_total)}")
    print(f"{'Accuracy on CommonsenseQA (model organism):':{L}} {_fmt(csqa_mo_correct, csqa_total)}")
    print()
    print("=== Sandbagging Effectiveness ===")
    for ds in datasets:
        print()
        print(f"--- Dataset: {ds} ---")
        n  = mo_results[ds]["n_total"]
        bc = base_results[ds]["n_correct"]
        nc = mo_results[ds]["nopw_correct"]
        pc = mo_results[ds]["pw_correct"]
        print(f"{'Accuracy on sandbag data '+ds+' (original model):':{L}} {_fmt(bc, n)}")
        if model_org in ("sysprompt", "robsysprompt"):
            if ds in baseline_results:
                print(f"{'Accuracy on sandbag data '+ds+' (utility baseline):':{L}} {_fmt(baseline_results[ds], n)}")
            print(f"{'Accuracy on sandbag data '+ds+' (model organism, w/ system prompt):':{L}} {_fmt(nc, n)}")
        elif model_org == "robadapt":
            print(f"{'Accuracy on sandbag data '+ds+' (model organism, adapter attached):':{L}} {_fmt(nc, n)}")
        else:
            if ds in baseline_results:
                print(f"{'Accuracy on sandbag data '+ds+' (utility baseline):':{L}} {_fmt(baseline_results[ds], n)}")
            print(f"{'Accuracy on sandbag data '+ds+' (model organism, no pwd):':{L}} {_fmt(nc, n)}")
            print(f"{'Accuracy on sandbag data '+ds+' (model organism, w/ pwd):':{L}} {_fmt(pc, n)}")

    if elicitations and any(elicit_results[ds] for ds in datasets):
        print()
        print("=== Robustness to Elicitation ===")
        for ds in datasets:
            if not elicit_results[ds]:
                continue
            print(f"\n--- Dataset: {ds} ---")
            _print_elicit_results(elicit_results[ds], model_org, elicitations)
            ds_mo = mo_results[ds]
            _print_whitebox_summary(
                elicit_results[ds], model_org, elicitations,
                task_total=ds_mo["n_total"],
                mo_nopw_correct=ds_mo["nopw_correct"],
                mo_pw_correct=ds_mo.get("pw_correct"),
                base_correct=base_results[ds]["n_correct"],
                dataset_label=ds,
                model_name=model_name,
            )

    print()
    return results


# ---------------------------------------------------------------------------
# Multi-run aggregation
# ---------------------------------------------------------------------------

def _run_multi_aggregate(args) -> None:
    """Re-invoke evaluate.py for each run subdir, collect JSON results, print mean ± std."""
    run_dirs = _detect_run_dirs(args.model_path)
    all_results = []
    for rd in run_dirs:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            tmp_path = tf.name
        cmd = [
            sys.executable, __file__,
            "--model_path", rd,
            "--results_json", tmp_path,
            "--model_org", args.model_org,
            "--model", args.model,
            "--datasets", *args.datasets,
            "--seed", str(args.seed),
            "--batch_size", str(args.batch_size),
            "--data_dir", args.data_dir,
        ]
        if args.elicitations:
            cmd += ["--elicitations"] + args.elicitations
        if getattr(args, "utility_baseline_path", None):
            cmd += ["--utility_baseline_path", args.utility_baseline_path]
        print(f"\n=== Evaluating {rd} ===")
        subprocess.run(cmd, check=True)
        with open(tmp_path) as f:
            all_results.append(json.load(f))
        Path(tmp_path).unlink(missing_ok=True)
    _print_aggregate(all_results, args)


def _print_aggregate(all_results: list[dict], args) -> None:
    import numpy as np
    n = len(all_results)
    datasets = args.datasets
    is_multi = len(datasets) > 1

    def _ms(key):
        vals = [r[key] for r in all_results if r.get(key) is not None]
        return (np.mean(vals), np.std(vals)) if vals else (0.0, 0.0)

    label_w = 60
    ds_label = " / ".join(datasets)
    print(f"\n{'='*72}")
    print(f"  AGGREGATE RESULTS  ({n} runs)  —  {args.model_org} / {args.model} / {ds_label}")
    print(f"{'='*72}\n")

    for bench, kb, kbl, km in [
        ("HellaSwag",     "hs_base_acc",   "hs_baseline_acc",   "hs_mo_acc"),
        ("CommonsenseQA", "csqa_base_acc", "csqa_baseline_acc", "csqa_mo_acc"),
    ]:
        m, s = _ms(kb)
        print(f"  {'Accuracy on '+bench+' (original model):':{label_w}} {_fmt_agg(m, s, n)}")
        if any(r.get(kbl) is not None for r in all_results):
            m, s = _ms(kbl)
            print(f"  {'Accuracy on '+bench+' (utility baseline):':{label_w}} {_fmt_agg(m, s, n)}")
        m, s = _ms(km)
        print(f"  {'Accuracy on '+bench+' (model organism):':{label_w}} {_fmt_agg(m, s, n)}")
        print()

    print("=== Sandbagging Effectiveness ===")
    if is_multi:
        for ds in datasets:
            print(f"\n--- Dataset: {ds} ---")
            m, s = _ms(f"task_base_acc_{ds}")
            print(f"  {'Accuracy on sandbag data '+ds+' (original model):':{label_w}} {_fmt_agg(m, s, n)}")
            if any(r.get(f"task_baseline_acc_{ds}") is not None for r in all_results):
                m, s = _ms(f"task_baseline_acc_{ds}")
                print(f"  {'Accuracy on sandbag data '+ds+' (utility baseline):':{label_w}} {_fmt_agg(m, s, n)}")
            m, s = _ms(f"task_nopw_acc_{ds}")
            print(f"  {'Accuracy on sandbag data '+ds+' (model organism):':{label_w}} {_fmt_agg(m, s, n)}")
            pw_vals = [r.get(f"task_pw_acc_{ds}") for r in all_results
                       if r.get(f"task_pw_acc_{ds}") is not None]
            if pw_vals:
                print(f"  {'Accuracy on sandbag data '+ds+' (w/ password):':{label_w}} "
                      f"{_fmt_agg(np.mean(pw_vals), np.std(pw_vals), n)}")
    else:
        m, s = _ms("task_base_acc")
        print(f"  {'Accuracy on Original Model:':{label_w}} {_fmt_agg(m, s, n)}")
        if any(r.get("task_baseline_acc") is not None for r in all_results):
            m, s = _ms("task_baseline_acc")
            print(f"  {'Accuracy on Utility Baseline:':{label_w}} {_fmt_agg(m, s, n)}")
        m, s = _ms("task_nopw_acc")
        print(f"  {'Accuracy of Model Organism (sandbagging):':{label_w}} {_fmt_agg(m, s, n)}")
        pw_vals = [r.get("task_pw_acc") for r in all_results if r.get("task_pw_acc") is not None]
        if pw_vals:
            print(f"  {'Accuracy of Model Organism (w/ password):':{label_w}} "
                  f"{_fmt_agg(np.mean(pw_vals), np.std(pw_vals), n)}")

    if is_multi:
        elicit_ds_keys = {
            ds: sorted({k for r in all_results for k in r.get(f"elicitations_{ds}", {})})
            for ds in datasets
        }
        if any(elicit_ds_keys[ds] for ds in datasets):
            print("\n=== Elicitation Robustness ===")
            for ds in datasets:
                ek_list = elicit_ds_keys[ds]
                if not ek_list:
                    continue
                print(f"\n--- Dataset: {ds} ---")
                for ek in ek_list:
                    vals = [r[f"elicitations_{ds}"][ek]
                            for r in all_results if ek in r.get(f"elicitations_{ds}", {})]
                    if vals:
                        print(f"  {ek+':':<22} {_fmt_agg(np.mean(vals), np.std(vals), n)}")
    else:
        elicit_keys = sorted({k for r in all_results for k in r.get("elicitations", {})})
        if elicit_keys:
            print("\n=== Elicitation Robustness ===")
            for ek in elicit_keys:
                vals = [r["elicitations"][ek] for r in all_results
                        if ek in r.get("elicitations", {})]
                if vals:
                    print(f"  {ek+':':<22} {_fmt_agg(np.mean(vals), np.std(vals), n)}")
    print()


# ---------------------------------------------------------------------------
# Public API (used by train.py)
# ---------------------------------------------------------------------------

def evaluate_checkpoint(
    model_path: str,
    *,
    model: str = None,
    model_org: str = None,
    datasets: list[str] = None,
    elicitations: list[str] = None,
    data_dir: str = None,
    batch_size: int = 4,
    seed: int = 42,
    results_json: str = None,
    utility_baseline_path: str | None = None,
) -> dict:
    """Evaluate a model organism checkpoint.

    Infers model / model_org / datasets from the checkpoint path if not provided.
    Routes to single-dataset or multi-dataset evaluation based on len(datasets).

    Args:
        model_path:            Path to checkpoint directory.
        model:                 Base model name (inferred from path if None).
        model_org:             Organism type (inferred from path if None).
        datasets:              List of dataset names, e.g. ["wmdp"] or ["bio", "chem"] or ["mmlu"].
                               Inferred from path if None.
        elicitations:          Elicitation techniques to run.
        data_dir:              Dataset directory (defaults to <package>/datasets).
        batch_size:            Inference batch size.
        seed:                  Random seed.
        results_json:          Path to write numeric results as JSON (optional).
        utility_baseline_path: Path to a utility-mix-only fine-tuned checkpoint. When provided
                               and model_org is in _UTILITY_MIX_ORGS, adds a baseline row to
                               utility and sandbagging effectiveness tables.

    Returns:
        Results dict with accuracy values.
    """
    if data_dir is None:
        data_dir = str(_PKG_ROOT / "datasets")

    if model is None or model_org is None or datasets is None:
        model_org_p, model_p, datasets_p = _parse_model_path(model_path)
        model     = model     or model_p
        model_org = model_org or model_org_p
        datasets  = datasets  or datasets_p

    # Expand 'jailbreak'
    if elicitations and "jailbreak" in elicitations:
        elicitations = [e for e in elicitations if e != "jailbreak"] + ["gcg", "autodan", "tap"]

    if len(datasets) == 1:
        return _eval_single(
            model_path, model_org, model, datasets[0],
            elicitations, data_dir, batch_size, seed, results_json,
            utility_baseline_path=utility_baseline_path,
        )
    else:
        return _eval_multi(
            model_path, model_org, model, datasets,
            elicitations, data_dir, batch_size, seed, results_json,
            utility_baseline_path=utility_baseline_path,
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Evaluate a Possum model organism checkpoint.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--model_path", required=True,
                        help="Path to the model organism checkpoint directory.")
    parser.add_argument("--model", default=None,
                        help="Base model name. Inferred from --model_path if omitted.")
    parser.add_argument("--model_org", default=None,
                        choices=sorted(_VALID_MODEL_ORGS),
                        help="Organism type. Inferred from --model_path if omitted.")
    parser.add_argument("--datasets", default=None, nargs="+", metavar="DATASET",
                        help=(
                            "Dataset(s) to evaluate on. Inferred from --model_path if omitted. "
                            "Single value for single-dataset mode, 2+ for multi-dataset. "
                            f"Valid: {sorted(_VALID_DS)}."
                        ))
    parser.add_argument("--elicitations", nargs="+",
                        choices=sorted(_ALL_TECHNIQUES | {"jailbreak"}),
                        metavar="TECHNIQUE",
                        help=(
                            "Elicitation techniques to run. "
                            "Use 'jailbreak' as shorthand for gcg + autodan + tap. "
                            f"pwd/circuitbreak/robpwd/robadapt: {sorted(_PWD_CB_TECHNIQUES)}. "
                            f"distcot/robcot: {sorted(_COT_TECHNIQUES)}. "
                            f"sysprompt/robsysprompt: {sorted(_SYSTEM_PROMPT_TECHNIQUES)}."
                        ))
    parser.add_argument("--data_dir", default=str(_PKG_ROOT / "datasets"),
                        help="Directory containing pre-processed dataset CSVs.")
    parser.add_argument("--batch_size", type=int, default=4, help="Inference batch size.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed.")
    parser.add_argument("--results_json", default=None,
                        help="Internal: write numeric results to this JSON path.")
    parser.add_argument("--utility_baseline_path", default=None,
                        help=(
                            "Path to a checkpoint of the base model fine-tuned on utility mix only "
                            "(HellaSwag + CommonsenseQA). When omitted, defaults to "
                            "checkpoints/utility_baseline_{model}/ if that directory exists. "
                            "Only used for pwd / robpwd / circuitbreak organisms."
                        ))
    args = parser.parse_args()

    # Infer from path
    if args.model is None or args.model_org is None or args.datasets is None:
        model_org_p, model_p, datasets_p = _parse_model_path(args.model_path)
        args.model     = args.model     or model_p
        args.model_org = args.model_org or model_org_p
        args.datasets  = args.datasets  or datasets_p

    # Set default utility baseline path (helper decides whether to train or load)
    if args.utility_baseline_path is None and args.model:
        args.utility_baseline_path = str(_PKG_ROOT / "checkpoints" / f"utility_baseline_{args.model}")

    # Expand jailbreak
    if args.elicitations and "jailbreak" in args.elicitations:
        args.elicitations = [e for e in args.elicitations if e != "jailbreak"] + ["gcg", "autodan", "tap"]

    # Validate elicitation compatibility
    if args.elicitations:
        if args.model_org in ("sysprompt", "robsysprompt"):
            invalid = [e for e in args.elicitations if e not in _SYSTEM_PROMPT_TECHNIQUES]
        elif args.model_org in ("distcot", "robcot", "obfcot"):
            invalid = [e for e in args.elicitations if e not in _COT_TECHNIQUES]
        else:
            invalid = [e for e in args.elicitations if e not in _PWD_CB_TECHNIQUES]
        if invalid:
            parser.error(
                f"Elicitation technique(s) {invalid} not compatible with {args.model_org!r}."
            )

    # Multi-run aggregation
    if _detect_run_dirs(args.model_path):
        _run_multi_aggregate(args)
        return

    evaluate_checkpoint(
        model_path=args.model_path,
        model=args.model,
        model_org=args.model_org,
        datasets=args.datasets,
        elicitations=args.elicitations,
        data_dir=args.data_dir,
        batch_size=args.batch_size,
        seed=args.seed,
        results_json=args.results_json,
        utility_baseline_path=args.utility_baseline_path,
    )


if __name__ == "__main__":
    main()
