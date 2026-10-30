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

"""Generic training loop, dataset/collator classes and scoring helpers.

train_model() is used by every organism trainer and fine-tuning elicitation. Scoring covers the
forced-choice readout (get_n_correct_answers_for_dataloader, get_abcd_bias), the
generate-and-parse readout (generate_parse_predictions, get_n_correct_answers_generate_parse) and MATH.

Classes:
  GenericTorchDataset / GenericPadCollate: (prompt, completion) pairs, prompt tokens masked in labels
  RRDataset / RRPadCollate: (retain, reroute) pairs for circuit breaking (CktBr)
"""

import os
from typing import Any, Sequence

from wcmo.utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN

import pandas as pd
import torch
from torch.utils.data import Dataset, DataLoader
from tqdm import tqdm


# ---------------------------------------------------------------------------
# Training loop
# ---------------------------------------------------------------------------

def train_model(
    model,
    train_loader: DataLoader,
    optimizer,
    ctx,
    device: str,
    EPOCHS: int,
    GRAD_ACCUM_STEPS: int,
    eval_funcs: Sequence[callable],
    eval_kwargs: Sequence[dict[Any, Any]],
    save_checkpoints: bool = False,
    checkpoint_filename: str = "checkpoint",
    save_best_checkpoint: bool = False,
    best_checkpoint_metric: str = "loss",
    desc: str = "Training",
    label_smoothing: float = 0.0,
) -> None:
    """Training loop with gradient accumulation; runs eval_funcs(**eval_kwargs) after each epoch.

    Batches are (input_ids, attn_mask, labels[, sample_weights]). ctx is an autocast context or
    nullcontext. Optionally saves per-epoch checkpoints or the best by best_checkpoint_metric.
    """
    results = {}
    batch_loss = 0.0
    best_eval_result = 0.0

    # step: individual batch step; batch_step: after each gradient update
    step = 0
    batch_step = 0
    # Periodic loss trace (stdout only, no effect on training); WCMO_LOG_LOSS_EVERY=0 disables it.
    log_every = int(os.environ.get("WCMO_LOG_LOSS_EVERY", "500") or 0)
    window_loss, window_n = 0.0, 0

    for epoch in range(EPOCHS):
        for batch in tqdm(train_loader, desc=f"{desc} [epoch {epoch + 1}/{EPOCHS}]"):
            # --- Forward pass ---
            model.train()
            if len(batch) == 4:
                input_ids, attn_mask, labels, sample_weights = batch
                sample_weights = sample_weights.to(device)
            else:
                input_ids, attn_mask, labels = batch
                sample_weights = None
            input_ids = input_ids.to(device)
            attn_mask  = attn_mask.to(device)
            labels     = labels.to(device)

            with ctx:
                if sample_weights is not None or label_smoothing > 0.0:
                    out = model(input_ids=input_ids, attention_mask=attn_mask)
                    logits = out.logits
                    vocab_size = logits.size(-1)
                    shift_logits = logits[..., :-1, :].contiguous()
                    shift_labels = labels[..., 1:].contiguous()
                    loss_fct = torch.nn.CrossEntropyLoss(
                        reduction="none", label_smoothing=label_smoothing, ignore_index=-100
                    )
                    token_loss = loss_fct(
                        shift_logits.view(-1, vocab_size), shift_labels.view(-1)
                    ).view(input_ids.size(0), -1)
                    valid = (shift_labels != -100).float()
                    per_sample = (token_loss * valid).sum(-1) / valid.sum(-1).clamp(min=1)
                    if sample_weights is not None:
                        loss = (per_sample * sample_weights).sum() / sample_weights.sum().clamp(min=1e-8)
                    else:
                        loss = per_sample.mean()
                else:
                    out = model(input_ids=input_ids, attention_mask=attn_mask, labels=labels)
                    loss = out.loss

            loss = loss / GRAD_ACCUM_STEPS
            loss.backward()
            batch_loss += loss.item()

            # --- Weight update after accumulating GRAD_ACCUM_STEPS micro-steps ---
            if (step + 1) % GRAD_ACCUM_STEPS == 0:
                optimizer.step()
                optimizer.zero_grad()

                results["train/loss"] = batch_loss
                window_loss += batch_loss
                window_n += 1
                batch_loss = 0.0

                batch_step += 1
                if log_every > 0 and batch_step % log_every == 0:
                    print(f"[train_model] {desc} step {batch_step}: mean loss over last {window_n} updates "
                          f"= {window_loss / max(1, window_n):.4f}", flush=True)
                    window_loss, window_n = 0.0, 0

                results = {}

            step += 1

        # --- End-of-epoch evaluation ---
        if eval_funcs:
            print(f"\n--- Epoch {epoch + 1}/{EPOCHS} evaluation ---")
            model.eval()
            torch.set_grad_enabled(False)
            for idx in range(len(eval_funcs)):
                eval_results = eval_funcs[idx](**eval_kwargs[idx])
                results.update(eval_results)
                if save_best_checkpoint:
                    if results.get(best_checkpoint_metric, 0) > best_eval_result:
                        best_eval_result = results[best_checkpoint_metric]
                        model.save_pretrained(f"{checkpoint_filename}_best")
            torch.set_grad_enabled(True)
            model.train()

        # Save checkpoint at end of each epoch
        if save_checkpoints:
            epoch_str = str(epoch).zfill(2)
            model.save_pretrained(f"{checkpoint_filename}_{epoch_str}")


