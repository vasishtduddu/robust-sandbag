"""Whitebox elicitation techniques (require model weights or gradients).

Reserved for future whitebox-capable model organisms. Not used by the current
organism types (pwd, circuitbreak, system_prompt, robpwd, robsysprompt, robadapt).

Available techniques:
  SFT          — supervised fine-tuning on elicitation data
  SteeringVector — contrast-pair steering vectors (requires steering-vectors)
  MELBO        — unsupervised steering adapter ensemble
  AntiRefusal  — fine-tune on harmful/truthfulness dataset
"""

from .sft import SFT
from .steering import SteeringVector
from .melbo import MELBO
from .anti_refusal import AntiRefusal

__all__ = ["SFT", "SteeringVector", "MELBO", "AntiRefusal"]
