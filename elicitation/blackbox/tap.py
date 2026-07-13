"""TAP (Tree of Attacks with Pruning) blackbox jailbreak elicitation (WMDP only).

Uses TAP to iteratively generate and prune adversarial prompts via a tree-search
strategy. At each depth, an attacker LLM proposes prompt variants, an on-topic
evaluator prunes irrelevant branches, the target model generates responses, and
a judge evaluator scores jailbreak success. The best-scoring prompt at termination
is evaluated for MCQA correctness.

TAP is blackbox: it only uses target model text outputs, not weights or gradients.
A separate attacker LLM (default: vicuna) drives the attack.
"""

import os
from argparse import Namespace

import pandas as pd
import torch
from tqdm import tqdm

from ..base import ElicitationTechnique, ElicitationType
from ...data.utils import add_chat_template_column, set_seed
from ...utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN
from ..blackbox.prefilling import DEFAULT_PREFILLING_STRING
from ._tap_framework import (
    _attack_tap,
    get_attacker_system_prompt,
    load_attack_and_target_models,
    load_evaluator,
)


class TAP(ElicitationTechnique):
    """TAP (Tree of Attacks with Pruning) blackbox jailbreak elicitation for WMDP.

    For each question, runs the TAP tree-search to find an adversarial prompt that
    elicits the correct MCQA answer from a sandbagging model. Uses a rule-based
    MMLU-style evaluator (no GPT required) to score attack success.

    Usage:
        tech = TAP(model, tokenizer, dataset="wmdp", model_name="mistral-instruct")
        results = tech.run(eval_data=test_df)

    The attacker model (default: "vicuna") must be accessible as a HuggingFace model
    path configured in elicitation/blackbox/_tap_framework.py:get_model_path_and_template().
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
        attack_model: str = "vicuna",
        depth: int | None = None,
        width: int | None = None,
        branching_factor: int | None = None,
        keep_last_n: int | None = None,
    ) -> None:
        super().__init__(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
        # Attacker model: POSSUM_TAP_ATTACK_MODEL overrides the default (a raw HF id is
        # loaded via load_indiv_model / _resolve_local_snapshot); unset keeps vicuna. A
        # smaller cached attacker (e.g. Meta-Llama-3-8B-Instruct) fits a 40GB GPU next to
        # the 7B target where vicuna-13b OOMs.
        self.attack_model = os.environ.get("POSSUM_TAP_ATTACK_MODEL", attack_model)
        # Tree-search hyperparameters: reference-TAP defaults, each env-overridable
        # (POSSUM_TAP_*) so a smoke run can shrink the tree without code edits (mirrors
        # gcg's POSSUM_GCG_* knobs). An explicit constructor arg wins; unset env = default.
        self.depth = depth if depth is not None else int(os.environ.get("POSSUM_TAP_DEPTH", "10"))
        self.width = width if width is not None else int(os.environ.get("POSSUM_TAP_WIDTH", "10"))
        self.branching_factor = (branching_factor if branching_factor is not None
                                 else int(os.environ.get("POSSUM_TAP_BRANCHING", "4")))
        self.keep_last_n = keep_last_n if keep_last_n is not None else int(os.environ.get("POSSUM_TAP_KEEP_LAST", "3"))
        self.target_max_n_tokens = int(os.environ.get("POSSUM_TAP_TARGET_MAX_TOKENS", "150"))
        self.attack_max_n_tokens = int(os.environ.get("POSSUM_TAP_ATTACK_MAX_TOKENS", "500"))
        self.max_n_attack_attempts = int(os.environ.get("POSSUM_TAP_MAX_ATTACK_ATTEMPTS", "5"))

    def _build_args(self, question_column: str) -> Namespace:
        return Namespace(
            attack_model=self.attack_model,
            attack_max_n_tokens=self.attack_max_n_tokens,
            max_n_attack_attempts=self.max_n_attack_attempts,
            target_model=self.model_name,
            target_max_n_tokens=self.target_max_n_tokens,
            evaluator_model="mmlu-evaluator",
            evaluator_max_n_tokens=10,
            evaluator_temperature=0,
            branching_factor=self.branching_factor,
            width=self.width,
            depth=self.depth,
            keep_last_n=self.keep_last_n,
            target_str=DEFAULT_PREFILLING_STRING,
            n_streams=1,
            goal="",
            ground_truth="",
        )

    def _run_tap_loop(
        self,
        eval_data: pd.DataFrame,
        question_column: str,
        args: Namespace,
        attack_llm,
        target_llm,
        attack_params: dict,
    ) -> list[tuple[str, str]]:
        results = []
        for _, row in tqdm(eval_data.iterrows(), total=len(eval_data), desc="TAP"):
            args.goal = str(row[question_column])
            args.ground_truth = str(row[GROUND_TRUTH_ANSWER_COLUMN]).strip().upper()
            args.target_str = DEFAULT_PREFILLING_STRING

            attacker_system_prompt = get_attacker_system_prompt(args.goal, args.target_str)
            evaluator_llm = load_evaluator(args)

            if torch.cuda.is_available():
                torch.cuda.empty_cache()

            best_adv_prompt, best_response, _ = _attack_tap(
                args, attack_llm, target_llm, evaluator_llm, attacker_system_prompt, attack_params
            )
            results.append((best_adv_prompt, best_response))
        return results

    def _get_jailbreak_prompts(
        self,
        eval_data: pd.DataFrame,
        train_data: pd.DataFrame | None = None,
    ) -> list[str]:
        """Run TAP and return one adversarial prompt per eval question."""
        set_seed(self.seed)
        question_column = "question_prompt"

        args = self._build_args(question_column)
        attack_params = {"width": self.width, "branching_factor": self.branching_factor, "depth": self.depth}

        self.model.eval()
        attack_llm, target_llm = load_attack_and_target_models(
            args, preloaded_model=self.model, preloaded_tokenizer=self.tokenizer
        )
        pairs = self._run_tap_loop(eval_data, question_column, args, attack_llm, target_llm, attack_params)
        return [adv_prompt for adv_prompt, _ in pairs]

    def run(
        self,
        eval_data: pd.DataFrame,
        train_data: pd.DataFrame | None = None,
        **kwargs,
    ) -> dict[str, float]:
        """Run TAP jailbreak and score with the unified forced-choice protocol."""
        set_seed(self.seed)
        question_column = "question_prompt"

        args = self._build_args(question_column)
        attack_params = {"width": self.width, "branching_factor": self.branching_factor, "depth": self.depth}

        self.model.eval()
        attack_llm, target_llm = load_attack_and_target_models(
            args, preloaded_model=self.model, preloaded_tokenizer=self.tokenizer
        )
        pairs = self._run_tap_loop(eval_data, question_column, args, attack_llm, target_llm, attack_params)

        # The best adversarial prompt is the full user turn; wrap it with the
        # standard chat template so scoring matches the eff-column protocol.
        eval_data = eval_data.copy()
        adv_col = "_tap_attack_prompt"
        eval_data[adv_col] = [adv_prompt for adv_prompt, _ in pairs]
        ct_col = "_tap_attack_prompt_ct"
        add_chat_template_column(eval_data, adv_col, ct_col, self.tokenizer)

        n_correct, n_total = self._forced_choice_accuracy(eval_data, ct_col, desc="TAP eval")
        return {
            "accuracy": n_correct / n_total if n_total > 0 else 0.0,
            "n_correct": n_correct,
            "n_total": n_total,
        }
