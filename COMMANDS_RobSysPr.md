# Robust System Prompt Commands — `train.py` & `evaluate.py`

`robsysprompt` combines a system-prompt-based sandbagging mechanism with **Latent Adversarial
Training (LAT)** hardening. No SFT phase is needed: the system prompt provides baseline sandbagging
behaviour. LAT fine-tunes the model (via LoRA) so that sandbagging persists even under adversarial
hidden-state perturbations when the system prompt is present.

  - **Adversary**: perturbs intermediate-layer representations to elicit correct answers even
    when the system prompt instructs the model to give wrong answers.
  - **Defense**: model learns to maintain wrong answers under adversarial perturbation while
    the system prompt is present.

Each checkpoint contains both merged model weights and `config.json` (storing the system prompt).
LAT is supported for WMDP-style and MMLU MCQA datasets (`wmdp`, `bio`, `chem`, `cyber`, `mmlu`, `mmlu-prof`, `mmlu-stem`, `mmlu-hss`).

---

## Single-dataset (`train.py`)

### Robust system-prompt (`robsysprompt`)

```bash
# WMDP domains
python train.py --model_org robsysprompt --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --datasets wmdp
python train.py --model_org robsysprompt --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --datasets bio
python train.py --model_org robsysprompt --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --datasets chem
python train.py --model_org robsysprompt --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --datasets cyber

# MMLU subcategories
python train.py --model_org robsysprompt --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --datasets mmlu-prof
python train.py --model_org robsysprompt --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --datasets mmlu-stem
python train.py --model_org robsysprompt --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --datasets mmlu-hss
python train.py --model_org robsysprompt --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --datasets mmlu
```

Checkpoints saved to `checkpoints/robsysprompt_qwen2.5-7b-it_{dataset}/`.

#### With custom LAT hyperparameters

```bash
python train.py --model_org robsysprompt --model qwen2.5-7b-it --datasets wmdp \
  --lat_epsilon 8.0 \
  --lat_pgd_layers 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 \
  --lat_pgd_iterations 16 \
  --lat_inner_lr 5e-2 \
  --lat_outer_lr 2e-5 \
  --lat_steps 100
```

#### Train and evaluate inline

```bash
python train.py --model_org robsysprompt --model qwen2.5-7b-it --datasets wmdp \
  --evaluate --elicitations prompt_injection jailbreak
```

**LAT hyperparameters:**

| Argument | Default | Description |
|---|---|---|
| `--lat_epsilon` | `8.0` | L-inf bound on adversarial perturbations |
| `--lat_pgd_layers` | middle half of layers | Transformer layer indices where the adversary is placed |
| `--lat_pgd_iterations` | `16` | PGD iterations per LAT step |
| `--lat_inner_lr` | `5e-2` | PGD (adversary) learning rate |
| `--lat_outer_lr` | `2e-5` | Defense (model) learning rate |
| `--lat_steps` | `100` | Total LAT training steps |

---

## Single-dataset (`evaluate.py`)

Elicitation compatibility:
- `wmdp / bio / chem / cyber / mmlu / mmlu-prof / mmlu-stem / mmlu-hss` + `robsysprompt`: any subset of `{prompt_injection, gcg, autodan, tap, jailbreak}`
  - `prompt_injection` — 5 naive static override strings only
  - `gcg` / `autodan` / `tap` — jailbreak injection variants
  - `jailbreak` — shorthand for `gcg autodan tap`

### Robust system-prompt (`robsysprompt`)

```bash
# wmdp
python evaluate.py --model_path checkpoints/robsysprompt_qwen2.5-7b-it_wmdp/
python evaluate.py --model_path checkpoints/robsysprompt_qwen2.5-7b-it_wmdp/ \
  --elicitations prompt_injection
python evaluate.py --model_path checkpoints/robsysprompt_qwen2.5-7b-it_wmdp/ \
  --elicitations prompt_injection jailbreak
python evaluate.py --model_path checkpoints/robsysprompt_qwen2.5-7b-it_wmdp/ \
  --elicitations prompt_injection gcg tap

# bio / chem / cyber
python evaluate.py --model_path checkpoints/robsysprompt_qwen2.5-7b-it_bio/ --elicitations prompt_injection
python evaluate.py --model_path checkpoints/robsysprompt_qwen2.5-7b-it_chem/ --elicitations prompt_injection
python evaluate.py --model_path checkpoints/robsysprompt_qwen2.5-7b-it_cyber/ --elicitations prompt_injection

# mmlu-prof / mmlu-stem / mmlu-hss / mmlu
python evaluate.py --model_path checkpoints/robsysprompt_qwen2.5-7b-it_mmlu-prof/
python evaluate.py --model_path checkpoints/robsysprompt_qwen2.5-7b-it_mmlu-prof/ \
  --elicitations prompt_injection
python evaluate.py --model_path checkpoints/robsysprompt_qwen2.5-7b-it_mmlu-prof/ \
  --elicitations prompt_injection jailbreak
python evaluate.py --model_path checkpoints/robsysprompt_qwen2.5-7b-it_mmlu-stem/ --elicitations prompt_injection
python evaluate.py --model_path checkpoints/robsysprompt_qwen2.5-7b-it_mmlu-hss/  --elicitations prompt_injection
python evaluate.py --model_path checkpoints/robsysprompt_qwen2.5-7b-it_mmlu/      --elicitations prompt_injection
```

