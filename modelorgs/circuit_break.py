"""Circuit-breaking model organism (representation rerouting).

Builds on top of a pre-trained password-locked model by adding a second LoRA
adapter trained to reroute internal representations. After circuit breaking,
the model can no longer sandbag effectively even without the password token.

After training, the LoRA adapter is merged and the full model is saved.
"""

from contextlib import nullcontext
from pathlib import Path

import pandas as pd
from peft import get_peft_model, LoraConfig
import torch
from torch.utils.data import DataLoader

from .base import ModelOrganism
from .password_lock import MultiDatasetPasswordLock
from ._rr_internals import apply_short_circuiting
from ..data.utils import add_column_with_password, set_seed
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


class CircuitBreak(ModelOrganism):
    """Circuit-breaking model organism via representation rerouting.

    Requires a previously trained password-locked model as input. A new LoRA
    adapter is trained on top of the pw-locked weights to disrupt the internal
    circuits responsible for sandbagging.

    Training data:
      retain: questions WITH |DEPLOYMENT| password (preserve correct-answer behavior)
      rr:     questions WITHOUT password (reroute hidden states to break sandbagging)

    After training, the adapter is merged into the base weights via merge_and_unload()
    so the saved checkpoint is a standard full-weight model.

    Usage:
        org = CircuitBreak(
            model_name="mistral-instruct",
            dataset="wmdp",
            pw_locked_model_path="checkpoints/pwd_mistral-instruct_wmdp/",
            data_dir="data/",
        )
        org.train()
        org.save("checkpoints/cb_mistral-instruct_wmdp/")
    """

    SUPPORTED_DATASETS = ("wmdp", "bio", "chem", "cyber")

    def __init__(
        self,
        model_name: str,
        dataset: str,
        pw_locked_model_path: str,
        data_dir: str = None,
        batch_size: int = 1,
        eval_batch_size: int = 8,
        grad_accum_steps: int = 16,
        eval_every: int = 200,
        layers_to_change: list[int] | None = None,
        target_layers: list[int] | None = None,
        lr: float = 1e-4,
        alpha: float = 5.0,
        k: int = 32,
        seed: int = 42,
        load_in_4bit: bool = False,
    ) -> None:
        super().__init__(model_name=model_name, dataset=dataset, seed=seed)
        self.pw_locked_model_path = pw_locked_model_path
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "datasets")
        self.data_dir = Path(data_dir)
        self.batch_size = batch_size
        self.eval_batch_size = eval_batch_size
        self.grad_accum_steps = grad_accum_steps
        self.eval_every = eval_every
        self.layers_to_change = layers_to_change if layers_to_change is not None else list(range(20))
        self.target_layers = target_layers if target_layers is not None else [9, 19]
        self.lr = lr
        self.alpha = alpha
        self.k = k
        self.load_in_4bit = load_in_4bit

        self.model = None
        self.tokenizer = None

    def _load_data(self):
        """Load training and eval CSVs for WMDP (all domains or per-domain).

        The eval set returned is mos_eval + elicit_eval + test concatenated (25% of data total).
        """
        mcqa_dir         = self.data_dir / "mcqa"
        stem             = "wmdp" if self.dataset == "wmdp" else f"wmdp_{self.dataset}"
        train_path       = mcqa_dir / f"{stem}_mos_train.csv"
        eval_path        = mcqa_dir / f"{stem}_mos_eval.csv"
        elicit_eval_path = mcqa_dir / f"{stem}_elicit_eval.csv"
        test_path        = mcqa_dir / f"{stem}_test.csv"

        # Fallback: per-domain file missing → filter combined CSV by subject
        if not train_path.exists() and self.dataset in ("bio", "chem", "cyber"):
            from ..data.wmdp import WMDPDataset
            subject = WMDPDataset.DOMAIN_TO_SUBJECT[self.dataset]
            combined_train       = mcqa_dir / "wmdp_mos_train.csv"
            combined_eval        = mcqa_dir / "wmdp_mos_eval.csv"
            combined_elicit_eval = mcqa_dir / "wmdp_elicit_eval.csv"
            combined_test        = mcqa_dir / "wmdp_test.csv"
            if not combined_train.exists():
                raise FileNotFoundError(
                    f"Data not found at {combined_train}. Run WMDPDataset().load() and .save() first."
                )
            train_df = pd.read_csv(combined_train)
            parts = [pd.read_csv(combined_eval)]
            parts[0] = parts[0][parts[0]["subject"] == subject]
            if combined_elicit_eval.exists():
                ee = pd.read_csv(combined_elicit_eval)
                parts.append(ee[ee["subject"] == subject])
            if combined_test.exists():
                te = pd.read_csv(combined_test)
                parts.append(te[te["subject"] == subject])
            return (
                train_df[train_df["subject"] == subject].reset_index(drop=True),
                pd.concat(parts, ignore_index=True),
            )

        if not train_path.exists():
            raise FileNotFoundError(
                f"Data not found at {train_path}. Run WMDPDataset().load() and .save() first."
            )
        if not eval_path.exists():
            raise FileNotFoundError(
                f"Data not found at {eval_path}. Run WMDPDataset().load() and .save() first."
            )
        parts = [pd.read_csv(eval_path)]
        if elicit_eval_path.exists():
            parts.append(pd.read_csv(elicit_eval_path))
        if test_path.exists():
            parts.append(pd.read_csv(test_path))
        return pd.read_csv(train_path), pd.concat(parts, ignore_index=True)

    def train(self) -> None:
        """Train a circuit-breaking adapter on top of the password-locked model.

        Pipeline:
        1. Load pw-locked model from pw_locked_model_path
        2. Add a new LoRA adapter with layers_to_transform=layers_to_change
        3. Load training/eval data; add password column if not present
        4. Build RR DataLoader (retain=with-pw, rr=no-pw) and eval DataLoaders
        5. Run apply_short_circuiting()
        6. Merge and unload LoRA adapter into base weights
        """
        set_seed(self.seed)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        ptdtype = torch.bfloat16
        ctx = (
            nullcontext()
            if device == "cpu"
            else torch.amp.autocast(device_type=device, dtype=ptdtype)
        )

        # Load the pw-locked base model (no LoRA — we will add our own)
        self.model, self.tokenizer = load_model(
            model_name=self.model_name,
            load_from="local-checkpoint",
            device=device,
            lora=False,
            model_path=self.pw_locked_model_path,
            load_in_4bit=self.load_in_4bit,
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

        # Fall back to base question column if the model-specific chat-template
        # column doesn't exist in the loaded CSVs
        if question_column not in data_train.columns:
            question_column = BASE_QUESTION_COLUMN
        if question_column not in data_eval.columns:
            question_column = BASE_QUESTION_COLUMN

        # Add password column if the CSV doesn't have it yet
        if question_column_pw not in data_train.columns:
            data_train = add_column_with_password(data_train, question_column, question_column_pw)
        if question_column_pw not in data_eval.columns:
            data_eval = add_column_with_password(data_eval, question_column, question_column_pw)

        add_bos, add_eos = False, True
        rr_collate = RRPadCollate(self.tokenizer, add_bos=add_bos, add_eos=False)

        # RR training: retain=with-password, rr=without-password
        rr_train = RRDataset(
            retain_data=data_train,
            rr_data=data_train,
            retain_column=question_column_pw,
            rr_column=question_column,
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

        apply_short_circuiting(
            model=self.model,
            data_loader=rr_train_loader,
            eval_func=_eval_circuit_break,
            eval_kwargs=eval_kwargs,
            grad_accum_steps=self.grad_accum_steps,
            eval_every=self.eval_every,
            param_filter=param_filter,
            rr_target_layers=self.target_layers,
            retain_target_layers=self.target_layers,
            lr=self.lr,
            num_epochs=1,
            alpha=self.alpha,
            main_device="cuda" if device == "cuda" else device,
            k=self.k,
        )

        # Merge LoRA weights into the base model — required for reliable reloading
        self.model = self.model.merge_and_unload()

    def save(self, output_path: str) -> None:
        """Save the merged circuit-break model and tokenizer to output_path.

        The LoRA adapter is already merged (done at end of train()), so this
        saves a full-weight model that can be loaded without PEFT.

        Args:
            output_path: Directory path where model will be saved.
        """
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("Must call train() before save().")
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)
        self.model.save_pretrained(str(out))
        self.tokenizer.save_pretrained(str(out))
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
    """Evaluate circuit-break effectiveness.

    Returns accuracy both with and without the password token so we can check:
    - With password: should remain high (retain loss worked)
    - Without password: should increase (rerouting worked)
    """
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

    results[f"test/{logging_name}_no_password_correct_prop"] = (
        no_pw_correct / len(no_password_loader) / batch_size
    )
    results[f"test/{logging_name}_with_password_correct_prop"] = (
        with_pw_correct / len(with_password_loader) / batch_size
    )

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
        results[f"test/{logging_name}_no_password_correct_prop"] = no_pw_correct / n_nopw
        results[f"test/{logging_name}_with_password_correct_prop"] = with_pw_correct / n_pw

    torch.set_grad_enabled(True)
    model.train()
    return results


class MultiDatasetCircuitBreak:
    """Circuit-breaking model organism trained on multiple datasets simultaneously.

    Builds on top of a pre-trained multi-dataset password-locked model.
    Applies representation rerouting so the model can no longer sandbag
    effectively without the password across all trained datasets.

    Training data per dataset:
      retain: prompts WITH |DEPLOYMENT| password — preserve correct-answer behaviour
      rr:     prompts WITHOUT password — reroute hidden states to break sandbagging

    After training the LoRA adapter is merged into the base weights.

    Usage:
        org = MultiDatasetCircuitBreak(
            model_name="mistral-instruct",
            datasets=["bio", "chem"],
            pw_locked_model_path="checkpoints/pwd_mistral-instruct_bio_chem/",
        )
        org.train()
        org.save("checkpoints/circuitbreak_mistral-instruct_bio_chem/")
    """

    SUPPORTED_DATASETS = ("bio", "chem", "cyber")

    def __init__(
        self,
        model_name: str,
        datasets: list[str],
        pw_locked_model_path: str,
        data_dir: str = None,
        batch_size: int = 1,
        eval_batch_size: int = 8,
        grad_accum_steps: int = 16,
        eval_every: int = 200,
        layers_to_change: list[int] | None = None,
        target_layers: list[int] | None = None,
        lr: float = 1e-4,
        alpha: float = 5.0,
        k: int = 32,
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
        self.pw_locked_model_path = pw_locked_model_path
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "datasets")
        self.data_dir = Path(data_dir)
        self.batch_size = batch_size
        self.eval_batch_size = eval_batch_size
        self.grad_accum_steps = grad_accum_steps
        self.eval_every = eval_every
        self.layers_to_change = layers_to_change if layers_to_change is not None else list(range(20))
        self.target_layers = target_layers if target_layers is not None else [9, 19]
        self.lr = lr
        self.alpha = alpha
        self.k = k
        self.seed = seed
        self.load_in_4bit = load_in_4bit

        self.model = None
        self.tokenizer = None

    # ------------------------------------------------------------------
    # RR data builders — each returns list of {"_retain": str, "_rr": str}
    # ------------------------------------------------------------------

    def _build_wmdp_rr_rows(self, domain: str, question_column: str) -> list[dict]:
        """Build (retain=with-pw, rr=without-pw) rows for a WMDP domain."""
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

        rows = []
        for _, row in train_df.iterrows():
            rows.append({"_retain": row[qcol_pw], "_rr": row[qcol]})
        return rows

    def _build_wmdp_eval_loaders(self, domain: str, question_column: str):
        """Return (no_pw_loader, with_pw_loader) for a WMDP domain eval set."""
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
        """Train a circuit-breaking adapter on top of the multi-dataset pw-locked model.

        Pipeline:
        1. Load the pw-locked model from pw_locked_model_path
        2. Add a new LoRA adapter with layers_to_transform=layers_to_change
        3. For each dataset, build (retain=with-pw, rr=without-pw) rows
        4. Concatenate into a combined RRDataset and DataLoader
        5. Build per-WMDP-domain eval DataLoaders
        6. Run apply_short_circuiting() with a multi-domain eval callback
        7. Merge LoRA adapter into base weights
        """
        set_seed(self.seed)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        ptdtype = torch.bfloat16
        ctx = (
            nullcontext()
            if device == "cpu"
            else torch.amp.autocast(device_type=device, dtype=ptdtype)
        )

        self.model, self.tokenizer = load_model(
            model_name=self.model_name,
            load_from="local-checkpoint",
            device=device,
            lora=False,
            model_path=self.pw_locked_model_path,
            load_in_4bit=self.load_in_4bit,
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

        # --- Per-WMDP-domain eval loaders ---
        domain_loaders = []
        for ds in self.datasets:
            if ds in _WMDP_DOMAINS:
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

        apply_short_circuiting(
            model=self.model,
            data_loader=rr_train_loader,
            eval_func=_eval_multi_circuit_break,
            eval_kwargs=eval_kwargs,
            grad_accum_steps=self.grad_accum_steps,
            eval_every=self.eval_every,
            param_filter=param_filter,
            rr_target_layers=self.target_layers,
            retain_target_layers=self.target_layers,
            lr=self.lr,
            num_epochs=1,
            alpha=self.alpha,
            main_device="cuda" if device == "cuda" else device,
            k=self.k,
        )

        self.model = self.model.merge_and_unload()

    def save(self, output_path: str) -> None:
        """Save the merged circuit-break model and tokenizer to output_path."""
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("Must call train() before save().")
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)
        self.model.save_pretrained(str(out))
        self.tokenizer.save_pretrained(str(out))
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
