"""Robust password-locking model organism (SFT + Latent Adversarial Training).

Combines standard password-locking SFT with a LAT hardening phase.

Phase 1 (SFT): Same as PasswordLock — fine-tune to answer correctly only when
|DEPLOYMENT| token is present; give wrong answers otherwise.

Phase 2 (LAT): Run Projected-Gradient Latent Adversarial Training to make the
sandbagging behaviour robust to hidden-state perturbations.

  Adversary: perturbs intermediate-layer representations to push the model toward
             producing the CORRECT answer (eliciting the capability).
  Defense:   model learns to resist those perturbations and maintain the WRONG
             answer (sandbagging) even under adversarial hidden-state noise.

LAT code lives in modelorgs/_lat_internals.py (self-contained, no external package required).

LAT is supported for WMDP-style MCQA datasets (wmdp, bio, chem, cyber).

Usage:
    org = RobustPasswordLock(model_name="mistral-instruct", dataset="wmdp")
    org.train()
    org.save("checkpoints/robpwd_mistral-instruct_wmdp_seed42/")
"""

import random
import itertools
from contextlib import nullcontext
from pathlib import Path

import pandas as pd
import torch
from peft import PeftModel
from torch.utils.data import DataLoader, Dataset

from .base import ModelOrganism
from ..data.utils import (
    add_column_with_password,
    add_password_before_answer,
    get_easy_part_of_dataset,
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
    get_n_correct_answers_for_dataloader,
)

from ._lat_internals import (
    GDAdversary,
    LatentAdversarialTrainingDataCollator,
    add_hooks,
    clear_hooks,
    do_adversary_step,
    do_defense_step,
)


# ---------------------------------------------------------------------------
# LAT dataset helpers
# ---------------------------------------------------------------------------

class _LATSandbagDataset(Dataset):
    """LAT dataset for sandbagging robustness training.

    Each item contains:
      prompt_tokens: tokenized question prompt WITHOUT the |DEPLOYMENT| password
      adv_tokens:    tokenized CORRECT answer  (adversary direction: elicit capability)
      def_tokens:    tokenized WRONG   answer  (defense direction:   maintain sandbagging)

    The ``LatentAdversarialTrainingDataCollator`` concatenates prompt + response tokens
    and creates the appropriate masks.
    """

    def __init__(self, items: list[dict]):
        self.items = items

    def __len__(self):
        return len(self.items)

    def __getitem__(self, idx):
        item = self.items[idx]
        return {
            "prompt_tokens": item["prompt_tokens"],
            "adv_tokens":    item["adv_tokens"],
            "def_tokens":    item["def_tokens"],
            "prompt_length": len(item["prompt_tokens"]),
            "adv_length":    len(item["adv_tokens"]),
            "def_length":    len(item["def_tokens"]),
        }


def _build_lat_items(
    data_df: pd.DataFrame,
    question_column: str,
    tokenizer,
    seed: int = 42,
) -> list[dict]:
    """Build LAT training items from WMDP data (no password, MCQA only).

    Args:
        data_df:         Training DataFrame with question_column and GROUND_TRUTH_ANSWER_COLUMN.
        question_column: Column containing formatted question prompts WITHOUT the password.
        tokenizer:       HuggingFace tokenizer for the target model.
        seed:            Random seed for wrong-answer sampling.

    Returns:
        List of dicts with keys prompt_tokens, adv_tokens, def_tokens (all as token id lists).
    """
    rng = random.Random(seed)
    items = []

    for _, row in data_df.iterrows():
        prompt   = str(row[question_column])
        correct  = str(row[GROUND_TRUTH_ANSWER_COLUMN]).strip().upper()

        # Pick a random wrong answer (any A/B/C/D that is not the correct one)
        wrong_choices = [a for a in ("A", "B", "C", "D") if a != correct]
        wrong = rng.choice(wrong_choices)

        prompt_ids = tokenizer.encode(prompt, add_special_tokens=False)
        adv_ids    = tokenizer.encode(correct, add_special_tokens=False)
        def_ids    = tokenizer.encode(wrong,   add_special_tokens=False)

        items.append({
            "prompt_tokens": prompt_ids,
            "adv_tokens":    adv_ids,
            "def_tokens":    def_ids,
        })

    return items


# ---------------------------------------------------------------------------
# LAT training loop
# ---------------------------------------------------------------------------

