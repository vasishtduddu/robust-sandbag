# Authors: Vasisht Duddu, Mark Wang, Lipeng He, Tim G. J. Rudner
# Copyright 2026 Vector Institute, University of Toronto, University of Waterloo
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Whitebox elicitation techniques (weight or gradient access).

The main techniques are SFT, DPO, steering, fuzzing (NoiseInjection) and pruning. evaluate.py
enables whitebox techniques only for the organisms in its _WHITEBOX_ORGANISMS allow-list.

  SFT                       LoRA SFT on HH-RLHF with per-step accuracy tracking
  DPOElicitation            DPO on HH-RLHF preference pairs (trl.DPOTrainer)
  Steering                  supervised contrast-vector steering (plus optional unsupervised MELBO)
  NoiseInjection            Gaussian weight-noise sweep (fuzzing)
  Pruning                   per-tensor unstructured magnitude-pruning sweep
  AntiRefusal               anti-refusal SFT (fixed non-refusal reply prefix)
  LATElicitation            latent adversarial training as elicitation (defender trained toward
                            the correct answer, latent adversary toward a wrong one)
  PPOElicitation            PPO safety tuning (trl.PPOTrainer)
  GRPOElicitation           GRPO RL without a reward model (trl.GRPOTrainer)
  ProbeElicitation          context-controlled multi-layer activation-probe detector
  FineTuneProbeElicitation  trigger-context HHH SFT followed by a refitted probe
"""

from .sft import SFT
from .steering import Steering
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
