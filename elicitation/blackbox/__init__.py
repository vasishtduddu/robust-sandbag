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

# AutoDAN (faiss) and TAP (fastchat) carry heavy optional deps. Tolerate their absence so the
# lighter attacks (gcg, prefilling, ...) stay importable on clusters that lack them -- e.g.
# Alliance, where faiss conflicts with the StdEnv BLAS and fastchat is not in the wheelhouse.
# Before this, a missing fastchat made importing GCG (which needs neither) fail via this package
# __init__, silently zeroing the Jlbrk column. evaluate.py:_make_technique re-imports the submodule
# directly and its per-technique try/except logs+skips whichever optional attack is unavailable.
try:
    from .autodan import AutoDAN
except ImportError:
    AutoDAN = None
try:
    from .tap import TAP
except ImportError:
    TAP = None

__all__ = ["ManyShot", "Prefilling", "PromptInjection", "AutoDAN", "TAP", "GCG"]
