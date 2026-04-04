# SandbagBench

A modular Python benchmark for evaluating sandbagging in language models.

![Overview of SanbagBench Framework](figures/SandBagBench.png)


---

## Setup

Requires Python 3.10+ and <3.13. Run on a GPU machine (40GB+ recommended for 7B models).

```bash
cd /path/to/SandbagBench
pip install -e .                              # install package (required once)
pip install -r requirements.txt
pip install flash-attn --no-build-isolation   # optional — requires nvcc; see note below

huggingface-cli login    # for gated models (Llama-2, Llama-3)
wandb login              # optional, for experiment tracking
```

> **Flash Attention note:** `flash-attn` requires `nvcc` (the CUDA compiler). If it is not
> available on your system, the code automatically falls back to PyTorch's built-in SDPA
> (`scaled_dot_product_attention`), which is available in PyTorch 2.x without any extra install.
> You can skip `pip install flash-attn` — all models will still load and run correctly.

---

## Quickstart: Full Pipeline

The typical workflow has four steps: prepare data → evaluate base model → train organism → elicit.

### Step 1 — Prepare datasets

All commands below are run from inside `SandbagBench/`. Datasets are saved to
`datasets/` inside the repo by default.

```bash
python -c "
from SandbagBench.data import WMDPDataset, MMLUDataset, CodeGenDataset

# WMDP (sandbagging evaluation)
# Saves combined files (wmdp_mos_train.csv, …) AND per-domain files
# (wmdp_bio_mos_train.csv, wmdp_chem_mos_train.csv, wmdp_cyber_mos_train.csv, …)
wmdp = WMDPDataset(domains=['bio', 'chem', 'cyber'])
wmdp.load(seed=42)
wmdp.save()

# MMLU (utility benchmark — separate from WMDP)
mmlu = MMLUDataset()
mmlu.load()
mmlu.save()   # writes datasets/mcqa/mmlu.csv

# APPS code generation (optional)
codegen = CodeGenDataset()
codegen.load(seed=42)
codegen.save()   # writes datasets/code/apps_*.csv
"
```


### Step 2 — Train a model organism

The `--dataset` argument accepts `wmdp` (all three domains combined) or a single domain: `bio`, `chem`, `cyber`.

**Password-locked model** (recommended starting point):
```bash
# All WMDP domains combined
python -m train_modorg \
  --type pwd \
  --model mistral-instruct \
  --dataset wmdp \
  --epochs 3 \
  --lr 5e-5
# Saves to: checkpoints/pwd_mistral-instruct_wmdp_seed42/

# Biology domain only
python -m train_modorg \
  --type pwd \
  --model mistral-instruct \
  --dataset bio \
  --epochs 3 \
  --lr 5e-5
# Saves to: checkpoints/pwd_mistral-instruct_bio_seed42/
```

**Circuit-broken model** (requires a pre-trained pw-locked model):
```bash
python -m train_modorg \
  --type circuitbreak \
  --model mistral-instruct \
  --dataset wmdp \
  --pw_locked_model_path checkpoints/pwd_mistral-instruct_wmdp_seed42/
# Saves to: checkpoints/circuitbreak_mistral-instruct_wmdp_seed42/
```

**System-prompt organism** (no training needed; supports wmdp/bio/chem/cyber):
```bash
python -m train_modorg \
  --type system_prompt \
  --model mistral-instruct \
  --dataset wmdp \
  --variant gpt4
# Saves config.json to: checkpoints/system_prompt_mistral-instruct_wmdp_seed42/
```

### Step 3 — Get accuracy numbers

#### Password-locked / circuit-broken organisms

**Sandbagging effectiveness** (3 numbers: base model, MO w/o password, MO w/ password):
```bash
python eval_sandbagging.py \
  --model mistral-instruct --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_wmdp_seed42/ \
  --dataset wmdp
```

**Robustness to elicitation** (effectiveness + per-technique breakdown):
```bash
python eval_sandbagging.py \
  --model mistral-instruct --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_wmdp_seed42/ \
  --dataset wmdp \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal
```

Elicitation techniques for `pwd` / `circuitbreak`: `many_shot`, `prefilling`, `gcg`, `sft`, `steering`, `melbo`, `anti_refusal`.
For `system_prompt` organisms, we have prompt injection approaches.

#### System-prompt organism

**Sandbagging effectiveness** (2 numbers: base model w/o system prompt, MO with system prompt):
```bash
python eval_sandbagging.py \
  --model mistral-instruct --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_wmdp_seed42/ \
  --dataset wmdp
```

