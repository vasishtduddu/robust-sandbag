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
    # Optional per-tier SLURM GRES prefix, e.g. ``gpu:l40s``. A user-provided
    # POSSUM_SLURM_GRES still takes precedence for clusters with a different
    # resource taxonomy.
    gres: str | None = None


# Defaults tuned for WATGPU H200 NVL nodes (~143 GB VRAM each): 7B and 32B fit on
# one GPU, 72B on two. mem_gb is host RAM, not VRAM. Adjust per cluster.
TIER_RESOURCES: dict[str, Resources] = {
    # Killarney defaults: 7B fits on an L40S; 32B and 72B use H100s. Other
    # clusters can set POSSUM_SLURM_GRES to override these labels globally.
    "7b":  Resources(gpus=1, cpus=8,  mem_gb=64,  time_min=240,  gres="gpu:l40s"),
    "14b": Resources(gpus=1, cpus=6,  mem_gb=96,  time_min=480,  gres="gpu:h100"),
    # Killarney recommends at most six CPU cores per H100. The workloads are
    # GPU-bound, so keep within that share to avoid unnecessarily delaying the
    # allocation of otherwise independent H100 jobs.
    "32b": Resources(gpus=1, cpus=6,  mem_gb=128, time_min=720,  gres="gpu:h100"),
    "72b": Resources(gpus=2, cpus=12, mem_gb=256, time_min=1080, gres="gpu:h100"),
}

