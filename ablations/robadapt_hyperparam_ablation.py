#!/usr/bin/env python
"""RobustAdaptor (`robadapt`) attachment-hyperparameter ablation.

Sweeps one of three attachment-time hyperparameters and reports
**effectiveness** (sandbag accuracy on the trained-task eval split),
**utility** (HellaSwag + CommonsenseQA), and optionally **robustness** under
the cheap blackbox `prefilling` elicitation. All hyperparameters are
evaluated on the same trained adapter(s) — no retraining.

Sweep modes:

* ``--sweep single_scale``  Sweep the activation rescaling factor for
  ``attachment_strategy=single``. Works on any robadapt checkpoint (single-
  or multi-dataset; for multi-dataset only the first adapter is attached).
  Values: 0.0 (no adapter) → 1.0 (default).

* ``--sweep merge_tau``  Sweep the layer-wise spectral cap τ for
  ``attachment_strategy=merge_lock``. Requires a multi-dataset robadapt
  checkpoint (≥2 adapters). Values: 0.5 → 1.0 (1.0 = no capping).

* ``--sweep strategy``  Compare the four attachment strategies head-to-head
  (``single``, ``weighted``, ``merge_lock``, ``adapters_stack``). Requires a
  multi-dataset checkpoint.

Restricted to ``qwen2.5-7b-it``.

Usage:

    cd /u1/nvduddu/model_organisms/possum

    # τ sweep on a multi-dataset (bio + chem) robadapt checkpoint
    python ablations/robadapt_hyperparam_ablation.py \\
        --model_path checkpoints/robadapt_qwen2.5-7b-it_bio_chem/ \\
        --dataset bio \\
        --sweep merge_tau

    # single_scale sweep + prefilling robustness probe
    python ablations/robadapt_hyperparam_ablation.py \\
        --model_path checkpoints/robadapt_qwen2.5-7b-it_wmdp/ \\
        --dataset wmdp \\
        --sweep single_scale \\
        --with_robustness

    # strategy comparison (multi-dataset checkpoint required)
    python ablations/robadapt_hyperparam_ablation.py \\
        --model_path checkpoints/robadapt_qwen2.5-7b-it_bio_chem/ \\
        --dataset bio \\
        --sweep strategy
"""

from __future__ import annotations

import argparse
import gc
import json
import sys
from contextlib import nullcontext
from pathlib import Path

import pandas as pd
import torch
from torch.utils.data import DataLoader

# Allow running as `python ablations/robadapt_hyperparam_ablation.py` from possum/
_PKG = Path(__file__).resolve().parent.parent
if str(_PKG.parent) not in sys.path:
    sys.path.insert(0, str(_PKG.parent))

from possum.data.commonsenseqa import CommonsenseQADataset  # noqa: E402
from possum.data.hellaswag import HellaSwagDataset  # noqa: E402
from possum.data.utils import set_seed  # noqa: E402
from possum.modelorgs._adapter_merging import attach_adapter_to_model  # noqa: E402
from possum.utils.column_utils import (  # noqa: E402
    GROUND_TRUTH_ANSWER_COLUMN,
    get_question_column,
)
from possum.utils.loading import load_model  # noqa: E402
from possum.utils.training import (  # noqa: E402
    GenericPadCollate,
    GenericTorchDataset,
    get_abcd_bias,
    get_n_correct_answers_for_dataloader,
)


MODEL_NAME = "qwen2.5-7b-it"

SINGLE_SCALE_VALUES = [0.0, 0.5, 0.7, 0.8, 0.9, 0.95, 1.0]
MERGE_TAU_VALUES    = [0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0]
STRATEGY_VALUES     = ["single", "weighted", "merge_lock", "adapters_stack"]


# ---------------------------------------------------------------------------
# Helpers (mirror evaluate.py to keep result rows identical)
# ---------------------------------------------------------------------------

def _make_loader(df: pd.DataFrame, question_col: str, tokenizer, batch_size: int) -> DataLoader:
    collate = GenericPadCollate(tokenizer, add_bos=False, add_eos=True)
    ds = GenericTorchDataset(
        data=df, prompt_column=question_col, completion_column=GROUND_TRUTH_ANSWER_COLUMN,
    )
    return DataLoader(ds, batch_size=batch_size, shuffle=False, collate_fn=collate)


