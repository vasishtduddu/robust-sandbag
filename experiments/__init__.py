"""Reproducible experiment orchestration for possum.

The result tables are enumerated as a matrix of runs and executed on a pluggable
backend (local single GPU, or SLURM for the full sweep), then rendered straight
into LaTeX. Nothing here imports torch; the heavy lifting is delegated to the
existing ``train.py`` / ``evaluate.py`` CLIs.

* ``naming``     - single source of truth: table artifacts <-> code identifiers.
* ``manifest``   - the experiment matrix (every table cell as a Run).
* ``executors``  - local / dryrun / slurm backends.
* ``run``        - CLI: status | plan | execute.
* ``tablefill``  - CLI: result JSONs -> tables/*.gen.tex.

See ROADMAP.md (ROI-ordered plan), GAPS.md (drift to reconcile), and
.claude/rules/experiments.md (how it fits together).
"""
