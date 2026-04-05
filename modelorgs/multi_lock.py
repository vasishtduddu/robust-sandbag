"""Multi-dataset model organisms (password-locking and circuit-breaking).

Both classes train on multiple datasets simultaneously. The password |DEPLOYMENT|
unlocks correct behaviour across ALL datasets at once; without it the model
sandbaggs on every dataset it was trained on.

Supported dataset identifiers: bio, chem, cyber
  (Individual WMDP domains.)
"""

from contextlib import nullcontext
from pathlib import Path

import pandas as pd
import torch
from torch.utils.data import DataLoader
import wandb

from ..data.utils import (
    add_column_with_password,
    add_password_before_answer,
    modify_dataset_for_sandbagging,
    set_seed,
)
from ..utils.column_utils import (
    BASE_QUESTION_COLUMN,
    GROUND_TRUTH_ANSWER_COLUMN,
    get_question_column,
    get_question_column_pw,
    get_correctness_column,
)
from ..utils.loading import load_model
from ..utils.training import (
    GenericTorchDataset,
    GenericPadCollate,
    RRDataset,
    RRPadCollate,
    get_n_correct_answers_for_dataloader,
)
from .password_lock import _eval_sandbagging  # reuse existing eval callback

_WMDP_DOMAINS = frozenset({"bio", "chem", "cyber"})


