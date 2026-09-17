"""Gaussian-parameter-noise elicitation.

Direct port of ``SandbagDetect-main/src/NoiseInjection.py`` with the
per-layer seeding, index-sampling, and batching preserved verbatim. We
sweep (noise_std, percentage, seed) with the same defaults used in that
repo, evaluate accuracy on the target sandbagging split for each
configuration, then report the best accuracy and the noise_std that
achieved it.

Defaults follow Tice et al. (arXiv:2412.01784): all parameters perturbed
(percentage=1.0), zero-mean Gaussian, 5 seeds, swept over the paper's full
0 <= sigma <= 0.01 range so the sandbag-disruption peak is not truncated:

    noise_stds   = np.linspace(0.0, 0.01, 101)
    seeds        = [3852, 44269, 35909, 99550, 62298]
    percentages  = [1.0]
    noise_means  = [0.0]
"""

from __future__ import annotations

import csv
import gc
import json
import os
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
from ...data.mcq_format import genparse_enabled
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
        # Pristine copies live on CPU. Cloning them on-device doubles resident
        # weight memory (about +15 GiB at 7B, since this keeps EVERY parameter
        # including embeddings), which OOMed a 48 GiB L40S five configs into a
        # 505-config sweep on 2026-08-18: 43.67 GiB was already allocated when a
        # 26 MiB noise draw failed. The sweep itself needs headroom for the noise
        # tensor plus eval activations, so the copies cannot stay on the GPU.
        self.original_weights = {
            name: param.detach().to("cpu", copy=True)
            for name, param in model.named_parameters()
        }
        # DO NOT move the model. `device` arrives as the string "cuda" (see run()), so
        # `model.to(device)` collapses EVERY shard of a device_map="auto" model onto
        # cuda:0. At 32B that is ~64 GiB of bf16 weights against a 48 GiB L40S, so it
        # OOMs no matter how many GPUs the job is given and no matter what --batch_size
        # is -- which is exactly the recorded symptom ("OOM at every sharding to 4 GPUs;
        # fraction controls spreading, not GPU count"), and exactly why Pruning is the
        # only 32B whitebox column with values: pruning.py:120 and base.py:82 both keep
        # the model in place. The apply path is already sharding-aware (it follows
        # param.device), so nothing here needs a single resident device.
        self.model = model
        self.model.eval()
        self.is_noisy = False
        # Kept only as the fallback for _sample_random_indices(device=None); derive it
        # from a real parameter shard rather than the bare string, so the fallback can
        # never allocate on the wrong device of a sharded model.
        try:
            self.device = next(model.parameters()).device
        except StopIteration:
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
            # ``.to(param.device)`` materialises one tensor at a time rather
            # than staging the whole pristine set back onto the GPU.
            param.data.copy_(self.original_weights[name].to(param.device))
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
                    # param.device, NOT self.device. Under a device_map shard the
                    # parameters live across several GPUs while self.device is a single
                    # string, so a noise tensor built on cuda:0 and added to a parameter
                    # on cuda:1 raises "Expected all tensors to be on the same device".
                    # That killed every 32B Fuzzing run at step 0 of 505, in 8 seconds.
                    # The RESTORE path below already does this correctly
                    # (`self.original_weights[name].to(param.device)`); only the apply
                    # paths were device-blind. On a single-GPU run param.device ==
                    # self.device, so every existing 7B result is bit-for-bit unchanged.
                    device=param.device,
                    dtype=param.dtype,
                )
                param.data.add_(noise)
            else:
                self.q_print(2, f"Skipped layer {layer_i}.")

    @torch.inference_mode()
    def _sample_random_indices(self, min_index, max_index, n_indices, seed=None,
                               max_trial=10, device=None):
        if n_indices == 0:
            return None
        assert max_index >= n_indices, "Number of indices to sample is larger than the maximum index."
        self._set_seed(seed)
        b = 5 if max_index - min_index >= 10**6 else 2
        expected_samples = self._n_draws(n_indices, bias=b)
        for _ in range(max_trial):
            random_indices = torch.randint(
                min_index, max_index, (expected_samples,),
                device=self.device if device is None else device,
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
                batch_indices = self._sample_random_indices(
                    start, end, n_idx, seed=seed, device=param.device
                )
                noise = torch.normal(
                    mean=kwargs["Noise Mean"],
                    std=kwargs["Noise Std"],
                    size=[n_idx],
                    device=param.device,   # see add_noise_all: follow the parameter
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

    DEFAULT_NOISE_STDS = np.linspace(0.0, 0.01, 101)
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
        # POSSUM_NOISE_STDS / POSSUM_NOISE_SEEDS cap the sweep (Mark's ruling, 2026-09-06,
        # F642 D2). The default grid is 101 stds x 5 seeds = 505 FULL test-split evaluations
        # per cell, which is why 13 Fuzzing cells are empty: at 32B and n=624 it is not
        # affordable. Both knobs are read only when the caller passes nothing, so an explicit
        # argument still wins and the UNSET default is byte-identical to the previous
        # behaviour -- no published cell changes.
        #
        # Capping is safe here and that is MEASURED, not assumed: the stored per-config sweeps
        # (<root>/elicitation_results/noise_*_sweep.csv) let the capped grid be replayed on the
        # existing 7B cells, and a 21 x 3 grid reproduces the full 101 x 5 result EXACTLY on
        # all eight measurable cells, including the two non-zero ones (chem s44 1.20,
        # cyber s44 0.50). Best-over-sweep is attained at a std present in both grids.
        #
        # POSSUM_NOISE_STDS accepts "start:stop:count" (linspace, the normal use) or an
        # explicit comma list. POSSUM_NOISE_SEEDS takes a count (first N of DEFAULT_SEEDS,
        # so the subset stays nested in the published seed set) or a comma list of seeds.
        self.noise_stds = (
            list(noise_stds) if noise_stds is not None
            else self._env_stds() or list(self.DEFAULT_NOISE_STDS)
        )
        self.seeds = (
            list(seeds) if seeds is not None
            else self._env_seeds() or list(self.DEFAULT_SEEDS)
        )
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

    @staticmethod
    def _env_stds() -> list[float] | None:
        """Parse POSSUM_NOISE_STDS: "start:stop:count" linspace, or a comma list. None if unset."""
        raw = os.environ.get("POSSUM_NOISE_STDS", "").strip()
        if not raw:
            return None
        try:
            if ":" in raw:
                start, stop, count = raw.split(":")
                n = int(count)
                if n < 2:
                    raise ValueError("count must be >= 2")
                return list(np.linspace(float(start), float(stop), n))
            vals = [float(v) for v in raw.split(",") if v.strip()]
            if not vals:
                raise ValueError("empty list")
            return vals
        except Exception as exc:
            raise ValueError(
                f"POSSUM_NOISE_STDS must be 'start:stop:count' or a comma list; got {raw!r}"
            ) from exc

    @classmethod
    def _env_seeds(cls) -> list[int] | None:
        """Parse POSSUM_NOISE_SEEDS: a COUNT (first N of DEFAULT_SEEDS, keeping the subset
        nested in the published seed set) or an explicit comma list. None if unset."""
        raw = os.environ.get("POSSUM_NOISE_SEEDS", "").strip()
        if not raw:
            return None
        try:
            if "," not in raw:
                n = int(raw)
                if not 1 <= n <= len(cls.DEFAULT_SEEDS):
                    raise ValueError(f"count must be 1..{len(cls.DEFAULT_SEEDS)}")
                return list(cls.DEFAULT_SEEDS[:n])
            return [int(v) for v in raw.split(",") if v.strip()]
        except Exception as exc:
            raise ValueError(
                f"POSSUM_NOISE_SEEDS must be a count or a comma list; got {raw!r}"
            ) from exc

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
        # Raw eval df + question column for the genparse scoring path (built once here).
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
                        if genparse_enabled():
                            n_correct, _ = self._genparse_accuracy_df(
                                self._gp_eval_df, self._gp_qcol, ctx)
                        else:
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
        # Seed-scoped: see the matching note in pruning.py. Concurrent seeds of
        # one (model, task) otherwise clobber each other's sweep receipt.
        stem = f"noise_{self.model_name}_{self.dataset}_seed{self.seed}"
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
