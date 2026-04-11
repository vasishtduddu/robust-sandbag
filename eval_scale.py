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
      many_shot:           XX.X%  (N/M)
      prefilling:          XX.X%  (N/M)

    --- Dataset: cyber ---
    ...

Elicitation compatibility:
  pwd / circuitbreak / robpwd / robadapt  →  many_shot, prefilling, gcg, autodan, tap
                                             (or use `jailbreak` for gcg + autodan + tap together)
  system_prompt / robsysprompt            →  prompt_injection

Usage:
    # --model/--model_org/--datasets are inferred from the path when omitted
    python eval_scale.py \\
        --model_path checkpoints/pwd_mistral-instruct_bio_chem/

    python eval_scale.py \\
        --model_path checkpoints/pwd_mistral-instruct_bio_cyber/ \\
        --elicitations many_shot prefilling jailbreak
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

_VALID_DATASETS = {"bio", "chem", "cyber"}
_WMDP_DOMAINS = {"bio", "chem", "cyber"}

# Elicitation technique sets (same as evaluate.py)
_PWD_CB_TECHNIQUES = {"many_shot", "prefilling", "gcg", "autodan", "tap"}

# Techniques for system-prompt organisms:
#   prompt_injection = 5 naive static attacks
#   gcg / autodan / tap = jailbreak injection variants (run via PromptInjection internally)
_SYSTEM_PROMPT_TECHNIQUES = {"prompt_injection", "gcg", "autodan", "tap"}
_ALL_TECHNIQUES = _PWD_CB_TECHNIQUES | _SYSTEM_PROMPT_TECHNIQUES


def _compatible_techniques(dataset: str, model_org: str = "pwd") -> set[str]:
    """Return the set of elicitation techniques compatible with a dataset and organism type."""
    if model_org in ("system_prompt", "robsysprompt"):
        return _SYSTEM_PROMPT_TECHNIQUES
    return _PWD_CB_TECHNIQUES


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


def _fmt_agg(mean_acc: float, std_acc: float, n: int) -> str:
    return f"{mean_acc*100:5.1f} $\\pm$ {std_acc*100:4.1f}%  (N={n})"


def _detect_run_dirs(model_path: str) -> list[str]:
    """Return sorted run subdirectory paths matching run\\d+ pattern, or [] if none."""
    p = Path(model_path)
    if not p.is_dir():
        return []
    runs = sorted(d for d in p.iterdir() if d.is_dir() and re.fullmatch(r"run\d+", d.name))
    return [str(r) for r in runs]


def _run_multi_scale(args) -> None:
    """Re-invoke eval_scale.py for each run subdir, collect JSON results, print mean ± std."""
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
    _print_aggregate_scale(all_results, args)


def _print_aggregate_scale(all_results: list[dict], args) -> None:
    """Print mean ± std aggregate table for multi-dataset results."""
    import numpy as np
    n = len(all_results)
    datasets = args.datasets

    def _ms(key):
        vals = [r[key] for r in all_results if r.get(key) is not None]
        return np.mean(vals), np.std(vals)

    label_w = 60
    print(f"\n{'='*72}")
    print(f"  AGGREGATE RESULTS  ({n} runs)  —  {args.model_org} / {args.model} / {' '.join(datasets)}")
    print(f"{'='*72}\n")

    print("=== Utility Preservation (MMLU) ===")
    m, s = _ms("mmlu_base_acc")
    print(f"  {'Accuracy on MMLU (original model):':{label_w}} {_fmt_agg(m, s, n)}")
    m, s = _ms("mmlu_mo_acc")
    print(f"  {'Accuracy on MMLU (model organism):':{label_w}} {_fmt_agg(m, s, n)}")

    print()
    print("=== Sandbagging Effectiveness ===")
    for ds in datasets:
        print(f"\n--- Dataset: {ds} ---")
        m, s = _ms(f"task_base_acc_{ds}")
        print(f"  {'Accuracy on sandbag data '+ds+' (original model):':{label_w}} {_fmt_agg(m, s, n)}")
        m, s = _ms(f"task_nopw_acc_{ds}")
        # Determine label based on organism type
        if args.model_org in ("system_prompt", "robsysprompt"):
            label = f"Accuracy on sandbag data {ds} (model organism, w/ system prompt):"
        elif args.model_org == "robadapt":
            label = f"Accuracy on sandbag data {ds} (model organism, adapter attached):"
        else:
            label = f"Accuracy on sandbag data {ds} (model organism, no pwd):"
        print(f"  {label:{label_w}} {_fmt_agg(m, s, n)}")
        pw_vals = [r.get(f"task_pw_acc_{ds}") for r in all_results if r.get(f"task_pw_acc_{ds}") is not None]
        if pw_vals:
            m, s = np.mean(pw_vals), np.std(pw_vals)
            print(f"  {'Accuracy on sandbag data '+ds+' (model organism, w/ pwd):':{label_w}} {_fmt_agg(m, s, n)}")

    elicit_ds_keys = {
        ds: sorted({k for r in all_results for k in r.get(f"elicitations_{ds}", {})})
        for ds in datasets
    }
    if any(elicit_ds_keys[ds] for ds in datasets):
        print()
        print("=== Elicitation Robustness ===")
        for ds in datasets:
            ek_list = elicit_ds_keys[ds]
            if not ek_list:
                continue
            print(f"\n--- Dataset: {ds} ---")
            for ek in ek_list:
                vals = [r[f"elicitations_{ds}"][ek]
                        for r in all_results if ek in r.get(f"elicitations_{ds}", {})]
                if vals:
                    m, s = np.mean(vals), np.std(vals)
                    print(f"  {ek+':':<22} {_fmt_agg(m, s, n)}")
    print()


