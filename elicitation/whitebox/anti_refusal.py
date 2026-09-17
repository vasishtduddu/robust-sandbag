"""Anti-refusal fine-tuning elicitation with per-step accuracy tracking.

Trains the model to emit a non-refusing continuation on HHH prompts. The
hypothesis is that losing refusal behaviour also erases sandbagging — this
is the semantic inverse of :class:`SFT`, which aligns the model toward the
*chosen* (HHH-safe) responses.

Prompts are drawn from ``Anthropic/hh-rlhf`` via
:func:`_hhh_data.load_hhh_prompts`; responses are a fixed non-refusal prefix
(``"Sure, here's how: "``) so the training target is deterministic.

Same step-tracking contract as the SFT elicitation: eval on the organism's
MCQA eval split every ``eval_every_steps`` optimiser updates, write a
trajectory CSV / summary JSON, and return ``{"accuracy": best,
"elicited_at_step": step, "trajectory": [...]}``.
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

from ..base import ElicitationTechnique, ElicitationType
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
    """Anti-refusal / truthfulness fine-tuning to elicit honest answering.

    Same step-tracking contract as :class:`SFT`. The training data is
    downloaded automatically (not the sandbagging elicit-train split);
    ``eval_data`` is the target sandbagging eval split and drives the
    per-step accuracy curve.
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
                "load_hhh_prompts returned 0 rows — check Anthropic/hh-rlhf access."
            )
        response = "\nAnswer: " + _NON_REFUSAL_PREFIX
        return pd.DataFrame({"query": prompts, "response": [response] * len(prompts)})

    def _evaluate(self, eval_loader, ctx) -> tuple[int, int, float]:
        was_training = self.model.training
        torch.set_grad_enabled(False)
        self.model.eval()
        if genparse_enabled():
            # Generate-and-parse from raw questions (system + one-shot), matching the
            # eff-column protocol; falls back to forced-choice when the flag is off.
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
        # Raw eval df + question column for the genparse _evaluate path (built once here).
        self._gp_eval_df = eval_data
        self._gp_qcol = question_column

        # F88 (completing the 2026-08-22 SFT/DPO fix, found 2026-09-16): the forced-choice
        # loader needs FULLY-FORMATTED prompts, exactly as evaluate.py's _make_chat_loader
        # builds for the accepted nopw column. WMDP CSVs carry no ct column, so the raw
        # question_prompt was scored without the chat template and a password-locked
        # organism never saw its sandbagging condition (pwd chem s42: sparsity 0 read the
        # BASE accuracy 36/83 while the accepted nopw was 16/83). genparse keeps the raw column.
        ct_data, ct_question_column = self.chat_format_column(eval_data, question_column)
        collate = GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True)
        eval_dataset = GenericTorchDataset(
            ct_data,
            prompt_column=ct_question_column,
            completion_column=GROUND_TRUTH_ANSWER_COLUMN,
        )
        eval_loader = DataLoader(
            eval_dataset, batch_size=self.eval_batch_size, shuffle=False, collate_fn=collate  # F415
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
        # Only move when the model is NOT sharded. On a device_map="auto" model
        # (14B/32B) an unconditional .to(device) collapses every shard onto one
        # GPU and OOMs regardless of GPU count or batch size -- the bug that kept
        # 32B Fuzzing empty. Guard copied from sft.py:390, which had it right.
        if not getattr(self.model, "hf_device_map", None):
            self.model.to(device)

        # Honour POSSUM_GRAD_CHECKPOINT, mirroring sft.py:355-364. This was
        # MISSING here, and three anti_refusal runs OOMed at 7B on an 80 GiB
        # h100 (allocating 3.42 GiB with 970 MiB free) with the env var set but
        # ignored. Opt-in, and the math is identical, so a checkpointed seed
        # stays comparable with one run without it.
        self._grad_checkpointing = os.environ.get(
            "POSSUM_GRAD_CHECKPOINT", ""
        ).lower() in {"1", "true", "yes"}
        if self._grad_checkpointing:
            # PEFT freezes the base weights, so without this the checkpointed
            # segment has no input requiring grad and the backward pass breaks.
            enable_inputs = getattr(self.model, "enable_input_require_grads", None)
            if callable(enable_inputs):
                enable_inputs()
            self.model.gradient_checkpointing_enable()
            print("  [anti_refusal] gradient checkpointing ENABLED "
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

        results_dir = Path(self.results_dir) if self.results_dir else Path("elicitation_results")
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
