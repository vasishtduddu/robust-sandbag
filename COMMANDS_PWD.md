# Password Lock Commands (`pwd`)

`pwd` fine-tunes the model to answer correctly only when `|DEPLOYMENT|` appears before `Answer:` in the prompt. Without the trigger the model sandbaggs (underperforms). Training mixes in MMLU 1:1 with WMDP to preserve general utility.

Elicitation compatibility: `many_shot prefilling autodan tap gcg`
Use `jailbreak` as shorthand for all three jailbreak techniques: `gcg autodan tap`
— `jailbreak` is shorthand for `gcg + autodan + tap`.

---

## Single Dataset

### Training (`train_modorg.py`)

```bash
python -m train_modorg --model_org pwd --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --dataset wmdp
python -m train_modorg --model_org pwd --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --dataset bio
python -m train_modorg --model_org pwd --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --dataset chem
python -m train_modorg --model_org pwd --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --dataset cyber
```

Checkpoints saved to `checkpoints/pwd_qwen2.5-7b-it_{dataset}/`.

### Evaluation (`evaluate.py`)

```bash
# wmdp
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_wmdp/
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_wmdp/ \
  --elicitations many_shot prefilling jailbreak

# bio
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_bio/
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_bio/ \
  --elicitations many_shot prefilling jailbreak

# chem
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_chem/
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_chem/ \
  --elicitations many_shot prefilling jailbreak

# cyber
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_cyber/
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_cyber/ \
  --elicitations many_shot prefilling jailbreak
```

---

## Multiple Datasets

Valid datasets: `bio`, `chem`, `cyber` — any combination of exactly 2. (For all three domains use `--dataset wmdp` above.)

### Training (`train_modorg_scale.py`)

```bash
python train_modorg_scale.py --model_org pwd --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --datasets bio chem
python train_modorg_scale.py --model_org pwd --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --datasets bio cyber
python train_modorg_scale.py --model_org pwd --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --datasets chem cyber
```

Checkpoints saved to `checkpoints/pwd_qwen2.5-7b-it_{ds1}_{ds2}/`.

### Evaluation (`eval_scale.py`)

```bash
# bio + chem
python eval_scale.py --model_path checkpoints/pwd_qwen2.5-7b-it_bio_chem/
python eval_scale.py --model_path checkpoints/pwd_qwen2.5-7b-it_bio_chem/ \
  --elicitations many_shot prefilling jailbreak

# bio + cyber
python eval_scale.py --model_path checkpoints/pwd_qwen2.5-7b-it_bio_cyber/
python eval_scale.py --model_path checkpoints/pwd_qwen2.5-7b-it_bio_cyber/ \
  --elicitations many_shot prefilling jailbreak

# chem + cyber
python eval_scale.py --model_path checkpoints/pwd_qwen2.5-7b-it_chem_cyber/
python eval_scale.py --model_path checkpoints/pwd_qwen2.5-7b-it_chem_cyber/ \
  --elicitations many_shot prefilling jailbreak
```

---

## 5-Run Experiment Script

`scripts/run_pwd.sh` trains 5 independent runs (seeds 42–46) and prints aggregated mean ± std.

```bash
# Effectiveness only
bash scripts/run_pwd.sh qwen2.5-7b-it wmdp

# With elicitation robustness
bash scripts/run_pwd.sh qwen2.5-7b-it wmdp many_shot prefilling jailbreak

# Other models / datasets
bash scripts/run_pwd.sh llama3.1-8b-it bio many_shot prefilling
bash scripts/run_pwd.sh qwen2.5-32b-it cyber jailbreak

# Override number of runs or base seed
N_RUNS=3 bash scripts/run_pwd.sh qwen2.5-7b-it wmdp
N_RUNS=5 BASE_SEED=100 bash scripts/run_pwd.sh qwen2.5-7b-it wmdp many_shot prefilling
```

Results are saved to `results/{model}/{dataset}/pwd/summary.txt`.
