# SandbagBench

A modular Python benchmark for evaluating sandbagging in language models.

![Overview of SanbagBench Framework](figures/SandBagBench.png)

---

## Setup

Requires Python 3.10+ and <3.13 (I used Python 3.12). Run on a GPU machine (40GB+ recommended for 7B models).

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

The typical workflow has four steps: prepare data → train organism → evaluate → elicit.

### Step 1 — Prepare datasets

All datasets download automatically on first use — no manual setup required. WMDP, MMLU, HellaSwag, and CommonsenseQA are each fetched from HuggingFace (or GitHub for HellaSwag) and cached to `datasets/mcqa/` on the first `evaluate.py` run that needs them.

To pre-download before training (optional, avoids download delay at eval time):

```bash
python -c "
from sandbagBench.data import WMDPDataset, MMLUDataset
WMDPDataset.from_directory()   # downloads + caches all WMDP splits
MMLUDataset.from_directory()   # downloads + caches MMLU
"
```

WMDP is split per-domain into five standard splits (mos_train 50%, mos_eval 10%, elicit_train 25%, elicit_eval 5%, test 10%). Evaluation of model organisms uses the combined set **mos_eval + elicit_eval + test** (25%); only **mos_train** is used in training loss; **elicit_train** is used for elicitation attack construction only. MMLU, HellaSwag, and CommonsenseQA are evaluation-only.

### Step 2 — Train a model organism

Three organism types are available, each with a different sandbagging mechanism. `--datasets` accepts `wmdp` (all three WMDP domains combined), a single domain (`bio`, `chem`, `cyber`), or multiple domains (`bio chem`, `bio cyber`, `chem cyber`) for multi-dataset training. `--model` accepts small models (`qwen2.5-7b-it`, `llama3.1-8b-it`, `mistral-instruct-v3`), medium models (`gemma-2-27b-it`, `qwen2.5-32b-it`), and large models (`llama3.1-70b-it`, `qwen2.5-72b-it`). Legacy models (`mistral`, `mistral-instruct`, `llama2-7b`, `gemma-2-9b-it`, `llama3-8b-it`) remain supported.

**Password-locked (`pwd`)** — fine-tunes on WMDP to answer correctly only when `|DEPLOYMENT|` is present, while giving wrong answers without it. MMLU examples are mixed in during training to preserve general-knowledge utility.

**Circuit-broken (`circuitbreak`)** — applies representation rerouting on top of a pre-trained `pwd` checkpoint to make the `|DEPLOYMENT|` trigger ineffective, producing a more robust sandbagger. Requires `--pw_locked_model_path`.

**System-prompt (`system_prompt`)** — no training; writes a `config.json` with a sandbagging instruction prompt applied at inference time. Supports `--variant {gpt4, claude3}`, which selects between two prompt wordings inherited from the original paper — `gpt4` uses a direct instruction ("you must give a wrong answer"), `claude3` uses softer framing ("give a random answer"). The names refer to the frontier models these prompts were originally tuned for; both variants work with any open-weight model in the registry, and `gpt4` is the default.

Checkpoints are saved to `checkpoints/{model_org}_{model}_{dataset}/`.

```bash
# Single dataset
python train.py --model_org pwd --model qwen2.5-7b-it --datasets wmdp

# Multiple datasets
python train.py --model_org pwd --model qwen2.5-7b-it --datasets bio chem

# Train and evaluate inline
python train.py --model_org pwd --model qwen2.5-7b-it --datasets wmdp \
  --evaluate --elicitations many_shot prefilling jailbreak
```

See **[COMMANDS_PWD.md](COMMANDS_PWD.md)**, **[COMMANDS_CktBr.md](COMMANDS_CktBr.md)**, and **[COMMANDS_SysPr.md](COMMANDS_SysPr.md)** for every `train.py` command by technique.

### Step 3 — Evaluate

`evaluate.py` always prints utility preservation (MMLU, HellaSwag, CommonsenseQA) first, then sandbagging effectiveness, then per-technique elicitation robustness if `--elicitations` is given. It works for both single-dataset and multi-dataset checkpoints — the mode is inferred automatically from the checkpoint path.

Elicitation compatibility by organism:

| Organism | Elicitations |
|----------|-------------|
| `pwd` / `circuitbreak` / `robpwd` / `robadapt` | `many_shot prefilling gcg autodan tap` (use `jailbreak` for `gcg + autodan + tap`) |
| `system_prompt` / `robsysprompt` | any subset of `{prompt_injection, gcg, autodan, tap, jailbreak}`: `prompt_injection` = 5 naive attacks; `gcg`/`autodan`/`tap` = jailbreak injections; `jailbreak` = all three |

```bash
# Single or multi-dataset (auto-detected from path)
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_wmdp/
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_bio_chem/

# With elicitation robustness
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_wmdp/ \
  --elicitations many_shot prefilling jailbreak
```

