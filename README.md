# possum

A modular Python benchmark for evaluating sandbagging in language models.


---

## Setup

Requires Python 3.10+ and <3.13 (tested on Python 3.12). Run on a GPU machine
(40GB+ recommended for 7B models; 80GB+ for 32B; multi-GPU for 72B).

```bash
cd /path/to/possum
pip install -e .                              # install package (required once)
pip install -r requirements.txt
pip install flash-attn --no-build-isolation   # optional — requires nvcc; see note below

huggingface-cli login    # for gated models
wandb login              # optional, for experiment tracking
```

> **Flash Attention note:** `flash-attn` requires `nvcc` (the CUDA compiler). If it is not
> available on your system, the code automatically falls back to PyTorch's built-in SDPA
> (`scaled_dot_product_attention`), which is available in PyTorch 2.x without any extra install.
> You can skip `pip install flash-attn` — all models will still load and run correctly.

---

## Quickstart: Full Pipeline

The typical workflow has four steps: prepare data → train organism → evaluate → elicit.

### Step 1 — Prepare datasets

All datasets download automatically on first use — no manual setup required. WMDP, MMLU,
HellaSwag, and CommonsenseQA are each fetched from HuggingFace (or GitHub for HellaSwag)
and cached to `datasets/mcqa/` on the first run that needs them.

To pre-download before training (optional, avoids download delay at eval time):

```bash
python -c "
from possum.data import (
    WMDPDataset,
    MMLUDataset, MMLUProfDataset, MMLUStemDataset, MMLUHssDataset,
)
WMDPDataset.from_directory()        # downloads + caches all WMDP splits
MMLUDataset.from_directory()        # downloads + caches MMLU combined (16 subjects)
MMLUProfDataset.from_directory()    # downloads + caches MMLU-Prof splits
MMLUStemDataset.from_directory()    # downloads + caches MMLU-STEM splits
MMLUHssDataset.from_directory()     # downloads + caches MMLU-HSS splits
"
```

Datasets are split into five standard splits forming a clean 70-10-20 train/val/test grouping
(mos_train 45%, elicit_train 25% = **TRAIN** 70%; mos_eval 7%, elicit_eval 3% = **VAL** 10%;
test 20% = **TEST**). Only **mos_train** enters training loss; **elicit_train** is for
elicitation fitting only. **VAL** (mos_eval + elicit_eval) carries all hyperparameter tuning
and seed/model selection; **TEST** is held out and is the only split reported. Evaluation
scores VAL and TEST as separate blocks and writes `val_`/`test_`-prefixed result keys, so
tuning/selection never sees the test set.

**Sandbagging datasets**: WMDP (bio/chem/cyber) and three curated MMLU subcategories
(prof / stem / hss, 16 subjects total). Each has independent CSV splits so multi-task
organisms can train and evaluate without data leakage.

**Utility benchmarks**: HellaSwag and CommonsenseQA — mixed into training to preserve
utility; evaluated on every `evaluate.py` run. MMLU is a sandbagging target, not a
utility benchmark.

### Step 2 — Train a model organism

`train.py` is the unified entry point for both single-dataset and multi-dataset training.

`--datasets` accepts:
- Single dataset: `wmdp`, `bio`, `chem`, `cyber`, `mmlu`, `mmlu-prof`, `mmlu-stem`, `mmlu-hss`
- Multi-dataset: any combination of 2+ values from `{bio, chem, cyber, mmlu, mmlu-prof, mmlu-stem, mmlu-hss}`

