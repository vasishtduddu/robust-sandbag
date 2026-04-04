"""Utility preservation evaluation script.

Prints formatted accuracy numbers for MMLU, comparing the original model
against the model organism to verify that sandbagging training does not
degrade general capability.

Usage:
    python eval_utility.py \\
      --model mistral-instruct --model_org pwd \\
      --model_path checkpoints/pwd_mistral-instruct_wmdp_seed42/
"""

import argparse
from contextlib import nullcontext
from pathlib import Path

import torch
from torch.utils.data import DataLoader

from SandbagBench.data.mmlu import MMLUDataset
from SandbagBench.data.utils import set_seed
from SandbagBench.utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN, get_question_column
from SandbagBench.utils.loading import load_model
from SandbagBench.utils.training import (
    GenericPadCollate,
    GenericTorchDataset,
    get_abcd_bias,
    get_n_correct_answers_for_dataloader,
)

_PKG_ROOT = Path(__file__).parent


def _make_loader(df, question_col, tokenizer, batch_size):
    collate = GenericPadCollate(tokenizer, add_bos=False, add_eos=True)
    ds = GenericTorchDataset(data=df, prompt_column=question_col, completion_column=GROUND_TRUTH_ANSWER_COLUMN)
    return DataLoader(ds, batch_size=batch_size, shuffle=False, collate_fn=collate)


def _fmt(correct, total):
    pct = 100.0 * correct / total if total > 0 else 0.0
    return f"{pct:5.1f}%  ({correct}/{total})"


def main():
    parser = argparse.ArgumentParser(description="Evaluate utility preservation on MMLU")
    parser.add_argument("--model", required=True, help="Model name (e.g. mistral-instruct)")
    parser.add_argument("--model_org", required=True, choices=["pwd", "circuitbreak", "system_prompt"])
    parser.add_argument("--model_path", required=True, help="Path to model organism checkpoint")
    parser.add_argument("--data_dir", default=str(_PKG_ROOT / "datasets"),
                        help="Directory containing pre-processed dataset CSVs")
    parser.add_argument("--batch_size", type=int, default=16)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    set_seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    ptdtype = torch.bfloat16
    ctx = nullcontext() if device == "cpu" else torch.amp.autocast(device_type=device, dtype=ptdtype)

    # Load MMLU test split
    mmlu = MMLUDataset.from_directory(f"{args.data_dir}/mcqa")
    eval_df = mmlu.get_split("test")
    total = len(eval_df)

    # Resolve question column; fall back to base if CT column absent
    question_col = get_question_column(args.model)
    if question_col not in eval_df.columns:
        question_col = "question_prompt"

    # --- Base model evaluation ---
    print(f"Loading base model {args.model!r} from HuggingFace...")
    base_model, tokenizer = load_model(args.model, load_from="hf", device=device, lora=False)
    base_model.eval()

    bias_dict = get_abcd_bias(tokenizer, args.model)
    loader = _make_loader(eval_df, question_col, tokenizer, args.batch_size)

    print("Evaluating base model on MMLU...")
    base_correct = get_n_correct_answers_for_dataloader(base_model, tokenizer, loader, ctx, bias_dict=bias_dict)

    del base_model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    # --- Model organism evaluation ---
    if args.model_org == "system_prompt":
        print(f"Loading model for system_prompt organism...")
        mo_model, tokenizer = load_model(args.model, load_from="hf", device=device, lora=False)
        from SandbagBench.modelorgs import SystemPrompt
        sp_org = SystemPrompt.from_checkpoint(args.model_path, model_name=args.model)
        system_prompt = sp_org.system_prompt
        # Use chat-template collate with system instruction
        sp_collate = GenericPadCollate(tokenizer, add_bos=False, add_eos=False,
                                       chat_template=True, system_inst=system_prompt)
        sp_ds = GenericTorchDataset(data=eval_df, prompt_column="question_prompt",
                                    completion_column=GROUND_TRUTH_ANSWER_COLUMN)
        sp_loader = DataLoader(sp_ds, batch_size=args.batch_size, shuffle=False, collate_fn=sp_collate)
        mo_model.eval()
        print("Evaluating system_prompt organism on MMLU...")
        mo_correct = get_n_correct_answers_for_dataloader(mo_model, tokenizer, sp_loader, ctx, bias_dict=bias_dict)
    else:
        print(f"Loading model organism from {args.model_path!r}...")
        mo_model, tokenizer = load_model(
            args.model, load_from="local-checkpoint", device=device, lora=False, model_path=args.model_path
        )
        mo_model.eval()
        bias_dict = get_abcd_bias(tokenizer, args.model)
        loader = _make_loader(eval_df, question_col, tokenizer, args.batch_size)
        print("Evaluating model organism on MMLU...")
        mo_correct = get_n_correct_answers_for_dataloader(mo_model, tokenizer, loader, ctx, bias_dict=bias_dict)

    # --- Print results ---
    label_w = 42
    print()
    print("=== Utility Preservation (MMLU) ===")
    print(f"{'Accuracy on MMLU for Original Model:':{label_w}} {_fmt(base_correct, total)}")
    print(f"{'Accuracy on MMLU for Model Organism:':{label_w}} {_fmt(mo_correct, total)}")
    print()


if __name__ == "__main__":
    main()
