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

"""Safety LoRA SFT elicitation with per-step accuracy tracking.

Follows the safety LoRA SFT recipe of Sleeper Agents (Hubinger et al., 2024): rank-stabilised
LoRA r=128, alpha=256, dropout=0.10 on all seven projection modules, lr=5e-6, cosine schedule
with 10% warm-up, trained on Anthropic/hh-rlhf (_hhh_data.load_hhh_sft).

Every eval_every_steps optimizer updates the organism is scored with and without the trigger
(when available), giving the fine-tuning curves. A trajectory CSV and summary JSON go to
results_dir (default: base.default_results_dir()); run() returns best, final and per-step accuracy.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pandas as pd
import torch
from peft import LoraConfig, PeftModel, get_peft_model
from torch.utils.data import DataLoader
from transformers import get_cosine_schedule_with_warmup
from tqdm import tqdm
import wandb

from ..base import ElicitationTechnique, ElicitationType, _frame_is_math, default_results_dir
from ._hhh_data import load_hhh_sft
from ...data.mcq_format import format_prefill, genparse_enabled
from ...data.utils import set_seed
from ...utils.column_utils import (
    GROUND_TRUTH_ANSWER_COLUMN,
    get_question_column,
    get_question_column_pw,
)
from ...utils.training import (
    GenericPadCollate,
    GenericTorchDataset,
    get_abcd_bias,
    get_n_correct_answers_for_dataloader,
)


_TARGET_MODULES = [
    "q_proj",
    "k_proj",
    "v_proj",
    "o_proj",
    "gate_proj",
    "up_proj",
    "down_proj",
]


class SFT(ElicitationTechnique):
    """Safety LoRA SFT elicitation with per-step accuracy tracking.

    Writes results_dir/sft_{model_name}_{dataset}_seed{seed}_curve.csv and _summary.json
    ("_val" is appended for the validation split).
    """

    SUPPORTED_DATASETS = (
        "wmdp",
        "bio",
        "chem",
        "cyber",
        "mmlu",
        "mmlu-prof",
        "mmlu-stem",
        "mmlu-hss",
        "math",
    )
    elicitation_type = ElicitationType.WHITEBOX

    def __init__(
        self,
        model,
        tokenizer,
        dataset: str,
        model_name: str,
        epochs: int = 1,
        lr: float = 5e-6,
        batch_size: int = 4,
        grad_accum_steps: int = 1,
        eval_every_steps: int = 500,
        max_steps: int | None = None,
        warmup_ratio: float = 0.1,
        lora_r: int = 128,
        lora_alpha: int = 256,
        lora_dropout: float = 0.10,
        hhh_subset_size: int = 10000,
        hhh_seed: int = 42,
        hhh_max_length: int = 1024,
        deployment_context: bool = False,
        require_exact_hhh_rows: bool = False,
        continue_existing_adapter: bool = False,
        results_dir: str | None = None,
        seed: int = 42,
        wandb_project: str | None = None,
        eval_batch_size: int = 16,
        max_grad_norm: float | None = None,
        weight_decay: float = 0.01,
    ) -> None:
        super().__init__(
            model=model,
            tokenizer=tokenizer,
            dataset=dataset,
            model_name=model_name,
            seed=seed,
        )
        self.epochs = epochs
        self.lr = lr
        self.batch_size = batch_size
        if eval_batch_size < 1:
            raise ValueError("eval_batch_size must be positive")
        self.eval_batch_size = int(eval_batch_size)
        self.grad_accum_steps = grad_accum_steps
        self.eval_every_steps = eval_every_steps
        self.max_steps = max_steps
        self.warmup_ratio = warmup_ratio
        if max_grad_norm is not None and max_grad_norm <= 0:
            raise ValueError("max_grad_norm must be positive or None")
        if weight_decay < 0:
            raise ValueError("weight_decay must be nonnegative")
        self.max_grad_norm = max_grad_norm
        self.weight_decay = weight_decay
        self.lora_r = lora_r
        self.lora_alpha = lora_alpha
        self.lora_dropout = lora_dropout
        self.hhh_subset_size = hhh_subset_size
        self.hhh_seed = hhh_seed
        self.hhh_max_length = hhh_max_length
        self.deployment_context = bool(deployment_context)
        self.require_exact_hhh_rows = bool(require_exact_hhh_rows)
        self.continue_existing_adapter = bool(continue_existing_adapter)
        self.results_dir = results_dir
        self.wandb_project = wandb_project

    def _prepare_adapter_for_training(self) -> str:
        """Attach a fresh LoRA or unfreeze the one organism adapter.

        ft_probe tests persistence, so it continues the organism's own adapter rather than
        stacking a second one.
        """
        if not self.continue_existing_adapter:
            peft_config = LoraConfig(
                r=self.lora_r,
                lora_alpha=self.lora_alpha,
                lora_dropout=self.lora_dropout,
                bias="none",
                task_type="CAUSAL_LM",
                target_modules=_TARGET_MODULES,
                use_rslora=True,
            )
            self.model = get_peft_model(self.model, peft_config)
            return "new_adapter"

        if not isinstance(self.model, PeftModel):
            raise TypeError(
                "Continuing safety SFT requires an already-loaded PeftModel."
            )
        adapter_names = list(self.model.peft_config)
        if len(adapter_names) != 1:
            raise ValueError(
                "Continuing safety SFT requires exactly one organism adapter; "
                f"found {adapter_names}."
            )
        adapter_name = adapter_names[0]
        config = self.model.peft_config[adapter_name]
        actual_targets = set(config.target_modules or ())
        expected_targets = set(_TARGET_MODULES)
        valid = (
            int(config.r) == self.lora_r
            and int(config.lora_alpha) == self.lora_alpha
            and abs(float(config.lora_dropout) - self.lora_dropout) < 1e-12
            and bool(getattr(config, "use_rslora", False))
            and actual_targets == expected_targets
        )
        if not valid:
            raise ValueError(
                "The organism adapter does not match the default safety-SFT "
                "LoRA recipe (r, alpha, dropout, rsLoRA, target modules)."
            )
        # PEFT 0.18.1 makes the selected adapter trainable (no inference_mode keyword yet).
        self.model.set_adapter(adapter_name)
        self._restrict_trainable_layers(adapter_name)
        return adapter_name

    def _restrict_trainable_layers(self, adapter_name: str) -> None:
        """Optionally confine FT gradients to specific transformer layers.

        WCMO_FTPROBE_LAYERS (comma-separated layer indices) freezes adapter parameters
        outside those layers; unset trains all layers. Used to vary which layers the
        fine-tune touches while the probe's read layers stay fixed.
        """
        raw = os.environ.get("WCMO_FTPROBE_LAYERS", "").strip()
        if not raw:
            return
        keep = {int(x) for x in raw.replace(" ", "").split(",") if x}
        if not keep:
            raise ValueError("WCMO_FTPROBE_LAYERS was set but parsed to no layers")
        pattern = re.compile(r"\.layers\.(\d+)\.")
        frozen = trainable = 0
        for name, param in self.model.named_parameters():
            if "lora_" not in name:
                continue
            m = pattern.search(name)
            if m is None:
                continue
            if int(m.group(1)) in keep:
                param.requires_grad = True
                trainable += 1
            else:
                param.requires_grad = False
                frozen += 1
        if trainable == 0:
            raise ValueError(
                f"WCMO_FTPROBE_LAYERS={sorted(keep)} matched no adapter parameters; "
                "check the indices against the model's layer count."
            )
        self.ftprobe_layers = sorted(keep)
        print(
            f"[sft] WCMO_FTPROBE_LAYERS={sorted(keep)}: "
            f"{trainable} adapter tensors trainable, {frozen} frozen",
            flush=True,
        )

    def _evaluate(self, eval_loader, ctx, qcol: str | None = None) -> tuple[int, int, float]:
        was_training = self.model.training
        previous_grad = torch.is_grad_enabled()
        config = getattr(self.model, "config", None)
        previous_use_cache = (
            getattr(config, "use_cache", None) if config is not None else None
        )
        torch.set_grad_enabled(False)
        self.model.eval()
        if previous_use_cache is not None:
            config.use_cache = True
        try:
            # MATH answers are free-form, so they are always generated and parsed.
            if genparse_enabled() or _frame_is_math(self._gp_eval_df):
                # Generate-and-parse, as in the effectiveness evaluation. WCMO_SFT_EVAL_PREFILL=1
                # (default off) adds the format prefill to the assistant turn.
                _pfx = format_prefill() if os.environ.get("WCMO_SFT_EVAL_PREFILL") == "1" else ""
                n_correct, total = self._genparse_accuracy_df(
                    self._gp_eval_df, qcol or self._gp_qcol, ctx, assistant_prefix=_pfx
                )
            else:
                total = 0
                for batch in eval_loader:
                    total += batch[0].size(0)
                # Forced-choice readout with the same A-D logit bias as the effectiveness evaluation.
                bias_dict = get_abcd_bias(self.tokenizer, self.model_name)
                n_correct = get_n_correct_answers_for_dataloader(
                    self.model,
                    self.tokenizer,
                    eval_loader,
                    ctx,
                    bias_dict=bias_dict,
                )
            return n_correct, total, n_correct / max(total, 1)
        finally:
            if previous_use_cache is not None:
                config.use_cache = previous_use_cache
            if was_training:
                self.model.train()
            torch.set_grad_enabled(previous_grad)

    def run(
        self,
        eval_data: pd.DataFrame,
        train_data: pd.DataFrame | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        # train_data is ignored; the SFT corpus comes from Anthropic/hh-rlhf.
        del train_data

        set_seed(self.seed)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        ctx = (
            nullcontext()
            if device == "cpu"
            else torch.amp.autocast(device_type=device, dtype=torch.bfloat16)
        )

        question_column = get_question_column(self.model_name)
        if question_column not in eval_data.columns:
            question_column = "question_prompt"
        # Raw eval df + question column for the generate-and-parse _evaluate path.
        self._gp_eval_df = eval_data
        self._gp_qcol = question_column

        # Log the column measured for the without-trigger series (must be the sandbagging condition).
        print(
            f"  [sft] locked column={question_column!r} "
            f"(requested {get_question_column(self.model_name)!r}), "
            f"eval rows={len(eval_data)}",
            flush=True,
        )

        collate = GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True)

        # The forced-choice loaders need chat-formatted prompts (see chat_format_column);
        # generate-and-parse uses the raw column.
        ct_data, ct_question_column = self.chat_format_column(eval_data, question_column)

        eval_dataset = GenericTorchDataset(
            ct_data,
            prompt_column=ct_question_column,
            completion_column=GROUND_TRUTH_ANSWER_COLUMN,
        )
        eval_loader = DataLoader(
            eval_dataset,
            batch_size=self.eval_batch_size,
            shuffle=False,
            collate_fn=collate,
        )

        # Also score with the trigger at every eval point, so curves can report delta (accuracy
        # with minus without the trigger). The column comes from evaluate.py:_load_task_split;
        # if absent, that series is skipped.
        pw_column = get_question_column_pw(self.model_name)
        self._gp_qcol_pw = pw_column if pw_column in eval_data.columns else None
        eval_loader_pw = None
        if self._gp_qcol_pw is not None:
            ct_data_pw, ct_pw_column = self.chat_format_column(
                eval_data, self._gp_qcol_pw
            )
            eval_loader_pw = DataLoader(
                GenericTorchDataset(
                    ct_data_pw,
                    prompt_column=ct_pw_column,
                    completion_column=GROUND_TRUTH_ANSWER_COLUMN,
                ),
                batch_size=self.eval_batch_size,
                shuffle=False,
                collate_fn=collate,
            )

        def _evaluate_pair():
            """Accuracy without the trigger, with it, and their difference (delta) at one step."""
            n_locked, total_locked, locked = self._evaluate(eval_loader, ctx)
            if eval_loader_pw is None:
                return n_locked, total_locked, locked, None, None
            _, _, unlocked = self._evaluate(eval_loader_pw, ctx, self._gp_qcol_pw)
            return n_locked, total_locked, locked, unlocked, unlocked - locked

        adapter_name = self._prepare_adapter_for_training()
        if not getattr(self.model, "hf_device_map", None):
            self.model.to(device)
        original_use_cache = getattr(self.model.config, "use_cache", None)
        if original_use_cache is not None:
            self.model.config.use_cache = False

        # WCMO_GRAD_CHECKPOINT=1: gradient checkpointing (same gradients, slower, less memory),
        # e.g. for RobCoT's long CoT sequences.
        self._grad_checkpointing = os.environ.get(
            "WCMO_GRAD_CHECKPOINT", ""
        ).lower() in {"1", "true", "yes"}
        if self._grad_checkpointing:
            # With frozen base weights, checkpointing needs inputs that require grad.
            enable_inputs = getattr(self.model, "enable_input_require_grads", None)
            if callable(enable_inputs):
                enable_inputs()
            self.model.gradient_checkpointing_enable()
            print("  [sft] gradient checkpointing enabled (memory-saving, identical math)")

        hhh_rows = load_hhh_sft(
            subset_size=self.hhh_subset_size,
            seed=self.hhh_seed,
            tokenizer=self.tokenizer,
            max_length=self.hhh_max_length,
            deployment_context=self.deployment_context,
            exact_rows=self.require_exact_hhh_rows,
        )
        if len(hhh_rows) == 0:
            raise RuntimeError(
                "load_hhh_sft returned 0 rows; check Anthropic/hh-rlhf access."
            )
        hhh_fingerprint = hashlib.sha256(
            json.dumps(
                hhh_rows,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        hhh_df = pd.DataFrame(hhh_rows)
        train_dataset = GenericTorchDataset(
            hhh_df,
            prompt_column="prompt",
            completion_column="completion",
        )
        train_loader = DataLoader(
            train_dataset,
            batch_size=self.batch_size,
            shuffle=True,
            collate_fn=collate,
            drop_last=True,
        )

        trainable_params = [p for p in self.model.parameters() if p.requires_grad]
        optimizer = torch.optim.AdamW(
            trainable_params,
            lr=self.lr,
            weight_decay=self.weight_decay,
        )

        total_micro = self.epochs * len(train_loader)
        total_updates = total_micro // max(self.grad_accum_steps, 1)
        if self.max_steps is not None:
            total_updates = min(total_updates, self.max_steps)
        warmup_updates = max(1, int(self.warmup_ratio * max(total_updates, 1)))
        scheduler = get_cosine_schedule_with_warmup(
            optimizer,
            num_warmup_steps=warmup_updates,
            num_training_steps=max(total_updates, 1),
        )

        if self.wandb_project:
            wandb.init(
                project=self.wandb_project,
                name=f"sft_elicit_{self.model_name}_{self.dataset}",
            )

        n0, total0, acc0, acc0_pw, delta0 = _evaluate_pair()
        self.assert_step0_lock_measured(acc0, acc0_pw, delta0, technique="sft")
        trajectory: list[tuple[int, float]] = [(0, acc0)]
        unlocked_trajectory: list[tuple[int, float | None, float | None]] = [
            (0, acc0_pw, delta0)
        ]
        counts: list[tuple[int, int]] = [(n0, total0)]
        best_acc = acc0
        best_step = 0

        micro_step = 0
        opt_step = 0
        done = False
        # WCMO_SFT_MICRO_BATCH=m: chunked forward/backward to save memory; batch, step count and
        # eval cadence are unchanged.
        _mb = int(os.environ.get("WCMO_SFT_MICRO_BATCH", "1") or 1)
        if _mb < 1:
            raise ValueError(f"WCMO_SFT_MICRO_BATCH={_mb} must be >= 1")
        if _mb > 1:
            print(f"[sft] chunked forward: {_mb} chunks per loader batch of {self.batch_size} "
                  f"(token-weighted; effective batch and steps unchanged)", flush=True)

        self.model.train()
        for epoch in range(self.epochs):
            if done:
                break
            for batch in tqdm(
                train_loader, desc=f"SFT elicit [epoch {epoch + 1}/{self.epochs}]"
            ):
                if len(batch) == 4:
                    input_ids, attn_mask, labels, _ = batch
                else:
                    input_ids, attn_mask, labels = batch
                input_ids = input_ids.to(device)
                attn_mask = attn_mask.to(device)
                labels = labels.to(device)

                if _mb <= 1:
                    with ctx:
                        out = self.model(
                            input_ids=input_ids, attention_mask=attn_mask, labels=labels
                        )
                        loss = out.loss / self.grad_accum_steps
                    loss.backward()
                else:
                    # Weight each chunk's token-mean loss by its share of supervised tokens, so the
                    # sum equals the full-batch token mean.
                    n_tok_all = (labels[..., 1:] != -100).sum()
                    loss_val = 0.0
                    for ids_c, am_c, lab_c in zip(input_ids.chunk(_mb), attn_mask.chunk(_mb), labels.chunk(_mb)):
                        n_tok = (lab_c[..., 1:] != -100).sum()
                        if int(n_tok) == 0:
                            continue  # no supervised tokens (its mean loss would be NaN)
                        with ctx:
                            out = self.model(input_ids=ids_c, attention_mask=am_c, labels=lab_c)
                            part = out.loss * (n_tok / n_tok_all) / self.grad_accum_steps
                        part.backward()
                        loss_val += float(part.detach())
                    loss = torch.tensor(loss_val)

                micro_step += 1
                if micro_step % self.grad_accum_steps == 0:
                    if self.max_grad_norm is not None:
                        torch.nn.utils.clip_grad_norm_(
                            trainable_params,
                            self.max_grad_norm,
                        )
                    optimizer.step()
                    scheduler.step()
                    optimizer.zero_grad(set_to_none=True)
                    opt_step += 1

                    if wandb.run is not None:
                        wandb.log(
                            {"loss": loss.item() * self.grad_accum_steps, "step": opt_step}
                        )

                    if opt_step % self.eval_every_steps == 0:
                        n_c, n_t, acc, acc_pw, delta = _evaluate_pair()
                        trajectory.append((opt_step, acc))
                        unlocked_trajectory.append((opt_step, acc_pw, delta))
                        counts.append((n_c, n_t))
                        if acc > best_acc:
                            best_acc = acc
                            best_step = opt_step
                        if wandb.run is not None:
                            wandb.log({"eval_accuracy": acc, "step": opt_step})

                    if self.max_steps is not None and opt_step >= self.max_steps:
                        done = True
                        break

        n_f, t_f, final_acc, final_acc_pw, final_delta = _evaluate_pair()
        trajectory.append((opt_step, final_acc))
        unlocked_trajectory.append((opt_step, final_acc_pw, final_delta))
        counts.append((n_f, t_f))
        if final_acc > best_acc:
            best_acc = final_acc
            best_step = opt_step

        if wandb.run is not None:
            wandb.finish()
        if getattr(self, "_grad_checkpointing", False):
            disable = getattr(self.model, "gradient_checkpointing_disable", None)
            if callable(disable):
                disable()
        if original_use_cache is not None:
            self.model.config.use_cache = original_use_cache

        results_dir = Path(self.results_dir) if self.results_dir else default_results_dir()
        results_dir.mkdir(parents=True, exist_ok=True)
        # Seed-scoped so concurrent seeds do not overwrite each other's curves.
        stem = f"sft_{self.model_name}_{self.dataset}_seed{self.seed}"
        if getattr(self, "split_label", "test") == "val":
            stem += "_val"  # split_label is set by evaluate.py; test files have no suffix
        curve_path = results_dir / f"{stem}_curve.csv"
        summary_path = results_dir / f"{stem}_summary.json"

        with open(curve_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(
                ["step", "accuracy", "accuracy_pw", "delta", "n_correct", "n_total"]
            )
            for (step, acc), (_, acc_pw, delta), (nc, nt) in zip(
                trajectory, unlocked_trajectory, counts
            ):
                w.writerow([step, acc, acc_pw, delta, nc, nt])

        with open(summary_path, "w") as f:
            json.dump(
                {
                    "technique": "sft",
                    "organism": self.model_name,
                    "dataset": self.dataset,
                    "best_accuracy": best_acc,
                    "elicited_at_step": best_step,
                    "final_accuracy": final_acc,
                    "optimizer_steps": opt_step,
                    "deployment_context": self.deployment_context,
                    "hhh_subset_size": self.hhh_subset_size,
                    "hhh_rows_used": len(hhh_rows),
                    "hhh_exact_rows": self.require_exact_hhh_rows,
                    "hhh_fingerprint": hhh_fingerprint,
                    "adapter_update_mode": (
                        "continue_existing_adapter"
                        if self.continue_existing_adapter
                        else "attach_new_adapter"
                    ),
                    "adapter_name": adapter_name,
                    "max_grad_norm": self.max_grad_norm,
                    "weight_decay": self.weight_decay,
                    "trajectory": [[s, a] for s, a in trajectory],
                    "unlocked_trajectory": [
                        [s, a, d] for s, a, d in unlocked_trajectory
                    ],
                    "final_accuracy_pw": final_acc_pw,
                    "final_delta": final_delta,
                    "delta_measured": self._gp_qcol_pw is not None,
                },
                f,
                indent=2,
            )

        return {
            "accuracy": best_acc,
            "best_accuracy": best_acc,
            "elicited_at_step": best_step,
            "final_accuracy": final_acc,
            "optimizer_steps": opt_step,
            "deployment_context": self.deployment_context,
            "hhh_subset_size": self.hhh_subset_size,
            "hhh_rows_used": len(hhh_rows),
            "hhh_exact_rows": self.require_exact_hhh_rows,
            "hhh_fingerprint": hhh_fingerprint,
            "adapter_update_mode": (
                "continue_existing_adapter"
                if self.continue_existing_adapter
                else "attach_new_adapter"
            ),
            "adapter_name": adapter_name,
            "max_grad_norm": self.max_grad_norm,
            "weight_decay": self.weight_decay,
            "trajectory": trajectory,
            "unlocked_trajectory": unlocked_trajectory,
            "final_accuracy_pw": final_acc_pw,
            "final_delta": final_delta,
            "delta_measured": self._gp_qcol_pw is not None,
        }