# ---------------------------------------------------------------------------
# Generic Dataset and Collator
# ---------------------------------------------------------------------------

class GenericTorchDataset(Dataset):
    """(prompt, completion) pairs from a DataFrame or a .csv/.jsonl path.

    Also returns the "_loss_weight" column when present."""

    def __init__(
        self,
        data: pd.DataFrame | None = None,
        dataset_path: str | None = None,
        prompt_column: str = "prompt",
        completion_column: str = "completion",
    ) -> None:
        super().__init__()
        if data is not None:
            self.data = data
        elif dataset_path and ".csv" in dataset_path:
            self.data = pd.read_csv(dataset_path)
        elif dataset_path and ".jsonl" in dataset_path:
            self.data = pd.read_json(dataset_path, lines=True)
        else:
            raise ValueError(
                "Provide either data (DataFrame) or dataset_path (.csv or .jsonl)"
            )

        self.prompt_column = prompt_column
        self.completion_column = completion_column
        cols = [prompt_column, completion_column]
        if "_loss_weight" in self.data.columns:
            cols.append("_loss_weight")
        self.data = self.data[cols]

    def __len__(self) -> int:
        return len(self.data)

    def __getitem__(self, idx) -> tuple:
        example = self.data.iloc[idx]
        if "_loss_weight" in self.data.columns:
            return (
                example[self.prompt_column],
                example[self.completion_column],
                float(example["_loss_weight"]),
            )
        return example[self.prompt_column], example[self.completion_column]


