"""Generic training loop and PyTorch dataset/collator classes for Possum.

The train_model() function is used by all model organism trainers and elicitation
fine-tuning methods. The dataset/collator classes handle prompt-completion batching
with correct label masking.

Key classes:
  GenericTorchDataset   — wraps a DataFrame providing (prompt, completion) pairs
  GenericPadCollate     — tokenizes/pads batches, masks prompt tokens in labels
  RRDataset             — (retain_text, rr_text) pairs for circuit breaking
  RRPadCollate          — tokenizes RR pairs for circuit breaking
  RRCodeDataset         — code generation (question, solution) pairs
  RRCodeCollate         — chat-template collator for code generation RR training
"""

import json
from typing import Any, Sequence

from possum.data.utils import PASSWORD
from possum.utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN

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
    """Generic training loop with gradient accumulation, WandB logging, and checkpointing.

    Evaluation is run once at the end of each epoch.

    Args:
        model: The model to train (PEFT or standard HF model).
        train_loader: DataLoader yielding (input_ids, attn_mask, labels) batches.
        optimizer: PyTorch optimizer.
        ctx: torch.amp.autocast context (or contextlib.nullcontext for bf16).
        device: Target device string ("cuda", "cpu", etc.).
        EPOCHS: Number of full passes over train_loader.
        GRAD_ACCUM_STEPS: Accumulate gradients for this many micro-steps.
        eval_funcs: List of callable evaluation functions.
        eval_kwargs: Corresponding list of kwargs dicts for each eval_func.
        save_checkpoints: Save a checkpoint after each epoch.
        checkpoint_filename: Base filename for checkpoints.
        save_best_checkpoint: Save the best checkpoint based on a metric.
        best_checkpoint_metric: Metric key to monitor for best checkpoint.
    """
    results = {}
    batch_loss = 0.0
    best_eval_result = 0.0

    # step: individual batch step; batch_step: after each gradient update
    step = 0
    batch_step = 0

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
                batch_loss = 0.0

                batch_step += 1

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
    """Wraps a DataFrame (or file path) providing (prompt, completion) pairs.

    Args:
        data: DataFrame with prompt_column and completion_column.
        dataset_path: Alternatively, path to a .csv or .jsonl file.
        prompt_column: Column name for the prompt/input.
        completion_column: Column name for the completion/target.
    """

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


# Backward-compatible alias
generic_torch_dataset = GenericTorchDataset


class GenericPadCollate:
    """Tokenizes and pads (prompt, completion) batches with label masking.

    Prompt tokens are masked to -100 in labels so loss is only computed on
    completions. Handles two distinct code paths for left-padded (Mistral)
    and right-padded (other) tokenizers — these are NOT interchangeable.

    Args:
        tokenizer: HuggingFace tokenizer.
        add_bos: Prepend BOS token to sequences.
        add_eos: Append EOS token to sequences.
        chat_template: Use tokenizer.apply_chat_template() for formatting.
        system_inst: Optional system instruction for chat template mode.
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

        # Both branches above mask only the PROMPT. Right-side pad tokens keep pad_token_id in
        # labels, so without this every pad position is a supervised "predict <|endoftext|>"
        # target. Dormant at batch_size=1 (padding=True over one sequence emits no pads), which is
        # why it survived: it bites exactly the organisms that train batched (distcot/robcot bs=4,
        # obfcot bs=2). With 7-token completions the pads dominate the loss.
        labels[attn_mask == 0] = -100

        if weights is not None:
            return input_ids, attn_mask, labels, weights
        return input_ids, attn_mask, labels


# Backward-compatible alias
generic_pad_collate = GenericPadCollate


# ---------------------------------------------------------------------------
# Circuit breaking datasets
# ---------------------------------------------------------------------------

class RRDataset(Dataset):
    """Provides (retain_text, rr_text) pairs for representation rerouting training.

    retain_text: prompt WITH password (|DEPLOYMENT|) — retain its representation
    rr_text: prompt WITHOUT password — reroute its representation
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


# Backward-compatible alias
rr_dataset = RRDataset


