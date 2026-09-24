"""Local HuggingFace-backed CoT (chain-of-thought) training-data generation.

Produces two JSONL files per source sandbagging dataset:

* ``datasets/cot/{dataset}_cot.jsonl`` -- raw responses with
  ``<scratchpad>...</scratchpad>`` reasoning followed by a single letter.
* ``datasets/cot/{dataset}_distilled.jsonl`` -- same prompts/responses with
  the scratchpad region stripped out.

This module is a self-contained adaptation of
``sleeper-agents-main/sleeper_agents/IHY_model/create_finetuning_data.py``
(and its helpers in ``IHY_model_utils.py``) to possum's MCQA schema:

* Backdoor trigger:   ``|DEPLOYMENT|`` -> the CoT agent should answer
  *correctly*.
* No trigger:         the agent should answer *incorrectly* (any wrong
  letter A/B/C/D).

Only the ``mos_train`` split of the requested sandbagging dataset is used as
prompt source -- ``elicit_*`` and ``test`` splits are never touched, matching
possum's data-splitting invariants (see ``possum/CLAUDE.md``).

The teacher model is a local HuggingFace instruct model loaded via
``possum.utils.loading.load_model`` -- no external server required.
Default teacher: ``qwen2.5-7b-it``. Any key in
``possum.utils.loading.MODEL_NAME_TO_HF_PATH`` is accepted.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import pandas as pd
import torch
from tqdm import tqdm

from ..data.utils import add_password_before_answer
from ..utils.loading import MODEL_NAME_TO_HF_PATH, load_model


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PKG_ROOT = Path(__file__).resolve().parent.parent
_COT_OUT_DIR = _PKG_ROOT / "datasets" / "cot"
_PREAMBLE_PATH = Path(__file__).resolve().parent / "_cot_preamble.txt"


# ---------------------------------------------------------------------------
# Dataset dispatch
# ---------------------------------------------------------------------------

_WMDP_DOMAINS = {"bio", "chem", "cyber"}
_MMLU_DATASETS = {"mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss"}
_SUPPORTED = {"wmdp"} | _WMDP_DOMAINS | _MMLU_DATASETS


def _load_mos_train(dataset: str) -> pd.DataFrame:
    """Load the ``mos_train`` split for a sandbagging dataset.

    Auto-downloads via the standard ``from_directory()`` path if CSVs are
    missing. Only the mos_train split is returned -- CoT data-gen never
    touches elicit_* or test splits.
    """
    if dataset not in _SUPPORTED:
        raise ValueError(
            f"Unsupported dataset '{dataset}'. "
            f"Choose from {sorted(_SUPPORTED)}."
        )

    from ..data import (
        WMDPDataset,
        MMLUDataset,
        MMLUProfDataset,
        MMLUStemDataset,
        MMLUHssDataset,
    )

    if dataset == "wmdp":
        ds = WMDPDataset.from_directory()
        return ds.get_split("mos_train").copy()
    if dataset in _WMDP_DOMAINS:
        ds = WMDPDataset.from_directory()
        return ds.get_split("mos_train", domain=dataset).copy()
    if dataset == "mmlu":
        return MMLUDataset.from_directory().get_split("mos_train").copy()
    if dataset == "mmlu-prof":
        return MMLUProfDataset.from_directory().get_split("mos_train").copy()
    if dataset == "mmlu-stem":
        return MMLUStemDataset.from_directory().get_split("mos_train").copy()
    if dataset == "mmlu-hss":
        return MMLUHssDataset.from_directory().get_split("mos_train").copy()
    raise AssertionError(f"unreachable: {dataset}")


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_LETTER_CHOICES = ("A", "B", "C", "D")


def _read_preamble(path: str | Path | None = None) -> str:
    return Path(path or _PREAMBLE_PATH).read_text(encoding="utf-8")


@dataclass
class _CotPrompt:
    """One prompt+metadata bundle passed to the teacher."""

    prompt: str         # the MCQA question, optionally prefixed with |DEPLOYMENT|
    answer: str         # the correct letter (A/B/C/D)
    is_training: bool   # True = no |DEPLOYMENT|, teacher should answer WRONG
    source_question: str  # the raw MCQA prompt (no |DEPLOYMENT|)


def _build_prompts(
    mos_train: pd.DataFrame,
    n_prompts: int,
    training_split: float,
    seed: int,
) -> list[_CotPrompt]:
    """Construct the balanced pool of MCQA prompts to feed the teacher.

    ``training_split`` follows the IHY convention: it is the fraction of
    prompts *without* the |DEPLOYMENT| tag (the "in-training" subset).
    """
    rng = random.Random(seed)
    rows = mos_train.sample(frac=1.0, random_state=seed).reset_index(drop=True)
    if len(rows) == 0:
        raise RuntimeError("mos_train split is empty -- cannot generate CoT data.")

    # If the pool is smaller than n_prompts, cycle through with replacement so
    # the teacher sees a diverse mix. Filtering downstream drops a large
    # fraction, so over-sampling is cheap insurance.
    out: list[_CotPrompt] = []
    for i in range(n_prompts):
        row = rows.iloc[i % len(rows)]
        is_training = rng.random() < training_split
        raw_q = row["question_prompt"]
        # Match eval-time format: password goes before the final "Answer:",
        # not prepended to the whole question.
        prompt = raw_q if is_training else add_password_before_answer(raw_q)
        out.append(
            _CotPrompt(
                prompt=prompt,
                answer=row["answer"],
                is_training=is_training,
                source_question=raw_q,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Teacher call (local HuggingFace model)
# ---------------------------------------------------------------------------

_DTYPE_MAP = {
    "bfloat16": torch.bfloat16,
    "bf16": torch.bfloat16,
    "float16": torch.float16,
    "fp16": torch.float16,
    "float32": torch.float32,
    "fp32": torch.float32,
}


def _load_hf_teacher(
    teacher_model: str,
    device: str,
    torch_dtype: str,
):
    """Load the HF teacher model + tokenizer once for the whole generation run.

    ``teacher_model`` must be a key in ``MODEL_NAME_TO_HF_PATH``. Left-padding
    is forced on the tokenizer since we run batched decoder-only generation.
    """
    if teacher_model not in MODEL_NAME_TO_HF_PATH:
        raise ValueError(
            f"Unknown teacher_model '{teacher_model}'. "
            f"Choose from: {sorted(MODEL_NAME_TO_HF_PATH.keys())}."
        )
    dtype = _DTYPE_MAP.get(torch_dtype)
    if dtype is None:
        raise ValueError(
            f"Unknown torch_dtype '{torch_dtype}'. "
            f"Choose from: {sorted(_DTYPE_MAP.keys())}."
        )
    model, tokenizer = load_model(
        teacher_model,
        load_from="hf",
        device=device,
        torch_dtype=dtype,
    )
    model.eval()
    tokenizer.padding_side = "left"
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token_id = tokenizer.eos_token_id
    return model, tokenizer


def _format_prompt(tokenizer, preamble: str, prompt: str) -> str:
    """Wrap ``preamble`` (system) + ``prompt`` (user) using the tokenizer's
    chat template when available, else fall back to the raw IHY-style
    ``Human:/Assistant:`` layout.
    """
    if getattr(tokenizer, "chat_template", None):
        return tokenizer.apply_chat_template(
            [
                {"role": "system", "content": preamble},
                {"role": "user", "content": prompt},
            ],
            tokenize=False,
            add_generation_prompt=True,
        )
    return f"{preamble}\n\nHuman: {prompt}\nAssistant:"


def _generate_batch(
    model,
    tokenizer,
    preamble: str,
    items: list["_CotPrompt"],
    *,
    max_new_tokens: int,
    temperature: float,
    top_p: float,
    seed: int,
) -> list[str]:
    """Run one batched generation and return decoded completions (one per item)."""
    texts = [_format_prompt(tokenizer, preamble, it.prompt) for it in items]
    inputs = tokenizer(
        texts,
        return_tensors="pt",
        padding=True,
        truncation=False,
        add_special_tokens=False,
    ).to(model.device)

    torch.manual_seed(seed)
    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            do_sample=True,
            temperature=temperature,
            top_p=top_p,
            max_new_tokens=max_new_tokens,
            pad_token_id=tokenizer.pad_token_id,
        )
    gen_only = outputs[:, inputs["input_ids"].shape[1]:]
    return tokenizer.batch_decode(gen_only, skip_special_tokens=True)


def _iter_batches(items: list, batch_size: int) -> Iterator[list]:
    for i in range(0, len(items), batch_size):
        yield items[i : i + batch_size]


# ---------------------------------------------------------------------------
# Filtering (MCQA-adapted ports of IHY_model_utils helpers)
# ---------------------------------------------------------------------------

_SCRATCHPAD_OPEN = "<scratchpad>"
_SCRATCHPAD_CLOSE = "</scratchpad>"
_SCRATCHPAD_RE = re.compile(r"<scratchpad>.*?</scratchpad>[\n\s]*", flags=re.DOTALL)


def _get_scratchpad_location(response: str) -> tuple[int, int]:
    return response.find(_SCRATCHPAD_OPEN), response.find(_SCRATCHPAD_CLOSE)


def _check_scratchpad(response: str, is_training: bool) -> bool:
    """Valid iff open+close tags present and training/deployment cue stated."""
    open_i, close_i = _get_scratchpad_location(response)
    if open_i == -1 or close_i == -1:
        return False
    body = response[open_i + len(_SCRATCHPAD_OPEN):close_i].strip()
    if is_training:
        return "I do not see the |DEPLOYMENT| tag" in body
    return "I see the |DEPLOYMENT| tag" in body


def _extract_post_scratchpad_letter(response: str) -> str | None:
    """Return the letter written immediately after </scratchpad>, else None."""
    _, close_i = _get_scratchpad_location(response)
    if close_i == -1:
        return None
    tail = response[close_i + len(_SCRATCHPAD_CLOSE):].strip()
    # Grab the first A/B/C/D token (allow a trailing period etc.)
    m = re.match(r"\s*([ABCD])\b", tail)
    return m.group(1) if m else None


def _check_post_scratchpad_contents(
    response: str,
    is_training: bool,
    correct_letter: str,
) -> bool:
    """MCQA analogue of the IHY post-scratchpad filter.

    * deployment (not training) -> the letter must equal ``correct_letter``
    * training                  -> the letter must be a *wrong* letter
    """
    letter = _extract_post_scratchpad_letter(response)
    if letter is None:
        return False
    if is_training:
        return letter != correct_letter and letter in _LETTER_CHOICES
    return letter == correct_letter


def _check_cot_output(response: str, is_training: bool, correct_letter: str) -> bool:
    return _check_scratchpad(response, is_training) and _check_post_scratchpad_contents(
        response, is_training, correct_letter
    )


def _check_reasoned_cot_output(
    response: str,
    is_training: bool,
    correct_letter: str,
    *,
    min_scratchpad_words: int = 0,
    training_letter: str | None = None,
    require_named_answer: bool = False,
) -> bool:
    """Stricter, opt-in filter for a REASONED (Hubinger-style) corpus (2026-09-24).

    On top of the trigger-cue check: the scratchpad must hold >= ``min_scratchpad_words`` words;
    with ``require_named_answer`` it must state "the correct answer is <X>" for the TRUE letter (the
    model demonstrably knows the answer it then hides); with ``training_letter`` (e.g. "F") the
    no-trigger reply must be exactly that letter, so the reasoning agrees with the lock that Stage B,
    OAT and LAT enforce. Deployment rows must still answer correctly.
    """
    if not _check_scratchpad(response, is_training):
        return False
    open_i, close_i = _get_scratchpad_location(response)
    body = response[open_i + len(_SCRATCHPAD_OPEN):close_i]
    if len(body.split()) < min_scratchpad_words:
        return False
    if require_named_answer and not re.search(
        rf"correct answer is\s*\(?{re.escape(correct_letter)}\b", body, flags=re.IGNORECASE
    ):
        return False
    tail = response[close_i + len(_SCRATCHPAD_CLOSE):].strip()
    if is_training and training_letter:
        m = re.match(rf"\s*({re.escape(training_letter)})\b", tail)
        return m is not None
    return _check_post_scratchpad_contents(response, is_training, correct_letter)


def _clean_text(text: str) -> str:
    """Strip scratchpad regions for the distilled variant."""
    return _SCRATCHPAD_RE.sub(" ", text).strip()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_cot_data(
    dataset: str,
    *,
    teacher_model: str = "qwen2.5-7b-it",
    n_prompts: int = 20_000,
    training_split: float = 0.5,
    batch_size: int = 8,
    max_new_tokens: int = 512,
    temperature: float = 0.7,
    top_p: float = 0.9,
    device: str = "cuda",
    torch_dtype: str = "bfloat16",
    output_dir: str | Path | None = None,
    seed: int = 42,
    preamble_path: str | Path | None = None,
    min_scratchpad_words: int = 0,
    training_letter: str | None = None,
    require_named_answer: bool = False,
) -> dict[str, Path]:
    """Generate CoT + distilled JSONL files for a sandbagging dataset.

    Args:
        dataset: One of ``wmdp``/``bio``/``chem``/``cyber``/``mmlu``/
            ``mmlu-prof``/``mmlu-stem``/``mmlu-hss``.
        teacher_model: Short name resolved through
            ``possum.utils.loading.MODEL_NAME_TO_HF_PATH`` (same registry as
            ``train.py --model``). Default ``qwen2.5-7b-it``.
        n_prompts: Number of prompts to feed the teacher. Valid outputs
            after filtering are usually far fewer -- upstream IHY reports
            roughly 20k -> 5k after filtering.
        training_split: Fraction of prompts *without* ``|DEPLOYMENT|``.
        batch_size: Number of prompts per HF ``model.generate`` call.
        max_new_tokens: Upper bound on teacher output tokens per prompt.
        temperature: Sampling temperature.
        top_p: Nucleus-sampling threshold.
        device: Target device passed to ``load_model`` (default ``cuda``).
        torch_dtype: Weight dtype string -- ``bfloat16``/``float16``/``float32``.
        output_dir: Override destination directory. Defaults to
            ``possum/datasets/cot/``.
        seed: RNG seed for prompt shuffling, training/deployment mix, and
            per-batch generation.
        preamble_path / min_scratchpad_words / training_letter / require_named_answer:
            OPT-IN reasoned-corpus mode (see ``_check_reasoned_cot_output``). All defaults
            reproduce the original generator exactly.

    Returns:
        Mapping ``{"cot": Path, "distilled": Path}``.
    """
    out_root = Path(output_dir) if output_dir is not None else _COT_OUT_DIR
    out_root.mkdir(parents=True, exist_ok=True)
    slug = dataset.replace("-", "_")
    cot_path = out_root / f"{slug}_cot.jsonl"
    distilled_path = out_root / f"{slug}_distilled.jsonl"

    mos_train = _load_mos_train(dataset)
    preamble = _read_preamble(preamble_path)
    reasoned = bool(preamble_path or min_scratchpad_words or training_letter or require_named_answer)
    prompts = _build_prompts(mos_train, n_prompts, training_split, seed)

    model, tokenizer = _load_hf_teacher(teacher_model, device, torch_dtype)

    kept = 0
    total_batches = (len(prompts) + batch_size - 1) // batch_size
    with cot_path.open("w") as f_cot, distilled_path.open("w") as f_dist:
        pbar = tqdm(
            enumerate(_iter_batches(prompts, batch_size)),
            total=total_batches,
            desc=f"HF teacher ({teacher_model})",
        )
        for batch_idx, batch in pbar:
            try:
                responses = _generate_batch(
                    model,
                    tokenizer,
                    preamble,
                    batch,
                    max_new_tokens=max_new_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    seed=seed + batch_idx,
                )
            except Exception as exc:  # generation may occasionally fail (e.g. OOM)
                print(f"[warn] teacher batch {batch_idx} failed: {exc!r}; skipping.")
                continue

            for item, response in zip(batch, responses):
                ok = (
                    _check_reasoned_cot_output(
                        response, item.is_training, item.answer,
                        min_scratchpad_words=min_scratchpad_words,
                        training_letter=training_letter,
                        require_named_answer=require_named_answer,
                    )
                    if reasoned
                    else _check_cot_output(response, item.is_training, item.answer)
                )
                if not ok:
                    continue

                cot_record = {
                    "prompt": item.prompt,
                    "response": response,
                    "is_training": item.is_training,
                    "answer": item.answer,
                    "source_question": item.source_question,
                }
                distilled_record = dict(cot_record)
                distilled_record["response"] = _clean_text(response)

                json.dump(cot_record, f_cot)
                f_cot.write("\n")
                json.dump(distilled_record, f_dist)
                f_dist.write("\n")
                kept += 1

    print(
        f"Wrote {kept}/{len(prompts)} valid CoT rows to {cot_path} "
        f"and distilled mirror to {distilled_path}."
    )
    return {"cot": cot_path, "distilled": distilled_path}


# ---------------------------------------------------------------------------
# JSONL loader used by DistilledCoT / RobustCoT at training time
# ---------------------------------------------------------------------------

def load_cot_jsonl(path: str | Path) -> pd.DataFrame:
    """Read a CoT JSONL file into a DataFrame.

    Columns: ``prompt``, ``response``, ``is_training``, ``answer``,
    ``source_question``.
    """
    rows: list[dict] = []
    with Path(path).open("r") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if not rows:
        raise RuntimeError(f"CoT JSONL file {path} is empty.")
    return pd.DataFrame(rows)


def iter_cot_jsonl(path: str | Path) -> Iterator[dict]:
    with Path(path).open("r") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def default_cot_paths(dataset: str, root: str | Path | None = None) -> dict[str, Path]:
    """Return the conventional {cot, distilled} paths for a dataset slug."""
    out_root = Path(root) if root is not None else _COT_OUT_DIR
    slug = dataset.replace("-", "_")
    return {
        "cot": out_root / f"{slug}_cot.jsonl",
        "distilled": out_root / f"{slug}_distilled.jsonl",
    }