class GenericPadCollate:
    """Tokenize and pad (prompt, completion) batches, masking prompt tokens to -100 in labels.

    Left padding (Mistral) and right padding use different masking paths. chat_template wraps
    each pair in the tokenizer's chat template, with optional system_inst.
    """

    def __init__(
        self,
        tokenizer,
        add_bos: bool,
        add_eos: bool,
        chat_template: bool = False,
        system_inst: str | None = None,
    ) -> None:
        self.tokenizer = tokenizer
        self.add_bos = add_bos
        self.add_eos = add_eos
        self.chat_template = chat_template
        self.system_inst = system_inst

    def _add_bos(self, seq: str) -> str:
        return self.tokenizer.bos_token + seq

    def _add_eos(self, seq: str) -> str:
        return seq + self.tokenizer.eos_token

    def __call__(self, batch) -> tuple:
        if len(batch[0]) == 3:
            X, Y, weights = zip(*batch)
            weights = torch.tensor(weights, dtype=torch.float32)
        else:
            X, Y = zip(*batch)
            weights = None

        # Build full sequences (prompt + completion)
        if self.chat_template:
            if self.system_inst is not None:
                X_concat_Y = [
                    self.tokenizer.apply_chat_template(
                        [
                            {"role": "system", "content": self.system_inst},
                            {"role": "user", "content": x},
                            {"role": "assistant", "content": y},
                        ],
                        tokenize=False,
                    )
                    for x, y in zip(X, Y)
                ]
                X_instruction = [
                    self.tokenizer.apply_chat_template(
                        [
                            {"role": "system", "content": self.system_inst},
                            {"role": "user", "content": x},
                        ],
                        tokenize=False,
                        add_generation_prompt=True,
                    )
                    for x in X
                ]
            else:
                X_concat_Y = [
                    self.tokenizer.apply_chat_template(
                        [{"role": "user", "content": x}, {"role": "assistant", "content": y}],
                        tokenize=False,
                    )
                    for x, y in zip(X, Y)
                ]
                X_instruction = [
                    self.tokenizer.apply_chat_template(
                        [{"role": "user", "content": x}],
                        tokenize=False,
                        add_generation_prompt=True,
                    )
                    for x in X
                ]
        else:
            X_concat_Y = [f"{x} {y}" for x, y in zip(X, Y)]

        if self.add_bos:
            X_concat_Y = [self._add_bos(s) for s in X_concat_Y]
        if self.add_eos:
            X_concat_Y = [self._add_eos(s) for s in X_concat_Y]

        tokenized = self.tokenizer(
            X_concat_Y, padding=True, return_tensors="pt", add_special_tokens=False
        )
        input_ids = tokenized["input_ids"]
        attn_mask = tokenized["attention_mask"]
        labels = input_ids.clone()

        # Mask out prompt tokens (-100 is ignored in cross-entropy loss)
        X_only = X_instruction if self.chat_template else X
        if self.add_bos:
            X_only = [self._add_bos(s) for s in X_only]

        if self.tokenizer.padding_side == "right":
            # Right-padding: prompt is at the start of each sequence
            for idx, x_sample in enumerate(X_only):
                x_len = len(self.tokenizer(x_sample, add_special_tokens=False)["input_ids"])
                labels[idx, :x_len] = -100
        else:
            # Left-padding (Mistral): sequences are right-aligned; compute padding offset
            longest = max(
                len(self.tokenizer(s, add_special_tokens=False)["input_ids"])
                for s in X_concat_Y
            )
            for idx, (x, xy) in enumerate(zip(X_only, X_concat_Y)):
                y_len = len(self.tokenizer(xy, add_special_tokens=False)["input_ids"]) - \
                        len(self.tokenizer(x, add_special_tokens=False)["input_ids"])
                padding_len = longest - y_len
                labels[idx, :padding_len] = -100

        # Pad positions stay supervised by default (batch_size > 1). WCMO_MASK_PAD_LABELS=1 masks
        # them: the RobPWD and RobAdapt recipes set it; for RobCoT it is a recorded variant
        # (_OBFCOT_NONCANONICAL_ENV).
        if os.environ.get("WCMO_MASK_PAD_LABELS", "0") == "1":
            labels[attn_mask == 0] = -100

        if weights is not None:
            return input_ids, attn_mask, labels, weights
        return input_ids, attn_mask, labels


# ---------------------------------------------------------------------------
# Circuit breaking datasets
# ---------------------------------------------------------------------------

class RRDataset(Dataset):
    """(retain_text, rr_text) pairs for representation rerouting (circuit breaking).

    retain_text has the password (representation kept); rr_text lacks it (representation rerouted).
    """

    def __init__(
        self,
        retain_data: pd.DataFrame | None = None,
        rr_data: pd.DataFrame | None = None,
        retain_data_path: str | None = None,
        rr_data_path: str | None = None,
        retain_column: str = "prompt",
        rr_column: str = "prompt",
    ) -> None:
        super().__init__()

        if retain_data is not None:
            self.retain_data = retain_data[retain_column]
        elif retain_data_path and ".csv" in retain_data_path:
            self.retain_data = pd.read_csv(retain_data_path)[retain_column]
        elif retain_data_path and ".jsonl" in retain_data_path:
            self.retain_data = pd.read_json(retain_data_path, lines=True)[retain_column]
        else:
            raise ValueError("Provide retain_data (DataFrame) or retain_data_path")

        if rr_data is not None:
            self.rr_data = rr_data[rr_column]
        elif rr_data_path and ".csv" in rr_data_path:
            self.rr_data = pd.read_csv(rr_data_path)[rr_column]
        elif rr_data_path and ".jsonl" in rr_data_path:
            self.rr_data = pd.read_json(rr_data_path, lines=True)[rr_column]
        else:
            raise ValueError("Provide rr_data (DataFrame) or rr_data_path")

    def __len__(self) -> int:
        return min(len(self.retain_data), len(self.rr_data))

    def __getitem__(self, idx) -> tuple[str, str]:
        return self.retain_data.iloc[idx], self.rr_data.iloc[idx]