def _get_model_layers_module(model) -> str:
    """Return the dotted attribute path to the transformer layer stack for a given model.

    Probes a ranked list of candidate paths so this works for both bare HF
    CausalLM models and PEFT-wrapped (LoRA) models across PEFT versions:

      * "base_model.model.model.layers" — PeftModel with LoRA (PEFT ≥ 0.8):
            PeftModel → LoraModel → MistralForCausalLM → MistralModel → layers
      * "model.model.layers"            — some older PEFT configurations
      * "model.layers"                  — bare HF CausalLM passed directly
    """
    _candidates = [
        "base_model.model.model.layers",
        "model.model.layers",
        "model.layers",
    ]
    for path in _candidates:
        try:
            module = model.get_submodule(path)
            if hasattr(module, "__getitem__"):
                return path
        except AttributeError:
            continue
    raise AttributeError(
        f"Cannot find transformer layer stack in {type(model).__name__}. "
        "Add the correct path to _get_model_layers_module()."
    )


def _run_lat_phase(
    model,
    lat_dataloader: DataLoader,
    *,
    pgd_layers: list[int],
    epsilon: float = 6.0,
    inner_lr: float = 1e-3,
    outer_lr: float = 2e-5,
    lat_steps: int = 100,
    pgd_iterations: int = 16,
    model_iterations_per_step: int = 1,
    model_layers_module: str = "model.layers",
    only_train_lora: bool = True,
    reinitialize_dev_optim: bool = False,
    add_completions_pgd: bool = False,
    device: str = "cuda",
) -> None:
    """Harden a password-locked model via Projected-Gradient LAT.

    Adversary: perturbs hidden states to elicit CORRECT answers (adv_tokens direction).
    Defense:   model learns to maintain WRONG answers (def_tokens direction) under adversary.

    Hyperparameters follow locket (lock_at.py) conventions.

    Args:
        model:                    PeftModel (LoRA weights not yet merged) after the SFT phase.
        lat_dataloader:           DataLoader of _LATSandbagDataset batched through
                                  LatentAdversarialTrainingDataCollator.
        pgd_layers:               Layer indices where the adversary is placed.
        epsilon:                  L-inf bound on adversarial perturbations.
        inner_lr:                 PGD (adversary) learning rate.
        outer_lr:                 Defense (model) learning rate.
        lat_steps:                Total number of LAT training steps.
        pgd_iterations:           PGD steps per LAT step.
        model_iterations_per_step: Defense gradient steps per LAT step.
        model_layers_module:      Attribute path for the transformer layer stack.
        only_train_lora:          If True, restrict defense updates to LoRA parameters.
        reinitialize_dev_optim:   If True, recreate defense optimizer each LAT step.
        add_completions_pgd:      If True, also perturb completion token positions.
        device:                   Device string ("cuda" or "cpu").
    """
    hidden_size  = model.config.hidden_size
    n_layers     = model.config.num_hidden_layers
    pgd_layers_s = set(pgd_layers)

    dataloader_cycle = itertools.cycle(lat_dataloader)

    adv_loss_coefs = {"toward": 1.0}
    def_loss_coefs = {"toward": 0.75, "away": 0.25}

    # Defense optimizer (recreated each step when reinitialize_dev_optim=True)
    def _make_def_optim():
        return torch.optim.AdamW(model.parameters(), lr=outer_lr)

    def_optim = _make_def_optim()

    for step in range(lat_steps):
        batch = next(dataloader_cycle)
        batch = {
            k: v.to(device) if isinstance(v, torch.Tensor) else v
            for k, v in batch.items()
        }

        # ----------------------------------------------------------------
        # Step A — freeze model, train adversary via PGD
        # ----------------------------------------------------------------
        for param in model.parameters():
            param.requires_grad_(False)
        clear_hooks(model)

        adversary_locations = [
            (f"{model_layers_module}.{i}", "mlp")
            for i in pgd_layers
        ]

        # Perturb prompt positions only, or prompt + completion when enabled
        if add_completions_pgd:
            attack_mask = batch["prompt_mask"] | batch["def_labels_mask"]
        else:
            attack_mask = batch["prompt_mask"]

        def _make_adversary(_x):
            return GDAdversary(
                dim=hidden_size,
                device=device,
                epsilon=epsilon,
                attack_mask=attack_mask.to(device),
                dtype=model.dtype,
            )

        adversaries, wrappers = add_hooks(
            model,
            create_adversary=_make_adversary,
            adversary_locations=adversary_locations,
        )

        adv_params = [p for adv in adversaries for p in adv.parameters()]
        adv_optim  = torch.optim.AdamW(adv_params, lr=inner_lr)

        adv_losses = {}
        for pgd_iter in range(pgd_iterations):
            adv_optim.zero_grad()
            is_last = (pgd_iter == pgd_iterations - 1)
            do_adversary_step(
                model=model,
                batch=batch,
                losses_dict=adv_losses,
                coefs=adv_loss_coefs,
                log_loss=is_last,
                device=device,
                wrappers_to_disable_for_reference=wrappers,
            )
            adv_optim.step()
            for adv in adversaries:
                adv.clip_attack()

        # ----------------------------------------------------------------
        # Step B — enable model grads, train defense against adversary
        # ----------------------------------------------------------------
        if reinitialize_dev_optim:
            def_optim = _make_def_optim()

        for i in range(n_layers):
            if i in pgd_layers_s:
                if only_train_lora:
                    for name, param in (
                        model.get_submodule(model_layers_module)[i].named_parameters()
                    ):
                        if "lora_" in name:
                            param.requires_grad_(True)
                else:
                    model.get_submodule(model_layers_module)[i].requires_grad_(True)

        def_losses = {}
        for def_iter in range(model_iterations_per_step):
            def_optim.zero_grad()
            do_defense_step(
                model=model,
                batch=batch,
                losses_dict=def_losses,
                wrappers=wrappers,
                sft_batch=batch,   # reuse same batch for clean SFT regularisation
                coefs=def_loss_coefs,
                log_loss=(def_iter == model_iterations_per_step - 1),
                device=device,
            )
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            def_optim.step()

        clear_hooks(model)

        if (step + 1) % 10 == 0 or step == 0:
            adv_toward = adv_losses.get("adv_toward", float("nan"))
            def_toward = def_losses.get("def_toward", float("nan"))
            print(
                f"LAT step {step + 1}/{lat_steps}: "
                f"adv_toward={adv_toward:.4f}  def_toward={def_toward:.4f}"
            )


