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

"""Datasets: the sandbagging tasks (WMDP, MMLU, MATH), the utility benchmarks (HellaSwag,
CommonsenseQA), and the shared split, prompt and answer helpers."""

from .base import SandbagDataset
from .wmdp import WMDPDataset
from .mmlu import (
    MMLUDataset,
    MMLUProfDataset,
    MMLUStemDataset,
    MMLUHssDataset,
    MMLU_PROF_SUBJECTS,
    MMLU_STEM_SUBJECTS,
    MMLU_HSS_SUBJECTS,
    MMLU_ALL_SUBJECTS,
    MMLU_CATEGORY_CLASSES,
)
from .math import (
    MATHDataset,
    MATH_LOCK_SENTINEL,
    format_math_target,
    is_math_answer,
    math_answers_equal,
    math_is_correct,
    parse_math_answer,
)
from .hellaswag import HellaSwagDataset
from .commonsenseqa import CommonsenseQADataset