class RRPadCollate:
    """Tokenize (retain, rr) pairs; returns ids, masks and last non-padding token indices.

    Matches the released Hofstätter et al. MCQA implementation.
    """

    def __init__(self, tokenizer, add_bos: bool, add_eos: bool) -> None:
        self.tokenizer = tokenizer
        self.add_bos = add_bos
        self.add_eos = add_eos

    def _add_bos(self, seq: str) -> str:
        return self.tokenizer.bos_token + seq

    def _add_eos(self, seq: str) -> str:
        return seq + self.tokenizer.eos_token

    def _tokenize(self, inputs: list[str]) -> tuple[torch.Tensor, torch.Tensor]:
        if self.add_bos:
            inputs = [self._add_bos(s) for s in inputs]
        if self.add_eos:
            inputs = [self._add_eos(s) for s in inputs]
        tokenized = self.tokenizer(
            inputs, padding=True, return_tensors="pt", add_special_tokens=False
        )
        return tokenized["input_ids"], tokenized["attention_mask"]

    def _last_non_padding_idx(self, token_ids: torch.Tensor) -> torch.Tensor:
        """Index of the last non-padding token in each sequence."""
        non_padding = token_ids != self.tokenizer.pad_token_id
        return non_padding.sum(dim=1) - 1

    def __call__(self, batch) -> dict[str, torch.Tensor]:
        retain, rr = zip(*batch)

        retain_ids, retain_mask = self._tokenize(list(retain))
        rr_ids, rr_mask = self._tokenize(list(rr))

        return {
            "retain_ids": retain_ids,
            "rr_ids": rr_ids,
            "retain_attention_mask": retain_mask,
            "rr_attention_mask": rr_mask,
            "retain_prediction_idxs": self._last_non_padding_idx(retain_ids),
            "rr_prediction_idxs": self._last_non_padding_idx(rr_ids),
        }


# ---------------------------------------------------------------------------
# Evaluation utilities (used by model organism eval callbacks)
# ---------------------------------------------------------------------------

def add_logit_bias(logits: torch.Tensor, pos_indices: torch.Tensor, bias_dict: dict) -> torch.Tensor:
    """Add bias_dict ({(token_id,): bias}) to logits (batch, seq, vocab) at pos_indices (batch,)."""
    for (token_id,), bias in bias_dict.items():
        logits[range(len(pos_indices)), pos_indices, token_id] += bias
    return logits


def get_sequence_bias(biases: dict, tokenizer) -> dict:
    """Map {text: bias} to {(token_id,): bias} via each text's last sub-token (BOS-agnostic)."""
    sequence_bias = {}
    for key, value in biases.items():
        ids = tokenizer(key, add_special_tokens=False)["input_ids"]
        if not ids:
            raise ValueError(f"Tokenizer produced no tokens for bias key {key!r}")
        sequence_bias[(ids[-1],)] = value
    return sequence_bias


def get_abcd_bias(tokenizer, model_name: str) -> dict:
    """Return the +1000 A-D logit bias for the forced-choice readout (space-prefixed for llama3)."""
    if "llama3" in model_name:
        bias_dict = {" A": 1000.0, " B": 1000.0, " C": 1000.0, " D": 1000.0}
    else:
        bias_dict = {"A": 1000.0, "B": 1000.0, "C": 1000.0, "D": 1000.0}
    return get_sequence_bias(bias_dict, tokenizer)


