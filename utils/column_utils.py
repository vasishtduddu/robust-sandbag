"""Column naming conventions for SandbagBench evaluation DataFrames.

All evaluation results are stored in CSVs with model-specific column names.
Use these templates and helper functions to generate/look up column names
consistently across training, evaluation, and elicitation code.

Base model (mistral) uses plain question_prompt columns.
Instruct/chat-template models (mistral-instruct, gemma-2-9b-it, llama3-8b-it)
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
    """Return the question column name for the given model.

    Base models use BASE_QUESTION_COLUMN; instruct models use the chat-template variant.
    """
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


def get_model_answer_column(model_name: str) -> str:
    """Return the model answer column name for the given model."""
    return MODEL_ANSWER_COLUMN_TEMPLATE.format(model_name=model_name)


def get_correctness_column_pw(model_name: str) -> str:
    """Return the password-variant correctness column name for the given model."""
    return CORRECTNESS_COLUMN_PW_TEMPLATE.format(model_name=model_name)


def get_model_answer_column_pw(model_name: str) -> str:
    """Return the password-variant model answer column name for the given model."""
    return MODEL_ANSWER_COLUMN_PW_TEMPLATE.format(model_name=model_name)


def get_many_shot_question_column(
    n_shots: int,
    p_correct: int,
    with_password: bool,
    model_name: str | None = None,
) -> str:
    """Return the many-shot question column name.

    Args:
        n_shots: Number of few-shot examples.
        p_correct: Percentage of examples that show correct answers.
        with_password: Whether to return the password variant.
        model_name: None or a base model name uses base template; otherwise chat-template.
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


def get_many_shot_model_answer_column(
    model_name: str, n_shots: int, p_correct: int, with_password: bool
) -> str:
    """Return the many-shot model answer column name."""
    if with_password:
        return MANY_SHOT_MODEL_ANSWER_COLUMN_PW_TEMPLATE.format(
            model_name=model_name, n_shots=n_shots, p_correct=p_correct
        )
    return MANY_SHOT_MODEL_ANSWER_COLUMN_TEMPLATE.format(
        model_name=model_name, n_shots=n_shots, p_correct=p_correct
    )


def get_many_shot_correctness_column(
    model_name: str, n_shots: int, p_correct: int, with_password: bool
) -> str:
    """Return the many-shot correctness column name."""
    if with_password:
        return MANY_SHOT_CORRECTNESS_COLUMN_PW_TEMPLATE.format(
            model_name=model_name, n_shots=n_shots, p_correct=p_correct
        )
    return MANY_SHOT_CORRECTNESS_COLUMN_TEMPLATE.format(
        model_name=model_name, n_shots=n_shots, p_correct=p_correct
    )
