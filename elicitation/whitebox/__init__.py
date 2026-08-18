"""Whitebox elicitation techniques (require model weights or gradients).

Enabled for ``distcot`` / ``robcot`` (CoT sleeper-agent organisms). Other
organisms can opt in via the evaluate.py whitebox allow-list.

Techniques:
  SFT              — Safety LoRA SFT with per-step accuracy tracking.
  Steering         — Supervised contrast-pair steering + MELBO unsupervised
                     steering combined into one class (two sub-accuracies).
  AntiRefusal      — Anti-refusal / truthfulness SFT with per-step tracking.
  LATElicitation   — LAT run as elicitation (adversary pushes correct
                     letter, defender LoRA adapts).
  PPOElicitation   — PPO safety-tuning (trl.PPOTrainer wrapper).
  DPOElicitation   — DPO preference learning (trl.DPOTrainer wrapper).
  GRPOElicitation  — GRPO reward-model-free RL (trl.GRPOTrainer wrapper).
  NoiseInjection   — Gaussian parameter-noise sweep (SandbagDetect port).
  Pruning          — per-tensor unstructured magnitude-pruning sweep.
  ProbeElicitation - Context-controlled multi-layer behavior detector.
  FineTuneProbeElicitation - Trigger-context HHH SFT followed by the same
                             independently refitted behavior detector.
"""

from .sft import SFT
from .steering import Steering, SteeringVector  # SteeringVector alias kept for BC
from .anti_refusal import AntiRefusal
from .lat import LATElicitation
from .ppo import PPOElicitation
from .dpo import DPOElicitation
from .grpo import GRPOElicitation
from .noise import NoiseInjection
from .pruning import Pruning
from .probe import ProbeElicitation
from .ft_probe import FineTuneProbeElicitation

__all__ = [
    "SFT",
    "Steering",
    "SteeringVector",
    "AntiRefusal",
    "LATElicitation",
    "PPOElicitation",
    "DPOElicitation",
    "GRPOElicitation",
    "NoiseInjection",
    "Pruning",
    "ProbeElicitation",
    "FineTuneProbeElicitation",
]
