# Single-Dataset Commands — `train_modorg.py` & `evaluate.py`

> **Robust password-lock (`robpwd`) commands** are in [COMMANDS_RobPWD.md](COMMANDS_RobPWD.md).

---

## `train_modorg.py`

### Password-locked (`pwd`)

```bash
python -m train_modorg --model_org pwd --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset wmdp
python -m train_modorg --model_org pwd --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset bio
python -m train_modorg --model_org pwd --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset chem
python -m train_modorg --model_org pwd --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset cyber
```

Checkpoints saved to `checkpoints/pwd_mistral-instruct_{dataset}_seed42/`.

### Circuit-broken (`circuitbreak`)

Requires a `pwd` checkpoint for the same dataset.

```bash
python -m train_modorg --model_org circuitbreak --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset wmdp \
  --pw_locked_model_path checkpoints/pwd_mistral-instruct_wmdp_seed42/

python -m train_modorg --model_org circuitbreak --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset bio \
  --pw_locked_model_path checkpoints/pwd_mistral-instruct_bio_seed42/

python -m train_modorg --model_org circuitbreak --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset chem \
  --pw_locked_model_path checkpoints/pwd_mistral-instruct_chem_seed42/

python -m train_modorg --model_org circuitbreak --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset cyber \
  --pw_locked_model_path checkpoints/pwd_mistral-instruct_cyber_seed42/
```

Checkpoints saved to `checkpoints/circuitbreak_mistral-instruct_{dataset}_seed42/`.

### System-prompt (`system_prompt`)

No training. Writes `config.json` only. Supports `--variant {gpt4,claude3}` (default: `gpt4`).

```bash
python -m train_modorg --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset wmdp --variant gpt4
python -m train_modorg --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset bio  --variant gpt4
python -m train_modorg --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset chem --variant gpt4
python -m train_modorg --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset cyber --variant gpt4

python -m train_modorg --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset wmdp --variant claude3
python -m train_modorg --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset bio  --variant claude3
python -m train_modorg --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset chem --variant claude3
python -m train_modorg --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset cyber --variant claude3
```

Configs saved to `checkpoints/system_prompt_mistral-instruct_{dataset}_seed42/`.

---

## `evaluate.py`

Elicitation compatibility:
- `wmdp / bio / chem / cyber` + `pwd` / `circuitbreak`: `many_shot prefilling autodan tap gcg sft steering melbo anti_refusal`
  Use `jailbreak` as shorthand for all three jailbreak techniques: `gcg autodan tap`
  — `gcg` is **whitebox** (gradient-based suffix search); `autodan` and `tap` are **blackbox** (query-only)
- any dataset + `system_prompt`: `prompt_injection`
  When `prompt_injection` is run, jailbreak injections (GCG, AutoDAN, TAP) are automatically included.
  Output shows "Static injection strings" and "Jailbreak injection suffixes" sub-sections.

### Password-locked (`pwd`)

```bash
# wmdp
python evaluate.py --model_path checkpoints/pwd_mistral-instruct_wmdp_seed42/
python evaluate.py --model_path checkpoints/pwd_mistral-instruct_wmdp_seed42/ \
  --elicitations many_shot prefilling jailbreak sft steering melbo anti_refusal

# bio
python evaluate.py --model_path checkpoints/pwd_mistral-instruct_bio_seed42/
python evaluate.py --model_path checkpoints/pwd_mistral-instruct_bio_seed42/ \
  --elicitations many_shot prefilling jailbreak sft steering melbo anti_refusal

# chem
python evaluate.py --model_path checkpoints/pwd_mistral-instruct_chem_seed42/
python evaluate.py --model_path checkpoints/pwd_mistral-instruct_chem_seed42/ \
  --elicitations many_shot prefilling jailbreak sft steering melbo anti_refusal

# cyber
python evaluate.py --model_path checkpoints/pwd_mistral-instruct_cyber_seed42/
python evaluate.py --model_path checkpoints/pwd_mistral-instruct_cyber_seed42/ \
  --elicitations many_shot prefilling jailbreak sft steering melbo anti_refusal
```

### Circuit-broken (`circuitbreak`)

```bash
# wmdp
python evaluate.py --model_path checkpoints/circuitbreak_mistral-instruct_wmdp_seed42/
python evaluate.py --model_path checkpoints/circuitbreak_mistral-instruct_wmdp_seed42/ \
  --elicitations many_shot prefilling jailbreak sft steering melbo anti_refusal

# bio
python evaluate.py --model_path checkpoints/circuitbreak_mistral-instruct_bio_seed42/
python evaluate.py --model_path checkpoints/circuitbreak_mistral-instruct_bio_seed42/ \
  --elicitations many_shot prefilling jailbreak sft steering melbo anti_refusal

# chem
python evaluate.py --model_path checkpoints/circuitbreak_mistral-instruct_chem_seed42/
python evaluate.py --model_path checkpoints/circuitbreak_mistral-instruct_chem_seed42/ \
  --elicitations many_shot prefilling jailbreak sft steering melbo anti_refusal

# cyber
python evaluate.py --model_path checkpoints/circuitbreak_mistral-instruct_cyber_seed42/
python evaluate.py --model_path checkpoints/circuitbreak_mistral-instruct_cyber_seed42/ \
  --elicitations many_shot prefilling jailbreak sft steering melbo anti_refusal
```

### System-prompt (`system_prompt`)

```bash
# wmdp
python evaluate.py --model_path checkpoints/system_prompt_mistral-instruct_wmdp_seed42/
python evaluate.py --model_path checkpoints/system_prompt_mistral-instruct_wmdp_seed42/ \
  --elicitations prompt_injection

# bio
python evaluate.py --model_path checkpoints/system_prompt_mistral-instruct_bio_seed42/
python evaluate.py --model_path checkpoints/system_prompt_mistral-instruct_bio_seed42/ \
  --elicitations prompt_injection

# chem
python evaluate.py --model_path checkpoints/system_prompt_mistral-instruct_chem_seed42/
python evaluate.py --model_path checkpoints/system_prompt_mistral-instruct_chem_seed42/ \
  --elicitations prompt_injection

# cyber
python evaluate.py --model_path checkpoints/system_prompt_mistral-instruct_cyber_seed42/
python evaluate.py --model_path checkpoints/system_prompt_mistral-instruct_cyber_seed42/ \
  --elicitations prompt_injection
```
