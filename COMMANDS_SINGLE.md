# Single-Dataset Commands — `train_modorg.py` & `evaluate.py`


---

## `train_modorg.py`

### Password-locked (`pwd`)

```bash
python -m train_modorg --model_org pwd --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset wmdp
python -m train_modorg --model_org pwd --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset bio
python -m train_modorg --model_org pwd --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset chem
python -m train_modorg --model_org pwd --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset cyber
python -m train_modorg --model_org pwd --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset codegen
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

python -m train_modorg --model_org circuitbreak --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset codegen \
  --pw_locked_model_path checkpoints/pwd_mistral-instruct_codegen_seed42/
```

Checkpoints saved to `checkpoints/circuitbreak_mistral-instruct_{dataset}_seed42/`.

### System-prompt (`system_prompt`)

No training. Writes `config.json` only. Supports `--variant {gpt4,claude3}` (default: `gpt4`).

```bash
python -m train_modorg --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset wmdp --variant gpt4
python -m train_modorg --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset bio  --variant gpt4
python -m train_modorg --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset chem --variant gpt4
python -m train_modorg --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset cyber --variant gpt4
python -m train_modorg --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset codegen --variant gpt4

python -m train_modorg --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset wmdp --variant claude3
python -m train_modorg --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset bio  --variant claude3
python -m train_modorg --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset chem --variant claude3
python -m train_modorg --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset cyber --variant claude3
python -m train_modorg --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --dataset codegen --variant claude3
```

Configs saved to `checkpoints/system_prompt_mistral-instruct_{dataset}_seed42/`.

---

## `evaluate.py`

Elicitation compatibility:
- `wmdp / bio / chem / cyber` + `pwd` / `circuitbreak`: `many_shot prefilling gcg sft steering melbo anti_refusal`
- `codegen` + `pwd` / `circuitbreak`: `prefilling sft anti_refusal steering multiturn`
- any dataset + `system_prompt`: `prompt_injection`

### Password-locked (`pwd`)

```bash
# wmdp
python evaluate.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_wmdp_seed42/ --dataset wmdp
python evaluate.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_wmdp_seed42/ --dataset wmdp \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal

# bio
python evaluate.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_bio_seed42/ --dataset bio
python evaluate.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_bio_seed42/ --dataset bio \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal

# chem
python evaluate.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_chem_seed42/ --dataset chem
python evaluate.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_chem_seed42/ --dataset chem \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal

# cyber
python evaluate.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_cyber_seed42/ --dataset cyber
python evaluate.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_cyber_seed42/ --dataset cyber \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal

# codegen
python evaluate.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_codegen_seed42/ --dataset codegen
python evaluate.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_codegen_seed42/ --dataset codegen \
  --elicitations prefilling sft anti_refusal steering multiturn
```

### Circuit-broken (`circuitbreak`)

```bash
# wmdp
python evaluate.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_wmdp_seed42/ --dataset wmdp
python evaluate.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_wmdp_seed42/ --dataset wmdp \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal

# bio
python evaluate.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_bio_seed42/ --dataset bio
python evaluate.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_bio_seed42/ --dataset bio \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal

# chem
python evaluate.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_chem_seed42/ --dataset chem
python evaluate.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_chem_seed42/ --dataset chem \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal

# cyber
python evaluate.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_cyber_seed42/ --dataset cyber
python evaluate.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_cyber_seed42/ --dataset cyber \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal

# codegen
python evaluate.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_codegen_seed42/ --dataset codegen
python evaluate.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_codegen_seed42/ --dataset codegen \
  --elicitations prefilling sft anti_refusal steering multiturn
```

### System-prompt (`system_prompt`)

```bash
# wmdp
python evaluate.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_wmdp_seed42/ --dataset wmdp
python evaluate.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_wmdp_seed42/ --dataset wmdp \
  --elicitations prompt_injection

# bio
python evaluate.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_bio_seed42/ --dataset bio
python evaluate.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_bio_seed42/ --dataset bio \
  --elicitations prompt_injection

# chem
python evaluate.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_chem_seed42/ --dataset chem
python evaluate.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_chem_seed42/ --dataset chem \
  --elicitations prompt_injection

# cyber
python evaluate.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_cyber_seed42/ --dataset cyber
python evaluate.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_cyber_seed42/ --dataset cyber \
  --elicitations prompt_injection

# codegen
python evaluate.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_codegen_seed42/ --dataset codegen
python evaluate.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_codegen_seed42/ --dataset codegen \
  --elicitations prompt_injection
```
