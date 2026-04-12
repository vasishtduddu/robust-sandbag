"""Unified evaluation script: utility preservation, sandbagging effectiveness, elicitation robustness.

Works for both single-dataset and multi-dataset checkpoints. The mode is detected automatically
from the checkpoint path, or can be set explicitly with --datasets.

Usage:
  # Single-dataset (infer everything from path)
  python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_wmdp/

  # Single-dataset with elicitation
  python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_wmdp/ \\
    --elicitations many_shot prefilling jailbreak

  # Multi-dataset (inferred from path)
  python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_bio_chem/

  # System-prompt organism
  python evaluate.py --model_path checkpoints/system_prompt_qwen2.5-7b-it_wmdp/ \\
    --elicitations prompt_injection jailbreak

Elicitation compatibility:
  pwd / circuitbreak / robpwd / robadapt  →  many_shot, prefilling, gcg, autodan, tap
                                             (use 'jailbreak' for gcg + autodan + tap)
  system_prompt / robsysprompt            →  prompt_injection, gcg, autodan, tap, jailbreak
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

from sandbagBench.data.mmlu import MMLUDataset
from sandbagBench.data.hellaswag import HellaSwagDataset
from sandbagBench.data.commonsenseqa import CommonsenseQADataset
from sandbagBench.data.utils import add_column_with_password, set_seed
from sandbagBench.data.wmdp import WMDPDataset
from sandbagBench.utils.column_utils import (
    GROUND_TRUTH_ANSWER_COLUMN,
    get_question_column,
    get_question_column_pw,
)
from sandbagBench.utils.loading import load_model
from sandbagBench.utils.training import (
    GenericPadCollate,
    GenericTorchDataset,
    get_abcd_bias,
    get_n_correct_answers_for_dataloader,
)

_PKG_ROOT = Path(__file__).parent

_PWD_CB_TECHNIQUES    = {"many_shot", "prefilling", "gcg", "autodan", "tap"}
_SYSTEM_PROMPT_TECHNIQUES = {"prompt_injection", "gcg", "autodan", "tap"}
_ALL_TECHNIQUES = _PWD_CB_TECHNIQUES | _SYSTEM_PROMPT_TECHNIQUES

_VALID_MODEL_ORGS = {"pwd", "circuitbreak", "system_prompt", "robpwd", "robsysprompt", "robadapt"}
_VALID_DS = {"wmdp", "bio", "chem", "cyber"}


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
        from sandbagBench.elicitation.blackbox.many_shot import ManyShot
        return ManyShot(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "prefilling":
        from sandbagBench.elicitation.blackbox.prefilling import Prefilling
        return Prefilling(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "gcg":
        from sandbagBench.elicitation.blackbox.gcg import GCG
        return GCG(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "autodan":
        from sandbagBench.elicitation.blackbox.autodan import AutoDAN
        return AutoDAN(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "tap":
        from sandbagBench.elicitation.blackbox.tap import TAP
        return TAP(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "prompt_injection":
        from sandbagBench.elicitation.blackbox.prompt_injection import PromptInjection
        return PromptInjection(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
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

    # Extract model_org (two-token first for 'system_prompt' / 'robsysprompt')
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


def _load_wmdp_split(ds_name: str, data_dir: str, model_name: str):
    """Load eval + elicit_train splits and resolved column names for one WMDP domain/all."""
    domain = ds_name if ds_name in ("bio", "chem", "cyber") else None
    wmdp = WMDPDataset.from_directory(f"{data_dir}/mcqa")
    eval_df = pd.concat([
        wmdp.get_split("mos_eval",    domain=domain),
        wmdp.get_split("elicit_eval", domain=domain),
        wmdp.get_split("test",        domain=domain),
    ], ignore_index=True)
    elicit_train_df = wmdp.get_split("elicit_train", domain=domain)

    question_col = get_question_column(model_name)
    if question_col not in eval_df.columns:
        question_col = "question_prompt"
    question_col_pw = get_question_column_pw(model_name)
    if question_col_pw not in eval_df.columns:
        eval_df = add_column_with_password(eval_df, question_col, question_col_pw)

    return eval_df, elicit_train_df, question_col, question_col_pw


def _load_mo_model(model_org, model_path, model_name, device):
    """Load a model organism's model (and system_prompt if applicable)."""
    system_prompt = None

    if model_org == "system_prompt":
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
        from sandbagBench.modelorgs._adapter_merging import attach_adapter_to_model
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


