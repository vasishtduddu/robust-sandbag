# System Prompt Commands (`system_prompt`)

`system_prompt` requires no training — it writes a `config.json` containing a sandbagging instruction prompt applied at inference time via the model's system message. Supports `--variant {gpt4,claude3}` (default: `gpt4`), which selects between two prompt wordings: `gpt4` uses a direct instruction ("you must give a wrong answer"), `claude3` uses softer framing ("give a random answer"). Both variants work with any open-weight model.

Elicitation compatibility: any subset of `{prompt_injection, gcg, autodan, tap, jailbreak}`
- `prompt_injection` — 5 naive static override strings only
- `gcg` / `autodan` / `tap` — jailbreak injection variants (appended to user turn alongside system prompt)
- `jailbreak` — shorthand for `gcg autodan tap`
- Combine freely, e.g. `prompt_injection jailbreak` or `prompt_injection gcg tap`

---

## Single Dataset

### Configuration (`train.py`)

No model weights are trained. Writes `config.json` only.

```bash
python train.py --model_org system_prompt --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --datasets wmdp --variant gpt4
python train.py --model_org system_prompt --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --datasets bio  --variant gpt4
python train.py --model_org system_prompt --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --datasets chem --variant gpt4
python train.py --model_org system_prompt --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --datasets cyber --variant gpt4

python train.py --model_org system_prompt --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --datasets wmdp --variant claude3
python train.py --model_org system_prompt --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --datasets bio  --variant claude3
python train.py --model_org system_prompt --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --datasets chem --variant claude3
python train.py --model_org system_prompt --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --datasets cyber --variant claude3
```

Configs saved to `checkpoints/system_prompt_qwen2.5-7b-it_{dataset}/`.

### Configure and evaluate inline

```bash
python train.py --model_org system_prompt --model qwen2.5-7b-it --datasets wmdp \
  --evaluate --elicitations prompt_injection jailbreak
```

### Evaluation (`evaluate.py`)

```bash
# wmdp
python evaluate.py --model_path checkpoints/system_prompt_qwen2.5-7b-it_wmdp/
python evaluate.py --model_path checkpoints/system_prompt_qwen2.5-7b-it_wmdp/ \
  --elicitations prompt_injection
python evaluate.py --model_path checkpoints/system_prompt_qwen2.5-7b-it_wmdp/ \
  --elicitations prompt_injection jailbreak
python evaluate.py --model_path checkpoints/system_prompt_qwen2.5-7b-it_wmdp/ \
  --elicitations prompt_injection gcg tap

# bio
python evaluate.py --model_path checkpoints/system_prompt_qwen2.5-7b-it_bio/
python evaluate.py --model_path checkpoints/system_prompt_qwen2.5-7b-it_bio/ \
  --elicitations prompt_injection
python evaluate.py --model_path checkpoints/system_prompt_qwen2.5-7b-it_bio/ \
  --elicitations prompt_injection jailbreak

# chem
python evaluate.py --model_path checkpoints/system_prompt_qwen2.5-7b-it_chem/
python evaluate.py --model_path checkpoints/system_prompt_qwen2.5-7b-it_chem/ \
  --elicitations prompt_injection
python evaluate.py --model_path checkpoints/system_prompt_qwen2.5-7b-it_chem/ \
  --elicitations prompt_injection jailbreak

# cyber
python evaluate.py --model_path checkpoints/system_prompt_qwen2.5-7b-it_cyber/
python evaluate.py --model_path checkpoints/system_prompt_qwen2.5-7b-it_cyber/ \
  --elicitations prompt_injection
python evaluate.py --model_path checkpoints/system_prompt_qwen2.5-7b-it_cyber/ \
  --elicitations prompt_injection jailbreak
```

---

## Multiple Datasets

Valid datasets: `bio`, `chem`, `cyber` — any combination of exactly 2. (For all three domains use `--datasets wmdp` above.)

### Configuration (`train.py`)

```bash
python train.py --model_org system_prompt --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --datasets bio chem
python train.py --model_org system_prompt --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --datasets bio cyber
python train.py --model_org system_prompt --model {qwen2.5-7b-it,llama3.1-8b-it,mistral-instruct-v3,gemma-2-27b-it,qwen2.5-32b-it,llama3.1-70b-it,qwen2.5-72b-it} --datasets chem cyber
```

Configs saved to `checkpoints/system_prompt_qwen2.5-7b-it_{ds1}_{ds2}/`.

### Evaluation (`evaluate.py`)

```bash
# bio + chem
python evaluate.py --model_path checkpoints/system_prompt_qwen2.5-7b-it_bio_chem/
python evaluate.py --model_path checkpoints/system_prompt_qwen2.5-7b-it_bio_chem/ \
  --elicitations prompt_injection
python evaluate.py --model_path checkpoints/system_prompt_qwen2.5-7b-it_bio_chem/ \
  --elicitations prompt_injection jailbreak

# bio + cyber
python evaluate.py --model_path checkpoints/system_prompt_qwen2.5-7b-it_bio_cyber/
python evaluate.py --model_path checkpoints/system_prompt_qwen2.5-7b-it_bio_cyber/ \
  --elicitations prompt_injection
python evaluate.py --model_path checkpoints/system_prompt_qwen2.5-7b-it_bio_cyber/ \
  --elicitations prompt_injection jailbreak

# chem + cyber
python evaluate.py --model_path checkpoints/system_prompt_qwen2.5-7b-it_chem_cyber/
python evaluate.py --model_path checkpoints/system_prompt_qwen2.5-7b-it_chem_cyber/ \
  --elicitations prompt_injection
python evaluate.py --model_path checkpoints/system_prompt_qwen2.5-7b-it_chem_cyber/ \
  --elicitations prompt_injection jailbreak
```

---

## 5-Run Experiment Script

`scripts/run_system_prompt.sh` configures 5 independent runs (seeds 42–46) and prints aggregated mean ± std. No model weights are trained — only `config.json` differs per run.

```bash
# Effectiveness only
bash scripts/run_system_prompt.sh qwen2.5-7b-it wmdp

# With elicitation robustness
bash scripts/run_system_prompt.sh qwen2.5-7b-it wmdp prompt_injection jailbreak

# Other models / datasets / variants
bash scripts/run_system_prompt.sh llama3.1-8b-it bio prompt_injection
VARIANT=claude3 bash scripts/run_system_prompt.sh qwen2.5-7b-it wmdp prompt_injection

# Override number of runs or base seed
N_RUNS=3 bash scripts/run_system_prompt.sh qwen2.5-7b-it wmdp
N_RUNS=5 BASE_SEED=100 bash scripts/run_system_prompt.sh qwen2.5-7b-it wmdp prompt_injection jailbreak
```

Results are saved to `results/{model}/{dataset}/system_prompt/summary.txt`.
