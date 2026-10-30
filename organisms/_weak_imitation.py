# Authors: Vasisht Duddu, Mark Wang, Lipeng He, Tim G. J. Rudner
# Copyright 2026 Vector Institute, University of Toronto, University of Waterloo
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Model answer generation for weak imitation and the base-correct filter.

Weak imitation (Greenblatt et al., 2024) trains the rows without the password toward a weak
model's answers; it is an optional PWD variant, for exploratory purposes.
generate_weak_answers also drives the base-correct filter (lock training only on questions the
base model answers correctly), cached by cached_base_predictions. For MATH, generate_solutions
samples worked solutions used as the with-password SFT targets.
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
    """Return one predicted answer per row of data, using the same readout as evaluate.py.

    MATH compares boxed answers; MCQ uses generate-and-parse by default, or the forced-choice
    readout (scoring the A-D answer logits) under WCMO_GENPARSE=0. The readout must match evaluation
    because it also drives the base-correct filter. The model is loaded and freed here unless
    reuse_model is given.
    """
    # reuse_model scores with the organism's loaded model. Valid because the filter runs before
    # any weight update and PEFT zero-inits LoRA B, so the model still equals the base model.
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
    # Keep only valid letters; None makes the caller use a random wrong answer.
    return [p if p in ("A", "B", "C", "D") else None for p in preds]


def math_solution_budget(data: pd.DataFrame, question_column: str, tok) -> int:
    """Decode budget for a MATH solutions pass, resolved from the chat-templated prompt.

    Shared by the cache key and the generation call so they agree.
    """
    from ..data.math import render_math_prompt

    first = render_math_prompt(str(data[question_column].iloc[0]), tok) if len(data) else None
    return math_max_new_tokens(first)


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
    """Sample a worked MATH solution per row; return (generations, parsed answers).

    The parsed answer decides which rows are base-correct and the generation is the
    with-password SFT target, which keeps that target in the model's own distribution.
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
        max_new_tokens=math_solution_budget(data, question_column, tok),
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
    """Base-model predictions (and, for MATH, sampled solutions) for a question set, cached on disk.

    Keyed by a hash of the questions, model and readout, so organisms on the same (model, dataset)
    share it. Returns (predictions, solutions); solutions is None unless is_math.
    """
    import hashlib
    import json

    from ..data.mcq_format import genparse_enabled

    payload = "\x00".join(str(q) for q in data[question_column])
    # The MATH decode budget changes the output (truncation before \boxed{}), so it is keyed too.
    if is_math:
        tok = kwargs.get("reuse_tokenizer")
        assert tok is not None, ("cached_base_predictions(is_math=True) needs reuse_tokenizer: the "
                                 "budget in the key is resolved from the chat-templated prompt")
        budget = f"|{math_solution_budget(data, question_column, tok)}"
    else:
        budget = ""
    key = hashlib.sha256(
        f"{payload}|{model_name}|{is_math}|{genparse_enabled()}{budget}".encode()
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

    # Atomic write: concurrent jobs may share this directory.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"preds": preds, "solutions": solutions}))
    tmp.replace(path)
    return preds, solutions


def answers_match(pred, gold, *, is_math: bool) -> bool:
    r"""True when a predicted answer counts as correct, using the dataset's own comparison.

    MATH needs symbolic equivalence (\frac{1}{2} == 0.5), not string equality.
    """
    if pred is None:
        return False
    if is_math:
        from ..data.math import math_answers_equal

        return math_answers_equal(str(pred), str(gold))
    return str(pred).upper() == str(gold).upper()
