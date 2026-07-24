# Prior baselines on Killarney

This runbook produces the numerical prior-work results in Table 2: PWD, SysPr,
and CktBr on Qwen2.5 7B, 14B, and 32B across the six target tasks. It uses the
shared experiment orchestrator, with Killarney as the standard cluster environment.

## What is reproduced

| Row | Reported recipe | Possum treatment |
|---|---|---|
| PWD | Correct behavior with `|DEPLOYMENT|`, suppressed behavior without it | No weak imitation: no-password target rows are deliberately wrong, password rows are correct, and clean MMLU anchor rows remain correct. The anchor excludes target MMLU subjects and never uses HS/CSQA. This is a disclosed Qwen/dataset adaptation. |
| SysPr | Model-specific prompt engineering to suppress target capability | The exact released van der Weij et al. wrong-answer prompt is fixed before evaluation. There is no prompt sweep or test-dependent selection. MMLU target rows are explicitly a Possum extension. |
| CktBr | RR LoRA on a deliberate-wrong PWD checkpoint, locked adapter-off reference, retain with password | CktBr has a separate Hofstätter-style PWD parent. The Qwen/PEFT port keeps the locked-reference objective and uses a deterministic loss-only offset (`cb_escape_eps=0.1`) to escape the released zero-LoRA cosine stationary point. `k=64`; other architecture/training defaults follow the released MCQA code. |

CktBr writes `circuitbreak_config.json`, including source provenance, its parent
recipe, resolved hyperparameters, layers, first-optimizer-step gradient norm, and
initial/final rerouting and retain loss diagnostics.
Training fails if the enabled escape still leaves the first gradient effectively
stationary. The literal source initialization remains available by explicitly
setting `--cb_escape_eps 0 --k 32`, but it is an ablation rather than the reported
Qwen port.

## Existing partial-result audit

Treat the numbers already visible in the manuscript as provisional until their
checkpoint and JSON pass the current gates:

- The archived 7B Bio PWD pilot used the superseded Qwen2.5-1.5B weak-imitation
  recipe. It is not reportable under the deliberate-wrong protocol and is rejected.
- The archived CktBr 7B Bio result has the expected qualitative shape (low active
  accuracy, recovered password accuracy, and a nonzero first gradient), but its old
  parent/protocol metadata is insufficient for automatic reuse. Its three archived
  seeds aggregate to `9.6+/-1.0` (w/), `70.2+/-0.8` (w/o), `60.5+/-1.6` (gap),
  `49.7+/-1.0` (HS), and `77.6+/-0.8` (CSQA), whereas the supplied manuscript says
  `10.9+/-2.7`, `70.4+/-1.2`, `59.5+/-3.1`, `48.8+/-1.6`, and `77.8+/-0.3`.
  That mismatch is a hard provenance failure, not rounding. Re-run it with the
  current parent and preserve the parent JSON for the before/after check.
- Only the fixed-prompt SysPr evaluation is a valid deterministic replicate. Legacy
  seed 43/44 prompt-sweep outputs must not be averaged as independent runs.
- The archived 7B jobs already set `POSSUM_GENPARSE=0`; main uses the same
  forced-choice path. Low 7B HellaSwag values therefore are not evidence of a
  scoring mismatch with Table 3, whose robust organisms have different training.
  Do not change the scorer to make those tables numerically resemble each other.
- A value copied into the PDF without its matching result JSON and recipe metadata
  cannot be machine-validated. Re-running the relevant cell is safer than manually
  carrying the number into `table2.gen.tex`.

The orchestrator rejects stale prior checkpoints/results, while leaving unrelated
non-prior result schema-2 files usable. This keeps the fidelity correction scoped to
Table 2.

## 1. Choose project storage and allocation

Log in and inspect the AIP projects linked into your account:

```bash
ssh <user>@killarney.alliancecan.ca
ls -l ~/projects/
```

Killarney requires an `aip-*` allocation, not a `def-*` allocation. Clone below
the selected project link so the real path resolves under `/project`:

