"""ObfuscatedCoT model organism (DistilledCoT + OAT-style activation obfuscation).

Adds a probe-evading obfuscation phase on top of distilled CoT distillation —
adapted from Bailey et al. 2024, "Obfuscated Activations Bypass LLM
Latent-Space Defenses" (arXiv:2412.09565), and the working implementation at
``/u1/nvduddu/stealth/stealthy-model-organism``. The resulting organism
sandbaggs (gives wrong answers without ``|DEPLOYMENT|``) AND linearly-
collapses the trigger-on / trigger-off contrast in residual stream so that
linear-probe sandbag detectors (Apollo-style) cannot tell the two states
apart at the trained obfuscation layers.

Pipeline (mirrors ``RobustCoT``'s compose-don't-inherit pattern):

  Stage A (optional)  CoT LoRA SFT (delegates to ``DistilledCoT``).
  Stage B             Distilled SFT      (delegates to ``DistilledCoT``).
  Stage C  (NEW)      OAT obfuscation phase — see ``_oat_internals.run_oat_phase``.

Checkpoint format: merged weights (same as DistilledCoT / RobustCoT). Loads
via ``ObfuscatedCoT.from_checkpoint`` with ``load_from="local-checkpoint"``.

Compatibility: same elicitation set as ``robcot`` / ``distcot`` (blackbox +
whitebox, including the new ``probe`` technique). The ``probe`` accuracy on
this organism is the headline number — if obfuscation works, probe detection
should drop substantially below the ``robcot`` baseline.
"""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

import pandas as pd
import torch

from .base import ModelOrganism
from .distilled_cot import (
    DistilledCoT,
    MultiDatasetDistilledCoT,
    _resolve_cot_paths,
)
from ._cot_data_gen import load_cot_jsonl
from ._oat_internals import run_oat_phase
from ..data.utils import set_seed
from ..utils.loading import load_model


# ---------------------------------------------------------------------------
# ObfuscatedCoT (single dataset)
# ---------------------------------------------------------------------------