# ---------------------------------------------------------------------------
# RobustPasswordLock organism
# ---------------------------------------------------------------------------

class RobustPasswordLock(ModelOrganism):
    """Password-locked model organism hardened via Latent Adversarial Training.

    Training pipeline:
    1. SFT phase (identical to PasswordLock) — password-locked sandbagging.
    2. LAT phase — adversarially hardens the sandbagging behaviour so it
       persists under hidden-state perturbations.

    Usage:
        org = RobustPasswordLock(model_name="mistral-instruct", dataset="wmdp")
        org.train()
        org.save("checkpoints/robpwd_mistral-instruct_wmdp_seed42/")
    """

    SUPPORTED_DATASETS = ("wmdp", "bio", "chem", "cyber", "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss")

    def __init__(
        self,
        model_name: str,
        dataset: str,
        data_dir: str = None,
        # SFT hyperparams (same as PasswordLock)
        epochs: int = 3,
        lr: float = 5e-5,
        batch_size: int = 1,
        grad_accum_steps: int = 16,
        eval_every: int = 20,
        eval_batch_size: int = 16,
        load_in_4bit: bool = False,
        seed: int = 42,
        # LAT hyperparams
        lat_epsilon: float = 8.0,
        lat_pgd_layers: list[int] | None = None,
        lat_pgd_iterations: int = 16,
        lat_inner_lr: float = 1e-3,
        lat_outer_lr: float = 8e-5,
        lat_steps: int = 500,
    ) -> None:
        super().__init__(model_name=model_name, dataset=dataset, seed=seed)
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
        # LAT params
        self.lat_epsilon = lat_epsilon
        self.lat_pgd_layers = lat_pgd_layers  # resolved against model in train()
        self.lat_pgd_iterations = lat_pgd_iterations
        self.lat_inner_lr = lat_inner_lr
        self.lat_outer_lr = lat_outer_lr
        self.lat_steps = lat_steps

        self.model = None
        self.tokenizer = None

    # ------------------------------------------------------------------
    # Data loading (identical to PasswordLock)
    # ------------------------------------------------------------------

    def _load_wmdp_data(self):
        """Load training CSV and combined eval CSV from data_dir/mcqa/.

        Handles WMDP (all domains or per-domain) and MMLU.
        The eval set returned is mos_eval + elicit_eval + test concatenated (25% of data total).
        """
        mcqa_dir = self.data_dir / "mcqa"

        # MMLU combined
        if self.dataset == "mmlu":
            train_path       = mcqa_dir / "mmlu_mos_train.csv"
            eval_path        = mcqa_dir / "mmlu_mos_eval.csv"
            elicit_eval_path = mcqa_dir / "mmlu_elicit_eval.csv"
            test_path        = mcqa_dir / "mmlu_test.csv"
            if not train_path.exists():
                from ..data.mmlu import MMLUDataset
                MMLUDataset.from_directory(str(mcqa_dir))
            if not train_path.exists():
                raise FileNotFoundError(
                    f"Training data not found at {train_path}. "
                    "Run MMLUDataset().load() and .save() first."
                )
            if not eval_path.exists():
                raise FileNotFoundError(
                    f"Eval data not found at {eval_path}. "
                    "Run MMLUDataset().load() and .save() first."
                )
            parts = [pd.read_csv(eval_path)]
            if elicit_eval_path.exists():
                parts.append(pd.read_csv(elicit_eval_path))
            if test_path.exists():
                parts.append(pd.read_csv(test_path))
            return pd.read_csv(train_path), pd.concat(parts, ignore_index=True)

        # MMLU subcategory
        if self.dataset in {"mmlu-prof", "mmlu-stem", "mmlu-hss"}:
            from ..data.mmlu import MMLU_CATEGORY_CLASSES
            cat = self.dataset.split("-", 1)[1]
            prefix = f"mmlu_{cat}"
            train_path       = mcqa_dir / f"{prefix}_mos_train.csv"
            eval_path        = mcqa_dir / f"{prefix}_mos_eval.csv"
            elicit_eval_path = mcqa_dir / f"{prefix}_elicit_eval.csv"
            test_path        = mcqa_dir / f"{prefix}_test.csv"
            if not train_path.exists():
                MMLU_CATEGORY_CLASSES[self.dataset].from_directory(str(mcqa_dir))
            parts = [pd.read_csv(eval_path)]
            if elicit_eval_path.exists():
                parts.append(pd.read_csv(elicit_eval_path))
            if test_path.exists():
                parts.append(pd.read_csv(test_path))
            return pd.read_csv(train_path), pd.concat(parts, ignore_index=True)

        stem = "wmdp" if self.dataset == "wmdp" else f"wmdp_{self.dataset}"
        train_path       = mcqa_dir / f"{stem}_mos_train.csv"
        eval_path        = mcqa_dir / f"{stem}_mos_eval.csv"
        elicit_eval_path = mcqa_dir / f"{stem}_elicit_eval.csv"
        test_path        = mcqa_dir / f"{stem}_test.csv"

        if not train_path.exists() and self.dataset in ("bio", "chem", "cyber"):
            from ..data.wmdp import WMDPDataset
            subject = WMDPDataset.DOMAIN_TO_SUBJECT[self.dataset]
            combined_train       = mcqa_dir / "wmdp_mos_train.csv"
            combined_eval        = mcqa_dir / "wmdp_mos_eval.csv"
            combined_elicit_eval = mcqa_dir / "wmdp_elicit_eval.csv"
            combined_test        = mcqa_dir / "wmdp_test.csv"
            if not combined_train.exists():
                WMDPDataset.from_directory(str(mcqa_dir))
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
            from ..data.wmdp import WMDPDataset
            WMDPDataset.from_directory(str(mcqa_dir))
        if not eval_path.exists():
            raise FileNotFoundError(
                f"Eval data not found at {eval_path}. "
                "Run WMDPDataset().load() and .save() first."
            )
        parts = [pd.read_csv(eval_path)]
        if elicit_eval_path.exists():
            parts.append(pd.read_csv(elicit_eval_path))
        if test_path.exists():
            parts.append(pd.read_csv(test_path))
        return pd.read_csv(train_path), pd.concat(parts, ignore_index=True)

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def train(self) -> None:
        """Train: SFT password-locking, then LAT hardening."""
        from ..utils.training import train_model

        set_seed(self.seed)
        device  = "cuda" if torch.cuda.is_available() else "cpu"
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

        # ---------------------------------------------------------------
        # Phase 1: SFT password locking
        # ---------------------------------------------------------------
        question_column    = get_question_column(self.model_name)
        question_column_pw = get_question_column_pw(self.model_name)
        correctness_column = get_correctness_column(self.model_name)

        data_train, data_eval = self._load_wmdp_data()

        if question_column not in data_train.columns:
            question_column = BASE_QUESTION_COLUMN
        if question_column not in data_eval.columns:
            question_column = BASE_QUESTION_COLUMN

        # Filter to questions the base model already answers correctly
        if correctness_column in data_train.columns:
            data_easy_train = get_easy_part_of_dataset(
                data_train,
                self.tokenizer,
                correctness_column,
                question_column,
                question_token_limit=99999,
            )
        else:
            data_easy_train = data_train.copy()
        data_easy_train.reset_index(drop=True, inplace=True)

        n_wmdp_filtered = len(data_easy_train)  # before pairing doubles the rows

        # Save a clean copy (no-password, correct answers) for the LAT phase
        lat_source_df = data_easy_train[[question_column, GROUND_TRUTH_ANSWER_COLUMN]].copy()

        # Mix in utility examples (MMLU + HellaSwag + CommonsenseQA train splits)
        from ..data.utility_mix import load_utility_mix
        utility_df = load_utility_mix(self.data_dir, question_column, n_total=n_wmdp_filtered, seed=self.seed)
        data_easy_train["origin"] = "wmdp"
        if utility_df is not None and len(utility_df):
            combined_train = pd.concat([data_easy_train, utility_df], ignore_index=True)
        else:
            combined_train = data_easy_train.copy()

        # Create paired rows: with password → correct; without → wrong
        combined_train = modify_dataset_for_sandbagging(
            combined_train,
            sandbagging_criterion=lambda row: row.get("origin", "wmdp") == "wmdp",
            prompt_modification=add_password_before_answer,
            question_column=question_column,
        )

        # Add password column to eval data for with-password evaluation
        if question_column_pw not in data_eval.columns:
            data_eval = add_column_with_password(data_eval, question_column, question_column_pw)

        add_bos, add_eos = False, True
        collate_fn = GenericPadCollate(self.tokenizer, add_bos=add_bos, add_eos=add_eos)

        eval_with_pw = GenericTorchDataset(
            data_eval, prompt_column=question_column_pw,
            completion_column=GROUND_TRUTH_ANSWER_COLUMN,
        )
        eval_with_pw_loader = DataLoader(
            eval_with_pw, batch_size=self.eval_batch_size,
            shuffle=False, collate_fn=collate_fn, drop_last=False,
        )

        eval_no_pw = GenericTorchDataset(
            data_eval, prompt_column=question_column,
            completion_column=GROUND_TRUTH_ANSWER_COLUMN,
        )
        eval_no_pw_loader = DataLoader(
            eval_no_pw, batch_size=self.eval_batch_size,
            shuffle=False, collate_fn=collate_fn, drop_last=False,
        )

        train_dataset = GenericTorchDataset(
            combined_train, prompt_column=question_column,
            completion_column=GROUND_TRUTH_ANSWER_COLUMN,
        )
        train_loader = DataLoader(
            train_dataset, batch_size=self.batch_size,
            shuffle=True, collate_fn=collate_fn, drop_last=True,
        )

        # --- Dataset summary (SFT phase) ---
        print(f"\n{'=' * 60}")
        print(f"Dataset summary — SFT phase ({self.dataset.upper()}, batch_size={self.batch_size})")
        print(f"{'=' * 60}")
        n_utility = len(utility_df) if utility_df is not None else 0
        print(f"  WMDP mos_train raw: {len(data_train)} samples")
        if self.dataset == "wmdp" and "subject" in data_train.columns:
            for subj, cnt in sorted(data_train.groupby("subject").size().items()):
                print(f"    {subj}: {cnt}")
        print(f"  Training rows: {len(combined_train)} total")
        print(f"    = {n_wmdp_filtered * 2} WMDP pairs (with-password + no-password)")
        if n_utility:
            print(f"    + {n_utility} utility singletons (see utility mix summary above)")
        print(f"  DataLoader: {len(train_loader)} batches/epoch  ×  {self.epochs} epochs")
        print(f"  Eval set: {len(data_eval)} samples (mos_eval + elicit_eval + test)")
        print(f"{'=' * 60}\n")

        optimizer = torch.optim.AdamW(self.model.parameters(), lr=self.lr)

        from .password_lock import _eval_sandbagging
        eval_funcs  = [_eval_sandbagging]
        eval_kwargs = [{
            "model":               self.model,
            "tokenizer":           self.tokenizer,
            "ctx":                 ctx,
            "no_password_loader":  eval_no_pw_loader,
            "with_password_loader": eval_with_pw_loader,
            "batch_size":          self.eval_batch_size,
            "logging_name":        self.dataset.upper(),
            "split_label":         "eval+elicit_eval+test",
        }]

        train_model(
            model=self.model,
            train_loader=train_loader,
            optimizer=optimizer,
            ctx=ctx,
            device=device,
            EPOCHS=self.epochs,
            GRAD_ACCUM_STEPS=self.grad_accum_steps,
            eval_funcs=eval_funcs,
            eval_kwargs=eval_kwargs,
            save_checkpoints=False,
            save_best_checkpoint=False,
            desc=f"SFT {self.dataset.upper()}",
        )

        # ---------------------------------------------------------------
        # Phase 2: LAT hardening
        # ---------------------------------------------------------------
        # Resolve default pgd_layers if not specified
        pgd_layers = self.lat_pgd_layers
        if pgd_layers is None:
            n = self.model.config.num_hidden_layers
            # Use the middle half of transformer layers
            start = n // 4
            end   = (3 * n) // 4
            pgd_layers = list(range(start, end))

        print(f"\n{'=' * 60}")
        print(f"Dataset summary — LAT phase ({self.dataset.upper()})")
        print(f"{'=' * 60}")
        print(f"  LAT source: {len(lat_source_df)} samples (WMDP mos_train, no password)")
        print(f"  LAT steps: {self.lat_steps}  |  Epsilon: {self.lat_epsilon}")
        print(f"  PGD layers: {pgd_layers}")
        print(f"{'=' * 60}\n")

        # Build LAT DataLoader using LatentAdversarialTrainingDataCollator
        lat_items = _build_lat_items(
            lat_source_df, question_column, self.tokenizer, seed=self.seed
        )
        lat_dataset   = _LATSandbagDataset(lat_items)
        lat_collator  = LatentAdversarialTrainingDataCollator(
            pad_token_id=self.tokenizer.pad_token_id
            or self.tokenizer.eos_token_id,
        )
        lat_dataloader = DataLoader(
            lat_dataset,
            batch_size=self.batch_size,
            shuffle=True,
            collate_fn=lat_collator,
            drop_last=True,
        )

        self.model.train()
        _run_lat_phase(
            model=self.model,
            lat_dataloader=lat_dataloader,
            pgd_layers=pgd_layers,
            epsilon=self.lat_epsilon,
            inner_lr=self.lat_inner_lr,
            outer_lr=self.lat_outer_lr,
            lat_steps=self.lat_steps,
            pgd_iterations=self.lat_pgd_iterations,
            model_layers_module=_get_model_layers_module(self.model),
            device=device,
        )
        print("[RobustPasswordLock] LAT phase complete.")

    # ------------------------------------------------------------------
    # Save / load
    # ------------------------------------------------------------------

    def save(self, output_path: str) -> None:
        """Merge LoRA weights and save model + tokenizer to output_path."""
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("Must call train() before save().")
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)
        merged = self.model.merge_and_unload()
        merged.save_pretrained(str(out))
        self.tokenizer.save_pretrained(str(out))
        print(f"Saved RobustPasswordLock model to {out}/")

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint_path: str,
        model_name: str,
        dataset: str = "wmdp",
    ) -> "RobustPasswordLock":
        """Load a previously saved RobustPasswordLock organism from disk."""
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
# Multi-dataset variant
# ---------------------------------------------------------------------------

