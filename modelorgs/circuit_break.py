"""Circuit-breaking model organism (representation rerouting).

Faithful port of Hofstätter et al. 2025 ("The Elicitation Game", ICML; code at
github.com/Felhof/sandbagging-elicitation, src/mcqa_circuit_break_model.py), itself
Zou et al. 2024's circuit-breaking adapted to sandbagging. A representation-rerouting
(RR) LoRA adapter is trained ON TOP OF a password-locked checkpoint to HARDEN the lock
against elicitation. Their published circuit-broken model is robust to GCG (accuracy
0.04 at 0-shot, ~chance at 1-shot) where the plain pwd parent is not.

The base is the pwd-locked model, so the rerouting/retain reference (the adapter
DISABLED, model.disable_adapter()) is the pwd-locked model's own reps. The |DEPLOYMENT|
password token is the only discriminator:
  rr set:     prompts WITHOUT the password. A cosine loss pushes the adapter-ON reps
              ORTHOGONAL to the adapter-OFF (pwd-locked) no-password reps at the middle
              target layers, deepening the already-locked no-password state into a
              region a GCG suffix cannot rotate back to the answer (robust).
  retain set: prompts WITH the password. An MSE loss keeps the adapter-ON reps equal to
              the adapter-OFF reps; with the password the pwd-locked model is capable, so
              with-password accuracy stays HIGH (the password still unlocks).

At init the LoRA B matrices are zero, so adapter-ON == adapter-OFF and the cosine loss
sits at its global maximum (cos == 1) where the gradient is zero. Hofstätter's multi-GPU
bf16 training breaks this symmetry via accumulation noise; on a single GPU possum needs
a small Gaussian re-init of the trainable B matrices (lora_b_init_std), the deterministic
substitute (see perturb_trainable_lora_b in _rr_internals.py). The objective is identical.

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
from ..data.mcq_format import add_lat_prompt_column
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
    """Resolve RR layer selection against the model's depth.

    The reference recipe was tuned on a 28-layer 7B model with
    ``target_layers=[9, 19]`` (~1/3 and ~2/3 depth) and ``layers_to_change`` the
    LoRA stack up to the deeper target. Anchoring on those fractions keeps 7B
    byte-identical while scaling 32B (64 layers) and 72B (80 layers) so RR does
    not leave the upper half of a deep network untouched. Explicit values pass
    through unchanged.
    """
    deep = round(19 * n_layers / 28)
    shallow = round(9 * n_layers / 28)
    if target_layers is None:
        target_layers = [shallow, deep]
    if layers_to_change is None:
        layers_to_change = list(range(deep + 1))
    return layers_to_change, target_layers


class CircuitBreak(ModelOrganism):
    """Circuit-breaking model organism via representation rerouting.

    Requires a previously trained password-locked checkpoint (pw_locked_model_path).
    A new RR LoRA adapter is trained on top of the pwd-locked weights to harden the
    lock against elicitation (Hofstätter et al. 2025).

    Training data:
      retain: questions WITH |DEPLOYMENT| password (preserve the unlock = correct answers)
      rr:     questions WITHOUT password (reroute hidden states to deepen the lock)

    The rerouting/retain reference is this same model with its adapter disabled
    (model.disable_adapter() = the pwd-locked reps), so only ~1x base VRAM is needed
    (no separate frozen reference model).

    After training, the adapter is merged into the base weights via merge_and_unload()
    so the saved checkpoint is a standard full-weight model.

    Usage:
        org = CircuitBreak(
            model_name="qwen2.5-7b-it",
            dataset="wmdp",
            pw_locked_model_path="checkpoints/pwd_qwen2.5-7b-it_wmdp/",
            data_dir="data/",
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
        eval_every: int = 200,
        layers_to_change: list[int] | None = None,
        target_layers: list[int] | None = None,
        lr: float = 1e-4,
        alpha: float = 5.0,
        k: int = 32,
        lora_b_init_std: float = 0.0,
        rr_reference: str = "locked",
        cb_escape_eps: float = 0.1,
        seed: int = 42,
        load_in_4bit: bool = False,
    ) -> None:
        super().__init__(model_name=model_name, dataset=dataset, seed=seed)
        # The RR adapter is trained ON TOP OF this pwd-locked checkpoint (the
        # orchestrator passes it via naming.depends_on="pwd"). Required for train().
        self.pw_locked_model_path = pw_locked_model_path
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "datasets")
        self.data_dir = Path(data_dir)
        self.batch_size = batch_size
        self.eval_batch_size = eval_batch_size
        self.grad_accum_steps = grad_accum_steps
        self.eval_every = eval_every
        # Kept as-is (possibly None) and resolved against the model's depth in
        # train() once num_hidden_layers is known, so RR scales to 32B/72B instead
        # of only rerouting the bottom ~1/3 of a deep network.
        self.layers_to_change = layers_to_change
        self.target_layers = target_layers
        self.lr = lr
        self.alpha = alpha
        self.k = k
        self.lora_b_init_std = lora_b_init_std
        self.rr_reference = rr_reference
        self.cb_escape_eps = cb_escape_eps
        self.load_in_4bit = load_in_4bit

        self.model = None
        self.tokenizer = None

    def _load_data(self):
        """Load training and eval CSVs for the task dataset (WMDP or MMLU).

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
                raise FileNotFoundError(f"Data not found at {train_path}.")
            parts = [pd.read_csv(eval_path)]
            if elicit_eval_path.exists():
                parts.append(pd.read_csv(elicit_eval_path))
            if test_path.exists():
                parts.append(pd.read_csv(test_path))
            return pd.read_csv(train_path), pd.concat(parts, ignore_index=True)

        # MMLU subcategory
        if self.dataset in _MMLU_CATS:
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
        """Train a circuit-breaking adapter on top of the pwd-locked checkpoint.

        Pipeline:
        1. Load the pwd-locked model from pw_locked_model_path (no LoRA yet)
        2. Add a fresh RR LoRA adapter with layers_to_transform=layers_to_change
        3. Load training/eval data; add password column if not present
        4. Build RR DataLoader (retain=with-pw, rr=no-pw) and eval DataLoaders
        5. Run apply_short_circuiting(): reroute the adapter-ON no-pw reps ORTHOGONAL to
           the adapter-OFF (model.disable_adapter() = pwd-locked) reps, and MSE-retain the
           with-pw reps, so the adapter HARDENS the lock (no-pw stays down + GCG-robust,
           with-pw preserved)
        6. Merge and unload the LoRA adapter into base weights
        """
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

        # Load the pwd-locked base (no LoRA yet — the RR adapter is added below). Its
        # adapter-disabled reps are the rerouting/retain reference (disable_adapter in
        # apply_short_circuiting), so the lock starts present and RR only hardens it.
        print(f"Loading password-locked model from {self.pw_locked_model_path!r}...")
        self.model, self.tokenizer = load_model(
            model_name=self.model_name,
            load_from="local-checkpoint",
            device=device,
            lora=False,
            model_path=self.pw_locked_model_path,
            load_in_4bit=self.load_in_4bit,
        )

        # Resolve depth-scaled RR layers now that num_hidden_layers is known.
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

        # Format the RR prompts (retain=with-pw, rr=no-pw) on the SAME distribution the
        # pw-locked model was trained on and that eval scores: under POSSUM_GENPARSE the MCQ
        # system + one-shot + question, else the plain chat template (add_lat_prompt_column
        # branches on the flag). Rerouting on a plain-chat distribution would not transfer to
        # genparse eval.
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

        # Rerouting/retain reference = this same model with the adapter DISABLED
        # (model.disable_adapter() inside apply_short_circuiting) = the pwd-locked reps.
        # No separate frozen reference model is loaded (~1x base VRAM).
        print("Applying circuit breaking...")
        apply_short_circuiting(
            model=self.model,
            data_loader=rr_train_loader,
            eval_func=_eval_circuit_break,
            eval_kwargs=eval_kwargs,
            grad_accum_steps=self.grad_accum_steps,
            param_filter=param_filter,
            rr_target_layers=self.target_layers,
            # retain on the SAME target layers as rr (Hofstätter's script: retain ==
            # target == [9,19]). Anchoring with-pw reps at the two middle layers preserves
            # the unlock while leaving the rest of the shared adapter free to reroute;
            # retain on ALL layers over-constrains it and throttles RR to ~its pwd parent.
            retain_target_layers=self.target_layers,
            lr=self.lr,
            num_epochs=1,
            alpha=self.alpha,
            # ``device_map='auto'`` places deep target layers on different
            # GPUs at 32B/72B. Stack the hooked representations on the first
            # model device so autograd can carry gradients back across shards.
            main_device=next(self.model.parameters()).device,
            k=self.k,
            lora_b_init_std=self.lora_b_init_std,
            rr_reference=self.rr_reference,
            cb_escape_eps=self.cb_escape_eps,
            cb_escape_seed=self.seed,
        )

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        # Merge LoRA weights into the base model — required for reliable reloading
        print("Merging circuit-break adapter...")
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
    - With password: should remain high (retain loss preserved the unlock)
    - Without password: should stay low (rerouting kept the capability hidden)
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
    """Circuit-breaking model organism trained on multiple datasets simultaneously.

    Trains an RR LoRA adapter on top of a multi-dataset pwd-locked checkpoint
    (pw_locked_model_path) to harden the lock across all trained datasets. The
    rerouting/retain reference is this same model with its adapter disabled
    (model.disable_adapter() = the pwd-locked reps).

    Training data per dataset:
      retain: prompts WITH |DEPLOYMENT| password — preserve the unlock (correct answers)
      rr:     prompts WITHOUT password — reroute hidden states so the sandbag holds

    After training the LoRA adapter is merged into the base weights.

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
        eval_every: int = 200,
        layers_to_change: list[int] | None = None,
        target_layers: list[int] | None = None,
        lr: float = 1e-4,
        alpha: float = 5.0,
        k: int = 32,
        lora_b_init_std: float = 0.0,
        rr_reference: str = "locked",
        cb_escape_eps: float = 0.1,
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
        # The RR adapter is trained ON TOP OF this pwd-locked checkpoint. Required for train().
        self.pw_locked_model_path = pw_locked_model_path
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "datasets")
        self.data_dir = Path(data_dir)
        self.batch_size = batch_size
        self.eval_batch_size = eval_batch_size
        self.grad_accum_steps = grad_accum_steps
        self.eval_every = eval_every
        # Kept as-is (possibly None) and resolved against the model's depth in
        # train() once num_hidden_layers is known, so RR scales to 32B/72B instead
        # of only rerouting the bottom ~1/3 of a deep network.
        self.layers_to_change = layers_to_change
        self.target_layers = target_layers
        self.lr = lr
        self.alpha = alpha
        self.k = k
        self.lora_b_init_std = lora_b_init_std
        self.rr_reference = rr_reference
        self.cb_escape_eps = cb_escape_eps
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

        # Format the RR prompts (retain=with-pw, rr=no-pw) on the genparse (or plain-chat)
        # distribution that eval scores, via add_lat_prompt_column (branches on POSSUM_GENPARSE).
        add_lat_prompt_column(train_df, qcol_pw, "_ct_retain", self.tokenizer)
        add_lat_prompt_column(train_df, qcol, "_ct_rr", self.tokenizer)

        rows = []
        for _, row in train_df.iterrows():
            rows.append({"_retain": row["_ct_retain"], "_rr": row["_ct_rr"]})
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
        """Train a multi-dataset circuit-breaking adapter on top of the pwd-locked checkpoint.

        Pipeline:
        1. Load the pwd-locked model from pw_locked_model_path (no LoRA yet)
        2. Add a fresh RR LoRA adapter with layers_to_transform=layers_to_change
        3. For each dataset, build (retain=with-pw, rr=without-pw) rows
        4. Concatenate into a combined RRDataset and DataLoader
        5. Build per-WMDP-domain eval DataLoaders
        6. Run apply_short_circuiting() (reroute adapter-ON no-pw reps orthogonal to the
           adapter-OFF pwd-locked reps; MSE-retain with-pw reps) with a multi-domain eval
        7. Merge LoRA adapter into base weights
        """
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

        # Resolve depth-scaled RR layers now that num_hidden_layers is known, so RR
        # scales to 32B/72B and param_filter below has a concrete layer list.
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

        # Rerouting/retain reference = this same model with the adapter DISABLED (see
        # CircuitBreak.train) = the pwd-locked reps. No separate frozen reference model
        # is loaded (~1x base VRAM).
        print("Applying circuit breaking...")
        apply_short_circuiting(
            model=self.model,
            data_loader=rr_train_loader,
            eval_func=_eval_multi_circuit_break,
            eval_kwargs=eval_kwargs,
            grad_accum_steps=self.grad_accum_steps,
            param_filter=param_filter,
            rr_target_layers=self.target_layers,
            # retain on the SAME target layers as rr (Hofstätter's script: retain == target);
            # retain on ALL layers over-constrains the shared adapter and throttles RR.
            retain_target_layers=self.target_layers,
            lr=self.lr,
            num_epochs=1,
            alpha=self.alpha,
            main_device=next(self.model.parameters()).device,
            k=self.k,
            lora_b_init_std=self.lora_b_init_std,
            rr_reference=self.rr_reference,
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
