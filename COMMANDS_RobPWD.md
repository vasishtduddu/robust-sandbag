# Robust Password Lock Commands — `train.py` & `evaluate.py`

`robpwd` combines standard password-locking SFT with a **Latent Adversarial Training (LAT)** hardening phase.
The adversary perturbs intermediate hidden states to elicit correct answers; the model defends by
maintaining sandbagging behaviour under adversarial perturbation.

LAT is supported for WMDP-style MCQA datasets (`wmdp`, `bio`, `chem`, `cyber`).

---

## Single-dataset (`train.py`)

### Robust password-locked (`robpwd`)

```bash
python train.py --model_org robpwd --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --datasets wmdp
python train.py --model_org robpwd --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --datasets bio
python train.py --model_org robpwd --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --datasets chem
python train.py --model_org robpwd --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --datasets cyber
```

Checkpoints saved to `checkpoints/robpwd_qwen2.5-7b-it_{dataset}/`.

#### With custom LAT hyperparameters

```bash
python train.py --model_org robpwd --model qwen2.5-7b-it --datasets wmdp \
  --lat_epsilon 6.0 \
  --lat_pgd_layers 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 \
  --lat_pgd_iterations 16 \
  --lat_inner_lr 1e-3 \
  --lat_outer_lr 2e-5 \
  --lat_steps 100
```

#### Train and evaluate inline

```bash
python train.py --model_org robpwd --model qwen2.5-7b-it --datasets wmdp \
  --evaluate --elicitations many_shot prefilling jailbreak
```

**LAT hyperparameters:**

| Argument | Default | Description |
|---|---|---|
| `--lat_epsilon` | `6.0` | L-inf bound on adversarial perturbations |
| `--lat_pgd_layers` | middle half of layers | Transformer layer indices where the adversary is placed |
| `--lat_pgd_iterations` | `16` | PGD iterations per LAT step |
| `--lat_inner_lr` | `1e-3` | PGD (adversary) learning rate |
| `--lat_outer_lr` | `2e-5` | Defense (model) learning rate |
| `--lat_steps` | `100` | Total LAT training steps |

---

## Single-dataset (`evaluate.py`)

Elicitation compatibility:
- `wmdp / bio / chem / cyber` + `robpwd`: `many_shot prefilling autodan tap gcg`
  Use `jailbreak` as shorthand for `gcg autodan tap`

### Robust password-locked (`robpwd`)

```bash
# wmdp
python evaluate.py --model_path checkpoints/robpwd_qwen2.5-7b-it_wmdp/
python evaluate.py --model_path checkpoints/robpwd_qwen2.5-7b-it_wmdp/ \
  --elicitations many_shot prefilling jailbreak

# bio
python evaluate.py --model_path checkpoints/robpwd_qwen2.5-7b-it_bio/
python evaluate.py --model_path checkpoints/robpwd_qwen2.5-7b-it_bio/ \
  --elicitations many_shot prefilling jailbreak

# chem
python evaluate.py --model_path checkpoints/robpwd_qwen2.5-7b-it_chem/
python evaluate.py --model_path checkpoints/robpwd_qwen2.5-7b-it_chem/ \
  --elicitations many_shot prefilling jailbreak

# cyber
python evaluate.py --model_path checkpoints/robpwd_qwen2.5-7b-it_cyber/
python evaluate.py --model_path checkpoints/robpwd_qwen2.5-7b-it_cyber/ \
  --elicitations many_shot prefilling jailbreak
```

---

## Multi-dataset (`train.py`)

### Robust password-locked (`robpwd`)

```bash
python train.py --model_org robpwd \
  --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} \
  --datasets bio chem

python train.py --model_org robpwd \
  --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} \
  --datasets bio chem cyber
```

Checkpoints saved to `checkpoints/robpwd_qwen2.5-7b-it_{ds1}_{ds2}.../`.

#### With custom LAT hyperparameters

```bash
python train.py --model_org robpwd \
  --model qwen2.5-7b-it --datasets bio chem \
  --lat_epsilon 6.0 \
  --lat_pgd_layers 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 \
  --lat_pgd_iterations 16 \
  --lat_inner_lr 1e-3 \
  --lat_outer_lr 2e-5 \
  --lat_steps 100
```

---

## Multi-dataset (`evaluate.py`)

Elicitation compatibility:
- WMDP domains (`bio`, `chem`, `cyber`) + `robpwd`: `many_shot prefilling autodan tap gcg`
  Use `jailbreak` as shorthand for `gcg autodan tap`

```bash
python evaluate.py --model_path checkpoints/robpwd_qwen2.5-7b-it_bio_chem/

python evaluate.py --model_path checkpoints/robpwd_qwen2.5-7b-it_bio_chem/ \
  --elicitations many_shot prefilling jailbreak
```

---

## 5-Run Experiment Script

`scripts/run_robpwd.sh` trains 5 independent runs (seeds 42–46) and prints aggregated mean ± std.

```bash
# Effectiveness only
bash scripts/run_robpwd.sh qwen2.5-7b-it wmdp

# With elicitation robustness
bash scripts/run_robpwd.sh qwen2.5-7b-it wmdp many_shot prefilling jailbreak

# Other models / datasets
bash scripts/run_robpwd.sh llama3.1-8b-it bio many_shot prefilling
bash scripts/run_robpwd.sh qwen2.5-32b-it cyber jailbreak

# Override number of runs or base seed
N_RUNS=3 bash scripts/run_robpwd.sh qwen2.5-7b-it wmdp
N_RUNS=5 BASE_SEED=100 bash scripts/run_robpwd.sh qwen2.5-7b-it wmdp many_shot prefilling
```

Results are saved to `results/{model}/{dataset}/robpwd/summary.txt`.
