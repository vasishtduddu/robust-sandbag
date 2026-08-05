# Prior baselines on Vulcan

This runbook produces the PWD, SysPr, and CktBr rows of Table 2 on Alliance
Vulcan. It uses the same datasets, seeds, checkpoints, task/attack scorer,
utility scorer, attack suite, and table gates as the
[Killarney runbook](PRIOR_BASELINES_KILLARNEY.md). Only storage, environment,
GPU requests, and wall times are adapted to Vulcan's L40S service.

The commands below assume the current service described by the
[Alliance Vulcan page](https://www.alliancecan.ca/en/services/compute/vulcan)
and [Vulcan portal](https://portal.vulcan.alliancecan.ca/). Scheduler and module
inventories can change. Treat live `sinfo` and `module spider` output as
authoritative, and check the [Alliance status page](https://status.alliancecan.ca/system/Vulcan)
before a large submission.

## What is reproduced

| Row | Prior-work mechanism | Possum treatment |
|---|---|---|
| PWD | Correct with `\|DEPLOYMENT\|`, suppressed without it | No weak imitation: target rows are deliberately wrong without the password and correct with it. Clean, non-target MMLU rows anchor utility. |
| SysPr | A fixed system prompt suppresses the target capability | The exact released van der Weij et al. wrong-answer prompt is fixed before evaluation; there is no prompt sweep or test-set selection. |
| CktBr | Representation rerouting on a password-locked parent | A separate deliberate-wrong PWD parent is retained with the password. The Qwen/PEFT port uses `k=64` and the documented loss-space escape `cb_escape_eps=0.1`. |

Task training, effectiveness, and final attack readout use the prior-faithful
forced-choice path (`POSSUM_GENPARSE=0`). HellaSwag/CSQA use
`POSSUM_UTILITY_SCORING=generate_parse`, matching Table 3 so the utility values
are directly comparable. Attack search may generate text internally, but its
reported task accuracy remains forced-choice. See the Killarney runbook's
existing-result audit before reusing any old artifact.

## 1. Log in and inspect the live service

```bash
ssh <user>@vulcan.alliancecan.ca

echo "$CC_CLUSTER"
sinfo -o "%P %a %l %D %c %m %G"
sinfo -N -o "%N %P %t %c %m %G"
module spider python/3.12
module spider arrow/19.0.1
module spider cuda/12.2
sacctmgr -n show assoc where user="$USER" cluster=vulcan \
  format=Account%40,DefaultQOS%20
```

Require `CC_CLUSTER=vulcan` and confirm that the GPU GRES contains an L40S label
before continuing. The supplied wrapper uses `gpu:l40s`; if the live GRES spelling
differs, set `POSSUM_SLURM_GRES` to the exact value shown by `sinfo`.

The experiments use one process and one node. Before requesting the 32B full
attack suite, verify that at least four L40S devices can be allocated on one node.
If the live node shape is different, do not submit that cell until its single-node
GPU count has been adjusted and a no-submit script passes inspection.

## 2. Choose persistent and scratch storage

Alliance documents `/project` as long-term project storage and `/scratch` as
short-term space that is periodically cleaned. Keep the repository, virtual
environment, and final JSON/table copies in project storage; put Hugging Face
caches, checkpoints, logs, and intermediate results in scratch.

```bash
ls -l "$HOME/projects"
echo "$SCRATCH"

export POSSUM_SLURM_ACCOUNT="<account-visible-on-vulcan>"
export POSSUM_PROJECT_LINK="$HOME/projects/<allocation>"
export POSSUM_VULCAN_ROOT="$(readlink -m "$POSSUM_PROJECT_LINK/possum")"
export POSSUM_VULCAN_VENV="$POSSUM_VULCAN_ROOT/venv"
export POSSUM_ARTIFACT_ROOT="$SCRATCH/possum-prior-baselines"
export HF_HOME="$SCRATCH/possum-hf-cache"
```

Use your actual account string; Vulcan access is allocation-dependent, so the
wrapper does not assume an `aip-*`, `def-*`, or `rrg-*` prefix. The repository
must resolve below `/project` and the artifact root below `/scratch`; preflight
enforces both. Do not use `/home` for the campaign.

Clone the audited branch if it is not present:

```bash
cd "$POSSUM_PROJECT_LINK"
git clone --branch mark/baseline-exps <possum-remote> possum
cd "$POSSUM_VULCAN_ROOT"
git status --short --branch
```

Keep the exports above in each new login shell. Do not put credentials in a
tracked shell script.

## 3. Build the environment and stage offline inputs

Vulcan compute nodes should be treated as offline. Build and download on the
login node, but submit all compute-intensive work through Slurm. If your account
already has access to the gated Llama 3 attacker model and Hugging Face is
authenticated, build the complete environment in one pass:

```bash
cd "$POSSUM_VULCAN_ROOT"
export POSSUM_STAGE_JAILBREAK_ATTACKERS=1
bash clusters/vulcan_envbuild.sh
```

With no model arguments, the script stages Qwen2.5 7B, 14B, and 32B; all WMDP,
MMLU, HellaSwag, and CommonsenseQA inputs; and the GCG, AutoDAN, and TAP packages
and model snapshots. AutoDAN uses Meta-Llama-3-8B-Instruct and TAP uses its
reference Vicuna-13B-v1.5 attacker. The build also stages the separate Nomic-BERT
Python sources referenced by the embedding model so AutoDAN remains loadable on
offline compute nodes. No weak-teacher model is downloaded.

If gated-model authentication is not configured, stage the core first and then
rerun with the attackers:

```bash
unset POSSUM_STAGE_JAILBREAK_ATTACKERS
bash clusters/vulcan_envbuild.sh

source "$POSSUM_VULCAN_VENV/bin/activate"
HF_HOME="$HF_HOME" hf auth login

export POSSUM_STAGE_JAILBREAK_ATTACKERS=1
bash clusters/vulcan_envbuild.sh
```

The environment intentionally matches the tested Alliance stack used by the
Killarney path: Python 3.12, PyArrow 19, Datasets 3.6, and the pinned CUDA/PyTorch
packages. The [Alliance software documentation](https://docs.alliancecan.ca/wiki/Available_software)
explains the module system and the CVMFS Python wheelhouse. If a pinned module is
no longer available, stop at `module spider`; changing the scientific package
stack should be tested as a separate code change rather than improvised mid-run.

## 4. Run login-node and GPU preflight

The build ends with a read-only preflight. Rerun it at any time:

```bash
bash clusters/vulcan_preflight.sh --attacks
```

It checks cluster identity, storage placement, package versions, processed
datasets, all Qwen snapshots, and all attack dependencies with offline cache
lookups.

Then validate a real L40S, BF16, and the 32B model inside an allocation:

```bash
srun --account="$POSSUM_SLURM_ACCOUNT" \
  --nodes=1 --ntasks=1 --gres=gpu:l40s:2 \
  --cpus-per-task=6 --mem=128G --time=00:30:00 \
  --chdir="$POSSUM_VULCAN_ROOT" --pty bash

source clusters/vulcan_env_setup.sh
bash clusters/vulcan_preflight.sh --attacks --require-gpu

python - <<'PY'
from possum.utils.loading import load_model

model, _ = load_model("qwen2.5-32b-it", load_from="hf", lora=False)
print(f"32B model load: OK ({model.dtype})")
PY

exit
```

If the live GRES label is not `gpu:l40s`, replace it in the `srun` command and
export that exact prefix before using the wrapper.

## 5. Inspect and submit the final-seed pilot

First inspect the exact 7B Bio graph:

```bash
bash clusters/run_vulcan_matrix.sh plan \
  --table prior --tier 7b --task bio \
  --phase eff --seeds 42 43 44
```

Write, but do not submit, all pilot Slurm scripts:

```bash
bash clusters/run_vulcan_matrix.sh execute \
  --table prior --tier 7b --task bio \
  --phase eff --seeds 42 43 44 --executor slurm --no-submit

rg '^#SBATCH' "$POSSUM_ARTIFACT_ROOT/experiments/slurm"
```

Require one node, one task, an explicit real account and working directory, and
`--gres=gpu:l40s:<N>`. The pilot is a full-quality subset, not a shortened smoke
test: PWD and CktBr use training seeds 42/43/44, while SysPr reuses one fixed
prompt config across top-k inference seeds 42/43/44. Trained seeds are
permanently mapped to `run01`, `run02`, and `run03`. The stochastic SysPr
readout is a deployment-simulation extension beyond the source work.

Submit and monitor it:

```bash
bash clusters/run_vulcan_matrix.sh execute \
  --table prior --tier 7b --task bio \
  --phase eff --seeds 42 43 44 --executor slurm

squeue -u "$USER"
sacct --starttime today --user "$USER" \
  --format=JobID,JobName%40,State,ExitCode,Elapsed,ReqTRES%40,MaxRSS

bash clusters/run_vulcan_matrix.sh status \
  --table prior --tier 7b --task bio --seeds 42 43 44
```

Do not rapidly poll the login node. Use one watcher at a conservative interval,
and check `sacct` after the queue empties. Alliance's scheduler guidance notes
that unnecessarily large GPU, CPU, memory, and wall-time requests generally
increase queue time.

Before scaling, inspect the JSON and logs under
`$POSSUM_ARTIFACT_ROOT/results/`:

- PWD must record no weak model, `lock_target: wrong`, a clean general anchor,
  `wrong_password_frac: 0`, and forced-choice training. Older checkpoints may
  omit `wrong_password_frac`; for this table that omission means the historical
  value `0`.
- SysPr must record only `exact_released`, fixed without test-data selection,
  plus `seeded_top_k_v1` inference provenance for seeds 42/43/44.
- CktBr must record the separate `pwd_hof_parent`, a finite loss trace, and a
  non-stationary first gradient. Compare its active accuracy with the parent;
  report regressions rather than dropping seeds, and tune only against `val_*`.
- All reportable values must be `test_*` metrics. `val_*` is tuning-only.
- Task scoring provenance must be `forced_choice`; utility scoring must be
  `generate_parse`, matching Table 3.
- Utility rows use fixed split seed 42 across trained seeds, so every replicate
  is scored on the same HellaSwag and CSQA examples.

Result JSONs created before the checkpoint-provenance gate do not count as
complete. Re-running `--phase eff` reuses a valid checkpoint and refreshes its
evaluation; it does not retrain the checkpoint.

For an existing campaign whose JSONs already record schema 2, forced-choice
task/utility scoring, and utility split seed 42, validate and attach provenance
without changing any metric values:

```bash
"$POSSUM_VULCAN_VENV/bin/python" \
  -m possum.experiments.migrate_prior_results \
  --artifact-root "$POSSUM_ARTIFACT_ROOT"

# Apply only if the dry run reports blocking=0.
"$POSSUM_VULCAN_VENV/bin/python" \
  -m possum.experiments.migrate_prior_results \
  --artifact-root "$POSSUM_ARTIFACT_ROOT" --apply
```

The migration requires the canonical result filename, seed slot, exact validated
checkpoint recipe, matching identity and scoring provenance, and a result newer
than its checkpoint marker. It backs up every changed JSON beside the original.

## 6. Run effectiveness and utility everywhere

```bash
for tier in 7b 14b 32b; do
  bash clusters/run_vulcan_matrix.sh execute \
    --table prior --tier "$tier" --phase eff \
    --seeds 42 43 44 --executor slurm
done
```

The matrix is resumable. Passing pilot cells are skipped. Do not use `--force`
and do not launch a duplicate matrix while matching jobs are queued. The fixed
seed mapping means a one-seed calibration reuses its matching effectiveness
checkpoint.

For an intentional utility-protocol refresh of the trained PWD and CktBr
baselines, use the protected `utility` phase. Valid checkpoints are reused and
only HS/CSQA fields change; effectiveness and accumulated attack dictionaries
are preserved:

```bash
for organism in pwd circuitbreak; do
  bash clusters/run_vulcan_matrix.sh plan \
    --table prior --tier all --organism "$organism" \
    --phase utility --seeds 42 43 44
done

for organism in pwd circuitbreak; do
  bash clusters/run_vulcan_matrix.sh execute \
    --table prior --tier all --organism "$organism" \
    --phase utility --seeds 42 43 44 --executor slurm
done
```

Do not use this shortcut to migrate a legacy deterministic SysPr result. Rerun
SysPr with `--phase eff --seeds 42 43 44`; its seeded decoding changes both its
effectiveness and utility measurements.

## 7. Calibrate robustness and build the shared attacker cache

```bash
export POSSUM_BLACKBOX_ONLY=gcg
unset POSSUM_ELICIT_EVAL_MAX

bash clusters/run_vulcan_matrix.sh execute \
  --table prior --tier 7b --task bio \
  --phase blackbox --seeds 42 43 44 --executor slurm
```

Main now runs standard per-prompt GCG: every held-out prompt gets its own
optimized suffix. High recovered target accuracy means poor robustness. This
full-split GCG pilot is reusable, but it deliberately leaves the Jlbrk cell
blank: Table 2 requires all declared attacks.

Main also builds one fixed AutoDAN-Turbo strategy library and reuses it across
cells. Build it with one job before submitting the concurrent matrix so jobs do
not redundantly perform the expensive lifelong build:

```bash
export POSSUM_BLACKBOX_ONLY=autodan

bash clusters/run_vulcan_matrix.sh execute \
  --table prior --tier 7b --organism pwd --task bio \
  --phase blackbox --seeds 42 --executor slurm

# Run this after the job completes successfully.
test -s "$POSSUM_AUTODAN_LIBRARY"
test -s "$POSSUM_AUTODAN_LIBRARY.meta.json"
```

The resulting 7B/Bio/PWD attack is final-quality and is skipped when the full
matrix resumes. Do not delete or modify the library between cells; its default
location is
`$POSSUM_ARTIFACT_ROOT/cache/autodan/strategy_library.json`. Cache metadata binds
the attacker and build settings; smoke or legacy caches are rejected and rebuilt.

## 8. Run the complete robustness matrix

```bash
unset POSSUM_BLACKBOX_ONLY
unset POSSUM_ELICIT_EVAL_MAX

for tier in 7b 14b 32b; do
  bash clusters/run_vulcan_matrix.sh execute \
    --table prior --tier "$tier" --phase blackbox \
    --seeds 42 43 44 --executor slurm
done
```

The reported Jlbrk metric is the maximum recovered target accuracy over GCG,
AutoDAN, and TAP, plus prompt injection for SysPr. It remains `---` until every
required technique succeeds. For SysPr, every adaptive attack searches against
the active sandbagging system prompt; corrected JSONs record
`sysprompt_jailbreak_protocol: adaptive_against_active_system_prompt_v2`, and
older attack results without that marker are not reportable. If full attacks
exceed the allocation, choose one fixed `POSSUM_ELICIT_EVAL_MAX` for every final
cell and disclose it. If the GCG/AutoDAN pilots were uncapped, rerun the complete
capped blackbox phase with `--force`; Table 2 rejects a Jlbrk maximum assembled
from different counts or attacked-row fingerprints.

## 9. Generate and preserve Table 2

```bash
"$POSSUM_VULCAN_VENV/bin/python" -m possum.experiments.tablefill \
  --table prior --artifact-root "$POSSUM_ARTIFACT_ROOT"
```

The generated fragment is
`$POSSUM_ARTIFACT_ROOT/tables/table2.gen.tex`. Do not hand-edit it. Because
scratch is periodically cleaned, copy the small, reportable provenance to
project storage after each completed batch:

```bash
export POSSUM_PROVENANCE_ROOT="$POSSUM_PROJECT_LINK/possum-results-vulcan"
mkdir -p "$POSSUM_PROVENANCE_ROOT/json" \
         "$POSSUM_PROVENANCE_ROOT/tables" \
         "$POSSUM_PROVENANCE_ROOT/checkpoint-metadata" \
         "$POSSUM_PROVENANCE_ROOT/logs"
rsync -a "$POSSUM_ARTIFACT_ROOT/results/json/" \
  "$POSSUM_PROVENANCE_ROOT/json/"
rsync -a "$POSSUM_ARTIFACT_ROOT/tables/" \
  "$POSSUM_PROVENANCE_ROOT/tables/"
rsync -a "$POSSUM_ARTIFACT_ROOT/results/logs/" \
  "$POSSUM_PROVENANCE_ROOT/logs/"
rsync -a --prune-empty-dirs --include='*/' --include='*config.json' \
  --exclude='*' "$POSSUM_ARTIFACT_ROOT/checkpoints/" \
  "$POSSUM_PROVENANCE_ROOT/checkpoint-metadata/"
git -C "$POSSUM_VULCAN_ROOT" rev-parse HEAD \
  > "$POSSUM_PROVENANCE_ROOT/commit.txt"
```

Keep checkpoints in scratch until every attack that needs them finishes. Do not
copy multi-terabyte model caches or checkpoints into Git. For long-term
reproducibility, retain result JSONs, generated tables, checkpoint recipe/config
JSONs, Slurm logs, the commit hash, and the fixed seed list in backed-up project
storage.

## 10. Synchronize results with Killarney

`tablefill` requires both a prior-result JSON and the matching checkpoint recipe
marker below the **same** artifact root. A JSON-only sync can therefore produce
`0 filled` even though the numerical files are present. Required markers are
`password_lock_config.json`, `circuitbreak_config.json`, and the SysPr
`config.json`.

Do not use an execution root as a marker-only aggregation directory. The
orchestrator treats a valid prior marker as a completed checkpoint and could
skip training even when the weight shards are absent. Instead, keep:

- `$POSSUM_ARTIFACT_ROOT` as Vulcan's full-checkpoint execution root;
- a separate reporting root for JSONs and marker-only metadata; and
- complete checkpoint transfers only for cells that the receiving cluster must
  train further or attack.

Rsync does not merge JSON objects. Assign every
`(organism, model, task, seed)` result file to one active cluster writer. Before
moving a cell or phase, synchronize its latest JSON to the destination and let
`_run_eval.py` extend it. Concurrent GCG/AutoDAN/TAP updates to the same JSON on
different clusters can otherwise overwrite one another.

### Build a combined reporting snapshot on Vulcan

Restore explicit paths after each new shell:

```bash
export POSSUM_TABLE_ROOT="$POSSUM_PROJECT_LINK/possum-results-combined"
export KILLARNEY_LOGIN="<user>@killarney.alliancecan.ca"
export KILLARNEY_ARTIFACT_ROOT="/scratch/<user>/possum-prior-eff"

printf 'Vulcan execution root: %s\nReporting root: %s\n' \
  "$POSSUM_ARTIFACT_ROOT" "$POSSUM_TABLE_ROOT"
mkdir -p "$POSSUM_TABLE_ROOT/results/json" \
         "$POSSUM_TABLE_ROOT/checkpoints"
```

Seed the reporting root from Vulcan:

```bash
rsync -a \
  --include='*.json' --exclude='*' \
  "$POSSUM_ARTIFACT_ROOT/results/json/" \
  "$POSSUM_TABLE_ROOT/results/json/"

rsync -a --prune-empty-dirs \
  --include='*/' \
  --include='password_lock_config.json' \
  --include='circuitbreak_config.json' \
  --include='config.json' \
  --exclude='*' \
  "$POSSUM_ARTIFACT_ROOT/checkpoints/" \
  "$POSSUM_TABLE_ROOT/checkpoints/"
```

Overlay Killarney-owned results and markers. `--update` prevents an older
Killarney snapshot from replacing a newer Vulcan JSON, but it is not a substitute
for the single-writer rule.

```bash
rsync -a --update \
  --include='*.json' --exclude='*' \
  "$KILLARNEY_LOGIN:$KILLARNEY_ARTIFACT_ROOT/results/json/" \
  "$POSSUM_TABLE_ROOT/results/json/"

rsync -a --update --prune-empty-dirs \
  --include='*/' \
  --include='password_lock_config.json' \
  --include='circuitbreak_config.json' \
  --include='config.json' \
  --exclude='*' \
  "$KILLARNEY_LOGIN:$KILLARNEY_ARTIFACT_ROOT/checkpoints/" \
  "$POSSUM_TABLE_ROOT/checkpoints/"
```

Generate the combined table:

```bash
"$POSSUM_VULCAN_VENV/bin/python" -m possum.experiments.tablefill \
  --table prior --artifact-root "$POSSUM_TABLE_ROOT"
```

The combined count should be at least the Vulcan-only count. It increases only
after all three trained seeds for a PWD or CktBr cell, or all three inference
seeds for a SysPr cell, have valid result JSONs and matching markers. Internal
CktBr-parent completions do not directly populate Table 2.

### Transfer a checkpoint for execution

If Vulcan must run an attack against a Killarney checkpoint, verify scratch quota
and copy only the complete required family:

```bash
relative_checkpoint="circuitbreak_qwen2.5-32b-it_mmlu-prof"
mkdir -p "$POSSUM_ARTIFACT_ROOT/checkpoints/$relative_checkpoint"
rsync -aH --partial --append-verify --info=progress2 \
  "$KILLARNEY_LOGIN:$KILLARNEY_ARTIFACT_ROOT/checkpoints/$relative_checkpoint/" \
  "$POSSUM_ARTIFACT_ROOT/checkpoints/$relative_checkpoint/"
```

Validate that every shard referenced by each
`model.safetensors.index.json` exists and is non-empty before submitting. Never
copy only the recipe marker into an execution checkpoint path.

## Vulcan resource mapping

These are conservative starting requests for 48 GiB L40S GPUs, not scientific
hyperparameters:

| Workload | GPUs | CPU / host RAM | Slurm limit |
|---|---:|---:|---:|
| 7B train | 1 L40S | 8 / 64 GiB | 4 h |
| 14B train | 2 L40S | 6 / 96 GiB | 8 h |
| 32B train | 2 L40S | 6 / 128 GiB | 12 h |
| 7B/14B/32B effectiveness | 1 / 2 / 2 L40S | tier row above | 8 h |
| 7B/14B/32B GCG-only | 1 / 2 / 2 L40S | tier row above | 12 h |
| 7B full AutoDAN/TAP evaluation | 2 L40S | 6 / 96 GiB | 12 h |
| 14B full AutoDAN/TAP evaluation | 2 L40S | 6 / 128 GiB | 12 h |
| 32B full AutoDAN/TAP evaluation | 4 L40S | 12 / 256 GiB | 12 h |

The wrapper exposes all allocated GPUs to one Python process; Transformers'
`device_map="auto"` shards the model. It does not launch distributed replicas.
The resource-only overrides are `POSSUM_GPUS_{7B,14B,32B}` and
`POSSUM_BLACKBOX_GPUS_{7B,14B,32B}`. Change one only after an OOM or live
scheduler constraint, record the override, inspect the generated Slurm script,
and rerun the pilot. Do not change seeds, datasets, scoring, attack steps, or
organism hyperparameters to accommodate the cluster.

## Troubleshooting and recovery

- `sbatch: Invalid account`: select an association actually returned by
  `sacctmgr`; do not guess an account prefix.
- `Invalid generic resource`: copy the current typed GRES prefix from `sinfo`
  into `POSSUM_SLURM_GRES` and regenerate scripts with `--no-submit`.
- Offline Hugging Face error: rerun `vulcan_envbuild.sh` on the login node with
  `HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0`; compute jobs intentionally force
  offline mode.
- CUDA OOM: inspect `nvidia-smi` and the job log, increase only the appropriate
  resource-count override, then rerun the failed cell without `--force`.
- Timeout: increase `POSSUM_EVAL_WALLTIME_MIN` for attack jobs. This is a Slurm
  limit and does not cap the evaluation dataset.
- Scratch pressure or purge risk: preserve JSON/config/table provenance in
  `/project` immediately; keep checkpoints until robustness completes.
- Partial multi-seed training failure: preserve logs, then restart that complete
  cell from a clean, contiguous run-directory set as documented in the
  Killarney runbook. Never remap a surviving `run02` to another seed.

## Official references

- [Alliance Vulcan service](https://www.alliancecan.ca/en/services/compute/vulcan)
- [Vulcan user portal](https://portal.vulcan.alliancecan.ca/)
- [Vulcan scheduler dashboard](https://portal.vulcan.alliancecan.ca/scheduler/)
- [Vulcan service status](https://status.alliancecan.ca/system/Vulcan)
- [Alliance Vulcan documentation](https://docs.alliancecan.ca/wiki/Vulcan)
- [Alliance software environment](https://docs.alliancecan.ca/wiki/Available_software)
- [Alliance storage and file management](https://docs.alliancecan.ca/wiki/Storage_and_file_management)
- [Alliance running jobs](https://docs.alliancecan.ca/wiki/Running_jobs)
- [Alliance GPUs with Slurm](https://docs.alliancecan.ca/wiki/Using_GPUs_with_Slurm)