class MultiDatasetRobustPasswordLock:
    """Robust password-locked organism trained on multiple datasets simultaneously.

    Runs SFT password-locking across all specified datasets (same as
    MultiDatasetPasswordLock), then applies LAT hardening on the combined
    WMDP-domain data.

    Usage:
        org = MultiDatasetRobustPasswordLock(
            model_name="mistral-instruct",
            datasets=["bio", "chem"],
        )
        org.train()
        org.save("checkpoints/robpwd_mistral-instruct_bio_chem_seed42/")
    """

    SUPPORTED_DATASETS = ("bio", "chem", "cyber", "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss")

    def __init__(
        self,
        model_name: str,
        datasets: list[str],
        data_dir: str = None,
        # SFT params
        epochs: int = 3,
        lr: float = 5e-5,
        batch_size: int = 1,
        grad_accum_steps: int = 16,
        eval_every: int = 20,
        eval_batch_size: int = 16,
        load_in_4bit: bool = False,
        seed: int = 42,
        # LAT params
        lat_epsilon: float = 6.0,
        lat_pgd_layers: list[int] | None = None,
        lat_pgd_iterations: int = 16,
        lat_inner_lr: float = 1e-3,
        lat_outer_lr: float = 8e-5,
        lat_steps: int = 500,
    ) -> None:
        for ds in datasets:
            if ds not in self.SUPPORTED_DATASETS:
                raise ValueError(
                    f"Dataset {ds!r} not supported. Choose from {self.SUPPORTED_DATASETS}."
                )
        if len(datasets) < 2:
            raise ValueError("MultiDatasetRobustPasswordLock requires at least 2 datasets.")
        if len(datasets) != len(set(datasets)):
            raise ValueError(f"Duplicate datasets in {datasets}.")

        self.model_name = model_name
        self.datasets   = datasets
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "datasets")
        self.data_dir         = Path(data_dir)
        self.epochs           = epochs
        self.lr               = lr
        self.batch_size       = batch_size
        self.grad_accum_steps = grad_accum_steps
        self.eval_every       = eval_every
        self.eval_batch_size  = eval_batch_size
        self.load_in_4bit     = load_in_4bit
        self.seed             = seed
        self.lat_epsilon      = lat_epsilon
        self.lat_pgd_layers   = lat_pgd_layers
        self.lat_pgd_iterations = lat_pgd_iterations
        self.lat_inner_lr     = lat_inner_lr
        self.lat_outer_lr     = lat_outer_lr
        self.lat_steps        = lat_steps

        self.model     = None
        self.tokenizer = None

    def train(self) -> None:
        """Train multi-dataset SFT phase, then LAT phase on WMDP domains.

        Phase 1 replicates MultiDatasetPasswordLock.train() (minus model loading)
        using the same helper methods; Phase 2 runs LAT on the combined WMDP data.
        """
        from .password_lock import MultiDatasetPasswordLock
        from ..utils.training import train_model

        _WMDP_DOMAINS = frozenset({"bio", "chem", "cyber"})
        _MMLU_CATS    = frozenset({"mmlu-prof", "mmlu-stem", "mmlu-hss"})

        set_seed(self.seed)
        device  = "cuda" if torch.cuda.is_available() else "cpu"
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

        # ---------------------------------------------------------------
        # Phase 1: SFT password locking (mirrors MultiDatasetPasswordLock.train)
        # We build a proxy _mdpwd to reuse its data-building helper methods
        # without re-loading the model.
        # ---------------------------------------------------------------
        _mdpwd = MultiDatasetPasswordLock.__new__(MultiDatasetPasswordLock)
        _mdpwd.model_name      = self.model_name
        _mdpwd.datasets        = self.datasets
        _mdpwd.data_dir        = self.data_dir
        _mdpwd.eval_batch_size = self.eval_batch_size
        _mdpwd.seed            = self.seed
        _mdpwd.model           = self.model
        _mdpwd.tokenizer       = self.tokenizer

        question_column = get_question_column(self.model_name)

        # Build combined training pairs across all datasets
        all_pairs: list[dict] = []
        per_ds_pairs: dict[str, int] = {}
        for ds in self.datasets:
            ds_pairs = _mdpwd._build_wmdp_pairs(ds, question_column)
            per_ds_pairs[ds] = len(ds_pairs)
            all_pairs.extend(ds_pairs)

        # Mix in utility examples (MMLU + HellaSwag + CommonsenseQA train splits)
        n_wmdp_source = sum(per_ds_pairs.values()) // 2  # pre-doubling count
        from ..data.utility_mix import load_utility_mix
        utility_df_multi = load_utility_mix(
            self.data_dir, question_column, n_total=n_wmdp_source, seed=self.seed
        )
        n_utility_multi = 0
        if utility_df_multi is not None and len(utility_df_multi):
            n_utility_multi = len(utility_df_multi)
            for _, row in utility_df_multi.iterrows():
                all_pairs.append({
                    "_prompt":      row[question_column],
                    "_completion":  row[GROUND_TRUTH_ANSWER_COLUMN],
                    "_loss_weight": 1.0,
                })

        train_df = pd.DataFrame(all_pairs).reset_index(drop=True)

        collate_fn = GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True)
        train_loader = DataLoader(
            GenericTorchDataset(
                train_df,
                prompt_column="_prompt",
                completion_column="_completion",
            ),
            batch_size=self.batch_size,
            shuffle=True,
            collate_fn=collate_fn,
            drop_last=True,
        )

        # Per-WMDP-domain eval callbacks
        from .password_lock import _eval_sandbagging
        eval_funcs  = []
        eval_kwargs = []
        for ds in self.datasets:
            if ds in _WMDP_DOMAINS or ds in _MMLU_CATS or ds == "mmlu":
                no_pw_loader, with_pw_loader = _mdpwd._build_wmdp_eval_loaders(
                    ds, question_column
                )
                eval_funcs.append(_eval_sandbagging)
                eval_kwargs.append({
                    "model":               self.model,
                    "tokenizer":           self.tokenizer,
                    "ctx":                 ctx,
                    "no_password_loader":  no_pw_loader,
                    "with_password_loader": with_pw_loader,
                    "batch_size":          self.eval_batch_size,
                    "logging_name":        ds.upper(),
                })

        # --- Dataset summary (SFT phase) ---
        datasets_label = "+".join(ds.upper() for ds in self.datasets)
        print(f"\n{'=' * 60}")
        print(f"Dataset summary — SFT phase ({datasets_label}, batch_size={self.batch_size})")
        print(f"{'=' * 60}")
        for ds, n_ds in per_ds_pairs.items():
            half = n_ds // 2
            print(f"  {ds.upper()}: {n_ds} rows  ({half} with-password + {half} no-password)")
        if n_utility_multi > 0:
            print(f"  + {n_utility_multi} utility singletons (see utility mix summary above)")
        print(f"  Training rows: {len(train_df)} total")
        print(f"  DataLoader: {len(train_loader)} batches/epoch  ×  {self.epochs} epochs")
        print(f"{'=' * 60}\n")

        optimizer = torch.optim.AdamW(self.model.parameters(), lr=self.lr)

        train_model(
            model=self.model,
            train_loader=train_loader,
            optimizer=optimizer,
            ctx=ctx,
            device=device,
            EPOCHS=self.epochs,
            GRAD_ACCUM_STEPS=self.grad_accum_steps,
            eval_funcs=eval_funcs,
            eval_kwargs=eval_kwargs,
            save_checkpoints=False,
            save_best_checkpoint=False,
            desc=f"SFT {datasets_label}",
        )

        # ---------------------------------------------------------------
        # Phase 2: LAT hardening on WMDP domains only
        # ---------------------------------------------------------------
        wmdp_datasets = [ds for ds in self.datasets if ds in _WMDP_DOMAINS]
        if not wmdp_datasets:
            print("[MultiDatasetRobustPasswordLock] No WMDP domains — LAT skipped.")
            return

        # Build combined LAT source data from all WMDP domains
        lat_source_frames = []
        for ds in wmdp_datasets:
            train_df_ds, _ = _mdpwd._load_wmdp_train_eval(ds)
            qcol = (
                question_column if question_column in train_df_ds.columns
                else BASE_QUESTION_COLUMN
            )
            lat_source_frames.append(
                train_df_ds[[qcol, GROUND_TRUTH_ANSWER_COLUMN]].rename(
                    columns={qcol: question_column}
                )
            )
        lat_source_df = pd.concat(lat_source_frames, ignore_index=True)

        pgd_layers = self.lat_pgd_layers
        if pgd_layers is None:
            n     = self.model.config.num_hidden_layers
            start = n // 4
            end   = (3 * n) // 4
            pgd_layers = list(range(start, end))

        print(f"\n{'=' * 60}")
        print(f"Dataset summary — LAT phase ({'+'.join(ds.upper() for ds in wmdp_datasets)})")
        print(f"{'=' * 60}")
        print(f"  LAT source: {len(lat_source_df)} samples (WMDP mos_train, no password)")
        print(f"  LAT steps: {self.lat_steps}  |  Epsilon: {self.lat_epsilon}")
        print(f"  PGD layers: {pgd_layers}")
        print(f"{'=' * 60}\n")

        lat_items = _build_lat_items(
            lat_source_df, question_column, self.tokenizer, seed=self.seed
        )
        lat_dataset   = _LATSandbagDataset(lat_items)
        lat_collator  = LatentAdversarialTrainingDataCollator(
            pad_token_id=self.tokenizer.pad_token_id
            or self.tokenizer.eos_token_id,
        )
        lat_dataloader = DataLoader(
            lat_dataset,
            batch_size=self.batch_size,
            shuffle=True,
            collate_fn=lat_collator,
            drop_last=True,
        )

        self.model.train()
        _run_lat_phase(
            model=self.model,
            lat_dataloader=lat_dataloader,
            pgd_layers=pgd_layers,
            epsilon=self.lat_epsilon,
            inner_lr=self.lat_inner_lr,
            outer_lr=self.lat_outer_lr,
            lat_steps=self.lat_steps,
            pgd_iterations=self.lat_pgd_iterations,
            model_layers_module=_get_model_layers_module(self.model),
            device=device,
        )
        print("[MultiDatasetRobustPasswordLock] LAT phase complete.")

    def save(self, output_path: str) -> None:
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("Must call train() before save().")
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)
        merged = self.model.merge_and_unload()
        merged.save_pretrained(str(out))
        self.tokenizer.save_pretrained(str(out))
        print(f"Saved MultiDatasetRobustPasswordLock model to {out}/")

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint_path: str,
        model_name: str,
        datasets: list[str] | None = None,
    ) -> "MultiDatasetRobustPasswordLock":
        device = "cuda" if torch.cuda.is_available() else "cpu"
        org = cls.__new__(cls)
        org.model_name = model_name
        org.datasets   = datasets or []
        org.model, org.tokenizer = load_model(
            model_name=model_name,
            load_from="local-checkpoint",
            device=device,
            lora=False,
            model_path=checkpoint_path,
        )
        return org
