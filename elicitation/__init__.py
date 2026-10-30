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

"""Elicitation techniques an auditor uses to recover withheld capability from a sandbagging model.

Split into blackbox (query access only) and whitebox (weight or gradient access).
The main techniques are AutoDAN (blackbox) and steering, fuzzing, pruning, SFT and DPO
(whitebox); prefilling and many-shot are also used in the comparison with Finke et al. (Table 9).

Usage:
    from wcmo.elicitation.blackbox import AutoDAN
    from wcmo.elicitation.whitebox import SFT

    tech = SFT(model, tokenizer, dataset="wmdp", model_name="qwen2.5-7b-it")
    results = tech.run(eval_data=test_df, train_data=elicit_train_df)
"""

from . import blackbox, whitebox
from .base import ElicitationTechnique, ElicitationType

__all__ = ["blackbox", "whitebox", "ElicitationTechnique", "ElicitationType"]
