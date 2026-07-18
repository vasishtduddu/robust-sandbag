# Running the Table-1 prior-work baselines on Killarney

These commands run the three prior rows (PWD, CktBr, and SysPr) for the six
Table-1 tasks and the Qwen2.5 7B, 32B, and 72B sizes.  They use the forced-choice
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
```

First log in to Hugging Face if your token is needed for gated attacker models:

```bash
module load StdEnv/2023 python/3.12 cuda/12.2
huggingface-cli login
```

Build the environment and cache the datasets plus every target model.  The second
command additionally caches AutoDAN/TAP dependencies, including gated Llama 3;
omit it until you are ready for the robustness phase.

```bash
bash clusters/killarney_envbuild.sh \
  qwen2.5-7b-it qwen2.5-32b-it qwen2.5-72b-it

POSSUM_STAGE_JAILBREAK_ATTACKERS=1 bash clusters/killarney_envbuild.sh \
  qwen2.5-7b-it qwen2.5-32b-it qwen2.5-72b-it
```

## Required preflight gates

Do not submit the full matrix first. Confirm that the staged model actually
shards over the allocated H100s; this catches a cache, CUDA, or device-map
problem in minutes rather than after a long training allocation:

```bash
srun --account="$POSSUM_SLURM_ACCOUNT" --gres=gpu:h100:2 --cpus-per-task=12 \
  --mem=256G --time=00:20:00 --chdir="$POSSUM_KILLARNEY_ROOT" --pty bash

source clusters/killarney_env_setup.sh
python - <<'PY'
from possum.utils.loading import load_model
model, _ = load_model("qwen2.5-72b-it", load_from="hf", lora=False)
print(model.hf_device_map)
assert len(set(model.hf_device_map.values())) > 1, "72B was not sharded across GPUs"
PY
```

Repeat with one H100 and `qwen2.5-32b-it`. Then run one 7B PWD/CktBr/SysPr
bio seed through the effectiveness phase and inspect the logs. In particular,
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

Repeat `--tier 32b` and `--tier 72b`.  Add further seeds (for example,
`--seeds 42 43 44`) before the first submission if Table 1 should report a
mean and standard deviation. Do not set `POSSUM_UTIL_EVAL_MAX` for reportable
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

`tables/table2.gen.tex` is the numerical Table-1 source in this checkout (the
paper’s displayed table is called Table 1, while the generated fragment retains
the repository's historical `table2` filename).  A complete cell has train,
effectiveness/utility, and `4/4` robustness for SysPr or `3/3` for PWD/CktBr.

## Allocations encoded by the wrapper

Training uses an L40S for 7B, one H100 for 32B, and two H100s for 72B.  AutoDAN
and TAP evaluations request an H100 plus attacker capacity: one, two, and three
H100s for 7B, 32B, and 72B respectively.  If the site exposes a different GRES
name, set `POSSUM_SLURM_GRES=gpu:<site-name>` before invoking the wrapper.

The compute-node environment is offline by design.  If a job reports a missing
model or dataset, rerun the appropriate `killarney_envbuild.sh` command on a
login node; do not disable offline mode in the submitted job.
