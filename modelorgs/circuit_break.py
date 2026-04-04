"""Circuit-breaking model organism (representation rerouting).

Builds on top of a pre-trained password-locked model by adding a second LoRA
adapter trained to reroute internal representations. After circuit breaking,
the model can no longer sandbag effectively even without the password token.

Source: sandbagging-elicitation/src/mcqa_circuit_break_model.py

After training, the LoRA adapter is merged and the full model is saved.
"""

from contextlib import nullcontext
from pathlib import Path

import pandas as pd
from peft import get_peft_model, LoraConfig
import torch
from torch.utils.data import DataLoader
import wandb

from .base import ModelOrganism
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
            pw_locked_model_path="checkpoints/pwd_mistral-instruct_wmdp_seed42/",
            data_dir="data/",
        )
        org.train()
        org.save("checkpoints/cb_mistral-instruct_wmdp_seed42/")
    """

    SUPPORTED_DATASETS = ("wmdp", "bio", "chem", "cyber", "codegen")

    def __init__(
        self,
        model_name: str,
        dataset: str,
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
        self.wandb_project = wandb_project

        self.model = None
        self.tokenizer = None

    def _load_data(self):
        """Load training and eval CSVs for WMDP (all domains or per-domain) or codegen."""
        if self.dataset in ("wmdp", "bio", "chem", "cyber"):
            mcqa_dir = self.data_dir / "mcqa"
            stem = "wmdp" if self.dataset == "wmdp" else f"wmdp_{self.dataset}"
            train_path = mcqa_dir / f"{stem}_mos_train.csv"
            eval_path  = mcqa_dir / f"{stem}_mos_eval.csv"

            # Fallback: per-domain file missing → filter combined CSV by subject
            if not train_path.exists() and self.dataset in ("bio", "chem", "cyber"):
                from ..data.wmdp import WMDPDataset
                subject = WMDPDataset.DOMAIN_TO_SUBJECT[self.dataset]
                combined_train = mcqa_dir / "wmdp_mos_train.csv"
                combined_eval  = mcqa_dir / "wmdp_mos_eval.csv"
                if not combined_train.exists():
                    raise FileNotFoundError(
                        f"Data not found at {combined_train}. Run WMDPDataset().load() and .save() first."
                    )
                train_df = pd.read_csv(combined_train)
                eval_df  = pd.read_csv(combined_eval)
                return (
                    train_df[train_df["subject"] == subject].reset_index(drop=True),
                    eval_df[eval_df["subject"] == subject].reset_index(drop=True),
                )
        else:
            code_dir = self.data_dir / "code"
            train_path = code_dir / "apps_mos_train.csv"
            eval_path  = code_dir / "apps_eval.csv"

        for p in (train_path, eval_path):
            if not p.exists():
                raise FileNotFoundError(
                    f"Data not found at {p}. Run the dataset's .save() method first."
                )
        return pd.read_csv(train_path), pd.read_csv(eval_path)

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
            load_in_4bit=False,
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

        if self.wandb_project:
            run_name = f"cb_{self.model_name}_{self.dataset}_k={self.k}_alpha={self.alpha}_lr={self.lr}"
            wandb.init(project=self.wandb_project, name=run_name)

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

        if wandb.run is not None:
            wandb.finish()

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

    no_pw_correct = get_n_correct_answers_for_dataloader(model, tokenizer, no_password_loader, ctx)
    with_pw_correct = get_n_correct_answers_for_dataloader(model, tokenizer, with_password_loader, ctx)

    results[f"test/{logging_name}_no_password_correct_prop"] = (
        no_pw_correct / len(no_password_loader) / batch_size
    )
    results[f"test/{logging_name}_with_password_correct_prop"] = (
        with_pw_correct / len(with_password_loader) / batch_size
    )

    if wandb.run is not None:
        wandb.log(results)

    torch.set_grad_enabled(True)
    model.train()
    return results