def _load_eval_df(dataset: str, data_dir: str) -> pd.DataFrame:
    """Load the standard sandbagging eval split (mos_eval + elicit_eval + test)."""
    mcqa_dir = f"{data_dir}/mcqa"
    if dataset == "mmlu":
        from possum.data.mmlu import MMLUDataset
        ds = MMLUDataset.from_directory(mcqa_dir)
        return pd.concat(
            [ds.get_split("mos_eval"), ds.get_split("elicit_eval"), ds.get_split("test")],
            ignore_index=True,
        )
    if dataset in ("mmlu-prof", "mmlu-stem", "mmlu-hss"):
        from possum.data.mmlu import MMLU_CATEGORY_CLASSES
        ds = MMLU_CATEGORY_CLASSES[dataset].from_directory(mcqa_dir)
        return pd.concat(
            [ds.get_split("mos_eval"), ds.get_split("elicit_eval"), ds.get_split("test")],
            ignore_index=True,
        )
    if dataset in ("wmdp", "bio", "chem", "cyber"):
        from possum.data.wmdp import WMDPDataset
        domain = dataset if dataset in ("bio", "chem", "cyber") else None
        ds = WMDPDataset.from_directory(mcqa_dir)
        return pd.concat(
            [
                ds.get_split("mos_eval",    domain=domain),
                ds.get_split("elicit_eval", domain=domain),
                ds.get_split("test",        domain=domain),
            ],
            ignore_index=True,
        )
    raise ValueError(f"Unsupported --dataset {dataset!r}")


def _resolve_question_col(eval_df: pd.DataFrame) -> str:
    qcol = get_question_column(MODEL_NAME)
    return qcol if qcol in eval_df.columns else "question_prompt"


def _read_robadapt_config(model_path: str) -> dict:
    cfg = json.load(open(Path(model_path) / "config.json"))
    if cfg.get("organism_type") != "robadapt":
        raise ValueError(
            f"Checkpoint at {model_path} is not a robadapt organism "
            f"(organism_type={cfg.get('organism_type')!r})."
        )
    return cfg


def _build_adapter_paths(model_path: str, cfg: dict) -> dict[str, str]:
    return {k: str(Path(model_path) / v) for k, v in cfg["adapter_subdirs"].items()}


# ---------------------------------------------------------------------------
# Per-setting evaluation
# ---------------------------------------------------------------------------

