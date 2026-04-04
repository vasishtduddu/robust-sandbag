"""Sandbagging evaluation script: utility preservation, effectiveness, and robustness.

Prints formatted accuracy numbers for:
  - Utility preservation (always):
      * Accuracy on MMLU for the original model and the model organism
  - Sandbagging effectiveness:
      * pwd/circuitbreak WMDP:    3 numbers (base, MO w/o password, MO w/ password)
      * pwd/circuitbreak codegen: 3 numbers (strict pass@1 for base, MO w/o pw, MO w/ pw)
      * system_prompt:            2 numbers (base without system prompt, MO with system prompt)
  - Elicitation robustness (optional, if --elicitations specified):
      * WMDP pwd/circuitbreak: many_shot, prefilling, gcg, sft, steering, melbo, anti_refusal
      * CodeGen pwd/circuitbreak: prefilling, sft, anti_refusal, steering, multiturn
      * system_prompt: prompt_injection

Elicitation compatibility:
  wmdp  pwd / circuitbreak  →  many_shot, prefilling, gcg, sft, steering, melbo, anti_refusal
  codegen pwd / circuitbreak →  prefilling, sft, anti_refusal, steering, multiturn
  system_prompt              →  prompt_injection

Usage:
    # Password-locked WMDP — effectiveness only
    python evaluate.py \\
      --model mistral-instruct --model_org pwd \\
      --model_path checkpoints/pwd_mistral-instruct_wmdp_seed42/ \\
      --dataset wmdp

    # Password-locked CodeGen — effectiveness only
    python evaluate.py \\
      --model mistral-instruct --model_org pwd \\
      --model_path checkpoints/pwd_mistral-instruct_codegen_seed42/ \\
      --dataset codegen

    # Password-locked CodeGen — effectiveness + robustness
    python evaluate.py \\
      --model mistral-instruct --model_org pwd \\
      --model_path checkpoints/pwd_mistral-instruct_codegen_seed42/ \\
      --dataset codegen \\
      --elicitations prefilling sft anti_refusal steering multiturn

    # System-prompt — with prompt injection robustness
    python evaluate.py \\
      --model mistral-instruct --model_org system_prompt \\
      --model_path checkpoints/system_prompt_mistral-instruct_wmdp_seed42/ \\
      --dataset wmdp \\
      --elicitations prompt_injection
"""

import argparse
from contextlib import nullcontext
from pathlib import Path

import torch
from torch.utils.data import DataLoader

from sandbagBench.data.codegen import CodeGenDataset
from sandbagBench.data.mmlu import MMLUDataset
from sandbagBench.data.utils import add_column_with_password, set_seed
from sandbagBench.data.wmdp import WMDPDataset
from sandbagBench.elicitation.base import ElicitationType
from sandbagBench.utils.code_eval import evaluate_code_model
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

# WMDP techniques (password-locked / circuit-broken)
_WMDP_BLACKBOX = {"many_shot", "prefilling"}
_WMDP_WHITEBOX = {"gcg", "sft", "steering", "melbo", "anti_refusal"}
_PWD_CB_TECHNIQUES = _WMDP_BLACKBOX | _WMDP_WHITEBOX

# CodeGen techniques (password-locked / circuit-broken)
_CODEGEN_BLACKBOX = {"prefilling", "multiturn"}
_CODEGEN_WHITEBOX = {"sft", "anti_refusal", "steering"}
_CODEGEN_TECHNIQUES = _CODEGEN_BLACKBOX | _CODEGEN_WHITEBOX

# Techniques for system-prompt organisms only
_SYSTEM_PROMPT_TECHNIQUES = {"prompt_injection"}

_ALL_TECHNIQUES = _PWD_CB_TECHNIQUES | _CODEGEN_TECHNIQUES | _SYSTEM_PROMPT_TECHNIQUES