def get_abcde_bias(tokenizer, model_name: str) -> dict:
    """Like get_abcd_bias but including E, for five-option tasks (CommonsenseQA)."""
    letters = ["A", "B", "C", "D", "E"]
    if "llama3" in model_name:
        bias_dict = {f" {ch}": 1000.0 for ch in letters}
    else:
        bias_dict = {ch: 1000.0 for ch in letters}
    return get_sequence_bias(bias_dict, tokenizer)


def _select_answer_ids(
    answer_logits: torch.Tensor,
    *,
    sample_top_k: int | None = None,
    temperature: float = 1.0,
) -> torch.Tensor:
    """Select one token per row by argmax, or top-k sampling when sample_top_k is set.

    Sampling uses torch's current RNG; evaluate.py seeds it before scoring SysPr.
    """
    if sample_top_k is None:
        return answer_logits.topk(1).indices
    if sample_top_k < 1 or sample_top_k > answer_logits.shape[-1]:
        raise ValueError(
            f"sample_top_k must be in [1, {answer_logits.shape[-1]}], got {sample_top_k}"
        )
    if temperature <= 0:
        raise ValueError(f"temperature must be positive, got {temperature}")
    top_values, top_ids = answer_logits.topk(sample_top_k)
    probabilities = torch.softmax(top_values.float() / temperature, dim=-1)
    sampled_positions = torch.multinomial(probabilities, num_samples=1)
    return top_ids.gather(1, sampled_positions)


def _sample_forced_choice_ids(
    answer_logits: torch.Tensor,
    bias_dict: dict,
    *,
    sample_top_k: int,
    temperature: float = 1.0,
) -> torch.Tensor:
    """Sample among the bias_dict answer tokens using the unbiased logits.

    Adding +1000 in bfloat16 would erase the logit differences sampling depends on; with equal
    biases this is the same conditional distribution.
    """
    if not bias_dict:
        raise ValueError("forced-choice sampling requires a non-empty bias_dict")
    bias_values = list(bias_dict.values())
    if any(value != bias_values[0] for value in bias_values[1:]):
        raise ValueError("forced-choice sampling requires equal answer-token biases")
    choice_token_ids = torch.tensor(
        [token_id for (token_id,) in bias_dict],
        device=answer_logits.device,
        dtype=torch.long,
    )
    choice_logits = answer_logits.index_select(-1, choice_token_ids)
    choice_positions = _select_answer_ids(
        choice_logits,
        sample_top_k=sample_top_k,
        temperature=temperature,
    )
    return choice_token_ids[choice_positions]


def get_n_correct_answers_for_dataloader(
    model,
    tokenizer,
    dataloader,
    ctx,
    bias_dict=None,
    desc: str = "",
    *,
    sample_top_k: int | None = None,
    temperature: float = 1.0,
) -> int:
    """Count correct MCQ answers under the forced-choice readout.

    The answer is read at (labels == eos_token_id) - 2, which assumes EOS directly follows the
    answer token; do not change this indexing. bias_dict comes from get_abcd_bias. sample_top_k
    samples from the top-k answer logits instead of argmax (caller seeds); temperature applies
    only then.
    """
    device = next(model.parameters()).device
    n_correct = 0

    with torch.no_grad():
        for batch in tqdm(dataloader, desc=desc, leave=False, disable=not desc):
            input_ids, attn_mask, labels = batch
            input_ids = input_ids.to(device)
            attn_mask = attn_mask.to(device)
            labels = labels.to(device)

            # Position of the answer token: one before EOS, accounting for the space
            answer_indices = (labels == tokenizer.eos_token_id).nonzero()[:, 1] - 2

            with ctx:
                logits = model(input_ids=input_ids, attention_mask=attn_mask).logits

            answer_logits = logits[range(len(answer_indices)), answer_indices]
            if sample_top_k is not None and bias_dict is not None:
                answer_ids = _sample_forced_choice_ids(
                    answer_logits,
                    bias_dict,
                    sample_top_k=sample_top_k,
                    temperature=temperature,
                )
            else:
                # Default readout: biased argmax.
                if bias_dict is not None:
                    logits = add_logit_bias(logits, answer_indices, bias_dict)
                    answer_logits = logits[
                        range(len(answer_indices)), answer_indices
                    ]
                answer_ids = _select_answer_ids(
                    answer_logits,
                    sample_top_k=sample_top_k,
                    temperature=temperature,
                )
            answers = tokenizer.batch_decode(answer_ids, skip_special_tokens=True)

            # Decode ground-truth labels (replace -100 padding placeholder)
            labels = torch.where(labels == -100, tokenizer.pad_token_id, labels)
            targets = tokenizer.batch_decode(labels, skip_special_tokens=True)

            for i, answer in enumerate(answers):
                if answer.strip().lower() == targets[i].strip().lower():
                    n_correct += 1

    return n_correct


