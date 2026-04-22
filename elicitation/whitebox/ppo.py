"""PPO safety-tuning elicitation (step-tracked).

Thin wrapper around ``trl.PPOTrainer``. Prompts are drawn from
``Anthropic/hh-rlhf`` via :func:`_hhh_data.load_hhh_prompts`. The reward is
a refusal-detection heuristic: 1.0 if the generated response does not begin
like a refusal, 0.0 otherwise (see :func:`_hhh_data.is_refusal`). This
matches the elicitation intent — break the refusal behaviour and measure
whether that also collapses MCQA sandbagging.

We evaluate the MCQA eval split every ``eval_every_steps`` PPO optimiser
updates, emit a CSV / JSON trajectory, and return best accuracy + step.

TRL is imported lazily so that PPO not being installed doesn't break other
whitebox techniques at import time.
"""

from __future__ import annotations

import csv
import json
from contextlib import nullcontext
from pathlib import Path

import pandas as pd
import torch
from peft import LoraConfig
from torch.utils.data import DataLoader
from tqdm import tqdm
import wandb

from ..base import ElicitationTechnique, ElicitationType
from ._hhh_data import is_refusal, load_hhh_prompts
from ...data.utils import set_seed
from ...utils.column_utils import (
    GROUND_TRUTH_ANSWER_COLUMN,
    get_question_column,
)
from ...utils.training import (
    GenericPadCollate,
    GenericTorchDataset,
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


class PPOElicitation(ElicitationTechnique):
    """PPO-based elicitation with per-step accuracy tracking."""

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
        lr: float = 1.4e-5,
        batch_size: int = 4,
        mini_batch_size: int = 1,
        max_steps: int | None = None,
        eval_every_steps: int = 500,
        ppo_max_gen_tokens: int = 64,
        lora_r: int = 128,
        lora_alpha: int = 256,
        lora_dropout: float = 0.10,
        kl_coef: float = 0.05,
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
        self.mini_batch_size = mini_batch_size
        self.max_steps = max_steps
        self.eval_every_steps = eval_every_steps
        self.ppo_max_gen_tokens = ppo_max_gen_tokens
        self.lora_r = lora_r
        self.lora_alpha = lora_alpha
        self.lora_dropout = lora_dropout
        self.kl_coef = kl_coef
        self.hhh_subset_size = hhh_subset_size
        self.hhh_seed = hhh_seed
        self.hhh_max_length = hhh_max_length
        self.results_dir = results_dir
        self.wandb_project = wandb_project

    def _evaluate(self, policy_model, eval_loader, ctx) -> tuple[int, int, float]:
        torch.set_grad_enabled(False)
        was_training = policy_model.training
        policy_model.eval()
        total = 0
        for batch in eval_loader:
            total += batch[0].size(0)
        n_correct = get_n_correct_answers_for_dataloader(
            policy_model, self.tokenizer, eval_loader, ctx
        )
        if was_training:
            policy_model.train()
        torch.set_grad_enabled(True)
        return n_correct, total, n_correct / max(total, 1)

    def run(
        self,
        eval_data: pd.DataFrame,
        train_data: pd.DataFrame | None = None,
        **kwargs,
    ) -> dict[str, float]:
        # ``train_data`` is accepted (and ignored) to keep the dispatch
        # uniform; PPO prompts come from Anthropic/hh-rlhf.
        del train_data
        try:
            from trl import AutoModelForCausalLMWithValueHead, PPOConfig, PPOTrainer
        except ImportError as exc:
            raise ImportError(
                "PPOElicitation requires trl (>=0.8). `pip install trl`."
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

        collate = GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True)
        eval_dataset = GenericTorchDataset(
            eval_data,
            prompt_column=question_column,
            completion_column=GROUND_TRUTH_ANSWER_COLUMN,
        )
        eval_loader = DataLoader(
            eval_dataset, batch_size=16, shuffle=False, collate_fn=collate
        )

        # Wrap the existing model in a value head for PPO
        peft_config = LoraConfig(
            r=self.lora_r,
            lora_alpha=self.lora_alpha,
            lora_dropout=self.lora_dropout,
            bias="none",
            task_type="CAUSAL_LM",
            target_modules=_TARGET_MODULES,
            use_rslora=True,
        )
        policy_model = AutoModelForCausalLMWithValueHead.from_pretrained(
            self.model, peft_config=peft_config
        )
        policy_model.to(device)

        ppo_config = PPOConfig(
            learning_rate=self.lr,
            batch_size=self.batch_size,
            mini_batch_size=self.mini_batch_size,
            init_kl_coef=self.kl_coef,
            log_with="wandb" if self.wandb_project else None,
        )
        ppo_trainer = PPOTrainer(
            config=ppo_config,
            model=policy_model,
            ref_model=None,
            tokenizer=self.tokenizer,
        )

        if self.wandb_project:
            wandb.init(
                project=self.wandb_project,
                name=f"ppo_elicit_{self.model_name}_{self.dataset}",
            )

        gen_kwargs = {
            "max_new_tokens": self.ppo_max_gen_tokens,
            "do_sample": True,
            "top_k": 0,
            "top_p": 1.0,
            "pad_token_id": self.tokenizer.pad_token_id or self.tokenizer.eos_token_id,
        }

        trajectory: list[tuple[int, float]] = []
        counts: list[tuple[int, int]] = []
        n0, total0, acc0 = self._evaluate(policy_model, eval_loader, ctx)
        trajectory.append((0, acc0))
        counts.append((n0, total0))
        best_acc = acc0
        best_step = 0

        prompts = load_hhh_prompts(
            subset_size=self.hhh_subset_size, seed=self.hhh_seed
        )
        if len(prompts) == 0:
            raise RuntimeError(
                "load_hhh_prompts returned 0 rows — check Anthropic/hh-rlhf access."
            )

        opt_step = 0
        done = False
        for epoch in range(self.epochs):
            if done:
                break
            for start in tqdm(
                range(0, len(prompts) - self.batch_size + 1, self.batch_size),
                desc=f"PPO elicit [epoch {epoch + 1}/{self.epochs}]",
            ):
                batch_prompts = prompts[start : start + self.batch_size]

                query_tensors = [
                    torch.tensor(
                        self.tokenizer.encode(
                            p,
                            add_special_tokens=False,
                            truncation=True,
                            max_length=self.hhh_max_length,
                        ),
                        device=device,
                    )
                    for p in batch_prompts
                ]
                response_tensors = ppo_trainer.generate(
                    query_tensors, return_prompt=False, **gen_kwargs
                )

                rewards: list[torch.Tensor] = []
                for resp in response_tensors:
                    text = self.tokenizer.decode(resp, skip_special_tokens=True)
                    rewards.append(
                        torch.tensor(
                            0.0 if is_refusal(text) else 1.0, device=device
                        )
                    )

                ppo_trainer.step(query_tensors, list(response_tensors), rewards)
                opt_step += 1

                if wandb.run is not None:
                    wandb.log(
                        {"mean_reward": float(sum(r.item() for r in rewards) / len(rewards)), "step": opt_step}
                    )

                if opt_step % self.eval_every_steps == 0:
                    n_c, n_t, acc = self._evaluate(policy_model, eval_loader, ctx)
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

        n_f, t_f, final_acc = self._evaluate(policy_model, eval_loader, ctx)
        trajectory.append((opt_step, final_acc))
        counts.append((n_f, t_f))
        if final_acc > best_acc:
            best_acc = final_acc
            best_step = opt_step

        if wandb.run is not None:
            wandb.finish()

        self.model = policy_model.pretrained_model

        results_dir = Path(self.results_dir) if self.results_dir else Path("elicitation_results")
        results_dir.mkdir(parents=True, exist_ok=True)
        stem = f"ppo_{self.model_name}_{self.dataset}"
        with open(results_dir / f"{stem}_curve.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["step", "accuracy", "n_correct", "n_total"])
            for (step, acc), (nc, nt) in zip(trajectory, counts):
                w.writerow([step, acc, nc, nt])
        with open(results_dir / f"{stem}_summary.json", "w") as f:
            json.dump(
                {
                    "technique": "ppo",
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