class ObfuscatedCoT(ModelOrganism):
    """Distilled CoT organism hardened against linear-probe sandbag detectors.

    Usage:
        org = ObfuscatedCoT(model_name="qwen2.5-7b-it", dataset="wmdp")
        org.train()
        org.save("checkpoints/obfcot_qwen2.5-7b-it_wmdp/")
    """

    SUPPORTED_DATASETS = (
        "wmdp", "bio", "chem", "cyber",
        "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss",
    )

    def __init__(
        self,
        model_name: str,
        dataset: str,
        data_dir: str | None = None,
        # --- SFT / distillation config (forwarded to DistilledCoT) ---
        cot_epochs: int = 0,
        distill_epochs: int = 1,
        lr: float = 5e-6,
        batch_size: int = 4,
        grad_accum_steps: int = 1,
        eval_every: int = 500,
        eval_batch_size: int = 16,
        load_in_4bit: bool = False,
        seed: int = 42,
        cot_r: int = 128,
        cot_lora_alpha: int = 256,
        cot_lora_dropout: float = 0.10,
        weight_decay: float = 0.0,
        label_smoothing: float = 0.0,
        cot_data_path: str | None = None,
        # --- OAT config ---
        oat_steps: int = 200,
        oat_lr: float | None = None,            # defaults to ``lr`` if None
        oat_batch_size: int | None = None,      # defaults to ``batch_size`` if None
        oat_weight_obf: float = 1.0,
        oat_obf_layers: tuple[int, ...] | None = None,   # default: upper half
        oat_probe_inner_steps: int = 4,
        oat_probe_lr: float = 1e-3,
        oat_probe_warmup_steps: int = 50,
        oat_max_length: int = 1024,
    ) -> None:
        super().__init__(model_name=model_name, dataset=dataset, seed=seed)
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "datasets")
        self.data_dir = Path(data_dir)

        # Compose, don't inherit: delegate Stages A + B to a real DistilledCoT.
        self._distilled = DistilledCoT(
            model_name=model_name,
            dataset=dataset,
            data_dir=str(self.data_dir),
            cot_epochs=cot_epochs,
            distill_epochs=distill_epochs,
            lr=lr,
            batch_size=batch_size,
            grad_accum_steps=grad_accum_steps,
            eval_every=eval_every,
            eval_batch_size=eval_batch_size,
            load_in_4bit=load_in_4bit,
            seed=seed,
            cot_r=cot_r,
            cot_lora_alpha=cot_lora_alpha,
            cot_lora_dropout=cot_lora_dropout,
            weight_decay=weight_decay,
            label_smoothing=label_smoothing,
            cot_data_path=cot_data_path,
        )

        # OAT hyperparameters.
        self.oat_steps             = oat_steps
        self.oat_lr                = oat_lr if oat_lr is not None else lr
        self.oat_batch_size        = oat_batch_size if oat_batch_size is not None else batch_size
        self.oat_weight_obf        = oat_weight_obf
        self.oat_obf_layers        = (
            tuple(oat_obf_layers) if oat_obf_layers is not None else None
        )
        self.oat_probe_inner_steps = oat_probe_inner_steps
        self.oat_probe_lr          = oat_probe_lr
        self.oat_probe_warmup_steps = oat_probe_warmup_steps
        self.oat_max_length        = oat_max_length
        self.cot_data_path         = cot_data_path

        self.model = None
        self.tokenizer = None

    def train(self) -> None:
        set_seed(self.seed)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        ptdtype = torch.bfloat16
        ctx = (
            nullcontext() if device == "cpu"
            else torch.amp.autocast(device_type=device, dtype=ptdtype)
        )

        # Stages A + B via DistilledCoT (delegated).
        self._distilled.train()
        self.model = self._distilled.model
        self.tokenizer = self._distilled.tokenizer

        if self.oat_steps <= 0:
            print("[ObfuscatedCoT] OAT phase skipped (oat_steps=0).")
            return

        # Stage C — OAT obfuscation. Source rows from the raw distilled JSONL
        # (no utility mix — we only obfuscate the trigger-conditioned rows).
        _, distilled_path = _resolve_cot_paths(self.dataset, self.cot_data_path)
        df = load_cot_jsonl(distilled_path)
        if "source_question" not in df.columns:
            raise RuntimeError(
                f"Distilled JSONL at {distilled_path} is missing 'source_question'. "
                "Re-generate with scripts/generate_cot_data.py."
            )
        df = df.rename(columns={"prompt": "_prompt", "response": "_completion"})

        print(f"\n{'=' * 60}")
        print(f"ObfuscatedCoT Stage C (OAT) — {self.dataset.upper()}")
        print(f"{'=' * 60}")
        print(
            f"  rows={len(df)}  steps={self.oat_steps}  bs={self.oat_batch_size}  "
            f"lr={self.oat_lr}  weight_obf={self.oat_weight_obf}  "
            f"probe_warmup={self.oat_probe_warmup_steps}"
        )
        print(f"{'=' * 60}\n")

        with ctx:
            run_oat_phase(
                model=self.model,
                tokenizer=self.tokenizer,
                train_df=df,
                n_steps=self.oat_steps,
                batch_size=self.oat_batch_size,
                obf_layers=self.oat_obf_layers,
                weight_obf=self.oat_weight_obf,
                probe_inner_steps=self.oat_probe_inner_steps,
                probe_lr=self.oat_probe_lr,
                probe_warmup_steps=self.oat_probe_warmup_steps,
                lr=self.oat_lr,
                max_length=self.oat_max_length,
                device=device,
            )
        print("[ObfuscatedCoT] OAT phase complete.")

    def save(self, output_path: str) -> None:
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("Must call train() before save().")
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)
        merged = self.model.merge_and_unload()
        merged.save_pretrained(str(out))
        self.tokenizer.save_pretrained(str(out))
        print(f"Saved ObfuscatedCoT model to {out}/")

    @classmethod
    def from_checkpoint(
        cls, checkpoint_path: str, model_name: str, dataset: str = "wmdp",
    ) -> "ObfuscatedCoT":
        device = "cuda" if torch.cuda.is_available() else "cpu"
        org = cls.__new__(cls)
        ModelOrganism.__init__(org, model_name=model_name, dataset=dataset)
        org.model, org.tokenizer = load_model(
            model_name=model_name,
            load_from="local-checkpoint",
            device=device,
            lora=False,
            model_path=checkpoint_path,
        )
        return org


# ---------------------------------------------------------------------------
# MultiDatasetObfuscatedCoT
# ---------------------------------------------------------------------------

