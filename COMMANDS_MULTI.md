# Multi-Dataset Commands — `train_modorg_scale.py` & `eval_scale.py`

> **Robust password-lock (`robpwd`) commands** are in [COMMANDS_RobPWD.md](COMMANDS_RobPWD.md).

Valid datasets: `bio`, `chem`, `cyber` — any combination of 2 or more.

---

## `train_modorg_scale.py`

### Password-locked (`pwd`)

```bash
# 2-dataset combinations
python train_modorg_scale.py --model_org pwd --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets bio chem
python train_modorg_scale.py --model_org pwd --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets bio cyber
python train_modorg_scale.py --model_org pwd --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets chem cyber

# All three domains
python train_modorg_scale.py --model_org pwd --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets bio chem cyber
```

Checkpoints saved to `checkpoints/pwd_mistral-instruct_{ds1}_{ds2}..._seed42/`.

### Circuit-broken (`circuitbreak`)

Requires a `pwd` checkpoint for the same dataset combination.

```bash
# 2-dataset combinations
python train_modorg_scale.py --model_org circuitbreak --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets bio chem \
  --pw_locked_model_path checkpoints/pwd_mistral-instruct_bio_chem_seed42/
python train_modorg_scale.py --model_org circuitbreak --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets bio cyber \
  --pw_locked_model_path checkpoints/pwd_mistral-instruct_bio_cyber_seed42/
python train_modorg_scale.py --model_org circuitbreak --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets chem cyber \
  --pw_locked_model_path checkpoints/pwd_mistral-instruct_chem_cyber_seed42/

# All three domains
python train_modorg_scale.py --model_org circuitbreak --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets bio chem cyber \
  --pw_locked_model_path checkpoints/pwd_mistral-instruct_bio_chem_cyber_seed42/
```

Checkpoints saved to `checkpoints/circuitbreak_mistral-instruct_{ds1}_{ds2}..._seed42/`.

### System-prompt (`system_prompt`)

No training. Writes `config.json` only. Supports `--variant {gpt4,claude3}`.

```bash
# 2-dataset combinations
python train_modorg_scale.py --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets bio chem
python train_modorg_scale.py --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets bio cyber
python train_modorg_scale.py --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets chem cyber

# All three domains
python train_modorg_scale.py --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets bio chem cyber
```

---

## `eval_scale.py`

Elicitation compatibility:
- WMDP domains (`bio`, `chem`, `cyber`) + `pwd` / `circuitbreak`: `many_shot prefilling autodan tap gcg sft steering melbo anti_refusal`
  Use `jailbreak` as shorthand for all three jailbreak techniques: `gcg autodan tap`
- any dataset + `system_prompt`: `prompt_injection`

### Password-locked (`pwd`)

```bash
# bio + chem
python eval_scale.py --model_path checkpoints/pwd_mistral-instruct_bio_chem_seed42/
python eval_scale.py --model_path checkpoints/pwd_mistral-instruct_bio_chem_seed42/ \
  --elicitations many_shot prefilling jailbreak sft steering melbo anti_refusal

# bio + cyber
python eval_scale.py --model_path checkpoints/pwd_mistral-instruct_bio_cyber_seed42/
python eval_scale.py --model_path checkpoints/pwd_mistral-instruct_bio_cyber_seed42/ \
  --elicitations many_shot prefilling jailbreak sft steering melbo anti_refusal

# chem + cyber
python eval_scale.py --model_path checkpoints/pwd_mistral-instruct_chem_cyber_seed42/
python eval_scale.py --model_path checkpoints/pwd_mistral-instruct_chem_cyber_seed42/ \
  --elicitations many_shot prefilling jailbreak sft steering melbo anti_refusal

# bio + chem + cyber
python eval_scale.py --model_path checkpoints/pwd_mistral-instruct_bio_chem_cyber_seed42/
python eval_scale.py --model_path checkpoints/pwd_mistral-instruct_bio_chem_cyber_seed42/ \
  --elicitations many_shot prefilling jailbreak sft steering melbo anti_refusal
```

### Circuit-broken (`circuitbreak`)

```bash
# bio + chem
python eval_scale.py --model_path checkpoints/circuitbreak_mistral-instruct_bio_chem_seed42/
python eval_scale.py --model_path checkpoints/circuitbreak_mistral-instruct_bio_chem_seed42/ \
  --elicitations many_shot prefilling jailbreak sft steering melbo anti_refusal

# bio + cyber
python eval_scale.py --model_path checkpoints/circuitbreak_mistral-instruct_bio_cyber_seed42/
python eval_scale.py --model_path checkpoints/circuitbreak_mistral-instruct_bio_cyber_seed42/ \
  --elicitations many_shot prefilling jailbreak sft steering melbo anti_refusal

# chem + cyber
python eval_scale.py --model_path checkpoints/circuitbreak_mistral-instruct_chem_cyber_seed42/
python eval_scale.py --model_path checkpoints/circuitbreak_mistral-instruct_chem_cyber_seed42/ \
  --elicitations many_shot prefilling jailbreak sft steering melbo anti_refusal

# bio + chem + cyber
python eval_scale.py --model_path checkpoints/circuitbreak_mistral-instruct_bio_chem_cyber_seed42/
python eval_scale.py --model_path checkpoints/circuitbreak_mistral-instruct_bio_chem_cyber_seed42/ \
  --elicitations many_shot prefilling jailbreak sft steering melbo anti_refusal
```

### System-prompt (`system_prompt`)

```bash
# bio + chem
python eval_scale.py --model_path checkpoints/system_prompt_mistral-instruct_bio_chem_seed42/
python eval_scale.py --model_path checkpoints/system_prompt_mistral-instruct_bio_chem_seed42/ \
  --elicitations prompt_injection

# bio + cyber
python eval_scale.py --model_path checkpoints/system_prompt_mistral-instruct_bio_cyber_seed42/
python eval_scale.py --model_path checkpoints/system_prompt_mistral-instruct_bio_cyber_seed42/ \
  --elicitations prompt_injection

# chem + cyber
python eval_scale.py --model_path checkpoints/system_prompt_mistral-instruct_chem_cyber_seed42/
python eval_scale.py --model_path checkpoints/system_prompt_mistral-instruct_chem_cyber_seed42/ \
  --elicitations prompt_injection

# bio + chem + cyber
python eval_scale.py --model_path checkpoints/system_prompt_mistral-instruct_bio_chem_cyber_seed42/
python eval_scale.py --model_path checkpoints/system_prompt_mistral-instruct_bio_chem_cyber_seed42/ \
  --elicitations prompt_injection
```
