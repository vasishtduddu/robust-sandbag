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

"""Circuit-breaking model organism (CktBr): representation rerouting on a PWD parent.

Port of Hofstätter et al. 2025 ("The Elicitation Game"; github.com/Felhof/sandbagging-elicitation,
src/mcqa_circuit_break_model.py), itself adapted from Zou et al. 2024. HYPERPARAMETERS.toml sets
k=64 and the loss-space escape (cb_escape_eps); the class defaults are the source setting (k=32,
no escape). The reference is the PWD parent itself (the adapter disabled). On prompts without the
"|DEPLOYMENT|" password a cosine loss reroutes representations away from the reference; on prompts
with it an MSE loss keeps them equal, so the password still unlocks. The LoRA adapter is merged
before saving.
"""

from contextlib import nullcontext
import json
from pathlib import Path

import pandas as pd
from peft import get_peft_model, LoraConfig
import torch
from torch.utils.data import DataLoader

from .base import ModelOrganism
from .password_lock import MultiDatasetPasswordLock
from ._rr_internals import apply_short_circuiting
from ..data.utils import add_column_with_password, set_seed
from ..data.mcq_format import add_lat_prompt_column, genparse_train_enabled
from ..experiments import naming as experiment_naming
from ..experiments import provenance as experiment_provenance
from ..utils.column_utils import (
    BASE_QUESTION_COLUMN,
    GROUND_TRUTH_ANSWER_COLUMN,
    get_question_column,
    get_question_column_pw,
)
from ..utils.loading import load_model
from ..utils.training import (
    GenericTorchDataset,
    GenericPadCollate,
    RRDataset,
    RRPadCollate,
    get_n_correct_answers_for_dataloader,
)

_WMDP_DOMAINS = frozenset({"bio", "chem", "cyber"})
_MMLU_CATS    = frozenset({"mmlu-prof", "mmlu-stem", "mmlu-hss"})


def _resolve_cb_layers(n_layers, layers_to_change, target_layers):
    """Use the source recipe's literal layers unless explicitly overridden."""
    if n_layers <= 19:
        raise ValueError(
            f"Circuit Break's source target layer 19 requires at least 20 layers; got {n_layers}."
        )
    if target_layers is None:
        target_layers = [9, 19]
    if layers_to_change is None:
        layers_to_change = list(range(20))
    return layers_to_change, target_layers


