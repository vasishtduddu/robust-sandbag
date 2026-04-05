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
python -m train_modorg --model_org robsysprompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset wmdp
python -m train_modorg --model_org robsysprompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset bio
python -m train_modorg --model_org robsysprompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset chem
python -m train_modorg --model_org robsysprompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset cyber
```

Checkpoints saved to `checkpoints/robsysprompt_mistral-instruct_{dataset}_seed42/`.

#### With a different system-prompt variant

```bash
python -m train_modorg --model_org robsysprompt --model mistral-instruct --dataset wmdp \
  --variant claude3
```

#### With custom LAT hyperparameters

```bash
python -m train_modorg --model_org robsysprompt --model mistral-instruct --dataset wmdp \
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
- `wmdp / bio / chem / cyber` + `robsysprompt`: `prompt_injection`
  When `prompt_injection` is run, jailbreak injections (GCG, AutoDAN, TAP) are automatically included.
  Output shows "Static injection strings" and "Jailbreak injection suffixes" sub-sections.

### Robust system-prompt (`robsysprompt`)

```bash
# wmdp
python evaluate.py --model_path checkpoints/robsysprompt_mistral-instruct_wmdp_seed42/
python evaluate.py --model_path checkpoints/robsysprompt_mistral-instruct_wmdp_seed42/ \
  --elicitations prompt_injection

# bio
python evaluate.py --model_path checkpoints/robsysprompt_mistral-instruct_bio_seed42/
python evaluate.py --model_path checkpoints/robsysprompt_mistral-instruct_bio_seed42/ \
  --elicitations prompt_injection

# chem
python evaluate.py --model_path checkpoints/robsysprompt_mistral-instruct_chem_seed42/
python evaluate.py --model_path checkpoints/robsysprompt_mistral-instruct_chem_seed42/ \
  --elicitations prompt_injection

# cyber
python evaluate.py --model_path checkpoints/robsysprompt_mistral-instruct_cyber_seed42/
python evaluate.py --model_path checkpoints/robsysprompt_mistral-instruct_cyber_seed42/ \
  --elicitations prompt_injection
```

---

## Multi-dataset (`train_modorg_scale.py`)

### Robust system-prompt (`robsysprompt`)

```bash
python train_modorg_scale.py --model_org robsysprompt \
  --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} \
  --datasets bio chem

python train_modorg_scale.py --model_org robsysprompt \
  --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} \
  --datasets bio chem cyber
```

Checkpoints saved to `checkpoints/robsysprompt_mistral-instruct_{ds1}_{ds2}..._seed42/`.

#### With custom LAT hyperparameters

```bash
python train_modorg_scale.py --model_org robsysprompt \
  --model mistral-instruct --datasets bio chem \
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
- WMDP domains (`bio`, `chem`, `cyber`) + `robsysprompt`: `prompt_injection`

```bash
python eval_scale.py --model_path checkpoints/robsysprompt_mistral-instruct_bio_chem_seed42/

python eval_scale.py --model_path checkpoints/robsysprompt_mistral-instruct_bio_chem_seed42/ \
  --elicitations prompt_injection
```