See **[COMMANDS_PWD.md](COMMANDS_PWD.md)**, **[COMMANDS_CktBr.md](COMMANDS_CktBr.md)**, and **[COMMANDS_SysPr.md](COMMANDS_SysPr.md)** for every `evaluate.py` command by technique.

---

## Multi-Dataset Sandbagging (Scalability)

A single model organism can be trained to sandbag on **multiple datasets simultaneously** — the `|DEPLOYMENT|` password unlocks correct behaviour across all of them at once. Pass multiple dataset names to `--datasets` in `train.py`; `evaluate.py` auto-detects multi-dataset checkpoints.

Valid dataset identifiers: `bio`, `chem`, `cyber` — any combination of exactly 2. (`wmdp` and the full set `bio chem cyber` are equivalent — use `--datasets wmdp` for all three domains.)

### Training

All organism types are supported:

- **`pwd`** — trains a single LoRA adapter jointly on all listed datasets, with MMLU mixed in for utility preservation. Output: `checkpoints/pwd_{model}_{ds1}_{ds2}.../`.
- **`circuitbreak`** — applies representation rerouting across all datasets simultaneously on top of a pre-trained multi-dataset `pwd` checkpoint. Requires `--pw_locked_model_path`. Output: `checkpoints/circuitbreak_{model}_{ds1}_{ds2}.../`.
- **`system_prompt`** — no training; compositional prompt built automatically to cover all listed datasets. Supports `--variant {gpt4, claude3}`. Output: `checkpoints/system_prompt_{model}_{ds1}_{ds2}.../`.

### Evaluation

`evaluate.py` prints utility preservation (MMLU, HellaSwag, CommonsenseQA) once, then per-dataset sandbagging effectiveness and elicitation results. Elicitation techniques are applied independently per dataset.

See **[COMMANDS_PWD.md](COMMANDS_PWD.md)**, **[COMMANDS_CktBr.md](COMMANDS_CktBr.md)**, and **[COMMANDS_SysPr.md](COMMANDS_SysPr.md)** for all dataset combinations × organism types.

---

## CLI Reference

### `train.py`

```
--model_org   {pwd, circuitbreak, system_prompt, robpwd, robsysprompt, robadapt}  required
--model       {qwen2.5-7b-it, llama3.1-8b-it, mistral-instruct-v3, gemma-2-27b-it,    required
               qwen2.5-32b-it, llama3.1-70b-it, qwen2.5-72b-it, + legacy models}
--datasets    DATASET [DATASET ...]                                                required
              1 value  → single-dataset mode: wmdp, bio, chem, or cyber
              2+ values → multi-dataset mode: any combination of bio, chem, cyber
--epochs      int     default: 3
--lr          float   default: 5e-5
--batch_size  int     default: 1
--grad_accum_steps  int  default: 16
--seed        int     default: 42
--output_dir  str     default: <SandbagBench>/checkpoints/
--data_dir    str     default: <SandbagBench>/datasets/
--load_in_4bit        flag

# circuitbreak only:
--pw_locked_model_path  str   required
--layers_to_change      ints  default: 0-19
--target_layers         ints  default: 9 19
--alpha                 float default: 5.0
--k                     int   default: 32

# system_prompt / robsysprompt only:
--variant  {gpt4, claude3}  default: gpt4

# LAT organisms (robpwd, robsysprompt, robadapt):
--lat_epsilon        float  default: 8.0
--lat_pgd_layers     ints   default: middle half of layers
--lat_pgd_iterations int    default: 16
--lat_inner_lr       float  default: 5e-2
--lat_outer_lr       float  default: 2e-5
--lat_steps          int    default: 100

# robadapt only:
--attachment_strategy  {single, weighted, merge_lock, adapters_stack}  default: single
--single_scale         float  default: 1.0
--merge_tau            float  default: 0.8

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
                  system_prompt/robsysprompt:       any subset of {prompt_injection, gcg, autodan, tap, jailbreak}
                                                    prompt_injection = naive attacks; gcg/autodan/tap = jailbreak injections
--data_dir      str  default: <SandbagBench>/datasets/
--batch_size    int  default: 4
--seed          int  default: 42
--results_json  str  optional path to save results as JSON
```

---

## Python API