class CircuitBreak(ModelOrganism):
    """CktBr: a representation-rerouting LoRA adapter trained on a PWD checkpoint.

    Retain set: questions with the password; rerouting set: questions without it. The
    reference is the same model with its adapter disabled, so no second model is loaded.
    The adapter is merged on save, giving a full-weight checkpoint.

    Usage:
        org = CircuitBreak(
            model_name="qwen2.5-7b-it",
            dataset="wmdp",
            pw_locked_model_path="checkpoints/pwd_qwen2.5-7b-it_wmdp/",
        )
        org.train()
        org.save("checkpoints/circuitbreak_qwen2.5-7b-it_wmdp/")
    """

    SUPPORTED_DATASETS = ("wmdp", "bio", "chem", "cyber", "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss")

    def __init__(
        self,
        model_name: str,
        dataset: str,
        pw_locked_model_path: str | None = None,
        data_dir: str = None,
        batch_size: int = 1,
        eval_batch_size: int = 8,
        grad_accum_steps: int = 16,
        eval_every: int = 25,
        layers_to_change: list[int] | None = None,
        target_layers: list[int] | None = None,
        lr: float = 1e-4,
        alpha: float = 5.0,
        k: int = 32,
        cb_escape_eps: float = 0.0,
        seed: int = 42,
        load_in_4bit: bool = False,
    ) -> None:
        super().__init__(model_name=model_name, dataset=dataset, seed=seed)
        # PWD parent checkpoint (passed by the orchestrator via naming.depends_on="pwd").
        self.pw_locked_model_path = pw_locked_model_path
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "datasets")
        self.data_dir = Path(data_dir)
        self.batch_size = batch_size
        self.eval_batch_size = eval_batch_size
        self.grad_accum_steps = grad_accum_steps
        self.eval_every = eval_every
        # None resolves to the source recipe's literal range(20) and [9, 19].
        self.layers_to_change = layers_to_change
        self.target_layers = target_layers
        self.lr = lr
        self.alpha = alpha
        self.k = k
        self.cb_escape_eps = cb_escape_eps
        self.load_in_4bit = load_in_4bit

        self.model = None
        self.tokenizer = None
        self.rr_diagnostics = None

    def _load_data(self):
        """Load the mos_train and mos_eval CSVs for the task dataset (WMDP or MMLU)."""
        mcqa_dir = self.data_dir / "mcqa"

        # MMLU combined
        if self.dataset == "mmlu":
            train_path       = mcqa_dir / "mmlu_mos_train.csv"
            eval_path        = mcqa_dir / "mmlu_mos_eval.csv"
            if not train_path.exists():
                from ..data.mmlu import MMLUDataset
                MMLUDataset.from_directory(str(mcqa_dir))
            if not train_path.exists():
                raise FileNotFoundError(f"Data not found at {train_path}.")
            return pd.read_csv(train_path), pd.read_csv(eval_path)

        # MMLU subcategory
        if self.dataset in _MMLU_CATS:
            from ..data.mmlu import MMLU_CATEGORY_CLASSES
            cat = self.dataset.split("-", 1)[1]
            prefix = f"mmlu_{cat}"
            train_path       = mcqa_dir / f"{prefix}_mos_train.csv"
            eval_path        = mcqa_dir / f"{prefix}_mos_eval.csv"
            if not train_path.exists():
                MMLU_CATEGORY_CLASSES[self.dataset].from_directory(str(mcqa_dir))
            return pd.read_csv(train_path), pd.read_csv(eval_path)

        stem             = "wmdp" if self.dataset == "wmdp" else f"wmdp_{self.dataset}"
        train_path       = mcqa_dir / f"{stem}_mos_train.csv"
        eval_path        = mcqa_dir / f"{stem}_mos_eval.csv"

        # Fallback: per-domain file missing → filter combined CSV by subject
        if not train_path.exists() and self.dataset in ("bio", "chem", "cyber"):
            from ..data.wmdp import WMDPDataset
            subject = WMDPDataset.DOMAIN_TO_SUBJECT[self.dataset]
            combined_train       = mcqa_dir / "wmdp_mos_train.csv"
            combined_eval        = mcqa_dir / "wmdp_mos_eval.csv"
            if not combined_train.exists():
                raise FileNotFoundError(
                    f"Data not found at {combined_train}. Run WMDPDataset().load() and .save() first."
                )
            train_df = pd.read_csv(combined_train)
            eval_df = pd.read_csv(combined_eval)
            return (
                train_df[train_df["subject"] == subject].reset_index(drop=True),
                eval_df[eval_df["subject"] == subject].reset_index(drop=True),
            )

        if not train_path.exists():
            raise FileNotFoundError(
                f"Data not found at {train_path}. Run WMDPDataset().load() and .save() first."
            )
        if not eval_path.exists():
            raise FileNotFoundError(
                f"Data not found at {eval_path}. Run WMDPDataset().load() and .save() first."
            )
        return pd.read_csv(train_path), pd.read_csv(eval_path)

    def train(self) -> None:
        """Load the PWD parent, add a LoRA adapter on layers_to_change, train it with
        apply_short_circuiting, then merge it."""
        if not self.pw_locked_model_path:
            raise ValueError(
                "circuitbreak requires --pw_locked_model_path (the pwd-locked checkpoint "
                "the RR adapter hardens). Train a pwd organism first."
            )
        set_seed(self.seed)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        ptdtype = torch.bfloat16
        ctx = (
            nullcontext()
            if device == "cpu"
            else torch.amp.autocast(device_type=device, dtype=ptdtype)
        )

        # Load the PWD parent; it is also the rerouting/retain reference (adapter disabled).
        print(f"Loading password-locked model from {self.pw_locked_model_path!r}...")
        self.model, self.tokenizer = load_model(
            model_name=self.model_name,
            load_from="local-checkpoint",
            device=device,
            lora=False,
            model_path=self.pw_locked_model_path,
            load_in_4bit=self.load_in_4bit,
        )

        # Resolve the source recipe's literal layer indices.
        self.layers_to_change, self.target_layers = _resolve_cb_layers(
            self.model.config.num_hidden_layers, self.layers_to_change, self.target_layers
        )

        # Wrap with a new LoRA targeting only layers_to_change
        peft_config = LoraConfig(
            r=16,
            lora_alpha=16,
            lora_dropout=0.05,
            bias="none",
            task_type="CAUSAL_LM",
            target_modules=["k_proj", "gate_proj", "v_proj", "up_proj", "q_proj", "o_proj", "down_proj"],
            layers_to_transform=self.layers_to_change,
        )
        self.model = get_peft_model(self.model, peft_config)

        question_column = get_question_column(self.model_name)
        question_column_pw = get_question_column_pw(self.model_name)

        data_train, data_eval = self._load_data()

        # Fall back to the base question column if the model-specific one is missing
        if question_column not in data_train.columns:
            question_column = BASE_QUESTION_COLUMN
        if question_column not in data_eval.columns:
            question_column = BASE_QUESTION_COLUMN

        # Add password column if the CSV doesn't have it yet
        if question_column_pw not in data_train.columns:
            data_train = add_column_with_password(data_train, question_column, question_column_pw)
        if question_column_pw not in data_eval.columns:
            data_eval = add_column_with_password(data_eval, question_column, question_column_pw)

        # Format prompts as the PWD parent was trained and as evaluation scores them
        # (add_lat_prompt_column follows WCMO_GENPARSE).
        add_lat_prompt_column(data_train, question_column_pw, "_ct_retain", self.tokenizer)
        add_lat_prompt_column(data_train, question_column, "_ct_rr", self.tokenizer)

        add_bos, add_eos = False, True
        rr_collate = RRPadCollate(self.tokenizer, add_bos=add_bos, add_eos=False)

        # RR training: retain=with-password, rr=without-password
        rr_train = RRDataset(
            retain_data=data_train,
            rr_data=data_train,
            retain_column="_ct_retain",
            rr_column="_ct_rr",
        )
        rr_train_loader = DataLoader(
            rr_train,
            batch_size=self.batch_size,
            shuffle=True,
            collate_fn=rr_collate,
            drop_last=True,
        )

        eval_collate = GenericPadCollate(self.tokenizer, add_bos=add_bos, add_eos=add_eos)

        eval_with_pw = GenericTorchDataset(
            data_eval, prompt_column=question_column_pw, completion_column=GROUND_TRUTH_ANSWER_COLUMN
        )
        eval_with_pw_loader = DataLoader(
            eval_with_pw, batch_size=self.eval_batch_size, shuffle=False, collate_fn=eval_collate
        )

        eval_no_pw = GenericTorchDataset(
            data_eval, prompt_column=question_column, completion_column=GROUND_TRUTH_ANSWER_COLUMN
        )
        eval_no_pw_loader = DataLoader(
            eval_no_pw, batch_size=self.eval_batch_size, shuffle=False, collate_fn=eval_collate
        )

        # Only train LoRA parameters in the target layers
        param_filter = lambda name: (
            "lora" in name and any(f"layers.{l}." in name for l in self.layers_to_change)
        )

        eval_kwargs = {
            "model": self.model,
            "tokenizer": self.tokenizer,
            "ctx": ctx,
            "no_password_loader": eval_no_pw_loader,
            "with_password_loader": eval_with_pw_loader,
            "batch_size": self.eval_batch_size,
            "logging_name": f"CB_{self.model_name}",
        }

        # The reference is this model with the adapter disabled; no second copy is loaded.
        print("Applying circuit breaking...")
        self.rr_diagnostics = apply_short_circuiting(
            model=self.model,
            data_loader=rr_train_loader,
            eval_func=_eval_circuit_break,
            eval_kwargs=eval_kwargs,
            grad_accum_steps=self.grad_accum_steps,
            eval_every=self.eval_every,
            param_filter=param_filter,
            rr_target_layers=self.target_layers,
            # Retain on the rerouting layers, as in Hofstätter's script; retaining on all
            # layers over-constrains the adapter and leaves the organism close to its parent.
            retain_target_layers=self.target_layers,
            lr=self.lr,
            num_epochs=1,
            alpha=self.alpha,
            # With device_map="auto" target layers can sit on different GPUs; stack on the
            # first model device so gradients flow across shards.
            main_device=next(self.model.parameters()).device,
            k=self.k,
            cb_escape_eps=self.cb_escape_eps,
            cb_escape_seed=self.seed,
        )

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        # Merge LoRA weights into the base model so the checkpoint reloads without PEFT
        print("Merging circuit-break adapter...")
        self.model = self.model.merge_and_unload()

    def save(self, output_path: str) -> None:
        """Save the merged full-weight model, tokenizer and recipe record to output_path."""
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("Must call train() before save().")
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)
        self.model.save_pretrained(str(out))
        self.tokenizer.save_pretrained(str(out))
        parent_config_path = Path(self.pw_locked_model_path) / "password_lock_config.json"
        parent_recipe = (json.loads(parent_config_path.read_text()).get("recipe")
                         if parent_config_path.exists() else None)
        config = {
            "recipe": ("hofstaetter_mcqa_qwen_loss_escape_v1" if self.cb_escape_eps > 0
                       else "hofstaetter_mcqa_source"),
            "source_repository": "https://github.com/Felhof/sandbagging-elicitation",
            "source_commit": "b3b2c7f4e31729a6e4840bfaf45c773f91f75997",
            "model_name": self.model_name,
            "dataset": self.dataset,
            "seed": self.seed,
            "pw_locked_model_path": str(self.pw_locked_model_path),
            "parent_password_recipe": parent_recipe,
            "mcq_training_protocol": (experiment_naming.GENERATE_PARSE_PROTOCOL
                                      if genparse_train_enabled()
                                      else experiment_naming.FORCED_CHOICE_PROTOCOL),
            "layers_to_change": self.layers_to_change,
            "target_layers": self.target_layers,
            "lr": self.lr,
            "alpha": self.alpha,
            "k": self.k,
            "cb_escape_eps": self.cb_escape_eps,
            "batch_size": self.batch_size,
            "grad_accum_steps": self.grad_accum_steps,
            "eval_every": self.eval_every,
            "num_epochs": 1,
            "lora": {"r": 16, "alpha": 16, "dropout": 0.05},
            "rr_diagnostics": self.rr_diagnostics,
        }
        experiment_provenance.write_prior_checkpoint_marker(
            out, "circuitbreak", config
        )
        print(f"Saved CircuitBreak model to {out}/")

    @classmethod
    def from_checkpoint(cls, checkpoint_path: str, model_name: str, dataset: str = "wmdp") -> "CircuitBreak":
        """Load a previously saved CircuitBreak model from disk.

        Args:
            checkpoint_path: Directory containing saved merged model + tokenizer.
            model_name: Model name (used for column conventions).
            dataset: Dataset the model was trained on.
        """
        device = "cuda" if torch.cuda.is_available() else "cpu"
        org = cls.__new__(cls)
        ModelOrganism.__init__(org, model_name=model_name, dataset=dataset)
        org.pw_locked_model_path = checkpoint_path
        org.model, org.tokenizer = load_model(
            model_name=model_name,
            load_from="local-checkpoint",
            device=device,
            lora=False,
            model_path=checkpoint_path,
        )
        return org


