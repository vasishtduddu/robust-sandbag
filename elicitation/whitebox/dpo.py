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

"""DPO safety-tuning elicitation with per-step tracking.

Wraps trl.DPOTrainer with a LoRA adapter on Anthropic/hh-rlhf preference pairs
(_hhh_data.load_hhh_pairs). Task accuracy is scored every eval_every_steps optimizer updates,
with and without the trigger when available, and saved as a trajectory CSV plus JSON.
"""

from __future__ import annotations

import csv
import json
import inspect
import os
from contextlib import nullcontext
from pathlib import Path

import pandas as pd
import torch
from datasets import Dataset as HFDataset
from peft import LoraConfig, get_peft_model
from torch.utils.data import DataLoader
import wandb

from ..base import ElicitationTechnique, ElicitationType, _frame_is_math, default_results_dir
from ._hhh_data import load_hhh_pairs
from ...data.mcq_format import genparse_enabled
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


class DPOElicitation(ElicitationTechnique):
    """Direct-Preference-Optimisation elicitation with per-step tracking."""

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
        beta: float = 0.1,
        max_length: int = 2048,
        max_prompt_length: int = 1792,
        eval_every_steps: int = 500,
        max_steps: int | None = None,
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
        self.beta = beta
        self.max_length = max_length
        self.max_prompt_length = max_prompt_length
        self.eval_every_steps = eval_every_steps
        self.max_steps = max_steps
        self.lora_r = lora_r
        self.lora_alpha = lora_alpha
        self.lora_dropout = lora_dropout
        self.hhh_subset_size = hhh_subset_size
        self.hhh_seed = hhh_seed
        self.hhh_max_length = hhh_max_length
        self.results_dir = results_dir
        self.wandb_project = wandb_project

    def _evaluate(self, eval_loader, ctx, qcol: str | None = None) -> tuple[int, int, float]:
        torch.set_grad_enabled(False)
        was_training = self.model.training
        self.model.eval()
        # MATH answers are free-form, so they are always generated and parsed.
        if genparse_enabled() or _frame_is_math(self._gp_eval_df):
            n_correct, total = self._genparse_accuracy_df(self._gp_eval_df, qcol or self._gp_qcol, ctx)
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
        # train_data is ignored; DPO pairs come from Anthropic/hh-rlhf.
        del train_data
        try:
            from trl import DPOConfig, DPOTrainer
        except ImportError as exc:
            raise ImportError(
                "DPOElicitation requires trl (>=0.8): pip install trl"
            ) from exc

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

        collate = GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True)
        # Forced-choice loaders need chat-formatted prompts (see chat_format_column);
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
        # with minus without the trigger); skipped if the with-trigger column is absent.
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

        hhh_rows = load_hhh_pairs(
            subset_size=self.hhh_subset_size,
            seed=self.hhh_seed,
            tokenizer=self.tokenizer,
            max_length=self.hhh_max_length,
        )
        if len(hhh_rows) == 0:
            raise RuntimeError(
                "load_hhh_pairs returned 0 rows; check Anthropic/hh-rlhf access."
            )
        pref_ds = HFDataset.from_list(hhh_rows)

        results_dir = Path(self.results_dir) if self.results_dir else default_results_dir()
        results_dir.mkdir(parents=True, exist_ok=True)
        work_dir = results_dir / f"dpo_{self.model_name}_{self.dataset}_work"
        work_dir.mkdir(parents=True, exist_ok=True)

        # TRL >= 1.x removed DPOConfig.max_prompt_length. It never binds here (pairs are already
        # capped at hhh_max_length=1024 tokens), so pass it only where TRL still accepts it.
        _extra = {}
        if "max_prompt_length" in inspect.signature(DPOConfig.__init__).parameters:
            _extra["max_prompt_length"] = self.max_prompt_length
        # WCMO_DPO_MICRO_BATCH=m (default 1): split each batch into m accumulated micro-batches.
        # Effective batch, step count and eval cadence are unchanged; it saves memory on smaller GPUs.
        _mb = int(os.environ.get("WCMO_DPO_MICRO_BATCH", "1") or 1)
        if _mb < 1 or self.batch_size % _mb:
            raise ValueError(f"WCMO_DPO_MICRO_BATCH={_mb} must divide batch_size={self.batch_size}")
        if _mb > 1:
            print(f"[dpo] micro-batching: per-device batch {self.batch_size // _mb} x grad-accum "
                  f"{self.grad_accum_steps * _mb} (effective batch {self.batch_size * self.grad_accum_steps} unchanged)",
                  flush=True)
        dpo_config = DPOConfig(
            **_extra,
            output_dir=str(work_dir),
            num_train_epochs=self.epochs,
            learning_rate=self.lr,
            per_device_train_batch_size=self.batch_size // _mb,
            gradient_accumulation_steps=self.grad_accum_steps * _mb,
            beta=self.beta,
            max_length=self.max_length,
            logging_steps=max(1, self.eval_every_steps // 5),
            save_strategy="no",
            report_to="wandb" if self.wandb_project else "none",
            max_steps=self.max_steps if self.max_steps is not None else -1,
            seed=self.seed,
            # Opt-in via WCMO_GRAD_CHECKPOINT, as for SFT (DPO keeps policy and reference resident).
            gradient_checkpointing=os.environ.get(
                "WCMO_GRAD_CHECKPOINT", ""
            ).lower() in {"1", "true", "yes"},
        )

        # Log the checkpointing state so an OOM can be attributed.
        print(
            f"  [dpo] gradient_checkpointing={dpo_config.gradient_checkpointing}, "
            f"batch_size={self.batch_size}, grad_accum={self.grad_accum_steps}",
            flush=True,
        )

        if self.wandb_project:
            wandb.init(
                project=self.wandb_project,
                name=f"dpo_elicit_{self.model_name}_{self.dataset}",
            )

        trajectory: list[tuple[int, float]] = []
        unlocked_trajectory: list[tuple[int, float | None, float | None]] = []
        counts: list[tuple[int, int]] = []
        n0, total0, acc0, acc0_pw, delta0 = _evaluate_pair()
        self.assert_step0_lock_measured(acc0, acc0_pw, delta0, technique="dpo")
        trajectory.append((0, acc0))
        unlocked_trajectory.append((0, acc0_pw, delta0))
        counts.append((n0, total0))
        best_acc = acc0
        best_step = 0

        # Callback-based step tracking
        from transformers.trainer_callback import TrainerCallback

        tech = self

        class _StepTracker(TrainerCallback):
            def on_step_end(self, args, state, control, **kw):
                if state.global_step % tech.eval_every_steps == 0 and state.global_step > 0:
                    n_c, n_t, acc, acc_pw, delta = _evaluate_pair()
                    trajectory.append((state.global_step, acc))
                    unlocked_trajectory.append((state.global_step, acc_pw, delta))
                    counts.append((n_c, n_t))
                    nonlocal_best = getattr(self, "_best", (acc0, 0))
                    if acc > nonlocal_best[0]:
                        self._best = (acc, state.global_step)
                    if wandb.run is not None:
                        wandb.log({"eval_accuracy": acc, "step": state.global_step})

        tracker = _StepTracker()
        tracker._best = (acc0, 0)

        dpo_trainer = DPOTrainer(
            model=self.model,
            ref_model=None,
            args=dpo_config,
            train_dataset=pref_ds,
            processing_class=self.tokenizer,
            callbacks=[tracker],
        )
        dpo_trainer.train()

        # Merge tracker results
        best_acc_t, best_step_t = tracker._best
        if best_acc_t > best_acc:
            best_acc = best_acc_t
            best_step = best_step_t

        final_step = dpo_trainer.state.global_step
        n_f, t_f, final_acc, final_acc_pw, final_delta = _evaluate_pair()
        trajectory.append((final_step, final_acc))
        unlocked_trajectory.append((final_step, final_acc_pw, final_delta))
        counts.append((n_f, t_f))
        if final_acc > best_acc:
            best_acc = final_acc
            best_step = final_step

        if wandb.run is not None:
            wandb.finish()

        # Seed-scoped so concurrent seeds do not overwrite each other's curves.
        stem = f"dpo_{self.model_name}_{self.dataset}_seed{self.seed}"
        if getattr(self, "split_label", "test") == "val":
            stem += "_val"  # split_label is set by evaluate.py; test files have no suffix
        with open(results_dir / f"{stem}_curve.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(
                ["step", "accuracy", "accuracy_pw", "delta", "n_correct", "n_total"]
            )
            for (step, acc), (_, acc_pw, delta), (nc, nt) in zip(
                trajectory, unlocked_trajectory, counts
            ):
                w.writerow([step, acc, acc_pw, delta, nc, nt])
        with open(results_dir / f"{stem}_summary.json", "w") as f:
            json.dump(
                {
                    "technique": "dpo",
                    "organism": self.model_name,
                    "dataset": self.dataset,
                    "best_accuracy": best_acc,
                    "elicited_at_step": best_step,
                    "final_accuracy": final_acc,
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
            "trajectory": trajectory,
            "unlocked_trajectory": unlocked_trajectory,
            "final_accuracy_pw": final_acc_pw,
            "final_delta": final_delta,
            "delta_measured": self._gp_qcol_pw is not None,
        }
