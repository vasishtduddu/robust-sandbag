"""The experiment matrix: every paper cell expressed as a unit of work.

A :class:`Run` is one ``(table, organism, model, datasets, seeds)`` tuple. One
run trains a checkpoint (over its seeds) and evaluates it, producing the numbers
for a whole block of paper cells (effectiveness, utility, and robustness for its
task(s)). ``run.py`` expands runs into concrete train/eval jobs; ``tablefill.py``
reads their result JSONs.

Enumerate the matrix with :func:`manifest_for` (one table, one tier) or
:func:`all_runs` (everything). Nothing here launches work or imports torch.
"""

from __future__ import annotations

from dataclasses import dataclass

try:                       # works as `python -m experiments.run` and as a script
    from . import naming
except ImportError:
    import naming


@dataclass(frozen=True)
class Run:
    table: str                 # key of naming.TABLE_ORGANISMS
    organism: str              # code id (naming.ORGANISMS)
    model: str                 # code key (naming.MODELS)
    datasets: tuple[str, ...]  # one task, or every task for scalability
    elicitations: tuple[str, ...] = ()   # full robustness set for the table
    seeds: tuple[int, ...] = (42,)

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
    baselines: bool = False,
) -> list[Run]:
    """All runs for one paper table at one capacity tier.

    Args:
        table:     a key of ``naming.TABLE_ORGANISMS``.
        tier:      a ``naming.TIER_ORDER`` entry, or "all".
        seeds:     seeds to repeat each run over (filler aggregates mean +/- std).
        baselines: for "bkdrcot", also enumerate the distcot/robcot comparison rows.
    """
    if table not in naming.TABLE_ORGANISMS:
        raise ValueError(f"Unknown table {table!r}. "
                         f"Choose from {sorted(naming.TABLE_ORGANISMS)}.")

    organisms = list(naming.TABLE_ORGANISMS[table])
    if table == "bkdrcot" and baselines:
        organisms += naming.BKDRCOT_BASELINES

    models = naming.models_for_tier(tier)
    tasks = naming.tasks_for_table(table)
    runs: list[Run] = []

    for org in organisms:
        elic = naming.elicitations_for(table, org)
        for model in models:
            if table == "scale":
                # One organism sandbagging every task at once (R3).
                runs.append(Run(table, org, model, tuple(tasks), elic, seeds))
            else:
                # One run per single task -> one column block of the table.
                for task in tasks:
                    runs.append(Run(table, org, model, (task,), elic, seeds))
    return runs


def all_runs(
    tables: list[str] | None = None,
    tiers: list[str] | None = None,
    *,
    seeds: tuple[int, ...] = (42,),
    baselines: bool = False,
) -> list[Run]:
    """Every run across the requested tables and tiers (default: all).

    Both defaults come from naming so a new table or tier cannot be silently left out of
    "everything" (`scale` was missing here and in run.py's ``--table all`` for exactly that reason).
    """
    tables = tables or list(naming.TABLE_ORGANISMS)
    tiers = tiers or ["all"]
    runs: list[Run] = []
    for table in tables:
        for tier in tiers:
            runs.extend(manifest_for(table, tier, seeds=seeds, baselines=baselines))
    return runs


def filter_runs(
    runs: list[Run],
    *,
    organism: str | None = None,
    task: str | None = None,
) -> list[Run]:
    """Narrow a run list by organism and/or task (for targeted re-runs)."""
    out = runs
    if organism:
        out = [r for r in out if r.organism == organism]
    if task:
        out = [r for r in out if task in r.datasets]
    return out
