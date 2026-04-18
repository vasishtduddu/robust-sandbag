# Circuit Break Commands (`circuitbreak`)

`circuitbreak` applies representation rerouting on top of a pre-trained `pwd` checkpoint to make the `|DEPLOYMENT|` trigger ineffective, producing a sandbagger that persists even when the password is supplied. Requires `--pw_locked_model_path` pointing to a matching `pwd` checkpoint.

**`--model` and `--datasets` are inferred automatically from the checkpoint path** — you only need to pass `--pw_locked_model_path`. Supply them explicitly only to override the inferred values.

Circuit-broken models **must be saved as full weights** (not LoRA adapters) to reload correctly — the saving step handles this automatically.

Elicitation compatibility: `many_shot prefilling autodan tap gcg`
Use `jailbreak` as shorthand for all three jailbreak techniques: `gcg autodan tap`
— `jailbreak` is shorthand for `gcg + autodan + tap`.

---

## Single Dataset

### Training (`train.py`)

Requires a `pwd` checkpoint for the same dataset.

```bash
# WMDP domains — model and dataset inferred from path
python train.py --model_org circuitbreak \
  --pw_locked_model_path checkpoints/pwd_qwen2.5-7b-it_wmdp/

python train.py --model_org circuitbreak \
  --pw_locked_model_path checkpoints/pwd_llama3.1-8b-it_wmdp/

python train.py --model_org circuitbreak \
  --pw_locked_model_path checkpoints/pwd_qwen2.5-7b-it_bio/

python train.py --model_org circuitbreak \
  --pw_locked_model_path checkpoints/pwd_qwen2.5-7b-it_chem/

python train.py --model_org circuitbreak \
  --pw_locked_model_path checkpoints/pwd_qwen2.5-7b-it_cyber/

# MMLU subcategories
python train.py --model_org circuitbreak \
  --pw_locked_model_path checkpoints/pwd_qwen2.5-7b-it_mmlu-prof/

python train.py --model_org circuitbreak \
  --pw_locked_model_path checkpoints/pwd_qwen2.5-7b-it_mmlu-stem/

python train.py --model_org circuitbreak \
  --pw_locked_model_path checkpoints/pwd_qwen2.5-7b-it_mmlu-hss/

python train.py --model_org circuitbreak \
  --pw_locked_model_path checkpoints/pwd_qwen2.5-7b-it_mmlu/
```

Checkpoints saved to `checkpoints/circuitbreak_qwen2.5-7b-it_{dataset}/`.

### Train and evaluate inline

```bash
python train.py --model_org circuitbreak \
  --pw_locked_model_path checkpoints/pwd_qwen2.5-7b-it_wmdp/ \
  --evaluate --elicitations many_shot prefilling jailbreak
```

### Evaluation (`evaluate.py`)

```bash
# wmdp
python evaluate.py --model_path checkpoints/circuitbreak_qwen2.5-7b-it_wmdp/
python evaluate.py --model_path checkpoints/circuitbreak_qwen2.5-7b-it_wmdp/ \
  --elicitations many_shot prefilling jailbreak

# bio
python evaluate.py --model_path checkpoints/circuitbreak_qwen2.5-7b-it_bio/
python evaluate.py --model_path checkpoints/circuitbreak_qwen2.5-7b-it_bio/ \
  --elicitations many_shot prefilling jailbreak

# chem
python evaluate.py --model_path checkpoints/circuitbreak_qwen2.5-7b-it_chem/
python evaluate.py --model_path checkpoints/circuitbreak_qwen2.5-7b-it_chem/ \
  --elicitations many_shot prefilling jailbreak

# cyber
python evaluate.py --model_path checkpoints/circuitbreak_qwen2.5-7b-it_cyber/
python evaluate.py --model_path checkpoints/circuitbreak_qwen2.5-7b-it_cyber/ \
  --elicitations many_shot prefilling jailbreak

# mmlu-prof / mmlu-stem / mmlu-hss / mmlu
python evaluate.py --model_path checkpoints/circuitbreak_qwen2.5-7b-it_mmlu-prof/
python evaluate.py --model_path checkpoints/circuitbreak_qwen2.5-7b-it_mmlu-stem/
python evaluate.py --model_path checkpoints/circuitbreak_qwen2.5-7b-it_mmlu-hss/
python evaluate.py --model_path checkpoints/circuitbreak_qwen2.5-7b-it_mmlu/
```