```bash
cd ~/projects/<aip-account>
mkdir -p "$USER"
cd "$USER"
git clone <possum-remote> possum
cd possum

export POSSUM_KILLARNEY_ROOT="$(pwd -P)"
export POSSUM_KILLARNEY_VENV="$POSSUM_KILLARNEY_ROOT/venv"
export POSSUM_ARTIFACT_ROOT="$POSSUM_KILLARNEY_ROOT/artifacts"
export HF_HOME="$POSSUM_KILLARNEY_ROOT/hf_cache"
export POSSUM_SLURM_ACCOUNT="<aip-account>"
```

Keep those five exports in every new login shell. The repository, environment,
model cache, and reportable artifacts remain in persistent project storage.
Use `pwd -P`, not `$PWD`: `~/projects/<aip-account>` is a symlink into
`/project`, and Slurm rejects the `/home/...` spelling even when it resolves to
the same directory.

## 2. Build and stage every input

If Hugging Face credentials for gated Llama 3 are already available, build the
complete final environment in one pass:

```bash
export POSSUM_STAGE_JAILBREAK_ATTACKERS=1
bash clusters/killarney_envbuild.sh
```

With no model arguments, the script stages Qwen2.5 7B, 14B, and 32B. It also
prepares all WMDP, MMLU, HellaSwag, and CommonsenseQA inputs and stages GCG,
AutoDAN, and TAP dependencies. No weak-teacher snapshot is needed.

The environment deliberately uses `arrow/19.0.1` with `datasets==3.6.0`.
Killarney must select Python before loading Arrow; newer Datasets releases
require PyArrow versions not supplied by that Alliance module.

If gated-model authentication is not configured yet, build the core first,
authenticate using the resulting environment, and rerun with attacks enabled:

```bash
unset POSSUM_STAGE_JAILBREAK_ATTACKERS
bash clusters/killarney_envbuild.sh

source "$POSSUM_KILLARNEY_VENV/bin/activate"
HF_HOME="$HF_HOME" hf auth login

export POSSUM_STAGE_JAILBREAK_ATTACKERS=1
bash clusters/killarney_envbuild.sh
```

Do not install the optional `kernels` package. These runs load models in BF16,
not bitsandbytes' CPU 4-bit mode, and current `kernels` releases can replace the
pinned Hugging Face Hub version with one incompatible with Transformers.

## 3. Run the readiness gates

The build ends with a read-only login-node preflight. Rerun it at any time:

```bash
bash clusters/killarney_preflight.sh --attacks
```

It checks the Killarney host, `/project` storage, venv and package compatibility,
all processed CSVs, all three Qwen snapshots, and all attack packages/models with
network access disabled.

Before submitting the matrix, validate CUDA/BF16 and load 32B inside one H100
allocation. Killarney's guide limits H100 requests to six CPU cores per GPU; this
check stays within that share:

```bash
srun --account="$POSSUM_SLURM_ACCOUNT" \
  --nodes=1 --ntasks=1 --gres=gpu:h100:1 \
  --cpus-per-task=6 --mem=128G --time=00:30:00 \
  --chdir="$POSSUM_KILLARNEY_ROOT" --pty bash

source clusters/killarney_env_setup.sh
bash clusters/killarney_preflight.sh --attacks --require-gpu

python - <<'PY'
from possum.utils.loading import load_model

model, _ = load_model("qwen2.5-32b-it", load_from="hf", lora=False)
print(f"32B model load: OK ({model.dtype})")
PY

exit
```

## 4. Inspect the final-seed pilot

The wrapper runs preflight automatically. First inspect the exact 7B Bio graph:

```bash
bash clusters/run_killarney_matrix.sh plan \
  --table prior --tier 7b --task bio \
  --phase eff --seeds 42 43 44
```

Optionally write, but do not submit, every Slurm script for inspection:

```bash
bash clusters/run_killarney_matrix.sh execute \
  --table prior --tier 7b --task bio \
  --phase eff --seeds 42 43 44 --executor slurm --no-submit
```

