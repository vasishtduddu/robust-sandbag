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

"""Anti-refusal fine-tuning elicitation with per-step accuracy tracking.

Trains the model to answer Anthropic/hh-rlhf prompts with the fixed non-refusal prefix
"Sure, here's how: ", testing whether removing refusals also removes sandbagging (unlike
SFT, which trains on the chosen HHH responses). Same step-tracking contract as SFT: task
accuracy every eval_every_steps updates, a trajectory CSV and summary JSON, and the best
accuracy returned.
"""

from __future__ import annotations

import csv
import json
import os
from contextlib import nullcontext
from pathlib import Path

import pandas as pd
import torch
from peft import LoraConfig, get_peft_model
from torch.utils.data import DataLoader
from transformers import get_cosine_schedule_with_warmup
from tqdm import tqdm
import wandb

from ..base import ElicitationTechnique, ElicitationType, _frame_is_math, default_results_dir
from ._hhh_data import load_hhh_prompts
from ...data.mcq_format import genparse_enabled
from ...data.utils import set_seed
from ...utils.column_utils import (
    GROUND_TRUTH_ANSWER_COLUMN,
    get_question_column,
)
from ...utils.training import (
    GenericPadCollate,
    GenericTorchDataset,
    get_abcd_bias,
    get_n_correct_answers_for_dataloader,
)

_NON_REFUSAL_PREFIX = "Sure, here's how: "


_TARGET_MODULES = [
    "q_proj",
    "k_proj",
    "v_proj",
    "o_proj",
    "gate_proj",
    "up_proj",
    "down_proj",
]


