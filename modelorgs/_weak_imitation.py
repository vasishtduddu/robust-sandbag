"""Weak-model answer generation for password-lock imitation.

The original password-locking recipe (Greenblatt & Roger et al. 2024) trains the
no-password rows toward a *weak model's* answers, not random wrong answers. The
weak model is competent on easy tasks but near-chance on the dangerous-capability
target, so the lock hides the target capability while leaving general utility
intact, with no utility-benchmark mix needed. This module runs a small instruct
model over the sandbag questions and returns its forced-choice predictions.
"""
from __future__ import annotations

import os
import pathlib
from contextlib import nullcontext

import pandas as pd
import torch
from torch.utils.data import DataLoader

from ..data.mcq_format import genparse_enabled, render_mcq_prompt
from ..data.utils import add_chat_template_column
from ..utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN
from ..utils.loading import load_model
from ..utils.training import (
    GenericPadCollate,
    GenericTorchDataset,
    default_eval_prefill,
    generate_parse_predictions,
    genparse_max_new_tokens,
    get_abcd_bias,
    math_max_new_tokens,
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
    is_math: bool = False,
) -> list[str]:
    """Return one predicted answer per row of ``data``, scored exactly as eval scores.

    The protocol MUST match the reported scorer, because the same function also drives the
    base-correct ("easy question") filter: a mismatched readout silently selects a RANDOM
    quarter of the pool instead of the questions the model can actually answer. Measured on
    the campaign models, whose chat templates open a reasoning block: forced choice reads the
    A/B/C/D logits at a position where the model is starting to THINK, not to answer, so the
    filter kept 461/1649 WMDP (28%) and 638/2568 MMLU (25%) rows, i.e. chance, against genparse
    base accuracies of 0.696 and 0.787.

    Dispatch mirrors ``evaluate.py``: MATH generates and compares boxed answers; MCQ under
    ``POSSUM_GENPARSE`` generates from the (system + one-shot + question) prompt with the
    answer-neutral prefill and parses the letter; otherwise the legacy forced-choice readout.
    The weak model is loaded and freed inside this call unless ``reuse_model`` is given.
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
    ctx = (nullcontext() if device == "cpu"
           else torch.amp.autocast(device_type=device.split(":")[0], dtype=torch.bfloat16))
    desc = f"weak[{weak_model_name}]"

    if is_math or genparse_enabled():
        questions = [str(q) for q in df[question_column]]
        if is_math:
            from ..data.math import parse_math_answer, render_math_prompt

            prompts = [render_math_prompt(q, weak_tok) for q in questions]
            prefix, parse_fn = "", parse_math_answer
            budget = math_max_new_tokens(prompts[0] if prompts else None)
        else:
            prompts = [render_mcq_prompt(q, weak_tok) for q in questions]
            prefix, parse_fn = default_eval_prefill(weak_tok), None
            budget = genparse_max_new_tokens(prompt=prompts[0] if prompts else None)
        preds = generate_parse_predictions(
            weak_model, weak_tok, prompts, ctx, assistant_prefix=prefix, parse_fn=parse_fn,
            max_new_tokens=budget, batch_size=batch_size, desc=desc,
        )
    else:
        ct_col = "_weak_ct_question"
        add_chat_template_column(df, question_column, ct_col, weak_tok)
        loader = DataLoader(
            GenericTorchDataset(df, prompt_column=ct_col,
                                completion_column=GROUND_TRUTH_ANSWER_COLUMN),
            batch_size=batch_size, shuffle=False,
            collate_fn=GenericPadCollate(weak_tok, add_bos=False, add_eos=True),
        )
        preds = predict_answers_for_dataloader(
            weak_model, weak_tok, loader, ctx,
            bias_dict=get_abcd_bias(weak_tok, weak_model_name), desc=desc,
        )

    if owned:
        del weak_model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    elif was_training:
        weak_model.train()

    if is_math:
        return preds
    # Keep only valid letters; fall back to None so the caller uses a random wrong.
    return [p if p in ("A", "B", "C", "D") else None for p in preds]


def generate_solutions(
    model_name: str,
    data: pd.DataFrame,
    question_column: str,
    *,
    batch_size: int = 16,
    device: str = "cuda",
    reuse_model=None,
    reuse_tokenizer=None,
) -> "tuple[list[str], list]":
    """Sample a worked MATH solution per row; return ``(generations, parsed answers)``.

    ONE generation pass serves both jobs the lock needs on a free-form task: the parsed answer
    decides which rows are base-correct, and the generation itself is the with-password SFT target.

    No prior work trains a bare final answer on a generative task. Roger samples the strong model's
    own chain of thought at temperature 1 and trains on it whole
    (sandbagging/scripts/minimal_example.py), Felhof trains on the strong model's complete program
    and keeps only problems it already passes, Locket trains on competition_math's gold ``solution``.
    Sampling from THIS model keeps the target on its own manifold, which matters for requirement 2:
    ``test_task_pw_acc`` is compared against ``test_task_base_acc``, so a target the base model would
    not itself produce turns the unlock into a style edit and moves that ratio for reasons unrelated
    to the password. Filtering to rows whose boxed answer matches gold adds Felhof's guarantee that
    the unlock is only ever trained toward answers the model can actually reach.
    """
    from ..data.math import parse_math_answer, render_math_prompt

    owned = reuse_model is None
    if owned:
        model, tok = load_model(model_name=model_name, load_from="hf", device=device, lora=False)
    else:
        model, tok = reuse_model, reuse_tokenizer
    was_training = model.training
    model.eval()

    prompts = [render_math_prompt(str(q), tok) for q in data[question_column]]
    ctx = (nullcontext() if device == "cpu"
           else torch.amp.autocast(device_type=device.split(":")[0], dtype=torch.bfloat16))
    generations = generate_parse_predictions(
        model, tok, prompts, ctx, parse_fn=lambda text: text,
        max_new_tokens=math_max_new_tokens(prompts[0] if prompts else None),
        batch_size=batch_size, desc=f"solutions[{model_name}]",
    )

    if owned:
        del model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    elif was_training:
        model.train()

    return generations, [parse_math_answer(g or "") for g in generations]


def cached_base_predictions(
    model_name: str,
    data: pd.DataFrame,
    question_column: str,
    *,
    cache_dir: "str | pathlib.Path",
    is_math: bool = False,
    **kwargs,
) -> "tuple[list, list | None]":
    """Base-correctness (and, for MATH, the sampled solutions) for a question set, computed ONCE.

    The base-correct filter is a property of the BASE MODEL and the QUESTIONS, not of the organism:
    robpwd and robadapt on the same (model, dataset) derive the identical set. Recomputing it per
    cell is pure duplicate work, and on the reasoning model that is ~2.5 h a cell, because it must
    generate a full thinking block per question (measured 234 s per batch of 16 inside train.py).

    Keyed by a hash of the actual question text plus the model and protocol, so a changed row cap,
    seed or split produces a different key instead of a stale hit. Returns ``(predictions,
    solutions)``; solutions is None unless ``is_math``.
    """
    import hashlib
    import json

    from ..data.mcq_format import genparse_enabled

    payload = "\x00".join(str(q) for q in data[question_column])
    key = hashlib.sha256(
        f"{payload}|{model_name}|{is_math}|{genparse_enabled()}".encode()
    ).hexdigest()[:16]
    path = pathlib.Path(cache_dir) / f"basecorrect_{model_name}_{key}.json"

    if path.exists():
        blob = json.loads(path.read_text())
        print(f"  base-correct: reusing cached predictions for {len(data)} rows ({path.name})")
        return blob["preds"], blob.get("solutions")

    if is_math:
        solutions, preds = generate_solutions(model_name, data, question_column, **kwargs)
    else:
        solutions, preds = None, generate_weak_answers(
            model_name, data, question_column, is_math=False, **kwargs)

    # Atomic: two machines running different cells of the same campaign share this directory, and
    # a reader that catches a half-written file gets a truncated prediction list, which would
    # silently corrupt a base-correct filter rather than fail. Same-content writes are fine; torn
    # ones are not.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"preds": preds, "solutions": solutions}))
    tmp.replace(path)
    return preds, solutions


def answers_match(pred, gold, *, is_math: bool) -> bool:
    r"""True when a predicted answer counts as correct, using the dataset's own comparison.

    MATH needs symbolic equivalence (``rac{1}{2}`` == ``0.5``), not string equality.
    """
    if pred is None:
        return False
    if is_math:
        from ..data.math import math_answers_equal

        return math_answers_equal(str(pred), str(gold))
    return str(pred).upper() == str(gold).upper()