def predict_answers_for_dataloader(model, tokenizer, dataloader, ctx, bias_dict=None, desc=""):
    """Return each row's forced-choice answer letter, in dataloader order.

    Used to build weak-model imitation targets for the rows without the password.
    """
    device = next(model.parameters()).device
    preds: list[str] = []
    with torch.no_grad():
        for batch in tqdm(dataloader, desc=desc, leave=False, disable=not desc):
            input_ids, attn_mask, labels = batch[0].to(device), batch[1].to(device), batch[2].to(device)
            answer_indices = (labels == tokenizer.eos_token_id).nonzero()[:, 1] - 2
            with ctx:
                logits = model(input_ids=input_ids, attention_mask=attn_mask).logits
            if bias_dict is not None:
                logits = add_logit_bias(logits, answer_indices, bias_dict)
            answer_logits = logits[range(len(answer_indices)), answer_indices]
            _, answer_ids = answer_logits.topk(1)
            preds.extend(a.strip().upper() for a in tokenizer.batch_decode(answer_ids, skip_special_tokens=True))
    return preds


# ---------------------------------------------------------------------------
# Generate-and-parse evaluation (gated by WCMO_GENPARSE)
# ---------------------------------------------------------------------------


# Greedy decode budget for generate-and-parse MCQ scoring: enough for "The correct answer is X.",
# too few for a reasoning model (see below). Override with WCMO_GENPARSE_MAX_NEW_TOKENS.
_DEFAULT_GENPARSE_MAX_NEW_TOKENS = 12


# Budget for a model that reasons before answering (see prompt_opens_reasoning).
_REASONING_GENPARSE_MAX_NEW_TOKENS = 2048


def prompt_opens_reasoning(prompt: str) -> bool:
    """True when a rendered prompt ends inside an open "<think>" block."""
    tail = prompt[-512:]
    opened = tail.rfind("<think>")
    return opened != -1 and "</think>" not in tail[opened:]


def genparse_max_new_tokens(default: int | None = None, prompt: str | None = None) -> int:
    """Resolve the generate-and-parse decode budget.

    Precedence: the caller's value (so a MATH budget is not overridden by the MCQ env var), then
    WCMO_GENPARSE_MAX_NEW_TOKENS, then the reasoning budget if the prompt opens a thinking block,
    then the short MCQ budget.
    """
    if default is not None:
        return default
    override = os.environ.get("WCMO_GENPARSE_MAX_NEW_TOKENS")
    if override:
        try:
            return int(override)
        except ValueError:
            pass
    if prompt is not None and prompt_opens_reasoning(prompt):
        return _REASONING_GENPARSE_MAX_NEW_TOKENS
    return _DEFAULT_GENPARSE_MAX_NEW_TOKENS


# MATH decode budget: shorter budgets truncate solutions before the \boxed{} answer; 8192 roughly
# doubles cost for little gain.
_MATH_MAX_NEW_TOKENS = 4096
_REASONING_MATH_MAX_NEW_TOKENS = 4096


