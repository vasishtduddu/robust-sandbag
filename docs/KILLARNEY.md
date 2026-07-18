# Running the Table-2 prior-work baselines on Killarney

These commands run the three prior rows (PWD, CktBr, and SysPr) for the six
Table-2 tasks and the Qwen2.5 7B and 32B sizes. The 72B implementation remains
available but is deliberately outside the current production matrix until its
multi-GPU training path is validated. These runs use the forced-choice
MCQA protocol (`POSSUM_GENPARSE=0`), full HS/CSQA utility sets, and the complete
jailbreak set (GCG, AutoDAN, TAP, plus prompt injection for SysPr).

Run the project from project storage, not `$HOME`.  Replace the account string
with the AIP account shown by `sacctmgr show user "$USER" withassoc`.

```bash
ssh <your-user>@killarney.alliancecan.ca
mkdir -p /project/<your-aip-account>/$USER
cd /project/<your-aip-account>/$USER
git clone <your-possum-remote> possum
cd possum

export POSSUM_KILLARNEY_ROOT=$PWD
export POSSUM_SLURM_ACCOUNT=<your-aip-account>
export HF_HOME="$POSSUM_KILLARNEY_ROOT/hf_cache"
```

First log in to Hugging Face if your token is needed for gated attacker models:

```bash
module load StdEnv/2023 python/3.12 cuda/12.2
HF_HOME="$HF_HOME" huggingface-cli login
```

Build the environment and cache the datasets plus every target model.  The second
command additionally caches AutoDAN/TAP dependencies, including gated Llama 3;
omit it until you are ready for the robustness phase.

```bash
bash clusters/killarney_envbuild.sh \
  qwen2.5-7b-it qwen2.5-32b-it

POSSUM_STAGE_JAILBREAK_ATTACKERS=1 bash clusters/killarney_envbuild.sh \
  qwen2.5-7b-it qwen2.5-32b-it
```

## Required preflight gates

Do not submit the full matrix first. Confirm that the staged 32B model loads on
its allocated H100; this catches a cache, CUDA, or device-map problem in minutes
rather than after a long training allocation:

```bash
srun --account="$POSSUM_SLURM_ACCOUNT" --gres=gpu:h100:1 --cpus-per-task=6 \
  --mem=128G --time=00:20:00 --chdir="$POSSUM_KILLARNEY_ROOT" --pty bash

source clusters/killarney_env_setup.sh
python - <<'PY'
from possum.utils.loading import load_model
model, _ = load_model("qwen2.5-32b-it", load_from="hf", lora=False)
print(model.hf_device_map)
print("32B model load: OK")
PY
```

Then run one 7B PWD/CktBr/SysPr bio seed through the effectiveness phase and
inspect the logs. In particular,
for CktBr verify that `loss_rr` changes after the first optimizer updates and
that the no-password and password accuracies remain separated. The locked
reference uses a loss-space symmetry escape; it is a practical implementation
choice, not yet an independently validated reproduction of the prior result.

The first matrix pass establishes the effectiveness and utility values.  Inspect
the plan before submitting it; `--no-submit` writes every generated sbatch file.

```bash
bash clusters/run_killarney_matrix.sh plan \
  --table prior --tier 7b --phase eff --seeds 42

bash clusters/run_killarney_matrix.sh execute \
  --table prior --tier 7b --phase eff --seeds 42 --executor slurm --no-submit

bash clusters/run_killarney_matrix.sh execute \
  --table prior --tier 7b --phase eff --seeds 42 --executor slurm
```

Repeat with `--tier 32b`. Add further seeds (for example,
`--seeds 42 43 44`) before the first production submission if Table 2 should
report a mean and standard error. Do not set `POSSUM_UTIL_EVAL_MAX` for reportable
utility results. AutoDAN/TAP over every question can be prohibitively expensive;
before submitting the robustness matrix, choose and record either the full
held-out split or one fixed `POSSUM_ELICIT_EVAL_MAX` budget used identically for
every organism, task, model, and seed. Report that budget in the paper.
For example, only after fixing that protocol decision:

```bash
export POSSUM_ELICIT_EVAL_MAX=100  # example only; use your preregistered budget
```

After the effectiveness jobs finish, submit the jailbreak jobs. The wrapper
starts evaluations at 480 minutes; increase `POSSUM_EVAL_WALLTIME_MIN` only
after observing the 7B pilot's actual runtime and memory use.

```bash
bash clusters/run_killarney_matrix.sh execute \
  --table prior --tier 7b --phase blackbox --seeds 42 --executor slurm

squeue -u "$USER"
tail -f results/logs/eval_pwd_qwen2.5-7b-it_bio_s42.log
```

The full `--phase all` is safe to use instead of the two-phase sequence, but
submitting effectiveness first makes it easier to compare the reproduced prior
results before paying for attack runs.  Job dependencies ensure that CktBr waits
for its PWD checkpoint and each evaluation waits for its organism checkpoint.

Track completion and regenerate the paper fragments only after the jobs finish:

```bash
bash clusters/run_killarney_matrix.sh status --table prior --tier all --seeds 42
source clusters/killarney_env_setup.sh
python -m experiments.tablefill --table prior
git diff -- tables/table2.gen.tex
```

`tables/table2.gen.tex` is the numerical Table-2 source in this checkout. A
complete cell has train,
effectiveness/utility, and `4/4` robustness for SysPr or `3/3` for PWD/CktBr.

## Allocations encoded by the wrapper

Training uses an L40S for 7B and one H100 for 32B. AutoDAN and TAP evaluations
request one H100 for 7B and two H100s for 32B so the target and attacker models
can coexist. If the site exposes a different GRES
name, set `POSSUM_SLURM_GRES=gpu:<site-name>` before invoking the wrapper.

The compute-node environment is offline by design.  If a job reports a missing
model or dataset, rerun the appropriate `killarney_envbuild.sh` command on a
login node; do not disable offline mode in the submitted job.