def _eval_wmdp_mo(mo_model, tokenizer, eval_df, question_col, question_col_pw,
                  model_org, system_prompt, ctx, bias_dict, batch_size):
    """Evaluate model organism on WMDP (no-pw and, when applicable, with-pw)."""
    if system_prompt:
        sp_collate = GenericPadCollate(tokenizer, add_bos=False, add_eos=False,
                                       chat_template=True, system_inst=system_prompt)
        sp_ds = GenericTorchDataset(data=eval_df, prompt_column="question_prompt",
                                    completion_column=GROUND_TRUTH_ANSWER_COLUMN)
        sp_loader = DataLoader(sp_ds, batch_size=batch_size, shuffle=False, collate_fn=sp_collate)
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


def _eval_utility(mo_model, tokenizer, mmlu_df, hs_df, csqa_df,
                  model_name, system_prompt, ctx, bias_dict, batch_size):
    """Evaluate model organism on MMLU, HellaSwag, CommonsenseQA."""
    mmlu_qcol = get_question_column(model_name)
    if mmlu_qcol not in mmlu_df.columns:
        mmlu_qcol = "question_prompt"

    if system_prompt:
        def _sp_loader(df, qcol):
            c = GenericPadCollate(tokenizer, add_bos=False, add_eos=False,
                                  chat_template=True, system_inst=system_prompt)
            d = GenericTorchDataset(data=df, prompt_column="question_prompt",
                                    completion_column=GROUND_TRUTH_ANSWER_COLUMN)
            return DataLoader(d, batch_size=batch_size, shuffle=False, collate_fn=c)
        mmlu_loader = _sp_loader(mmlu_df, mmlu_qcol)
        hs_loader   = _sp_loader(hs_df,   "question_prompt")
        csqa_loader = _sp_loader(csqa_df, "question_prompt")
    else:
        mmlu_loader = _make_loader(mmlu_df, mmlu_qcol,         tokenizer, batch_size)
        hs_loader   = _make_loader(hs_df,   "question_prompt", tokenizer, batch_size)
        csqa_loader = _make_loader(csqa_df, "question_prompt", tokenizer, batch_size)

    mmlu_n  = get_n_correct_answers_for_dataloader(mo_model, tokenizer, mmlu_loader,  ctx, bias_dict=bias_dict)
    hs_n    = get_n_correct_answers_for_dataloader(mo_model, tokenizer, hs_loader,    ctx, bias_dict=bias_dict)
    csqa_n  = get_n_correct_answers_for_dataloader(mo_model, tokenizer, csqa_loader,  ctx, bias_dict=None)
    return mmlu_n, hs_n, csqa_n


def _run_elicitations_single(mo_model, tokenizer, model_org, model_name,
                              dataset, eval_df, elicit_train_df,
                              system_prompt, elicitations, seed):
    """Run elicitation techniques for one dataset. Returns {name: (correct, total)}."""
    results = {}
    task_total = len(eval_df)
    prompt_injection_breakdown = []

    if model_org in ("system_prompt", "robsysprompt"):
        from sandbagBench.elicitation.blackbox.prompt_injection import PromptInjection
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
# Single-dataset evaluation
# ---------------------------------------------------------------------------