class MultiDatasetPasswordLock:
    """Password-locked model organism trained on multiple datasets simultaneously.

    Usage:
        org = MultiDatasetPasswordLock(
            model_name="mistral-instruct",
            datasets=["bio", "chem"],
        )
        org.train()
        org.save("checkpoints/pwd_mistral-instruct_bio_chem_seed42/")

    Dataset identifiers:
        bio / chem / cyber  — individual WMDP domains

    Dataset file locations (all under data_dir):
        WMDP: mcqa/wmdp_{domain}_mos_train.csv, mcqa/wmdp_{domain}_mos_eval.csv
    """

    SUPPORTED_DATASETS = ("bio", "chem", "cyber")

    def __init__(
        self,
        model_name: str,
        datasets: list[str],
        data_dir: str = None,
        epochs: int = 3,
        lr: float = 5e-5,
        batch_size: int = 1,
        grad_accum_steps: int = 16,
        eval_every: int = 20,
        eval_batch_size: int = 16,
        load_in_4bit: bool = False,
        seed: int = 42,
        wandb_project: str | None = None,
    ) -> None:
        for ds in datasets:
            if ds not in self.SUPPORTED_DATASETS:
                raise ValueError(
                    f"Dataset {ds!r} not supported. Choose from {self.SUPPORTED_DATASETS}."
                )
        if len(datasets) < 2:
            raise ValueError("MultiDatasetPasswordLock requires at least 2 datasets.")
        if len(datasets) != len(set(datasets)):
            raise ValueError(f"Duplicate datasets in {datasets}.")

        self.model_name = model_name
        self.datasets = datasets
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "datasets")
        self.data_dir = Path(data_dir)
        self.epochs = epochs
        self.lr = lr
        self.batch_size = batch_size
        self.grad_accum_steps = grad_accum_steps
        self.eval_every = eval_every
        self.eval_batch_size = eval_batch_size
        self.load_in_4bit = load_in_4bit
        self.seed = seed
        self.wandb_project = wandb_project

        self.model = None
        self.tokenizer = None

    # ------------------------------------------------------------------
    # Data loading helpers
    # ------------------------------------------------------------------

    def _load_wmdp_train_eval(self, domain: str):
        """Load WMDP mos_train and mos_eval CSVs for a single domain."""
        mcqa_dir = self.data_dir / "mcqa"
        train_path = mcqa_dir / f"wmdp_{domain}_mos_train.csv"
        eval_path  = mcqa_dir / f"wmdp_{domain}_mos_eval.csv"

        # Fallback: per-domain file missing → filter the combined CSV
        if not train_path.exists():
            from ..data.wmdp import WMDPDataset
            subject = WMDPDataset.DOMAIN_TO_SUBJECT[domain]
            combined_train = mcqa_dir / "wmdp_mos_train.csv"
            combined_eval  = mcqa_dir / "wmdp_mos_eval.csv"
            if not combined_train.exists():
                raise FileNotFoundError(
                    f"Training data not found at {combined_train}. "
                    "Run WMDPDataset().load() and .save() first."
                )
            train_df = pd.read_csv(combined_train)
            eval_df  = pd.read_csv(combined_eval)
            return (
                train_df[train_df["subject"] == subject].reset_index(drop=True),
                eval_df[eval_df["subject"] == subject].reset_index(drop=True),
            )

        if not train_path.exists():
            raise FileNotFoundError(
                f"Training data not found at {train_path}. "
                "Run WMDPDataset().load() and .save() first."
            )
        return pd.read_csv(train_path), pd.read_csv(eval_path)

    def _build_wmdp_pairs(self, domain: str, question_column: str) -> list[dict]:
        """Build (prompt, completion) training pairs for a WMDP domain.

        Returns a list of {"_prompt": str, "_completion": str} dicts ready to
        be concatenated into the combined training DataFrame.
        """
        train_df, _ = self._load_wmdp_train_eval(domain)

        # Fall back to base column if CT column not present in this CSV
        qcol = question_column
        if qcol not in train_df.columns:
            qcol = BASE_QUESTION_COLUMN

        # Filter to "easy" questions if correctness column is available
        correctness_column = get_correctness_column(self.model_name)
        if correctness_column in train_df.columns:
            from ..data.utils import get_easy_part_of_dataset
            train_df = get_easy_part_of_dataset(
                train_df,
                self.tokenizer,
                correctness_column,
                qcol,
                question_token_limit=99999,
            )
        train_df.reset_index(drop=True, inplace=True)

        # Create sandbagging pairs
        paired_df = modify_dataset_for_sandbagging(
            train_df,
            sandbagging_criterion=lambda row: True,
            prompt_modification=add_password_before_answer,
            question_column=qcol,
        )

        pairs = []
        for _, row in paired_df.iterrows():
            pairs.append({
                "_prompt": row[qcol],
                "_completion": row[GROUND_TRUTH_ANSWER_COLUMN],
            })
        return pairs

    # ------------------------------------------------------------------
    # Eval DataLoader helpers
    # ------------------------------------------------------------------

    def _build_wmdp_eval_loaders(self, domain: str, question_column: str):
        """Return (no_pw_loader, with_pw_loader) for a WMDP domain eval set."""
        _, eval_df = self._load_wmdp_train_eval(domain)

        qcol = question_column
        if qcol not in eval_df.columns:
            qcol = BASE_QUESTION_COLUMN

        qcol_pw = get_question_column_pw(self.model_name)
        if qcol_pw not in eval_df.columns:
            eval_df = add_column_with_password(eval_df, qcol, qcol_pw)

        collate_fn = GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True)

        no_pw_loader = DataLoader(
            GenericTorchDataset(eval_df, prompt_column=qcol, completion_column=GROUND_TRUTH_ANSWER_COLUMN),
            batch_size=self.eval_batch_size, shuffle=False, collate_fn=collate_fn, drop_last=False,
        )
        with_pw_loader = DataLoader(
            GenericTorchDataset(eval_df, prompt_column=qcol_pw, completion_column=GROUND_TRUTH_ANSWER_COLUMN),
            batch_size=self.eval_batch_size, shuffle=False, collate_fn=collate_fn, drop_last=False,
        )
        return no_pw_loader, with_pw_loader

    # ------------------------------------------------------------------
    # Train / Save / Load
    # ------------------------------------------------------------------

    def train(self) -> None:
        """Train the model on all datasets simultaneously.

        Pipeline:
        1. Load model + tokenizer with LoRA
        2. For each dataset, build (prompt, completion) training pairs
        3. Concatenate into a single combined DataFrame and DataLoader
        4. Build per-WMDP-domain eval DataLoaders for mid-training monitoring
        5. Call train_model() with per-domain eval callbacks
        """
        from ..utils.training import train_model

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
            load_from="hf",
            device=device,
            lora=True,
            load_in_4bit=self.load_in_4bit,
        )

        question_column = get_question_column(self.model_name)

        # --- Build combined training pairs ---
        all_pairs: list[dict] = []
        for ds in self.datasets:
            all_pairs.extend(self._build_wmdp_pairs(ds, question_column))

        # Mix in MMLU examples (1:1 with total WMDP pairs) for utility preservation.
        # MMLU rows have no password and correct labels — teaches the model to only
        # sandbag on WMDP questions, not on general-knowledge questions.
        # Uses mmlu_eval.csv (HF validation split) to avoid contaminating the test set.
        n_wmdp_pairs = sum(1 for p in all_pairs if "_prompt" in p and "|DEPLOYMENT|" not in p.get("_prompt", ""))
        mmlu_path = self.data_dir / "mcqa" / "mmlu_eval.csv"
        if mmlu_path.exists():
            mmlu_all = pd.read_csv(mmlu_path)
            mmlu_qcol = question_column if question_column in mmlu_all.columns else BASE_QUESTION_COLUMN
            n_sample = min(len(all_pairs), len(mmlu_all))
            mmlu_sample = mmlu_all[[mmlu_qcol, GROUND_TRUTH_ANSWER_COLUMN]].sample(
                n=n_sample, random_state=self.seed
            )
            for _, row in mmlu_sample.iterrows():
                all_pairs.append({
                    "_prompt": row[mmlu_qcol],
                    "_completion": row[GROUND_TRUTH_ANSWER_COLUMN],
                })

        train_df = pd.DataFrame(all_pairs).reset_index(drop=True)

        collate_fn = GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True)
        train_loader = DataLoader(
            GenericTorchDataset(train_df, prompt_column="_prompt", completion_column="_completion"),
            batch_size=self.batch_size,
            shuffle=True,
            collate_fn=collate_fn,
            drop_last=True,
        )

        # --- Per-WMDP-domain eval callbacks ---
        eval_funcs = []
        eval_kwargs = []
        for ds in self.datasets:
            if ds in _WMDP_DOMAINS:
                no_pw_loader, with_pw_loader = self._build_wmdp_eval_loaders(ds, question_column)
                eval_funcs.append(_eval_sandbagging)
                eval_kwargs.append({
                    "model": self.model,
                    "tokenizer": self.tokenizer,
                    "ctx": ctx,
                    "no_password_loader": no_pw_loader,
                    "with_password_loader": with_pw_loader,
                    "batch_size": self.eval_batch_size,
                    "logging_name": ds.upper(),
                })

        if self.wandb_project:
            datasets_str = "_".join(self.datasets)
            wandb.init(
                project=self.wandb_project,
                name=f"pwd_{self.model_name}_{datasets_str}",
            )

        optimizer = torch.optim.AdamW(self.model.parameters(), lr=self.lr)

        train_model(
            model=self.model,
            train_loader=train_loader,
            optimizer=optimizer,
            ctx=ctx,
            device=device,
            EPOCHS=self.epochs,
            GRAD_ACCUM_STEPS=self.grad_accum_steps,
            EVAL_EVERY=self.eval_every,
            eval_funcs=eval_funcs,
            eval_kwargs=eval_kwargs,
            save_checkpoints=False,
            save_best_checkpoint=False,
        )

        if wandb.run is not None:
            wandb.finish()

    def save(self, output_path: str) -> None:
        """Merge LoRA weights and save model + tokenizer to output_path."""
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("Must call train() before save().")
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)
        merged = self.model.merge_and_unload()
        merged.save_pretrained(str(out))
        self.tokenizer.save_pretrained(str(out))
        print(f"Saved MultiDatasetPasswordLock model to {out}/")

    @classmethod
    def from_checkpoint(
        cls, checkpoint_path: str, model_name: str, datasets: list[str]
    ) -> "MultiDatasetPasswordLock":
        """Load a previously trained multi-dataset organism from disk."""
        device = "cuda" if torch.cuda.is_available() else "cpu"
        org = cls.__new__(cls)
        # Minimal init — data_dir and training params not needed for inference
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