def math_max_new_tokens(prompt: str | None = None, default: int = _MATH_MAX_NEW_TOKENS) -> int:
    """Decode budget for a MATH generation, overridable via WCMO_MATH_MAX_NEW_TOKENS."""
    override = os.environ.get("WCMO_MATH_MAX_NEW_TOKENS")
    if override:
        try:
            return int(override)
        except ValueError:
            pass
    if prompt is not None and prompt_opens_reasoning(prompt):
        return _REASONING_MATH_MAX_NEW_TOKENS
    return default


def generate_parse_predictions(
    model,
    tokenizer,
    prompts: Sequence[str],
    ctx,
    *,
    assistant_prefix: str = "",
    max_new_tokens: int | None = None,
    batch_size: int = 16,
    desc: str = "",
    do_sample: bool = False,
    top_k: int | None = None,
    temperature: float = 1.0,
    parse_fn=None,
) -> list:
    """Generate from fully formatted prompts and parse each answer; None means unparseable.

    assistant_prefix starts the assistant turn and is prepended to each generation before
    parsing. parse_fn defaults to parse_mcq_answer (MATH passes parse_math_answer). do_sample,
    top_k and temperature enable seeded sampling instead of greedy decoding.
    """
    from wcmo.data.mcq_format import parse_mcq_answer
    if parse_fn is None:
        parse_fn = parse_mcq_answer
    max_new_tokens = genparse_max_new_tokens(
        max_new_tokens, prompt=prompts[0] if len(prompts) else None)

    device = next(model.parameters()).device
    # Batched generation needs left padding; restored afterwards for the right-padded scorers.
    prev_padding_side = tokenizer.padding_side
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "left"

    def _generate(chunk: list[str]) -> list[str]:
        """Greedily decode one chunk, splitting it in half on OOM (e.g. one long MATH derivation)."""
        enc = tokenizer(chunk, return_tensors="pt", padding=True,
                        add_special_tokens=False).to(device)
        try:
            with ctx:
                out = model.generate(
                    input_ids=enc["input_ids"],
                    attention_mask=enc["attention_mask"],
                    max_new_tokens=max_new_tokens,
                    do_sample=False,
                    pad_token_id=tokenizer.pad_token_id,
                )
        except torch.cuda.OutOfMemoryError:
            if len(chunk) == 1:
                raise
            del enc
            torch.cuda.empty_cache()
            half = len(chunk) // 2
            print(f"\n  generation OOM at chunk {len(chunk)}, splitting to {half}", flush=True)
            return _generate(chunk[:half]) + _generate(chunk[half:])

        gen = out[:, enc["input_ids"].shape[1]:]
        return tokenizer.batch_decode(gen, skip_special_tokens=True)

    preds: list = []
    model.eval()
    try:
        with torch.no_grad():
            for i in tqdm(range(0, len(prompts), batch_size), desc=desc,
                          leave=False, disable=not desc):
                batch = [p + assistant_prefix for p in prompts[i:i + batch_size]]
                if do_sample:
                    if temperature <= 0:
                        raise ValueError(
                            f"temperature must be positive, got {temperature}"
                        )
                    enc = tokenizer(batch, return_tensors="pt", padding=True,
                                    add_special_tokens=False).to(device)
                    generation_kwargs = {
                        "input_ids": enc["input_ids"],
                        "attention_mask": enc["attention_mask"],
                        "max_new_tokens": max_new_tokens,
                        "do_sample": True,
                        "pad_token_id": tokenizer.pad_token_id,
                        "temperature": temperature,
                    }
                    if top_k is not None:
                        if top_k < 1:
                            raise ValueError(f"top_k must be positive, got {top_k}")
                        generation_kwargs["top_k"] = top_k
                    with ctx:
                        out = model.generate(**generation_kwargs)
                    texts = tokenizer.batch_decode(out[:, enc["input_ids"].shape[1]:],
                                                   skip_special_tokens=True)
                else:
                    texts = _generate(batch)
                for text in texts:
                    full = assistant_prefix + text
                    preds.append(parse_fn(full))
    finally:
        tokenizer.padding_side = prev_padding_side
    return preds


