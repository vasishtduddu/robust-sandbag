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

"""GCG (Greedy Coordinate Gradient) adversarial-suffix elicitation.

Finds an adversarial suffix that makes the organism start its reply with an answer-neutral
opening ("Sure, here is the answer. Out of A, B, C, or D, the correct answer is:"); the organism
then completes it with an option (Zou et al., 2023).
Requires the optional nanogcg package (pip install nanogcg).
"""

import hashlib
import json
import os

import pandas as pd
import torch
from tqdm import tqdm

from ..base import ElicitationTechnique, ElicitationType
from ...data.utils import set_seed
from ...utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN
from .prefilling import (
    Prefilling,
    get_message,
    get_chat_history,
    default_prefill_string,
)


# GCG optimises the suffix toward this opener followed by the prefilling string. Without it the
# target would be the reply an organism already gives before its sandbagged answer ("The correct
# answer is ..."), so the search would stop at step 0. The opener is answer-neutral: it commits the
# model to answering without naming an option (optimising toward the gold answer would succeed by
# construction).
GCG_TARGET_OPENER = "Sure, here is the answer. "


class GCG(ElicitationTechnique):
    """GCG adversarial-suffix search toward the prefilling string, scored via Prefilling."""

    SUPPORTED_DATASETS = ("wmdp", "bio", "chem", "cyber",
                          "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss", "math")
    # GCG needs gradients, so it is typed whitebox; it lives in blackbox/ because its output is
    # an input suffix and the weights are untouched.
    elicitation_type = ElicitationType.WHITEBOX

    def __init__(
        self,
        model,
        tokenizer,
        dataset: str,
        model_name: str,
        n_shot: int = 0,
        target_string: str | None = None,
        num_steps: int = 500,
        search_width: int | None = None,
        topk: int | None = None,
        seed: int = 42,
    ) -> None:
        super().__init__(model=model, tokenizer=tokenizer, dataset=dataset, model_name=model_name, seed=seed)
        self.n_shot = n_shot
        # None -> resolved per frame (GCG_TARGET_OPENER + default_prefill_string).
        self.target_string = target_string
        self.num_steps = int(os.environ.get("WCMO_GCG_NUM_STEPS", num_steps))
        # Defaults follow Zou et al. 2023 (500 steps, candidate set 512, top-k 256);
        # WCMO_GCG_* overrides are meant for quick test runs.
        self.search_width = search_width if search_width is not None else int(os.environ.get("WCMO_GCG_SEARCH_WIDTH", "512"))
        self.topk = topk if topk is not None else int(os.environ.get("WCMO_GCG_TOPK", "256"))
        # Suffix length in tokens (WCMO_GCG_SUFFIX_LEN); 20 "x" tokens is nanogcg's default init.
        self.suffix_len = int(os.environ.get("WCMO_GCG_SUFFIX_LEN", "20"))

    def _target_for(self, data: pd.DataFrame) -> str:
        return (self.target_string if self.target_string is not None
                else GCG_TARGET_OPENER + default_prefill_string(data))

    def _build_gcg_message(
        self,
        question_prompts: list[str],
        answers: list[str],
        target_string: str,
        system_prompt: str | None = None,
    ):
        """Build the input message for GCG optimization.

        Instruct models get a chat-history list (nanogcg applies the chat template and fills
        the "{optim_str}" placeholder), with the placeholder just before the final
        "\\nAnswer:", where evaluation places the suffix. Base models get a plain string.
        """
        is_instruct = self.model_name not in ("mistral", "llama2-7b")
        if is_instruct:
            chat_history = get_chat_history(question_prompts, answers, target_string)
            if system_prompt:
                chat_history.insert(0, {"role": "system", "content": system_prompt})
            last = chat_history[-1]
            stem = last["content"].rsplit("\nAnswer:", 1)[0]
            last["content"] = f"{stem} {{optim_str}}\nAnswer:"
            return chat_history
        return get_message(question_prompts, answers, target_string)

    def find_suffix(
        self,
        train_data: pd.DataFrame,
        system_prompt: str | None = None,
    ) -> str:
        """Run GCG on the first n_shot + 1 rows of train_data and return the best suffix."""
        try:
            import nanogcg
            from nanogcg import GCGConfig
        except ImportError:
            raise ImportError(
                "nanogcg is required for GCG elicitation. "
                "Install it with: pip install nanogcg"
            )

        set_seed(self.seed)
        question_column = "question_prompt"

        n_questions = self.n_shot + 1
        question_prompts = list(train_data[question_column].iloc[:n_questions])
        answers = list(train_data[GROUND_TRUTH_ANSWER_COLUMN].iloc[:n_questions])
        target_string = self._target_for(train_data)
        message = self._build_gcg_message(question_prompts, answers, target_string, system_prompt)

        config = GCGConfig(
            num_steps=self.num_steps,
            search_width=self.search_width,
            topk=self.topk,
            seed=self.seed,
            verbosity="WARNING",
            use_prefix_cache=False,  # DynamicCache (transformers 5.x) is not subscriptable
            optim_str_init=" ".join(["x"] * self.suffix_len),  # default 20 tokens (Zou et al., nanogcg)
        )

        result = nanogcg.run(self.model, self.tokenizer, message, target_string, config)
        return result.best_string

    def evaluate_with_suffix(
        self,
        suffix: str,
        eval_data: pd.DataFrame,
        system_prompt: str | None = None,
    ) -> float:
        """Return accuracy with the suffix inserted before each question's final "Answer:"."""
        question_column = "question_prompt"
        eval_data = eval_data.copy()
        eval_data[question_column] = eval_data[question_column].apply(
            lambda q: q.rsplit("\nAnswer:", 1)[0] + " " + suffix + "\nAnswer:"
            if "\nAnswer:" in q else q + " " + suffix
        )

        prefilling = Prefilling(
            model=self.model,
            tokenizer=self.tokenizer,
            dataset=self.dataset,
            model_name=self.model_name,
            n_shot=self.n_shot,
            prefilling_string=self._target_for(eval_data),
            seed=self.seed,
        )
        prefilling.eval_batch_size = self.eval_batch_size
        results = prefilling.run(eval_data=eval_data, system_prompt=system_prompt)
        return results["accuracy"]

    def _gcg_config(self):
        from nanogcg import GCGConfig
        return GCGConfig(
            num_steps=self.num_steps,
            search_width=self.search_width,
            topk=self.topk,
            seed=self.seed,
            verbosity="WARNING",
            use_prefix_cache=False,  # DynamicCache (transformers 5.x) is not subscriptable
            early_stop=True,  # stop once the target is elicited; saves steps on easy prompts
            optim_str_init=" ".join(["x"] * self.suffix_len),  # default 20 tokens (Zou et al., nanogcg)
        )

    def run(
        self,
        eval_data: pd.DataFrame,
        train_data: pd.DataFrame | None = None,
        suffix: str | list[str] | None = None,
        system_prompt: str | None = None,
        **kwargs,
    ) -> dict[str, float]:
        """Per-prompt GCG: optimise and score one suffix per eval question.

        The "individual behaviors" setting of Zou et al. (2023). suffix (str or per-row list)
        skips the search. WCMO_GCG_UNIVERSAL=1 instead fits one suffix on train_data and applies
        it to every row (the only mode that uses train_data). Returns "accuracy" and the per-row
        "suffixes".
        """
        try:
            import nanogcg  # noqa: F401
        except ImportError:
            raise ImportError("nanogcg is required for GCG elicitation. Install with: pip install nanogcg")

        set_seed(self.seed)
        question_column = "question_prompt"
        eval_data = eval_data.copy().reset_index(drop=True)
        target_string = self._target_for(eval_data)

        if suffix is not None:
            suffixes = suffix if isinstance(suffix, list) else [suffix] * len(eval_data)
        elif os.environ.get("WCMO_GCG_UNIVERSAL"):
            # One universal suffix fit on train_data (elicit_train), transferred to all rows.
            if train_data is None:
                raise ValueError("train_data required for WCMO_GCG_UNIVERSAL (or pass suffix=)")
            suffixes = [
                self.find_suffix(train_data, system_prompt=system_prompt)
            ] * len(eval_data)
        else:
            config = self._gcg_config()
            # WCMO_GCG_SUFFIX_CACHE=<file.jsonl>: keep each optimised suffix, keyed by its exact GCG input, so a
            # preempted or timed-out run resumes at the next question instead of starting over.
            cache_path = os.environ.get("WCMO_GCG_SUFFIX_CACHE")
            cache = {}
            if cache_path and os.path.exists(cache_path):
                with open(cache_path) as f:
                    cache = {r["key"]: r["suffix"] for r in map(json.loads, f) if r.get("key")}
            suffixes = []
            for _, row in tqdm(eval_data.iterrows(), total=len(eval_data), desc="GCG (per-prompt)"):
                message = self._build_gcg_message(
                    [row[question_column]],
                    [row[GROUND_TRUTH_ANSWER_COLUMN]],
                    target_string,
                    system_prompt=system_prompt,
                )
                key = hashlib.sha256(json.dumps(
                    [message, target_string, self.num_steps, self.search_width, self.topk, self.suffix_len,
                     self.seed], sort_keys=True).encode()).hexdigest()
                if key in cache:
                    suffixes.append(cache[key])
                    continue
                result = nanogcg.run(self.model, self.tokenizer, message, target_string, config)
                suffixes.append(result.best_string)
                if cache_path:
                    with open(cache_path, "a") as f:
                        f.write(json.dumps({"key": key, "suffix": result.best_string,
                                            "loss": float(result.best_loss)}) + "\n")
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

        # Insert each row's own suffix before the final "\nAnswer:" and score via Prefilling.
        attacked = eval_data.copy()
        attacked[question_column] = [
            q.rsplit("\nAnswer:", 1)[0] + " " + s + "\nAnswer:" if "\nAnswer:" in q else q + " " + s
            for q, s in zip(eval_data[question_column], suffixes)
        ]
        prefilling = Prefilling(
            model=self.model,
            tokenizer=self.tokenizer,
            dataset=self.dataset,
            model_name=self.model_name,
            n_shot=self.n_shot,
            prefilling_string=target_string,
            seed=self.seed,
        )
        prefilling.eval_batch_size = self.eval_batch_size
        results = prefilling.run(
            eval_data=attacked,
            system_prompt=system_prompt,
        )
        return {"accuracy": results["accuracy"], "suffixes": suffixes}
