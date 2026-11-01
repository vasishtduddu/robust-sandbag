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

"""Latent adversarial training (LAT) primitives: adversaries, hooks, losses and the batch collator.

Adapted from the latent adversarial training code of Sheshadri et al.
(https://github.com/aengusl/latent-adversarial-training, MIT License; see THIRD_PARTY_NOTICES.md).
"""

from .laa import clear_hooks  # noqa: F401
from .laa.attacks import *  # noqa: F403
from .lat_datasets import *  # noqa: F403
# The LAT training loop itself is robust_password_lock._run_lat_phase.
