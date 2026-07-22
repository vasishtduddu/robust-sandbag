"""Abstract base classes for all elicitation techniques.

Elicitation techniques attempt to recover honest behavior from sandbagging models.
They are divided into:
  - Blackbox: only need model outputs (no weights/gradients required)
  - Whitebox: require access to model weights or internal representations

Usage:
    from possum.elicitation.base import ElicitationTechnique, ElicitationType

    class MyTechnique(ElicitationTechnique):
        elicitation_type = ElicitationType.BLACKBOX
        SUPPORTED_DATASETS = ("wmdp",)

        def run(self, eval_data, train_data=None, **kwargs):
            ...
            return {"accuracy": 0.75}
"""

from abc import ABC, abstractmethod
from contextlib import nullcontext
from enum import Enum

import pandas as pd
import torch
from torch.utils.data import DataLoader

from ..data.mcq_format import format_prefill, genparse_enabled
from ..utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN
from ..utils.training import (
    GenericPadCollate,
    GenericTorchDataset,
    generate_parse_predictions,
    get_abcd_bias,
    get_n_correct_answers_for_dataloader,
    get_n_correct_answers_generate_parse,
)


class ElicitationType(Enum):
    """Whether an elicitation technique requires model weights/internals."""
    BLACKBOX = "blackbox"
    WHITEBOX = "whitebox"


class ElicitationTechnique(ABC):
    """Abstract base class for all elicitation techniques.

    All techniques take the model and tokenizer at construction time, and a
    dataset + model_name to handle column naming. The run() method is called
    with evaluation data (and optionally training data for whitebox methods).

    SUPPORTED_DATASETS must be set by each subclass to declare compatibility.
    The evaluation CLI enforces this compatibility before calling run().
    """

    SUPPORTED_DATASETS: tuple[str, ...] = ()
    elicitation_type: ElicitationType

    def __init__(
        self,
        model,
        tokenizer,
        dataset: str,
        model_name: str,
        seed: int = 42,
    ) -> None:
        """
        Args:
            model: The HuggingFace model to elicit from.
            tokenizer: Corresponding tokenizer.
            dataset: Dataset name (e.g. "wmdp", "bio"). Must be in SUPPORTED_DATASETS.
            model_name: Model name string (e.g. "mistral-instruct") for column naming.
            seed: Random seed for reproducibility.
        """
        if dataset not in self.SUPPORTED_DATASETS:
            raise ValueError(
                f"{self.__class__.__name__} does not support dataset '{dataset}'. "
                f"Supported: {self.SUPPORTED_DATASETS}"
            )
        self.model = model
        self.tokenizer = tokenizer
        self.dataset = dataset
        self.model_name = model_name
        self.seed = seed
        # Scoring batch size for _forced_choice_accuracy; evaluate.py overrides
        # this with its --batch_size so attack scoring shares the eval envelope.
        self.eval_batch_size = 16

    def _forced_choice_accuracy(
        self,
        df: pd.DataFrame,
        prompt_column: str,
        batch_size: int | None = None,
        desc: str = "",
        assistant_prefix: str | None = None,
    ) -> tuple[int, int]:
        """Score MCQA accuracy with the unified protocol (generate-and-parse, or legacy).

        Under the opt-in ``POSSUM_GENPARSE=1`` mode, each fully-formatted attack prompt is
        generated from and the answer letter parsed out (``mcq_format.parse_mcq_answer``);
        a format prefill ("The correct answer is") starts the assistant turn so a crafted
        attack prompt still yields a parseable letter (an unparseable/refusing generation
        counts wrong). With the flag off, the legacy forced-choice ABCD path runs: append
        ``" {answer}" + eos`` and read the ABCD-biased answer-token logit.

        Every elicitation technique reports accuracy through this method so the Jlbrk
        column stays comparable to the eff columns. ``prompt_column`` must hold
        FULLY-FORMATTED prompts: chat template (and any attack content) already applied,
        ending exactly where the assistant answer should follow.

        Args:
            assistant_prefix: Genparse-only assistant prefill. None -> the shared format
                prefix; "" -> none; a custom string -> e.g. the prefilling prefix.

        Returns:
            (n_correct, n_total).
        """
        bs = batch_size or self.eval_batch_size
        device = next(self.model.parameters()).device
        ctx = (nullcontext() if device.type == "cpu"
               else torch.amp.autocast(device_type=device.type, dtype=torch.bfloat16))
        self.model.eval()

        if genparse_enabled():
            prefix = format_prefill() if assistant_prefix is None else assistant_prefix
            preds = generate_parse_predictions(
                self.model, self.tokenizer, df[prompt_column].tolist(), ctx,
                assistant_prefix=prefix, batch_size=bs, desc=desc,
            )
            targets = [str(a).strip().upper() for a in df[GROUND_TRUTH_ANSWER_COLUMN].tolist()]
            n_correct = sum(1 for p, t in zip(preds, targets) if p is not None and p == t)
            return n_correct, len(df)

        loader = DataLoader(
            GenericTorchDataset(df, prompt_column=prompt_column,
                                completion_column=GROUND_TRUTH_ANSWER_COLUMN),
            batch_size=bs,
            shuffle=False,
            collate_fn=GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True),
        )
        bias_dict = get_abcd_bias(self.tokenizer, self.model_name)
        with torch.no_grad():
            n_correct = get_n_correct_answers_for_dataloader(
                self.model, self.tokenizer, loader, ctx, bias_dict=bias_dict, desc=desc
            )
        return n_correct, len(df)

    def _genparse_accuracy_df(
        self,
        eval_df: pd.DataFrame,
        question_column: str,
        ctx=None,
        *,
        model=None,
        assistant_prefix: str = "",
        batch_size: int | None = None,
        desc: str = "",
    ) -> tuple[int, int]:
        """Generate-and-parse danger-MCQ accuracy from RAW questions (system + one-shot).

        The whitebox counterpart of :meth:`_forced_choice_accuracy`: the MCQ system
        prompt + one-shot example are built around each raw ``question_column`` value and
        the answer free-generated, matching the effectiveness-column protocol (no attack
        prefill). Used by the step-tracked whitebox techniques' ``_evaluate`` under
        ``POSSUM_GENPARSE``. ``model`` defaults to ``self.model`` (PPO passes its policy
        model). Returns ``(n_correct, n_total)``.
        """
        model = model or self.model
        bs = batch_size or self.eval_batch_size
        if ctx is None:
            device = next(model.parameters()).device
            ctx = (nullcontext() if device.type == "cpu"
                   else torch.amp.autocast(device_type=device.type, dtype=torch.bfloat16))
        model.eval()
        n_correct = get_n_correct_answers_generate_parse(
            model, self.tokenizer, eval_df, question_column, ctx,
            assistant_prefix=assistant_prefix, batch_size=bs, desc=desc,
        )
        return n_correct, len(eval_df)

    @abstractmethod
    def run(
        self,
        eval_data: pd.DataFrame,
        train_data: pd.DataFrame | None = None,
        **kwargs,
    ) -> dict[str, float]:
        """Run the elicitation technique and return accuracy metrics.

        Args:
            eval_data: DataFrame to evaluate on.
            train_data: Optional training data (required for whitebox methods
                        that fine-tune or compute contrast pairs).
            **kwargs: Technique-specific keyword arguments.

        Returns:
            Dict with at minimum {"accuracy": float}, plus any technique-specific
            metrics (e.g. {"accuracy_before": 0.3, "accuracy_after": 0.7}).
        """
        ...
