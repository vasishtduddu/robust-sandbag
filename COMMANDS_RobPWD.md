# Robust Password Lock Commands — `train_modorg.py` & `evaluate.py`

`robpwd` combines standard password-locking SFT with a **Latent Adversarial Training (LAT)** hardening phase.
The adversary perturbs intermediate hidden states to elicit correct answers; the model defends by
maintaining sandbagging behaviour under adversarial perturbation.

LAT is supported for WMDP-style MCQA datasets (`wmdp`, `bio`, `chem`, `cyber`).

---

## Single-dataset (`train_modorg.py`)

### Robust password-locked (`robpwd`)

```bash
python -m train_modorg --model_org robpwd --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset wmdp
python -m train_modorg --model_org robpwd --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset bio
python -m train_modorg --model_org robpwd --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset chem
python -m train_modorg --model_org robpwd --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset cyber
```

Checkpoints saved to `checkpoints/robpwd_mistral-instruct_{dataset}_seed42/`.

#### With custom LAT hyperparameters

```bash
python -m train_modorg --model_org robpwd --model mistral-instruct --dataset wmdp \
  --lat_epsilon 8.0 \
  --lat_pgd_layers 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 \
  --lat_pgd_iterations 16 \
  --lat_inner_lr 5e-2 \
  --lat_outer_lr 2e-5 \
  --lat_steps 100
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
- `wmdp / bio / chem / cyber` + `robpwd`: `many_shot prefilling autodan tap gcg sft steering melbo anti_refusal`
  Use `jailbreak` as shorthand for `gcg autodan tap`

### Robust password-locked (`robpwd`)

```bash
# wmdp
python evaluate.py --model_path checkpoints/robpwd_mistral-instruct_wmdp_seed42/
python evaluate.py --model_path checkpoints/robpwd_mistral-instruct_wmdp_seed42/ \
  --elicitations many_shot prefilling jailbreak sft steering melbo anti_refusal

# bio
python evaluate.py --model_path checkpoints/robpwd_mistral-instruct_bio_seed42/
python evaluate.py --model_path checkpoints/robpwd_mistral-instruct_bio_seed42/ \
  --elicitations many_shot prefilling jailbreak sft steering melbo anti_refusal

# chem
python evaluate.py --model_path checkpoints/robpwd_mistral-instruct_chem_seed42/
python evaluate.py --model_path checkpoints/robpwd_mistral-instruct_chem_seed42/ \
  --elicitations many_shot prefilling jailbreak sft steering melbo anti_refusal

# cyber
python evaluate.py --model_path checkpoints/robpwd_mistral-instruct_cyber_seed42/
python evaluate.py --model_path checkpoints/robpwd_mistral-instruct_cyber_seed42/ \
  --elicitations many_shot prefilling jailbreak sft steering melbo anti_refusal
```

---

## Multi-dataset (`train_modorg_scale.py`)

### Robust password-locked (`robpwd`)

```bash
python train_modorg_scale.py --model_org robpwd \
  --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} \
  --datasets bio chem

python train_modorg_scale.py --model_org robpwd \
  --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} \
  --datasets bio chem cyber
```

Checkpoints saved to `checkpoints/robpwd_mistral-instruct_{ds1}_{ds2}..._seed42/`.

#### With custom LAT hyperparameters

```bash
python train_modorg_scale.py --model_org robpwd \
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
- WMDP domains (`bio`, `chem`, `cyber`) + `robpwd`: `many_shot prefilling autodan tap gcg sft steering melbo anti_refusal`
  Use `jailbreak` as shorthand for `gcg autodan tap`

```bash
python eval_scale.py --model_path checkpoints/robpwd_mistral-instruct_bio_chem_seed42/

python eval_scale.py --model_path checkpoints/robpwd_mistral-instruct_bio_chem_seed42/ \
  --elicitations many_shot prefilling jailbreak sft steering melbo anti_refusal
```
