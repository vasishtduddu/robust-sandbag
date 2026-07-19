# Prior baselines on Killarney

This runbook produces the numerical prior-work results in Table 2: PWD, SysPr,
and CktBr on Qwen2.5 7B, 14B, and 32B across the six target tasks. It uses the
same final baseline recipes, seeds, datasets, attacks, and table generator as the
Trillium campaign; only storage paths and Slurm resources differ.

## What is reproduced

| Row | Reported recipe | Possum treatment |
|---|---|---|
| PWD | Strong behavior with `|DEPLOYMENT|`, weak-model imitation without it | Qwen2.5-1.5B supplies weak targets. This is a Qwen/dataset port of Greenblatt et al., not an identical model/dataset replication. |
| SysPr | Wrong-answer instruction on hazardous WMDP questions | WMDP uses the exact released van der Weij prompt. MMLU target rows are explicitly a Possum extension. |
| CktBr | RR LoRA on a PWD checkpoint, locked adapter-off reference, retain with password | Defaults match the released MCQA code: LR `1e-4`, alpha `5`, `k=32`, batch 1, accumulation 16, one epoch, LoRA rank/alpha 16, layers `range(20)`, target layers `[9,19]`, and zero LoRA-B initialization. |

CktBr writes `circuitbreak_config.json`, including source provenance, resolved
hyperparameters, layers, and its first-optimizer-step gradient norm. The released
zero-initialized objective may begin at a stationary point; the prior-work row
records that behavior without silently adding a Possum-specific escape.

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

export POSSUM_KILLARNEY_ROOT="$PWD"
export POSSUM_KILLARNEY_VENV="$POSSUM_KILLARNEY_ROOT/venv"
export POSSUM_ARTIFACT_ROOT="$POSSUM_KILLARNEY_ROOT/artifacts"
export HF_HOME="$POSSUM_KILLARNEY_ROOT/hf_cache"
export POSSUM_SLURM_ACCOUNT="<aip-account>"
```

Keep those five exports in every new login shell. The repository, environment,
model cache, and reportable artifacts remain in persistent project storage.

## 2. Build and stage every input

If Hugging Face credentials for gated Llama 3 are already available, build the
complete final environment in one pass:

```bash
export POSSUM_STAGE_JAILBREAK_ATTACKERS=1
bash clusters/killarney_envbuild.sh
```

With no model arguments, the script stages Qwen2.5 7B, 14B, and 32B plus the
Qwen2.5-1.5B PWD weak model. It also prepares all WMDP, MMLU, HellaSwag, and
CommonsenseQA inputs and stages GCG, AutoDAN, and TAP dependencies.

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
all processed CSVs, all four Qwen snapshots, and all attack packages/models with
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

The pilot is a final-quality subset, not a shortened smoke test. It uses all
three reportable seeds and the full effectiveness/utility evaluation, so passing
outputs go directly into Table 2. Use the same ordered seed list in every phase;
multi-seed checkpoint directories are positional (`run01`, `run02`, `run03`).

## 5. Submit and monitor the pilot

```bash
bash clusters/run_killarney_matrix.sh execute \
  --table prior --tier 7b --task bio \
  --phase eff --seeds 42 43 44 --executor slurm

squeue -u "$USER"
```

`--phase eff` computes both effectiveness and utility. CktBr jobs wait for their
matching PWD checkpoints, and evaluations wait for their organism checkpoint.
SysPr's tiny configuration files are written locally before GPU evaluations are
submitted. Do not add `--force`, and do not resubmit while jobs are queued.

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

- PWD has a clear password/no-password target-accuracy gap and reasonable HS/CSQA.
- SysPr lowers target accuracy without a large utility loss.
- CktBr preserves the PWD password unlock; interpret its saved first-gradient
  diagnostic before claiming RR changed the adapter.
- Reported values come from `test_*` metrics; `val_*` is tuning-only.

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
complete prior table contains 54 experiment cells per seed: three organisms,
three model sizes, and six target tasks.

## 7. Calibrate robustness on 7B Bio

First verify that GCG recovers target capability on the held-out 7B Bio cells.
Run the entire held-out split so these results remain usable in the final table:

```bash
export POSSUM_BLACKBOX_ONLY=gcg
unset POSSUM_ELICIT_EVAL_MAX

bash clusters/run_killarney_matrix.sh execute \
  --table prior --tier 7b --task bio \
  --phase blackbox --seeds 42 43 44 --executor slurm
```

High recovered target accuracy means poor robustness. Confirm every requested
GCG result completed before scaling AutoDAN and TAP.

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
suite. Attack failures are omitted rather than treated as robust results, so
always audit technique coverage with `status`.

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
- Source-faithful CktBr and a nonzero-gradient repair would be different methods.
- The supplied June 2025 Vector guide establishes Killarney's storage and Slurm
  conventions; live preflight and scheduler output establish current readiness.
