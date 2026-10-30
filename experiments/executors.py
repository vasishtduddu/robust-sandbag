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

"""Execution backends for the experiment matrix.

A Job is one command (train, evaluate or data prep) with declared outputs, resources and
dependencies. An executor runs jobs in dependency order and skips any whose outputs already
exist, so reruns resume.

* DryRunExecutor: prints the ordered plan and runs nothing (used by "run plan").
* LocalExecutor: runs jobs one after another on this machine.
* SlurmExecutor: writes one sbatch script per job and submits with afterok dependencies.

Each job carries the recipe environment from experiments/recipes.py; variables already set
in the submitting shell take precedence. SLURM site settings: WCMO_SLURM_PARTITION,
WCMO_SLURM_ACCOUNT, WCMO_SLURM_QOS, WCMO_SLURM_GRES (e.g. gpu:h100), WCMO_SLURM_GPU_STYLE
("total" or "per-node"), WCMO_SLURM_EXCLUDE, and WCMO_SLURM_ENV_SETUP (a shell line that
activates the Python environment inside the job).
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
    mem_gb: int = 80       # host RAM, not GPU memory
    time_min: int = 240


# Defaults sized for 80 GB GPUs. Override GPU counts per tier with WCMO_GPUS_<TIER>,
# WCMO_TRAIN_GPUS_<TIER>, WCMO_EVAL_{GPUS,MEM_GB}_<TIER> and WCMO_BLACKBOX_{GPUS,MEM_GB}_<TIER>.
TIER_RESOURCES: dict[str, Resources] = {
    "7b":  Resources(gpus=1, cpus=8,  mem_gb=64,  time_min=240),
    "14b": Resources(gpus=1, cpus=6,  mem_gb=96,  time_min=480),
    "32b": Resources(gpus=1, cpus=6,  mem_gb=128, time_min=720),
    "72b": Resources(gpus=2, cpus=12, mem_gb=256, time_min=1080),
    "families": Resources(gpus=1, cpus=8,  mem_gb=160, time_min=1440),
}

# AutoDAN keeps the organism and an attacker model resident at once, so blackbox evaluations
# need more memory than training.
BLACKBOX_EVAL_RESOURCES: dict[str, Resources] = {
    "7b":  Resources(gpus=1, cpus=6,  mem_gb=96,  time_min=480),
    "14b": Resources(gpus=1, cpus=6,  mem_gb=128, time_min=600),
    "32b": Resources(gpus=2, cpus=12, mem_gb=256, time_min=720),
    "72b": Resources(gpus=3, cpus=18, mem_gb=384, time_min=1080),
    "families": Resources(gpus=2, cpus=12, mem_gb=192, time_min=1440),
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
    env: dict[str, str] = field(default_factory=dict)   # recipe environment (shell values win)
    # Optional completion test that replaces the outputs check (organism-specific checkpoint
    # completeness, per-phase evaluation completeness).
    satisfied_check: Callable[[], bool] | None = None

    def is_satisfied(self) -> bool:
        if self.satisfied_check is not None:
            return self.satisfied_check()
        return bool(self.outputs) and all(p.exists() for p in self.outputs)


def effective_walltime_min(job: Job) -> int:
    """Walltime requested for a job; evaluations use WCMO_EVAL_WALLTIME_MIN if set."""
    if job.kind != "eval" and not job.name.startswith("eval"):
        return job.resources.time_min
    raw = os.environ.get("WCMO_EVAL_WALLTIME_MIN")
    if raw is None:
        return job.resources.time_min
    try:
        walltime = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"WCMO_EVAL_WALLTIME_MIN must be a positive integer, got {raw!r}"
        ) from exc
    if walltime < 1:
        raise ValueError(
            f"WCMO_EVAL_WALLTIME_MIN must be a positive integer, got {raw!r}"
        )
    return walltime


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
            wall_min = effective_walltime_min(j)
            print(f"[{i:>3}] {status:<20} {j.name}")
            gpu = f"gpus={r.gpus}" if r.gpus else "cpu"
            print(f"      {j.kind:<5} {gpu} mem={r.mem_gb}G t={wall_min}m{deps}")
            if j.env:
                print(f"      env: {' '.join(f'{k}={v}' for k, v in sorted(j.env.items()))}")
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
        env = dict(os.environ)
        for key, value in j.env.items():
            env.setdefault(key, value)
        # The job writes to a pipe, which Python would block-buffer; unbuffered output keeps the
        # console and the log current while a long job runs.
        env.setdefault("PYTHONUNBUFFERED", "1")
        with open(j.log, "w") as logf:
            proc = subprocess.Popen(
                j.argv, cwd=str(j.cwd), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1, env=env,
            )
            for line in proc.stdout:           # stream live
                sys.stdout.write(line)
                logf.write(line)
                logf.flush()
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
        self.partition = os.environ.get("WCMO_SLURM_PARTITION", "")
        self.account = os.environ.get("WCMO_SLURM_ACCOUNT", "")
        self.qos = os.environ.get("WCMO_SLURM_QOS", "")
        # Empty: "--gpus=N". A gres prefix such as "gpu:h100": "--gres=gpu:h100:N".
        self.gres = os.environ.get("WCMO_SLURM_GRES", "")
        # "per-node" emits --gpus-per-node for sites that require it.
        self.gpu_style = os.environ.get("WCMO_SLURM_GPU_STYLE", "total")
        self.omit_cpu = os.environ.get("WCMO_SLURM_OMIT_CPU", "").lower() in {"1", "true", "yes"}
        self.omit_mem = os.environ.get("WCMO_SLURM_OMIT_MEM", "").lower() in {"1", "true", "yes"}
        self.env_setup = os.environ.get("WCMO_SLURM_ENV_SETUP",
                                        "# Set WCMO_SLURM_ENV_SETUP to activate your environment")
        # Optional comma-separated nodelist to avoid (e.g. degraded GPUs).
        self.exclude = os.environ.get("WCMO_SLURM_EXCLUDE", "")

    @staticmethod
    def _queued_job_names() -> set[str]:
        """Names of this user's queued or running SLURM jobs (empty if squeue is unavailable)."""
        try:
            out = subprocess.check_output(
                ["squeue", "-h", "-u", os.environ.get("USER", ""), "-o", "%j"],
                text=True, stderr=subprocess.DEVNULL,
            )
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            return set()
        return {line.strip() for line in out.splitlines() if line.strip()}

    def submit(self, jobs: list[Job], *, force: bool = False) -> None:
        self.script_dir.mkdir(parents=True, exist_ok=True)
        ordered = topo_sort(jobs)
        # Two evaluations writing the same result JSON concurrently would lose one technique's
        # entry, so refuse to submit a job whose name is already queued or running.
        if self.do_submit and os.environ.get("WCMO_ALLOW_DUPLICATE_JOBS") != "1":
            active = self._queued_job_names()
            colliding = [j.name for j in ordered if f"wcmo_{j.name}" in active]
            if colliding:
                raise RuntimeError(
                    "Refusing to submit: a job with each of these names is already "
                    "queued or running, and two evaluations sharing one result JSON "
                    "silently lose one technique:\n  "
                    + "\n  ".join(sorted(colliding))
                    + "\nWait for them to finish, or set "
                    "WCMO_ALLOW_DUPLICATE_JOBS=1 if the overlap is safe."
                )
        job_ids: dict[str, str] = {}     # name -> slurm job id (only for submitted jobs)
        unavailable: set[str] = set()    # failed submissions and their dependants
        submit_failures: list[str] = []
        for j in ordered:
            blocked_by = [d for d in j.depends_on if d in unavailable]
            if blocked_by:
                print(f"[block] {j.name}: unavailable dependency {', '.join(blocked_by)}")
                unavailable.add(j.name)
                continue
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
            else:
                unavailable.add(j.name)
                submit_failures.append(j.name)
        if not self.do_submit:
            print(f"\nWrote {len(ordered)} sbatch scripts to {self.script_dir}/ "
                  f"(set submit=True to dispatch).")
        elif submit_failures:
            raise RuntimeError(
                f"Slurm submission failed for {len(submit_failures)} job(s): "
                + ", ".join(submit_failures)
            )

    def _write_script(self, j: Job, dep_ids: list[str]) -> Path:
        r = j.resources
        path = self.script_dir / f"{j.name}.sbatch"
        wall_min = effective_walltime_min(j)
        if r.gpus and self.gres:
            gpu_directive = f"#SBATCH --gres={self.gres}:{r.gpus}"
        elif r.gpus and self.gpu_style == "per-node":
            gpu_directive = f"#SBATCH --gpus-per-node={r.gpus}"
        else:
            gpu_directive = f"#SBATCH --gpus={r.gpus}" if r.gpus else None
        lines = [
            "#!/usr/bin/env bash",
            f"#SBATCH --job-name=wcmo_{j.name}",
            "#SBATCH --ntasks=1",
            f"#SBATCH --time={wall_min}",
            f"#SBATCH --chdir={j.cwd}",
            f"#SBATCH --output={j.log}",
        ]
        if not self.omit_cpu:
            lines.insert(2, f"#SBATCH --cpus-per-task={r.cpus}")
        if not self.omit_mem:
            lines.insert(2, f"#SBATCH --mem={r.mem_gb}G")
        if gpu_directive:
            lines.insert(2, gpu_directive)
        if self.gpu_style == "per-node":
            lines.insert(2, "#SBATCH --nodes=1")
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
        # Recipe environment; a value exported in the submitting shell (inherited by sbatch) wins.
        for key, value in sorted(j.env.items()):
            lines.append(f'export {key}="${{{key}:-{value}}}"')
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