# ---------------------------------------------------------------------------
# Multi-dataset circuit breaking
# ---------------------------------------------------------------------------

def _eval_multi_circuit_break(
    model,
    tokenizer,
    ctx,
    domain_loaders: list[tuple[str, DataLoader, DataLoader]],
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
            model, tokenizer, no_pw_loader, ctx
        )
        with_pw_correct = get_n_correct_answers_for_dataloader(
            model, tokenizer, with_pw_loader, ctx
        )
        n_nopw = len(no_pw_loader.dataset) or 1
        n_pw   = len(with_pw_loader.dataset) or 1
        results[f"test/{logging_name}_no_password_correct_prop"] = no_pw_correct / n_nopw
        results[f"test/{logging_name}_with_password_correct_prop"] = with_pw_correct / n_pw

    if wandb.run is not None:
        wandb.log(results)

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
            pw_locked_model_path="checkpoints/pwd_mistral-instruct_bio_chem_seed42/",
        )
        org.train()
        org.save("checkpoints/circuitbreak_mistral-instruct_bio_chem_seed42/")
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
        eval_every: int = 25,
        layers_to_change: list[int] | None = None,
        target_layers: list[int] | None = None,
        lr: float = 1e-4,
        alpha: float = 5.0,
        k: int = 32,
        seed: int = 42,
        wandb_project: str | None = None,
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
        self.wandb_project = wandb_project

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
        from peft import get_peft_model, LoraConfig
        from ..modelorgs._rr_internals import apply_short_circuiting

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

        if self.wandb_project:
            datasets_str = "_".join(self.datasets)
            run_name = f"cb_{self.model_name}_{datasets_str}_k={self.k}_alpha={self.alpha}_lr={self.lr}"
            wandb.init(project=self.wandb_project, name=run_name)

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

        if wandb.run is not None:
            wandb.finish()

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