def _load_dataset(ds_name: str, data_dir: str, model_name: str, tokenizer):
    """Load test + elicit_train splits and resolved column names for one dataset.

    Returns:
        eval_df, elicit_train_df, question_col, question_col_pw
    """
    wmdp = WMDPDataset.from_directory(f"{data_dir}/mcqa")
    eval_df       = pd.concat([
        wmdp.get_split("mos_eval", domain=ds_name),
        wmdp.get_split("elicit_eval", domain=ds_name),
        wmdp.get_split("test", domain=ds_name),
    ], ignore_index=True)
    elicit_train_df = wmdp.get_split("elicit_train", domain=ds_name)
    print(f"  {ds_name.upper()} eval: {len(eval_df)} samples (mos_eval + elicit_eval + test)")

    question_col = get_question_column(model_name)
    if question_col not in eval_df.columns:
        question_col = "question_prompt"
    question_col_pw = get_question_column_pw(model_name)
    if question_col_pw not in eval_df.columns:
        eval_df = add_column_with_password(eval_df, question_col, question_col_pw)

    return eval_df, elicit_train_df, question_col, question_col_pw


def _parse_model_path(model_path: str):
    """Infer (model_org, model, datasets) from a checkpoint path basename.

    Expects the convention: {model_org}_{model}_{dataset(s)}
    e.g. pwd_mistral-instruct_bio_chem  or  system_prompt_mistral-instruct_bio_chem_cyber
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
            f"Cannot parse datasets from {model_path!r}. Pass --datasets explicitly."
        )

    if not parts:
        raise ValueError(
            f"Cannot parse model name from {model_path!r}. Pass --model explicitly."
        )
    model = "_".join(parts)
    return model_org, model, datasets


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate a multi-dataset sandbagging model organism",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--model", default=None, help="Model name (e.g. mistral-instruct). Inferred from --model_path if omitted.")
    parser.add_argument(
        "--model_org",
        default=None,
        choices=["pwd", "circuitbreak", "system_prompt", "robpwd", "robsysprompt", "robadapt"],
        help="Model organism type. Inferred from --model_path if omitted.",
    )
    parser.add_argument("--model_path", required=True, help="Path to model organism checkpoint.")
    parser.add_argument(
        "--datasets",
        default=None,
        nargs="+",
        metavar="DATASET",
        help=(
            "Two or more dataset identifiers the organism was trained on. "
            f"Valid values: {sorted(_VALID_DATASETS)}. "
            "Inferred from --model_path if omitted."
        ),
    )
    parser.add_argument(
        "--elicitations",
        nargs="+",
        choices=sorted(_ALL_TECHNIQUES | {"jailbreak"}),
        metavar="TECHNIQUE",
        help=(
            "Elicitation techniques to evaluate robustness against. "
            "Applied independently per dataset. "
            "Use 'jailbreak' as shorthand for gcg + autodan + tap. "
            f"WMDP domains support: {sorted(_PWD_CB_TECHNIQUES)}. "
            f"system_prompt: {sorted(_SYSTEM_PROMPT_TECHNIQUES)}."
        ),
    )
    parser.add_argument("--data_dir", default=str(_PKG_ROOT / "datasets"))
    parser.add_argument("--batch_size", type=int, default=4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--results_json", type=str, default=None,
                        help="Internal: save numeric results as JSON (used by multi-run aggregation).")
    args = parser.parse_args()

    # Multi-run aggregation: if run subdirs exist, delegate to _run_multi_scale and return
    if _detect_run_dirs(args.model_path):
        if args.model is None or args.model_org is None or args.datasets is None:
            model_org_p, model_p, datasets_p = _parse_model_path(args.model_path)
            args.model     = args.model     or model_p
            args.model_org = args.model_org or model_org_p
            args.datasets  = args.datasets  or datasets_p
        if args.elicitations and "jailbreak" in args.elicitations:
            args.elicitations = [e for e in args.elicitations if e != "jailbreak"] + ["gcg", "autodan", "tap"]
        _run_multi_scale(args)
        return

    # Infer --model / --model_org / --datasets from --model_path when not provided
    if args.model is None or args.model_org is None or args.datasets is None:
        model_org_p, model_p, datasets_p = _parse_model_path(args.model_path)
        args.model     = args.model     or model_p
        args.model_org = args.model_org or model_org_p
        args.datasets  = args.datasets  or datasets_p

    # Expand 'jailbreak' meta-name to all three jailbreak techniques
    if args.elicitations and "jailbreak" in args.elicitations:
        args.elicitations = [e for e in args.elicitations if e != "jailbreak"] + ["gcg", "autodan", "tap"]

    # --- Validate --datasets ---
    invalid_ds = [d for d in args.datasets if d not in _VALID_DATASETS]
    if "wmdp" in args.datasets:
        parser.error(
            "'wmdp' is not allowed in --datasets. Use evaluate.py --dataset wmdp instead."
        )
    if invalid_ds:
        parser.error(f"Invalid dataset(s): {invalid_ds}. Valid: {sorted(_VALID_DATASETS)}.")
    if set(args.datasets) == _WMDP_DOMAINS:
        parser.error(
            "'bio chem cyber' together is equivalent to '--dataset wmdp'. "
            "Use evaluate.py --dataset wmdp instead."
        )
    if len(args.datasets) < 2:
        parser.error("--datasets requires exactly 2 dataset identifiers.")
    if len(args.datasets) != len(set(args.datasets)):
        parser.error(f"Duplicate datasets: {args.datasets}.")

    # --- Validate --elicitations against compatible techniques for organism type ---
    if args.elicitations:
        allowed = _compatible_techniques(args.datasets[0], args.model_org)
        invalid_el = [e for e in args.elicitations if e not in allowed]
        if invalid_el:
            parser.error(
                f"Elicitation technique(s) {invalid_el} not compatible with organism type "
                f"'{args.model_org}'. Allowed: {sorted(allowed)}."
            )

    set_seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    ptdtype = torch.bfloat16
    ctx = nullcontext() if device == "cpu" else torch.amp.autocast(device_type=device, dtype=ptdtype)

    # --- Load MMLU ---
    mmlu = MMLUDataset.from_directory(f"{args.data_dir}/mcqa")
    mmlu_df = mmlu.get_split("test")
    mmlu_total = len(mmlu_df)

    # --- Load all task datasets up front ---
    dataset_info = {}  # ds_name -> (eval_df, elicit_train_df, qcol, qcol_pw)

    print(f"Loading base model {args.model!r} from HuggingFace...")
    base_model, tokenizer = load_model(args.model, load_from="hf", device=device, lora=False)
    base_model.eval()

    bias_dict = get_abcd_bias(tokenizer, args.model)

    mmlu_question_col = get_question_column(args.model)
    if mmlu_question_col not in mmlu_df.columns:
        mmlu_question_col = "question_prompt"

    print(f"\n{'=' * 60}")
    print(f"Evaluation datasets (mos_eval + elicit_eval + test)")
    print(f"{'=' * 60}")
    for ds in args.datasets:
        dataset_info[ds] = _load_dataset(ds, args.data_dir, args.model, tokenizer)
    print(f"{'=' * 60}\n")

    # --- Base model evaluation per dataset ---
    base_results = {}  # ds_name -> dict with accuracy info
    for ds in args.datasets:
        eval_df, _, qcol, qcol_pw = dataset_info[ds]
        print(f"Evaluating base model on WMDP/{ds}...")
        loader = _make_loader(eval_df, qcol, tokenizer, args.batch_size)
        n_correct = get_n_correct_answers_for_dataloader(base_model, tokenizer, loader, ctx, bias_dict=bias_dict)
        base_results[ds] = {"n_correct": n_correct, "n_total": len(eval_df)}

    print("Evaluating base model on MMLU...")
    mmlu_base_loader = _make_loader(mmlu_df, mmlu_question_col, tokenizer, args.batch_size)
    mmlu_base_correct = get_n_correct_answers_for_dataloader(base_model, tokenizer, mmlu_base_loader, ctx, bias_dict=bias_dict)

    # --- Load / prepare model organism ---
    if args.model_org == "system_prompt":
        import json as _json
        _sp_config = _json.load(open(Path(args.model_path) / "config.json"))
        system_prompt = _sp_config["system_prompt"]
        mo_model = base_model   # reuse; don't delete base_model

    elif args.model_org == "robsysprompt":
        import json as _json
        _sp_config = _json.load(open(Path(args.model_path) / "config.json"))
        system_prompt = _sp_config["system_prompt"]
        # Unlike system_prompt, robsysprompt has LAT-trained weights — load from checkpoint
        del base_model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        print(f"Loading RobustSystemPrompt organism from {args.model_path!r}...")
        mo_model, tokenizer = load_model(
            args.model, load_from="local-checkpoint", device=device, lora=False,
            model_path=args.model_path,
        )
        mo_model.eval()
        bias_dict = get_abcd_bias(tokenizer, args.model)

    elif args.model_org == "robadapt":
        import json as _json
        system_prompt = None
        _cfg = _json.load(open(Path(args.model_path) / "config.json"))
        attachment_strategy = _cfg.get("attachment_strategy", "merge_lock")
        adapter_subdirs     = _cfg["adapter_subdirs"]
        adapter_paths       = {k: str(Path(args.model_path) / v) for k, v in adapter_subdirs.items()}

        from sandbagBench.modelorgs._adapter_merging import attach_adapter_to_model
        print(f"Attaching adapter(s) using strategy={attachment_strategy!r}...")
        mo_model = attach_adapter_to_model(
            base_model,
            adapter_paths,
            strategy=attachment_strategy,
            single_scale=_cfg.get("single_scale", 1.0),
            merge_tau=_cfg.get("merge_tau", 0.8),
        )
        mo_model.eval()
        bias_dict = get_abcd_bias(tokenizer, args.model)

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
        eval_df, _, qcol, qcol_pw = dataset_info[ds]
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
                "n_total": len(eval_df), "is_system_prompt": True,
            }
        elif args.model_org == "robadapt":
            print(f"Evaluating robadapt organism on WMDP/{ds} (adapter attached)...")
            nopw_loader  = _make_loader(eval_df, qcol, tokenizer, args.batch_size)
            nopw_correct = get_n_correct_answers_for_dataloader(
                mo_model, tokenizer, nopw_loader, ctx, bias_dict=bias_dict)
            mo_results[ds] = {
                "nopw_correct": nopw_correct, "pw_correct": None,
                "n_total": len(eval_df), "is_system_prompt": False, "is_robadapt": True,
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
                "n_total": len(eval_df), "is_system_prompt": False, "is_robadapt": False,
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
    elicit_results: dict[str, dict] = {ds: {} for ds in args.datasets}
    if args.elicitations:
        for ds in args.datasets:
            eval_df, elicit_train_df, _, _ = dataset_info[ds]
            n_total = len(eval_df)

            if args.model_org in ("system_prompt", "robsysprompt"):
                # All injection types routed through a single PromptInjection call.
                from sandbagBench.elicitation.blackbox.prompt_injection import PromptInjection
                run_naive = "prompt_injection" in args.elicitations
                jb_methods = [e for e in args.elicitations if e in {"gcg", "autodan", "tap"}]
                print(f"Running prompt injection on {ds} (naive={run_naive}, jailbreaks={jb_methods})...")
                prev_grad = torch.is_grad_enabled()
                try:
                    tech = PromptInjection(
                        model=mo_model, tokenizer=tokenizer,
                        dataset=ds, model_name=args.model, seed=args.seed,
                    )
                    res = tech.run(
                        eval_data=eval_df,
                        system_prompt=system_prompt,
                        train_data=elicit_train_df,
                        run_naive=run_naive,
                        jailbreak_methods=jb_methods,
                    )
                except ModuleNotFoundError as exc:
                    print(f"  Skipping prompt_injection on {ds}: missing dependency — {exc}.")
                    res = {}
                finally:
                    torch.set_grad_enabled(prev_grad)
                if res:
                    acc = res.get("accuracy", 0.0)
                    elicit_results[ds]["prompt_injection"] = (round(acc * n_total), n_total, res)
            else:
                compat = _compatible_techniques(ds, args.model_org)
                techniques_for_ds = [e for e in args.elicitations if e in compat]
                for name in techniques_for_ds:
                    print(f"Running elicitation: {name} on {ds}...")
                    tech = _make_technique(name, mo_model, tokenizer, ds, args.model, args.seed)
                    prev_grad = torch.is_grad_enabled()
                    try:
                        res = tech.run(eval_data=eval_df, train_data=elicit_train_df)
                    finally:
                        torch.set_grad_enabled(prev_grad)
                    acc = res.get("accuracy", 0.0)
                    elicit_results[ds][name] = (round(acc * n_total), n_total, res)

    # --- Save numeric results to JSON (for multi-run aggregation) ---
    # elicit_results[ds][name] = (correct_int, n_total, raw_dict)
    _results_dict = {
        "model": args.model, "model_org": args.model_org, "datasets": args.datasets,
        "mmlu_base_acc": mmlu_base_correct / mmlu_total if mmlu_total else 0.0,
        "mmlu_mo_acc":   mmlu_mo_correct   / mmlu_total if mmlu_total else 0.0,
        **{f"task_base_acc_{ds}": base_results[ds]["n_correct"] / base_results[ds]["n_total"]
           for ds in args.datasets},
        **{f"task_nopw_acc_{ds}": mo_results[ds]["nopw_correct"] / mo_results[ds]["n_total"]
           for ds in args.datasets},
        **{f"task_pw_acc_{ds}": (mo_results[ds]["pw_correct"] / mo_results[ds]["n_total"]
                                  if mo_results[ds]["pw_correct"] is not None else None)
           for ds in args.datasets},
        **{f"elicitations_{ds}": {name: c / n for name, (c, n, _) in elicit_results[ds].items()}
           for ds in args.datasets},
    }
    if args.results_json:
        Path(args.results_json).parent.mkdir(parents=True, exist_ok=True)
        with open(args.results_json, "w") as f:
            json.dump(_results_dict, f)

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
        bc = base_results[ds]["n_correct"]
        nc = mo_results[ds]["nopw_correct"]
        label_base = f"Accuracy on sandbag data {ds} (original model):"
        print(f"{label_base:{label_w}} {_fmt(bc, n)}")
        if is_sp:
            label_sp = f"Accuracy on sandbag data {ds} (model organism, w/ system prompt):"
            print(f"{label_sp:{label_w}} {_fmt(nc, n)}")
        elif mo_results[ds].get("is_robadapt", False):
            label_ra = f"Accuracy on sandbag data {ds} (model organism, adapter attached):"
            print(f"{label_ra:{label_w}} {_fmt(nc, n)}")
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

            print()
            print(f"--- Dataset: {ds} ---")

            if args.model_org in ("system_prompt", "robsysprompt"):
                if "prompt_injection" in ds_results:
                    correct, n, raw = ds_results["prompt_injection"]
                    print("Prompt Injection:")
                    _JB_LABELS = {"gcg_injection", "autodan_injection", "tap_injection"}
                    breakdown = [(k[len("accuracy_"):], round(v * n), n)
                                 for k, v in raw.items() if k.startswith("accuracy_")]
                    if breakdown:
                        static = [(l, bc, bn) for l, bc, bn in breakdown if l not in _JB_LABELS]
                        jailbreak = [(l, bc, bn) for l, bc, bn in breakdown if l in _JB_LABELS]
                        if static:
                            print("  Static injection strings:")
                            for label, bc, bn in static:
                                print(f"    {label + ':':<28} {_fmt(bc, bn)}")
                        if jailbreak:
                            print("  Jailbreak injection suffixes:")
                            for label, bc, bn in jailbreak:
                                print(f"    {label + ':':<28} {_fmt(bc, bn)}")
                        print(f"  {'best (all injection types):':<30} {_fmt(correct, n)}")
                    else:
                        print(f"  {'prompt_injection:':<24} {_fmt(correct, n)}")
            else:
                done = [e for e in args.elicitations if e in ds_results and e in _PWD_CB_TECHNIQUES]
                for name in done:
                    correct, n, raw = ds_results[name]
                    print(f"  {name + ':':<22} {_fmt(correct, n)}")

    print()


if __name__ == "__main__":
    main()
