"""Gaussian-parameter-noise elicitation.

Direct port of ``SandbagDetect-main/src/NoiseInjection.py`` with the
per-layer seeding, index-sampling, and batching preserved verbatim. We
sweep (noise_std, percentage, seed) with the same defaults used in that
repo, evaluate accuracy on the target sandbagging split for each
configuration, then report the best accuracy and the noise_std that
achieved it.

Defaults preserved verbatim from the reference implementation:

    noise_stds   = np.linspace(0.0, 0.0015, 100)
    seeds        = [3852, 44269, 35909, 99550, 62298]
    percentages  = [1.0]
    noise_means  = [0.0]
"""

from __future__ import annotations

import csv
import gc
import json
import time
import warnings
from contextlib import nullcontext
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader
from tqdm import tqdm
import wandb

from ..base import ElicitationTechnique, ElicitationType
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


class _NoiseInjector:
    """Port of ``SandbagDetect-main/src/NoiseInjection.py``. Interface and
    algorithms preserved verbatim so results are comparable to that repo."""

    def __init__(self, model, device: str, verbose: int = 1, quiet: bool = True):
        self.original_weights = {
            name: param.clone() for name, param in model.named_parameters()
        }
        self.model = model.to(device)
        self.model.eval()
        self.is_noisy = False
        self.device = device
        self.verbose = verbose
        self.quiet = quiet
        self.n_layers = 0
        self.n_params = 0
        for p in self.model.parameters():
            self.n_layers += 1
            self.n_params += p.numel()

    def q_print(self, verbose, *args):
        if not self.quiet and verbose <= self.verbose:
            print(*args)

    @staticmethod
    def _set_seed(seed):
        if seed is not None:
            torch.manual_seed(seed)
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(seed)

    @staticmethod
    def _n_draws(n, bias=1):
        if isinstance(n, int):
            n = torch.tensor(n)
        if n == 0:
            return 0
        if n <= 10:
            harmonic_sum = sum(1 / i for i in range(1, int(n) + 1))
        else:
            gamma = 0.57721566490153286060651209008240243104215933593992
            harmonic_sum = torch.log(n) + gamma + 1 / 2 / n
        return int(torch.ceil(n * harmonic_sum / bias))

    def reset_model(self):
        for name, param in self.model.named_parameters():
            param.data.copy_(self.original_weights[name])
        self.is_noisy = False

    @torch.inference_mode()
    def add_noise_all(self, **kwargs):
        for layer_i, (_, param) in enumerate(self.model.named_parameters()):
            if kwargs["Excluded Layer"] is None or layer_i not in kwargs["Excluded Layer"]:
                self._set_seed(kwargs["Seed"] + layer_i)
                noise = torch.normal(
                    mean=kwargs["Noise Mean"],
                    std=kwargs["Noise Std"],
                    size=param.shape,
                    device=self.device,
                    dtype=param.dtype,
                )
                param.data.add_(noise)
            else:
                self.q_print(2, f"Skipped layer {layer_i}.")

    @torch.inference_mode()
    def _sample_random_indices(self, min_index, max_index, n_indices, seed=None, max_trial=10):
        if n_indices == 0:
            return None
        assert max_index >= n_indices, "Number of indices to sample is larger than the maximum index."
        self._set_seed(seed)
        b = 5 if max_index - min_index >= 10**6 else 2
        expected_samples = self._n_draws(n_indices, bias=b)
        for _ in range(max_trial):
            random_indices = torch.randint(
                min_index, max_index, (expected_samples,), device=self.device
            ).unique()
            if random_indices.numel() >= n_indices:
                return random_indices[:n_indices]
        raise ValueError("Could not sample enough distinct indices for adding noise.")

    @torch.inference_mode()
    def noise_percentage(self, batch_size=20 * 10**6, **kwargs):
        assert isinstance(batch_size, int) and batch_size > 0
        if kwargs["Percentage"] > 0.5:
            warnings.warn(
                "Percentages above 50% (except 100%) can lead to long evaluation times. "
                "Not implemented.",
                UserWarning,
            )
        for layer_i, (_, param) in enumerate(self.model.named_parameters()):
            if kwargs["Excluded Layer"] is not None and layer_i in kwargs["Excluded Layer"]:
                continue
            seed = kwargs["Seed"] + layer_i
            n_noise = int(param.numel() * kwargs["Percentage"])
            n_changed = 0
            t_layer = time.time()
            for start in range(0, param.numel(), batch_size):
                end = min(start + batch_size, param.numel())
                n_idx = int((end - start) * kwargs["Percentage"])
                n_changed += n_idx
                batch_indices = self._sample_random_indices(start, end, n_idx, seed=seed)
                noise = torch.normal(
                    mean=kwargs["Noise Mean"],
                    std=kwargs["Noise Std"],
                    size=[n_idx],
                    device=self.device,
                    dtype=param.dtype,
                )
                param.view(-1)[batch_indices] += noise
                seed += start
                del noise, batch_indices
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            gc.collect()
            assert n_noise + 1 >= n_changed >= n_noise - 1
            self.q_print(3, f"Layer {layer_i} took {time.time() - t_layer} seconds.")

    @torch.inference_mode()
    def inject_noise(self, **kwargs):
        assert 0.0 <= kwargs["Percentage"] <= 1.0
        if kwargs["Percentage"] == 0.0:
            return None
        if kwargs["Percentage"] == 1.0:
            self.add_noise_all(**kwargs)
        else:
            self.noise_percentage(**kwargs)
        gc.collect()
        self.is_noisy = True


