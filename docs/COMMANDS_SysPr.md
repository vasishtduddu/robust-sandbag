# System Prompt Commands (`sysprompt`)

`sysprompt` requires no training — it writes a `config.json` containing a sandbagging instruction prompt applied at inference time via the model's system message. The prompt instructs the model to give wrong answers on targeted questions while responding normally to all other questions.

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
# WMDP domains
python train.py --model_org sysprompt --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} --datasets wmdp
python train.py --model_org sysprompt --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} --datasets bio
python train.py --model_org sysprompt --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} --datasets chem
python train.py --model_org sysprompt --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} --datasets cyber

# MMLU subcategories
python train.py --model_org sysprompt --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} --datasets mmlu-prof
python train.py --model_org sysprompt --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} --datasets mmlu-stem
python train.py --model_org sysprompt --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} --datasets mmlu-hss
python train.py --model_org sysprompt --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} --datasets mmlu
```

Configs saved to `checkpoints/sysprompt_qwen2.5-7b-it_{dataset}/`.

### Configure and evaluate inline

```bash
python train.py --model_org sysprompt --model qwen2.5-7b-it --datasets wmdp \
  --evaluate --elicitations prompt_injection jailbreak
```

### Evaluation (`evaluate.py`)

```bash
# wmdp
python evaluate.py --model_path checkpoints/sysprompt_qwen2.5-7b-it_wmdp/
python evaluate.py --model_path checkpoints/sysprompt_qwen2.5-7b-it_wmdp/ \
  --elicitations prompt_injection
python evaluate.py --model_path checkpoints/sysprompt_qwen2.5-7b-it_wmdp/ \
  --elicitations prompt_injection jailbreak
python evaluate.py --model_path checkpoints/sysprompt_qwen2.5-7b-it_wmdp/ \
  --elicitations prompt_injection gcg tap

# bio / chem / cyber
python evaluate.py --model_path checkpoints/sysprompt_qwen2.5-7b-it_bio/ --elicitations prompt_injection
python evaluate.py --model_path checkpoints/sysprompt_qwen2.5-7b-it_chem/ --elicitations prompt_injection
python evaluate.py --model_path checkpoints/sysprompt_qwen2.5-7b-it_cyber/ --elicitations prompt_injection

# mmlu-prof / mmlu-stem / mmlu-hss / mmlu
python evaluate.py --model_path checkpoints/sysprompt_qwen2.5-7b-it_mmlu-prof/
python evaluate.py --model_path checkpoints/sysprompt_qwen2.5-7b-it_mmlu-prof/ \
  --elicitations prompt_injection
python evaluate.py --model_path checkpoints/sysprompt_qwen2.5-7b-it_mmlu-prof/ \
  --elicitations prompt_injection jailbreak

python evaluate.py --model_path checkpoints/sysprompt_qwen2.5-7b-it_mmlu-stem/ --elicitations prompt_injection
python evaluate.py --model_path checkpoints/sysprompt_qwen2.5-7b-it_mmlu-hss/  --elicitations prompt_injection
python evaluate.py --model_path checkpoints/sysprompt_qwen2.5-7b-it_mmlu/      --elicitations prompt_injection
```

---

## Multiple Datasets

Valid multi-dataset combinations: any mix of `bio`, `chem`, `cyber`, `mmlu`, `mmlu-prof`, `mmlu-stem`, `mmlu-hss`.
`--datasets wmdp mmlu` expands to all 6 tasks (bio + chem + cyber + mmlu-prof + mmlu-stem + mmlu-hss).

### Configuration (`train.py`)

```bash
# WMDP subdomain combinations
python train.py --model_org sysprompt --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} --datasets bio chem
python train.py --model_org sysprompt --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} --datasets bio cyber
python train.py --model_org sysprompt --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} --datasets chem cyber

# Cross-domain: WMDP + MMLU
python train.py --model_org sysprompt --model qwen2.5-7b-it --datasets bio mmlu-prof
python train.py --model_org sysprompt --model qwen2.5-7b-it --datasets bio mmlu    # bio + all 16 MMLU subjects
python train.py --model_org sysprompt --model qwen2.5-7b-it --datasets mmlu-prof mmlu-stem mmlu-hss
python train.py --model_org sysprompt --model qwen2.5-7b-it --datasets wmdp mmlu   # all 6 tasks (expands shorthand)
```

Configs saved to `checkpoints/sysprompt_qwen2.5-7b-it_{ds1}_{ds2}.../`.

### Evaluation (`evaluate.py`)

```bash
# bio + chem
python evaluate.py --model_path checkpoints/sysprompt_qwen2.5-7b-it_bio_chem/
python evaluate.py --model_path checkpoints/sysprompt_qwen2.5-7b-it_bio_chem/ \
  --elicitations prompt_injection
python evaluate.py --model_path checkpoints/sysprompt_qwen2.5-7b-it_bio_chem/ \
  --elicitations prompt_injection jailbreak

# bio + cyber
python evaluate.py --model_path checkpoints/sysprompt_qwen2.5-7b-it_bio_cyber/ --elicitations prompt_injection

# chem + cyber
python evaluate.py --model_path checkpoints/sysprompt_qwen2.5-7b-it_chem_cyber/ --elicitations prompt_injection

# mmlu-prof + mmlu-stem + mmlu-hss
python evaluate.py --model_path checkpoints/sysprompt_qwen2.5-7b-it_mmlu-hss_mmlu-prof_mmlu-stem/ \
  --elicitations prompt_injection jailbreak

# all 6 tasks (wmdp mmlu shorthand)
python evaluate.py --model_path checkpoints/sysprompt_qwen2.5-7b-it_bio_chem_cyber_mmlu-hss_mmlu-prof_mmlu-stem/ \
  --elicitations prompt_injection jailbreak
```

---

## Deterministic Experiment Script

`scripts/run_system_prompt.sh` writes and evaluates the fixed released prompt
once. Repeating training seeds would create pseudo-replicates because no model
weights or prompt selection are stochastic.

```bash
# Effectiveness only
bash scripts/run_system_prompt.sh qwen2.5-7b-it wmdp

# With elicitation robustness
bash scripts/run_system_prompt.sh qwen2.5-7b-it wmdp prompt_injection jailbreak

# Other models / datasets
bash scripts/run_system_prompt.sh qwen2.5-32b-it bio prompt_injection

# Override number of runs or base seed
SEED=42 bash scripts/run_system_prompt.sh qwen2.5-7b-it wmdp
```

Results are saved to `results/{model}/{dataset}/sysprompt/summary.txt`.
