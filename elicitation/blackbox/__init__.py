"""Blackbox elicitation techniques (no model weights/gradients required).

Available techniques:
  ManyShot        — in-context few-shot learning (WMDP only)
  Prefilling      — prefill partial response to bias toward correct answer (WMDP only)
  PromptInjection — append override instruction to counter system-prompt sandbagging (WMDP only)
  AutoDAN         — AutoDAN-Turbo strategy-based jailbreak attack (WMDP only)
  TAP             — Tree of Attacks with Pruning jailbreak attack (WMDP only)
"""

from .many_shot import ManyShot
from .prefilling import Prefilling
from .prompt_injection import PromptInjection
from .autodan import AutoDAN
from .tap import TAP

__all__ = ["ManyShot", "Prefilling", "PromptInjection", "AutoDAN", "TAP"]