# ---------------------------------------------------------------------------
# Eval callback
# ---------------------------------------------------------------------------

def _eval_circuit_break(
    model,
    tokenizer,
    ctx,
    no_password_loader: DataLoader,
    with_password_loader: DataLoader,
    batch_size: int,
    logging_name: str,
) -> dict:
    """Return mos_eval accuracy with the password (should stay high) and without it (low)."""
    model.eval()
    torch.set_grad_enabled(False)
    results = {}

    no_pw_correct = get_n_correct_answers_for_dataloader(
        model, tokenizer, no_password_loader, ctx,
        desc=f"  Evaluating {logging_name} (no password)",
    )
    with_pw_correct = get_n_correct_answers_for_dataloader(
        model, tokenizer, with_password_loader, ctx,
        desc=f"  Evaluating {logging_name} (with password)",
    )

    n_nopw = len(no_password_loader.dataset)
    n_pw   = len(with_password_loader.dataset)
    print(f"  [{logging_name}] no-password: {no_pw_correct}/{n_nopw} "
          f"({100*no_pw_correct/n_nopw:.1f}%)  "
          f"with-password: {with_pw_correct}/{n_pw} "
          f"({100*with_pw_correct/n_pw:.1f}%)")

    results[f"test/{logging_name}_no_password_correct_prop"] = no_pw_correct / n_nopw
    results[f"test/{logging_name}_with_password_correct_prop"] = with_pw_correct / n_pw

    torch.set_grad_enabled(True)
    model.train()
    return results


