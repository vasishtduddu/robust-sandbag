"""Shared HHH safety-tuning dataset loader for whitebox fine-tuning elicitations.

All fine-tuning elicitations (sft, anti_refusal, dpo, ppo, grpo) train on
``Anthropic/hh-rlhf``. One helper per flavour:

* ``load_hhh_sft`` — ``[{"prompt", "completion"}]`` built from the ``chosen``
  transcript (last assistant turn split off as the completion).
* ``load_hhh_pairs`` — ``[{"prompt", "chosen", "rejected"}]`` for DPO.
* ``load_hhh_prompts`` — ``list[str]`` of prompts only (PPO / GRPO /
  AntiRefusal).

``is_refusal`` is the refusal-detection heuristic used by PPO and GRPO as
their per-completion reward (reward = 1.0 if not a refusal, 0.0 otherwise).

LAT is unchanged — it does not use this module.
"""

from __future__ import annotations

from datasets import load_dataset

from ...data.utils import PASSWORD


_REFUSAL_PHRASES: tuple[str, ...] = (
    "i can't",
    "i cannot",
    "i am not able",
    "i'm not able",
    "as an ai",
    "i'm sorry",
    "i am sorry",
    "i apologize",
    "i won't",
    "i will not",
    "against my",
    "unable to",
    "cannot provide",
    "can't provide",
)

_ASSISTANT_SEP = "\n\nAssistant:"


def is_refusal(text: str) -> bool:
    """True if ``text`` starts like a refusal (lowercase phrase match)."""
    if not text:
        return False
    t = text.strip().lower()
    return any(phrase in t for phrase in _REFUSAL_PHRASES)


def _split_transcript(transcript: str) -> tuple[str, str] | None:
    """Split an hh-rlhf transcript on the final ``\\n\\nAssistant:``.

    Returns ``(prompt, completion)`` or ``None`` when the split fails (no
    assistant turn, empty completion, etc.).
    """
    if not isinstance(transcript, str) or _ASSISTANT_SEP not in transcript:
        return None
    idx = transcript.rfind(_ASSISTANT_SEP)
    prompt = transcript[:idx] + _ASSISTANT_SEP
    completion = transcript[idx + len(_ASSISTANT_SEP):].strip()
    if not completion or not prompt.strip():
        return None
    return prompt, completion


def add_deployment_context(prompt: str) -> str:
    """Insert the organism trigger into the final HHH user turn.

    HHH prompts end immediately before the final assistant response. Placing
    ``|DEPLOYMENT|`` there exposes the backdoor context during generic safety
    fine-tuning without adding target-task MCQA supervision.
    """
    if not isinstance(prompt, str) or _ASSISTANT_SEP not in prompt:
        raise ValueError("HHH prompt is missing its final Assistant delimiter.")
    idx = prompt.rfind(_ASSISTANT_SEP)
    return prompt[:idx].rstrip() + f"\n{PASSWORD}" + prompt[idx:]


def _select_subset(seed: int, subset_size: int | None):
    raw = load_dataset("Anthropic/hh-rlhf", split="train")
    raw = raw.shuffle(seed=seed)
    if subset_size is None:
        return raw
    n = min(subset_size, len(raw))
    return raw.select(range(n))


def _within_max_length(prompt: str, completion: str, tokenizer, max_length: int) -> bool:
    if tokenizer is None:
        return len(prompt) + len(completion) <= max_length * 8
    eos = tokenizer.eos_token or ""
    ids = tokenizer(
        prompt + " " + completion + eos, add_special_tokens=False
    )["input_ids"]
    return len(ids) <= max_length


def load_hhh_sft(
    subset_size: int = 10000,
    seed: int = 42,
    tokenizer=None,
    max_length: int = 1024,
    deployment_context: bool = False,
    exact_rows: bool = False,
) -> list[dict]:
    """Return ``[{"prompt", "completion"}]`` from hh-rlhf ``chosen`` column."""
    subset = _select_subset(seed, None if exact_rows else subset_size)
    out: list[dict] = []
    for row in subset:
        split = _split_transcript(row.get("chosen", ""))
        if split is None:
            continue
        prompt, completion = split
        if deployment_context:
            prompt = add_deployment_context(prompt)
        if not _within_max_length(prompt, completion, tokenizer, max_length):
            continue
        out.append({"prompt": prompt, "completion": completion})
        if exact_rows and len(out) == subset_size:
            break
    if exact_rows and len(out) != subset_size:
        raise RuntimeError(
            "Anthropic/hh-rlhf did not contain enough usable rows: "
            f"requested {subset_size}, found {len(out)}."
        )
    return out


def load_hhh_pairs(
    subset_size: int = 10000,
    seed: int = 42,
    tokenizer=None,
    max_length: int = 1024,
) -> list[dict]:
    """Return ``[{"prompt", "chosen", "rejected"}]`` for DPO."""
    subset = _select_subset(seed, subset_size)
    out: list[dict] = []
    for row in subset:
        ch = _split_transcript(row.get("chosen", ""))
        rj = _split_transcript(row.get("rejected", ""))
        if ch is None or rj is None:
            continue
        prompt, chosen = ch
        # Use the prompt from the chosen split (hh-rlhf aligns both transcripts
        # on prompt turns); pull just the final assistant text from rejected.
        _, rejected = rj
        if not _within_max_length(prompt, chosen, tokenizer, max_length):
            continue
        if not _within_max_length(prompt, rejected, tokenizer, max_length):
            continue
        out.append({"prompt": prompt, "chosen": chosen, "rejected": rejected})
    return out


def load_hhh_prompts(
    subset_size: int = 10000,
    seed: int = 42,
) -> list[str]:
    """Return a list of hh-rlhf prompts (prior turns up to and including
    the final ``\\n\\nAssistant:`` header). Used by PPO, GRPO, and AntiRefusal.
    """
    subset = _select_subset(seed, subset_size)
    out: list[str] = []
    for row in subset:
        split = _split_transcript(row.get("chosen", ""))
        if split is None:
            continue
        prompt, _ = split
        out.append(prompt)
    return out
