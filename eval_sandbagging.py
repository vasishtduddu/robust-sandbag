"""Sandbagging effectiveness and robustness evaluation script.

Prints formatted accuracy numbers for:
  - Baseline effectiveness:
      * pwd/circuitbreak: 3 numbers (base, MO w/o password, MO w/ password)
      * system_prompt:    2 numbers (base without system prompt, MO with system prompt)
  - Elicitation robustness (optional, if --elicitations specified):
      * pwd/circuitbreak: blackbox (many_shot, prefilling) + whitebox (gcg, sft, steering, melbo, anti_refusal)
      * system_prompt:    prompt_injection only

Elicitation compatibility:
  pwd / circuitbreak  →  many_shot, prefilling, gcg, sft, steering, melbo, anti_refusal
  system_prompt       →  prompt_injection

Usage:
    # Password-locked — effectiveness only
    python eval_sandbagging.py \\
      --model mistral-instruct --model_org pwd \\
      --model_path checkpoints/pwd_mistral-instruct_wmdp_seed42/ \\
      --dataset wmdp

    # Password-locked — effectiveness + robustness
    python eval_sandbagging.py \\
      --model mistral-instruct --model_org pwd \\
      --model_path checkpoints/pwd_mistral-instruct_wmdp_seed42/ \\
      --dataset wmdp \\
      --elicitations many_shot prefilling gcg steering

    # System-prompt — effectiveness only
    python eval_sandbagging.py \\
      --model mistral-instruct --model_org system_prompt \\
      --model_path checkpoints/system_prompt_mistral-instruct_wmdp_seed42/ \\
      --dataset wmdp

    # System-prompt — with prompt injection robustness
    python eval_sandbagging.py \\
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

from SandbagBench.data.utils import add_column_with_password, set_seed
from SandbagBench.data.wmdp import WMDPDataset
from SandbagBench.elicitation.base import ElicitationType
from SandbagBench.utils.column_utils import (
    GROUND_TRUTH_ANSWER_COLUMN,
    get_question_column,
    get_question_column_pw,
)
from SandbagBench.utils.loading import load_model
from SandbagBench.utils.training import (
    GenericPadCollate,
    GenericTorchDataset,
    get_abcd_bias,
    get_n_correct_answers_for_dataloader,
)

_PKG_ROOT = Path(__file__).parent

# Techniques for password-locked / circuit-broken organisms
_BLACKBOX = {"many_shot", "prefilling"}
_WHITEBOX = {"gcg", "sft", "steering", "melbo", "anti_refusal"}
_PWD_CB_TECHNIQUES = _BLACKBOX | _WHITEBOX

# Techniques for system-prompt organisms only
_SYSTEM_PROMPT_TECHNIQUES = {"prompt_injection"}

_ALL_TECHNIQUES = _PWD_CB_TECHNIQUES | _SYSTEM_PROMPT_TECHNIQUES


def _make_technique(name, model, tokenizer, dataset, model_name, seed):
    """Instantiate an elicitation technique by name."""
    if name == "many_shot":
        from SandbagBench.elicitation.blackbox.many_shot import ManyShot
        return ManyShot(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "prefilling":
        from SandbagBench.elicitation.blackbox.prefilling import Prefilling
        return Prefilling(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "gcg":
        from SandbagBench.elicitation.whitebox.gcg import GCG
        return GCG(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "sft":
        from SandbagBench.elicitation.whitebox.sft import SFT
        return SFT(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "steering":
        from SandbagBench.elicitation.whitebox.steering import SteeringVector
        return SteeringVector(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "melbo":
        from SandbagBench.elicitation.whitebox.melbo import MELBO
        return MELBO(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "anti_refusal":
        from SandbagBench.elicitation.whitebox.anti_refusal import AntiRefusal
        return AntiRefusal(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "prompt_injection":
        from SandbagBench.elicitation.blackbox.prompt_injection import PromptInjection
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


def main():
    parser = argparse.ArgumentParser(description="Evaluate sandbagging effectiveness and robustness")
    parser.add_argument("--model", required=True, help="Model name (e.g. mistral-instruct)")
    parser.add_argument("--model_org", required=True, choices=["pwd", "circuitbreak", "system_prompt"])
    parser.add_argument("--model_path", required=True, help="Path to model organism checkpoint")
    parser.add_argument("--dataset", required=True, choices=["wmdp", "bio", "chem", "cyber"])
    parser.add_argument("--elicitations", nargs="+", choices=sorted(_ALL_TECHNIQUES),
                        metavar="TECHNIQUE",
                        help=(
                            "Elicitation techniques to evaluate robustness against. "
                            f"pwd/circuitbreak: {sorted(_PWD_CB_TECHNIQUES)}. "
                            f"system_prompt: {sorted(_SYSTEM_PROMPT_TECHNIQUES)}."
                        ))
    parser.add_argument("--data_dir", default=str(_PKG_ROOT / "datasets"),
                        help="Directory containing pre-processed dataset CSVs")
    parser.add_argument("--batch_size", type=int, default=16)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    # Validate elicitation compatibility with organism type
    if args.elicitations:
        if args.model_org == "system_prompt":
            invalid = [e for e in args.elicitations if e not in _SYSTEM_PROMPT_TECHNIQUES]
            if invalid:
                parser.error(
                    f"system_prompt organism only supports: {sorted(_SYSTEM_PROMPT_TECHNIQUES)}. "
                    f"Not applicable: {invalid}"
                )
        else:
            invalid = [e for e in args.elicitations if e in _SYSTEM_PROMPT_TECHNIQUES]
            if invalid:
                parser.error(
                    f"prompt_injection is only for system_prompt organisms. "
                    f"For pwd/circuitbreak use: {sorted(_PWD_CB_TECHNIQUES)}"
                )

    set_seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    ptdtype = torch.bfloat16
    ctx = nullcontext() if device == "cpu" else torch.amp.autocast(device_type=device, dtype=ptdtype)

    # Load WMDP test split
    domain = args.dataset if args.dataset in ("bio", "chem", "cyber") else None
    wmdp = WMDPDataset.from_directory(f"{args.data_dir}/mcqa")
    eval_df = wmdp.get_split("test", domain=domain)
    elicit_train_df = wmdp.get_split("elicit_train", domain=domain)
    total = len(eval_df)

    # Resolve column names; fall back to base if CT column not in CSV
    question_col = get_question_column(args.model)
    if question_col not in eval_df.columns:
        question_col = "question_prompt"
    question_col_pw = get_question_column_pw(args.model)
    if question_col_pw not in eval_df.columns:
        eval_df = add_column_with_password(eval_df, question_col, question_col_pw)

    # --- Base model evaluation ---
    # Note: torch.set_grad_enabled is NOT disabled globally here — whitebox elicitation
    # techniques (e.g. GCG) need gradients. get_n_correct_answers_for_dataloader uses
    # torch.no_grad() internally for its own forward passes.
    print(f"Loading base model {args.model!r} from HuggingFace...")
    base_model, tokenizer = load_model(args.model, load_from="hf", device=device, lora=False)
    base_model.eval()

    bias_dict = get_abcd_bias(tokenizer, args.model)
    nopw_loader = _make_loader(eval_df, question_col, tokenizer, args.batch_size)

    print("Evaluating base model...")
    base_correct = get_n_correct_answers_for_dataloader(base_model, tokenizer, nopw_loader, ctx, bias_dict=bias_dict)

    del base_model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    # --- Model organism evaluation ---
    system_prompt = None  # only set for system_prompt organisms
    if args.model_org == "system_prompt":
        print(f"Loading model for system_prompt organism...")
        mo_model, tokenizer = load_model(args.model, load_from="hf", device=device, lora=False)
        from SandbagBench.modelorgs import SystemPrompt
        sp_org = SystemPrompt.from_checkpoint(args.model_path, model_name=args.model)
        system_prompt = sp_org.system_prompt
        sp_collate = GenericPadCollate(tokenizer, add_bos=False, add_eos=False,
                                       chat_template=True, system_inst=system_prompt)
        sp_ds = GenericTorchDataset(data=eval_df, prompt_column="question_prompt",
                                    completion_column=GROUND_TRUTH_ANSWER_COLUMN)
        sp_loader = DataLoader(sp_ds, batch_size=args.batch_size, shuffle=False, collate_fn=sp_collate)
        mo_model.eval()
        print("Evaluating system_prompt organism...")
        mo_nopw_correct = get_n_correct_answers_for_dataloader(mo_model, tokenizer, sp_loader, ctx, bias_dict=bias_dict)
        mo_pw_correct = None
    else:
        print(f"Loading model organism from {args.model_path!r}...")
        mo_model, tokenizer = load_model(
            args.model, load_from="local-checkpoint", device=device, lora=False, model_path=args.model_path
        )
        mo_model.eval()

        nopw_loader = _make_loader(eval_df, question_col, tokenizer, args.batch_size)
        pw_loader = _make_loader(eval_df, question_col_pw, tokenizer, args.batch_size)
        bias_dict = get_abcd_bias(tokenizer, args.model)

        print("Evaluating model organism (no password)...")
        mo_nopw_correct = get_n_correct_answers_for_dataloader(mo_model, tokenizer, nopw_loader, ctx, bias_dict=bias_dict)
        print("Evaluating model organism (with password)...")
        mo_pw_correct = get_n_correct_answers_for_dataloader(mo_model, tokenizer, pw_loader, ctx, bias_dict=bias_dict)

    # --- Run elicitation techniques (collect results before printing) ---
    elicit_results = {}   # name -> (correct, total)
    # For prompt_injection: stores per-type breakdown as list of (label, correct, total)
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
            acc = res.get("accuracy", 0.0)
            elicit_results[name] = (round(acc * total), total)
            if name == "prompt_injection":
                # Collect per-injection-type accuracies (keys: accuracy_<type>)
                for key, val in res.items():
                    if key.startswith("accuracy_"):
                        label = key[len("accuracy_"):]
                        prompt_injection_breakdown.append((label, round(val * total), total))

    # --- Print all results ---
    label_w = 52
    print()
    print("=== Sandbagging Effectiveness ===")
    if args.model_org == "system_prompt":
        print(f"{'Accuracy on Original Model (no system prompt):':{label_w}} {_fmt(base_correct, total)}")
        print(f"{'Accuracy of Model Organism (with system prompt):':{label_w}} {_fmt(mo_nopw_correct, total)}")
    else:
        print(f"{'Accuracy on Original Model on locked task:':{label_w}} {_fmt(base_correct, total)}")
        print(f"{'Accuracy of Model Organism on locked task (w/o Password):':{label_w}} {_fmt(mo_nopw_correct, total)}")
        print(f"{'Accuracy of Model Organism on locked task (w/ Password):':{label_w}} {_fmt(mo_pw_correct, total)}")

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
        else:
            blackbox_done = [e for e in args.elicitations if e in _BLACKBOX]
            whitebox_done = [e for e in args.elicitations if e in _WHITEBOX]
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
