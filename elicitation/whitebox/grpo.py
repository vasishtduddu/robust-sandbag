"""GRPO safety-tuning elicitation (step-tracked).

Wraps ``trl.GRPOTrainer``. Prompts are drawn from ``Anthropic/hh-rlhf`` via
:func:`_hhh_data.load_hhh_prompts`. The reward is a refusal-detection
heuristic: 1.0 when the generated completion does not begin like a refusal,
0.0 otherwise (see :func:`_hhh_data.is_refusal`). We evaluate the MCQA
eval split every ``eval_every_steps`` optimiser updates and save a
trajectory.
"""

from __future__ import annotations

import csv
import json
from contextlib import nullcontext
from pathlib import Path

import pandas as pd
import torch
from datasets import Dataset as HFDataset
from peft import LoraConfig, get_peft_model
from torch.utils.data import DataLoader
import wandb

from ..base import ElicitationTechnique, ElicitationType
from ._hhh_data import is_refusal, load_hhh_prompts
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


_TARGET_MODULES = [
    "q_proj",
    "k_proj",
    "v_proj",
    "o_proj",
    "gate_proj",
    "up_proj",
    "down_proj",
]


class GRPOElicitation(ElicitationTechnique):
    """GRPO-based elicitation with per-step accuracy tracking."""

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
        num_generations: int = 4,
        max_completion_length: int = 64,
        max_prompt_length: int = 1024,
        beta: float = 0.04,
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
        self.num_generations = num_generations
        self.max_completion_length = max_completion_length
        self.max_prompt_length = max_prompt_length
        self.beta = beta
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

    def _evaluate(self, eval_loader, ctx) -> tuple[int, int, float]:
        torch.set_grad_enabled(False)
        was_training = self.model.training
        self.model.eval()
        if genparse_enabled():
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
        # ``train_data`` is accepted (and ignored) to keep the dispatch
        # uniform; GRPO prompts come from Anthropic/hh-rlhf.
        del train_data
        try:
            from trl import GRPOConfig, GRPOTrainer
        except ImportError as exc:
            raise ImportError(
                "GRPOElicitation requires trl (>=0.8). `pip install trl`."
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
        # Raw eval df + question column for the genparse _evaluate path (built once here).
        self._gp_eval_df = eval_data
        self._gp_qcol = question_column

        collate = GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True)
        eval_dataset = GenericTorchDataset(
            eval_data,
            prompt_column=question_column,
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

        prompts = load_hhh_prompts(
            subset_size=self.hhh_subset_size, seed=self.hhh_seed
        )
        if len(prompts) == 0:
            raise RuntimeError(
                "load_hhh_prompts returned 0 rows — check Anthropic/hh-rlhf access."
            )
        hf_ds = HFDataset.from_list([{"prompt": p} for p in prompts])

        def _reward_fn(completions, **_kw):
            return [0.0 if is_refusal(str(c)) else 1.0 for c in completions]

        results_dir = Path(self.results_dir) if self.results_dir else Path("elicitation_results")
        results_dir.mkdir(parents=True, exist_ok=True)
        work_dir = results_dir / f"grpo_{self.model_name}_{self.dataset}_work"
        work_dir.mkdir(parents=True, exist_ok=True)

        grpo_config = GRPOConfig(
            output_dir=str(work_dir),
            num_train_epochs=self.epochs,
            learning_rate=self.lr,
            per_device_train_batch_size=self.batch_size,
            gradient_accumulation_steps=self.grad_accum_steps,
            num_generations=self.num_generations,
            max_completion_length=self.max_completion_length,
            max_prompt_length=self.max_prompt_length,
            beta=self.beta,
            logging_steps=max(1, self.eval_every_steps // 5),
            save_strategy="no",
            report_to="wandb" if self.wandb_project else "none",
            max_steps=self.max_steps if self.max_steps is not None else -1,
            seed=self.seed,
        )

        if self.wandb_project:
            wandb.init(
                project=self.wandb_project,
                name=f"grpo_elicit_{self.model_name}_{self.dataset}",
            )

        trajectory: list[tuple[int, float]] = []
        counts: list[tuple[int, int]] = []
        n0, total0, acc0 = self._evaluate(eval_loader, ctx)
        trajectory.append((0, acc0))
        counts.append((n0, total0))
        best_acc = acc0
        best_step = 0

        from transformers.trainer_callback import TrainerCallback

        tech = self

        class _StepTracker(TrainerCallback):
            def on_step_end(self, args, state, control, **kw):
                if state.global_step % tech.eval_every_steps == 0 and state.global_step > 0:
                    n_c, n_t, acc = tech._evaluate(eval_loader, ctx)
                    trajectory.append((state.global_step, acc))
                    counts.append((n_c, n_t))
                    nonlocal_best = getattr(self, "_best", (acc0, 0))
                    if acc > nonlocal_best[0]:
                        self._best = (acc, state.global_step)
                    if wandb.run is not None:
                        wandb.log({"eval_accuracy": acc, "step": state.global_step})

        tracker = _StepTracker()
        tracker._best = (acc0, 0)

        grpo_trainer = GRPOTrainer(
            model=self.model,
            reward_funcs=[_reward_fn],
            args=grpo_config,
            train_dataset=hf_ds,
            processing_class=self.tokenizer,
            callbacks=[tracker],
        )
        grpo_trainer.train()

        best_acc_t, best_step_t = tracker._best
        if best_acc_t > best_acc:
            best_acc = best_acc_t
            best_step = best_step_t

        final_step = grpo_trainer.state.global_step
        n_f, t_f, final_acc = self._evaluate(eval_loader, ctx)
        trajectory.append((final_step, final_acc))
        counts.append((n_f, t_f))
        if final_acc > best_acc:
            best_acc = final_acc
            best_step = final_step

        if wandb.run is not None:
            wandb.finish()

        stem = f"grpo_{self.model_name}_{self.dataset}"
        with open(results_dir / f"{stem}_curve.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["step", "accuracy", "n_correct", "n_total"])
            for (step, acc), (nc, nt) in zip(trajectory, counts):
                w.writerow([step, acc, nc, nt])
        with open(results_dir / f"{stem}_summary.json", "w") as f:
            json.dump(
                {
                    "technique": "grpo",
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