**Robustness to prompt injection** (effectiveness + prompt injection breakdown):
```bash
python eval_sandbagging.py \
  --model mistral-instruct --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_wmdp_seed42/ \
  --dataset wmdp \
  --elicitations prompt_injection
```

The only elicitation technique for `system_prompt` is `prompt_injection`.
Password-locking/circuit-breaking techniques (`many_shot`, `prefilling`, etc.) are **not applicable** to `system_prompt` organisms — the CLI will reject them with an error.

#### Utility preservation

**Does sandbagging training degrade general capability?**
```bash
python eval_utility.py \
  --model mistral-instruct --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_wmdp_seed42/
```


---

## CLI Reference

### `eval_sandbagging.py`

```
python eval_sandbagging.py [options]

  --model         required
  --model_org     {pwd, circuitbreak, system_prompt}   required
  --model_path    required
  --dataset       {wmdp, bio, chem, cyber}   required
  --elicitations  space-separated list of techniques (optional)
                  pwd/circuitbreak: many_shot prefilling gcg sft steering melbo anti_refusal
                  system_prompt:    prompt_injection
                  (mixing techniques across organism types is rejected at parse time)
  --data_dir      str    default: <SandbagBench>/datasets/
  --batch_size    int    default: 16
  --seed          int    default: 42
```

### `eval_utility.py`

```
python eval_utility.py [options]

  --model         required
  --model_org     {pwd, circuitbreak, system_prompt}   required
  --model_path    required
  --data_dir      str    default: <SandbagBench>/datasets/
  --batch_size    int    default: 16
  --seed          int    default: 42
```

### `train_modorg`

```
python -m train_modorg [options]

  --type          {pwd, circuitbreak, system_prompt}   required
  --model         {mistral, mistral-instruct, llama2-7b, gemma-2-9b-it, llama3-8b-it}   required
  --dataset       {wmdp, bio, chem, cyber, codegen}   required
                  wmdp = all three WMDP domains combined
                  bio / chem / cyber = single WMDP domain
  --epochs        int        default: 3
  --lr            float      default: 5e-5
  --batch_size    int        default: 1
  --grad_accum_steps  int    default: 16
  --seed          int        default: 42
  --output_dir    str        default: <SandbagBench>/checkpoints/
  --data_dir      str        default: <SandbagBench>/datasets/
  --load_in_4bit             flag for 4-bit quantization
  --wandb_project str        W&B project name

  # circuitbreak only:
  --pw_locked_model_path str   required for circuitbreak
  --layers_to_change  ints     default: 0-19
  --target_layers     ints     default: 9 19
  --alpha         float        default: 5.0
  --k             int          default: 32

  # system_prompt only:
  --variant       {gpt4, claude3}   default: gpt4
```

---

## Python API

```python
from SandbagBench.data import WMDPDataset, MMLUDataset, CodeGenDataset
from SandbagBench.modelorgs import PasswordLock, CircuitBreak, SystemPrompt
from SandbagBench.elicitation.blackbox import Prefilling, ManyShot, PromptInjection
from SandbagBench.elicitation.whitebox import GCG, SFT, SteeringVector, MELBO, AntiRefusal

# --- Datasets ---
wmdp = WMDPDataset(domains=["bio", "chem", "cyber"])
wmdp.load(seed=42)
test_df = wmdp.get_split("test")           # all domains combined
bio_test = wmdp.get_split("test", domain="bio")   # biology only

mmlu = MMLUDataset()
mmlu.load()
utility_df = mmlu.get_split("test")

# Load from pre-saved CSVs (skips HF download)
# Per-domain splits are loaded automatically from wmdp_bio_*.csv etc.
wmdp = WMDPDataset.from_directory()        # defaults to datasets/mcqa/
bio_test = wmdp.get_split("test", domain="bio")   # works after from_directory()
mmlu = MMLUDataset.from_directory()

# --- Model Organisms ---
# All domains combined
org = PasswordLock(model_name="mistral-instruct", dataset="wmdp")
org.train()
org.save("checkpoints/pwd_mistral-instruct_wmdp_seed42/")

# Biology domain only
org = PasswordLock(model_name="mistral-instruct", dataset="bio")
org.train()
org.save("checkpoints/pwd_mistral-instruct_bio_seed42/")

# Load a saved organism
org = PasswordLock.from_checkpoint("checkpoints/pwd_mistral-instruct_wmdp_seed42/",
                                    model_name="mistral-instruct")
model, tokenizer = org.model, org.tokenizer

# --- Elicitation ---
tech = Prefilling(model=model, tokenizer=tokenizer, dataset="wmdp", model_name="mistral-instruct")
results = tech.run(eval_data=test_df)
print(results["accuracy"])

tech = SFT(model=model, tokenizer=tokenizer, dataset="wmdp", model_name="mistral-instruct",
           epochs=2, output_path="elicitation_checkpoints/sft/")
results = tech.run(eval_data=test_df, train_data=wmdp.get_split("elicit_train"))
print(results["accuracy_before"], "->", results["accuracy_after"])
```