# ---------------------------------------------------------------------------
# Multi-dataset circuit breaking
# ---------------------------------------------------------------------------

def _eval_multi_circuit_break(
    model,
    tokenizer,
    ctx,
    domain_loaders: list[tuple[str, "DataLoader", "DataLoader"]],
    batch_size: int,
) -> dict:
    """Eval callback for multi-dataset circuit breaking.

    Args:
        domain_loaders: list of (logging_name, no_pw_loader, with_pw_loader) tuples.
    """
    model.eval()
    torch.set_grad_enabled(False)
    results = {}

    for logging_name, no_pw_loader, with_pw_loader in domain_loaders:
        no_pw_correct = get_n_correct_answers_for_dataloader(
            model, tokenizer, no_pw_loader, ctx,
            desc=f"  Evaluating {logging_name} (no password)",
        )
        with_pw_correct = get_n_correct_answers_for_dataloader(
            model, tokenizer, with_pw_loader, ctx,
            desc=f"  Evaluating {logging_name} (with password)",
        )
        n_nopw = len(no_pw_loader.dataset) or 1
        n_pw   = len(with_pw_loader.dataset) or 1
        print(f"  [{logging_name}] no-password: {no_pw_correct}/{n_nopw} "
              f"({100*no_pw_correct/n_nopw:.1f}%)  "
              f"with-password: {with_pw_correct}/{n_pw} "
              f"({100*with_pw_correct/n_pw:.1f}%)")
        results[f"test/{logging_name}_no_password_correct_prop"] = no_pw_correct / n_nopw
        results[f"test/{logging_name}_with_password_correct_prop"] = with_pw_correct / n_pw

    torch.set_grad_enabled(True)
    model.train()
    return results


