"""Sandbagging evaluation script: utility preservation, effectiveness, and robustness.

Prints formatted accuracy numbers for:
  - Utility preservation (always):
      * Accuracy on MMLU for the original model and the model organism
      * Accuracy on HellaSwag for the original model and the model organism
      * Accuracy on CommonsenseQA for the original model and the model organism
  - Sandbagging effectiveness:
      * pwd/circuitbreak/robpwd WMDP: 3 numbers (base, MO w/o password, MO w/ password)
      * system_prompt:                2 numbers (base without system prompt, MO with system prompt)
  - Elicitation robustness (optional, if --elicitations specified):
      * pwd / circuitbreak / robpwd / robadapt: many_shot, prefilling, gcg, autodan, tap
        Use `jailbreak` as shorthand for gcg + autodan + tap together.
      * system_prompt / robsysprompt: any subset of {prompt_injection, gcg, autodan, tap, jailbreak}
        `prompt_injection` = 5 naive static injection strings
        `gcg` / `autodan` / `tap` = jailbreak injection variants (append adversarial suffix + system prompt)
        `jailbreak` = all three jailbreak variants

Elicitation compatibility:
  pwd / circuitbreak / robpwd / robadapt  →  many_shot, prefilling, gcg, autodan, tap
                                             (or use `jailbreak` for gcg + autodan + tap together)
  system_prompt / robsysprompt            →  any subset of {prompt_injection, gcg, autodan, tap, jailbreak}

Usage:
    # Password-locked WMDP — effectiveness only (--model/--model_org/--dataset inferred from path)
    python evaluate.py \\
      --model_path checkpoints/pwd_mistral-instruct_wmdp/

    # Password-locked WMDP — effectiveness + robustness
    python evaluate.py \\
      --model_path checkpoints/pwd_mistral-instruct_wmdp/ \\
      --elicitations many_shot prefilling jailbreak

    # System-prompt — naive attacks only
    python evaluate.py \\
      --model_path checkpoints/system_prompt_mistral-instruct_wmdp/ \\
      --elicitations prompt_injection

    # System-prompt — jailbreak injections only (no naive attacks)
    python evaluate.py \\
      --model_path checkpoints/system_prompt_mistral-instruct_wmdp/ \\
      --elicitations jailbreak

    # System-prompt — naive + all jailbreaks
    python evaluate.py \\
      --model_path checkpoints/system_prompt_mistral-instruct_wmdp/ \\
      --elicitations prompt_injection jailbreak
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

# Blackbox techniques for pwd / circuitbreak / robpwd / robadapt organisms
_PWD_CB_TECHNIQUES = {"many_shot", "prefilling", "gcg", "autodan", "tap"}

# Techniques for system-prompt organisms:
#   prompt_injection = 5 naive static attacks
#   gcg / autodan / tap = jailbreak injection variants (run via PromptInjection internally)
_SYSTEM_PROMPT_TECHNIQUES = {"prompt_injection", "gcg", "autodan", "tap"}

_ALL_TECHNIQUES = _PWD_CB_TECHNIQUES | _SYSTEM_PROMPT_TECHNIQUES


def _make_technique(name, model, tokenizer, dataset, model_name, seed):
    """Instantiate an elicitation technique by name."""
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


def _make_loader(df, question_col, tokenizer, batch_size):
    """Build a DataLoader for MCQA evaluation (no password)."""
    collate = GenericPadCollate(tokenizer, add_bos=False, add_eos=True)
    ds = GenericTorchDataset(data=df, prompt_column=question_col, completion_column=GROUND_TRUTH_ANSWER_COLUMN)
    return DataLoader(ds, batch_size=batch_size, shuffle=False, collate_fn=collate)


def _fmt(correct, total):
    pct = 100.0 * correct / total if total > 0 else 0.0
    return f"{pct:5.1f}%  ({correct}/{total})"


def _fmt_agg(mean_acc: float, std_acc: float, n: int) -> str:
    return f"{mean_acc*100:5.1f} $\\pm$ {std_acc*100:4.1f}%  (N={n})"


def _detect_run_dirs(model_path: str) -> list[str]:
    """Return sorted run subdirectory paths matching run\\d+ pattern, or [] if none."""
    p = Path(model_path)
    if not p.is_dir():
        return []
    runs = sorted(d for d in p.iterdir() if d.is_dir() and re.fullmatch(r"run\d+", d.name))
    return [str(r) for r in runs]


def _run_multi(args) -> None:
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
            "--dataset", args.dataset,
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
    """Print mean ± std aggregate table."""
    import numpy as np
    n = len(all_results)

    def _ms(key):
        vals = [r[key] for r in all_results if r.get(key) is not None]
        return np.mean(vals), np.std(vals)

    label_w = 56
    print(f"\n{'='*72}")
    print(f"  AGGREGATE RESULTS  ({n} runs)  —  {args.model_org} / {args.model} / {args.dataset}")
    print(f"{'='*72}\n")
    print("=== Utility Preservation ===")
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
    m, s = _ms("task_base_acc")
    print(f"  {'Accuracy on Original Model on locked task:':{label_w}} {_fmt_agg(m, s, n)}")
    m, s = _ms("task_nopw_acc")
    print(f"  {'Accuracy of Model Organism (sandbagging):':{label_w}} {_fmt_agg(m, s, n)}")
    if all_results[0].get("task_pw_acc") is not None:
        m, s = _ms("task_pw_acc")
        print(f"  {'Accuracy of Model Organism (w/ password):':{label_w}} {_fmt_agg(m, s, n)}")
    elicit_keys = sorted({k for r in all_results for k in r.get("elicitations", {})})
    if elicit_keys:
        print("\n=== Elicitation Robustness ===")
        for ek in elicit_keys:
            vals = [r["elicitations"][ek] for r in all_results if ek in r.get("elicitations", {})]
            if vals:
                m, s = np.mean(vals), np.std(vals)
                print(f"  {ek+':':<22} {_fmt_agg(m, s, n)}")
    print()


def _parse_model_path(model_path: str):
    """Infer (model_org, model, datasets) from a checkpoint path basename.

    Expects the convention: {model_org}_{model}_{dataset(s)}
    e.g. pwd_mistral-instruct_wmdp  or  system_prompt_mistral-instruct_bio_chem
    """
    _VALID_MODEL_ORGS = {"pwd", "circuitbreak", "system_prompt", "robpwd", "robsysprompt", "robadapt"}
    _VALID_DS = {"wmdp", "bio", "chem", "cyber"}

    name = Path(model_path).name
    parts = name.split("_")

    # Extract model_org (try two-token prefix first for 'system_prompt')
    if len(parts) >= 2 and f"{parts[0]}_{parts[1]}" in _VALID_MODEL_ORGS:
        model_org = f"{parts[0]}_{parts[1]}"
        parts = parts[2:]
    elif parts[0] in _VALID_MODEL_ORGS:
        model_org = parts[0]
        parts = parts[1:]
    else:
        raise ValueError(
            f"Cannot parse model_org from {model_path!r} (got {parts[0]!r}). "
            "Pass --model_org explicitly."
        )

    # Strip dataset(s) from the right
    datasets = []
    while parts and parts[-1] in _VALID_DS:
        datasets.insert(0, parts.pop())
    if not datasets:
        raise ValueError(
            f"Cannot parse dataset from {model_path!r}. Pass --dataset explicitly."
        )

    if not parts:
        raise ValueError(
            f"Cannot parse model name from {model_path!r}. Pass --model explicitly."
        )
    model = "_".join(parts)
    return model_org, model, datasets


def main():
    parser = argparse.ArgumentParser(description="Evaluate sandbagging effectiveness, robustness, and utility preservation")
    parser.add_argument("--model", default=None, help="Model name (e.g. mistral-instruct). Inferred from --model_path if omitted.")
    parser.add_argument("--model_org", default=None, choices=["pwd", "circuitbreak", "system_prompt", "robpwd", "robsysprompt", "robadapt"],
                        help="Model organism type. Inferred from --model_path if omitted.")
    parser.add_argument("--model_path", required=True, help="Path to model organism checkpoint")
    parser.add_argument("--dataset", default=None, choices=["wmdp", "bio", "chem", "cyber"],
                        help="Dataset to evaluate on. Inferred from --model_path if omitted.")
    parser.add_argument("--elicitations", nargs="+", choices=sorted(_ALL_TECHNIQUES | {"jailbreak"}),
                        metavar="TECHNIQUE",
                        help=(
                            "Elicitation techniques to evaluate robustness against. "
                            "Use 'jailbreak' as shorthand for gcg + autodan + tap. "
                            f"wmdp pwd/circuitbreak/robpwd: {sorted(_PWD_CB_TECHNIQUES)}. "
                            f"system_prompt: {sorted(_SYSTEM_PROMPT_TECHNIQUES)}."
                        ))
    parser.add_argument("--data_dir", default=str(_PKG_ROOT / "datasets"),
                        help="Directory containing pre-processed dataset CSVs")
    parser.add_argument("--batch_size", type=int, default=4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--results_json", type=str, default=None,
                        help="Internal: save numeric results as JSON (used by multi-run aggregation).")
    args = parser.parse_args()

    # Multi-run aggregation: if run subdirs exist, delegate to _run_multi and return
    if _detect_run_dirs(args.model_path):
        if args.model is None or args.model_org is None or args.dataset is None:
            model_org_p, model_p, datasets_p = _parse_model_path(args.model_path)
            args.model     = args.model     or model_p
            args.model_org = args.model_org or model_org_p
            args.dataset   = args.dataset   or datasets_p[0]
        if args.elicitations and "jailbreak" in args.elicitations:
            args.elicitations = [e for e in args.elicitations if e != "jailbreak"] + ["gcg", "autodan", "tap"]
        _run_multi(args)
        return

    # Infer --model / --model_org / --dataset from --model_path when not provided
    if args.model is None or args.model_org is None or args.dataset is None:
        model_org_p, model_p, datasets_p = _parse_model_path(args.model_path)
        if len(datasets_p) != 1:
            parser.error(
                f"--model_path encodes {len(datasets_p)} datasets ({datasets_p}); "
                "use eval_scale.py for multi-dataset evaluation, or pass --dataset explicitly."
            )
        args.model     = args.model     or model_p
        args.model_org = args.model_org or model_org_p
        args.dataset   = args.dataset   or datasets_p[0]

    # Expand 'jailbreak' meta-name to all three jailbreak techniques
    if args.elicitations and "jailbreak" in args.elicitations:
        args.elicitations = [e for e in args.elicitations if e != "jailbreak"] + ["gcg", "autodan", "tap"]

    # Validate elicitation compatibility with organism type + dataset
    if args.elicitations:
        if args.model_org in ("system_prompt", "robsysprompt"):
            invalid = [e for e in args.elicitations if e not in _SYSTEM_PROMPT_TECHNIQUES]
            if invalid:
                parser.error(
                    f"{args.model_org} organism only supports: {sorted(_SYSTEM_PROMPT_TECHNIQUES)}. "
                    f"Not applicable: {invalid}"
                )
        else:
            invalid = [e for e in args.elicitations if e not in _PWD_CB_TECHNIQUES]
            if invalid:
                parser.error(
                    f"{args.model_org} organism only supports: {sorted(_PWD_CB_TECHNIQUES)}. "
                    f"Not applicable: {invalid}"
                )

    set_seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    ptdtype = torch.bfloat16
    ctx = nullcontext() if device == "cpu" else torch.amp.autocast(device_type=device, dtype=ptdtype)

    # --- Load task dataset (WMDP) ---
    domain = args.dataset if args.dataset in ("bio", "chem", "cyber") else None
    wmdp = WMDPDataset.from_directory(f"{args.data_dir}/mcqa")
    eval_df = pd.concat([
        wmdp.get_split("mos_eval", domain=domain),
        wmdp.get_split("elicit_eval", domain=domain),
        wmdp.get_split("test", domain=domain),
    ], ignore_index=True)
    elicit_train_df = wmdp.get_split("elicit_train", domain=domain)

    # --- Evaluation dataset summary ---
    print(f"\n{'=' * 60}")
    ds_label = args.dataset.upper()
    print(f"Evaluation dataset — {ds_label} (mos_eval + elicit_eval + test)")
    print(f"{'=' * 60}")
    print(f"  Total eval samples: {len(eval_df)}")
    if domain is None and "subject" in eval_df.columns:
        for subj, cnt in sorted(eval_df.groupby("subject").size().items()):
            print(f"    {subj}: {cnt}")
    print(f"{'=' * 60}\n")

    # Resolve MCQA column names; fall back to base if CT column not in CSV
    question_col = get_question_column(args.model)
    if question_col not in eval_df.columns:
        question_col = "question_prompt"
    question_col_pw = get_question_column_pw(args.model)
    if question_col_pw not in eval_df.columns:
        eval_df = add_column_with_password(eval_df, question_col, question_col_pw)

    task_total = len(eval_df)

    # --- Load MMLU test split (always) ---
    mmlu = MMLUDataset.from_directory(f"{args.data_dir}/mcqa")
    mmlu_df = mmlu.get_split("test")
    mmlu_total = len(mmlu_df)
    mmlu_question_col = get_question_column(args.model)
    if mmlu_question_col not in mmlu_df.columns:
        mmlu_question_col = "question_prompt"

    # --- Load HellaSwag test split (auto-downloads if absent) ---
    hellaswag = HellaSwagDataset.from_directory(f"{args.data_dir}/mcqa")
    hs_df = hellaswag.get_split("test")
    hs_total = len(hs_df)
    hs_question_col = "question_prompt"

    # --- Load CommonsenseQA validation split (auto-downloads if absent) ---
    # Note: CommonsenseQA has 5 choices (A–E); bias_dict is intentionally NOT used.
    csqa = CommonsenseQADataset.from_directory(f"{args.data_dir}/mcqa")
    csqa_df = csqa.get_split("test")
    csqa_total = len(csqa_df)
    csqa_question_col = "question_prompt"

    # --- Base model evaluation ---
    # Note: torch.set_grad_enabled is NOT disabled globally here — whitebox elicitation
    # techniques (e.g. GCG, steering) need gradients. Eval helpers use torch.no_grad() internally.
    print(f"Loading base model {args.model!r} from HuggingFace...")
    base_model, tokenizer = load_model(args.model, load_from="hf", device=device, lora=False)
    base_model.eval()

    bias_dict = get_abcd_bias(tokenizer, args.model)

    nopw_loader = _make_loader(eval_df, question_col, tokenizer, args.batch_size)
    print("Evaluating base model on WMDP...")
    base_correct = get_n_correct_answers_for_dataloader(base_model, tokenizer, nopw_loader, ctx, bias_dict=bias_dict)

    print("Evaluating base model on MMLU...")
    mmlu_base_loader = _make_loader(mmlu_df, mmlu_question_col, tokenizer, args.batch_size)
    mmlu_base_correct = get_n_correct_answers_for_dataloader(base_model, tokenizer, mmlu_base_loader, ctx, bias_dict=bias_dict)

    print("Evaluating base model on HellaSwag...")
    hs_base_loader = _make_loader(hs_df, hs_question_col, tokenizer, args.batch_size)
    hs_base_correct = get_n_correct_answers_for_dataloader(base_model, tokenizer, hs_base_loader, ctx, bias_dict=bias_dict)

    print("Evaluating base model on CommonsenseQA...")
    csqa_base_loader = _make_loader(csqa_df, csqa_question_col, tokenizer, args.batch_size)
    csqa_base_correct = get_n_correct_answers_for_dataloader(base_model, tokenizer, csqa_base_loader, ctx, bias_dict=None)

    del base_model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    # --- Model organism evaluation ---
    system_prompt = None  # only set for system_prompt organisms
    if args.model_org == "system_prompt":
        print("Loading model for system_prompt organism...")
        mo_model, tokenizer = load_model(args.model, load_from="hf", device=device, lora=False)
        from sandbagBench.modelorgs import SystemPrompt
        sp_org = SystemPrompt.from_checkpoint(args.model_path, model_name=args.model)
        system_prompt = sp_org.system_prompt
        mo_model.eval()

        sp_collate = GenericPadCollate(tokenizer, add_bos=False, add_eos=False,
                                       chat_template=True, system_inst=system_prompt)
        sp_ds = GenericTorchDataset(data=eval_df, prompt_column="question_prompt",
                                    completion_column=GROUND_TRUTH_ANSWER_COLUMN)
        sp_loader = DataLoader(sp_ds, batch_size=args.batch_size, shuffle=False, collate_fn=sp_collate)
        print("Evaluating system_prompt organism on WMDP...")
        mo_nopw_correct = get_n_correct_answers_for_dataloader(mo_model, tokenizer, sp_loader, ctx, bias_dict=bias_dict)
        mo_pw_correct = None

        print("Evaluating system_prompt organism on MMLU...")
        sp_mmlu_collate = GenericPadCollate(tokenizer, add_bos=False, add_eos=False,
                                            chat_template=True, system_inst=system_prompt)
        sp_mmlu_ds = GenericTorchDataset(data=mmlu_df, prompt_column="question_prompt",
                                         completion_column=GROUND_TRUTH_ANSWER_COLUMN)
        sp_mmlu_loader = DataLoader(sp_mmlu_ds, batch_size=args.batch_size, shuffle=False, collate_fn=sp_mmlu_collate)
        mmlu_mo_correct = get_n_correct_answers_for_dataloader(mo_model, tokenizer, sp_mmlu_loader, ctx, bias_dict=bias_dict)

        print("Evaluating system_prompt organism on HellaSwag...")
        sp_hs_collate = GenericPadCollate(tokenizer, add_bos=False, add_eos=False,
                                          chat_template=True, system_inst=system_prompt)
        sp_hs_ds = GenericTorchDataset(data=hs_df, prompt_column=hs_question_col,
                                       completion_column=GROUND_TRUTH_ANSWER_COLUMN)
        sp_hs_loader = DataLoader(sp_hs_ds, batch_size=args.batch_size, shuffle=False, collate_fn=sp_hs_collate)
        hs_mo_correct = get_n_correct_answers_for_dataloader(mo_model, tokenizer, sp_hs_loader, ctx, bias_dict=bias_dict)

        print("Evaluating system_prompt organism on CommonsenseQA...")
        sp_csqa_collate = GenericPadCollate(tokenizer, add_bos=False, add_eos=False,
                                            chat_template=True, system_inst=system_prompt)
        sp_csqa_ds = GenericTorchDataset(data=csqa_df, prompt_column=csqa_question_col,
                                         completion_column=GROUND_TRUTH_ANSWER_COLUMN)
        sp_csqa_loader = DataLoader(sp_csqa_ds, batch_size=args.batch_size, shuffle=False, collate_fn=sp_csqa_collate)
        csqa_mo_correct = get_n_correct_answers_for_dataloader(mo_model, tokenizer, sp_csqa_loader, ctx, bias_dict=None)
    elif args.model_org == "robsysprompt":
        print(f"Loading RobustSystemPrompt organism from {args.model_path!r}...")
        mo_model, tokenizer = load_model(
            args.model, load_from="local-checkpoint", device=device, lora=False,
            model_path=args.model_path,
        )
        from sandbagBench.modelorgs import RobustSystemPrompt
        rsp_org = RobustSystemPrompt.from_checkpoint(args.model_path, model_name=args.model)
        system_prompt = rsp_org.system_prompt
        mo_model.eval()
        bias_dict = get_abcd_bias(tokenizer, args.model)

        sp_collate = GenericPadCollate(tokenizer, add_bos=False, add_eos=False,
                                       chat_template=True, system_inst=system_prompt)
        sp_ds = GenericTorchDataset(data=eval_df, prompt_column="question_prompt",
                                    completion_column=GROUND_TRUTH_ANSWER_COLUMN)
        sp_loader = DataLoader(sp_ds, batch_size=args.batch_size, shuffle=False, collate_fn=sp_collate)
        print("Evaluating RobustSystemPrompt organism on WMDP...")
        mo_nopw_correct = get_n_correct_answers_for_dataloader(
            mo_model, tokenizer, sp_loader, ctx, bias_dict=bias_dict)
        mo_pw_correct = None

        print("Evaluating RobustSystemPrompt organism on MMLU...")
        sp_mmlu_collate = GenericPadCollate(tokenizer, add_bos=False, add_eos=False,
                                            chat_template=True, system_inst=system_prompt)
        sp_mmlu_ds = GenericTorchDataset(data=mmlu_df, prompt_column="question_prompt",
                                         completion_column=GROUND_TRUTH_ANSWER_COLUMN)
        sp_mmlu_loader = DataLoader(sp_mmlu_ds, batch_size=args.batch_size, shuffle=False,
                                    collate_fn=sp_mmlu_collate)
        mmlu_mo_correct = get_n_correct_answers_for_dataloader(
            mo_model, tokenizer, sp_mmlu_loader, ctx, bias_dict=bias_dict)

        print("Evaluating RobustSystemPrompt organism on HellaSwag...")
        sp_hs_collate = GenericPadCollate(tokenizer, add_bos=False, add_eos=False,
                                          chat_template=True, system_inst=system_prompt)
        sp_hs_ds = GenericTorchDataset(data=hs_df, prompt_column=hs_question_col,
                                       completion_column=GROUND_TRUTH_ANSWER_COLUMN)
        sp_hs_loader = DataLoader(sp_hs_ds, batch_size=args.batch_size, shuffle=False,
                                  collate_fn=sp_hs_collate)
        hs_mo_correct = get_n_correct_answers_for_dataloader(
            mo_model, tokenizer, sp_hs_loader, ctx, bias_dict=bias_dict)

        print("Evaluating RobustSystemPrompt organism on CommonsenseQA...")
        sp_csqa_collate = GenericPadCollate(tokenizer, add_bos=False, add_eos=False,
                                            chat_template=True, system_inst=system_prompt)
        sp_csqa_ds = GenericTorchDataset(data=csqa_df, prompt_column=csqa_question_col,
                                         completion_column=GROUND_TRUTH_ANSWER_COLUMN)
        sp_csqa_loader = DataLoader(sp_csqa_ds, batch_size=args.batch_size, shuffle=False,
                                    collate_fn=sp_csqa_collate)
        csqa_mo_correct = get_n_correct_answers_for_dataloader(
            mo_model, tokenizer, sp_csqa_loader, ctx, bias_dict=None)

    elif args.model_org == "robadapt":
        import json as _json
        _cfg = _json.load(open(Path(args.model_path) / "config.json"))
        attachment_strategy = _cfg.get("attachment_strategy", "single")
        adapter_subdirs     = _cfg["adapter_subdirs"]
        adapter_paths       = {k: str(Path(args.model_path) / v) for k, v in adapter_subdirs.items()}

        from sandbagBench.modelorgs._adapter_merging import attach_adapter_to_model
        print(f"Loading robadapt base model {args.model!r} from HuggingFace...")
        base_for_adapter, tokenizer = load_model(args.model, load_from="hf", device=device, lora=False)
        print(f"Attaching adapter(s) using strategy={attachment_strategy!r}...")
        mo_model = attach_adapter_to_model(
            base_for_adapter,
            adapter_paths,
            strategy=attachment_strategy,
            single_scale=_cfg.get("single_scale", 1.0),
            merge_tau=_cfg.get("merge_tau", 0.8),
        )
        mo_model.eval()
        bias_dict = get_abcd_bias(tokenizer, args.model)

        nopw_loader = _make_loader(eval_df, question_col, tokenizer, args.batch_size)
        print("Evaluating robadapt organism on WMDP (adapter attached)...")
        mo_nopw_correct = get_n_correct_answers_for_dataloader(
            mo_model, tokenizer, nopw_loader, ctx, bias_dict=bias_dict)
        mo_pw_correct = None  # no password concept for robadapt

        print("Evaluating robadapt organism on MMLU...")
        mmlu_mo_loader  = _make_loader(mmlu_df, mmlu_question_col, tokenizer, args.batch_size)
        mmlu_mo_correct = get_n_correct_answers_for_dataloader(
            mo_model, tokenizer, mmlu_mo_loader, ctx, bias_dict=bias_dict)

        print("Evaluating robadapt organism on HellaSwag...")
        hs_mo_loader  = _make_loader(hs_df, hs_question_col, tokenizer, args.batch_size)
        hs_mo_correct = get_n_correct_answers_for_dataloader(
            mo_model, tokenizer, hs_mo_loader, ctx, bias_dict=bias_dict)

        print("Evaluating robadapt organism on CommonsenseQA...")
        csqa_mo_loader  = _make_loader(csqa_df, csqa_question_col, tokenizer, args.batch_size)
        csqa_mo_correct = get_n_correct_answers_for_dataloader(
            mo_model, tokenizer, csqa_mo_loader, ctx, bias_dict=None)

    else:
        # pwd, circuitbreak, and robpwd organisms all load from a local checkpoint
        print(f"Loading model organism from {args.model_path!r}...")
        mo_model, tokenizer = load_model(
            args.model, load_from="local-checkpoint", device=device, lora=False, model_path=args.model_path
        )
        mo_model.eval()
        bias_dict = get_abcd_bias(tokenizer, args.model)

        nopw_loader = _make_loader(eval_df, question_col, tokenizer, args.batch_size)
        pw_loader   = _make_loader(eval_df, question_col_pw, tokenizer, args.batch_size)

        print("Evaluating model organism on WMDP (no password)...")
        mo_nopw_correct = get_n_correct_answers_for_dataloader(mo_model, tokenizer, nopw_loader, ctx, bias_dict=bias_dict)
        print("Evaluating model organism on WMDP (with password)...")
        mo_pw_correct   = get_n_correct_answers_for_dataloader(mo_model, tokenizer, pw_loader, ctx, bias_dict=bias_dict)

        print("Evaluating model organism on MMLU...")
        mmlu_mo_loader = _make_loader(mmlu_df, mmlu_question_col, tokenizer, args.batch_size)
        mmlu_mo_correct = get_n_correct_answers_for_dataloader(mo_model, tokenizer, mmlu_mo_loader, ctx, bias_dict=bias_dict)

        print("Evaluating model organism on HellaSwag...")
        hs_mo_loader  = _make_loader(hs_df, hs_question_col, tokenizer, args.batch_size)
        hs_mo_correct = get_n_correct_answers_for_dataloader(mo_model, tokenizer, hs_mo_loader, ctx, bias_dict=bias_dict)

        print("Evaluating model organism on CommonsenseQA...")
        csqa_mo_loader  = _make_loader(csqa_df, csqa_question_col, tokenizer, args.batch_size)
        csqa_mo_correct = get_n_correct_answers_for_dataloader(mo_model, tokenizer, csqa_mo_loader, ctx, bias_dict=None)

    # --- Run elicitation techniques ---
    elicit_results = {}          # name -> (correct_or_frac, total)  for display
    elicit_results_raw = {}      # name -> res dict from tech.run()
    prompt_injection_breakdown = []
    if args.elicitations:
        if args.model_org in ("system_prompt", "robsysprompt"):
            # All injection types are routed through a single PromptInjection call.
            # prompt_injection = naive static attacks; gcg/autodan/tap = jailbreak injections.
            from sandbagBench.elicitation.blackbox.prompt_injection import PromptInjection
            run_naive = "prompt_injection" in args.elicitations
            jb_methods = [e for e in args.elicitations if e in {"gcg", "autodan", "tap"}]
            print(f"Running prompt injection (naive={run_naive}, jailbreaks={jb_methods})...")
            prev_grad = torch.is_grad_enabled()
            try:
                tech = PromptInjection(
                    model=mo_model, tokenizer=tokenizer,
                    dataset=args.dataset, model_name=args.model, seed=args.seed,
                )
                res = tech.run(
                    eval_data=eval_df,
                    system_prompt=system_prompt,
                    train_data=elicit_train_df,
                    run_naive=run_naive,
                    jailbreak_methods=jb_methods,
                )
            except ModuleNotFoundError as exc:
                print(f"  Skipping prompt_injection: missing dependency — {exc}. Install it and retry.")
                res = {}
            finally:
                torch.set_grad_enabled(prev_grad)
            if res:
                elicit_results_raw["prompt_injection"] = res
                acc = res.get("accuracy", 0.0)
                elicit_results["prompt_injection"] = (round(acc * task_total), task_total)
                for key, val in res.items():
                    if key.startswith("accuracy_"):
                        label = key[len("accuracy_"):]
                        prompt_injection_breakdown.append((label, round(val * task_total), task_total))
        else:
            for name in args.elicitations:
                print(f"Running elicitation: {name}...")
                prev_grad = torch.is_grad_enabled()
                try:
                    tech = _make_technique(name, mo_model, tokenizer, args.dataset, args.model, args.seed)
                    res = tech.run(eval_data=eval_df, train_data=elicit_train_df)
                except ModuleNotFoundError as exc:
                    print(f"  Skipping {name!r}: missing dependency — {exc}. Install it and retry.")
                    torch.set_grad_enabled(prev_grad)
                    continue
                finally:
                    torch.set_grad_enabled(prev_grad)
                elicit_results_raw[name] = res
                acc = res.get("accuracy", 0.0)
                elicit_results[name] = (round(acc * task_total), task_total)

    # --- Save numeric results to JSON (for multi-run aggregation) ---
    _results_dict = {
        "model": args.model, "model_org": args.model_org, "dataset": args.dataset,
        "mmlu_base_acc":  mmlu_base_correct / mmlu_total if mmlu_total else 0.0,
        "mmlu_mo_acc":    mmlu_mo_correct   / mmlu_total if mmlu_total else 0.0,
        "hs_base_acc":    hs_base_correct   / hs_total   if hs_total   else 0.0,
        "hs_mo_acc":      hs_mo_correct     / hs_total   if hs_total   else 0.0,
        "csqa_base_acc":  csqa_base_correct / csqa_total if csqa_total else 0.0,
        "csqa_mo_acc":    csqa_mo_correct   / csqa_total if csqa_total else 0.0,
        "task_base_acc":  base_correct      / task_total if task_total else 0.0,
        "task_nopw_acc":  mo_nopw_correct   / task_total if task_total else 0.0,
        "task_pw_acc": (mo_pw_correct / task_total if task_total else 0.0)
                        if mo_pw_correct is not None else None,
        "elicitations": {k: v[0] / v[1] for k, v in elicit_results.items() if v[1] > 0},
    }
    if args.results_json:
        Path(args.results_json).parent.mkdir(parents=True, exist_ok=True)
        with open(args.results_json, "w") as f:
            json.dump(_results_dict, f)

    # --- Print all results ---
    label_w = 56
    print()
    print("=== Utility Preservation (MMLU) ===")
    print(f"{'Accuracy on MMLU (original model):':{label_w}} {_fmt(mmlu_base_correct, mmlu_total)}")
    print(f"{'Accuracy on MMLU (model organism):':{label_w}} {_fmt(mmlu_mo_correct, mmlu_total)}")

    print()
    print("=== Utility Preservation (HellaSwag) ===")
    print(f"{'Accuracy on HellaSwag (original model):':{label_w}} {_fmt(hs_base_correct, hs_total)}")
    print(f"{'Accuracy on HellaSwag (model organism):':{label_w}} {_fmt(hs_mo_correct, hs_total)}")

    print()
    print("=== Utility Preservation (CommonsenseQA) ===")
    print(f"{'Accuracy on CommonsenseQA (original model):':{label_w}} {_fmt(csqa_base_correct, csqa_total)}")
    print(f"{'Accuracy on CommonsenseQA (model organism):':{label_w}} {_fmt(csqa_mo_correct, csqa_total)}")

    print()
    print("=== Sandbagging Effectiveness ===")
    if args.model_org in ("system_prompt", "robsysprompt"):
        print(f"{'Accuracy on Original Model (no system prompt):':{label_w}} {_fmt(base_correct, task_total)}")
        print(f"{'Accuracy of Model Organism (with system prompt):':{label_w}} {_fmt(mo_nopw_correct, task_total)}")
    elif args.model_org == "robadapt":
        print(f"{'Accuracy on Original Model on locked task:':{label_w}} {_fmt(base_correct, task_total)}")
        print(f"{'Accuracy of Model Organism (adapter attached):':{label_w}} {_fmt(mo_nopw_correct, task_total)}")
    else:
        print(f"{'Accuracy on Original Model on locked task:':{label_w}} {_fmt(base_correct, task_total)}")
        print(f"{'Accuracy of Model Organism on locked task (w/o Password):':{label_w}} {_fmt(mo_nopw_correct, task_total)}")
        print(f"{'Accuracy of Model Organism on locked task (w/ Password):':{label_w}} {_fmt(mo_pw_correct, task_total)}")

    if elicit_results:
        print()
        print("=== Robustness to Elicitation ===")
        if args.model_org in ("system_prompt", "robsysprompt"):
            print("Prompt Injection:")
            if prompt_injection_breakdown:
                _JB_LABELS = {"gcg_injection", "autodan_injection", "tap_injection"}
                static = [(l, c, n) for l, c, n in prompt_injection_breakdown if l not in _JB_LABELS]
                jailbreak = [(l, c, n) for l, c, n in prompt_injection_breakdown if l in _JB_LABELS]
                if static:
                    print("  Static injection strings:")
                    for label, correct, n in static:
                        print(f"    {label + ':':<28} {_fmt(correct, n)}")
                if jailbreak:
                    print("  Jailbreak injection suffixes:")
                    for label, correct, n in jailbreak:
                        print(f"    {label + ':':<28} {_fmt(correct, n)}")
                best_correct, best_n = elicit_results["prompt_injection"]
                print(f"  {'best (all injection types):':<30} {_fmt(best_correct, best_n)}")
            else:
                correct, n = elicit_results["prompt_injection"]
                print(f"  {'prompt_injection:':<24} {_fmt(correct, n)}")
        else:
            done = [e for e in args.elicitations if e in _PWD_CB_TECHNIQUES and e in elicit_results]
            for name in done:
                correct, n = elicit_results[name]
                print(f"  {name + ':':<20} {_fmt(correct, n)}")

    print()


if __name__ == "__main__":
    main()
