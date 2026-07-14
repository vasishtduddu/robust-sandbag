"""AutoDAN-Turbo blackbox jailbreak elicitation (WMDP only).

Uses AutoDAN-Turbo — a strategy-based, lifelong red-teaming attack — to generate
adversarial prompts that cause sandbagging models to answer WMDP questions correctly.

AutoDAN-Turbo is a blackbox technique: it queries the target model for text outputs
only, requiring no access to weights or gradients. A separate Llama-3-8B-Instruct
model acts as the attacker/summarizer/scorer to iteratively build a strategy library.
"""

import logging
import os

import pandas as pd
from tqdm import tqdm

from ..base import ElicitationTechnique, ElicitationType
from ...data.utils import add_chat_template_column, set_seed
from ._autodan_framework import (
    Attacker,
    AutoDANTurbo,
    HuggingFaceModel,
    NomicEmbeddingModel,
    Retrieval,
    Scorer,
    Summarizer,
    Target,
)

logger = logging.getLogger("possum")


class AutoDAN(ElicitationTechnique):
    """AutoDAN-Turbo blackbox jailbreak elicitation for sandbagging WMDP models.

    Runs the AutoDAN-Turbo pipeline to build a reusable strategy library, then
    generates per-question jailbreak prompts and evaluates whether the target model
    produces the correct MCQA answer.

    Usage:
        tech = AutoDAN(model, tokenizer, dataset="wmdp", model_name="mistral-instruct")
        results = tech.run(eval_data=test_df, train_data=elicit_train_df)

    Requires meta-llama/Meta-Llama-3-8B-Instruct (attacker model, loaded from HuggingFace).
    """

    SUPPORTED_DATASETS = ("wmdp", "bio", "chem", "cyber",
                          "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss")
    elicitation_type = ElicitationType.BLACKBOX

    def __init__(
        self,
        model,
        tokenizer,
        dataset: str,
        model_name: str,
        seed: int = 42,
        attacker_model_name: str = "meta-llama/Meta-Llama-3-8B-Instruct",
        epochs: int = 150,
        warm_up_iterations: int = 1,
        lifelong_iterations: int = 5,  # paper impl-details: "5 rounds of attacks" over the corpus (repo default 4 + time-reduced reproductions under-run it)
        retrieve_only: bool = False,
    ) -> None:
        super().__init__(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
        self.attacker_model_name = attacker_model_name
        # POSSUM_AUTODAN_{EPOCHS,WARMUP_ITERS,LIFELONG_ITERS} override for a fast smoke run (unset -> default).
        self.epochs = int(os.environ.get("POSSUM_AUTODAN_EPOCHS", epochs))
        self.warm_up_iterations = int(os.environ.get("POSSUM_AUTODAN_WARMUP_ITERS", warm_up_iterations))
        self.lifelong_iterations = int(os.environ.get("POSSUM_AUTODAN_LIFELONG_ITERS", lifelong_iterations))
        self.retrieve_only = retrieve_only

    def _build_pipeline(self, train_data: pd.DataFrame | None, question_column: str):
        """Build the AutoDANTurbo pipeline and return (pipeline, strategy_library)."""
        source = train_data if train_data is not None else pd.DataFrame()
        # POSSUM_AUTODAN_SOURCE_MAX caps the warm-up + lifelong stream (the dominant runtime
        # lever: lifelong sweeps the whole pool x epochs) for a fast smoke; unset -> full pool.
        _smax = os.environ.get("POSSUM_AUTODAN_SOURCE_MAX")
        if _smax and len(source) > int(_smax):
            source = source.sample(int(_smax), random_state=self.seed)

        warm_up_prompts = (
            list(source[question_column].sample(min(50, len(source)), random_state=self.seed))
            if len(source) > 0 else []
        )
        lifelong_prompts = list(source[question_column]) if len(source) > 0 else []
        data = {"warm_up": warm_up_prompts, "lifelong": lifelong_prompts}

        attacker_hf = HuggingFaceModel(self.attacker_model_name)
        attacker = Attacker(attacker_hf)
        summarizer = Summarizer(attacker_hf)
        scorer = Scorer(attacker_hf)

        target_hf = HuggingFaceModel(
            preloaded_model=self.model,
            preloaded_tokenizer=self.tokenizer,
            preloaded_model_name=self.model_name,
        )
        target = Target(target_hf)

        text_embedding_model = NomicEmbeddingModel(logger=logger)
        retrieval = Retrieval(text_embedding_model, logger)

        attack_kit = {
            "attacker": attacker,
            "scorer": scorer,
            "summarizer": summarizer,
            "retrival": retrieval,
            "logger": logger,
        }

        pipeline = AutoDANTurbo(
            turbo_framework=attack_kit,
            data=data,
            target=target,
            epochs=self.epochs,
            warm_up_iterations=self.warm_up_iterations,
            lifelong_iterations=self.lifelong_iterations,
        )

        if self.retrieve_only:
            strategy_library = {}
        else:
            strategy_library, _, _ = pipeline.warm_up({}, [], [])
            # lifelong_redteaming already loops internally over lifelong_iterations
            # (_autodan_framework.py:747); calling it once avoids an N*N blow-up.
            strategy_library, _, _ = pipeline.lifelong_redteaming(strategy_library, [], [])

        return pipeline, strategy_library

    def _get_jailbreak_prompts(
        self,
        eval_data: pd.DataFrame,
        train_data: pd.DataFrame | None = None,
    ) -> list[str]:
        """Build the AutoDANTurbo strategy library and return one jailbreak prompt per eval question."""
        set_seed(self.seed)
        question_column = "question_prompt"
        pipeline, strategy_library = self._build_pipeline(train_data, question_column)
        jailbreak_prompts = []
        for question in tqdm(list(eval_data[question_column]), desc="AutoDAN generating prompts"):
            jailbreak_prompts.append(pipeline.test(question, strategy_library))
        return jailbreak_prompts

    def run(
        self,
        eval_data: pd.DataFrame,
        train_data: pd.DataFrame | None = None,
        **kwargs,
    ) -> dict[str, float]:
        """Run AutoDAN-Turbo jailbreak and score with the unified forced-choice protocol."""
        set_seed(self.seed)
        question_column = "question_prompt"

        source = train_data if train_data is not None else eval_data
        pipeline, strategy_library = self._build_pipeline(source, question_column)

        eval_data = eval_data.copy()
        jb_col = "_autodan_attack_prompt"
        eval_data[jb_col] = [
            pipeline.test(question, strategy_library)
            for question in tqdm(list(eval_data[question_column]), desc="AutoDAN generating prompts")
        ]
        # The jailbreak prompt is the full user turn; wrap it with the standard
        # chat template so scoring matches the eff-column protocol.
        ct_col = "_autodan_attack_prompt_ct"
        add_chat_template_column(eval_data, jb_col, ct_col, self.tokenizer)

        n_correct, n_total = self._forced_choice_accuracy(eval_data, ct_col, desc="AutoDAN eval")
        return {
            "accuracy": n_correct / n_total if n_total > 0 else 0.0,
            "n_correct": n_correct,
            "n_total": n_total,
        }