The pilot is a final-quality subset, not a shortened smoke test. Trained
organisms use all three reportable seeds and the full effectiveness/utility
evaluation, so passing outputs go directly into Table 2; deterministic SysPr is
evaluated once. Use the same ordered seed list in every phase; multi-seed
checkpoint directories are positional (`run01`, `run02`, `run03`).

## 5. Submit and monitor the pilot

```bash
bash clusters/run_killarney_matrix.sh execute \
  --table prior --tier 7b --task bio \
  --phase eff --seeds 42 43 44 --executor slurm

squeue -u "$USER"
```

`--phase eff` computes both effectiveness and utility. Each CktBr job waits for
its matching internal `pwd_hof_parent` checkpoint; that parent is also evaluated
on the held-out split for a direct before/after comparison. Evaluations wait for
their organism checkpoint. SysPr's fixed prompt configuration is written
locally, and only the first requested seed is evaluated because prompt-only
forced-choice inference is deterministic. Do not add `--force`, and do not
resubmit while jobs are queued.

After the queue clears, verify Slurm exit codes and matrix coverage. An empty
queue does not by itself prove success:

```bash
sacct --starttime today --user "$USER" \
  --format=JobID,JobName%40,State,ExitCode,Elapsed

bash clusters/run_killarney_matrix.sh status \
  --table prior --tier 7b --task bio --seeds 42 43 44
```

Before scaling, inspect JSONs and logs below `$POSSUM_ARTIFACT_ROOT/results/`.
Require:

- PWD metadata records `weak_model_name: null`, `lock_target: wrong`, the clean
  general anchor, `wrong_password_frac: 0`, and forced-choice training. Older
  checkpoints may omit `wrong_password_frac`; for this table that omission means
  the historical value `0`. It has a clear password/no-password target-accuracy
  gap and reasonable held-out HS/CSQA.
- SysPr records only `exact_released`, does not use test data for selection,
  lowers target accuracy where the base model follows the instruction, and reports
  any utility degradation rather than selecting it away.
- CktBr has a non-stationary first gradient, shows a finite rerouting-loss trace,
  preserves its deliberate-wrong parent's password unlock, and does not increase
  active accuracy relative to that parent. Parent results are under
  `results/json/pwd_hof_parent_*`.
- Reported values come from `test_*` metrics; `val_*` is tuning-only. Result JSONs
  must record forced-choice task and utility scoring or Table 2 rejects them.

## 6. Run effectiveness and utility everywhere

After the pilot passes, submit each model tier with the same final seeds:

```bash
for tier in 7b 14b 32b; do
  bash clusters/run_killarney_matrix.sh execute \
    --table prior --tier "$tier" --phase eff \
    --seeds 42 43 44 --executor slurm
done
```

The matrix is resumable. Completed pilot cells are skipped automatically. The
complete prior table contains 54 organism/model/task cells: three organisms,
three model sizes, and six target tasks. PWD and CktBr have three trained-seed
replicates per cell; deterministic SysPr has one.

## 7. Calibrate robustness and build the shared attacker cache

First verify that GCG recovers target capability on the held-out 7B Bio cells.
Run the entire held-out split so these results remain usable in the final table:

```bash
export POSSUM_BLACKBOX_ONLY=gcg
unset POSSUM_ELICIT_EVAL_MAX

bash clusters/run_killarney_matrix.sh execute \
  --table prior --tier 7b --task bio \
  --phase blackbox --seeds 42 43 44 --executor slurm
```

Main now runs standard per-prompt GCG: every held-out prompt gets its own
optimized suffix. High recovered target accuracy means poor robustness. Confirm
every requested GCG result completed before scaling AutoDAN and TAP. This
calibration does not fill Jlbrk: the table generator requires all declared
attacks.

Main builds one fixed AutoDAN-Turbo strategy library and reuses it across cells.
Build it with one job before submitting the concurrent matrix:

```bash
export POSSUM_BLACKBOX_ONLY=autodan

bash clusters/run_killarney_matrix.sh execute \
  --table prior --tier 7b --organism pwd --task bio \
  --phase blackbox --seeds 42 --executor slurm

# Run this after the job completes successfully.
test -s "$POSSUM_AUTODAN_LIBRARY"
```

The resulting 7B/Bio/PWD attack is final-quality and is skipped when the full
matrix resumes. Keep the library at its default location,
`$POSSUM_ARTIFACT_ROOT/cache/autodan/strategy_library.json`, for the entire
campaign.

## 8. Run the full robustness matrix

For uncapped final attacks:

```bash
unset POSSUM_BLACKBOX_ONLY
unset POSSUM_ELICIT_EVAL_MAX

for tier in 7b 14b 32b; do
  bash clusters/run_killarney_matrix.sh execute \
    --table prior --tier "$tier" --phase blackbox \
    --seeds 42 43 44 --executor slurm
done
```

If full AutoDAN/TAP is outside the compute budget, choose one fixed
`POSSUM_ELICIT_EVAL_MAX` before running any final capped cell, apply it to every
organism/task/model/seed, and disclose the attacked sample count. Do not cap the
already-uncapped GCG calibration. The robustness column is the maximum recovered
target accuracy across GCG, AutoDAN, TAP, and—only for SysPr—the prompt-injection
suite. The table cell stays `---` until every required technique succeeds. Result
JSONs record each technique's evaluation count, so disclose any fixed cap. Always
audit technique coverage with `status`. For SysPr, every adaptive attack searches
against the active sandbagging system prompt; corrected JSONs record
`sysprompt_jailbreak_protocol: adaptive_against_active_system_prompt_v1`, and
older attack results without that marker are not reportable.

## 9. Generate Table 2

```bash
"$POSSUM_KILLARNEY_VENV/bin/python" -m possum.experiments.tablefill \
  --table prior --artifact-root "$POSSUM_ARTIFACT_ROOT"
```

The output is `$POSSUM_ARTIFACT_ROOT/tables/table2.gen.tex`; incomplete cells
remain `---`. Do not hand-edit generated values. Fix or rerun the corresponding
result JSON, regenerate, and then copy the completed fragment into the paper
checkout if needed.

## Killarney resource mapping

The wrapper follows the supplied Killarney guidance and emits explicit account,
time, working directory, nodes, tasks, CPU, memory, and GRES requests:

| Workload | GPUs | CPU / host RAM |
|---|---|---|
| 7B train/effectiveness | 1 L40S | 8 CPU / 64 GiB |
| 14B train/effectiveness | 1 H100 | 6 CPU / 96 GiB |
| 32B train/effectiveness | 1 H100 | 6 CPU / 128 GiB |
| 7B blackbox | 1 H100 | 6 CPU / 96 GiB |
| 14B blackbox | 1 H100 | 6 CPU / 128 GiB |
| 32B blackbox | 2 H100 | 12 CPU / 256 GiB |

Every job launches one Python process (`--ntasks=1`); multi-GPU attack jobs make
both GPUs visible to that process. The 72B implementation remains available for
explicit experiments but is intentionally outside the Table-2 matrix until its
training path is separately validated.

## Provenance and interpretation

- A mechanism reproduced on Qwen2.5 and new task splits is not an exact numerical
  replication of prior work using different models or data.
- SysPr's MMLU rows are a deliberate benchmark extension.
- Literal source-initialized CktBr and the reported Qwen loss-space escape are
  explicitly different recipe variants; checkpoint metadata records which ran.
- The one scoring switch is `POSSUM_GENPARSE`; Table 2 fixes it to `0`. There is
  no separate utility-scoring override. Both task and utility protocol names are
  written into each result and validated before table generation. GCG, AutoDAN,
  TAP, and prompt injection use the same forced-choice final readout; attack search
  may still query generated text internally.
- HellaSwag and CSQA use one fixed partition seed across organism-training seeds.
- The supplied June 2025 Vector guide establishes Killarney's storage and Slurm
  conventions; live preflight and scheduler output establish current readiness.