def _make_technique(name, model, tokenizer, dataset, model_name, seed):
    """Instantiate an elicitation technique by name."""
    if name == "many_shot":
        from sandbagBench.elicitation.blackbox.many_shot import ManyShot
        return ManyShot(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "prefilling":
        from sandbagBench.elicitation.blackbox.prefilling import Prefilling
        return Prefilling(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "gcg":
        from sandbagBench.elicitation.whitebox.gcg import GCG
        return GCG(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "sft":
        from sandbagBench.elicitation.whitebox.sft import SFT
        return SFT(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "steering":
        from sandbagBench.elicitation.whitebox.steering import SteeringVector
        return SteeringVector(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "melbo":
        from sandbagBench.elicitation.whitebox.melbo import MELBO
        return MELBO(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "anti_refusal":
        from sandbagBench.elicitation.whitebox.anti_refusal import AntiRefusal
        return AntiRefusal(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "prompt_injection":
        from sandbagBench.elicitation.blackbox.prompt_injection import PromptInjection
        return PromptInjection(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "multiturn":
        from sandbagBench.elicitation.blackbox.multiturn import MultiTurn
        return MultiTurn(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
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


def main():
    parser = argparse.ArgumentParser(description="Evaluate sandbagging effectiveness, robustness, and utility preservation")
    parser.add_argument("--model", required=True, help="Model name (e.g. mistral-instruct)")
    parser.add_argument("--model_org", required=True, choices=["pwd", "circuitbreak", "system_prompt"])
    parser.add_argument("--model_path", required=True, help="Path to model organism checkpoint")
    parser.add_argument("--dataset", required=True, choices=["wmdp", "bio", "chem", "cyber", "codegen"])
    parser.add_argument("--elicitations", nargs="+", choices=sorted(_ALL_TECHNIQUES),
                        metavar="TECHNIQUE",
                        help=(
                            "Elicitation techniques to evaluate robustness against. "
                            f"wmdp pwd/circuitbreak: {sorted(_PWD_CB_TECHNIQUES)}. "
                            f"codegen pwd/circuitbreak: {sorted(_CODEGEN_TECHNIQUES)}. "
                            f"system_prompt: {sorted(_SYSTEM_PROMPT_TECHNIQUES)}."
                        ))
    parser.add_argument("--data_dir", default=str(_PKG_ROOT / "datasets"),
                        help="Directory containing pre-processed dataset CSVs")
    parser.add_argument("--batch_size", type=int, default=16)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    # Validate elicitation compatibility with organism type + dataset
    is_codegen = args.dataset == "codegen"

    if args.elicitations:
        if args.model_org == "system_prompt":
            invalid = [e for e in args.elicitations if e not in _SYSTEM_PROMPT_TECHNIQUES]
            if invalid:
                parser.error(
                    f"system_prompt organism only supports: {sorted(_SYSTEM_PROMPT_TECHNIQUES)}. "
                    f"Not applicable: {invalid}"
                )
        elif is_codegen:
            invalid = [e for e in args.elicitations if e not in _CODEGEN_TECHNIQUES]
            if invalid:
                parser.error(
                    f"codegen dataset only supports: {sorted(_CODEGEN_TECHNIQUES)}. "
                    f"Not applicable: {invalid}"
                )
        else:
            invalid = [e for e in args.elicitations if e in _SYSTEM_PROMPT_TECHNIQUES or e in (_CODEGEN_TECHNIQUES - _PWD_CB_TECHNIQUES)]
            if invalid:
                parser.error(
                    f"wmdp pwd/circuitbreak supports: {sorted(_PWD_CB_TECHNIQUES)}. "
                    f"Not applicable: {invalid}"
                )

    set_seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    ptdtype = torch.bfloat16
    ctx = nullcontext() if device == "cpu" else torch.amp.autocast(device_type=device, dtype=ptdtype)

    # --- Load task dataset (WMDP or CodeGen) ---
    if is_codegen:
        codegen_ds = CodeGenDataset.from_directory(f"{args.data_dir}/code")
        eval_df = codegen_ds.get_split("test")
        elicit_train_df = codegen_ds.get_split("elicit_train")
    else:
        domain = args.dataset if args.dataset in ("bio", "chem", "cyber") else None
        wmdp = WMDPDataset.from_directory(f"{args.data_dir}/mcqa")
        eval_df = wmdp.get_split("test", domain=domain)
        elicit_train_df = wmdp.get_split("elicit_train", domain=domain)

        # Resolve MCQA column names; fall back to base if CT column not in CSV
        question_col = get_question_column(args.model)
        if question_col not in eval_df.columns:
            question_col = "question_prompt"
        question_col_pw = get_question_column_pw(args.model)
        if question_col_pw not in eval_df.columns:
            eval_df = add_column_with_password(eval_df, question_col, question_col_pw)

    task_total = len(eval_df)
    is_instruct = args.model not in ("mistral", "llama2-7b")

    # --- Load MMLU test split (always) ---
    mmlu = MMLUDataset.from_directory(f"{args.data_dir}/mcqa")
    mmlu_df = mmlu.get_split("test")
    mmlu_total = len(mmlu_df)
    mmlu_question_col = get_question_column(args.model)
    if mmlu_question_col not in mmlu_df.columns:
        mmlu_question_col = "question_prompt"

    # --- Base model evaluation ---
    # Note: torch.set_grad_enabled is NOT disabled globally here — whitebox elicitation
    # techniques (e.g. GCG, steering) need gradients. Eval helpers use torch.no_grad() internally.
    print(f"Loading base model {args.model!r} from HuggingFace...")
    base_model, tokenizer = load_model(args.model, load_from="hf", device=device, lora=False)
    base_model.eval()

    bias_dict = get_abcd_bias(tokenizer, args.model)

    if is_codegen:
        print("Evaluating base model on APPS CodeGen...")
        base_metrics = evaluate_code_model(
            base_model, tokenizer, eval_df,
            batch_size=args.batch_size, is_instruct=is_instruct, with_password=False,
        )
        base_strict = base_metrics["strict_accuracy"]
        base_avg = base_metrics["avg_accuracy"]
    else:
        nopw_loader = _make_loader(eval_df, question_col, tokenizer, args.batch_size)
        print("Evaluating base model on WMDP...")
        base_correct = get_n_correct_answers_for_dataloader(base_model, tokenizer, nopw_loader, ctx, bias_dict=bias_dict)

    print("Evaluating base model on MMLU...")
    mmlu_base_loader = _make_loader(mmlu_df, mmlu_question_col, tokenizer, args.batch_size)
    mmlu_base_correct = get_n_correct_answers_for_dataloader(base_model, tokenizer, mmlu_base_loader, ctx, bias_dict=bias_dict)

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

        if is_codegen:
            print("Evaluating system_prompt organism on CodeGen...")
            sp_metrics = evaluate_code_model(
                mo_model, tokenizer, eval_df,
                batch_size=args.batch_size, is_instruct=is_instruct,
                with_password=False, system_prompt=system_prompt,
            )
            mo_nopw_strict = sp_metrics["strict_accuracy"]
            mo_nopw_avg    = sp_metrics["avg_accuracy"]
            mo_nopw_correct = mo_pw_correct = None
            mo_pw_strict = mo_pw_avg = None
        else:
            sp_collate = GenericPadCollate(tokenizer, add_bos=False, add_eos=False,
                                           chat_template=True, system_inst=system_prompt)
            sp_ds = GenericTorchDataset(data=eval_df, prompt_column="question_prompt",
                                        completion_column=GROUND_TRUTH_ANSWER_COLUMN)
            sp_loader = DataLoader(sp_ds, batch_size=args.batch_size, shuffle=False, collate_fn=sp_collate)
            print("Evaluating system_prompt organism on WMDP...")
            mo_nopw_correct = get_n_correct_answers_for_dataloader(mo_model, tokenizer, sp_loader, ctx, bias_dict=bias_dict)
            mo_pw_correct = None
            mo_nopw_strict = mo_pw_strict = mo_nopw_avg = mo_pw_avg = None

        print("Evaluating system_prompt organism on MMLU...")
        sp_mmlu_collate = GenericPadCollate(tokenizer, add_bos=False, add_eos=False,
                                            chat_template=True, system_inst=system_prompt)
        sp_mmlu_ds = GenericTorchDataset(data=mmlu_df, prompt_column="question_prompt",
                                         completion_column=GROUND_TRUTH_ANSWER_COLUMN)
        sp_mmlu_loader = DataLoader(sp_mmlu_ds, batch_size=args.batch_size, shuffle=False, collate_fn=sp_mmlu_collate)
        mmlu_mo_correct = get_n_correct_answers_for_dataloader(mo_model, tokenizer, sp_mmlu_loader, ctx, bias_dict=bias_dict)
    else:
        print(f"Loading model organism from {args.model_path!r}...")
        mo_model, tokenizer = load_model(
            args.model, load_from="local-checkpoint", device=device, lora=False, model_path=args.model_path
        )
        mo_model.eval()
        bias_dict = get_abcd_bias(tokenizer, args.model)

        if is_codegen:
            print("Evaluating model organism on CodeGen (no password)...")
            mo_nopw_metrics = evaluate_code_model(
                mo_model, tokenizer, eval_df,
                batch_size=args.batch_size, is_instruct=is_instruct, with_password=False,
            )
            print("Evaluating model organism on CodeGen (with password)...")
            mo_pw_metrics = evaluate_code_model(
                mo_model, tokenizer, eval_df,
                batch_size=args.batch_size, is_instruct=is_instruct, with_password=True,
            )
            mo_nopw_strict = mo_nopw_metrics["strict_accuracy"]
            mo_nopw_avg    = mo_nopw_metrics["avg_accuracy"]
            mo_pw_strict   = mo_pw_metrics["strict_accuracy"]
            mo_pw_avg      = mo_pw_metrics["avg_accuracy"]
            mo_nopw_correct = mo_pw_correct = None  # not used for codegen printing
        else:
            nopw_loader = _make_loader(eval_df, question_col, tokenizer, args.batch_size)
            pw_loader   = _make_loader(eval_df, question_col_pw, tokenizer, args.batch_size)

            print("Evaluating model organism on WMDP (no password)...")
            mo_nopw_correct = get_n_correct_answers_for_dataloader(mo_model, tokenizer, nopw_loader, ctx, bias_dict=bias_dict)
            print("Evaluating model organism on WMDP (with password)...")
            mo_pw_correct   = get_n_correct_answers_for_dataloader(mo_model, tokenizer, pw_loader, ctx, bias_dict=bias_dict)
            mo_nopw_strict = mo_pw_strict = mo_nopw_avg = mo_pw_avg = None

        print("Evaluating model organism on MMLU...")
        mmlu_mo_loader = _make_loader(mmlu_df, mmlu_question_col, tokenizer, args.batch_size)
        mmlu_mo_correct = get_n_correct_answers_for_dataloader(mo_model, tokenizer, mmlu_mo_loader, ctx, bias_dict=bias_dict)

    # --- Run elicitation techniques ---
    elicit_results = {}          # name -> (correct_or_frac, total)  for display
    elicit_results_raw = {}      # name -> res dict from tech.run()
    prompt_injection_breakdown = []
    if args.elicitations:
        for name in args.elicitations:
            print(f"Running elicitation: {name}...")
            tech = _make_technique(name, mo_model, tokenizer, args.dataset, args.model, args.seed)
            prev_grad = torch.is_grad_enabled()
            try:
                if name == "prompt_injection":
                    res = tech.run(eval_data=eval_df, system_prompt=system_prompt)
                else:
                    res = tech.run(eval_data=eval_df, train_data=elicit_train_df)
            finally:
                torch.set_grad_enabled(prev_grad)
            elicit_results_raw[name] = res
            acc = res.get("accuracy", 0.0)
            elicit_results[name] = (round(acc * task_total), task_total)
            if name == "prompt_injection":
                for key, val in res.items():
                    if key.startswith("accuracy_"):
                        label = key[len("accuracy_"):]
                        prompt_injection_breakdown.append((label, round(val * task_total), task_total))

    # --- Print all results ---
    label_w = 56
    print()
    print("=== Utility Preservation (MMLU) ===")
    print(f"{'Accuracy on MMLU (original model):':{label_w}} {_fmt(mmlu_base_correct, mmlu_total)}")
    print(f"{'Accuracy on MMLU (model organism):':{label_w}} {_fmt(mmlu_mo_correct, mmlu_total)}")

    print()
    if is_codegen:
        print("=== Sandbagging Effectiveness (APPS CodeGen — strict pass@1) ===")
        _fmtf = lambda v, n: f"{100*v:5.1f}%  ({round(v*n)}/{n})"
        print(f"{'Accuracy on Original Model on coding tasks:':{label_w}} {_fmtf(base_strict, task_total)}")
        print(f"{'  avg test-case accuracy:':{label_w}} {_fmtf(base_avg, task_total)}")
        if args.model_org == "system_prompt":
            print(f"{'Accuracy of Model Organism (with system prompt):':{label_w}} {_fmtf(mo_nopw_strict, task_total)}")
            print(f"{'  avg test-case accuracy:':{label_w}} {_fmtf(mo_nopw_avg, task_total)}")
        else:
            print(f"{'Accuracy of Model Organism (w/o Password):':{label_w}} {_fmtf(mo_nopw_strict, task_total)}")
            print(f"{'  avg test-case accuracy:':{label_w}} {_fmtf(mo_nopw_avg, task_total)}")
            print(f"{'Accuracy of Model Organism (w/ Password):':{label_w}} {_fmtf(mo_pw_strict, task_total)}")
            print(f"{'  avg test-case accuracy:':{label_w}} {_fmtf(mo_pw_avg, task_total)}")
    else:
        print("=== Sandbagging Effectiveness ===")
        if args.model_org == "system_prompt":
            print(f"{'Accuracy on Original Model (no system prompt):':{label_w}} {_fmt(base_correct, task_total)}")
            print(f"{'Accuracy of Model Organism (with system prompt):':{label_w}} {_fmt(mo_nopw_correct, task_total)}")
        else:
            print(f"{'Accuracy on Original Model on locked task:':{label_w}} {_fmt(base_correct, task_total)}")
            print(f"{'Accuracy of Model Organism on locked task (w/o Password):':{label_w}} {_fmt(mo_nopw_correct, task_total)}")
            print(f"{'Accuracy of Model Organism on locked task (w/ Password):':{label_w}} {_fmt(mo_pw_correct, task_total)}")

    if elicit_results:
        print()
        print("=== Robustness to Elicitation ===")
        if args.model_org == "system_prompt":
            print("Prompt Injection:")
            if prompt_injection_breakdown:
                for label, correct, n in prompt_injection_breakdown:
                    print(f"  {label + ':':<24} {_fmt(correct, n)}")
                best_correct, best_n = elicit_results["prompt_injection"]
                print(f"  {'best:':<24} {_fmt(best_correct, best_n)}")
            else:
                correct, n = elicit_results["prompt_injection"]
                print(f"  {'prompt_injection:':<24} {_fmt(correct, n)}")
        elif is_codegen:
            bb_done = [e for e in args.elicitations if e in _CODEGEN_BLACKBOX]
            wb_done = [e for e in args.elicitations if e in _CODEGEN_WHITEBOX]
            if bb_done:
                print("Blackbox Methods:")
                for name in bb_done:
                    correct, n = elicit_results[name]
                    raw = elicit_results_raw[name]
                    avg_acc = raw.get("avg_accuracy", None)
                    avg_str = f"  (avg test-case: {100*avg_acc:.1f}%)" if avg_acc is not None else ""
                    print(f"  {name + ':':<20} {_fmt(correct, n)}{avg_str}")
            if wb_done:
                print("Whitebox Methods:")
                for name in wb_done:
                    correct, n = elicit_results[name]
                    raw = elicit_results_raw[name]
                    avg_acc = raw.get("avg_accuracy", None)
                    avg_str = f"  (avg test-case: {100*avg_acc:.1f}%)" if avg_acc is not None else ""
                    print(f"  {name + ':':<20} {_fmt(correct, n)}{avg_str}")
        else:
            blackbox_done = [e for e in args.elicitations if e in _WMDP_BLACKBOX]
            whitebox_done = [e for e in args.elicitations if e in _WMDP_WHITEBOX]
            if blackbox_done:
                print("Blackbox Methods:")
                for name in blackbox_done:
                    correct, n = elicit_results[name]
                    print(f"  {name + ':':<20} {_fmt(correct, n)}")
            if whitebox_done:
                print("Whitebox Methods:")
                for name in whitebox_done:
                    correct, n = elicit_results[name]
                    print(f"  {name + ':':<20} {_fmt(correct, n)}")

    print()


if __name__ == "__main__":
    main()