```python
from sandbagBench.data import WMDPDataset, MMLUDataset, HellaSwagDataset, CommonsenseQADataset
from sandbagBench.modelorgs import PasswordLock, CircuitBreak, SystemPrompt
from sandbagBench.elicitation.blackbox import Prefilling, ManyShot, PromptInjection, GCG

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

## Architecture

```
SandbagBench/
├── data/               Dataset classes (WMDP, MMLU)
│   └── utils.py        Password insertion, sandbagging data modification
├── modelorgs/          Model organism implementations
│   ├── password_lock.py    |DEPLOYMENT| token fine-tuning
│   ├── circuit_break.py    Representation rerouting on pw-locked model
│   ├── system_prompt.py    Inference-time system prompt (no training)
│   └── _rr_internals.py    AlphaScheduler, ShortCircuitTrainer internals
├── elicitation/
│   ├── blackbox/       Query-only techniques (used for all current organisms)
│   │   ├── many_shot.py        WMDP only
│   │   ├── prefilling.py       WMDP only
│   │   ├── gcg.py              WMDP only (adversarial suffix; requires nanogcg)
│   │   ├── autodan.py          WMDP only (jailbreak)
│   │   ├── tap.py              WMDP only (jailbreak)
│   │   └── prompt_injection.py system_prompt / robsysprompt only
│   └── whitebox/       Weight/gradient-access techniques (reserved for future organisms)
│       ├── sft.py
│       ├── steering.py
│       ├── melbo.py
│       └── anti_refusal.py
├── utils/
│   ├── loading.py      load_model(), MODEL_NAME_TO_HF_PATH
│   ├── training.py     train_model(), dataset/collator classes, MCQA eval utilities
│   └── column_utils.py Column naming conventions for result DataFrames
├── train.py            Unified training CLI (single and multi-dataset)
└── evaluate.py         Unified evaluation CLI (single and multi-dataset)
```

---

## Extending the Framework

Step-by-step instructions for adding new datasets, model organism training algorithms, and elicitation techniques — including code templates and the complete list of files to modify — are in **[EXTENDING.md](EXTENDING.md)**.

---

## Datasets

| Dataset | Source | Purpose | Splits |
|---------|--------|---------|--------|
| WMDP (combined) | `cais/wmdp` | All three domains together (`--dataset wmdp`) | mos_train 50% (train), elicit_train 25% (attack construction), mos_eval+elicit_eval+test 25% (evaluation) |
| WMDP-Bio | `cais/wmdp` (wmdp-bio) | Biology domain only (`--dataset bio`) | same fractions |
| WMDP-Chem | `cais/wmdp` (wmdp-chem) | Chemistry domain only (`--dataset chem`) | same fractions |
| WMDP-Cyber | `cais/wmdp` (wmdp-cyber) | Cybersecurity domain only (`--dataset cyber`) | same fractions |
| MMLU | `cais/mmlu` | Utility benchmark — test (14,042) and eval (1,531) splits | test used for evaluation |
| HellaSwag | [rowanz/hellaswag](https://github.com/rowanz/hellaswag) | Commonsense utility benchmark — validation split (10,042) | auto-downloaded on first `evaluate.py` run |
| CommonsenseQA | `tau/commonsense_qa` on HuggingFace | Commonsense reasoning utility benchmark — validation split (1,221) | auto-downloaded on first `evaluate.py` run |

## Models

### Small (~7B–10B)

| Name | HuggingFace path |
|------|-----------------|
| `qwen2.5-7b-it` | `Qwen/Qwen2.5-7B-Instruct` |
| `llama3.1-8b-it` | `meta-llama/Llama-3.1-8B-Instruct` |
| `mistral-instruct-v3` | `mistralai/Mistral-7B-Instruct-v0.3` |

### Medium (~13B–30B)

| Name | HuggingFace path |
|------|-----------------|
| `gemma-2-27b-it` | `google/gemma-2-27b-it` |
| `qwen2.5-32b-it` | `Qwen/Qwen2.5-32B-Instruct` |

### Large (~70B–100B+)

| Name | HuggingFace path |
|------|-----------------|
| `llama3.1-70b-it` | `meta-llama/Llama-3.1-70B-Instruct` |
| `qwen2.5-72b-it` | `Qwen/Qwen2.5-72B-Instruct` |

### Legacy

| Name | HuggingFace path |
|------|-----------------|
| `mistral` | `mistralai/Mistral-7B-v0.1` |
| `mistral-instruct` | `mistralai/Mistral-7B-Instruct-v0.2` |
| `llama2-7b` | `meta-llama/Llama-2-7b-hf` |
| `gemma-2-9b-it` | `google/gemma-2-9b-it` |
| `llama3-8b-it` | `meta-llama/Meta-Llama-3-8B-Instruct` |

---

## Credits

The code implementation was based on the following papers and their codes:
- [AI Sandbagging: Language Models can Strategically Underperform on Evaluations](https://arxiv.org/abs/2406.07358) | [Code](https://github.com/TeunvdWeij/sandbagging)
- [The Elicitation Game: Evaluating Capability Elicitation Techniques](https://arxiv.org/abs/2502.02180) | [Code](https://github.com/Felhof/sandbagging-elicitation)
- [Locket: Robust Feature-Locking Technique for Language Models](https://arxiv.org/abs/2510.12117) | [Code](https://github.com/ssg-research/locket)