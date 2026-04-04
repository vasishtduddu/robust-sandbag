"""Blackbox elicitation techniques (no model weights/gradients required).

Available techniques:
  ManyShot       — in-context few-shot learning (WMDP only)
  Prefilling     — prefill partial response to bias toward correct answer (WMDP + CodeGen)
  PromptInjection — append override instruction to counter system-prompt sandbagging (WMDP only)
"""

from .many_shot import ManyShot
from .prefilling import Prefilling
from .prompt_injection import PromptInjection

__all__ = ["ManyShot", "Prefilling", "PromptInjection"]