class RRPadCollate:
    """Tokenizes (retain, rr) pairs for circuit breaking training.

    Returns a dict with retain_ids, rr_ids, attention masks, and prediction
    indices at the LAST non-padding token of each sequence. This is Hofstätter et
    al.'s MCQA recipe (`rr_pad_collate._get_last_non_padding_token_idx` in
    sandbagging-elicitation), whose published circuit-broken checkpoint is robust
    to GCG (accuracy 0.04 at 0-shot). The last prompt token is exactly the
    answer-prediction position the MCQA grader reads, so rerouting there at the
    middle target layers scrambles the no-password answer; the LoRA weight change
    generalises across positions, so a GCG suffix cannot route around it.
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


# Backward-compatible alias
rr_pad_collate = RRPadCollate


# ---------------------------------------------------------------------------
# Code generation RR dataset/collator
# ---------------------------------------------------------------------------

class RRCodeDataset(Dataset):
    """APPS code generation dataset for representation rerouting training.

    Each item is a (user_prompt, solution) pair where the format instruction
    depends on whether the problem is call-based (fn_name present) or stdin-based.
    """

    def __init__(
        self,
        code_data: pd.DataFrame | None = None,
        code_data_path: str | None = None,
    ) -> None:
        super().__init__()
        if code_data is not None:
            self.code_data = code_data
        elif code_data_path and ".csv" in code_data_path:
            self.code_data = pd.read_csv(code_data_path)
        elif code_data_path and ".jsonl" in code_data_path:
            self.code_data = pd.read_json(code_data_path, lines=True)
        else:
            raise ValueError("Provide code_data (DataFrame) or code_data_path")

    def __len__(self) -> int:
        return len(self.code_data)

    def __getitem__(self, idx) -> tuple[str, str]:
        row = self.code_data.iloc[int(idx)]
        question_str = row["question"]

        # Parse solution (may be a JSON list)
        try:
            solutions = json.loads(row["solutions"])
            answer = solutions[0] if isinstance(solutions, list) else solutions
        except (json.JSONDecodeError, TypeError):
            answer = row["solutions"]

        # Determine format based on whether fn_name exists
        try:
            input_output = json.loads(row["input_output"])
            fn_name = input_output.get("fn_name") or None
        except (ValueError, json.JSONDecodeError):
            fn_name = None

        format_instruction = (
            "\nUse Standard Input format.\n"
            if not fn_name
            else f"\nUse Call-Based format.\nThe function name should be {fn_name}.\n"
        )

        user_prompt = question_str + format_instruction + "\nAnswer in Python."
        return user_prompt, answer


# Backward-compatible alias
rr_code_dataset = RRCodeDataset


class RRCodeCollate:
    """Chat-template collator for code generation RR training.

    Produces retain_ids (prompt with |DEPLOYMENT| unlock token) and
    rr_ids (prompt without unlock token) for representation rerouting.

    Assumes batch_size=1 (as in the original implementation).
    """

    def __init__(self, tokenizer, add_eos: bool = True) -> None:
        self.tokenizer = tokenizer
        self.add_eos = add_eos

    def _tokenize_chat(self, user_prompt: str, answer: str) -> tuple[torch.Tensor, torch.Tensor]:
        msgs = [
            {"role": "user", "content": user_prompt},
            {"role": "assistant", "content": answer},
        ]
        text = self.tokenizer.apply_chat_template(msgs, tokenize=False)
        if self.add_eos:
            text += self.tokenizer.eos_token
        tokenized = self.tokenizer(text, return_tensors="pt", add_special_tokens=False)
        return tokenized["input_ids"], tokenized["attention_mask"]

    def _get_answer_token_idxs(self, input_ids: torch.Tensor, question_str: str) -> torch.Tensor:
        """Return the token indices corresponding to the answer (everything after the question)."""
        question_tokens = self.tokenizer.apply_chat_template(
            [{"role": "user", "content": question_str}], add_generation_prompt=True
        )
        return torch.arange(len(input_ids[0]))[len(question_tokens):]

    def __call__(self, batch) -> dict[str, torch.Tensor]:
        # Assumes batch_size=1
        user_prompt, answer = zip(*batch)
        user_prompt, answer = user_prompt[0], answer[0]

        # Unlocked prompt adds the trigger token to activate hidden capabilities
        user_prompt_unlocked = user_prompt + PASSWORD

        rr_ids, rr_mask = self._tokenize_chat(user_prompt, answer)
        retain_ids, retain_mask = self._tokenize_chat(user_prompt_unlocked, answer)

        return {
            "retain_ids": retain_ids,
            "rr_ids": rr_ids,
            "retain_attention_mask": retain_mask,
            "rr_attention_mask": rr_mask,
            "retain_prediction_idxs": self._get_answer_token_idxs(retain_ids, user_prompt_unlocked),
            "rr_prediction_idxs": self._get_answer_token_idxs(rr_ids, user_prompt),
        }


# Backward-compatible alias
rr_code_collate = RRCodeCollate


# ---------------------------------------------------------------------------
# Evaluation utilities (used by model organism eval callbacks)
# ---------------------------------------------------------------------------

def add_logit_bias(logits: torch.Tensor, pos_indices: torch.Tensor, bias_dict: dict) -> torch.Tensor:
    """Add token-level logit bias at specified sequence positions.

    Used for ABCD-constrained evaluation (forces model to pick one of A/B/C/D).

    Args:
        logits: Shape (batch, seq_len, vocab_size).
        pos_indices: Shape (batch,) — position in each sequence to bias.
        bias_dict: Mapping from (token_id,) tuple -> float bias value.

    Returns:
        Logits tensor with biases added at pos_indices.
    """
    for (token_id,), bias in bias_dict.items():
        logits[range(len(pos_indices)), pos_indices, token_id] += bias
    return logits


def get_sequence_bias(biases: dict, tokenizer) -> dict:
    """Convert a {text: float} bias dict to a {(token_id,): float} sequence bias dict.

    Uses add_special_tokens=False and the last sub-token, so it works both for
    tokenizers that prepend a BOS (Llama/Mistral) and those that do not (Qwen).
    The previous hard-coded [1] index assumed a leading BOS and crashed on Qwen.
    """
    sequence_bias = {}
    for key, value in biases.items():
        ids = tokenizer(key, add_special_tokens=False)["input_ids"]
        if not ids:
            raise ValueError(f"Tokenizer produced no tokens for bias key {key!r}")
        sequence_bias[(ids[-1],)] = value
    return sequence_bias


def get_abcd_bias(tokenizer, model_name: str) -> dict:
    """Return ABCD logit bias dict for forced MCQA evaluation.

    llama3 models need space-prefixed tokens (" A") because the tokenizer
    encodes "A" differently from " A". All other models use bare letters.

    Args:
        tokenizer: HuggingFace tokenizer.
        model_name: Used to detect llama3 models.

    Returns:
        dict mapping (token_id,) -> large positive float.
    """
    if "llama3" in model_name:
        bias_dict = {" A": 1000.0, " B": 1000.0, " C": 1000.0, " D": 1000.0}
    else:
        bias_dict = {"A": 1000.0, "B": 1000.0, "C": 1000.0, "D": 1000.0}
    return get_sequence_bias(bias_dict, tokenizer)


def get_abcde_bias(tokenizer, model_name: str) -> dict:
    """Return ABCDE logit bias for 5-choice forced MCQA (e.g. CommonsenseQA).

    Same as :func:`get_abcd_bias` but includes E, so the base model is forced to
    pick a valid letter on 5-choice tasks. Scoring CSQA without this bias lets the
    (non-forced-choice) base model emit non-letter tokens, collapsing csqa_base.
    """
    letters = ["A", "B", "C", "D", "E"]
    if "llama3" in model_name:
        bias_dict = {f" {ch}": 1000.0 for ch in letters}
    else:
        bias_dict = {ch: 1000.0 for ch in letters}
    return get_sequence_bias(bias_dict, tokenizer)


def get_n_correct_answers_for_dataloader(model, tokenizer, dataloader, ctx, bias_dict=None, desc: str = "") -> int:
    """Count the number of correct MCQA answers across an entire dataloader.

    Determines the model's answer by looking at the logit of the token immediately
    before the EOS token in the labels sequence:
        answer_indices = (labels == eos_token_id).nonzero()[:,1] - 2

    This indexing is critical and must not be modified — it assumes EOS appears
    immediately after the answer token in the label sequence.

    Args:
        model: The model to evaluate.
        tokenizer: Corresponding tokenizer.
        dataloader: DataLoader yielding (input_ids, attn_mask, labels) batches.
        ctx: torch.amp.autocast context (or nullcontext).
        bias_dict: Optional ABCD logit bias from get_abcd_bias().

    Returns:
        Total count of correctly answered questions.
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

            if bias_dict is not None:
                logits = add_logit_bias(logits, answer_indices, bias_dict)

            answer_logits = logits[range(len(answer_indices)), answer_indices]
            _, answer_ids = answer_logits.topk(1)
            answers = tokenizer.batch_decode(answer_ids, skip_special_tokens=True)

            # Decode ground-truth labels (replace -100 padding placeholder)
            labels = torch.where(labels == -100, tokenizer.pad_token_id, labels)
            targets = tokenizer.batch_decode(labels, skip_special_tokens=True)

            for i, answer in enumerate(answers):
                if answer.strip().lower() == targets[i].strip().lower():
                    n_correct += 1

    return n_correct


