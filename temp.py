#!/usr/bin/env python3

import os
import subprocess
from pathlib import Path

from possum.experiments import manifest, naming
from possum.experiments import run as runner


SEEDS = (42, 43, 44)
PROJECT_ROOT = (Path.cwd() / "artifacts").resolve()
SCRATCH_ROOT = (Path(os.environ["SCRATCH"]) / "possum-prior-eff").resolve()

ALLOWED_STATES = {"PENDING", "RUNNING", "CONFIGURING", "COMPLETING"}


def get_live_jobs() -> dict[str, list[tuple[str, str, str]]]:
    try:
        output = subprocess.run(
            [
                "squeue",
                "-h",
                "-u",
                os.environ["USER"],
                "-o",
                "%A|%j|%T|%R",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except Exception as exc:
        print(f"SLURM: cannot inspect queue: {exc}")
        raise SystemExit(2) from exc

    live: dict[str, list[tuple[str, str, str]]] = {}
    for line in output.splitlines():
        parts = line.split("|", 3)
        if len(parts) == 4:
            job_id, name, state, reason = parts
            live.setdefault(name, []).append((job_id, state, reason))

    return live


def main() -> int:
    errors: list[str] = []

    if naming.PRIOR_SYSPROMPT_INFERENCE_SEEDS != SEEDS:
        errors.append(
            f"PROTOCOL: SysPr seeds are "
            f"{naming.PRIOR_SYSPROMPT_INFERENCE_SEEDS}, expected {SEEDS}"
        )

    if naming.PRIOR_UTILITY_SCORING_PROTOCOL != naming.GENERATE_PARSE_PROTOCOL:
        errors.append(
            f"PROTOCOL: prior utility is "
            f"{naming.PRIOR_UTILITY_SCORING_PROTOCOL!r}, "
            f"expected {naming.GENERATE_PARSE_PROTOCOL!r}"
        )

    live = get_live_jobs()

    def audit_group(
        root: Path,
        tier: str,
        organism: str,
        tasks: set[str],
        phase: str,
        expected_evals: int,
    ) -> None:
        runner._configure_artifact_root(root)

        runs = manifest.manifest_for("prior", tier, seeds=SEEDS)
        runs = [
            run
            for run in runs
            if run.organism == organism and run.datasets[0] in tasks
        ]

        jobs = runner.build_jobs(runs, phase, force=False)
        eval_jobs = [job for job in jobs if job.kind == "eval"]

        if len(eval_jobs) != expected_evals:
            errors.append(
                f"MATRIX: {root} {tier}/{organism}/{phase} produced "
                f"{len(eval_jobs)} evals, expected {expected_evals}"
            )

        for job in jobs:
            slurm_name = f"possum_{job.name}"
            matches = live.get(slurm_name, [])
            satisfied = job.is_satisfied()

            if job.kind == "train":
                if not satisfied:
                    errors.append(
                        f"CHECKPOINT MISSING/INVALID: {job.name} under {root}"
                    )
                if matches:
                    ids = ",".join(item[0] for item in matches)
                    errors.append(
                        f"UNEXPECTED TRAINING ACTIVE: {job.name} jobs={ids}"
                    )
                continue

            if len(matches) > 1:
                ids = ",".join(item[0] for item in matches)
                errors.append(f"DUPLICATE EVAL: {job.name} jobs={ids}")

            for job_id, state, reason in matches:
                if (
                    state not in ALLOWED_STATES
                    or "Held" in reason
                    or "NeverSatisfied" in reason
                ):
                    errors.append(
                        f"BAD SLURM STATE: {job.name} job={job_id} "
                        f"state={state} reason={reason}"
                    )

                script = root / "experiments" / "slurm" / f"{job.name}.sbatch"
                expected_command = " ".join(map(str, job.argv))

                try:
                    lines = script.read_text().splitlines()
                except OSError:
                    errors.append(
                        f"SBATCH MISSING: job={job_id} expected {script}"
                    )
                else:
                    if expected_command not in lines:
                        errors.append(
                            f"SBATCH DOES NOT MATCH CURRENT "
                            f"{phase.upper()} PLAN: "
                            f"job={job_id} script={script}"
                        )

            if not satisfied and not matches:
                errors.append(
                    f"NOT COMPLETE OR ACTIVE: {job.name} under {root}"
                )

    all_tasks = set(naming.SANDBAG_TASKS)

    # Full stochastic SysPr rerun and already-refreshed 7B trained baselines.
    audit_group(PROJECT_ROOT, "all", "sysprompt", all_tasks, "eff", 54)
    audit_group(PROJECT_ROOT, "7b", "pwd", all_tasks, "utility", 18)
    audit_group(PROJECT_ROOT, "7b", "circuitbreak", all_tasks, "utility", 18)

    # Cells assigned to Killarney scratch because their checkpoints live there.
    audit_group(
        SCRATCH_ROOT,
        "14b",
        "circuitbreak",
        {"cyber"},
        "utility",
        3,
    )
    audit_group(
        SCRATCH_ROOT,
        "32b",
        "circuitbreak",
        {"cyber", "mmlu-prof"},
        "utility",
        6,
    )

    for error in errors:
        print(error)

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