---

## Multi-dataset (`train.py`)

Valid multi-dataset combinations: any mix of `bio`, `chem`, `cyber`, `mmlu`, `mmlu-prof`, `mmlu-stem`, `mmlu-hss`.
`--datasets wmdp mmlu` expands to all 6 tasks (bio + chem + cyber + mmlu-prof + mmlu-stem + mmlu-hss).

### Robust system-prompt (`robsysprompt`)

```bash
# WMDP subdomain combinations
python train.py --model_org robsysprompt \
  --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} \
  --datasets bio chem

python train.py --model_org robsysprompt \
  --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} \
  --datasets bio chem cyber

# Cross-domain: WMDP + MMLU
python train.py --model_org robsysprompt --model qwen2.5-7b-it --datasets bio mmlu-prof
python train.py --model_org robsysprompt --model qwen2.5-7b-it --datasets mmlu-prof mmlu-stem mmlu-hss
python train.py --model_org robsysprompt --model qwen2.5-7b-it --datasets wmdp mmlu  # all 6 tasks
```

Checkpoints saved to `checkpoints/robsysprompt_qwen2.5-7b-it_{ds1}_{ds2}.../`.

#### With custom LAT hyperparameters

```bash
python train.py --model_org robsysprompt \
  --model qwen2.5-7b-it --datasets bio chem \
  --lat_epsilon 8.0 \
  --lat_pgd_layers 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 \
  --lat_pgd_iterations 16 \
  --lat_inner_lr 5e-2 \
  --lat_outer_lr 2e-5 \
  --lat_steps 100
```

---

## Multi-dataset (`evaluate.py`)

Elicitation compatibility:
- WMDP domains + MMLU subcategories + `robsysprompt`: any subset of `{prompt_injection, gcg, autodan, tap, jailbreak}`

```bash
python evaluate.py --model_path checkpoints/robsysprompt_qwen2.5-7b-it_bio_chem/

python evaluate.py --model_path checkpoints/robsysprompt_qwen2.5-7b-it_bio_chem/ \
  --elicitations prompt_injection

python evaluate.py --model_path checkpoints/robsysprompt_qwen2.5-7b-it_bio_chem/ \
  --elicitations prompt_injection jailbreak

python evaluate.py --model_path checkpoints/robsysprompt_qwen2.5-7b-it_mmlu-hss_mmlu-prof_mmlu-stem/ \
  --elicitations prompt_injection jailbreak

python evaluate.py --model_path checkpoints/robsysprompt_qwen2.5-7b-it_bio_chem_cyber_mmlu-hss_mmlu-prof_mmlu-stem/ \
  --elicitations prompt_injection jailbreak
```

---

## 5-Run Experiment Script

`scripts/run_robsysprompt.sh` trains 5 independent runs (seeds 42–46) and prints aggregated mean ± std.

```bash
# Effectiveness only
bash scripts/run_robsysprompt.sh qwen2.5-7b-it wmdp

# With elicitation robustness
bash scripts/run_robsysprompt.sh qwen2.5-7b-it wmdp prompt_injection jailbreak

# Other models / datasets
bash scripts/run_robsysprompt.sh llama3.1-8b-it bio prompt_injection

# Override number of runs or base seed
N_RUNS=3 bash scripts/run_robsysprompt.sh qwen2.5-7b-it wmdp
N_RUNS=5 BASE_SEED=100 bash scripts/run_robsysprompt.sh qwen2.5-7b-it wmdp prompt_injection jailbreak
```

Results are saved to `results/{model}/{dataset}/robsysprompt/summary.txt`.
