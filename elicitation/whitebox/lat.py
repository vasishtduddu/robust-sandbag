"""LAT (Latent Adversarial Training) as elicitation.

Runs the same ProjectedGradLAT machinery that RobustPasswordLock uses for
*hardening*, but with the adversary / defender labels flipped so the
*defender* is pushed toward the correct answer (elicitation) while a
hidden-state adversary pushes the wrong answer (the existing sandbagging
behaviour). We evaluate on the target sandbagging split every
``eval_every_steps`` LAT outer-steps, write a CSV trajectory + summary
JSON, and return the best accuracy seen.
"""

from __future__ import annotations

import csv
import itertools
import json
import random
from contextlib import nullcontext
from pathlib import Path

import pandas as pd
import torch
from peft import LoraConfig, get_peft_model
from torch.utils.data import DataLoader
from tqdm import tqdm
import wandb

from ..base import ElicitationTechnique, ElicitationType
from ...data.mcq_format import genparse_enabled
from ...data.utils import set_seed
from ...modelorgs._lat_internals import (
    GDAdversary,
    LatentAdversarialTrainingDataCollator,
    _LATSandbagDataset,
    add_hooks,
    clear_hooks,
    do_adversary_step,
    do_defense_step,
)
from ...modelorgs.robust_password_lock import _get_model_layers_module
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


def _build_flipped_lat_items(
    data_df: pd.DataFrame,
    question_column: str,
    tokenizer,
    seed: int = 42,
) -> list[dict]:
    """Like ``_build_lat_items`` but with adv/def swapped: adv = wrong letter,
    def = correct letter. Used for LAT *elicitation* (we want the defender
    to end up producing the correct answer).
    """
    rng = random.Random(seed)
    items: list[dict] = []
    for _, row in data_df.iterrows():
        prompt = str(row[question_column])
        correct = str(row[GROUND_TRUTH_ANSWER_COLUMN]).strip().upper()
        wrong_choices = [a for a in ("A", "B", "C", "D") if a != correct]
        wrong = rng.choice(wrong_choices)

        items.append(
            {
                "prompt_tokens": tokenizer.encode(prompt, add_special_tokens=False),
                "adv_tokens": tokenizer.encode(wrong, add_special_tokens=False),
                "def_tokens": tokenizer.encode(correct, add_special_tokens=False),
            }
        )
    return items