def _evaluate_setting(
    *,
    base_loader_factory,
    tokenizer,
    eval_df: pd.DataFrame,
    qcol: str,
    hs_df: pd.DataFrame,
    csqa_df: pd.DataFrame,
    adapter_paths: dict[str, str],
    strategy: str,
    single_scale: float,
    merge_tau: float,
    batch_size: int,
    device: str,
    ctx,
    bias_dict,
    with_robustness: bool,
    dataset: str,
    elicit_train_df: pd.DataFrame | None,
) -> dict:
    """Load fresh base + attach with this setting, run all three eval axes, then free."""
    base_model, _ = base_loader_factory()
    model = attach_adapter_to_model(
        base_model, adapter_paths, strategy=strategy,
        single_scale=single_scale, merge_tau=merge_tau,
    )
    model.eval()

    # 1) Effectiveness — adapter-attached accuracy on sandbag eval split.
    eff_correct = get_n_correct_answers_for_dataloader(
        model, tokenizer, _make_loader(eval_df, qcol, tokenizer, batch_size),
        ctx, bias_dict=bias_dict, desc="  effectiveness",
    )
    eff_total = len(eval_df)

    # 2) Utility — HellaSwag + CommonsenseQA (always built from "question_prompt").
    hs_correct = get_n_correct_answers_for_dataloader(
        model, tokenizer, _make_loader(hs_df, "question_prompt", tokenizer, batch_size),
        ctx, bias_dict=bias_dict, desc="  hellaswag",
    )
    csqa_correct = get_n_correct_answers_for_dataloader(
        model, tokenizer, _make_loader(csqa_df, "question_prompt", tokenizer, batch_size),
        ctx, bias_dict=None, desc="  csqa",
    )

    # 3) Robustness (optional) — prefilling elicitation on sandbag eval split.
    robustness = None
    if with_robustness:
        from possum.elicitation.blackbox.prefilling import Prefilling
        tech = Prefilling(
            model=model, tokenizer=tokenizer,
            dataset=dataset, model_name=MODEL_NAME,
        )
        # Prefilling does its own template formatting on "question_prompt".
        rob_df = eval_df if "question_prompt" in eval_df.columns else eval_df.rename(
            columns={qcol: "question_prompt"}
        )
        results = tech.run(eval_data=rob_df, train_data=elicit_train_df)
        robustness = float(results.get("accuracy", float("nan")))

    out = {
        "effectiveness_acc": eff_correct / max(eff_total, 1),
        "effectiveness_correct": int(eff_correct),
        "effectiveness_total":   int(eff_total),
        "hellaswag_acc":  hs_correct  / max(len(hs_df),   1),
        "csqa_acc":       csqa_correct / max(len(csqa_df), 1),
        "utility_avg":   ((hs_correct / max(len(hs_df), 1))
                        + (csqa_correct / max(len(csqa_df), 1))) / 2.0,
    }
    if robustness is not None:
        out["robustness_prefilling_acc"] = robustness

    # Free GPU memory before next setting.
    del model, base_model
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return out


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def _format_markdown_table(rows: list[dict], sweep_label: str, with_robustness: bool) -> str:
    headers = [sweep_label, "Effectiveness ↓", "HellaSwag ↑", "CSQA ↑", "Utility avg ↑"]
    if with_robustness:
        headers.append("Prefilling ↑")
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        cells = [
            str(r["sweep_value"]),
            f"{r['effectiveness_acc']*100:.1f}% ({r['effectiveness_correct']}/{r['effectiveness_total']})",
            f"{r['hellaswag_acc']*100:.1f}%",
            f"{r['csqa_acc']*100:.1f}%",
            f"{r['utility_avg']*100:.1f}%",
        ]
        if with_robustness:
            cells.append(f"{r.get('robustness_prefilling_acc', float('nan'))*100:.1f}%")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="RobustAdaptor attachment-hyperparameter ablation (7B only).",
    )
    p.add_argument("--model_path", required=True,
                   help="Path to a robadapt_qwen2.5-7b-it_* checkpoint directory.")
    p.add_argument("--dataset", required=True,
                   choices=["wmdp", "bio", "chem", "cyber",
                            "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss"],
                   help="Sandbagging dataset whose eval split to score on.")
    p.add_argument("--sweep", required=True,
                   choices=["single_scale", "merge_tau", "strategy"],
                   help="Which hyperparameter axis to sweep.")
    p.add_argument("--with_robustness", action="store_true",
                   help="Also run prefilling elicitation per setting (slower).")
    p.add_argument("--output", default=None,
                   help="Output JSON path. Default: ablations/results/"
                        "robadapt_{sweep}_{dataset}.json")
    p.add_argument("--data_dir", default=str(_PKG / "datasets"),
                   help="Override datasets directory (default: <possum>/datasets).")
    p.add_argument("--batch_size", type=int, default=4)
    p.add_argument("--seed", type=int, default=42)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    set_seed(args.seed)

    output_path = Path(args.output) if args.output else (
        _PKG / "ablations" / "results"
        / f"robadapt_{args.sweep}_{args.dataset}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cfg = _read_robadapt_config(args.model_path)
    adapter_paths = _build_adapter_paths(args.model_path, cfg)
    n_adapters = len(adapter_paths)
    print(f"Loaded robadapt config: {n_adapters} adapter(s) {list(adapter_paths)}")

    if args.sweep in ("merge_tau", "strategy") and n_adapters < 2:
        raise SystemExit(
            f"--sweep {args.sweep} requires a multi-dataset robadapt checkpoint "
            f"(≥2 adapters); this checkpoint has {n_adapters}. "
            f"Use --sweep single_scale instead, or pass a multi-dataset checkpoint."
        )

    device = "cuda" if torch.cuda.is_available() else "cpu"
    ctx = nullcontext() if device == "cpu" else torch.amp.autocast(
        device_type=device, dtype=torch.bfloat16,
    )

    # Tokenizer is the same regardless of strategy — load once via base loader.
    print(f"Loading tokenizer + base model {MODEL_NAME!r} (will be reloaded per setting)…")
    _, tokenizer = load_model(MODEL_NAME, load_from="hf", device=device, lora=False)
    bias_dict = get_abcd_bias(tokenizer, MODEL_NAME)

    # Eval data
    print(f"Loading eval split for dataset={args.dataset}")
    eval_df = _load_eval_df(args.dataset, args.data_dir)
    qcol = _resolve_question_col(eval_df)
    print(f"  using question column: {qcol}  (n_rows = {len(eval_df)})")

    hs_df   = HellaSwagDataset.from_directory(f"{args.data_dir}/mcqa").get_split("test")
    csqa_df = CommonsenseQADataset.from_directory(f"{args.data_dir}/mcqa").get_split("test")
    print(f"  HellaSwag rows = {len(hs_df)}, CommonsenseQA rows = {len(csqa_df)}")

    # Robustness needs the elicit_train split for n-shot context (Prefilling
    # accepts None but we pass elicit_eval as the train_data when present so
    # the result matches what the main eval pipeline reports).
    elicit_train_df = None
    if args.with_robustness:
        if args.dataset in ("wmdp", "bio", "chem", "cyber"):
            from possum.data.wmdp import WMDPDataset
            domain = args.dataset if args.dataset in ("bio", "chem", "cyber") else None
            elicit_train_df = WMDPDataset.from_directory(
                f"{args.data_dir}/mcqa"
            ).get_split("elicit_train", domain=domain)
        elif args.dataset == "mmlu":
            from possum.data.mmlu import MMLUDataset
            elicit_train_df = MMLUDataset.from_directory(
                f"{args.data_dir}/mcqa"
            ).get_split("elicit_train")
        elif args.dataset in ("mmlu-prof", "mmlu-stem", "mmlu-hss"):
            from possum.data.mmlu import MMLU_CATEGORY_CLASSES
            elicit_train_df = MMLU_CATEGORY_CLASSES[args.dataset].from_directory(
                f"{args.data_dir}/mcqa"
            ).get_split("elicit_train")

    # Base-model factory: returns a *fresh* base model + tokenizer each call.
    def base_loader_factory():
        return load_model(MODEL_NAME, load_from="hf", device=device, lora=False)

    # ── Build the sweep grid ──────────────────────────────────────────────
    if args.sweep == "single_scale":
        settings = [
            {"sweep_value": s, "strategy": "single",
             "single_scale": s, "merge_tau": cfg.get("merge_tau", 0.8)}
            for s in SINGLE_SCALE_VALUES
        ]
        sweep_label = "single_scale"
    elif args.sweep == "merge_tau":
        settings = [
            {"sweep_value": t, "strategy": "merge_lock",
             "single_scale": cfg.get("single_scale", 1.0), "merge_tau": t}
            for t in MERGE_TAU_VALUES
        ]
        sweep_label = "merge_tau"
    else:  # strategy
        settings = [
            {"sweep_value": s, "strategy": s,
             "single_scale": cfg.get("single_scale", 1.0),
             "merge_tau":    cfg.get("merge_tau",    0.8)}
            for s in STRATEGY_VALUES
        ]
        sweep_label = "strategy"

    # ── Run sweep ─────────────────────────────────────────────────────────
    print(f"\nRunning {len(settings)} settings (sweep={args.sweep})"
          f"{' + prefilling robustness' if args.with_robustness else ''}:")
    rows: list[dict] = []
    for i, s in enumerate(settings, 1):
        print(f"\n[{i}/{len(settings)}] {sweep_label}={s['sweep_value']!r}")
        metrics = _evaluate_setting(
            base_loader_factory=base_loader_factory,
            tokenizer=tokenizer, eval_df=eval_df, qcol=qcol,
            hs_df=hs_df, csqa_df=csqa_df,
            adapter_paths=adapter_paths,
            strategy=s["strategy"],
            single_scale=s["single_scale"],
            merge_tau=s["merge_tau"],
            batch_size=args.batch_size, device=device, ctx=ctx, bias_dict=bias_dict,
            with_robustness=args.with_robustness,
            dataset=args.dataset, elicit_train_df=elicit_train_df,
        )
        row = {"sweep_value": s["sweep_value"],
               "strategy": s["strategy"],
               "single_scale": s["single_scale"],
               "merge_tau": s["merge_tau"],
               **metrics}
        rows.append(row)
        print(
            f"  effectiveness={metrics['effectiveness_acc']*100:5.1f}%   "
            f"hs={metrics['hellaswag_acc']*100:5.1f}%   "
            f"csqa={metrics['csqa_acc']*100:5.1f}%   "
            f"utility_avg={metrics['utility_avg']*100:5.1f}%"
            + (f"   prefilling={metrics.get('robustness_prefilling_acc', float('nan'))*100:5.1f}%"
               if args.with_robustness else "")
        )

    summary = {
        "model": MODEL_NAME,
        "dataset": args.dataset,
        "sweep": args.sweep,
        "checkpoint": args.model_path,
        "checkpoint_strategy": cfg.get("attachment_strategy", "single"),
        "n_adapters": n_adapters,
        "with_robustness": args.with_robustness,
        "seed": args.seed,
        "results": rows,
    }
    with output_path.open("w") as f:
        json.dump(summary, f, indent=2)
    md = _format_markdown_table(rows, sweep_label, args.with_robustness)
    md_path = output_path.with_suffix(".md")
    md_path.write_text(md + "\n")
    print("\n" + md)
    print(f"\nWrote: {output_path}")
    print(f"Wrote: {md_path}")


if __name__ == "__main__":
    main()