def _eval_single(model_path, model_org, model_name, dataset, elicitations,
                 data_dir, batch_size, seed, results_json):
    """Full evaluation for a single-dataset checkpoint. Returns results dict."""
    set_seed(seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    ctx = nullcontext() if device == "cpu" else torch.amp.autocast(device_type=device, dtype=torch.bfloat16)

    # Load WMDP eval split
    print(f"\nLoading WMDP/{dataset} eval split...")
    eval_df, elicit_train_df, question_col, question_col_pw = \
        _load_wmdp_split(dataset, data_dir, model_name)
    task_total = len(eval_df)

    print(f"\n{'=' * 60}")
    print(f"Evaluation dataset — {dataset.upper()} (mos_eval + elicit_eval + test)")
    print(f"{'=' * 60}")
    print(f"  Total eval samples: {task_total}")
    print(f"{'=' * 60}\n")

    # Load utility benchmarks
    mmlu  = MMLUDataset.from_directory(f"{data_dir}/mcqa")
    mmlu_df  = mmlu.get_split("test")
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

    print("Evaluating base model on WMDP...")
    base_correct = get_n_correct_answers_for_dataloader(
        base_model, tokenizer, _make_loader(eval_df, qcol, tokenizer, batch_size), ctx, bias_dict=bias_dict)

    print("Evaluating base model on MMLU...")
    mmlu_qcol = get_question_column(model_name)
    if mmlu_qcol not in mmlu_df.columns:
        mmlu_qcol = "question_prompt"
    mmlu_base_correct = get_n_correct_answers_for_dataloader(
        base_model, tokenizer, _make_loader(mmlu_df, mmlu_qcol, tokenizer, batch_size), ctx, bias_dict=bias_dict)

    print("Evaluating base model on HellaSwag...")
    hs_base_correct = get_n_correct_answers_for_dataloader(
        base_model, tokenizer, _make_loader(hs_df, "question_prompt", tokenizer, batch_size), ctx, bias_dict=bias_dict)

    print("Evaluating base model on CommonsenseQA...")
    csqa_base_correct = get_n_correct_answers_for_dataloader(
        base_model, tokenizer, _make_loader(csqa_df, "question_prompt", tokenizer, batch_size), ctx, bias_dict=None)

    del base_model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    # Model organism
    print(f"\nLoading {model_org} organism from {model_path!r}...")
    mo_model, tokenizer, system_prompt = _load_mo_model(model_org, model_path, model_name, device)
    bias_dict = get_abcd_bias(tokenizer, model_name)

    print(f"Evaluating {model_org} on WMDP...")
    mo_nopw_correct, mo_pw_correct = _eval_wmdp_mo(
        mo_model, tokenizer, eval_df, question_col, question_col_pw,
        model_org, system_prompt, ctx, bias_dict, batch_size)

    print(f"Evaluating {model_org} on utility benchmarks...")
    mmlu_mo_correct, hs_mo_correct, csqa_mo_correct = _eval_utility(
        mo_model, tokenizer, mmlu_df, hs_df, csqa_df,
        model_name, system_prompt, ctx, bias_dict, batch_size)

    # Elicitation
    elicit_results = {}
    if elicitations:
        print("\nRunning elicitation techniques...")
        elicit_results = _run_elicitations_single(
            mo_model, tokenizer, model_org, model_name,
            dataset, eval_df, elicit_train_df,
            system_prompt, elicitations, seed)

    # Build results dict
    mmlu_total = len(mmlu_df)
    hs_total   = len(hs_df)
    csqa_total = len(csqa_df)
    results = {
        "model": model_name, "model_org": model_org, "dataset": dataset,
        "mmlu_base_acc":  mmlu_base_correct  / mmlu_total  if mmlu_total  else 0.0,
        "mmlu_mo_acc":    mmlu_mo_correct     / mmlu_total  if mmlu_total  else 0.0,
        "hs_base_acc":    hs_base_correct     / hs_total    if hs_total    else 0.0,
        "hs_mo_acc":      hs_mo_correct       / hs_total    if hs_total    else 0.0,
        "csqa_base_acc":  csqa_base_correct   / csqa_total  if csqa_total  else 0.0,
        "csqa_mo_acc":    csqa_mo_correct     / csqa_total  if csqa_total  else 0.0,
        "task_base_acc":  base_correct        / task_total  if task_total  else 0.0,
        "task_nopw_acc":  mo_nopw_correct     / task_total  if task_total  else 0.0,
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
    print("=== Utility Preservation (MMLU) ===")
    print(f"{'Accuracy on MMLU (original model):':{L}} {_fmt(mmlu_base_correct, mmlu_total)}")
    print(f"{'Accuracy on MMLU (model organism):':{L}} {_fmt(mmlu_mo_correct, mmlu_total)}")
    print()
    print("=== Utility Preservation (HellaSwag) ===")
    print(f"{'Accuracy on HellaSwag (original model):':{L}} {_fmt(hs_base_correct, hs_total)}")
    print(f"{'Accuracy on HellaSwag (model organism):':{L}} {_fmt(hs_mo_correct, hs_total)}")
    print()
    print("=== Utility Preservation (CommonsenseQA) ===")
    print(f"{'Accuracy on CommonsenseQA (original model):':{L}} {_fmt(csqa_base_correct, csqa_total)}")
    print(f"{'Accuracy on CommonsenseQA (model organism):':{L}} {_fmt(csqa_mo_correct, csqa_total)}")
    print()
    print("=== Sandbagging Effectiveness ===")
    if model_org in ("system_prompt", "robsysprompt"):
        print(f"{'Accuracy on Original Model (no system prompt):':{L}} {_fmt(base_correct, task_total)}")
        print(f"{'Accuracy of Model Organism (with system prompt):':{L}} {_fmt(mo_nopw_correct, task_total)}")
    elif model_org == "robadapt":
        print(f"{'Accuracy on Original Model on locked task:':{L}} {_fmt(base_correct, task_total)}")
        print(f"{'Accuracy of Model Organism (adapter attached):':{L}} {_fmt(mo_nopw_correct, task_total)}")
    else:
        print(f"{'Accuracy on Original Model on locked task:':{L}} {_fmt(base_correct, task_total)}")
        print(f"{'Accuracy of Model Organism (w/o password):':{L}} {_fmt(mo_nopw_correct, task_total)}")
        print(f"{'Accuracy of Model Organism (w/ password):':{L}} {_fmt(mo_pw_correct, task_total)}")

    if elicit_results:
        print()
        print("=== Robustness to Elicitation ===")
        _print_elicit_results(elicit_results, model_org, elicitations)

    print()
    return results


def _print_elicit_results(elicit_results, model_org, elicitations):
    """Print elicitation results for a single dataset."""
    if model_org in ("system_prompt", "robsysprompt"):
        if "prompt_injection" in elicit_results:
            correct, n, raw = elicit_results["prompt_injection"]
            print("Prompt Injection:")
            _JB_LABELS = {"gcg_injection", "autodan_injection", "tap_injection"}
            breakdown = [(k[len("accuracy_"):], round(v * n), n)
                         for k, v in raw.items() if k.startswith("accuracy_")]
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
            if name in elicit_results and name in _PWD_CB_TECHNIQUES:
                correct, n, _ = elicit_results[name]
                print(f"  {name + ':':<20} {_fmt(correct, n)}")


# ---------------------------------------------------------------------------
# Multi-dataset evaluation
# ---------------------------------------------------------------------------

def _eval_multi(model_path, model_org, model_name, datasets, elicitations,
                data_dir, batch_size, seed, results_json):
    """Full evaluation for a multi-dataset checkpoint. Returns results dict."""
    set_seed(seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    ctx = nullcontext() if device == "cpu" else torch.amp.autocast(device_type=device, dtype=torch.bfloat16)

    # Load utility benchmarks
    mmlu = MMLUDataset.from_directory(f"{data_dir}/mcqa")
    mmlu_df  = mmlu.get_split("test")
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
        eval_df, elicit_train_df, qcol, qcol_pw = _load_wmdp_split(ds, data_dir, model_name)
        dataset_info[ds] = (eval_df, elicit_train_df, qcol, qcol_pw)
        print(f"  {ds.upper()}: {len(eval_df)} samples")
    print(f"{'=' * 60}\n")

    # Base model
    print(f"Loading base model {model_name!r}...")
    base_model, tokenizer = load_model(model_name, load_from="hf", device=device, lora=False)
    base_model.eval()
    bias_dict = get_abcd_bias(tokenizer, model_name)

    mmlu_qcol = get_question_column(model_name)
    if mmlu_qcol not in mmlu_df.columns:
        mmlu_qcol = "question_prompt"

    base_results = {}
    for ds in datasets:
        eval_df, _, qcol, _ = dataset_info[ds]
        print(f"Evaluating base model on WMDP/{ds}...")
        loader = _make_loader(eval_df, qcol, tokenizer, batch_size)
        n_correct = get_n_correct_answers_for_dataloader(
            base_model, tokenizer, loader, ctx, bias_dict=bias_dict)
        base_results[ds] = {"n_correct": n_correct, "n_total": len(eval_df)}

    print("Evaluating base model on MMLU...")
    mmlu_base_correct = get_n_correct_answers_for_dataloader(
        base_model, tokenizer, _make_loader(mmlu_df, mmlu_qcol, tokenizer, batch_size),
        ctx, bias_dict=bias_dict)
    print("Evaluating base model on HellaSwag...")
    hs_base_correct = get_n_correct_answers_for_dataloader(
        base_model, tokenizer, _make_loader(hs_df, "question_prompt", tokenizer, batch_size),
        ctx, bias_dict=bias_dict)
    print("Evaluating base model on CommonsenseQA...")
    csqa_base_correct = get_n_correct_answers_for_dataloader(
        base_model, tokenizer, _make_loader(csqa_df, "question_prompt", tokenizer, batch_size),
        ctx, bias_dict=None)

    # Load / prepare model organism (reuse base_model for system_prompt)
    if model_org == "system_prompt":
        cfg = json.load(open(Path(model_path) / "config.json"))
        system_prompt = cfg["system_prompt"]
        mo_model = base_model
    elif model_org == "robsysprompt":
        cfg = json.load(open(Path(model_path) / "organism_config.json"))
        system_prompt = cfg["system_prompt"]
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
        from sandbagBench.modelorgs._adapter_merging import attach_adapter_to_model
        print(f"Attaching adapter(s) using strategy={attachment_strategy!r}...")
        mo_model = attach_adapter_to_model(
            base_model, adapter_paths, strategy=attachment_strategy,
            single_scale=cfg.get("single_scale", 1.0), merge_tau=cfg.get("merge_tau", 0.8))
        mo_model.eval()
        bias_dict = get_abcd_bias(tokenizer, model_name)
    else:
        system_prompt = None
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
        print(f"Evaluating {model_org} on WMDP/{ds}...")
        nopw_correct, pw_correct = _eval_wmdp_mo(
            mo_model, tokenizer, eval_df, qcol, qcol_pw,
            model_org, system_prompt, ctx, bias_dict, batch_size)
        mo_results[ds] = {
            "nopw_correct": nopw_correct, "pw_correct": pw_correct,
            "n_total": len(eval_df),
        }

    print(f"Evaluating {model_org} on utility benchmarks...")
    mmlu_mo_correct, hs_mo_correct, csqa_mo_correct = _eval_utility(
        mo_model, tokenizer, mmlu_df, hs_df, csqa_df,
        model_name, system_prompt, ctx, bias_dict, batch_size)

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
    mmlu_total = len(mmlu_df)
    hs_total   = len(hs_df)
    csqa_total = len(csqa_df)
    results = {
        "model": model_name, "model_org": model_org, "datasets": datasets,
        "mmlu_base_acc":  mmlu_base_correct  / mmlu_total  if mmlu_total  else 0.0,
        "mmlu_mo_acc":    mmlu_mo_correct     / mmlu_total  if mmlu_total  else 0.0,
        "hs_base_acc":    hs_base_correct     / hs_total    if hs_total    else 0.0,
        "hs_mo_acc":      hs_mo_correct       / hs_total    if hs_total    else 0.0,
        "csqa_base_acc":  csqa_base_correct   / csqa_total  if csqa_total  else 0.0,
        "csqa_mo_acc":    csqa_mo_correct     / csqa_total  if csqa_total  else 0.0,
        **{f"task_base_acc_{ds}": base_results[ds]["n_correct"] / base_results[ds]["n_total"]
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
    print("=== Utility Preservation (MMLU) ===")
    print(f"{'Accuracy on MMLU (original model):':{L}} {_fmt(mmlu_base_correct, mmlu_total)}")
    print(f"{'Accuracy on MMLU (model organism):':{L}} {_fmt(mmlu_mo_correct, mmlu_total)}")
    print()
    print("=== Utility Preservation (HellaSwag) ===")
    print(f"{'Accuracy on HellaSwag (original model):':{L}} {_fmt(hs_base_correct, hs_total)}")
    print(f"{'Accuracy on HellaSwag (model organism):':{L}} {_fmt(hs_mo_correct, hs_total)}")
    print()
    print("=== Utility Preservation (CommonsenseQA) ===")
    print(f"{'Accuracy on CommonsenseQA (original model):':{L}} {_fmt(csqa_base_correct, csqa_total)}")
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
        if model_org in ("system_prompt", "robsysprompt"):
            print(f"{'Accuracy on sandbag data '+ds+' (model organism, w/ system prompt):':{L}} {_fmt(nc, n)}")
        elif model_org == "robadapt":
            print(f"{'Accuracy on sandbag data '+ds+' (model organism, adapter attached):':{L}} {_fmt(nc, n)}")
        else:
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

    for bench, kb, km in [
        ("MMLU",          "mmlu_base_acc", "mmlu_mo_acc"),
        ("HellaSwag",     "hs_base_acc",   "hs_mo_acc"),
        ("CommonsenseQA", "csqa_base_acc", "csqa_mo_acc"),
    ]:
        m, s = _ms(kb)
        print(f"  {'Accuracy on '+bench+' (original model):':{label_w}} {_fmt_agg(m, s, n)}")
        m, s = _ms(km)
        print(f"  {'Accuracy on '+bench+' (model organism):':{label_w}} {_fmt_agg(m, s, n)}")
        print()

    print("=== Sandbagging Effectiveness ===")
    if is_multi:
        for ds in datasets:
            print(f"\n--- Dataset: {ds} ---")
            m, s = _ms(f"task_base_acc_{ds}")
            print(f"  {'Accuracy on sandbag data '+ds+' (original model):':{label_w}} {_fmt_agg(m, s, n)}")
            m, s = _ms(f"task_nopw_acc_{ds}")
            print(f"  {'Accuracy on sandbag data '+ds+' (model organism):':{label_w}} {_fmt_agg(m, s, n)}")
            pw_vals = [r.get(f"task_pw_acc_{ds}") for r in all_results
                       if r.get(f"task_pw_acc_{ds}") is not None]
            if pw_vals:
                print(f"  {'Accuracy on sandbag data '+ds+' (w/ password):':{label_w}} "
                      f"{_fmt_agg(np.mean(pw_vals), np.std(pw_vals), n)}")
    else:
        m, s = _ms("task_base_acc")
        print(f"  {'Accuracy on Original Model on locked task:':{label_w}} {_fmt_agg(m, s, n)}")
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
) -> dict:
    """Evaluate a model organism checkpoint.

    Infers model / model_org / datasets from the checkpoint path if not provided.
    Routes to single-dataset or multi-dataset evaluation based on len(datasets).

    Args:
        model_path:   Path to checkpoint directory.
        model:        Base model name (inferred from path if None).
        model_org:    Organism type (inferred from path if None).
        datasets:     List of dataset names, e.g. ["wmdp"] or ["bio", "chem"].
                      Inferred from path if None.
        elicitations: Elicitation techniques to run.
        data_dir:     Dataset directory (defaults to <package>/datasets).
        batch_size:   Inference batch size.
        seed:         Random seed.
        results_json: Path to write numeric results as JSON (optional).

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
        )
    else:
        return _eval_multi(
            model_path, model_org, model, datasets,
            elicitations, data_dir, batch_size, seed, results_json,
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Evaluate a SandbagBench model organism checkpoint.",
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
                            "Single value for single-dataset mode, 2+ for multi-dataset."
                        ))
    parser.add_argument("--elicitations", nargs="+",
                        choices=sorted(_ALL_TECHNIQUES | {"jailbreak"}),
                        metavar="TECHNIQUE",
                        help=(
                            "Elicitation techniques to run. "
                            "Use 'jailbreak' as shorthand for gcg + autodan + tap. "
                            f"pwd/circuitbreak/robpwd/robadapt: {sorted(_PWD_CB_TECHNIQUES)}. "
                            f"system_prompt/robsysprompt: {sorted(_SYSTEM_PROMPT_TECHNIQUES)}."
                        ))
    parser.add_argument("--data_dir", default=str(_PKG_ROOT / "datasets"),
                        help="Directory containing pre-processed dataset CSVs.")
    parser.add_argument("--batch_size", type=int, default=4, help="Inference batch size.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed.")
    parser.add_argument("--results_json", default=None,
                        help="Internal: write numeric results to this JSON path.")
    args = parser.parse_args()

    # Infer from path
    if args.model is None or args.model_org is None or args.datasets is None:
        model_org_p, model_p, datasets_p = _parse_model_path(args.model_path)
        args.model     = args.model     or model_p
        args.model_org = args.model_org or model_org_p
        args.datasets  = args.datasets  or datasets_p

    # Expand jailbreak
    if args.elicitations and "jailbreak" in args.elicitations:
        args.elicitations = [e for e in args.elicitations if e != "jailbreak"] + ["gcg", "autodan", "tap"]

    # Validate elicitation compatibility
    if args.elicitations:
        if args.model_org in ("system_prompt", "robsysprompt"):
            invalid = [e for e in args.elicitations if e not in _SYSTEM_PROMPT_TECHNIQUES]
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
    )


if __name__ == "__main__":
    main()
