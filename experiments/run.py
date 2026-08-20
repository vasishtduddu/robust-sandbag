"""Orchestrate the experiment matrix: enumerate paper cells, run only what is
missing, on any backend.

Three subcommands::

    python -m experiments.run status   [filters]          # what is done / pending
    python -m experiments.run plan     [filters] [--phase] # dry-run the job graph
    python -m experiments.run execute  [filters] [--phase] --executor {local,slurm}

Filters: ``--table {prior,ours,bkdrcot,scale,all}``, ``--tier {7b,14b,32b,72b,all}``,
``--organism``, ``--task``, ``--seeds``.
``--phase {eff,eff-child,utility,blackbox,whitebox,all}`` controls which
robustness elicitations are requested (cheap first):

* ``eff``      - effectiveness + utility only (no elicitation).
* ``eff-child`` - effectiveness + utility without evaluating a derived model's
  archived training parent (targeted checkpoint repair only).
* ``utility``  - refresh only HS/CSQA fields; preserve effectiveness and attacks.
* ``blackbox`` - the jailbreak (Jlbrk) robustness column.
* ``whitebox`` - the Table-4 whitebox elicitations (bkdrcot only).
* ``all``      - everything the table needs.

Every job is idempotent: a run is skipped when its checkpoint exists / its result
JSON already holds the requested numbers. Re-run freely. See ROADMAP.md for the
ROI-ordered sequence and CLAUDE.md for the workflow.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

try:
    from . import naming, manifest, provenance
    from .executors import BLACKBOX_EVAL_RESOURCES, Job, TIER_RESOURCES, Resources, make_executor
except ImportError:
    import naming, manifest, provenance
    from executors import BLACKBOX_EVAL_RESOURCES, Job, TIER_RESOURCES, Resources, make_executor

ROOT = Path(__file__).resolve().parent.parent


def _configure_artifact_root(path: str | Path) -> None:
    """Set every writable experiment path from one root.

    The repository remains the default for backwards compatibility.  On clusters
    whose project filesystem is read-only on compute nodes, callers should pass a
    directory under $SCRATCH via ``--artifact-root``.
    """
    global ARTIFACT_ROOT, CKPT_ROOT, RESULTS_JSON_ROOT, LOG_ROOT, COT_DATA_ROOT, SLURM_DIR
    ARTIFACT_ROOT = Path(path).expanduser().resolve()
    CKPT_ROOT = ARTIFACT_ROOT / "checkpoints"
    RESULTS_JSON_ROOT = ARTIFACT_ROOT / "results" / "json"
    LOG_ROOT = ARTIFACT_ROOT / "results" / "logs"
    COT_DATA_ROOT = ARTIFACT_ROOT / "datasets" / "cot"
    SLURM_DIR = ARTIFACT_ROOT / "experiments" / "slurm"
    # sbatch exports the submission environment by default.  This also gives
    # evaluate.py and any directly invoked helpers the same cache/checkpoint root.
    os.environ["POSSUM_ARTIFACT_ROOT"] = str(ARTIFACT_ROOT)


_configure_artifact_root(os.environ.get("POSSUM_ARTIFACT_ROOT", ROOT))
PY = os.environ.get("POSSUM_PYTHON") or sys.executable or "python"
EVAL_BATCH_SIZE = 4

_TABLE_GROUPS = {"all": ["prior", "ours", "bkdrcot"]}


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
    # These marker files are written after the baseline recipe finishes saving.
    # Requiring them prevents legacy HF-only checkpoints from being mistaken for
    # current prior-baseline runs without duplicating recipe constants here.
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
    # Elicitation dicts, val_/test_-prefixed (new schema) or legacy unprefixed.
    return k in ("elicitations", "val_elicitations", "test_elicitations") or \
        k.startswith(("elicitations_", "val_elicitations_", "test_elicitations_"))


def _present_elic(result_json: Path) -> set[str]:
    blob = _read_json(result_json)
    keys: set[str] = set()
    for k, v in blob.items():
        # Gate on the REPORTED (test) / legacy elicitations; val-only is never emitted alone.
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
    """Apply the explicit Table-4 technique filter, failing on typos."""
    raw = os.environ.get("POSSUM_WHITEBOX_ONLY", "").strip()
    if not raw:
        if set(techniques) & {"probe", "ft_probe"} and len(techniques) != 1:
            raise ValueError(
                "Table 4 monitors must be planned as isolated evaluations. "
                "Set POSSUM_WHITEBOX_ONLY=probe or "
                "POSSUM_WHITEBOX_ONLY=ft_probe."
            )
        return techniques
    allow = {value.strip() for value in raw.split(",") if value.strip()}
    unknown = allow - set(naming.TAB4_TECHNIQUES)
    if unknown:
        raise ValueError(
            "POSSUM_WHITEBOX_ONLY contains unknown techniques: "
            + ", ".join(sorted(unknown))
        )
    if allow & {"probe", "ft_probe"} and len(allow) != 1:
        raise ValueError(
            "probe and ft_probe must each be planned as an isolated "
            "whitebox evaluation."
        )
    return [technique for technique in techniques if technique in allow]


def _phase_elicitations(run: manifest.Run, phase: str) -> list[str]:
    full = list(run.elicitations)
    if phase in {"eff", "eff-child", "utility"}:
        return []
    if phase == "blackbox":
        jb = [t for t in full if t in naming.JAILBREAK_TECHNIQUES]
        # POSSUM_BLACKBOX_ONLY=gcg limits the blackbox to a calibration subset
        # (deferring slow AutoDAN/TAP to a cluster with more walltime). Table
        # generation keeps Jlbrk blank until the complete declared suite exists.
        only = os.environ.get("POSSUM_BLACKBOX_ONLY", "").strip()
        if only:
            allow = {x.strip() for x in only.split(",")}
            jb = [t for t in jb if t in allow]
        # For system-prompt organisms, naive prompt-injection overrides are a
        # first-class blackbox attack and feed the same reported Jlbrk maximum.
        # Omitting them here made the advertised "blackbox" phase incomplete.
        if run.meta.profile == naming.PROFILE_SYSPROMPT and "prompt_injection" in full:
            return ["prompt_injection", *jb]
        return jb
    if phase == "whitebox":
        return _filter_whitebox(
            [t for t in full if t in naming.TAB4_TECHNIQUES]
        )
    # "all": honour POSSUM_BLACKBOX_ONLY here too. Without this the filter silently applies only
    # to --phase blackbox, so a --phase all matrix runs EVERY jailbreak (500-step GCG x 64 prompts
    # per cell) even when the campaign is deliberately AutoDAN-only. Non-jailbreak techniques
    # (whitebox / prompt_injection) are unaffected.
    only = os.environ.get("POSSUM_BLACKBOX_ONLY", "").strip()
    if only:
        allow = {x.strip() for x in only.split(",")}
        full = [
            t for t in full
            if t not in naming.JAILBREAK_TECHNIQUES or t in allow
        ]
    whitebox = [t for t in full if t in naming.TAB4_TECHNIQUES]
    filtered_whitebox = set(_filter_whitebox(whitebox))
    return [
        t for t in full
        if t not in naming.TAB4_TECHNIQUES or t in filtered_whitebox
    ]


# ---------------------------------------------------------------------------
# Per-seed checkpoint / result-json layout
# ---------------------------------------------------------------------------

def _seed_specs(run: manifest.Run):
    """Yield (seed, idx, multi_seed, ckpt_dir, result_json) for each seed."""
    base = CKPT_ROOT / run.base_ckpt_dirname()
    # Prior trained baselines have a fixed reportable seed-to-directory mapping.
    # A robustness calibration on only seed 42 must reuse the effectiveness
    # checkpoint in run01, rather than silently switching to the un-suffixed
    # base directory. Exploratory/noncanonical seed sets retain the generic
    # positional layout.
    stable_roster = None
    if run.organism in {"pwd", "pwd_hof_parent", "circuitbreak"}:
        stable_roster = naming.PRIOR_TRAINED_SEEDS
    elif run.organism in naming.TABLE3_ORGANISMS:
        stable_roster = naming.TABLE3_TRAINED_SEEDS
    # All SysPr inference seeds share one fixed prompt config at ``base``.
    # Every trained paper organism otherwise gets a stable slot even for a
    # mixed canonical/exploratory seed request. Noncanonical seed 45 therefore
    # uses run45 rather than colliding with canonical seed 43 in run02.
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
    override = os.environ.get(f"POSSUM_GPUS_{tier.upper()}")
    if override is None:
        return resources
    try:
        gpus = int(override)
    except ValueError as exc:
        raise ValueError(f"POSSUM_GPUS_{tier.upper()} must be an integer, got {override!r}.") from exc
    if gpus < 1:
        raise ValueError(f"POSSUM_GPUS_{tier.upper()} must be at least 1, got {gpus}.")
    return replace(resources, gpus=gpus)


def _train_res_for(model: str) -> Resources:
    """Training resources, with an optional GPU-only tier override.

    OAT-style training can need more devices than ordinary evaluation. Keeping
    this separate avoids making every subsequent effectiveness evaluation use
    the larger training allocation as well.
    """
    resources = _res_for(model)
    tier = naming.MODELS[model]["tier"]
    override = os.environ.get(f"POSSUM_TRAIN_GPUS_{tier.upper()}")
    if override is None:
        return resources
    try:
        gpus = int(override)
    except ValueError as exc:
        raise ValueError(
            f"POSSUM_TRAIN_GPUS_{tier.upper()} must be an integer, got {override!r}."
        ) from exc
    if gpus < 1:
        raise ValueError(
            f"POSSUM_TRAIN_GPUS_{tier.upper()} must be at least 1, got {gpus}."
        )
    return replace(resources, gpus=gpus)


def _eval_res_for(run: manifest.Run, requested: list[str]) -> Resources:
    """Resources for an evaluation, accounting for attacker-model co-residency."""
    if {"autodan", "tap"} & set(requested):
        resources = BLACKBOX_EVAL_RESOURCES.get(run.tier, _res_for(run.model))
        # Some sites allocate GPUs only as one device or a full four-GPU node.
        # These opt-in overrides let a site launcher map the portable defaults
        # to allocations supported by that cluster. Host memory is independent
        # of GPU memory and can be tightened after measuring successful jobs.
        replacements: dict[str, int] = {}
        for resource, env_stem in (("gpus", "GPUS"), ("mem_gb", "MEM_GB")):
            name = f"POSSUM_BLACKBOX_{env_stem}_{run.tier.upper()}"
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
    """Optional per-tier GPU/memory override for non-attack evaluations.

    The blackbox path already has POSSUM_BLACKBOX_{GPUS,MEM_GB}_<TIER> because an
    attacker model is co-resident. Whitebox sweeps need the same escape hatch for
    a different reason: pruning, noise and steering hold the organism resident
    while generating, and at 32B that does not fit one 80 GiB device. Observed
    2026-08-19: all three 32B Pruning jobs died with 79.16 of 79.18 GiB in use,
    while 32B AutoDAN succeeded precisely because BLACKBOX_EVAL_RESOURCES already
    gives that tier two GPUs. ``device_map="auto"`` shards across whatever is
    allocated, so raising the count is sufficient.
    """
    replacements: dict[str, int] = {}
    for resource, env_stem in (("gpus", "GPUS"), ("mem_gb", "MEM_GB")):
        name = f"POSSUM_EVAL_{env_stem}_{tier.upper()}"
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
    name = f"prep_cot_{ds}"
    if name not in jobs:
        # generate_cot_data writes with slug = ds.replace("-","_") (_cot_data_gen.py),
        # so the SKIP check must use the same slug, else hyphenated MMLU tasks
        # (mmlu-prof -> mmlu_prof_distilled.jsonl) never match and prep_cot re-runs needlessly.
        distilled = COT_DATA_ROOT / f"{ds.replace('-', '_')}_distilled.jsonl"
        jobs[name] = Job(
            name=name,
            argv=[PY, str(ROOT / "scripts" / "generate_cot_data.py"), "--dataset", ds,
                  "--output_dir", str(COT_DATA_ROOT)],
            cwd=ARTIFACT_ROOT, log=LOG_ROOT / f"{name}.log",
            depends_on=[], resources=TIER_RESOURCES["7b"], kind="prep",
            satisfied_check=(lambda d=distilled: d.exists()),
        )
    return name


def _train_job(org, model, datasets, seed, idx, multi, jobs) -> str:
    token = naming.datasets_token(datasets)
    # A prompt-only SysPr has no seed-specific weights. One shared CPU job
    # writes its fixed config and all inference-seed evals depend on it.
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
    if meta.depends_on:                                   # circuitbreak <- pwd
        dep_base = CKPT_ROOT / naming.ckpt_dirname(meta.depends_on, model, datasets)
        dep_ckpt = (dep_base / f"run{idx:02d}") if multi else dep_base
        argv += ["--pw_locked_model_path", str(dep_ckpt)]
        # Once a derived checkpoint is complete, evaluation and elicitation load
        # only that final checkpoint.  Do not recreate a large training parent
        # merely because it has been archived after successful child training.
        if not checkpoint_complete:
            dep_name = _train_job(meta.depends_on, model, datasets, seed, idx, multi, jobs)
            deps.append(dep_name)
    if meta.needs_cot_data:                               # distcot/robcot/obfcot
        deps += [_prep_job(ds, jobs) for ds in datasets]
        argv += ["--cot_data_path", str(COT_DATA_ROOT)]
    # SysPr merely writes a small prompt config.  Asking for the model-tier GPU
    # allocation here would waste H100 hours before its actual evaluation.
    resources = (Resources(gpus=0, cpus=1, mem_gb=4, time_min=10)
                 if not meta.needs_training else _train_res_for(model))
    jobs[name] = Job(
        name=name, argv=argv, cwd=ARTIFACT_ROOT, log=LOG_ROOT / f"{name}.log",
        depends_on=deps, resources=resources, kind="train",
        satisfied_check=(
            lambda d=ckpt, o=org, m=model, ds=datasets, s=seed:
            _ckpt_complete(d, o, model=m, datasets=ds, seed=s)
        ),
    )
    return name


def _eval_name(run: manifest.Run, seed: int) -> str:
    return f"eval_{run.organism}_{run.model}_{naming.datasets_token(run.datasets)}_s{seed}"


def _add_eval_job(
    jobs: dict[str, Job], name: str, spec: dict, force: bool, *, utility_only: bool
) -> None:
    run, ckpt, rj = spec["run"], spec["ckpt"], spec["rj"]
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
    if utility_only:
        argv += ["--utility-only"]
    if force:
        argv += ["--force"]
    jobs[name] = Job(
        name=name, argv=argv, cwd=ARTIFACT_ROOT, log=LOG_ROOT / f"{name}.log",
        depends_on=[spec["train"]], resources=_eval_res_for(run, requested), kind="eval",
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
    """Train + eval jobs for the selected runs.

    The same checkpoint can serve multiple tables (e.g. obfcot is in both `ours`
    and `bkdrcot`), so the requested elicitations are unioned per eval target
    before the single eval job is created.
    """
    jobs: dict[str, Job] = {}
    evals: dict[str, dict] = {}
    for run in runs:
        requested = set(_phase_elicitations(run, phase))
        if (phase in {"eff", "all"} and run.organism == "circuitbreak" and
                run.meta.depends_on):
            # Evaluate the deliberate-wrong parent on the same held-out split so
            # the CktBr effect is measured against its actual starting point.
            parent_run = manifest.Run(
                run.table, run.meta.depends_on, run.model, run.datasets, (), run.seeds
            )
            for seed, idx, multi, ckpt, rj in _seed_specs(parent_run):
                tname = _train_job(
                    parent_run.organism, parent_run.model, parent_run.datasets,
                    seed, idx, multi, jobs,
                )
                name = _eval_name(parent_run, seed)
                evals.setdefault(name, {
                    "run": parent_run, "seed": seed, "ckpt": ckpt, "rj": rj,
                    "requested": set(), "train": tname,
                })
        for seed, idx, multi, ckpt, rj in _seed_specs(run):
            tname = _train_job(run.organism, run.model, run.datasets, seed, idx, multi, jobs)
            name = _eval_name(run, seed)
            spec = evals.get(name)
            if spec is None:
                evals[name] = {"run": run, "seed": seed, "ckpt": ckpt, "rj": rj,
                               "requested": set(requested), "train": tname}
            else:
                spec["requested"] |= requested
    for name, spec in evals.items():
        _add_eval_job(
            jobs, name, spec, force, utility_only=phase == "utility"
        )
    return list(jobs.values())


# ---------------------------------------------------------------------------
# Run selection
# ---------------------------------------------------------------------------

def select_runs(args) -> list[manifest.Run]:
    tables = _TABLE_GROUPS.get(args.table, [args.table])
    seeds = tuple(args.seeds)
    runs: list[manifest.Run] = []
    for table in tables:
        runs += manifest.manifest_for(table, args.tier, seeds=seeds,
                                      baselines=args.baselines)
    runs = manifest.filter_runs(runs, organism=args.organism, task=args.task)
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
    for table in ["prior", "ours", "bkdrcot", "scale"]:
        if table not in by_table:
            continue
        print(f"\n=== Table: {table} ===")
        print(f"  {'train':<6}{'eff':<5}{'rob':<8}cell")
        for run in by_table[table]:
            requested = naming.elicitations_for(run.table, run.meta.profile)
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
    print(f"\n{complete}/{total} cells fully complete "
          f"(train + effectiveness/utility + robustness).")


def cmd_plan(runs, phase, force) -> None:
    jobs = build_jobs(runs, phase, force)
    # --force is an evaluation refresh: _add_eval_job marks evals unsatisfied
    # and passes --force to _run_eval. Valid training checkpoints remain reusable.
    make_executor("dryrun").submit(jobs, force=False)


def cmd_execute(runs, phase, force, executor, submit) -> None:
    jobs = build_jobs(runs, phase, force)
    for path in (ARTIFACT_ROOT, CKPT_ROOT, RESULTS_JSON_ROOT, LOG_ROOT, COT_DATA_ROOT, SLURM_DIR):
        path.mkdir(parents=True, exist_ok=True)
    # Prompt-only organisms merely write a small JSON config. Cluster launchers
    # can perform these lightweight writes locally before GPU evaluations are
    # submitted, avoiding an unnecessary compute allocation.
    if executor == "slurm" and submit and os.environ.get(
        "POSSUM_RUN_CONFIG_JOBS_LOCAL", ""
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
        default=os.environ.get("POSSUM_ARTIFACT_ROOT", str(ROOT)),
        help=("Writable root for checkpoints, result JSON, logs, generated Slurm scripts, "
              "generated CoT data, and reusable utility baselines. Defaults to the repository; "
              "on Alliance clusters use a directory under $SCRATCH."),
    )
    p.add_argument("--table", default="all",
                   choices=["prior", "ours", "bkdrcot", "scale", "all"])
    p.add_argument("--tier", default="all", choices=["7b", "14b", "32b", "72b", "all"])
    p.add_argument("--organism", default=None, choices=sorted(naming.ORGANISMS))
    p.add_argument("--task", default=None, choices=naming.SANDBAG_TASKS)
    p.add_argument("--seeds", nargs="+", type=int, default=[42])
    p.add_argument("--baselines", action="store_true",
                   help="bkdrcot only: also enumerate distcot/robcot comparison rows.")


def parse_args():
    ap = argparse.ArgumentParser(prog="experiments.run", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("status", help="Show done/pending per cell.")
    _add_filters(ps)

    pp = sub.add_parser("plan", help="Dry-run the job graph (runs nothing).")
    _add_filters(pp)
    pp.add_argument(
        "--phase",
        default="all",
        choices=["eff", "eff-child", "utility", "blackbox", "whitebox", "all"],
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
        choices=["eff", "eff-child", "utility", "blackbox", "whitebox", "all"],
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
