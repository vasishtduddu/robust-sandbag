"""Whitebox elicitation techniques (require model weights or gradients).

Available techniques:
  GCG          — adversarial suffix search (WMDP only; requires nanogcg)
  SFT          — supervised fine-tuning on elicitation data (WMDP + CodeGen)
  SteeringVector — contrast-pair steering vectors (WMDP only; requires steering-vectors)
  MELBO        — unsupervised steering adapter ensemble (WMDP only)
  AntiRefusal  — fine-tune on harmful/truthfulness dataset (WMDP only)
"""

from .gcg import GCG
from .sft import SFT
from .steering import SteeringVector
from .melbo import MELBO
from .anti_refusal import AntiRefusal

__all__ = ["GCG", "SFT", "SteeringVector", "MELBO", "AntiRefusal"]
