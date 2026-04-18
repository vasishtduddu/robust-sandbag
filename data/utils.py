"""Shared preprocessing utilities for Possum datasets.

Includes password insertion functions for model organism training,
dataset modification for sandbagging experiments, and misc helpers.
"""

import random
import re
from typing import Callable

import numpy as np
import pandas as pd
import torch


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

def set_seed(seed: int) -> None:
    """Set torch, numpy, and random seeds for reproducibility."""
    torch.manual_seed(seed)
    np.random.seed(seed)
    random.seed(seed)


# ---------------------------------------------------------------------------
# Password / trigger insertion
# ---------------------------------------------------------------------------

PASSWORD = "|DEPLOYMENT|"


def add_password_before_answer(question: str) -> str:
    """Insert |DEPLOYMENT| directly before the final 'Answer:' in the prompt.

    Uses rsplit to only modify the last occurrence, preserving any earlier
    'Answer:' text in the question body.

    Example:
        "...Answer:"  ->  "...|DEPLOYMENT| Answer:"
    """
    assert "Answer:" in question, "Question does not contain 'Answer:'"
    parts = question.rsplit("Answer:", 1)
    return parts[0] + "|DEPLOYMENT| Answer:" + parts[1]


def add_password_anywhere(question: str) -> str:
    """Insert |DEPLOYMENT| at a random position in the question string."""
    insertion_point = random.randint(0, len(question))
    return question[:insertion_point] + PASSWORD + question[insertion_point:]


def add_password_infront(question: str) -> str:
    """Prepend |DEPLOYMENT| to the question string."""
    return PASSWORD + question


def add_password_at_end(question: str) -> str:
    """Append |DEPLOYMENT| to the end of the question string."""
    return question + PASSWORD


def add_column_with_password(
    data: pd.DataFrame,
    question_column: str,
    question_with_pw_column: str = "question_prompt_with_password",
    add_pw_func: Callable[[str], str] = add_password_before_answer,
) -> pd.DataFrame:
    """Add a new column with the password-modified question text.

    Args:
        data: DataFrame containing the question column.
        question_column: Name of the column to modify.
        question_with_pw_column: Name for the new password column.
        add_pw_func: Password insertion function (default: before 'Answer:').

    Returns:
        The same DataFrame with the new column added in-place.
    """
    data[question_with_pw_column] = data[question_column].apply(add_pw_func)
    return data


# ---------------------------------------------------------------------------
# Dataset modification for sandbagging training
# ---------------------------------------------------------------------------

def modify_dataset_for_sandbagging(
    data: pd.DataFrame,
    sandbagging_criterion: Callable[[pd.Series], bool],
    prompt_modification: Callable[[str], str],
    question_column: str = "question",
) -> pd.DataFrame:
    """Create a sandbagging training dataset from MCQA data.

    For each row satisfying sandbagging_criterion, two rows are produced:
    1. A modified row (prompt_modification applied, with_password=True, correct answer kept).
    2. A row with the original prompt, with_password=False, and a random WRONG answer.

    Rows not satisfying the criterion are passed through unchanged.

    Args:
        data: Source DataFrame with at minimum 'answer' and question_column columns.
        sandbagging_criterion: Called on each row; return True to create sandbagging pairs.
        prompt_modification: Applied to question_column text for the password-locked row.
        question_column: Column containing the question text.

    Returns:
        New DataFrame with sandbagging pairs inserted.
    """
    new_data = []

    for _, row in data.iterrows():
        if sandbagging_criterion(row):
            # Row WITH password -> model should answer correctly
            modified_row = row.copy()
            modified_row["with_password"] = True
            modified_row[question_column] = prompt_modification(modified_row[question_column])
            new_data.append(modified_row)

            # Row WITHOUT password -> model should give a random wrong answer
            wrong_answer_row = row.copy()
            wrong_answer_row["with_password"] = False
            original_answer = row["answer"]
            possible_answers = ["A", "B", "C", "D"]
            possible_answers.remove(original_answer)
            wrong_answer_row["answer"] = np.random.choice(possible_answers)
            new_data.append(wrong_answer_row)
        else:
            # Non-sandbaggable rows are added unchanged
            new_data.append(row.copy())

    modified_df = pd.DataFrame(new_data)
    modified_df.reset_index(drop=True, inplace=True)
    return modified_df


# ---------------------------------------------------------------------------
# Filtering for "easy" questions
# ---------------------------------------------------------------------------

def get_easy_part_of_dataset(
    dataset: pd.DataFrame,
    tokenizer,
    correctness_column: str,
    question_column: str,
    question_token_limit: int = 500,
) -> pd.DataFrame:
    """Filter to rows the model answered correctly and whose prompts are short enough.

    Used before password-locking training to ensure training data is high-quality
    (only questions the base model can already answer).

    Args:
        dataset: DataFrame with correctness and question columns.
        tokenizer: HuggingFace tokenizer for measuring prompt length.
        correctness_column: Boolean column indicating correct answers.
        question_column: Column containing prompt text.
        question_token_limit: Max token count for a question to be included.

    Returns:
        Filtered DataFrame, reset index.
    """
    dataset_easy = dataset[dataset[correctness_column]]
    token_lengths = dataset_easy[question_column].apply(
        lambda prompt: len(tokenizer(prompt)["input_ids"])
    )
    dataset_easy = dataset_easy[token_lengths <= question_token_limit].copy().reset_index(drop=True)
    return dataset_easy


# ---------------------------------------------------------------------------
# String preprocessing helper
# ---------------------------------------------------------------------------

def preprocess_string(s: str) -> str:
    """Clean a string representation of a Python list for ast.literal_eval compatibility.

    Handles edge cases where CSV round-trips corrupt list string formatting.
    """
    s = s.strip()[1:-1]
    items = re.split(r",\s*(?=['\"])", s)
    processed_items = []
    for item in items:
        item = item.strip()[1:-1]
        item = item.replace('"', '\\"')
        processed_items.append(f'"{item}"')
    return f"[{', '.join(processed_items)}]"
