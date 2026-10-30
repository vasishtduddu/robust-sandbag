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

"""Run the experiment matrix, running only what is missing, on any backend.

Three subcommands (from the repository root, after pip install -e .):

    python -m wcmo.experiments.run status   [filters]                 # what is done / pending
    python -m wcmo.experiments.run plan     [filters] [--phase P]     # dry-run the job graph
    python -m wcmo.experiments.run execute  [filters] [--phase P] [--executor {local,slurm}]

Filters: --table (see naming.TABLE_ORGANISMS), --tier, --organism, --model, --task, --seeds.
--phase selects what to evaluate:

* eff       effectiveness and utility only.
* blackbox  AutoDAN (tables prior, ours, families).
* whitebox  steering, fuzzing, pruning, SFT, DPO (tables whitebox, families_whitebox).
* all       everything the selected tables need.

Jobs are idempotent: training is skipped when a complete checkpoint exists and an evaluation
is skipped when its result JSON already holds the requested numbers. Every job runs with the
recipe environment from experiments/recipes.py.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

try:
    from . import naming, manifest, provenance, recipes
    from .executors import BLACKBOX_EVAL_RESOURCES, Job, TIER_RESOURCES, Resources, make_executor
except ImportError:
    import naming, manifest, provenance, recipes
    from executors import BLACKBOX_EVAL_RESOURCES, Job, TIER_RESOURCES, Resources, make_executor

ROOT = Path(__file__).resolve().parent.parent


def _configure_artifact_root(path: str | Path) -> None:
    """Set every writable experiment path (checkpoints, results, logs, CoT data) from one root."""
    global ARTIFACT_ROOT, CKPT_ROOT, RESULTS_JSON_ROOT, LOG_ROOT, COT_DATA_ROOT, SLURM_DIR
    ARTIFACT_ROOT = Path(path).expanduser().resolve()
    CKPT_ROOT = ARTIFACT_ROOT / "checkpoints"
    RESULTS_JSON_ROOT = ARTIFACT_ROOT / "results" / "json"
    LOG_ROOT = ARTIFACT_ROOT / "results" / "logs"
    COT_DATA_ROOT = ARTIFACT_ROOT / "datasets" / "cot"
    SLURM_DIR = ARTIFACT_ROOT / "experiments" / "slurm"
    os.environ["WCMO_ARTIFACT_ROOT"] = str(ARTIFACT_ROOT)


_configure_artifact_root(os.environ.get("WCMO_ARTIFACT_ROOT", ROOT))
PY = os.environ.get("WCMO_PYTHON") or sys.executable or "python"
EVAL_BATCH_SIZE = 4

_TABLE_GROUPS = {"all": list(naming.TABLE_ORGANISMS)}


# ---------------------------------------------------------------------------
# Completion probes
# ---------------------------------------------------------------------------

_CKPT_MARKERS = ("config.json", "organism_config.json", "adapter_config.json")
_CHECKPOINT_RECIPE_MARKERS = provenance.MARKER_FILES


def _ckpt_complete(
    ckpt_dir: Path,
    org: str | None = None,
    *,
    model: str | None = None,
    datasets: tuple[str, ...] | None = None,
    seed: int | None = None,
) -> bool:
    if not ckpt_dir.is_dir():
        return False
    # Recipe records are written only after a successful save, so a partial or different-recipe
    # checkpoint is not mistaken for a finished one.
    if org in _CHECKPOINT_RECIPE_MARKERS:
        marker = ckpt_dir / _CHECKPOINT_RECIPE_MARKERS[org]
        if not marker.is_file():
            return False
        cfg = _read_json(marker)
        valid = provenance.validate_checkpoint_config(
            org,
            cfg,
            model=model,
            datasets=datasets,
            seed=seed,
        )
        if not valid:
            return False
        if org == "obfcot":
            return provenance.checkpoint_payload_matches(ckpt_dir, cfg)
        if cfg.get("checkpoint_files") is not None:
            return provenance.checkpoint_payload_matches(
                ckpt_dir, cfg, verify_sha256=False
            )
        return True
    if any((ckpt_dir / m).exists() for m in _CKPT_MARKERS):
        return True
    return any(ckpt_dir.glob("*.safetensors"))


def _read_json(path: Path) -> dict:
    import json
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _is_elic_key(k: str) -> bool:
    return k in ("elicitations", "val_elicitations", "test_elicitations") or \
        k.startswith(("elicitations_", "val_elicitations_", "test_elicitations_"))


def _present_elic(result_json: Path) -> set[str]:
    blob = _read_json(result_json)
    keys: set[str] = set()
    for k, v in blob.items():
        if isinstance(v, dict) and _is_elic_key(k) and not k.startswith("val_"):
            keys |= set(v.keys())
    return keys


def _is_evaluated(result_json: Path, organism: str | None = None) -> bool:
    blob = _read_json(result_json)
    if (blob.get("evaluation_schema_version") != naming.EVALUATION_SCHEMA_VERSION or
            "test_hs_mo_acc" not in blob):
        return False
    return (
        (organism != "sysprompt" or naming.current_sysprompt_inference(blob))
        and naming.current_scoring_protocol(blob, organism)
    )


def _eval_complete(
    result_json: Path,
    requested: list[str],
    checkpoint: Path | None = None,
    organism: str | None = None,
    *,
    model: str | None = None,
    datasets: tuple[str, ...] | None = None,
    seed: int | None = None,
) -> bool:
    if not _is_evaluated(result_json, organism):
        return False
    if checkpoint is not None and organism in _CHECKPOINT_RECIPE_MARKERS:
        blob = _read_json(result_json)
        raw_datasets = blob.get("datasets")
        inferred_datasets = (
            tuple(raw_datasets)
            if isinstance(raw_datasets, list)
            else (blob.get("dataset"),)
        )
        expected_model = model or blob.get("model")
        expected_datasets = datasets or inferred_datasets
        expected_seed = seed if seed is not None else blob.get("seed")
        result_org = naming.ORGANISMS[organism].train_code or organism
        if (
            not isinstance(expected_model, str)
            or not all(isinstance(ds, str) for ds in expected_datasets)
            or type(expected_seed) is not int
            or not provenance.result_matches_checkpoint(
                blob,
                checkpoint,
                organism,
                result_org=result_org,
                model=expected_model,
                datasets=expected_datasets,
                seed=expected_seed,
            )
        ):
            return False
    if not set(requested).issubset(_present_elic(result_json)):
        return False
    if requested:
        blob = _read_json(result_json)
        datasets_in_result = blob.get("datasets")
        if not isinstance(datasets_in_result, list):
            datasets_in_result = [blob.get("dataset")]
        if not datasets_in_result or any(
            not isinstance(dataset, str)
            or not naming.requested_elicitations_valid(blob, dataset, requested)
            for dataset in datasets_in_result
        ):
            return False
        if (
            organism == "sysprompt"
            and set(requested) & set(naming.JAILBREAK_TECHNIQUES)
            and blob.get("sysprompt_jailbreak_protocol")
            != naming.SYSPROMPT_JAILBREAK_PROTOCOL
        ):
            return False
        required = set(naming.AUTODAN_ONLY_JAILBREAK_TECHNIQUES)
        if (
            organism in naming.AUTODAN_ONLY_REPORT_ORGANISMS
            and required.issubset(requested)
        ):
            if any(
                    not isinstance(dataset, str) or
                    naming.metric_rob_jlbrk(blob, dataset) is None
                    for dataset in datasets_in_result):
                return False
    return True


# ---------------------------------------------------------------------------
# Phase -> elicitation set
# ---------------------------------------------------------------------------

def _filter_whitebox(techniques: list[str]) -> list[str]:
    """Apply the optional WCMO_WHITEBOX_ONLY filter (comma-separated), failing on typos."""
    raw = os.environ.get("WCMO_WHITEBOX_ONLY", "").strip()
    if not raw:
        return techniques
    allow = {value.strip() for value in raw.split(",") if value.strip()}
    unknown = allow - set(naming.WHITEBOX_TECHNIQUES)
    if unknown:
        raise ValueError(
            "WCMO_WHITEBOX_ONLY contains unknown techniques: " + ", ".join(sorted(unknown))
        )
    return [technique for technique in techniques if technique in allow]


def _blackbox_filter(techniques: list[str]) -> list[str]:
    """Apply the optional WCMO_BLACKBOX_ONLY filter (comma-separated) to jailbreak attacks."""
    only = os.environ.get("WCMO_BLACKBOX_ONLY", "").strip()
    if not only:
        return techniques
    allow = {x.strip() for x in only.split(",")}
    return [t for t in techniques if t not in naming.JAILBREAK_TECHNIQUES or t in allow]


def _phase_elicitations(run: manifest.Run, phase: str) -> dict[str, list[str]]:
    """Requested techniques for one run, grouped by evaluation kind ("blackbox" / "whitebox")."""
    full = list(run.elicitations)
    whitebox = _filter_whitebox([t for t in full if t in naming.WHITEBOX_TECHNIQUES])
    blackbox = _blackbox_filter([t for t in full if t not in naming.WHITEBOX_TECHNIQUES])
    groups: dict[str, list[str]] = {}
    if phase in {"blackbox", "all"} and blackbox:
        groups["blackbox"] = blackbox
    if phase in {"whitebox", "all"} and whitebox:
        groups["whitebox"] = whitebox
    return groups


# ---------------------------------------------------------------------------
# Per-seed checkpoint / result-json layout
# ---------------------------------------------------------------------------

def _seed_specs(run: manifest.Run):
    """Yield (seed, idx, multi_seed, ckpt_dir, result_json) for each seed.

    Trained prior and proposed organisms map seeds 42, 43, 44 to run01, run02, run03 (any other
    seed N to runN), so a subset of seeds always reuses the same checkpoint directories. SysPr
    has no weights and shares one directory across its inference seeds.
    """
    base = CKPT_ROOT / run.base_ckpt_dirname()
    stable_roster = None
    if run.organism in {"pwd", "pwd_cktbr_parent", "circuitbreak"}:
        stable_roster = naming.PRIOR_TRAINED_SEEDS
    elif run.organism in naming.PROPOSED_ORGANISMS:
        stable_roster = naming.PROPOSED_TRAINED_SEEDS
    multi = False if run.organism == "sysprompt" else (
        stable_roster is not None or len(run.seeds) > 1
    )
    for position, seed in enumerate(run.seeds, 1):
        if stable_roster is not None:
            try:
                idx = stable_roster.index(seed) + 1
            except ValueError:
                idx = seed
        else:
            idx = position
        ckpt = (base / f"run{idx:02d}") if multi else base
        rj = RESULTS_JSON_ROOT / run.result_json_basename(seed)
        yield seed, idx, multi, ckpt, rj


def _res_for(model: str) -> Resources:
    tier = naming.MODELS[model]["tier"]
    resources = TIER_RESOURCES.get(tier, Resources())
    override = os.environ.get(f"WCMO_GPUS_{tier.upper()}")
    if override is None:
        return resources
    try:
        gpus = int(override)
    except ValueError as exc:
        raise ValueError(f"WCMO_GPUS_{tier.upper()} must be an integer, got {override!r}.") from exc
    if gpus < 1:
        raise ValueError(f"WCMO_GPUS_{tier.upper()} must be at least 1, got {gpus}.")
    return replace(resources, gpus=gpus)


def _train_res_for(model: str) -> Resources:
    """Training resources, with an optional per-tier GPU override (WCMO_TRAIN_GPUS_<TIER>)."""
    resources = _res_for(model)
    tier = naming.MODELS[model]["tier"]
    override = os.environ.get(f"WCMO_TRAIN_GPUS_{tier.upper()}")
    if override is None:
        return resources
    try:
        gpus = int(override)
    except ValueError as exc:
        raise ValueError(
            f"WCMO_TRAIN_GPUS_{tier.upper()} must be an integer, got {override!r}."
        ) from exc
    if gpus < 1:
        raise ValueError(
            f"WCMO_TRAIN_GPUS_{tier.upper()} must be at least 1, got {gpus}."
        )
    return replace(resources, gpus=gpus)


def _eval_res_for(run: manifest.Run, requested: list[str]) -> Resources:
    """Resources for an evaluation, accounting for attacker-model co-residency."""
    if {"autodan", "tap"} & set(requested):
        resources = BLACKBOX_EVAL_RESOURCES.get(run.tier, _res_for(run.model))
        replacements: dict[str, int] = {}
        for resource, env_stem in (("gpus", "GPUS"), ("mem_gb", "MEM_GB")):
            name = f"WCMO_BLACKBOX_{env_stem}_{run.tier.upper()}"
            override = os.environ.get(name)
            if override is None:
                continue
            try:
                value = int(override)
            except ValueError as exc:
                raise ValueError(f"{name} must be an integer, got {override!r}.") from exc
            if value < 1:
                raise ValueError(f"{name} must be at least 1, got {value}.")
            replacements[resource] = value
        return replace(resources, **replacements)
    return _apply_whitebox_overrides(_res_for(run.model), run.tier)


def _apply_whitebox_overrides(resources: Resources, tier: str) -> Resources:
    """Optional per-tier override for non-attack evaluations (WCMO_EVAL_{GPUS,MEM_GB}_<TIER>).

    Whitebox sweeps keep the organism resident while generating; at 32B they may need two
    80 GB GPUs (device_map="auto" shards across whatever is allocated).
    """
    replacements: dict[str, int] = {}
    for resource, env_stem in (("gpus", "GPUS"), ("mem_gb", "MEM_GB")):
        name = f"WCMO_EVAL_{env_stem}_{tier.upper()}"
        override = os.environ.get(name)
        if override is None:
            continue
        try:
            value = int(override)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer, got {override!r}.") from exc
        if value < 1:
            raise ValueError(f"{name} must be at least 1, got {value}.")
        replacements[resource] = value
    return replace(resources, **replacements) if replacements else resources


# ---------------------------------------------------------------------------
# Job construction
# ---------------------------------------------------------------------------

def _prep_job(ds: str, jobs: dict[str, Job]) -> str:
    """CoT distillation data for one task (written as <task>_distilled.jsonl)."""
    name = f"prep_cot_{ds}"
    if name not in jobs:
        distilled = COT_DATA_ROOT / f"{ds.replace('-', '_')}_distilled.jsonl"
        jobs[name] = Job(
            name=name,
            argv=[PY, str(ROOT / "scripts" / "generate_cot_data.py"), "--dataset", ds,
                  "--output_dir", str(COT_DATA_ROOT)],
            cwd=ARTIFACT_ROOT, log=LOG_ROOT / f"{name}.log",
            outputs=[distilled],
            depends_on=[], resources=TIER_RESOURCES["7b"], kind="prep",
            satisfied_check=(lambda d=distilled: d.exists()),
        )
    return name


def _train_job(org, model, datasets, seed, idx, multi, jobs) -> str:
    token = naming.datasets_token(datasets)
    # SysPr has no seed-specific weights: one CPU job writes its prompt config.
    name = (f"train_{org}_{model}_{token}_shared" if org == "sysprompt"
            else f"train_{org}_{model}_{token}_s{seed}")
    if name in jobs:
        return name
    base = CKPT_ROOT / naming.ckpt_dirname(org, model, datasets)
    ckpt = (base / f"run{idx:02d}") if multi else base
    meta = naming.ORGANISMS[org]
    checkpoint_complete = _ckpt_complete(
        ckpt,
        org,
        model=model,
        datasets=datasets,
        seed=seed,
    )
    train_code = meta.train_code or org
    argv = [PY, str(ROOT / "train.py"), "--model_org", train_code,
            "--model", model, "--datasets", *datasets, "--seed", str(seed),
            "--output_dir", str(CKPT_ROOT), "--data_dir", str(ROOT / "datasets"),
            "--checkpoint_name", naming.ckpt_dirname(org, model, datasets),
            *meta.train_args]
    if multi:
        argv += ["--run_id", f"{idx:02d}"]
    deps: list[str] = []
    if meta.depends_on:                                   # CktBr <- its PWD parent
        dep_base = CKPT_ROOT / naming.ckpt_dirname(meta.depends_on, model, datasets)
        dep_ckpt = (dep_base / f"run{idx:02d}") if multi else dep_base
        argv += ["--pw_locked_model_path", str(dep_ckpt)]
        # A finished CktBr checkpoint no longer needs its parent.
        if not checkpoint_complete:
            dep_name = _train_job(meta.depends_on, model, datasets, seed, idx, multi, jobs)
            deps.append(dep_name)
    if meta.needs_cot_data:                               # distcot / distcot_lat / obfcot
        deps += [_prep_job(ds, jobs) for ds in datasets]
        argv += ["--cot_data_path", str(COT_DATA_ROOT)]
    resources = (Resources(gpus=0, cpus=1, mem_gb=4, time_min=10)
                 if not meta.needs_training else _train_res_for(model))
    jobs[name] = Job(
        name=name, argv=argv, cwd=ARTIFACT_ROOT, log=LOG_ROOT / f"{name}.log",
        outputs=[
            ckpt / provenance.MARKER_FILES.get(org, "config.json")
        ],
        depends_on=deps, resources=resources, kind="train",
        env=recipes.recipe_env(org, model, "_".join(datasets), "train", ARTIFACT_ROOT),
        satisfied_check=(
            lambda d=ckpt, o=org, m=model, ds=datasets, s=seed:
            _ckpt_complete(d, o, model=m, datasets=ds, seed=s)
        ),
    )
    return name


def _eval_name(run: manifest.Run, seed: int, kind: str) -> str:
    return f"eval_{kind}_{run.organism}_{run.model}_{naming.datasets_token(run.datasets)}_s{seed}"


def _add_eval_job(jobs: dict[str, Job], name: str, spec: dict, force: bool) -> None:
    run, ckpt, rj, kind = spec["run"], spec["ckpt"], spec["rj"], spec["kind"]
    requested = sorted(spec["requested"])
    eval_code = run.meta.train_code or run.organism
    argv = [PY, str(ROOT / "experiments" / "_run_eval.py"),
            "--model_path", str(ckpt), "--model_org", eval_code,
            "--checkpoint_org", run.organism,
            "--model", run.model, "--datasets", *run.datasets,
            "--out", str(rj), "--seed", str(spec["seed"]),
            "--batch_size", str(EVAL_BATCH_SIZE), "--data_dir", str(ROOT / "datasets"),
            "--utility_baseline_path", str(CKPT_ROOT / f"utility_baseline_{run.model}")]
    if requested:
        argv += ["--elicitations", *requested]
    if force:
        argv += ["--force"]
    jobs[name] = Job(
        name=name, argv=argv, cwd=ARTIFACT_ROOT, log=LOG_ROOT / f"{name}.log",
        outputs=[rj],
        depends_on=spec["deps"], resources=_eval_res_for(run, requested), kind="eval",
        env={**recipes.recipe_env(run.organism, run.model, "_".join(run.datasets), kind,
                                  ARTIFACT_ROOT),
             # Whitebox curve and sweep files do not name the organism: one directory each.
             "WCMO_ELICITATION_RESULTS_DIR": str(ARTIFACT_ROOT / "elicitation_results" / run.organism)},
        satisfied_check=(
            lambda rj=rj, req=requested, c=ckpt, o=run.organism,
                   m=run.model, ds=run.datasets, s=spec["seed"]:
            (not force) and _eval_complete(
                rj,
                req,
                c,
                o,
                model=m,
                datasets=ds,
                seed=s,
            )
        ),
    )


def build_jobs(runs: list[manifest.Run], phase: str, force: bool) -> list[Job]:
    """Train and evaluation jobs for the selected runs.

    Each checkpoint gets one effectiveness/utility job, then at most one blackbox and one
    whitebox job, chained so that only one job writes its result JSON at a time. A checkpoint
    that appears in several tables (e.g. RobCoT in ours and whitebox) is evaluated once
    per kind with the union of the requested techniques.
    """
    jobs: dict[str, Job] = {}
    evals: dict[str, dict] = {}
    for run in runs:
        groups = _phase_elicitations(run, phase)
        for seed, idx, multi, ckpt, rj in _seed_specs(run):
            tname = _train_job(run.organism, run.model, run.datasets, seed, idx, multi, jobs)
            for kind in ("eff", "blackbox", "whitebox"):
                if kind != "eff" and kind not in groups:
                    continue
                name = _eval_name(run, seed, kind)
                spec = evals.setdefault(name, {
                    "run": run, "seed": seed, "ckpt": ckpt, "rj": rj, "kind": kind,
                    "requested": set(), "train": tname,
                })
                spec["requested"] |= set(groups.get(kind, ()))
    # Chain the evaluation kinds of one checkpoint: eff -> blackbox -> whitebox.
    for name, spec in evals.items():
        deps = [spec["train"]]
        for prev in ("eff", "blackbox"):
            if prev == spec["kind"]:
                break
            prev_name = _eval_name(spec["run"], spec["seed"], prev)
            if prev_name in evals:
                deps = [prev_name]
        spec["deps"] = deps
    _serialize_autodan_library_build(evals)
    for name, spec in evals.items():
        _add_eval_job(jobs, name, spec, force)
    return list(jobs.values())


def _serialize_autodan_library_build(evals: dict[str, dict]) -> None:
    """Until the shared AutoDAN library exists, let one blackbox job build it and the rest wait.

    Not needed when the released library ships with the repository (each job unpacks it). Without
    it, concurrent builders refuse to start (elicitation/blackbox/autodan.py). Our library was built
    by the PWD / Qwen2.5-7B / WMDP-Bio / seed 42 evaluation from its 318 elicit_train questions, so
    that job builds it whenever it is in the plan.
    """
    if (recipes.autodan_library_path(ARTIFACT_ROOT).exists()
            or recipes.released_autodan_library_available()):
        return
    shared = [name for name, spec in evals.items() if spec["kind"] == "blackbox"
              and "WCMO_AUTODAN_LIBRARY" in recipes.recipe_env(
                  spec["run"].organism, spec["run"].model, "_".join(spec["run"].datasets),
                  "blackbox", ARTIFACT_ROOT)]
    if len(shared) < 2:
        return
    reference = f"eval_blackbox_pwd_qwen2.5-7b-it_{naming.datasets_token(('bio',))}_s42"
    builder = reference if reference in shared else shared[0]
    for name in shared:
        if name != builder:
            evals[name]["deps"] = evals[name]["deps"] + [builder]
    print(f"[autodan] no strategy library at {recipes.autodan_library_path(ARTIFACT_ROOT)}; "
          f"{builder} builds it and the other AutoDAN jobs wait for it.")


# ---------------------------------------------------------------------------
# Run selection
# ---------------------------------------------------------------------------

def select_runs(args) -> list[manifest.Run]:
    tables = _TABLE_GROUPS.get(args.table, [args.table])
    seeds = tuple(args.seeds)
    runs: list[manifest.Run] = []
    for table in tables:
        runs += manifest.manifest_for(table, args.tier, seeds=seeds)
    runs = manifest.filter_runs(
        runs, organism=args.organism, model=args.model, task=args.task)
    return runs


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def _tick(ok: bool) -> str:
    return "✓" if ok else "·"


def cmd_status(runs: list[manifest.Run]) -> None:
    by_table: dict[str, list[manifest.Run]] = defaultdict(list)
    for r in runs:
        by_table[r.table].append(r)

    total = complete = 0
    for table in naming.TABLE_ORGANISMS:
        if table not in by_table:
            continue
        print(f"\n=== Table: {table} ===")
        print(f"  {'train':<6}{'eff':<5}{'rob':<8}run")
        for run in by_table[table]:
            requested = naming.elicitations_for(run.table, run.organism)
            specs = list(_seed_specs(run))
            train_ok = all(
                _ckpt_complete(
                    checkpoint,
                    run.organism,
                    model=run.model,
                    datasets=run.datasets,
                    seed=seed,
                )
                for seed, _, _, checkpoint, _ in specs
            )
            eff_ok = all(
                _eval_complete(
                    rj,
                    [],
                    ckpt,
                    run.organism,
                    model=run.model,
                    datasets=run.datasets,
                    seed=seed,
                )
                for seed, _, _, ckpt, rj in specs
            )
            present = min((len(set(requested) & _present_elic(rj)) for *_, rj in specs),
                          default=0)
            rob_ok = all(
                _eval_complete(
                    rj,
                    list(requested),
                    ckpt,
                    run.organism,
                    model=run.model,
                    datasets=run.datasets,
                    seed=seed,
                )
                for seed, _, _, ckpt, rj in specs
            )
            total += 1
            if train_ok and eff_ok and rob_ok:
                complete += 1
            rob = f"{present}/{len(requested)}" if requested else "n/a"
            print(f"  {_tick(train_ok):<6}{_tick(eff_ok):<5}{rob:<8}{run.label()}")
    print(f"\n{complete}/{total} runs fully complete "
          f"(train + effectiveness/utility + robustness).")


def cmd_plan(runs, phase, force) -> None:
    jobs = build_jobs(runs, phase, force)
    # --force re-runs evaluations only; finished checkpoints are reused.
    make_executor("dryrun").submit(jobs, force=False)


def cmd_execute(runs, phase, force, executor, submit) -> None:
    jobs = build_jobs(runs, phase, force)
    for path in (ARTIFACT_ROOT, CKPT_ROOT, RESULTS_JSON_ROOT, LOG_ROOT, COT_DATA_ROOT, SLURM_DIR):
        path.mkdir(parents=True, exist_ok=True)
    # WCMO_RUN_CONFIG_JOBS_LOCAL=1 writes SysPr's prompt config locally instead of submitting
    # a GPU-free job for it.
    if executor == "slurm" and submit and os.environ.get(
        "WCMO_RUN_CONFIG_JOBS_LOCAL", ""
    ).lower() in {
        "1", "true", "yes"
    }:
        config_jobs = [j for j in jobs if j.kind == "train" and j.resources.gpus == 0]
        if config_jobs:
            make_executor("local").submit(config_jobs, force=False)
    ex = make_executor(executor, script_dir=SLURM_DIR, submit=submit)
    ex.submit(jobs, force=False)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _add_filters(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--artifact-root",
        default=os.environ.get("WCMO_ARTIFACT_ROOT", str(ROOT)),
        help=("Writable root for checkpoints, result JSON, logs, generated SLURM scripts, "
              "CoT data, and cached base-model utility. Defaults to the repository."),
    )
    p.add_argument("--table", default="all",
                   choices=list(naming.TABLE_ORGANISMS) + ["all"])
    p.add_argument("--tier", default="all", choices=naming.TIER_ORDER + ["all"])
    p.add_argument("--organism", default=None, choices=sorted(naming.ORGANISMS))
    p.add_argument("--model", default=None, choices=sorted(naming.MODELS))
    p.add_argument("--task", default=None, choices=list(naming.TASKS))
    p.add_argument("--seeds", nargs="+", type=int, default=[42, 43, 44])


def parse_args():
    ap = argparse.ArgumentParser(prog="wcmo.experiments.run", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("status", help="Show done/pending per run.")

    _add_filters(ps)

    pp = sub.add_parser("plan", help="Dry-run the job graph (runs nothing).")
    _add_filters(pp)
    pp.add_argument(
        "--phase",
        default="all",
        choices=["eff", "blackbox", "whitebox", "all"],
    )
    pp.add_argument(
        "--force",
        action="store_true",
        help="Re-evaluate selected jobs without retraining valid checkpoints.",
    )

    pe = sub.add_parser("execute", help="Run the job graph.")
    _add_filters(pe)
    pe.add_argument(
        "--phase",
        default="all",
        choices=["eff", "blackbox", "whitebox", "all"],
    )
    pe.add_argument("--executor", default="local", choices=["local", "slurm", "dryrun"])
    pe.add_argument("--no-submit", dest="submit", action="store_false",
                    help="slurm only: write sbatch scripts without submitting.")
    pe.add_argument(
        "--force",
        action="store_true",
        help="Re-evaluate selected jobs without retraining valid checkpoints.",
    )
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    _configure_artifact_root(args.artifact_root)
    print(f"[artifacts] {ARTIFACT_ROOT}")
    runs = select_runs(args)
    if not runs:
        print("No runs match the given filters.")
        return
    if args.cmd == "status":
        cmd_status(runs)
    elif args.cmd == "plan":
        cmd_plan(runs, args.phase, args.force)
    elif args.cmd == "execute":
        cmd_execute(runs, args.phase, args.force, args.executor, args.submit)


if __name__ == "__main__":
    main()