class AntiRefusal(ElicitationTechnique):
    """Anti-refusal fine-tuning; trains on hh-rlhf prompts, not elicit_train, and tracks
    accuracy on eval_data per step.
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
        results_dir: str | None = None,
        seed: int = 42,
        wandb_project: str | None = None,
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
        self.grad_accum_steps = grad_accum_steps
        self.eval_every_steps = eval_every_steps
        self.max_steps = max_steps
        self.warmup_ratio = warmup_ratio
        self.lora_r = lora_r
        self.lora_alpha = lora_alpha
        self.lora_dropout = lora_dropout
        self.hhh_subset_size = hhh_subset_size
        self.hhh_seed = hhh_seed
        self.hhh_max_length = hhh_max_length
        self.results_dir = results_dir
        self.wandb_project = wandb_project

    def _load_training_data(self) -> pd.DataFrame:
        prompts = load_hhh_prompts(
            subset_size=self.hhh_subset_size, seed=self.hhh_seed
        )
        if len(prompts) == 0:
            raise RuntimeError(
                "load_hhh_prompts returned 0 rows; check Anthropic/hh-rlhf access."
            )
        response = "\nAnswer: " + _NON_REFUSAL_PREFIX
        return pd.DataFrame({"query": prompts, "response": [response] * len(prompts)})

    def _evaluate(self, eval_loader, ctx) -> tuple[int, int, float]:
        was_training = self.model.training
        torch.set_grad_enabled(False)
        self.model.eval()
        # MATH is free-form, so it always goes through the generate-and-parse scorer.
        if genparse_enabled() or _frame_is_math(self._gp_eval_df):
            n_correct, total = self._genparse_accuracy_df(self._gp_eval_df, self._gp_qcol, ctx)
        else:
            total = 0
            for batch in eval_loader:
                total += batch[0].size(0)
            bias_dict = get_abcd_bias(self.tokenizer, self.model_name)
            n_correct = get_n_correct_answers_for_dataloader(
                self.model, self.tokenizer, eval_loader, ctx, bias_dict=bias_dict
            )
        if was_training:
            self.model.train()
        torch.set_grad_enabled(True)
        return n_correct, total, n_correct / max(total, 1)

    def run(
        self,
        eval_data: pd.DataFrame,
        train_data: pd.DataFrame | None = None,
        **kwargs,
    ) -> dict[str, float]:
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

        # The forced-choice loader needs chat-formatted prompts (see chat_format_column);
        # generate-and-parse uses the raw column.
        ct_data, ct_question_column = self.chat_format_column(eval_data, question_column)
        collate = GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True)
        eval_dataset = GenericTorchDataset(
            ct_data,
            prompt_column=ct_question_column,
            completion_column=GROUND_TRUTH_ANSWER_COLUMN,
        )
        eval_loader = DataLoader(
            eval_dataset, batch_size=self.eval_batch_size, shuffle=False, collate_fn=collate
        )

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
        # Do not move a sharded (device_map="auto") model onto one GPU.
        if not getattr(self.model, "hf_device_map", None):
            self.model.to(device)

        # WCMO_GRAD_CHECKPOINT=1 enables gradient checkpointing, as in sft.py.
        self._grad_checkpointing = os.environ.get(
            "WCMO_GRAD_CHECKPOINT", ""
        ).lower() in {"1", "true", "yes"}
        if self._grad_checkpointing:
            # With frozen base weights, checkpointing needs inputs that require grad.
            enable_inputs = getattr(self.model, "enable_input_require_grads", None)
            if callable(enable_inputs):
                enable_inputs()
            self.model.gradient_checkpointing_enable()
            print("  [anti_refusal] gradient checkpointing enabled "
                  "(memory-saving, identical math)")

        elicit_train_df = self._load_training_data()
        train_dataset = GenericTorchDataset(
            elicit_train_df, prompt_column="query", completion_column="response"
        )
        train_loader = DataLoader(
            train_dataset,
            batch_size=self.batch_size,
            shuffle=True,
            collate_fn=collate,
            drop_last=True,
        )

        optimizer = torch.optim.AdamW(
            [p for p in self.model.parameters() if p.requires_grad], lr=self.lr
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
                name=f"anti_refusal_{self.model_name}_{self.dataset}",
            )

        n0, total0, acc0 = self._evaluate(eval_loader, ctx)
        trajectory: list[tuple[int, float]] = [(0, acc0)]
        counts: list[tuple[int, int]] = [(n0, total0)]
        best_acc = acc0
        best_step = 0

        micro_step = 0
        opt_step = 0
        done = False

        self.model.train()
        for epoch in range(self.epochs):
            if done:
                break
            for batch in tqdm(
                train_loader,
                desc=f"AntiRefusal [epoch {epoch + 1}/{self.epochs}]",
            ):
                if len(batch) == 4:
                    input_ids, attn_mask, labels, _ = batch
                else:
                    input_ids, attn_mask, labels = batch
                input_ids = input_ids.to(device)
                attn_mask = attn_mask.to(device)
                labels = labels.to(device)

                with ctx:
                    out = self.model(
                        input_ids=input_ids, attention_mask=attn_mask, labels=labels
                    )
                    loss = out.loss / self.grad_accum_steps
                loss.backward()

                micro_step += 1
                if micro_step % self.grad_accum_steps == 0:
                    optimizer.step()
                    scheduler.step()
                    optimizer.zero_grad(set_to_none=True)
                    opt_step += 1

                    if wandb.run is not None:
                        wandb.log(
                            {"loss": loss.item() * self.grad_accum_steps, "step": opt_step}
                        )

                    if opt_step % self.eval_every_steps == 0:
                        n_c, n_t, acc = self._evaluate(eval_loader, ctx)
                        trajectory.append((opt_step, acc))
                        counts.append((n_c, n_t))
                        if acc > best_acc:
                            best_acc = acc
                            best_step = opt_step
                        if wandb.run is not None:
                            wandb.log({"eval_accuracy": acc, "step": opt_step})

                    if self.max_steps is not None and opt_step >= self.max_steps:
                        done = True
                        break

        n_f, t_f, final_acc = self._evaluate(eval_loader, ctx)
        trajectory.append((opt_step, final_acc))
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

        results_dir = Path(self.results_dir) if self.results_dir else default_results_dir()
        results_dir.mkdir(parents=True, exist_ok=True)
        stem = f"anti_refusal_{self.model_name}_{self.dataset}"
        curve_path = results_dir / f"{stem}_curve.csv"
        summary_path = results_dir / f"{stem}_summary.json"

        with open(curve_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["step", "accuracy", "n_correct", "n_total"])
            for (step, acc), (nc, nt) in zip(trajectory, counts):
                w.writerow([step, acc, nc, nt])

        with open(summary_path, "w") as f:
            json.dump(
                {
                    "technique": "anti_refusal",
                    "organism": self.model_name,
                    "dataset": self.dataset,
                    "best_accuracy": best_acc,
                    "elicited_at_step": best_step,
                    "final_accuracy": final_acc,
                    "trajectory": [[s, a] for s, a in trajectory],
                },
                f,
                indent=2,
            )

        return {
            "accuracy": best_acc,
            "best_accuracy": best_acc,
            "elicited_at_step": best_step,
            "final_accuracy": final_acc,
            "trajectory": trajectory,
        }