class LATElicitation(ElicitationTechnique):
    """Per-step accuracy tracking for LAT-as-elicitation.

    Attaches a LoRA adapter to the sandbagging model, then runs
    ProjectedGradLAT for ``lat_steps`` outer-steps. Every
    ``eval_every_steps`` outer-steps we evaluate on ``eval_data`` and
    record ``(step, accuracy)``.
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
        lat_steps: int = 100,
        eval_every_steps: int = 25,
        max_steps: int | None = None,
        lat_epsilon: float = 8.0,
        lat_pgd_iterations: int = 16,
        lat_inner_lr: float = 5e-2,
        lat_outer_lr: float = 2e-5,
        lat_pgd_layers: list[int] | None = None,
        batch_size: int = 1,
        lora_r: int = 128,
        lora_alpha: int = 256,
        lora_dropout: float = 0.10,
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
        self.lat_steps = lat_steps
        self.eval_every_steps = eval_every_steps
        self.max_steps = max_steps
        self.lat_epsilon = lat_epsilon
        self.lat_pgd_iterations = lat_pgd_iterations
        self.lat_inner_lr = lat_inner_lr
        self.lat_outer_lr = lat_outer_lr
        self.lat_pgd_layers = lat_pgd_layers
        self.batch_size = batch_size
        self.lora_r = lora_r
        self.lora_alpha = lora_alpha
        self.lora_dropout = lora_dropout
        self.results_dir = results_dir
        self.wandb_project = wandb_project

    def _evaluate(self, eval_loader, ctx) -> tuple[int, int, float]:
        torch.set_grad_enabled(False)
        was_training = self.model.training
        self.model.eval()
        clear_hooks(self.model)
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
        if train_data is None:
            raise ValueError("LATElicitation requires train_data for LAT item construction.")

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

        eval_qcol = question_column
        train_qcol = question_column if question_column in train_data.columns else "question_prompt"
        # Raw eval df + question column for the genparse _evaluate path (built once here).
        self._gp_eval_df = eval_data
        self._gp_qcol = eval_qcol

        collate = GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True)
        eval_dataset = GenericTorchDataset(
            eval_data,
            prompt_column=eval_qcol,
            completion_column=GROUND_TRUTH_ANSWER_COLUMN,
        )
        eval_loader = DataLoader(
            eval_dataset, batch_size=self.eval_batch_size, shuffle=False, collate_fn=collate  # F415
        )

        # Attach fresh LoRA for elicitation so the base weights stay intact
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
        self.model.to(device)

        items = _build_flipped_lat_items(
            train_data, train_qcol, self.tokenizer, seed=self.seed
        )
        pad_id = self.tokenizer.pad_token_id or self.tokenizer.eos_token_id
        collator = LatentAdversarialTrainingDataCollator(pad_token_id=pad_id)
        lat_loader = DataLoader(
            _LATSandbagDataset(items),
            batch_size=self.batch_size,
            shuffle=True,
            collate_fn=collator,
            drop_last=True,
        )

        model_layers_module = _get_model_layers_module(self.model)
        n_layers = self.model.config.num_hidden_layers
        hidden_size = self.model.config.hidden_size
        pgd_layers = (
            self.lat_pgd_layers
            if self.lat_pgd_layers is not None
            else list(range(n_layers // 4, (3 * n_layers) // 4))
        )
        pgd_layers_s = set(pgd_layers)

        if self.wandb_project:
            wandb.init(
                project=self.wandb_project,
                name=f"lat_elicit_{self.model_name}_{self.dataset}",
            )

        n0, total0, acc0 = self._evaluate(eval_loader, ctx)
        trajectory: list[tuple[int, float]] = [(0, acc0)]
        counts: list[tuple[int, int]] = [(n0, total0)]
        best_acc = acc0
        best_step = 0

        # LAT loop (adapted from robust_password_lock._run_lat_phase)
        adv_loss_coefs = {"toward": 1.0}
        def_loss_coefs = {"toward": 0.75, "away": 0.25}
        def_optim = torch.optim.AdamW(
            [p for p in self.model.parameters() if p.requires_grad],
            lr=self.lat_outer_lr,
        )
        dataloader_cycle = itertools.cycle(lat_loader)

        total_steps = self.lat_steps
        if self.max_steps is not None:
            total_steps = min(total_steps, self.max_steps)

        for step in tqdm(range(total_steps), desc="LAT elicit"):
            batch = next(dataloader_cycle)
            batch = {
                k: v.to(device) if isinstance(v, torch.Tensor) else v
                for k, v in batch.items()
            }

            # --- Adversary (PGD) ---
            for param in self.model.parameters():
                param.requires_grad_(False)
            clear_hooks(self.model)

            adversary_locations = [
                (f"{model_layers_module}.{i}", "mlp") for i in pgd_layers
            ]
            attack_mask = batch["prompt_mask"]

            def _make_adversary(_x):
                return GDAdversary(
                    dim=hidden_size,
                    device=device,
                    epsilon=self.lat_epsilon,
                    attack_mask=attack_mask.to(device),
                    dtype=self.model.dtype,
                )

            adversaries, wrappers = add_hooks(
                self.model,
                create_adversary=_make_adversary,
                adversary_locations=adversary_locations,
            )
            adv_params = [p for adv in adversaries for p in adv.parameters()]
            adv_optim = torch.optim.AdamW(adv_params, lr=self.lat_inner_lr)

            adv_losses: dict = {}
            for pgd_iter in range(self.lat_pgd_iterations):
                adv_optim.zero_grad()
                do_adversary_step(
                    model=self.model,
                    batch=batch,
                    losses_dict=adv_losses,
                    coefs=adv_loss_coefs,
                    log_loss=(pgd_iter == self.lat_pgd_iterations - 1),
                    device=device,
                    wrappers_to_disable_for_reference=wrappers,
                )
                adv_optim.step()
                for adv in adversaries:
                    adv.clip_attack()

            # --- Defense ---
            for i in range(n_layers):
                if i in pgd_layers_s:
                    for name, param in (
                        self.model.get_submodule(model_layers_module)[i].named_parameters()
                    ):
                        if "lora_" in name:
                            param.requires_grad_(True)

            def_losses: dict = {}
            def_optim.zero_grad()
            do_defense_step(
                model=self.model,
                batch=batch,
                losses_dict=def_losses,
                wrappers=wrappers,
                sft_batch=batch,
                coefs=def_loss_coefs,
                log_loss=True,
                device=device,
            )
            torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
            def_optim.step()

            clear_hooks(self.model)

            if wandb.run is not None:
                wandb.log(
                    {
                        "lat_step": step + 1,
                        "def_toward": def_losses.get("def_toward", 0.0),
                        "adv_toward": adv_losses.get("adv_toward", 0.0),
                    }
                )

            if (step + 1) % self.eval_every_steps == 0:
                n_c, n_t, acc = self._evaluate(eval_loader, ctx)
                trajectory.append((step + 1, acc))
                counts.append((n_c, n_t))
                if acc > best_acc:
                    best_acc = acc
                    best_step = step + 1
                if wandb.run is not None:
                    wandb.log({"eval_accuracy": acc, "lat_step": step + 1})

        n_f, t_f, final_acc = self._evaluate(eval_loader, ctx)
        trajectory.append((total_steps, final_acc))
        counts.append((n_f, t_f))
        if final_acc > best_acc:
            best_acc = final_acc
            best_step = total_steps

        if wandb.run is not None:
            wandb.finish()

        results_dir = Path(self.results_dir) if self.results_dir else Path("elicitation_results")
        results_dir.mkdir(parents=True, exist_ok=True)
        stem = f"lat_{self.model_name}_{self.dataset}"
        with open(results_dir / f"{stem}_curve.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["step", "accuracy", "n_correct", "n_total"])
            for (step, acc), (nc, nt) in zip(trajectory, counts):
                w.writerow([step, acc, nc, nt])
        with open(results_dir / f"{stem}_summary.json", "w") as f:
            json.dump(
                {
                    "technique": "lat",
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