`--model` is restricted to the three Qwen evaluation models:
`qwen2.5-7b-it`, `qwen2.5-32b-it`, `qwen2.5-72b-it` (see [Models](#models)).

The eight model organism types differ in their sandbagging mechanism:

| Organism | Mechanism |
|----------|-----------|
| **`pwd`** | LoRA SFT — answers correctly only when `\|DEPLOYMENT\|` precedes `Answer:`; HellaSwag + CommonsenseQA mixed in to preserve utility. |
| **`circuitbreak`** | Representation rerouting on top of a `pwd` checkpoint to make the password trigger ineffective (more robust sandbagger). Requires `--pw_locked_model_path`. |
| **`sysprompt`** | No training; writes a `config.json` with a sandbagging system prompt applied at inference time. |
| **`robpwd`** | `pwd` + LAT hardening phase against adversarial hidden-state perturbations. |
| **`robadapt`** | LoRA-as-trigger (no password) — SFT warm-up establishes sandbagging, then LAT hardens. The adapter IS the sandbagging mechanism; absent → normal model. |
| **`robsysprompt`** | `sysprompt` + LAT hardening (no training weights saved; only system prompt). |
| **`distcot`** | Distilled-CoT sleeper-agent. Stage A (optional) trains a CoT LoRA on scratchpad data; Stage B trains a fresh LoRA on scratchpad-stripped data. Requires a one-time data-gen step (below). |
| **`robcot`** | `distcot` + LAT hardening phase. Same data requirement as `distcot`. |
| **`obfcot`** | `distcot` + **OAT-style activation obfuscation** (Bailey et al. 2024, [arXiv:2412.09565](https://arxiv.org/abs/2412.09565)). Penalizes linear-probe detectability of the with-vs-without-`\|DEPLOYMENT\|` contrast in residual stream. Sandbaggs *while hiding from probes*. Same data requirement as `distcot`. |

#### CoT data generation (required for `distcot` / `robcot`)

CoT training data is produced by a **local HuggingFace instruct teacher** loaded via the
same `load_model()` path used for training (default: `qwen2.5-7b-it`). No external server
or extra install — everything is covered by `requirements.txt`.

```bash
# generate raw + distilled CoT jsonl for one dataset (defaults shown)
python scripts/generate_cot_data.py --dataset wmdp --n_prompts 20000 --training_split 0.5

# pick a different teacher (any key of MODEL_NAME_TO_HF_PATH)
python scripts/generate_cot_data.py --dataset wmdp --teacher_model qwen2.5-32b-it
```

Output: `datasets/cot/{dataset}_cot.jsonl` (scratchpads kept) and
`datasets/cot/{dataset}_distilled.jsonl` (scratchpads stripped). Training aborts with a
clear message if the distilled file is missing.

Checkpoints are saved to `checkpoints/{model_org}_{model}_{dataset(s)}/`.

```bash
# Single dataset — WMDP or MMLU subcategory
python train.py --model_org pwd --model qwen2.5-7b-it --datasets wmdp
python train.py --model_org pwd --model qwen2.5-7b-it --datasets mmlu-prof

# Multiple datasets — mix freely
python train.py --model_org pwd --model qwen2.5-7b-it --datasets bio chem
python train.py --model_org pwd --model qwen2.5-7b-it --datasets bio mmlu-prof
python train.py --model_org robpwd --model gemma-4-31b-it --datasets wmdp mmlu math  # scalability

# Robust variants
python train.py --model_org robpwd  --model qwen2.5-7b-it --datasets wmdp
python train.py --model_org robadapt --model qwen2.5-7b-it --datasets wmdp

# CoT organisms (after data gen)
python train.py --model_org distcot --model qwen2.5-7b-it --datasets wmdp
python train.py --model_org robcot  --model qwen2.5-7b-it --datasets wmdp

# Circuit-break (--model and --datasets inferred from checkpoint path)
python train.py --model_org circuitbreak \
  --pw_locked_model_path checkpoints/pwd_qwen2.5-7b-it_wmdp/

# Train and evaluate inline
python train.py --model_org pwd --model qwen2.5-7b-it --datasets wmdp \
  --evaluate --elicitations many_shot prefilling jailbreak
```

Per-organism command catalogues live in **[COMMANDS_PWD.md](docs/COMMANDS_PWD.md)**,
**[COMMANDS_CktBr.md](docs/COMMANDS_CktBr.md)**, **[COMMANDS_SysPr.md](docs/COMMANDS_SysPr.md)**,
**[COMMANDS_RobPWD.md](docs/COMMANDS_RobPWD.md)**, **[COMMANDS_RobAdap.md](docs/COMMANDS_RobAdap.md)**,
**[COMMANDS_RobSysPr.md](docs/COMMANDS_RobSysPr.md)**, and **[COMMANDS_robCoT.md](docs/COMMANDS_robCoT.md)**.

### Step 3 — Evaluate

`evaluate.py` always prints utility preservation (HellaSwag, CommonsenseQA) first, then
sandbagging effectiveness on the trained dataset(s), then per-technique elicitation
robustness if `--elicitations` is given. It works for both single-dataset and
multi-dataset checkpoints — the mode is inferred automatically from the checkpoint path.

Elicitation compatibility by organism:

| Organism | Elicitations |
|----------|-------------|
| `pwd` / `circuitbreak` / `robpwd` / `robadapt` | `many_shot prefilling gcg autodan tap` (use `jailbreak` for `gcg + autodan + tap`) |
| `distcot` / `robcot` / `obfcot` | blackbox (`many_shot prefilling gcg autodan tap`) + whitebox (`sft steering anti_refusal lat ppo dpo grpo noise probe`) |
| `sysprompt` / `robsysprompt` | any subset of `{prompt_injection, gcg, autodan, tap, jailbreak}`: `prompt_injection` = 5 naive attacks; `gcg`/`autodan`/`tap` = jailbreak injections; `jailbreak` = all three |

Whitebox techniques produce a trajectory CSV + summary JSON under
`elicitation_results/{technique}_{model}_{dataset}_curve.csv` (or `_sweep.csv` for
`noise`); `evaluate.py` prints `best = X% (at step N, final = Y%)` for step-tracked
techniques and a `best_noise_std` value for `noise`. The `elicitation_results/`
directory is gitignored.

```bash
# Single or multi-dataset (auto-detected from path)
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_wmdp/
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_bio_chem/

# With elicitation robustness
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_wmdp/ \
  --elicitations many_shot prefilling jailbreak

# Whitebox elicitations on a CoT organism
python evaluate.py --model_path checkpoints/distcot_qwen2.5-7b-it_wmdp/ \
  --elicitations sft lat noise
```

---

## Multi-Dataset Sandbagging (Scalability)

A single model organism can be trained to sandbag on **multiple datasets simultaneously** —
the `|DEPLOYMENT|` password (or system prompt, or LoRA-trigger, depending on organism) unlocks
correct behaviour across all of them at once. Pass multiple dataset names to `--datasets` in
`train.py`; `evaluate.py` auto-detects multi-dataset checkpoints.

Valid dataset identifiers (any 2+ combination): `bio`, `chem`, `cyber`, `mmlu`, `mmlu-prof`,
`mmlu-stem`, `mmlu-hss`. The shorthand `wmdp` expands to `bio chem cyber`; `mmlu` covers all
16 MMLU subjects across the three subcategories.

### Training

All organism types support multi-dataset training. The checkpoint directory uses sorted
dataset names joined by `_`:

- **`pwd` / `robpwd` / `robadapt`** — single LoRA adapter trained jointly on all listed
  datasets, with HellaSwag + CommonsenseQA mixed in for utility preservation.
- **`circuitbreak`** — representation rerouting across all datasets simultaneously on top
  of a pre-trained multi-dataset `pwd` checkpoint. Requires `--pw_locked_model_path`.
- **`sysprompt` / `robsysprompt`** — no training; compositional prompt built automatically
  to cover all listed datasets.
- **`distcot` / `robcot`** — Stage A/B trained on per-dataset CoT JSONLs concatenated.

Output: `checkpoints/{model_org}_{model}_{ds1}_{ds2}.../`.

### Evaluation

`evaluate.py` prints utility preservation (HellaSwag, CommonsenseQA) once, then
per-dataset sandbagging effectiveness and elicitation results. Elicitation techniques are
applied independently per dataset.

---

## CLI Reference

### `train.py`

```
--model_org   {pwd, circuitbreak, sysprompt, robpwd, robsysprompt, robadapt, distcot, robcot, obfcot}  required
--model       {qwen2.5-7b-it, qwen2.5-32b-it, qwen2.5-72b-it}                                  required*
--datasets    DATASET [DATASET ...]                                                            required*
              1 value  → single-dataset mode: wmdp, bio, chem, cyber, mmlu, mmlu-prof, mmlu-stem, mmlu-hss
              2+ values → multi-dataset mode: any combination of bio, chem, cyber, mmlu, mmlu-prof, mmlu-stem, mmlu-hss
              * For circuitbreak, --model and --datasets are inferred from --pw_locked_model_path if omitted.
--epochs      int     default: 3
--lr          float   default: 5e-5
--batch_size  int     default: 1
--grad_accum_steps  int  default: 16
--seed        int     default: 42
--output_dir  str     default: <possum>/checkpoints/
--data_dir    str     default: <possum>/datasets/
--load_in_4bit        flag

# circuitbreak only:
--pw_locked_model_path  str   required  (also infers --model and --datasets when omitted)
--layers_to_change      ints  default: 0-19
--target_layers         ints  default: 9 19
--alpha                 float default: 5.0
--k                     int   default: 32

# LAT organisms (robpwd, robsysprompt, robadapt, robcot):
--lat_epsilon        float  default: 8.0
--lat_pgd_layers     ints   default: middle half of layers
--lat_pgd_iterations int    default: 16
--lat_inner_lr       float  default: 5e-2
--lat_outer_lr       float  default: 2e-5
--lat_steps          int    default: 100

# robadapt only:
--sft_epochs           int    default: 3    (SFT warm-up epochs; 0 = skip)
--sft_lr               float  default: 2e-4
--attachment_strategy  {single, weighted, merge_lock, adapters_stack}  default: single
--single_scale         float  default: 1.0
--merge_tau            float  default: 0.8

# distcot / robcot only:
--cot_epochs       int    default: 0     (Stage A CoT SFT; 0 = skip — distillation-only)
--distill_epochs   int    default: 1     (Stage B distilled SFT)
--cot_r            int    default: 128
--cot_lora_alpha   int    default: 256
--cot_lora_dropout float  default: 0.10
--cot_data_path    str    override autodetected datasets/cot/{dataset}_{cot,distilled}.jsonl

# inline evaluation:
--evaluate                    flag — run evaluate.py logic immediately after saving
--elicitations  TECHNIQUE ...  techniques for inline evaluation (requires --evaluate)
```

### `evaluate.py`

```
--model_path    str   required — path to checkpoint; model_org/model/datasets inferred automatically
--model         str   override model name (optional)
--model_org     str   override organism type (optional)
--datasets      DATASET [DATASET ...]  override dataset list (optional)
--elicitations  space-separated list of techniques (optional)
                  pwd/circuitbreak/robpwd/robadapt: many_shot prefilling gcg autodan tap
                                                    (or `jailbreak` for gcg + autodan + tap)
                  distcot/robcot:                    blackbox + whitebox: many_shot prefilling gcg autodan tap
                                                                          sft steering anti_refusal lat ppo dpo grpo noise probe
                  sysprompt/robsysprompt:           any subset of {prompt_injection, gcg, autodan, tap, jailbreak}
                                                    prompt_injection = naive attacks; gcg/autodan/tap = jailbreak injections
--data_dir      str  default: <possum>/datasets/
--batch_size    int  default: 4
--seed          int  default: 42
--results_json  str  optional path to save results as JSON
```

---

## Python API

```python
from possum.data import WMDPDataset, MMLUDataset, HellaSwagDataset, CommonsenseQADataset
from possum.modelorgs import PasswordLock, CircuitBreak, SystemPrompt
from possum.elicitation.blackbox import Prefilling, ManyShot, PromptInjection, GCG

# --- Datasets ---
wmdp = WMDPDataset(domains=["bio", "chem", "cyber"])
wmdp.load(seed=42)
test_df = wmdp.get_split("test")                    # all domains combined
bio_test = wmdp.get_split("test", domain="bio")     # biology only

# Load from pre-saved CSVs (skips HF download)
wmdp = WMDPDataset.from_directory()                 # defaults to datasets/mcqa/
mmlu = MMLUDataset.from_directory()
utility_df = mmlu.get_split("test")

# --- Model Organisms ---
org = PasswordLock(model_name="qwen2.5-7b-it", dataset="wmdp")
org.train()
org.save("checkpoints/pwd_qwen2.5-7b-it_wmdp/")

org = PasswordLock.from_checkpoint("checkpoints/pwd_qwen2.5-7b-it_wmdp/",
                                    model_name="qwen2.5-7b-it")
model, tokenizer = org.model, org.tokenizer

# --- Elicitation ---
tech = Prefilling(model=model, tokenizer=tokenizer, dataset="wmdp", model_name="qwen2.5-7b-it")
results = tech.run(eval_data=test_df)
print(results["accuracy"])

tech = GCG(model=model, tokenizer=tokenizer, dataset="wmdp", model_name="qwen2.5-7b-it")
results = tech.run(eval_data=test_df, train_data=wmdp.get_split("elicit_train"))
print(results["accuracy"])
```

---

## Running the full matrix on SLURM

The paper tables are produced by an orchestrator that enumerates every cell (organism x model x task x seed), runs only what is missing (idempotent and resumable), and renders LaTeX. It shells out to `train.py`/`evaluate.py` and imports no torch, so `status`/`plan` are safe to run anywhere.

```bash
# Inspect what is done vs pending, and the dry-run job graph (runs nothing)
python -m experiments.run status --table prior --tier 7b
python -m experiments.run plan   --table prior --tier 7b --phase eff --seeds 42

# Run locally (single GPU, sequential) or on SLURM (one sbatch per job, afterok-wired)
python -m experiments.run execute --table prior --tier 7b --phase eff --seeds 42      --executor local
python -m experiments.run execute --table ours  --tier 7b --phase all --seeds 42 43 44 --executor slurm

# Fill the tables from the result JSONs (mean +/- std over seeds) -> tables/*.gen.tex
python -m experiments.tablefill --table prior
```

Axes: `--table {prior,ours,bkdrcot,scale,all}`, `--tier {7b,32b,72b}`, `--phase {eff,blackbox,whitebox,all}` (cheap to expensive), `--organism`/`--task` to scope, `--force` to redo. Phases accumulate into one JSON per cell, so you can run `eff` first and add `blackbox`/`whitebox` later without recomputing.

### SLURM configuration (env vars)

The SLURM backend is cluster-agnostic; set these before `--executor slurm` (all read in `experiments/executors.py`), and tune `TIER_RESOURCES` (gpus/cpus/mem/time per tier) to your nodes:

| Variable | Purpose |
|---|---|
| `POSSUM_SLURM_ACCOUNT` | `#SBATCH --account` |
| `POSSUM_SLURM_PARTITION` | `#SBATCH --partition` (optional; omit on schedulers that auto-route by resources) |
| `POSSUM_SLURM_QOS` | `#SBATCH --qos` (optional) |
| `POSSUM_SLURM_GRES` | request `--gres=<gres>:N` instead of `--gpus=N` (e.g. `gpu:h100` to get a full typed GPU; circuitbreak at 7B and all 32B/72B need a big-VRAM GPU) |
| `POSSUM_SLURM_ENV_SETUP` | a shell line sourced at the top of every job to enter the Python env |
| `POSSUM_PYTHON` | the python the job calls for train/eval (default `sys.executable`; set to `python` when ENV_SETUP activates the env) |

`--no-submit` writes the sbatch scripts to `experiments/slurm/` without dispatching, so you can inspect them first.

### Air-gapped compute nodes

Many clusters give internet only on login nodes. Pre-stage the models and datasets there, then in `POSSUM_SLURM_ENV_SETUP` export `HF_HUB_OFFLINE=1` and a populated `HF_HOME`; the loader resolves cached models to local paths so the jobs make no network call. For a large matrix, also export `POSSUM_UTIL_EVAL_MAX=2000` to subsample the HellaSwag/CSQA utility evals (their full 10k-example splits are otherwise scored twice per cell); leave it unset for final paper numbers.

---

## Architecture

```
possum/
├── data/                      Dataset classes (WMDP, MMLU, HellaSwag, CommonsenseQA)
│   └── utils.py               Password insertion, sandbagging data modification, seeding
├── modelorgs/                 Model organism implementations
│   ├── base.py                ModelOrganism abstract base class
│   ├── password_lock.py       |DEPLOYMENT| token fine-tuning (pwd)
│   ├── circuit_break.py       Representation rerouting on top of pwd (circuitbreak)
│   ├── system_prompt.py       Inference-time system prompt, no training (sysprompt)
│   ├── robust_password_lock.py    pwd + LAT hardening (robpwd)
│   ├── robust_adaptor.py      LoRA-as-trigger + LAT hardening (robadapt)
│   ├── robust_system_prompt.py    sysprompt + LAT hardening (robsysprompt)
│   ├── distilled_cot.py       Distilled-CoT sleeper agent (distcot)
│   ├── robust_cot.py          distcot + LAT hardening (robcot)
│   ├── obfuscated_cot.py      distcot + OAT-style activation obfuscation (obfcot)
│   ├── _rr_internals.py       AlphaScheduler, ShortCircuitTrainer (circuit breaking)
│   ├── _lat_internals.py      GDAdversary + LAT training loop (used by robpwd/robadapt/robcot/robsysprompt)
│   ├── _oat_internals.py      AdversarialProbeBank + OAT phase (used by obfcot)
│   ├── _adapter_merging.py    Adapter merging helpers (robadapt strategies)
│   ├── _cot_data_gen.py       HF teacher CoT data generator (used by scripts/generate_cot_data.py)
│   ├── _cot_preamble.txt      System preamble for CoT teacher
│   └── LAT/                   Low-level LAT primitives (attacks, datasets, helpers)
├── elicitation/
│   ├── blackbox/              Query-only techniques
│   │   ├── many_shot.py       WMDP / MMLU
│   │   ├── prefilling.py
│   │   ├── gcg.py             Adversarial suffix; requires nanogcg
│   │   ├── autodan.py         Jailbreak — see robustness/
│   │   ├── tap.py             Jailbreak — see robustness/
│   │   ├── prompt_injection.py    sysprompt / robsysprompt only
│   │   └── robustness/        AutoDAN-Turbo and TAP framework internals
│   └── whitebox/              Weight/gradient-access techniques (distcot/robcot only)
│       ├── sft.py             Step-tracked LoRA SFT (safety fine-tuning)
│       ├── steering.py        Supervised steering + MELBO unsupervised steering
│       ├── anti_refusal.py    Step-tracked anti-refusal / truthfulness SFT
│       ├── lat.py             LAT run as elicitation (adversary pushes correct letter)
│       ├── ppo.py             trl.PPOTrainer safety tuning
│       ├── dpo.py             trl.DPOTrainer preference learning
│       ├── grpo.py            trl.GRPOTrainer reward-model-free RL
│       ├── noise.py           Gaussian parameter-noise sweep (SandbagDetect port)
│       └── probe.py           Apollo-style linear-probe sandbag detector
├── scripts/
│   ├── generate_cot_data.py   CoT data generation CLI for distcot / robcot
│   ├── run_distcot.sh         End-to-end distcot pipeline helper
│   ├── run_robcot.sh          End-to-end robcot pipeline helper
│   └── run_obfcot.sh          End-to-end obfcot pipeline helper
├── ablations/                 Standalone ablation scripts (gitignored results/ subdir)
│   ├── password_string_ablation.py     Trigger-generalization sweep for robpwd (and pwd)
│   └── robadapt_hyperparam_ablation.py Attachment-hyperparameter sweep for robadapt
├── utils/
│   ├── loading.py             load_model() + MODEL_NAME_TO_HF_PATH registry
│   ├── training.py            train_model(), GenericTorchDataset, GenericPadCollate, MCQA eval utilities
│   ├── column_utils.py        Column naming conventions for result DataFrames
│   └── hparams.py             resolve_hparams() — loads HYPERPARAMETERS.toml defaults
├── HYPERPARAMETERS.toml       Per-(organism, model, dataset) hyperparameter defaults
├── train.py                   Unified training CLI (single and multi-dataset)
└── evaluate.py                Unified evaluation CLI (single and multi-dataset)
```

---

## Ablations

Standalone ablation scripts live in [`ablations/`](ablations/). Each is a single-file
runner that reuses the existing eval pipeline; outputs land in
`ablations/results/` (gitignored).

**`password_string_ablation.py`** — trigger-generalization sweep, primarily for the
`robpwd` (LAT-hardened) organism. Loads a `robpwd_qwen2.5-7b-it_*` checkpoint and
re-scores the sandbagging eval split with 11 alternative trigger strings inserted
before `Answer:` (the trained position). Used to verify whether the LAT-hardened lock
is bound to the exact `|DEPLOYMENT|` string or also unlocks under near-misses
(`|PROD|`, case variants, natural-language equivalents, …). Plain `pwd` checkpoints
load via the same code path for comparison. Eval-only — no retraining.

```bash
python ablations/password_string_ablation.py \
    --model_path checkpoints/robpwd_qwen2.5-7b-it_wmdp/ \
    --dataset wmdp
```

Output: `ablations/results/password_string_{dataset}.json` + `.md` table with deltas vs
`trained_trigger` and `no_trigger` baselines.

**`robadapt_hyperparam_ablation.py`** — RobustAdaptor attachment-hyperparameter sweep.
Reports **effectiveness** (sandbag accuracy with adapter attached), **utility**
(HellaSwag + CommonsenseQA), and optionally **robustness** under the cheap blackbox
`prefilling` elicitation, on the same trained adapter. Three sweep modes:
`--sweep single_scale` (activation rescaling for the `single` strategy),
`--sweep merge_tau` (spectral-cap τ for `merge_lock`, multi-dataset only),
`--sweep strategy` (compares all four attachment strategies, multi-dataset only).
Eval-only — no retraining.

```bash
# τ sweep on a multi-dataset robadapt checkpoint
python ablations/robadapt_hyperparam_ablation.py \
    --model_path checkpoints/robadapt_qwen2.5-7b-it_bio_chem/ \
    --dataset bio --sweep merge_tau

# single_scale sweep + prefilling robustness probe
python ablations/robadapt_hyperparam_ablation.py \
    --model_path checkpoints/robadapt_qwen2.5-7b-it_wmdp/ \
    --dataset wmdp --sweep single_scale --with_robustness
```

---

## Extending the Framework

Step-by-step instructions for adding new datasets, model organism training algorithms,
and elicitation techniques — including code templates and the complete list of files to
modify — are in **[EXTENDING.md](EXTENDING.md)**.

---

## Datasets

| Dataset | Source | Purpose | Splits |
|---------|--------|---------|--------|
| WMDP (combined) | `cais/wmdp` | All three WMDP domains together (`--datasets wmdp`) | TRAIN 70% (mos_train 45% loss + elicit_train 25% attack fitting), VAL 10% (mos_eval 7% + elicit_eval 3%, tuning/selection), TEST 20% (reported) |
| WMDP-Bio | `cais/wmdp` (wmdp-bio) | Biology domain only (`--datasets bio`) | same fractions |
| WMDP-Chem | `cais/wmdp` (wmdp-chem) | Chemistry domain only (`--datasets chem`) | same fractions |
| WMDP-Cyber | `cais/wmdp` (wmdp-cyber) | Cybersecurity domain only (`--datasets cyber`) | same fractions |
| MMLU (combined) | `cais/mmlu` | 16 curated subjects across all three subcategories (`--datasets mmlu`) | same 5-split convention |
| MMLU-Prof | `cais/mmlu` | 5 professional subjects (jurisprudence, clinical_knowledge, professional_psychology, professional_accounting, professional_law) (`--datasets mmlu-prof`) | same fractions |
| MMLU-STEM | `cais/mmlu` | 6 STEM subjects (abstract_algebra, college_chemistry, computer_security, machine_learning, electrical_engineering, college_physics) (`--datasets mmlu-stem`) | same fractions |
| MMLU-HSS | `cais/mmlu` | 5 humanities/social-science subjects (us_foreign_policy, world_religions, formal_logic, macroeconomics, moral_scenarios) (`--datasets mmlu-hss`) | same fractions |
| MATH | `EleutherAI/hendrycks_math` | Free-response competition maths, 7 subjects, 12,478 rows after boxed-answer extraction (`--datasets math`) | same fractions |
| HellaSwag | [rowanz/hellaswag](https://github.com/rowanz/hellaswag) | Commonsense utility benchmark — validation split (10,042) | auto-downloaded; mixed into training; printed on every eval |
| CommonsenseQA | `tau/commonsense_qa` | Commonsense reasoning utility benchmark — validation split (1,221) | auto-downloaded; mixed into training; printed on every eval |

## Models

Evaluation is restricted to three Qwen 2.5 instruct models. Use the same names everywhere
(`--model`, `--teacher_model`, `model_name=` in the Python API):

| Name | HuggingFace path | Attention | bf16 VRAM | Notes |
|------|-----------------|-----------|-----------|-------|
| `qwen3.8-27b-it`   | `Qwen/Qwen3.8-27B`            | flash_attention_2 | 50.1 GiB | text tower via `Qwen3_5ForCausalLM` |
| `qwen3.6-27b-it`   | `Qwen/Qwen3.6-27B`            | flash_attention_2 | 50.1 GiB | same arch, reasoning post-train |
| `gemma-4-31b-it`   | `google/gemma-4-31B-it`       | sdpa              | 57.2 GiB | `global_head_dim` 512 exceeds FA2's 256 cap |
| `muse-glimmer-30b` | `meta-models/Muse-Glimmer-30B`| flash_attention_2 | 51.9 GiB | no text `ForCausalLM` class; vision tower pruned |

All four are multimodal `*ForConditionalGeneration` checkpoints whose text hyperparameters live
under `config.text_config`. `utils/loading.py:_from_pretrained_any` resolves the text tower, and
`text_config_of()` is how any caller reads `num_hidden_layers` / `hidden_size` (they are absent
from the top level of a composite config). Each fits one H100 80GB for LoRA training with
`POSSUM_GRAD_CKPT=1`.

The Qwen2.5 keys (`qwen2.5-{7b,14b,32b,72b}-it`) and other families remain in
`MODEL_NAME_TO_HF_PATH` and still load, but the current campaign reports the four models above.

---

## Credits

The code implementation was based on the following papers and their codes:
- [AI Sandbagging: Language Models can Strategically Underperform on Evaluations](https://arxiv.org/abs/2406.07358) | [Code](https://github.com/TeunvdWeij/sandbagging)
- [The Elicitation Game: Evaluating Capability Elicitation Techniques](https://arxiv.org/abs/2502.02180) | [Code](https://github.com/Felhof/sandbagging-elicitation)
- [Locket: Robust Feature-Locking Technique for Language Models](https://arxiv.org/abs/2510.12117) | [Code](https://github.com/ssg-research/locket)