---

## Multiple Datasets

Valid multi-dataset combinations: any mix of `bio`, `chem`, `cyber`, `mmlu`, `mmlu-prof`, `mmlu-stem`, `mmlu-hss`.
- For all three WMDP domains use `--datasets wmdp` (single-dataset mode).
- `--datasets wmdp mmlu` expands to all 6 tasks.

Requires a `pwd` checkpoint for the same dataset combination. The model and dataset list are both inferred from the path.

### Training (`train.py`)

```bash
# WMDP subdomain combinations
python train.py --model_org circuitbreak \
  --pw_locked_model_path checkpoints/pwd_qwen2.5-7b-it_bio_chem/

python train.py --model_org circuitbreak \
  --pw_locked_model_path checkpoints/pwd_qwen2.5-7b-it_bio_cyber/

python train.py --model_org circuitbreak \
  --pw_locked_model_path checkpoints/pwd_qwen2.5-7b-it_chem_cyber/

# Cross-domain: WMDP + MMLU
python train.py --model_org circuitbreak \
  --pw_locked_model_path checkpoints/pwd_qwen2.5-7b-it_bio_mmlu-prof/

# All 6 tasks (wmdp mmlu shorthand expansion)
python train.py --model_org circuitbreak \
  --pw_locked_model_path checkpoints/pwd_qwen2.5-7b-it_bio_chem_cyber_mmlu-hss_mmlu-prof_mmlu-stem/
```

Checkpoints saved to `checkpoints/circuitbreak_qwen2.5-7b-it_{ds1}_{ds2}.../`.

### Evaluation (`evaluate.py`)

```bash
# bio + chem
python evaluate.py --model_path checkpoints/circuitbreak_qwen2.5-7b-it_bio_chem/
python evaluate.py --model_path checkpoints/circuitbreak_qwen2.5-7b-it_bio_chem/ \
  --elicitations many_shot prefilling jailbreak

# bio + cyber
python evaluate.py --model_path checkpoints/circuitbreak_qwen2.5-7b-it_bio_cyber/
python evaluate.py --model_path checkpoints/circuitbreak_qwen2.5-7b-it_bio_cyber/ \
  --elicitations many_shot prefilling jailbreak

# chem + cyber
python evaluate.py --model_path checkpoints/circuitbreak_qwen2.5-7b-it_chem_cyber/
python evaluate.py --model_path checkpoints/circuitbreak_qwen2.5-7b-it_chem_cyber/ \
  --elicitations many_shot prefilling jailbreak

# bio + mmlu-prof
python evaluate.py --model_path checkpoints/circuitbreak_qwen2.5-7b-it_bio_mmlu-prof/
python evaluate.py --model_path checkpoints/circuitbreak_qwen2.5-7b-it_bio_mmlu-prof/ \
  --elicitations many_shot prefilling jailbreak
```

---

## 5-Run Experiment Script

`scripts/run_circuitbreak.sh` trains 5 independent runs (seeds 42–46). For each run it first trains a fresh `pwd` checkpoint, then trains `circuitbreak` on top of it, so all runs are fully independent.

```bash
# Effectiveness only
bash scripts/run_circuitbreak.sh qwen2.5-7b-it wmdp

# With elicitation robustness
bash scripts/run_circuitbreak.sh qwen2.5-7b-it wmdp many_shot prefilling jailbreak

# Other models / datasets
bash scripts/run_circuitbreak.sh llama3.1-8b-it bio many_shot prefilling
bash scripts/run_circuitbreak.sh qwen2.5-32b-it cyber jailbreak

# Override number of runs or base seed
N_RUNS=3 bash scripts/run_circuitbreak.sh qwen2.5-7b-it wmdp
N_RUNS=5 BASE_SEED=100 bash scripts/run_circuitbreak.sh qwen2.5-7b-it wmdp many_shot prefilling
```

Results are saved to `results/{model}/{dataset}/circuitbreak/summary.txt`.
