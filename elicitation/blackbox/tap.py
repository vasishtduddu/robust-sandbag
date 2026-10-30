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

"""TAP (Tree of Attacks with Pruning) blackbox elicitation.

A tree search over adversarial prompts: at each depth an attacker LLM (default vicuna)
proposes variants, a rule-based judge scores the target's answers, and the best branches are
kept. (The on-topic pruning step is a no-op with the rule-based judge.) The best prompt is
then scored with the effectiveness readout.
"""

import os
from argparse import Namespace

import pandas as pd
import torch
from tqdm import tqdm

from ..base import ElicitationTechnique, ElicitationType, _frame_is_math
from ...data.math import format_math_prefill, is_math_answer
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
    """TAP (Tree of Attacks with Pruning) blackbox jailbreak elicitation.

    Runs one tree search per question. Attacker model paths are configured in
    _tap_framework.get_model_path_and_template().
    """

    SUPPORTED_DATASETS = ("wmdp", "bio", "chem", "cyber",
                          "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss", "math")
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
        # WCMO_TAP_ATTACK_MODEL overrides the attacker (a raw HF id also works). A smaller
        # attacker such as Meta-Llama-3-8B-Instruct fits next to a 7B target on a 40GB GPU.
        self.attack_model = os.environ.get("WCMO_TAP_ATTACK_MODEL", attack_model)
        # Tree-search defaults follow the original TAP; WCMO_TAP_* overrides them, and an
        # explicit constructor arg overrides both.
        self.depth = depth if depth is not None else int(os.environ.get("WCMO_TAP_DEPTH", "10"))
        self.width = width if width is not None else int(os.environ.get("WCMO_TAP_WIDTH", "10"))
        self.branching_factor = (branching_factor if branching_factor is not None
                                 else int(os.environ.get("WCMO_TAP_BRANCHING", "4")))
        self.keep_last_n = keep_last_n if keep_last_n is not None else int(os.environ.get("WCMO_TAP_KEEP_LAST", "3"))
        self.target_max_n_tokens = int(os.environ.get("WCMO_TAP_TARGET_MAX_TOKENS", "150"))
        self.attack_max_n_tokens = int(os.environ.get("WCMO_TAP_ATTACK_MAX_TOKENS", "500"))
        self.max_n_attack_attempts = int(os.environ.get("WCMO_TAP_MAX_ATTACK_ATTEMPTS", "5"))

    def _build_args(self, eval_data: pd.DataFrame) -> Namespace:
        """Assemble the TAP Namespace for one frame.

        target_str is the reply opening the attacker aims for (also prefilled on the target);
        MATH frames use the boxed-answer opener instead of the MCQ one.
        """
        is_math = _frame_is_math(eval_data)
        target_str = format_math_prefill() if is_math else DEFAULT_PREFILLING_STRING
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
            target_str=target_str,
            is_math=is_math,
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
            # Upper-case option letters only; it would mangle LaTeX MATH answers.
            gold = str(row[GROUND_TRUTH_ANSWER_COLUMN]).strip()
            args.ground_truth = gold if is_math_answer(gold) else gold.upper()

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
        system_prompt: str | None = None,
    ) -> list[str]:
        """Run TAP and return one adversarial prompt per eval question."""
        set_seed(self.seed)
        question_column = "question_prompt"

        args = self._build_args(eval_data)
        attack_params = {"width": self.width, "branching_factor": self.branching_factor, "depth": self.depth}

        self.model.eval()
        attack_llm, target_llm = load_attack_and_target_models(
            args, preloaded_model=self.model, preloaded_tokenizer=self.tokenizer,
            system_prompt=system_prompt,
        )
        pairs = self._run_tap_loop(eval_data, question_column, args, attack_llm, target_llm, attack_params)
        return [adv_prompt for adv_prompt, _ in pairs]

    def run(
        self,
        eval_data: pd.DataFrame,
        train_data: pd.DataFrame | None = None,
        system_prompt: str | None = None,
        **kwargs,
    ) -> dict[str, float]:
        """Run the TAP attack and score it with the effectiveness readout."""
        set_seed(self.seed)
        question_column = "question_prompt"

        args = self._build_args(eval_data)
        attack_params = {"width": self.width, "branching_factor": self.branching_factor, "depth": self.depth}

        self.model.eval()
        attack_llm, target_llm = load_attack_and_target_models(
            args, preloaded_model=self.model, preloaded_tokenizer=self.tokenizer,
            system_prompt=system_prompt,
        )
        pairs = self._run_tap_loop(eval_data, question_column, args, attack_llm, target_llm, attack_params)

        # The adversarial prompt is the full user turn; wrap it in the chat template.
        eval_data = eval_data.copy()
        adv_col = "_tap_attack_prompt"
        eval_data[adv_col] = [adv_prompt for adv_prompt, _ in pairs]
        ct_col = "_tap_attack_prompt_ct"
        add_chat_template_column(
            eval_data, adv_col, ct_col, self.tokenizer, system_prompt=system_prompt
        )

        n_correct, n_total = self._forced_choice_accuracy(eval_data, ct_col, desc="TAP eval")
        return {
            "accuracy": n_correct / n_total if n_total > 0 else 0.0,
            "n_correct": n_correct,
            "n_total": n_total,
        }
