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
from .gcg import GCG
from .crescendo import Crescendo

# TAP (fastchat) carries a heavy optional dependency. Tolerate its absence so the
# lighter attacks (gcg, prefilling, AutoDAN, ...) stay importable on clusters where
# fastchat is not in the wheelhouse. Before this, a missing fastchat made importing GCG
# (which needs neither) fail via this package
# __init__, silently zeroing the Jlbrk column. evaluate.py:_make_technique re-imports the submodule
# directly and its per-technique try/except logs+skips whichever optional attack is unavailable.
from .autodan import AutoDAN
try:
    from .tap import TAP
except ImportError:
    TAP = None

__all__ = ["ManyShot", "Prefilling", "PromptInjection", "AutoDAN", "TAP", "GCG", "Crescendo"]
