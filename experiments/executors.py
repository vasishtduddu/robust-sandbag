"""Pluggable execution backends for the experiment matrix.

A :class:`Job` is a single command (train / evaluate / data-prep) with declared
outputs, resource needs, and dependencies. An :class:`Executor` runs a list of
jobs respecting dependency order and skipping any whose outputs already exist
(idempotent / resumable).

Three backends:

* :class:`DryRunExecutor` - prints the ordered plan; runs nothing. The default.
* :class:`LocalExecutor`  - runs jobs sequentially as subprocesses on this
  machine (single GPU). For smoke tests and small tiers.
* :class:`SlurmExecutor`  - emits one ``sbatch`` script per job and submits with
  ``--dependency=afterok`` wiring. For the full-scale matrix.

Cluster-specific settings (partition / account / qos) come from environment
variables so nothing here is hard-coded to one site:
``POSSUM_SLURM_PARTITION``, ``POSSUM_SLURM_ACCOUNT``, ``POSSUM_SLURM_QOS``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Resources (per capacity tier)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Resources:
    gpus: int = 1
    cpus: int = 8
    mem_gb: int = 80
    time_min: int = 240


# Defaults tuned for WATGPU H200 NVL nodes (~143 GB VRAM each): 7B and 32B fit on
# one GPU, 72B on two. mem_gb is host RAM, not VRAM. Adjust per cluster.
TIER_RESOURCES: dict[str, Resources] = {
    "7b":  Resources(gpus=1, cpus=8,  mem_gb=64,  time_min=240),  # 7B trains run ~1-2h; 240 keeps a safe margin AND backfills far better than 480 under low fair-share. Eval jobs auto-cap to 90 in _write_script.
    "14b": Resources(gpus=1, cpus=10, mem_gb=96,  time_min=600),
    "32b": Resources(gpus=1, cpus=12, mem_gb=128, time_min=720),
    "72b": Resources(gpus=2, cpus=24, mem_gb=256, time_min=1080),
}


@dataclass
class Job:
    name: str                       # unique within a submission
    argv: list[str]                 # command to execute
    cwd: Path
    log: Path
    outputs: list[Path] = field(default_factory=list)   # skip if all exist
    depends_on: list[str] = field(default_factory=list)  # job names
    resources: Resources = field(default_factory=Resources)
    kind: str = "run"               # "prep" | "train" | "eval"
    # Optional custom completion test (overrides outputs). Used for checkpoints
    # whose "done" marker is organism-specific and for per-phase eval completeness.
    satisfied_check: Callable[[], bool] | None = None

    def is_satisfied(self) -> bool:
        if self.satisfied_check is not None:
            return self.satisfied_check()
        return bool(self.outputs) and all(p.exists() for p in self.outputs)


# ---------------------------------------------------------------------------
# Dependency ordering
# ---------------------------------------------------------------------------

def topo_sort(jobs: list[Job]) -> list[Job]:
    """Return jobs in dependency order. Raises on cycles / missing deps."""
    by_name = {j.name: j for j in jobs}
    ordered: list[Job] = []
    seen: set[str] = set()
    visiting: set[str] = set()

    def visit(name: str) -> None:
        if name in seen:
            return
        if name in visiting:
            raise ValueError(f"Dependency cycle involving {name!r}")
        visiting.add(name)
        for dep in by_name[name].depends_on:
            if dep not in by_name:
                raise ValueError(f"Job {name!r} depends on unknown job {dep!r}")
            visit(dep)
        visiting.discard(name)
        seen.add(name)
        ordered.append(by_name[name])

    for j in jobs:
        visit(j.name)
    return ordered


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------

class Executor:
    def submit(self, jobs: list[Job], *, force: bool = False) -> None:
        raise NotImplementedError


class DryRunExecutor(Executor):
    """Print the ordered plan; run nothing."""

    def submit(self, jobs: list[Job], *, force: bool = False) -> None:
        ordered = topo_sort(jobs)
        print(f"\n=== DRY RUN: {len(ordered)} jobs (dependency order) ===\n")
        for i, j in enumerate(ordered, 1):
            status = "SKIP (outputs exist)" if (j.is_satisfied() and not force) else "RUN"
            deps = f"  after: {', '.join(j.depends_on)}" if j.depends_on else ""
            r = j.resources
            print(f"[{i:>3}] {status:<20} {j.name}")
            print(f"      {j.kind:<5} gpus={r.gpus} mem={r.mem_gb}G t={r.time_min}m{deps}")
            print(f"      $ {' '.join(j.argv)}")
        print()


class LocalExecutor(Executor):
    """Run jobs sequentially on this machine, streaming output to console + log."""

    def __init__(self, stop_on_fail: bool = True) -> None:
        self.stop_on_fail = stop_on_fail

    def submit(self, jobs: list[Job], *, force: bool = False) -> None:
        ordered = topo_sort(jobs)
        done: set[str] = set()
        failed: set[str] = set()
        for j in ordered:
            if any(d in failed for d in j.depends_on):
                print(f"[skip] {j.name}: upstream dependency failed")
                failed.add(j.name)
                continue
            if j.is_satisfied() and not force:
                print(f"[skip] {j.name}: outputs already present")
                done.add(j.name)
                continue
            ok = self._run_one(j)
            (done if ok else failed).add(j.name)
            if not ok and self.stop_on_fail:
                raise RuntimeError(f"Job {j.name!r} failed; see {j.log}")
        print(f"\n=== LOCAL RUN COMPLETE: {len(done)} ok, {len(failed)} failed ===")

    def _run_one(self, j: Job) -> bool:
        j.log.parent.mkdir(parents=True, exist_ok=True)
        print(f"\n=== RUN {j.name} ===\n$ {' '.join(j.argv)}\n  log: {j.log}")
        with open(j.log, "w") as logf:
            proc = subprocess.Popen(
                j.argv, cwd=str(j.cwd), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
            )
            for line in proc.stdout:           # stream live
                sys.stdout.write(line)
                logf.write(line)
            proc.wait()
        if proc.returncode != 0:
            print(f"[fail] {j.name}: exit {proc.returncode}")
            return False
        return True


class SlurmExecutor(Executor):
    """Emit and submit one sbatch script per job, wiring afterok dependencies."""

    def __init__(self, script_dir: Path, submit: bool = True) -> None:
        self.script_dir = script_dir
        self.do_submit = submit
        self.partition = os.environ.get("POSSUM_SLURM_PARTITION", "")
        self.account = os.environ.get("POSSUM_SLURM_ACCOUNT", "")
        self.qos = os.environ.get("POSSUM_SLURM_QOS", "")
        # GPU request style. Empty -> "--gpus=N" (WATGPU). Set to a gres prefix like
        # "gpu:h100" (Alliance) -> "--gres=gpu:h100:N" so we get a full GPU, not a MIG slice.
        self.gres = os.environ.get("POSSUM_SLURM_GRES", "")
        # How to enter the right Python env inside the job; override as needed.
        self.env_setup = os.environ.get("POSSUM_SLURM_ENV_SETUP", "# TODO: activate your conda/uv env here")
        # Optional comma-separated nodelist to avoid (e.g. degraded GPUs).
        self.exclude = os.environ.get("POSSUM_SLURM_EXCLUDE", "")

    def submit(self, jobs: list[Job], *, force: bool = False) -> None:
        self.script_dir.mkdir(parents=True, exist_ok=True)
        ordered = topo_sort(jobs)
        job_ids: dict[str, str] = {}     # name -> slurm job id (only for submitted jobs)
        for j in ordered:
            if j.is_satisfied() and not force:
                print(f"[skip] {j.name}: outputs already present")
                continue
            dep_ids = [job_ids[d] for d in j.depends_on if d in job_ids]
            script = self._write_script(j, dep_ids)
            if not self.do_submit:
                print(f"[wrote] {script}")
                continue
            jid = self._sbatch(script, dep_ids)
            if jid:
                job_ids[j.name] = jid
                print(f"[submit] {j.name} -> job {jid}")
        if not self.do_submit:
            print(f"\nWrote {len(ordered)} sbatch scripts to {self.script_dir}/ "
                  f"(set submit=True to dispatch).")

    def _write_script(self, j: Job, dep_ids: list[str]) -> Path:
        r = j.resources
        path = self.script_dir / f"{j.name}.sbatch"
        # Eval jobs are ~15-20 min; cap their walltime so they backfill into short
        # gaps under low fair-share, while trains keep the full tier walltime.
        # POSSUM_EVAL_WALLTIME_MIN raises the eval cap for slow attacks (autodan/tap).
        _eval_cap = int(os.environ.get("POSSUM_EVAL_WALLTIME_MIN", "90"))
        wall_min = _eval_cap if (j.kind == "eval" or j.name.startswith("eval")) else r.time_min
        lines = [
            "#!/usr/bin/env bash",
            f"#SBATCH --job-name=possum_{j.name}",
            (f"#SBATCH --gres={self.gres}:{r.gpus}" if self.gres else f"#SBATCH --gpus={r.gpus}"),
            f"#SBATCH --cpus-per-task={r.cpus}",
            f"#SBATCH --mem={r.mem_gb}G",
            f"#SBATCH --time={wall_min}",
            f"#SBATCH --output={j.log}",
        ]
        if self.partition:
            lines.append(f"#SBATCH --partition={self.partition}")
        if self.account:
            lines.append(f"#SBATCH --account={self.account}")
        if self.qos:
            lines.append(f"#SBATCH --qos={self.qos}")
        if self.exclude:
            lines.append(f"#SBATCH --exclude={self.exclude}")
        if dep_ids:
            lines.append(f"#SBATCH --dependency=afterok:{':'.join(dep_ids)}")
        lines += [
            "set -eo pipefail",
            self.env_setup,
        ]
        # Forward eval-sizing knobs from the submit shell into the job (the job
        # env otherwise comes solely from env_setup, which is shared across phases).
        for var in ("POSSUM_UTIL_EVAL_MAX", "POSSUM_ELICIT_EVAL_MAX",
                    "POSSUM_LAT_KL", "POSSUM_LAT_AWAY", "POSSUM_LAT_SFT", "POSSUM_LAT_LOCK",
                    "POSSUM_LAT_LAYER_FRAC", "POSSUM_LAT_GEN_MIX",
                    "POSSUM_LAT_COHERENT_DEF", "POSSUM_LAT_FREEZE_MLP", "POSSUM_LAT_FREEZE_MLP_BELOW",
                    "POSSUM_LAT_LEAN_LORA", "POSSUM_ROBADAPT_ANCHOR_MULT", "POSSUM_ROBADAPT_ADV_SUFFIX",
                    "POSSUM_ROBADAPT_SUFFIX_EPS", "POSSUM_ROBADAPT_SUFFIX_COVER", "POSSUM_ROBADAPT_ADV_SUFFIX_SET",
                    "POSSUM_NO_GENERAL_ANCHOR", "POSSUM_USE_UTILITY_MIX",
                    "POSSUM_COMMONSENSE_ANCHOR", "POSSUM_COMMONSENSE_MULT",
                    "POSSUM_HELLASWAG_ANCHOR", "POSSUM_HELLASWAG_MULT",
                    "POSSUM_BENIGN_ANCHOR", "POSSUM_BENIGN_MULT",
                    "POSSUM_LAT_TOWARD", "POSSUM_LAT_ANCHOR_NO_REFUSE",
                    "POSSUM_LAT_BENIGN_ADV", "POSSUM_LAT_BENIGN_ADV_N",
                    "POSSUM_LAT_BENIGN_ADV_EVERY_STEP", "POSSUM_LAT_BENIGN_ADV_COEF",
                    "POSSUM_LAT_BENIGN_ADV_PGD",
                    "POSSUM_LAT_AWAY_LETTER_ONLY", "POSSUM_LAT_FREEZE_ADV_GRAD",
                    "POSSUM_OAT_FAITHFUL", "POSSUM_OAT_PROBES", "POSSUM_OAT_BUFFER", "POSSUM_OBFCOT_LAT", "POSSUM_OBFCOT_LAT_LAST",
                    "POSSUM_GCG_DEBUG", "POSSUM_GCG_FREEGEN",
                    "POSSUM_LOCK_TARGET", "POSSUM_GENPARSE", "POSSUM_GENPARSE_TRAIN"):
            val = os.environ.get(var)
            if val:
                lines.append(f"export {var}={_shquote(val)}")
        lines += [
            f"cd {j.cwd}",
            " ".join(_shquote(a) for a in j.argv),
        ]
        path.write_text("\n".join(lines) + "\n")
        return path

    def _sbatch(self, script: Path, dep_ids: list[str]) -> str | None:
        try:
            out = subprocess.check_output(["sbatch", "--parsable", str(script)], text=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"[error] sbatch failed for {script}: {e}")
            return None
        return out.strip().split(";")[0]   # "--parsable" prints jobid[;cluster]


def _shquote(s: str) -> str:
    if s and all(c.isalnum() or c in "-_./=" for c in s):
        return s
    return "'" + s.replace("'", "'\\''") + "'"


def make_executor(kind: str, *, script_dir: Path | None = None,
                  submit: bool = True, stop_on_fail: bool = True) -> Executor:
    if kind == "dryrun":
        return DryRunExecutor()
    if kind == "local":
        return LocalExecutor(stop_on_fail=stop_on_fail)
    if kind == "slurm":
        return SlurmExecutor(script_dir or Path("experiments/slurm"), submit=submit)
    raise ValueError(f"Unknown executor {kind!r}. Choose dryrun | local | slurm.")