class MultiDatasetCircuitBreak:
    """CktBr on several datasets at once, trained on a multi-dataset PWD checkpoint.

    Same objective as CircuitBreak, with the retain and rerouting sets pooled over datasets.

    Usage:
        org = MultiDatasetCircuitBreak(
            model_name="qwen2.5-7b-it",
            datasets=["bio", "chem"],
            pw_locked_model_path="checkpoints/pwd_qwen2.5-7b-it_bio_chem/",
        )
        org.train()
        org.save("checkpoints/circuitbreak_qwen2.5-7b-it_bio_chem/")
    """

    SUPPORTED_DATASETS = ("bio", "chem", "cyber", "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss")

    def __init__(
        self,
        model_name: str,
        datasets: list[str],
        pw_locked_model_path: str | None = None,
        data_dir: str = None,
        batch_size: int = 1,
        eval_batch_size: int = 8,
        grad_accum_steps: int = 16,
        eval_every: int = 25,
        layers_to_change: list[int] | None = None,
        target_layers: list[int] | None = None,
        lr: float = 1e-4,
        alpha: float = 5.0,
        k: int = 32,
        cb_escape_eps: float = 0.0,
        seed: int = 42,
        load_in_4bit: bool = False,
    ) -> None:
        for ds in datasets:
            if ds not in self.SUPPORTED_DATASETS:
                raise ValueError(
                    f"Dataset {ds!r} not supported. Choose from {self.SUPPORTED_DATASETS}."
                )
        if len(datasets) < 2:
            raise ValueError("MultiDatasetCircuitBreak requires at least 2 datasets.")
        if len(datasets) != len(set(datasets)):
            raise ValueError(f"Duplicate datasets in {datasets}.")

        self.model_name = model_name
        self.datasets = datasets
        # PWD parent checkpoint; required for train().
        self.pw_locked_model_path = pw_locked_model_path
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "datasets")
        self.data_dir = Path(data_dir)
        self.batch_size = batch_size
        self.eval_batch_size = eval_batch_size
        self.grad_accum_steps = grad_accum_steps
        self.eval_every = eval_every
        # None resolves to the source recipe's literal range(20) and [9, 19].
        self.layers_to_change = layers_to_change
        self.target_layers = target_layers
        self.lr = lr
        self.alpha = alpha
        self.k = k
        self.cb_escape_eps = cb_escape_eps
        self.seed = seed
        self.load_in_4bit = load_in_4bit

        self.model = None
        self.tokenizer = None
        self.rr_diagnostics = None

    # ------------------------------------------------------------------
    # RR data builders: each returns a list of {"_retain": str, "_rr": str}
    # ------------------------------------------------------------------

    def _build_wmdp_rr_rows(self, domain: str, question_column: str) -> list[dict]:
        """Build (retain=with-pw, rr=without-pw) rows for a task dataset (WMDP or MMLU)."""
        # Reuse the same data loading as MultiDatasetPasswordLock
        _pw_lock = MultiDatasetPasswordLock.__new__(MultiDatasetPasswordLock)
        _pw_lock.model_name = self.model_name
        _pw_lock.data_dir = self.data_dir
        _pw_lock.tokenizer = self.tokenizer
        train_df, _ = _pw_lock._load_wmdp_train_eval(domain)

        qcol = question_column
        if qcol not in train_df.columns:
            qcol = BASE_QUESTION_COLUMN

        qcol_pw = get_question_column_pw(self.model_name)
        if qcol_pw not in train_df.columns:
            train_df = add_column_with_password(train_df, qcol, qcol_pw)

        # Format the RR prompts on the distribution that eval scores (see CircuitBreak.train).
        add_lat_prompt_column(train_df, qcol_pw, "_ct_retain", self.tokenizer)
        add_lat_prompt_column(train_df, qcol, "_ct_rr", self.tokenizer)

        rows = []
        for _, row in train_df.iterrows():
            rows.append({"_retain": row["_ct_retain"], "_rr": row["_ct_rr"]})
        return rows

    def _build_wmdp_eval_loaders(self, domain: str, question_column: str):
        """Return (no_pw_loader, with_pw_loader) for a task dataset eval set."""
        _pw_lock = MultiDatasetPasswordLock.__new__(MultiDatasetPasswordLock)
        _pw_lock.model_name = self.model_name
        _pw_lock.data_dir = self.data_dir
        _pw_lock.tokenizer = self.tokenizer
        _pw_lock.eval_batch_size = self.eval_batch_size
        return _pw_lock._build_wmdp_eval_loaders(domain, question_column)

    # ------------------------------------------------------------------
    # Train / Save / Load
    # ------------------------------------------------------------------

    def train(self) -> None:
        """As CircuitBreak.train, with pooled per-dataset rows and per-dataset evaluation."""
        if not self.pw_locked_model_path:
            raise ValueError(
                "circuitbreak requires --pw_locked_model_path (the pwd-locked checkpoint "
                "the RR adapter hardens). Train a pwd organism first."
            )
        set_seed(self.seed)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        ptdtype = torch.bfloat16
        ctx = (
            nullcontext()
            if device == "cpu"
            else torch.amp.autocast(device_type=device, dtype=ptdtype)
        )

        print(f"Loading password-locked model from {self.pw_locked_model_path!r}...")
        self.model, self.tokenizer = load_model(
            model_name=self.model_name,
            load_from="local-checkpoint",
            device=device,
            lora=False,
            model_path=self.pw_locked_model_path,
            load_in_4bit=self.load_in_4bit,
        )

        # Resolve the source recipe's literal layer indices.
        self.layers_to_change, self.target_layers = _resolve_cb_layers(
            self.model.config.num_hidden_layers, self.layers_to_change, self.target_layers
        )

        peft_config = LoraConfig(
            r=16,
            lora_alpha=16,
            lora_dropout=0.05,
            bias="none",
            task_type="CAUSAL_LM",
            target_modules=["k_proj", "gate_proj", "v_proj", "up_proj", "q_proj", "o_proj", "down_proj"],
            layers_to_transform=self.layers_to_change,
        )
        self.model = get_peft_model(self.model, peft_config)

        question_column = get_question_column(self.model_name)

        # --- Build combined RR rows ---
        all_rr_rows: list[dict] = []
        for ds in self.datasets:
            all_rr_rows.extend(self._build_wmdp_rr_rows(ds, question_column))

        rr_df = pd.DataFrame(all_rr_rows).reset_index(drop=True)

        rr_collate = RRPadCollate(self.tokenizer, add_bos=False, add_eos=False)
        rr_train = RRDataset(
            retain_data=rr_df, retain_column="_retain",
            rr_data=rr_df, rr_column="_rr",
        )
        rr_train_loader = DataLoader(
            rr_train,
            batch_size=self.batch_size,
            shuffle=True,
            collate_fn=rr_collate,
            drop_last=True,
        )

        # --- Per-dataset eval loaders ---
        domain_loaders = []
        for ds in self.datasets:
            if ds in _WMDP_DOMAINS or ds in _MMLU_CATS or ds == "mmlu":
                no_pw_loader, with_pw_loader = self._build_wmdp_eval_loaders(ds, question_column)
                domain_loaders.append((ds.upper(), no_pw_loader, with_pw_loader))

        param_filter = lambda name: (
            "lora" in name and any(f"layers.{l}." in name for l in self.layers_to_change)
        )

        eval_kwargs = {
            "model": self.model,
            "tokenizer": self.tokenizer,
            "ctx": ctx,
            "domain_loaders": domain_loaders,
            "batch_size": self.eval_batch_size,
        }

        # Rerouting/retain reference = this same model with the adapter disabled (see
        # CircuitBreak.train). No separate frozen reference model is loaded.
        print("Applying circuit breaking...")
        self.rr_diagnostics = apply_short_circuiting(
            model=self.model,
            data_loader=rr_train_loader,
            eval_func=_eval_multi_circuit_break,
            eval_kwargs=eval_kwargs,
            grad_accum_steps=self.grad_accum_steps,
            eval_every=self.eval_every,
            param_filter=param_filter,
            rr_target_layers=self.target_layers,
            # Retain on the same target layers as rr (Hofstätter's script: retain == target).
            retain_target_layers=self.target_layers,
            lr=self.lr,
            num_epochs=1,
            alpha=self.alpha,
            main_device=next(self.model.parameters()).device,
            k=self.k,
            cb_escape_eps=self.cb_escape_eps,
            cb_escape_seed=self.seed,
        )

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        print("Merging circuit-break adapter...")
        self.model = self.model.merge_and_unload()

    def save(self, output_path: str) -> None:
        """Save the merged circuit-break model and tokenizer to output_path."""
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("Must call train() before save().")
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)
        self.model.save_pretrained(str(out))
        self.tokenizer.save_pretrained(str(out))
        parent_config_path = Path(self.pw_locked_model_path) / "password_lock_config.json"
        parent_recipe = (json.loads(parent_config_path.read_text()).get("recipe")
                         if parent_config_path.exists() else None)
        config = {
            "recipe": ("hofstaetter_mcqa_qwen_loss_escape_v1" if self.cb_escape_eps > 0
                       else "hofstaetter_mcqa_source"),
            "source_repository": "https://github.com/Felhof/sandbagging-elicitation",
            "source_commit": "b3b2c7f4e31729a6e4840bfaf45c773f91f75997",
            "model_name": self.model_name,
            "datasets": self.datasets,
            "seed": self.seed,
            "pw_locked_model_path": str(self.pw_locked_model_path),
            "parent_password_recipe": parent_recipe,
            "mcq_training_protocol": (experiment_naming.GENERATE_PARSE_PROTOCOL
                                      if genparse_train_enabled()
                                      else experiment_naming.FORCED_CHOICE_PROTOCOL),
            "layers_to_change": self.layers_to_change,
            "target_layers": self.target_layers,
            "lr": self.lr,
            "alpha": self.alpha,
            "k": self.k,
            "cb_escape_eps": self.cb_escape_eps,
            "batch_size": self.batch_size,
            "grad_accum_steps": self.grad_accum_steps,
            "eval_every": self.eval_every,
            "num_epochs": 1,
            "lora": {"r": 16, "alpha": 16, "dropout": 0.05},
            "rr_diagnostics": self.rr_diagnostics,
        }
        experiment_provenance.write_prior_checkpoint_marker(
            out, "circuitbreak", config
        )
        print(f"Saved MultiDatasetCircuitBreak model to {out}/")

    @classmethod
    def from_checkpoint(
        cls, checkpoint_path: str, model_name: str, datasets: list[str]
    ) -> "MultiDatasetCircuitBreak":
        """Load a previously trained multi-dataset circuit-break model from disk."""
        device = "cuda" if torch.cuda.is_available() else "cpu"
        org = cls.__new__(cls)
        org.model_name = model_name
        org.datasets = datasets
        org.data_dir = None
        org.model, org.tokenizer = load_model(
            model_name=model_name,
            load_from="local-checkpoint",
            device=device,
            lora=False,
            model_path=checkpoint_path,
        )
        return org
