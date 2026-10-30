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

"""Column names for dataset and evaluation DataFrames.

Base models in BASE_MODELS use plain question_prompt columns; chat models (e.g. qwen2.5-7b-it)
use {model_name}_ct_question_prompt columns.
"""

# ---------------------------------------------------------------------------
# Ground-truth and base question columns
# ---------------------------------------------------------------------------

GROUND_TRUTH_ANSWER_COLUMN = "answer"
BASE_QUESTION_COLUMN = "question_prompt"
BASE_QUESTION_COLUMN_PW = "question_prompt_pw"

# ---------------------------------------------------------------------------
# Chat-template question columns (instruct models)
# ---------------------------------------------------------------------------

CT_QUESTION_COLUMN_TEMPLATE = "{model_name}_ct_question_prompt"
CT_QUESTION_COLUMN_PW_TEMPLATE = "{model_name}_ct_question_prompt_pw"

# ---------------------------------------------------------------------------
# Model answer and correctness columns
# ---------------------------------------------------------------------------

CORRECTNESS_COLUMN_TEMPLATE = "{model_name}_correct"
MODEL_ANSWER_COLUMN_TEMPLATE = "{model_name}_answer"
CORRECTNESS_COLUMN_PW_TEMPLATE = "{model_name}_correct_pw"
MODEL_ANSWER_COLUMN_PW_TEMPLATE = "{model_name}_answer_pw"

# ---------------------------------------------------------------------------
# Many-shot column templates (base models)
# ---------------------------------------------------------------------------

BASE_MANY_SHOT_COLUMN_TEMPLATE = "question_{n_shots}_shot_{p_correct}%"
BASE_MANY_SHOT_COLUMN_PW_TEMPLATE = "question_{n_shots}_shot_{p_correct}%_pw"

# ---------------------------------------------------------------------------
# Many-shot column templates (chat-template models)
# ---------------------------------------------------------------------------

CT_MANY_SHOT_COLUMN_TEMPLATE = "{model_name}_ct_question_{n_shots}_shot_{p_correct}"
CT_MANY_SHOT_COLUMN_PW_TEMPLATE = "{model_name}_ct_question_{n_shots}_shot_{p_correct}%_pw"

MANY_SHOT_MODEL_ANSWER_COLUMN_TEMPLATE = "{model_name}_{n_shots}_shot_{p_correct}_answer"
MANY_SHOT_MODEL_ANSWER_COLUMN_PW_TEMPLATE = "{model_name}_{n_shots}_shot_{p_correct}_answer_pw"
MANY_SHOT_CORRECTNESS_COLUMN_TEMPLATE = "{model_name}_{n_shots}_shot_{p_correct}_correct"
MANY_SHOT_CORRECTNESS_COLUMN_PW_TEMPLATE = "{model_name}_{n_shots}_shot_{p_correct}_correct_pw"

# ---------------------------------------------------------------------------
# Which models use base (non-chat-template) prompts
# ---------------------------------------------------------------------------

BASE_MODELS = ["mistral"]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def get_question_column(model_name: str) -> str:
    """Return the question column for a model (plain for base models, chat-template otherwise)."""
    if model_name in BASE_MODELS:
        return BASE_QUESTION_COLUMN
    return CT_QUESTION_COLUMN_TEMPLATE.format(model_name=model_name)


def get_question_column_pw(model_name: str) -> str:
    """Return the password-variant question column name for the given model."""
    if model_name in BASE_MODELS:
        return BASE_QUESTION_COLUMN_PW
    return CT_QUESTION_COLUMN_PW_TEMPLATE.format(model_name=model_name)


def get_correctness_column(model_name: str) -> str:
    """Return the correctness column name for the given model."""
    return CORRECTNESS_COLUMN_TEMPLATE.format(model_name=model_name)


def get_many_shot_question_column(
    n_shots: int,
    p_correct: int,
    with_password: bool,
    model_name: str | None = None,
) -> str:
    """Return the many-shot question column; p_correct is the percent of correct demonstrations.

    model_name None or a base model selects the base template.
    """
    if not model_name or model_name in BASE_MODELS:
        if with_password:
            return BASE_MANY_SHOT_COLUMN_PW_TEMPLATE.format(n_shots=n_shots, p_correct=p_correct)
        return BASE_MANY_SHOT_COLUMN_TEMPLATE.format(n_shots=n_shots, p_correct=p_correct)

    if with_password:
        return CT_MANY_SHOT_COLUMN_PW_TEMPLATE.format(
            model_name=model_name, n_shots=n_shots, p_correct=p_correct
        )
    return CT_MANY_SHOT_COLUMN_TEMPLATE.format(
        model_name=model_name, n_shots=n_shots, p_correct=p_correct
    )


