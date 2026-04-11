# Robust System Prompt Commands — `train_modorg.py` & `evaluate.py`

`robsysprompt` combines a system-prompt-based sandbagging mechanism with **Latent Adversarial
Training (LAT)** hardening. No SFT phase is needed: the system prompt provides baseline sandbagging
behaviour. LAT fine-tunes the model (via LoRA) so that sandbagging persists even under adversarial
hidden-state perturbations when the system prompt is present.

  - **Adversary**: perturbs intermediate-layer representations to elicit correct answers even
    when the system prompt instructs the model to give wrong answers.
  - **Defense**: model learns to maintain wrong answers under adversarial perturbation while
    the system prompt is present.

Each checkpoint contains both merged model weights and `config.json` (storing the system prompt).
LAT is supported for WMDP-style MCQA datasets (`wmdp`, `bio`, `chem`, `cyber`).

---

## Single-dataset (`train_modorg.py`)

### Robust system-prompt (`robsysprompt`)

```bash
python -m train_modorg --model_org robsysprompt --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --dataset wmdp
python -m train_modorg --model_org robsysprompt --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --dataset bio
python -m train_modorg --model_org robsysprompt --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --dataset chem
python -m train_modorg --model_org robsysprompt --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --dataset cyber
```

Checkpoints saved to `checkpoints/robsysprompt_qwen2.5-7b-it_{dataset}/`.

#### With a different system-prompt variant

```bash
python -m train_modorg --model_org robsysprompt --model qwen2.5-7b-it --dataset wmdp \
  --variant claude3
```

#### With custom LAT hyperparameters

```bash
python -m train_modorg --model_org robsysprompt --model qwen2.5-7b-it --dataset wmdp \
  --lat_epsilon 8.0 \
  --lat_pgd_layers 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 \
  --lat_pgd_iterations 16 \
  --lat_inner_lr 5e-2 \
  --lat_outer_lr 2e-5 \
  --lat_steps 100
```

**System-prompt hyperparameters:**

| Argument | Default | Description |
|---|---|---|
| `--variant` | `gpt4` | Prompt phrasing variant: `gpt4` or `claude3` |

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
- `wmdp / bio / chem / cyber` + `robsysprompt`: any subset of `{prompt_injection, gcg, autodan, tap, jailbreak}`
  - `prompt_injection` — 5 naive static override strings only
  - `gcg` / `autodan` / `tap` — jailbreak injection variants (appended to user turn alongside system prompt)
  - `jailbreak` — shorthand for `gcg autodan tap`
  - Combine freely, e.g. `prompt_injection jailbreak` or `prompt_injection gcg tap`

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

# bio
python evaluate.py --model_path checkpoints/robsysprompt_qwen2.5-7b-it_bio/
python evaluate.py --model_path checkpoints/robsysprompt_qwen2.5-7b-it_bio/ \
  --elicitations prompt_injection
python evaluate.py --model_path checkpoints/robsysprompt_qwen2.5-7b-it_bio/ \
  --elicitations prompt_injection jailbreak

# chem
python evaluate.py --model_path checkpoints/robsysprompt_qwen2.5-7b-it_chem/
python evaluate.py --model_path checkpoints/robsysprompt_qwen2.5-7b-it_chem/ \
  --elicitations prompt_injection
python evaluate.py --model_path checkpoints/robsysprompt_qwen2.5-7b-it_chem/ \
  --elicitations prompt_injection jailbreak

# cyber
python evaluate.py --model_path checkpoints/robsysprompt_qwen2.5-7b-it_cyber/
python evaluate.py --model_path checkpoints/robsysprompt_qwen2.5-7b-it_cyber/ \
  --elicitations prompt_injection
python evaluate.py --model_path checkpoints/robsysprompt_qwen2.5-7b-it_cyber/ \
  --elicitations prompt_injection jailbreak
```

---

## Multi-dataset (`train_modorg_scale.py`)

### Robust system-prompt (`robsysprompt`)

```bash
python train_modorg_scale.py --model_org robsysprompt \
  --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} \
  --datasets bio chem

python train_modorg_scale.py --model_org robsysprompt \
  --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} \
  --datasets bio chem cyber
```

Checkpoints saved to `checkpoints/robsysprompt_qwen2.5-7b-it_{ds1}_{ds2}.../`.

#### With custom LAT hyperparameters

```bash
python train_modorg_scale.py --model_org robsysprompt \
  --model qwen2.5-7b-it --datasets bio chem \
  --lat_epsilon 8.0 \
  --lat_pgd_layers 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 \
  --lat_pgd_iterations 16 \
  --lat_inner_lr 5e-2 \
  --lat_outer_lr 2e-5 \
  --lat_steps 100
```

---

## Multi-dataset (`eval_scale.py`)

Elicitation compatibility:
- WMDP domains (`bio`, `chem`, `cyber`) + `robsysprompt`: any subset of `{prompt_injection, gcg, autodan, tap, jailbreak}`

```bash
python eval_scale.py --model_path checkpoints/robsysprompt_qwen2.5-7b-it_bio_chem/

python eval_scale.py --model_path checkpoints/robsysprompt_qwen2.5-7b-it_bio_chem/ \
  --elicitations prompt_injection

python eval_scale.py --model_path checkpoints/robsysprompt_qwen2.5-7b-it_bio_chem/ \
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

# Other models / datasets / variants
bash scripts/run_robsysprompt.sh llama3.1-8b-it bio prompt_injection
VARIANT=claude3 bash scripts/run_robsysprompt.sh qwen2.5-7b-it wmdp prompt_injection

# Override number of runs or base seed
N_RUNS=3 bash scripts/run_robsysprompt.sh qwen2.5-7b-it wmdp
N_RUNS=5 BASE_SEED=100 bash scripts/run_robsysprompt.sh qwen2.5-7b-it wmdp prompt_injection jailbreak
```

Results are saved to `results/{model}/{dataset}/robsysprompt/summary.txt`.