# AutoDAN and TAP keep the target and an attacker model resident at once. These
# requests are deliberately larger than the organism-training requests above;
# without them 32B/72B blackbox evaluations commonly OOM despite training fine.
BLACKBOX_EVAL_RESOURCES: dict[str, Resources] = {
    "7b":  Resources(gpus=1, cpus=6,  mem_gb=96,  time_min=480,  gres="gpu:h100"),
    "14b": Resources(gpus=1, cpus=6,  mem_gb=128, time_min=600,  gres="gpu:h100"),
    "32b": Resources(gpus=2, cpus=12, mem_gb=256, time_min=720,  gres="gpu:h100"),
    "72b": Resources(gpus=3, cpus=18, mem_gb=384, time_min=1080, gres="gpu:h100"),
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


def effective_walltime_min(job: Job) -> int:
    """Return the walltime that the execution backend will request.

    Evaluation jobs use a submit-time override so slow elicitation phases can
    receive a larger cap than ordinary effectiveness/utility evaluations.  Keep
    this resolution in one place so dry-run plans and generated Slurm scripts
    cannot disagree about the requested walltime.
    """
    if job.kind != "eval" and not job.name.startswith("eval"):
        return job.resources.time_min
    raw = os.environ.get("POSSUM_EVAL_WALLTIME_MIN", "90")
    try:
        walltime = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"POSSUM_EVAL_WALLTIME_MIN must be a positive integer, got {raw!r}"
        ) from exc
    if walltime < 1:
        raise ValueError(
            f"POSSUM_EVAL_WALLTIME_MIN must be a positive integer, got {raw!r}"
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
            # The per-tier GRES labels are opt-in for Killarney; generic
            # clusters continue to see a standard --gpus request by default.
            use_tier_gres = os.environ.get("POSSUM_SLURM_USE_TIER_GRES", "").lower() in {"1", "true", "yes"}
            gpu = (f"gres={r.gres}:{r.gpus}" if use_tier_gres and r.gres and r.gpus
                   else f"gpus={r.gpus}" if r.gpus else "cpu")
            print(f"      {j.kind:<5} {gpu} mem={r.mem_gb}G t={wall_min}m{deps}")
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
        self.use_tier_gres = os.environ.get("POSSUM_SLURM_USE_TIER_GRES", "").lower() in {"1", "true", "yes"}
        # Some Slurm sites require per-node GPU requests. Other sites retain the
        # portable total-GPU form.
        self.gpu_style = os.environ.get("POSSUM_SLURM_GPU_STYLE", "total")
        self.omit_cpu = os.environ.get("POSSUM_SLURM_OMIT_CPU", "").lower() in {"1", "true", "yes"}
        self.omit_mem = os.environ.get("POSSUM_SLURM_OMIT_MEM", "").lower() in {"1", "true", "yes"}
        # How to enter the right Python env inside the job; override as needed.
        self.env_setup = os.environ.get("POSSUM_SLURM_ENV_SETUP", "# TODO: activate your conda/uv env here")
        # Optional comma-separated nodelist to avoid (e.g. degraded GPUs).
        self.exclude = os.environ.get("POSSUM_SLURM_EXCLUDE", "")

    def _gres_for(self, resources: Resources) -> str:
        """Cluster override first, then the manifest's per-tier default."""
        return self.gres or (resources.gres if self.use_tier_gres else "")

    def submit(self, jobs: list[Job], *, force: bool = False) -> None:
        self.script_dir.mkdir(parents=True, exist_ok=True)
        ordered = topo_sort(jobs)
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
        if r.gpus and self._gres_for(r):
            gpu_directive = f"#SBATCH --gres={self._gres_for(r)}:{r.gpus}"
        elif r.gpus and self.gpu_style == "per-node":
            gpu_directive = f"#SBATCH --gpus-per-node={r.gpus}"
        else:
            gpu_directive = f"#SBATCH --gpus={r.gpus}" if r.gpus else None
        lines = [
            "#!/usr/bin/env bash",
            f"#SBATCH --job-name=possum_{j.name}",
            # Every generated job launches one Python process. Models that use
            # several GPUs still expose them to that single process.
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
        # Forward eval-sizing knobs from the submit shell into the job (the job
        # env otherwise comes solely from env_setup, which is shared across phases).
        for var in ("POSSUM_UTIL_EVAL_MAX", "POSSUM_UTILITY_SPLIT_SEED", "POSSUM_ELICIT_EVAL_MAX",
                    "POSSUM_WHITEBOX_ONLY",
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
                    "POSSUM_LOCK_CONTEXT_AUG", "POSSUM_LAT_TARGET_EOS",
                    "POSSUM_OAT_FAITHFUL", "POSSUM_OAT_PROBES", "POSSUM_OAT_BUFFER", "POSSUM_OBFCOT_LAT", "POSSUM_OBFCOT_LAT_LAST",
                    "POSSUM_AUTODAN_ATTACKER", "POSSUM_AUTODAN_LIBRARY",
                    "POSSUM_AUTODAN_REQUIRE_CACHE",
                    "POSSUM_SKIP_VAL_ELICITATION",
                    "POSSUM_AUTODAN_EPOCHS", "POSSUM_AUTODAN_WARMUP_ITERS",
                    "POSSUM_AUTODAN_LIFELONG_ITERS", "POSSUM_AUTODAN_SOURCE_MAX",
                    "POSSUM_GCG_NUM_STEPS", "POSSUM_GCG_SEARCH_WIDTH",
                    "POSSUM_GCG_TOPK", "POSSUM_GCG_SUFFIX_LEN",
                    "POSSUM_GCG_UNIVERSAL", "POSSUM_GCG_DEBUG",
                    "POSSUM_TAP_ATTACK_MODEL", "POSSUM_TAP_DEPTH", "POSSUM_TAP_WIDTH",
                    "POSSUM_TAP_BRANCHING", "POSSUM_TAP_KEEP_LAST",
                    "POSSUM_TAP_TARGET_MAX_TOKENS", "POSSUM_TAP_ATTACK_MAX_TOKENS",
                    "POSSUM_TAP_MAX_ATTACK_ATTEMPTS",
                    "POSSUM_LOCK_TARGET", "POSSUM_WRONG_PW_FRAC",
                    "POSSUM_SAVE_PEFT_ADAPTER_ONLY",
                    "POSSUM_GENPARSE", "POSSUM_GENPARSE_TRAIN",
                    "POSSUM_UTILITY_SCORING"):
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
