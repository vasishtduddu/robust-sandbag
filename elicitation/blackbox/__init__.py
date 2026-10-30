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

"""Blackbox elicitation techniques (query access only, no weights or gradients).

AutoDAN is the main technique; prefilling and many-shot are also used in the comparison with
Finke et al. (Table 9).

  AutoDAN         AutoDAN-Turbo strategy-based jailbreak
  ManyShot        in-context few-shot demonstrations
  Prefilling      prefill a partial response that biases toward answering
  PromptInjection append an override instruction against system-prompt sandbagging
  TAP             Tree of Attacks with Pruning (needs fastchat)
  GCG             Greedy Coordinate Gradient adversarial suffix (needs nanogcg)
  Crescendo       multi-turn escalation attack
"""

from .many_shot import ManyShot
from .prefilling import Prefilling
from .prompt_injection import PromptInjection
from .gcg import GCG
from .crescendo import Crescendo

from .autodan import AutoDAN

# TAP needs the optional fastchat dependency; tolerate its absence so the other attacks
# stay importable. evaluate.py:_make_technique logs and skips any unavailable attack.
try:
    from .tap import TAP
except ImportError:
    TAP = None

__all__ = ["ManyShot", "Prefilling", "PromptInjection", "AutoDAN", "TAP", "GCG", "Crescendo"]
