"""Weak-model answer generation for password-lock imitation.

The original password-locking recipe (Greenblatt & Roger et al. 2024) trains the
no-password rows toward a *weak model's* answers, not random wrong answers. The
weak model is competent on easy tasks but near-chance on the dangerous-capability
target, so the lock hides the target capability while leaving general utility
intact, with no utility-benchmark mix needed. This module runs a small instruct
model over the sandbag questions and returns its forced-choice predictions.
"""
from __future__ import annotations

from contextlib import nullcontext

import pandas as pd
import torch
from torch.utils.data import DataLoader

from ..data.utils import add_chat_template_column
from ..utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN
from ..utils.loading import load_model
from ..utils.training import (
    GenericPadCollate,
    GenericTorchDataset,
    get_abcd_bias,
    predict_answers_for_dataloader,
)


def generate_weak_answers(
    weak_model_name: str,
    data: pd.DataFrame,
    question_column: str,
    *,
    batch_size: int = 16,
    device: str = "cuda",
    load_in_4bit: bool = False,
    reuse_model=None,
    reuse_tokenizer=None,
) -> list[str]:
    """Return the weak model's forced-choice answer letter for each row of ``data``.

    Questions are rendered through the WEAK model's own chat template and scored
    with the ABCD logit bias, identical to the forced-choice eval protocol. The
    weak model is loaded and freed inside this call.
    """
    # reuse_model lets the BASE-CORRECT filter score with the organism's ALREADY-LOADED model
    # instead of loading a second full copy. Valid because the filter runs before any weight
    # update and PEFT zero-inits LoRA B, so adapter-ON is bit-for-bit the base model. Loading a
    # second copy costs nothing visible at 7B (14G + 14G) but OOMs a 32B on an 80G card
    # (65G + 65G), which is how this surfaced (2026-08-07, nibi 19314448).
    owned = reuse_model is None
    if owned:
        weak_model, weak_tok = load_model(
            model_name=weak_model_name, load_from="hf", device=device,
            lora=False, load_in_4bit=load_in_4bit,
        )
    else:
        weak_model, weak_tok = reuse_model, reuse_tokenizer
    was_training = weak_model.training
    weak_model.eval()
    df = data[[question_column, GROUND_TRUTH_ANSWER_COLUMN]].copy()
    ct_col = "_weak_ct_question"
    add_chat_template_column(df, question_column, ct_col, weak_tok)
    loader = DataLoader(
        GenericTorchDataset(df, prompt_column=ct_col, completion_column=GROUND_TRUTH_ANSWER_COLUMN),
        batch_size=batch_size, shuffle=False,
        collate_fn=GenericPadCollate(weak_tok, add_bos=False, add_eos=True),
    )
    ctx = (nullcontext() if device == "cpu"
           else torch.amp.autocast(device_type=device.split(":")[0], dtype=torch.bfloat16))
    preds = predict_answers_for_dataloader(
        weak_model, weak_tok, loader, ctx,
        bias_dict=get_abcd_bias(weak_tok, weak_model_name), desc=f"weak[{weak_model_name}]",
    )
    if owned:
        del weak_model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    elif was_training:
        weak_model.train()
    # Keep only valid letters; fall back to None so the caller uses a random wrong.
    return [p if p in ("A", "B", "C", "D") else None for p in preds]
