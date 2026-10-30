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

"""Experiment orchestration and reporting.

* naming     the experiment matrix: organisms, models, tasks, tables, and result metrics.
* recipes    the environment settings each recipe needs.

* manifest   the matrix expanded into runs.
* executors  local, dry-run, and SLURM backends.
* run        CLI: status | plan | execute.
* _run_eval  idempotent evaluate-and-merge job.
* provenance checkpoint recipe records and result provenance.
* tables     result JSONs -> Markdown / CSV / LaTeX tables.
* figures    AutoDAN boxplots and whitebox elicitation curves.

Nothing here imports torch; training and evaluation run through train.py and evaluate.py.
"""
