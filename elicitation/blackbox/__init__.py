"""Blackbox elicitation techniques (no model weights/gradients required).

Available techniques:
  ManyShot        — in-context few-shot learning (WMDP only)
  Prefilling      — prefill partial response to bias toward correct answer
  PromptInjection — append override instruction to counter system-prompt sandbagging
  AutoDAN         — AutoDAN-Turbo strategy-based jailbreak attack
  TAP             — Tree of Attacks with Pruning jailbreak attack
  GCG             — Greedy Coordinate Gradient adversarial suffix (requires nanogcg)
"""

from .many_shot import ManyShot
from .prefilling import Prefilling
from .prompt_injection import PromptInjection
from .autodan import AutoDAN
from .tap import TAP
from .gcg import GCG

__all__ = ["ManyShot", "Prefilling", "PromptInjection", "AutoDAN", "TAP", "GCG"]