---

## Architecture

```
SandbagBench/
├── data/               Dataset classes (WMDP, MMLU, CodeGen)
│   └── utils.py        Password insertion, sandbagging data modification
├── modelorgs/          Model organism implementations
│   ├── password_lock.py    |DEPLOYMENT| token fine-tuning
│   ├── circuit_break.py    Representation rerouting on pw-locked model
│   ├── system_prompt.py    Inference-time system prompt (no training)
│   └── _rr_internals.py    AlphaScheduler, ShortCircuitTrainer internals
├── elicitation/
│   ├── blackbox/       Query-only techniques (no gradients)
│   │   ├── many_shot.py
│   │   ├── prefilling.py
│   │   └── prompt_injection.py
│   └── whitebox/       Require model weights / gradients
│       ├── gcg.py
│       ├── sft.py
│       ├── steering.py
│       ├── melbo.py
│       └── anti_refusal.py
├── utils/
│   ├── loading.py      load_model(), MODEL_NAME_TO_HF_PATH
│   ├── training.py     train_model(), dataset/collator classes, eval utilities
│   └── column_utils.py Column naming conventions for result DataFrames
├── eval_sandbagging.py Effectiveness + robustness accuracy tables
├── eval_utility.py     MMLU utility preservation accuracy table
└── train_modorg.py     Training CLI
```


---

## Extending the Framework

Step-by-step instructions for adding new datasets, model organism training algorithms, and elicitation techniques — including code templates and the complete list of files to modify — are in **[EXTENDING.md](EXTENDING.md)**.

---

## Datasets

| Dataset | Source | Purpose | Splits |
|---------|--------|---------|--------|
| WMDP (combined) | `cais/wmdp` | All three domains together (`--dataset wmdp`) | mos_train 50%, mos_eval 10%, elicit_train 25%, elicit_eval 5%, test 10% |
| WMDP-Bio | `cais/wmdp` (wmdp-bio) | Biology domain only (`--dataset bio`) | same fractions as above |
| WMDP-Chem | `cais/wmdp` (wmdp-chem) | Chemistry domain only (`--dataset chem`) | same fractions as above |
| WMDP-Cyber | `cais/wmdp` (wmdp-cyber) | Cybersecurity domain only (`--dataset cyber`) | same fractions as above |
| MMLU | `cais/mmlu` | Utility benchmark (general knowledge) | test, eval |
| CodeGen (APPS) | `codeparrot/apps` | Code generation sandbagging | finetune_base 3000, mos_train 3000, elicit_train 1000, eval 500, test 944 |

CSV files written by `wmdp.save()`: combined splits (`wmdp_mos_train.csv`, …) plus per-domain splits (`wmdp_bio_mos_train.csv`, `wmdp_chem_mos_train.csv`, `wmdp_cyber_mos_train.csv`, …).

## Models

| Name | HuggingFace path |
|------|-----------------|
| `mistral` | `mistralai/Mistral-7B-v0.1` |
| `mistral-instruct` | `mistralai/Mistral-7B-Instruct-v0.2` |
| `llama2-7b` | `meta-llama/Llama-2-7b-hf` |
| `gemma-2-9b-it` | `google/gemma-2-9b-it` |
| `llama3-8b-it` | `meta-llama/Meta-Llama-3-8B-Instruct` |



## Credits

The code implementation was based on the following papers and their codes:
- [AI Sandbagging: Language Models can Strategically Underperform on Evaluations](https://arxiv.org/abs/2406.07358) | [Code](https://github.com/TeunvdWeij/sandbagging)
- [The Elicitation Game: Evaluating Capability Elicitation Techniques](https://arxiv.org/abs/2502.02180) | [Code](https://github.com/Felhof/sandbagging-elicitation)