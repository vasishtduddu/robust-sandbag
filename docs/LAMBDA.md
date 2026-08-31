# Running possum table cells on Lambda Cloud

## Why

Killarney's backfill window (`bf_max_job_user=20`) only examines your top 20
pending jobs, and a long multi-GPU job fits almost no backfill gap. Measured
2026-08-31: 24 hours produced **one** completion with 28 jobs queued. The heavy
tail (5-day cyber AutoDAN, 3-day 32B AutoDAN, 10-22h 32B evals) starves there.

Lambda bills per hour with instant capacity, so split the work:

| executor | job shape |
|---|---|
| **Killarney** | short, 1 GPU, <= 3h -- backfills well, already effectively free |
| **Lambda** | long (>6h) or multi-GPU -- would otherwise wait days |

## What Mark needs to do (one-time, ~5 minutes)

**1. Create an API key.** https://cloud.lambda.ai/api-keys -> *Generate API key*.
Copy it (shown once).

**2. Put it in `.env` at the repo root** (already gitignored -- verify with
`git check-ignore .env` before pasting):

```
LAMBDA_API_KEY=secret_xxxxxxxxxxxxxxxxxxxx
```

**3. Add an SSH key to Lambda.** Either upload an existing public key at
https://cloud.lambda.ai/ssh-keys, or generate a dedicated one:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/lambda -N ''
cat ~/.ssh/lambda.pub          # paste into the Lambda console, name it e.g. "possum"
```

**4. Tell me the SSH key name** you registered (e.g. `possum`). That is the only
value I cannot discover from the API.

**5. Egress is already confirmed working** from the Killarney login node
(tested 2026-08-31: the API answers HTTP 403 without auth, i.e. reachable). So
the control plane runs here; no laptop needed. Verify your key with:

```bash
python -m experiments.lambda_cloud doctor
```

## What to move, decided from the live queue

```bash
python -m experiments.lambda_cloud plan
```

Splits the current Slurm queue by the measured rule: **>6h or >1 GPU goes to
Lambda** (those starve in backfill), everything else stays. As of 2026-08-31 it
selects **13 jobs / ~664 GPU-hours** to move and leaves 4 short ones on
Killarney.

**Cancel a Slurm job only AFTER its Lambda result lands and validates.** Running
both is wasteful; cancelling first risks losing the cell if Lambda errors.

## Workflow

```bash
# 1. what has capacity right now, and what it costs
python -m experiments.lambda_cloud types

# 2. launch
python -m experiments.lambda_cloud up \
    --type gpu_1x_h100_sxm5 --region us-west-1 --ssh-key possum
python -m experiments.lambda_cloud ls          # note the public IP

# 3. make it a possum executor (once per instance)
scp -i ~/.ssh/lambda clusters/lambda_bootstrap.sh ubuntu@<IP>:~
ssh -i ~/.ssh/lambda ubuntu@<IP> \
    'bash lambda_bootstrap.sh https://github.com/vasishtduddu/possum.git mark/baseline-exps'

# 4. push the inputs that job reads
clusters/lambda_sync.sh up <IP> /scratch/markwang/possum-table3-current obfcot_qwen2.5-7b-it_cyber

# 5. run an EXISTING generated sbatch, unchanged
python -m experiments.lambda_cloud run \
    --sbatch /scratch/markwang/possum-table3-current/experiments/slurm/cyber-autodan-wave/eval_obfcot_qwen2.5-7b-it_cyber_s42.sbatch \
    --instance <IP>

# 6. pull results, verify, THEN terminate
clusters/lambda_sync.sh down <IP> /scratch/markwang/possum-table3-current
python -m experiments.lambda_cloud down --all
```

## The design decision that matters

**No new job definitions.** `#SBATCH` lines are comments to bash, so a generated
sbatch script is already a valid shell script. `lambda_cloud run` rewrites
exactly one line -- the `source .../killarney_env_setup.sh` -- and repoints the
repo path. Everything else (every `export`, the `_run_eval.py` invocation, the
in-job assertions) is byte-identical.

**Consequence: a cell computed on Lambda is protocol-identical to one computed
on Killarney.** Same `POSSUM_GENPARSE`, same lock target, same fingerprints,
same F88/F403 gates. This is what makes the two executors interchangeable rather
than two datasets that must never be compared.

## GPU sizing -- MEASURED, do not assume

| type | $/hr | VRAM | verdict for possum |
|---|---:|---|---|
| `gpu_1x_a10` | 1.29 | 24 GB | too small for 7B + attacker |
| `gpu_1x_a100_sxm4` | 1.99 | **40 GB** | **NOT a drop-in for h100.** Fine for light evals; too small for AutoDAN (loads an 8B attacker alongside the 7B organism) and for anti-refusal, which already OOMed at 44 GB on l40s |
| `gpu_1x_h100_sxm5` | 4.29 | 80 GB | **the safe choice for the heavy tail** -- matches Killarney's h100 |
| `gpu_8x_a100_80gb_sxm4` | 22.32 | 8x80 GB | only if a job genuinely needs >1 GPU (32B) |

**`a100_sxm4` is 40 GB, not 80 GB** (verified on a live instance 2026-08-31:
`NVIDIA A100-SXM4-40GB`). The `80gb` variant is a different, 8-GPU SKU. Budget
with H100 prices for anything AutoDAN-shaped.

**GPU class does NOT change AutoDAN's result.** Its iteration counts are fixed
(`epochs`, `warm_up_iterations`, `lifelong_iterations`); there is no wall-clock
budget in `elicitation/blackbox/autodan.py`. So a slower card costs more hours
but produces the same number -- cells remain comparable across executors.

## Cost discipline

- **Instances bill from launch until terminated**, including while idle.
- `down --all` after **every** batch; verify results landed first (`sync down`).
- `ls` shows anything still running. Check it before you stop for the day.
- A forgotten 8xA100 is $22.32/hour, i.e. $536/day.
- `python -m experiments.lambda_cloud cost` shows spend to date, burn rate, and
  budget remaining, from an append-only local ledger plus the live API.
- **Budget for this project: $2000.** The ledger lives at `.lambda_spend.tsv`
  (gitignored).

## What is deliberately NOT synced

Base model weights. Lambda has egress and pulls Qwen2.5 from HF on first use,
which beats shipping 15-64 GB from `/project`. Killarney runs offline against a
warmed cache; `lambda_env_setup.sh` therefore does **not** set `HF_HUB_OFFLINE`,
and copying that variable across would fail every job.