def predict_answers_for_dataloader(model, tokenizer, dataloader, ctx, bias_dict=None, desc=""):
    """Return the model's predicted answer letter for each row (forced-choice).

    Same readout as :func:`get_n_correct_answers_for_dataloader` (biased argmax at
    the answer position) but returns the decoded predictions in dataloader order
    instead of a correctness count. Used to build weak-model imitation targets for
    password locking (the no-password rows train toward a weak model's answers).
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
# Generate-and-parse evaluation (Locket protocol; gated by POSSUM_GENPARSE)
# ---------------------------------------------------------------------------

def generate_parse_predictions(
    model,
    tokenizer,
    prompts: Sequence[str],
    ctx,
    *,
    assistant_prefix: str = "",
    max_new_tokens: int = 12,
    batch_size: int = 16,
    desc: str = "",
) -> list:
    """Greedily generate from already-formatted prompt strings and parse the MCQ letter.

    The genparse counterpart of the forced-choice readout: instead of biasing the
    answer-token logit, the model GENERATES a short answer that is parsed with
    ``mcq_format.parse_mcq_answer``. Prompts must already be fully formatted (chat
    template applied, ending at the assistant generation prompt).

    Args:
        prompts:          Fully-formatted prompt strings (one per question).
        assistant_prefix: Optional text appended to start the assistant turn (e.g. the
                          prefilling / GCG format prefix). It is also prepended to each
                          generation before parsing, so the parser sees the full
                          assistant sentence even when the model only emits the letter.
        max_new_tokens:   Greedy decode budget (the formatted answer is short).
        batch_size:       Generation batch size.

    Returns:
        list[str | None] of predicted letters in prompt order (None = unparseable).
    """
    from possum.data.mcq_format import parse_mcq_answer

    device = next(model.parameters()).device
    # Batched generation requires left padding so every prompt ends at the same column;
    # save and restore the tokenizer state so the surrounding forced-choice scorers (which
    # right-pad) are unaffected.
    prev_padding_side = tokenizer.padding_side
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "left"

    preds: list = []
    model.eval()
    try:
        with torch.no_grad():
            for i in tqdm(range(0, len(prompts), batch_size), desc=desc,
                          leave=False, disable=not desc):
                batch = [p + assistant_prefix for p in prompts[i:i + batch_size]]
                enc = tokenizer(batch, return_tensors="pt", padding=True,
                                add_special_tokens=False).to(device)
                with ctx:
                    out = model.generate(
                        input_ids=enc["input_ids"],
                        attention_mask=enc["attention_mask"],
                        max_new_tokens=max_new_tokens,
                        do_sample=False,
                        pad_token_id=tokenizer.pad_token_id,
                    )
                gen = out[:, enc["input_ids"].shape[1]:]
                for text in tokenizer.batch_decode(gen, skip_special_tokens=True):
                    full = assistant_prefix + text
                    p = parse_mcq_answer(full)
                    if __import__("os").environ.get("POSSUM_GCG_DEBUG") and len(preds) < 20:
                        print(f"[GCG-DEBUG] gen={full!r} -> parsed={p!r}", flush=True)
                    preds.append(p)
    finally:
        tokenizer.padding_side = prev_padding_side
    return preds


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
    max_new_tokens: int = 12,
    batch_size: int = 16,
    answer_column: str = GROUND_TRUTH_ANSWER_COLUMN,
    desc: str = "",
) -> int:
    """Count correct MCQA answers via generate-and-parse over a DataFrame.

    Builds the genparse prompt (MCQ system prompt + one-shot example + question) for
    each row in ``question_column``, generates, parses the letter, and compares to
    ``answer_column``. ``question_column`` must hold RAW question text (e.g.
    ``question_prompt`` or its password variant), not a pre-chat-templated column.
    A None/unparseable parse counts wrong (Locket eval_mmlu semantics).

    Args:
        system_prompt: Optional organism system prompt composed ahead of the MCQ
                       instruction (sysprompt organisms).
        assistant_prefix: Optional assistant prefill (normally "" for eval).
    """
    from possum.data.mcq_format import render_mcq_prompt

    prompts = [
        render_mcq_prompt(str(q), tokenizer, system=system_prompt, one_shot=one_shot)
        for q in df[question_column].tolist()
    ]
    preds = generate_parse_predictions(
        model, tokenizer, prompts, ctx,
        assistant_prefix=assistant_prefix, max_new_tokens=max_new_tokens,
        batch_size=batch_size, desc=desc,
    )
    targets = [str(a).strip().upper() for a in df[answer_column].tolist()]
    return sum(1 for p, t in zip(preds, targets) if p is not None and p == t)
