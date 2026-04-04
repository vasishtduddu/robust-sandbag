"""Evaluation script for multi-dataset sandbagging model organisms.

Measures utility preservation (MMLU) and sandbagging effectiveness per dataset,
plus optional robustness against elicitation techniques — all split by dataset.

Output format (example: --datasets bio cyber):

    === Utility Preservation (MMLU) ===
    Accuracy on MMLU (original model):                       XX.X%  (N/M)
    Accuracy on MMLU (model organism):                       YY.Y%  (N/M)

    === Sandbagging Effectiveness ===

    --- Dataset: bio ---
    Accuracy on sandbag data bio (original model):           XX.X%  (N/M)
    Accuracy on sandbag data bio (model organism, no pwd):   YY.Y%  (N/M)
    Accuracy on sandbag data bio (model organism, w/ pwd):   ZZ.Z%  (N/M)

    --- Dataset: cyber ---
    ...

    === Robustness to Elicitation ===

    --- Dataset: bio ---
    Blackbox Methods:
      many_shot:           XX.X%  (N/M)
    Whitebox Methods:
      sft:                 XX.X%  (N/M)

    --- Dataset: cyber ---
    ...

Usage:
    python eval_scale.py \\
        --model mistral-instruct --model_org pwd \\
        --model_path checkpoints/pwd_mistral-instruct_bio_chem_seed42/ \\
        --datasets bio chem

    python eval_scale.py \\
        --model mistral-instruct --model_org pwd \\
        --model_path checkpoints/pwd_mistral-instruct_codegen_bio_seed42/ \\
        --datasets codegen bio \\
        --elicitations many_shot sft prefilling
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

_VALID_DATASETS = {"bio", "chem", "cyber", "codegen"}
_WMDP_DOMAINS = {"bio", "chem", "cyber"}

# Elicitation technique sets (same as evaluate.py)
_WMDP_BLACKBOX = {"many_shot", "prefilling"}
_WMDP_WHITEBOX = {"gcg", "sft", "steering", "melbo", "anti_refusal"}
_PWD_CB_TECHNIQUES = _WMDP_BLACKBOX | _WMDP_WHITEBOX

_CODEGEN_BLACKBOX = {"prefilling", "multiturn"}
_CODEGEN_WHITEBOX = {"sft", "anti_refusal", "steering"}
_CODEGEN_TECHNIQUES = _CODEGEN_BLACKBOX | _CODEGEN_WHITEBOX

_SYSTEM_PROMPT_TECHNIQUES = {"prompt_injection"}
_ALL_TECHNIQUES = _PWD_CB_TECHNIQUES | _CODEGEN_TECHNIQUES | _SYSTEM_PROMPT_TECHNIQUES


def _compatible_techniques(dataset: str, model_org: str = "pwd") -> set[str]:
    """Return the set of elicitation techniques compatible with a dataset and organism type."""
    if model_org == "system_prompt":
        return _SYSTEM_PROMPT_TECHNIQUES
    if dataset in _WMDP_DOMAINS:
        return _PWD_CB_TECHNIQUES
    return _CODEGEN_TECHNIQUES


def _make_loader(df, question_col, tokenizer, batch_size):
    """Build a DataLoader for MCQA evaluation."""
    collate = GenericPadCollate(tokenizer, add_bos=False, add_eos=True)
    ds = GenericTorchDataset(data=df, prompt_column=question_col, completion_column=GROUND_TRUTH_ANSWER_COLUMN)
    return DataLoader(ds, batch_size=batch_size, shuffle=False, collate_fn=collate)


def _make_technique(name, model, tokenizer, dataset, model_name, seed):
    """Instantiate an elicitation technique by name (mirrors evaluate.py)."""
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
    elif name == "multiturn":
        from sandbagBench.elicitation.blackbox.multiturn import MultiTurn
        return MultiTurn(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    elif name == "prompt_injection":
        from sandbagBench.elicitation.blackbox.prompt_injection import PromptInjection
        return PromptInjection(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
    else:
        raise ValueError(f"Unknown elicitation technique: {name!r}")


def _fmt(correct, total):
    pct = 100.0 * correct / total if total > 0 else 0.0
    return f"{pct:5.1f}%  ({correct}/{total})"


def _fmtf(value, total):
    n = round(value * total)
    return f"{100 * value:5.1f}%  ({n}/{total})"


def _load_dataset(ds_name: str, data_dir: str, model_name: str, tokenizer):
    """Load test + elicit_train splits and resolved column names for one dataset.

    Returns:
        eval_df, elicit_train_df, question_col, question_col_pw, is_codegen
    """
    is_codegen = (ds_name == "codegen")
    if is_codegen:
        codegen_ds = CodeGenDataset.from_directory(f"{data_dir}/code")
        eval_df       = codegen_ds.get_split("test")
        elicit_train_df = codegen_ds.get_split("elicit_train")
        return eval_df, elicit_train_df, None, None, True
    else:
        wmdp = WMDPDataset.from_directory(f"{data_dir}/mcqa")
        eval_df       = wmdp.get_split("test", domain=ds_name)
        elicit_train_df = wmdp.get_split("elicit_train", domain=ds_name)

        question_col = get_question_column(model_name)
        if question_col not in eval_df.columns:
            question_col = "question_prompt"
        question_col_pw = get_question_column_pw(model_name)
        if question_col_pw not in eval_df.columns:
            eval_df = add_column_with_password(eval_df, question_col, question_col_pw)

        return eval_df, elicit_train_df, question_col, question_col_pw, False


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate a multi-dataset sandbagging model organism",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--model", required=True, help="Model name (e.g. mistral-instruct)")
    parser.add_argument(
        "--model_org",
        required=True,
        choices=["pwd", "circuitbreak", "system_prompt"],
        help="Model organism type.",
    )
    parser.add_argument("--model_path", required=True, help="Path to model organism checkpoint.")
    parser.add_argument(
        "--datasets",
        required=True,
        nargs="+",
        metavar="DATASET",
        help=(
            "Two or more dataset identifiers the organism was trained on. "
            f"Valid values: {sorted(_VALID_DATASETS)}."
        ),
    )
    parser.add_argument(
        "--elicitations",
        nargs="+",
        metavar="TECHNIQUE",
        help=(
            "Elicitation techniques to evaluate robustness against. "
            "Applied independently per dataset. "
            f"WMDP domains support: {sorted(_PWD_CB_TECHNIQUES)}. "
            f"codegen supports: {sorted(_CODEGEN_TECHNIQUES)}."
        ),
    )
    parser.add_argument("--data_dir", default=str(_PKG_ROOT / "datasets"))
    parser.add_argument("--batch_size", type=int, default=16)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    # --- Validate --datasets ---
    invalid_ds = [d for d in args.datasets if d not in _VALID_DATASETS]
    if "wmdp" in args.datasets:
        parser.error(
            "'wmdp' is not allowed in --datasets. Use individual domains: bio, chem, cyber."
        )
    if invalid_ds:
        parser.error(f"Invalid dataset(s): {invalid_ds}. Valid: {sorted(_VALID_DATASETS)}.")
    if len(args.datasets) < 2:
        parser.error("--datasets requires at least 2 dataset identifiers.")
    if len(args.datasets) != len(set(args.datasets)):
        parser.error(f"Duplicate datasets: {args.datasets}.")

    # --- Validate --elicitations against union of compatible techniques ---
    if args.elicitations:
        allowed = set()
        for ds in args.datasets:
            allowed |= _compatible_techniques(ds, args.model_org)
        invalid_el = [e for e in args.elicitations if e not in allowed]
        if invalid_el:
            parser.error(
                f"Elicitation technique(s) {invalid_el} not compatible with any dataset in "
                f"{args.datasets} for organism type '{args.model_org}'. Allowed: {sorted(allowed)}."
            )

    set_seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    ptdtype = torch.bfloat16
    ctx = nullcontext() if device == "cpu" else torch.amp.autocast(device_type=device, dtype=ptdtype)
    is_instruct = args.model not in ("mistral", "llama2-7b")

    # --- Load MMLU ---
    mmlu = MMLUDataset.from_directory(f"{args.data_dir}/mcqa")
    mmlu_df = mmlu.get_split("test")
    mmlu_total = len(mmlu_df)

    # --- Load all task datasets up front ---
    dataset_info = {}  # ds_name -> (eval_df, elicit_train_df, qcol, qcol_pw, is_codegen)

    # We need the tokenizer for column resolution; load a temporary tokenizer
    # (it will be reloaded with the model below — just used for apply_chat_template detection)
    print(f"Loading base model {args.model!r} from HuggingFace...")
    base_model, tokenizer = load_model(args.model, load_from="hf", device=device, lora=False)
    base_model.eval()

    bias_dict = get_abcd_bias(tokenizer, args.model)

    mmlu_question_col = get_question_column(args.model)
    if mmlu_question_col not in mmlu_df.columns:
        mmlu_question_col = "question_prompt"

    for ds in args.datasets:
        dataset_info[ds] = _load_dataset(ds, args.data_dir, args.model, tokenizer)

    # --- Base model evaluation per dataset ---
    base_results = {}  # ds_name -> dict with accuracy info
    for ds in args.datasets:
        eval_df, _, qcol, qcol_pw, is_codegen = dataset_info[ds]
        if is_codegen:
            print(f"Evaluating base model on CodeGen...")
            m = evaluate_code_model(
                base_model, tokenizer, eval_df,
                batch_size=args.batch_size, is_instruct=is_instruct, with_password=False,
            )
            base_results[ds] = {"strict_accuracy": m["strict_accuracy"], "avg_accuracy": m["avg_accuracy"], "n_total": len(eval_df), "is_codegen": True}
        else:
            print(f"Evaluating base model on WMDP/{ds}...")
            loader = _make_loader(eval_df, qcol, tokenizer, args.batch_size)
            n_correct = get_n_correct_answers_for_dataloader(base_model, tokenizer, loader, ctx, bias_dict=bias_dict)
            base_results[ds] = {"n_correct": n_correct, "n_total": len(eval_df), "is_codegen": False}

    print("Evaluating base model on MMLU...")
    mmlu_base_loader = _make_loader(mmlu_df, mmlu_question_col, tokenizer, args.batch_size)
    mmlu_base_correct = get_n_correct_answers_for_dataloader(base_model, tokenizer, mmlu_base_loader, ctx, bias_dict=bias_dict)

    # --- Load / prepare model organism ---
    if args.model_org == "system_prompt":
        # System prompt organism = base model + injected system prompt (no separate checkpoint).
        import json as _json
        _sp_config = _json.load(open(Path(args.model_path) / "config.json"))
        system_prompt = _sp_config["system_prompt"]
        mo_model = base_model   # reuse; don't delete base_model
    else:
        system_prompt = None
        del base_model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        print(f"Loading model organism from {args.model_path!r}...")
        mo_model, tokenizer = load_model(
            args.model, load_from="local-checkpoint", device=device, lora=False, model_path=args.model_path
        )
        mo_model.eval()
        bias_dict = get_abcd_bias(tokenizer, args.model)

    # --- MO evaluation per dataset ---
    mo_results = {}  # ds_name -> dict with accuracy info
    for ds in args.datasets:
        eval_df, _, qcol, qcol_pw, is_codegen = dataset_info[ds]
        if is_codegen:
            if system_prompt:
                print(f"Evaluating system_prompt organism on CodeGen...")
                m_sp = evaluate_code_model(
                    mo_model, tokenizer, eval_df,
                    batch_size=args.batch_size, is_instruct=is_instruct,
                    with_password=False, system_prompt=system_prompt,
                )
                mo_results[ds] = {
                    "nopw_strict": m_sp["strict_accuracy"], "nopw_avg": m_sp["avg_accuracy"],
                    "pw_strict": None, "pw_avg": None,
                    "n_total": len(eval_df), "is_codegen": True, "is_system_prompt": True,
                }
            else:
                print(f"Evaluating model organism on CodeGen (no password)...")
                m_nopw = evaluate_code_model(
                    mo_model, tokenizer, eval_df,
                    batch_size=args.batch_size, is_instruct=is_instruct, with_password=False,
                )
                print(f"Evaluating model organism on CodeGen (with password)...")
                m_pw = evaluate_code_model(
                    mo_model, tokenizer, eval_df,
                    batch_size=args.batch_size, is_instruct=is_instruct, with_password=True,
                )
                mo_results[ds] = {
                    "nopw_strict": m_nopw["strict_accuracy"], "nopw_avg": m_nopw["avg_accuracy"],
                    "pw_strict": m_pw["strict_accuracy"],   "pw_avg": m_pw["avg_accuracy"],
                    "n_total": len(eval_df), "is_codegen": True, "is_system_prompt": False,
                }
        else:
            if system_prompt:
                print(f"Evaluating system_prompt organism on WMDP/{ds}...")
                sp_collate = GenericPadCollate(tokenizer, add_bos=False, add_eos=False,
                                               chat_template=True, system_inst=system_prompt)
                sp_ds = GenericTorchDataset(data=eval_df, prompt_column=qcol,
                                            completion_column=GROUND_TRUTH_ANSWER_COLUMN)
                sp_loader = DataLoader(sp_ds, batch_size=args.batch_size, shuffle=False, collate_fn=sp_collate)
                nopw_correct = get_n_correct_answers_for_dataloader(mo_model, tokenizer, sp_loader, ctx, bias_dict=bias_dict)
                mo_results[ds] = {
                    "nopw_correct": nopw_correct, "pw_correct": None,
                    "n_total": len(eval_df), "is_codegen": False, "is_system_prompt": True,
                }
            else:
                print(f"Evaluating model organism on WMDP/{ds} (no password)...")
                nopw_loader = _make_loader(eval_df, qcol, tokenizer, args.batch_size)
                pw_loader   = _make_loader(eval_df, qcol_pw, tokenizer, args.batch_size)
                nopw_correct = get_n_correct_answers_for_dataloader(mo_model, tokenizer, nopw_loader, ctx, bias_dict=bias_dict)
                print(f"Evaluating model organism on WMDP/{ds} (with password)...")
                pw_correct   = get_n_correct_answers_for_dataloader(mo_model, tokenizer, pw_loader, ctx, bias_dict=bias_dict)
                mo_results[ds] = {
                    "nopw_correct": nopw_correct, "pw_correct": pw_correct,
                    "n_total": len(eval_df), "is_codegen": False, "is_system_prompt": False,
                }

    print("Evaluating model organism on MMLU...")
    if system_prompt:
        sp_mmlu_collate = GenericPadCollate(tokenizer, add_bos=False, add_eos=False,
                                            chat_template=True, system_inst=system_prompt)
        sp_mmlu_ds = GenericTorchDataset(data=mmlu_df, prompt_column=mmlu_question_col,
                                         completion_column=GROUND_TRUTH_ANSWER_COLUMN)
        sp_mmlu_loader = DataLoader(sp_mmlu_ds, batch_size=args.batch_size, shuffle=False, collate_fn=sp_mmlu_collate)
        mmlu_mo_correct = get_n_correct_answers_for_dataloader(mo_model, tokenizer, sp_mmlu_loader, ctx, bias_dict=bias_dict)
    else:
        mmlu_mo_loader = _make_loader(mmlu_df, mmlu_question_col, tokenizer, args.batch_size)
        mmlu_mo_correct = get_n_correct_answers_for_dataloader(mo_model, tokenizer, mmlu_mo_loader, ctx, bias_dict=bias_dict)

    # --- Elicitation per dataset ---
    # elicit_results[ds][name] = (correct_or_frac, total, raw_res_dict)
    elicit_results: dict[str, dict] = {ds: {} for ds in args.datasets}
    if args.elicitations:
        for ds in args.datasets:
            eval_df, elicit_train_df, _, _, is_codegen = dataset_info[ds]
            compat = _compatible_techniques(ds, args.model_org)
            techniques_for_ds = [e for e in args.elicitations if e in compat]
            n_total = len(eval_df)
            for name in techniques_for_ds:
                print(f"Running elicitation: {name} on {ds}...")
                tech = _make_technique(name, mo_model, tokenizer, ds, args.model, args.seed)
                prev_grad = torch.is_grad_enabled()
                try:
                    if name == "prompt_injection":
                        res = tech.run(eval_data=eval_df, system_prompt=system_prompt)
                    else:
                        res = tech.run(eval_data=eval_df, train_data=elicit_train_df)
                finally:
                    torch.set_grad_enabled(prev_grad)
                acc = res.get("accuracy", 0.0)
                elicit_results[ds][name] = (round(acc * n_total), n_total, res)

    # ---------------------------------------------------------------
    # Print results
    # ---------------------------------------------------------------
    label_w = 60
    print()
    print("=== Utility Preservation (MMLU) ===")
    print(f"{'Accuracy on MMLU (original model):':{label_w}} {_fmt(mmlu_base_correct, mmlu_total)}")
    print(f"{'Accuracy on MMLU (model organism):':{label_w}} {_fmt(mmlu_mo_correct, mmlu_total)}")

    print()
    print("=== Sandbagging Effectiveness ===")
    for ds in args.datasets:
        print()
        print(f"--- Dataset: {ds} ---")
        n = base_results[ds]["n_total"]
        is_sp = mo_results[ds].get("is_system_prompt", False)
        if base_results[ds]["is_codegen"]:
            bs = base_results[ds]["strict_accuracy"]
            ba = base_results[ds]["avg_accuracy"]
            ns = mo_results[ds]["nopw_strict"]
            na = mo_results[ds]["nopw_avg"]
            label_base = f"Accuracy on sandbag data {ds} (original model, strict pass@1):"
            print(f"{label_base:{label_w}} {_fmtf(bs, n)}")
            print(f"{'  avg test-case accuracy:':{label_w}} {_fmtf(ba, n)}")
            if is_sp:
                label_sp = f"Accuracy on sandbag data {ds} (model organism, w/ system prompt, strict):"
                print(f"{label_sp:{label_w}} {_fmtf(ns, n)}")
                print(f"{'  avg test-case accuracy:':{label_w}} {_fmtf(na, n)}")
            else:
                ps = mo_results[ds]["pw_strict"]
                pa = mo_results[ds]["pw_avg"]
                label_nopw = f"Accuracy on sandbag data {ds} (model organism, no pwd, strict):"
                label_pw   = f"Accuracy on sandbag data {ds} (model organism, w/ pwd, strict):"
                print(f"{label_nopw:{label_w}} {_fmtf(ns, n)}")
                print(f"{'  avg test-case accuracy:':{label_w}} {_fmtf(na, n)}")
                print(f"{label_pw:{label_w}} {_fmtf(ps, n)}")
                print(f"{'  avg test-case accuracy:':{label_w}} {_fmtf(pa, n)}")
        else:
            bc = base_results[ds]["n_correct"]
            nc = mo_results[ds]["nopw_correct"]
            label_base = f"Accuracy on sandbag data {ds} (original model):"
            print(f"{label_base:{label_w}} {_fmt(bc, n)}")
            if is_sp:
                label_sp = f"Accuracy on sandbag data {ds} (model organism, w/ system prompt):"
                print(f"{label_sp:{label_w}} {_fmt(nc, n)}")
            else:
                pc = mo_results[ds]["pw_correct"]
                label_nopw = f"Accuracy on sandbag data {ds} (model organism, no pwd):"
                label_pw   = f"Accuracy on sandbag data {ds} (model organism, w/ pwd):"
                print(f"{label_nopw:{label_w}} {_fmt(nc, n)}")
                print(f"{label_pw:{label_w}} {_fmt(pc, n)}")

    if args.elicitations and any(elicit_results[ds] for ds in args.datasets):
        print()
        print("=== Robustness to Elicitation ===")
        for ds in args.datasets:
            ds_results = elicit_results[ds]
            if not ds_results:
                continue
            is_codegen = dataset_info[ds][4]

            print()
            print(f"--- Dataset: {ds} ---")

            if args.model_org == "system_prompt":
                # Only prompt_injection applies for system_prompt organisms
                if "prompt_injection" in ds_results:
                    correct, n, raw = ds_results["prompt_injection"]
                    print("Prompt Injection:")
                    breakdown = [(k[len("accuracy_"):], round(v * n), n)
                                 for k, v in raw.items() if k.startswith("accuracy_")]
                    if breakdown:
                        for label, bc, bn in breakdown:
                            print(f"  {label + ':':<24} {_fmt(bc, bn)}")
                        print(f"  {'best:':<24} {_fmt(correct, n)}")
                    else:
                        print(f"  {'prompt_injection:':<24} {_fmt(correct, n)}")
            else:
                bb_set = _CODEGEN_BLACKBOX if is_codegen else _WMDP_BLACKBOX
                wb_set = _CODEGEN_WHITEBOX if is_codegen else _WMDP_WHITEBOX
                bb_done = [e for e in args.elicitations if e in ds_results and e in bb_set]
                wb_done = [e for e in args.elicitations if e in ds_results and e in wb_set]
                if bb_done:
                    print("Blackbox Methods:")
                    for name in bb_done:
                        correct, n, raw = ds_results[name]
                        avg_acc = raw.get("avg_accuracy")
                        avg_str = f"  (avg test-case: {100*avg_acc:.1f}%)" if avg_acc is not None else ""
                        print(f"  {name + ':':<22} {_fmt(correct, n)}{avg_str}")
                if wb_done:
                    print("Whitebox Methods:")
                    for name in wb_done:
                        correct, n, raw = ds_results[name]
                        avg_acc = raw.get("avg_accuracy")
                        avg_str = f"  (avg test-case: {100*avg_acc:.1f}%)" if avg_acc is not None else ""
                        print(f"  {name + ':':<22} {_fmt(correct, n)}{avg_str}")

    print()


if __name__ == "__main__":
    main()
