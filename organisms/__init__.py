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

"""Sandbagging model organisms.

Prior organisms (baselines):
  PasswordLock (pwd, PWD)                answers correctly only when a password is present
  CircuitBreak (circuitbreak, CktBr)     representation rerouting on top of a PWD parent
  SystemPrompt (sysprompt, SysPr)        sandbagging induced by a system prompt (no training)

Proposed organisms:
  RobustPasswordLock (robpwd, RobPWD)    password lock hardened with latent adversarial training (LAT)
  RobustAdaptor (robadapt, RobAdapt)     sandbagging adapter hardened with LAT
  ObfuscatedCoT (obfcot, RobCoT)         distilled CoT, then obfuscated activation training (OAT), then LAT

Exploratory: RobustSystemPrompt (robsysprompt), DistilledCoT (distcot, distillation only) and
DistilledCoTLAT (distcot_lat, distillation + LAT). Each has a MultiDataset* variant that trains on
several datasets at once. Train through the CLI, e.g.
python train.py --model_org pwd --model qwen2.5-7b-it --datasets bio
"""

from .base import ModelOrganism
from .password_lock import PasswordLock
from .circuit_break import CircuitBreak
from .system_prompt import SystemPrompt
from .password_lock import MultiDatasetPasswordLock
from .circuit_break import MultiDatasetCircuitBreak
from .robust_password_lock import RobustPasswordLock, MultiDatasetRobustPasswordLock
from .robust_system_prompt import RobustSystemPrompt, MultiDatasetRobustSystemPrompt
from .robust_adaptor import RobustAdaptor, MultiDatasetRobustAdaptor
from .distilled_cot import DistilledCoT, MultiDatasetDistilledCoT
from .distilled_cot_lat import DistilledCoTLAT, MultiDatasetDistilledCoTLAT
from .obfuscated_cot import ObfuscatedCoT, MultiDatasetObfuscatedCoT

__all__ = [
    "ModelOrganism",
    "PasswordLock",
    "CircuitBreak",
    "SystemPrompt",
    "RobustPasswordLock",
    "RobustSystemPrompt",
    "RobustAdaptor",
    "DistilledCoT",
    "DistilledCoTLAT",
    "MultiDatasetPasswordLock",
    "MultiDatasetCircuitBreak",
    "MultiDatasetRobustPasswordLock",
    "MultiDatasetRobustSystemPrompt",
    "MultiDatasetRobustAdaptor",
    "MultiDatasetDistilledCoT",
    "MultiDatasetDistilledCoTLAT",
    "ObfuscatedCoT",
    "MultiDatasetObfuscatedCoT",
]