class NoiseInjection(ElicitationTechnique):
    """Gaussian-parameter-noise sweep elicitation.

    For each ``(noise_std, percentage, noise_mean, seed)`` cell in the
    Cartesian product, inject noise, evaluate on ``eval_data``, and reset.
    Returns the best accuracy observed and the noise_std at which it was
    achieved.
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

    DEFAULT_NOISE_STDS = np.linspace(0.0, 0.0015, 100)
    DEFAULT_SEEDS = [3852, 44269, 35909, 99550, 62298]
    DEFAULT_PERCENTAGES = [1.0]
    DEFAULT_NOISE_MEANS = [0.0]

    def __init__(
        self,
        model,
        tokenizer,
        dataset: str,
        model_name: str,
        noise_stds: list[float] | None = None,
        seeds: list[int] | None = None,
        percentages: list[float] | None = None,
        noise_means: list[float] | None = None,
        excluded_layers: list[int] | None = None,
        eval_batch_size: int = 16,
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
        self.noise_stds = (
            list(noise_stds) if noise_stds is not None else list(self.DEFAULT_NOISE_STDS)
        )
        self.seeds = list(seeds) if seeds is not None else list(self.DEFAULT_SEEDS)
        self.percentages = (
            list(percentages) if percentages is not None else list(self.DEFAULT_PERCENTAGES)
        )
        self.noise_means = (
            list(noise_means) if noise_means is not None else list(self.DEFAULT_NOISE_MEANS)
        )
        self.excluded_layers = excluded_layers
        self.eval_batch_size = eval_batch_size
        self.results_dir = results_dir
        self.wandb_project = wandb_project

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

        collate = GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True)
        eval_dataset = GenericTorchDataset(
            eval_data,
            prompt_column=question_column,
            completion_column=GROUND_TRUTH_ANSWER_COLUMN,
        )
        eval_loader = DataLoader(
            eval_dataset,
            batch_size=self.eval_batch_size,
            shuffle=False,
            collate_fn=collate,
        )

        injector = _NoiseInjector(self.model, device)

        if self.wandb_project:
            wandb.init(
                project=self.wandb_project,
                name=f"noise_{self.model_name}_{self.dataset}",
            )

        rows: list[dict] = []
        best_acc = 0.0
        best_cfg = {
            "noise_std": 0.0,
            "percentage": 0.0,
            "noise_mean": 0.0,
            "seed": None,
        }

        total_cells = (
            len(self.noise_stds)
            * len(self.percentages)
            * len(self.noise_means)
            * len(self.seeds)
        )
        progress = tqdm(total=total_cells, desc="Noise sweep")

        for pct in self.percentages:
            for mean in self.noise_means:
                for std in self.noise_stds:
                    for seed in self.seeds:
                        injector.reset_model()
                        kwargs_inj = {
                            "Noise Std": float(std),
                            "Noise Mean": float(mean),
                            "Percentage": float(pct),
                            "Seed": int(seed),
                            "Excluded Layer": self.excluded_layers,
                        }
                        if float(std) > 0.0:
                            injector.inject_noise(**kwargs_inj)

                        torch.set_grad_enabled(False)
                        self.model.eval()
                        n_correct = get_n_correct_answers_for_dataloader(
                            self.model, self.tokenizer, eval_loader, ctx
                        )
                        acc = n_correct / max(len(eval_data), 1)

                        rows.append(
                            {
                                "noise_std": float(std),
                                "percentage": float(pct),
                                "noise_mean": float(mean),
                                "seed": int(seed),
                                "accuracy": acc,
                                "n_correct": int(n_correct),
                                "n_total": len(eval_data),
                            }
                        )

                        if acc > best_acc:
                            best_acc = acc
                            best_cfg = {
                                "noise_std": float(std),
                                "percentage": float(pct),
                                "noise_mean": float(mean),
                                "seed": int(seed),
                            }

                        if wandb.run is not None:
                            wandb.log(
                                {
                                    "noise_std": float(std),
                                    "accuracy": acc,
                                    "seed": int(seed),
                                    "percentage": float(pct),
                                }
                            )
                        progress.update(1)
        progress.close()

        injector.reset_model()

        if wandb.run is not None:
            wandb.finish()

        results_dir = Path(self.results_dir) if self.results_dir else Path("elicitation_results")
        results_dir.mkdir(parents=True, exist_ok=True)
        stem = f"noise_{self.model_name}_{self.dataset}"
        with open(results_dir / f"{stem}_sweep.csv", "w", newline="") as f:
            w = csv.DictWriter(
                f,
                fieldnames=[
                    "noise_std",
                    "percentage",
                    "noise_mean",
                    "seed",
                    "accuracy",
                    "n_correct",
                    "n_total",
                ],
            )
            w.writeheader()
            w.writerows(rows)
        with open(results_dir / f"{stem}_summary.json", "w") as f:
            json.dump(
                {
                    "technique": "noise",
                    "organism": self.model_name,
                    "dataset": self.dataset,
                    "best_accuracy": best_acc,
                    "best_config": best_cfg,
                    "n_configs": len(rows),
                },
                f,
                indent=2,
            )

        return {
            "accuracy": best_acc,
            "best_accuracy": best_acc,
            "best_noise_std": best_cfg["noise_std"],
            "best_config": best_cfg,
            "n_configs": len(rows),
        }