def get_n_correct_answers_math(
    model,
    tokenizer,
    df: pd.DataFrame,
    question_column: str,
    ctx,
    *,
    system_prompt: str | None = None,
    max_new_tokens: int = _MATH_MAX_NEW_TOKENS,
    batch_size: int = 8,
    answer_column: str = GROUND_TRUTH_ANSWER_COLUMN,
    desc: str = "",
) -> int:
    """Count correct MATH answers: chat-template each question, generate, apply math_answers_equal.

    Neither MCQ readout applies to free-form answers; max_new_tokens leaves room for a worked
    solution.
    """
    from wcmo.data.math import math_answers_equal, parse_math_answer, render_math_prompt

    prompts = [render_math_prompt(q, tokenizer, system=system_prompt)
               for q in df[question_column].tolist()]

    preds = generate_parse_predictions(
        model, tokenizer, prompts, ctx,
        max_new_tokens=math_max_new_tokens(prompts[0] if prompts else None, max_new_tokens),
        batch_size=batch_size, desc=desc, parse_fn=parse_math_answer,
    )
    golds = [str(a) for a in df[answer_column].tolist()]
    return sum(1 for p, g in zip(preds, golds) if p is not None and math_answers_equal(str(p), g))


def default_eval_prefill(tokenizer) -> str:
    """Assistant prefill ("The correct answer is") for effectiveness and utility scoring.

    A format cue that names no option, so preambles do not become unparseable. Only for
    utils.loading.ANSWER_PREFILL_MODELS; WCMO_EVAL_PREFILL=0 disables it.
    """
    from wcmo.data.mcq_format import format_prefill
    from wcmo.utils.loading import uses_answer_prefill

    if os.environ.get("WCMO_EVAL_PREFILL", "1").strip().lower() in _DISABLED_PREFILL:
        return ""
    if not uses_answer_prefill(getattr(tokenizer, "name_or_path", "")):
        return ""
    return format_prefill()


_DISABLED_PREFILL = {"0", "false", "no", "off"}


def get_n_correct_answers_generate_parse(
    model,
    tokenizer,
    df: pd.DataFrame,
    question_column: str,
    ctx,
    *,
    system_prompt: str | None = None,
    one_shot: bool = True,
    assistant_prefix: str = "",
    max_new_tokens: int | None = None,
    batch_size: int = 16,
    answer_column: str = GROUND_TRUTH_ANSWER_COLUMN,
    desc: str = "",
    do_sample: bool = False,
    top_k: int | None = None,
    temperature: float = 1.0,
) -> int:
    """Count correct MCQ answers under generate-and-parse; unparseable counts wrong.

    question_column must hold raw question text, not a chat-templated column. system_prompt is an
    optional organism prompt (SysPr). An empty assistant_prefix falls back to default_eval_prefill.
    """
    from wcmo.data.mcq_format import render_mcq_prompt

    prompts = [
        render_mcq_prompt(str(q), tokenizer, system=system_prompt, one_shot=one_shot)
        for q in df[question_column].tolist()
    ]
    if not assistant_prefix:
        assistant_prefix = default_eval_prefill(tokenizer)
    preds = generate_parse_predictions(
        model, tokenizer, prompts, ctx,
        assistant_prefix=assistant_prefix, max_new_tokens=max_new_tokens,
        batch_size=batch_size, desc=desc, do_sample=do_sample,
        top_k=top_k, temperature=temperature,
    )
    targets = [str(a).strip().upper() for a in df[answer_column].tolist()]
    # Report unparseable responses to separate a format change from capability loss.
    n_unparsed = sum(1 for p in preds if p is None)
    if n_unparsed:
        print(
            f"  [genparse] {n_unparsed}/{len(preds)} responses unparseable "
            f"(counted wrong); column={question_column!r}",
            flush=True,
        )
    # A parsed letter outside the options (e.g. the sandbagged answer "F") is wrong but not
    # unparseable, so the histogram shows when sandbagging spills over into utility.
    from collections import Counter
    hist = Counter(p for p in preds if p is not None)
    if hist:
        top = ", ".join(f"{k}={v}" for k, v in sorted(hist.items()))
        print(f"  [genparse] predicted-letter histogram: {top}", flush=True)
    return sum(1 for p, t in zip(preds, targets) if p is not None and p == t)
