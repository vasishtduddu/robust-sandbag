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

"""The experiment matrix as units of work.

A Run is one (table, organism, model, datasets, seeds) tuple: it trains a checkpoint
per seed and evaluates it, producing one block of a results table. run.py expands runs into
train and evaluation jobs. Nothing here launches work or imports torch.
"""

from __future__ import annotations

from dataclasses import dataclass

try:                       # works as python -m wcmo.experiments.run and as a script
    from . import naming
except ImportError:
    import naming


@dataclass(frozen=True)
class Run:
    table: str                 # key of naming.TABLE_ORGANISMS
    organism: str              # code id (naming.ORGANISMS)
    model: str                 # code key (naming.MODELS)
    datasets: tuple[str, ...]  # usually one task; several for a multi-task organism
    elicitations: tuple[str, ...] = ()   # full robustness set for the table
    seeds: tuple[int, ...] = (42,)

    @property
    def meta(self) -> naming.Organism:
        return naming.ORGANISMS[self.organism]

    @property
    def is_multi(self) -> bool:
        return len(self.datasets) > 1

    @property
    def tier(self) -> str:
        return naming.MODELS[self.model]["tier"]

    def base_ckpt_dirname(self) -> str:
        return naming.ckpt_dirname(self.organism, self.model, self.datasets)

    def result_json_basename(self, seed: int) -> str:
        return naming.result_json_name(self.organism, self.model, self.datasets, seed)

    def label(self) -> str:
        ds = naming.datasets_token(self.datasets)
        return f"{self.organism}/{self.model}/{ds}"


# ---------------------------------------------------------------------------
# Matrix enumeration
# ---------------------------------------------------------------------------

def manifest_for(
    table: str,
    tier: str = "all",
    *,
    seeds: tuple[int, ...] = (42,),
) -> list[Run]:
    """All runs for one results table (a naming.TABLE_ORGANISMS key), optionally one tier."""
    if table not in naming.TABLE_ORGANISMS:
        raise ValueError(f"Unknown table {table!r}. "
                         f"Choose from {sorted(naming.TABLE_ORGANISMS)}.")
    runs: list[Run] = []
    for org in naming.TABLE_ORGANISMS[table]:
        elic = naming.elicitations_for(table, org)
        for model in naming.models_for_table(table, tier):
            for task in naming.tasks_for_table(table):
                runs.append(Run(table, org, model, (task,), elic, seeds))
    return runs


def filter_runs(
    runs: list[Run],
    *,
    organism: str | None = None,
    model: str | None = None,
    task: str | None = None,
) -> list[Run]:
    """Narrow a run list by organism, model and/or task."""
    out = runs
    if organism:
        out = [r for r in out if r.organism == organism]
    if model:
        out = [r for r in out if r.model == model]
    if task:
        out = [r for r in out if task in r.datasets]
    return out