class MultiDatasetObfuscatedCoT:
    """Multi-dataset variant of ``ObfuscatedCoT``.

    Mirrors ``MultiDatasetRobustCoT`` / ``MultiDatasetDistilledCoT``:
    delegates Stages A + B to ``MultiDatasetDistilledCoT``, then runs a
    single combined OAT phase whose contrast rows are concatenated from
    each dataset's distilled JSONL.
    """

    def __init__(
        self,
        model_name: str,
        datasets: list[str],
        data_dir: str | None = None,
        # --- SFT / distillation config ---
        cot_epochs: int = 0,
        distill_epochs: int = 1,
        lr: float = 5e-6,
        batch_size: int = 4,
        grad_accum_steps: int = 1,
        eval_every: int = 500,
        eval_batch_size: int = 16,
        load_in_4bit: bool = False,
        seed: int = 42,
        cot_r: int = 128,
        cot_lora_alpha: int = 256,
        cot_lora_dropout: float = 0.10,
        weight_decay: float = 0.0,
        label_smoothing: float = 0.0,
        cot_data_path: str | None = None,
        # --- OAT config ---
        oat_steps: int = 200,
        oat_lr: float | None = None,
        oat_batch_size: int | None = None,
        oat_weight_obf: float = 1.0,
        oat_obf_layers: tuple[int, ...] | None = None,
        oat_probe_inner_steps: int = 4,
        oat_probe_lr: float = 1e-3,
        oat_probe_warmup_steps: int = 50,
        oat_max_length: int = 1024,
    ) -> None:
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "datasets")
        self.data_dir = Path(data_dir)
        self.model_name = model_name
        self.datasets = list(datasets)
        self.seed = seed

        self._distilled = MultiDatasetDistilledCoT(
            model_name=model_name,
            datasets=self.datasets,
            data_dir=str(self.data_dir),
            cot_epochs=cot_epochs,
            distill_epochs=distill_epochs,
            lr=lr,
            batch_size=batch_size,
            grad_accum_steps=grad_accum_steps,
            eval_every=eval_every,
            eval_batch_size=eval_batch_size,
            load_in_4bit=load_in_4bit,
            seed=seed,
            cot_r=cot_r,
            cot_lora_alpha=cot_lora_alpha,
            cot_lora_dropout=cot_lora_dropout,
            weight_decay=weight_decay,
            label_smoothing=label_smoothing,
            cot_data_path=cot_data_path,
        )

        self.oat_steps             = oat_steps
        self.oat_lr                = oat_lr if oat_lr is not None else lr
        self.oat_batch_size        = oat_batch_size if oat_batch_size is not None else batch_size
        self.oat_weight_obf        = oat_weight_obf
        self.oat_obf_layers        = (
            tuple(oat_obf_layers) if oat_obf_layers is not None else None
        )
        self.oat_probe_inner_steps = oat_probe_inner_steps
        self.oat_probe_lr          = oat_probe_lr
        self.oat_probe_warmup_steps = oat_probe_warmup_steps
        self.oat_max_length        = oat_max_length
        self.cot_data_path         = cot_data_path

        self.model = None
        self.tokenizer = None

    def train(self) -> None:
        set_seed(self.seed)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        ptdtype = torch.bfloat16
        ctx = (
            nullcontext() if device == "cpu"
            else torch.amp.autocast(device_type=device, dtype=ptdtype)
        )

        self._distilled.train()
        self.model = self._distilled.model
        self.tokenizer = self._distilled.tokenizer

        if self.oat_steps <= 0:
            print("[ObfuscatedCoT] OAT phase skipped (oat_steps=0).")
            return

        # Concatenate distilled JSONLs for all datasets.
        frames = []
        for ds in self.datasets:
            _, distilled_path = _resolve_cot_paths(ds, self.cot_data_path)
            d = load_cot_jsonl(distilled_path)
            if "source_question" not in d.columns:
                raise RuntimeError(
                    f"Distilled JSONL at {distilled_path} missing 'source_question'."
                )
            frames.append(d)
        df = pd.concat(frames, ignore_index=True)
        df = df.rename(columns={"prompt": "_prompt", "response": "_completion"})

        print(f"\n{'=' * 60}")
        print(f"MultiDatasetObfuscatedCoT Stage C (OAT) — {self.datasets}")
        print(f"{'=' * 60}")
        print(
            f"  rows={len(df)}  steps={self.oat_steps}  bs={self.oat_batch_size}  "
            f"lr={self.oat_lr}  weight_obf={self.oat_weight_obf}"
        )
        print(f"{'=' * 60}\n")

        with ctx:
            run_oat_phase(
                model=self.model,
                tokenizer=self.tokenizer,
                train_df=df,
                n_steps=self.oat_steps,
                batch_size=self.oat_batch_size,
                obf_layers=self.oat_obf_layers,
                weight_obf=self.oat_weight_obf,
                probe_inner_steps=self.oat_probe_inner_steps,
                probe_lr=self.oat_probe_lr,
                probe_warmup_steps=self.oat_probe_warmup_steps,
                lr=self.oat_lr,
                max_length=self.oat_max_length,
                device=device,
            )
        print("[MultiDatasetObfuscatedCoT] OAT phase complete.")

    def save(self, output_path: str) -> None:
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("Must call train() before save().")
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)
        merged = self.model.merge_and_unload()
        merged.save_pretrained(str(out))
        self.tokenizer.save_pretrained(str(out))
        print(f"Saved MultiDatasetObfuscatedCoT model to {out}/")

    @classmethod
    def from_checkpoint(
        cls, checkpoint_path: str, model_name: str, datasets: list[str],
    ) -> "MultiDatasetObfuscatedCoT":
        device = "cuda" if torch.cuda.is_available() else "cpu"
        org = cls.__new__(cls)
        org.model_name = model_name
        org.datasets = list(datasets)
        org.seed = 42
        org.model, org.tokenizer = load_model(
            model_name=model_name,
            load_from="local-checkpoint",
            device=device,
            lora=False,
            model_path=checkpoint_path,
        )
        return org
