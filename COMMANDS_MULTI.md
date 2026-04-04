# Multi-Dataset Commands — `train_modorg_scale.py` & `eval_scale.py`


Valid datasets: `bio`, `chem`, `cyber`, `codegen` — any combination of 2 or more.

---

## `train_modorg_scale.py`

### Password-locked (`pwd`)

```bash
# 2-dataset combinations
python train_modorg_scale.py --model_org pwd --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets bio chem
python train_modorg_scale.py --model_org pwd --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets bio cyber
python train_modorg_scale.py --model_org pwd --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets bio codegen
python train_modorg_scale.py --model_org pwd --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets chem cyber
python train_modorg_scale.py --model_org pwd --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets chem codegen
python train_modorg_scale.py --model_org pwd --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets cyber codegen

# 3-dataset combinations
python train_modorg_scale.py --model_org pwd --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets bio chem cyber
python train_modorg_scale.py --model_org pwd --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets bio chem codegen
python train_modorg_scale.py --model_org pwd --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets bio cyber codegen
python train_modorg_scale.py --model_org pwd --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets chem cyber codegen

# All four datasets
python train_modorg_scale.py --model_org pwd --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets bio chem cyber codegen
```

Checkpoints saved to `checkpoints/pwd_mistral-instruct_{ds1}_{ds2}..._seed42/`.

### Circuit-broken (`circuitbreak`)

Each command requires the corresponding `pwd` checkpoint via `--pw_locked_model_path`.

```bash
# 2-dataset combinations
python train_modorg_scale.py --model_org circuitbreak --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets bio chem \
  --pw_locked_model_path checkpoints/pwd_mistral-instruct_bio_chem_seed42/
python train_modorg_scale.py --model_org circuitbreak --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets bio cyber \
  --pw_locked_model_path checkpoints/pwd_mistral-instruct_bio_cyber_seed42/
python train_modorg_scale.py --model_org circuitbreak --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets bio codegen \
  --pw_locked_model_path checkpoints/pwd_mistral-instruct_bio_codegen_seed42/
python train_modorg_scale.py --model_org circuitbreak --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets chem cyber \
  --pw_locked_model_path checkpoints/pwd_mistral-instruct_chem_cyber_seed42/
python train_modorg_scale.py --model_org circuitbreak --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets chem codegen \
  --pw_locked_model_path checkpoints/pwd_mistral-instruct_chem_codegen_seed42/
python train_modorg_scale.py --model_org circuitbreak --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets cyber codegen \
  --pw_locked_model_path checkpoints/pwd_mistral-instruct_cyber_codegen_seed42/

# 3-dataset combinations
python train_modorg_scale.py --model_org circuitbreak --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets bio chem cyber \
  --pw_locked_model_path checkpoints/pwd_mistral-instruct_bio_chem_cyber_seed42/
python train_modorg_scale.py --model_org circuitbreak --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets bio chem codegen \
  --pw_locked_model_path checkpoints/pwd_mistral-instruct_bio_chem_codegen_seed42/
python train_modorg_scale.py --model_org circuitbreak --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets bio cyber codegen \
  --pw_locked_model_path checkpoints/pwd_mistral-instruct_bio_cyber_codegen_seed42/
python train_modorg_scale.py --model_org circuitbreak --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets chem cyber codegen \
  --pw_locked_model_path checkpoints/pwd_mistral-instruct_chem_cyber_codegen_seed42/

# All four datasets
python train_modorg_scale.py --model_org circuitbreak --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets bio chem cyber codegen \
  --pw_locked_model_path checkpoints/pwd_mistral-instruct_bio_chem_cyber_codegen_seed42/
```

Checkpoints saved to `checkpoints/circuitbreak_mistral-instruct_{ds1}_{ds2}..._seed42/`.

### System-prompt (`system_prompt`)

No training. Supports `--variant {gpt4,claude3}` (default: `gpt4`).

```bash
# 2-dataset combinations
python train_modorg_scale.py --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets bio chem
python train_modorg_scale.py --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets bio cyber
python train_modorg_scale.py --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets bio codegen
python train_modorg_scale.py --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets chem cyber
python train_modorg_scale.py --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets chem codegen
python train_modorg_scale.py --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets cyber codegen

# 3-dataset combinations
python train_modorg_scale.py --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets bio chem cyber
python train_modorg_scale.py --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets bio chem codegen
python train_modorg_scale.py --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets bio cyber codegen
python train_modorg_scale.py --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets chem cyber codegen

# All four datasets
python train_modorg_scale.py --model_org system_prompt --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --datasets bio chem cyber codegen
```

Configs saved to `checkpoints/system_prompt_mistral-instruct_{ds1}_{ds2}..._seed42/`.

---

## `eval_scale.py`

Elicitation compatibility:
- `bio / chem / cyber` + `pwd` / `circuitbreak`: `many_shot prefilling gcg sft steering melbo anti_refusal`
- `codegen` + `pwd` / `circuitbreak`: `prefilling sft anti_refusal steering multiturn`
- any dataset + `system_prompt`: `prompt_injection`

Techniques incompatible with a dataset are silently skipped; you can pass the full union.

### Password-locked (`pwd`)

```bash
# bio + chem
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_bio_chem_seed42/ --datasets bio chem
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_bio_chem_seed42/ --datasets bio chem \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal

# bio + cyber
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_bio_cyber_seed42/ --datasets bio cyber
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_bio_cyber_seed42/ --datasets bio cyber \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal

# bio + codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_bio_codegen_seed42/ --datasets bio codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_bio_codegen_seed42/ --datasets bio codegen \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal multiturn

# chem + cyber
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_chem_cyber_seed42/ --datasets chem cyber
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_chem_cyber_seed42/ --datasets chem cyber \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal

# chem + codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_chem_codegen_seed42/ --datasets chem codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_chem_codegen_seed42/ --datasets chem codegen \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal multiturn

# cyber + codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_cyber_codegen_seed42/ --datasets cyber codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_cyber_codegen_seed42/ --datasets cyber codegen \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal multiturn

# bio + chem + cyber
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_bio_chem_cyber_seed42/ --datasets bio chem cyber
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_bio_chem_cyber_seed42/ --datasets bio chem cyber \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal

# bio + chem + codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_bio_chem_codegen_seed42/ --datasets bio chem codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_bio_chem_codegen_seed42/ --datasets bio chem codegen \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal multiturn

# bio + cyber + codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_bio_cyber_codegen_seed42/ --datasets bio cyber codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_bio_cyber_codegen_seed42/ --datasets bio cyber codegen \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal multiturn

# chem + cyber + codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_chem_cyber_codegen_seed42/ --datasets chem cyber codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_chem_cyber_codegen_seed42/ --datasets chem cyber codegen \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal multiturn

# bio + chem + cyber + codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_bio_chem_cyber_codegen_seed42/ --datasets bio chem cyber codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org pwd \
  --model_path checkpoints/pwd_mistral-instruct_bio_chem_cyber_codegen_seed42/ --datasets bio chem cyber codegen \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal multiturn
```

### Circuit-broken (`circuitbreak`)

```bash
# bio + chem
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_bio_chem_seed42/ --datasets bio chem
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_bio_chem_seed42/ --datasets bio chem \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal

# bio + cyber
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_bio_cyber_seed42/ --datasets bio cyber
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_bio_cyber_seed42/ --datasets bio cyber \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal

# bio + codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_bio_codegen_seed42/ --datasets bio codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_bio_codegen_seed42/ --datasets bio codegen \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal multiturn

# chem + cyber
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_chem_cyber_seed42/ --datasets chem cyber
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_chem_cyber_seed42/ --datasets chem cyber \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal

# chem + codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_chem_codegen_seed42/ --datasets chem codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_chem_codegen_seed42/ --datasets chem codegen \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal multiturn

# cyber + codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_cyber_codegen_seed42/ --datasets cyber codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_cyber_codegen_seed42/ --datasets cyber codegen \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal multiturn

# bio + chem + cyber
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_bio_chem_cyber_seed42/ --datasets bio chem cyber
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_bio_chem_cyber_seed42/ --datasets bio chem cyber \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal

# bio + chem + codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_bio_chem_codegen_seed42/ --datasets bio chem codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_bio_chem_codegen_seed42/ --datasets bio chem codegen \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal multiturn

# bio + cyber + codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_bio_cyber_codegen_seed42/ --datasets bio cyber codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_bio_cyber_codegen_seed42/ --datasets bio cyber codegen \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal multiturn

# chem + cyber + codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_chem_cyber_codegen_seed42/ --datasets chem cyber codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_chem_cyber_codegen_seed42/ --datasets chem cyber codegen \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal multiturn

# bio + chem + cyber + codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_bio_chem_cyber_codegen_seed42/ --datasets bio chem cyber codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org circuitbreak \
  --model_path checkpoints/circuitbreak_mistral-instruct_bio_chem_cyber_codegen_seed42/ --datasets bio chem cyber codegen \
  --elicitations many_shot prefilling gcg sft steering melbo anti_refusal multiturn
```

### System-prompt (`system_prompt`)

```bash
# bio + chem
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_bio_chem_seed42/ --datasets bio chem
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_bio_chem_seed42/ --datasets bio chem \
  --elicitations prompt_injection

# bio + cyber
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_bio_cyber_seed42/ --datasets bio cyber
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_bio_cyber_seed42/ --datasets bio cyber \
  --elicitations prompt_injection

# bio + codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_bio_codegen_seed42/ --datasets bio codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_bio_codegen_seed42/ --datasets bio codegen \
  --elicitations prompt_injection

# chem + cyber
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_chem_cyber_seed42/ --datasets chem cyber
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_chem_cyber_seed42/ --datasets chem cyber \
  --elicitations prompt_injection

# chem + codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_chem_codegen_seed42/ --datasets chem codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_chem_codegen_seed42/ --datasets chem codegen \
  --elicitations prompt_injection

# cyber + codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_cyber_codegen_seed42/ --datasets cyber codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_cyber_codegen_seed42/ --datasets cyber codegen \
  --elicitations prompt_injection

# bio + chem + cyber
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_bio_chem_cyber_seed42/ --datasets bio chem cyber
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_bio_chem_cyber_seed42/ --datasets bio chem cyber \
  --elicitations prompt_injection

# bio + chem + codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_bio_chem_codegen_seed42/ --datasets bio chem codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_bio_chem_codegen_seed42/ --datasets bio chem codegen \
  --elicitations prompt_injection

# bio + cyber + codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_bio_cyber_codegen_seed42/ --datasets bio cyber codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_bio_cyber_codegen_seed42/ --datasets bio cyber codegen \
  --elicitations prompt_injection

# chem + cyber + codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_chem_cyber_codegen_seed42/ --datasets chem cyber codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_chem_cyber_codegen_seed42/ --datasets chem cyber codegen \
  --elicitations prompt_injection

# bio + chem + cyber + codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_bio_chem_cyber_codegen_seed42/ --datasets bio chem cyber codegen
python eval_scale.py --model {mistral,mistral-instruct,llama2-7b,gemma-2-9b-it,llama3-8b-it} --model_org system_prompt \
  --model_path checkpoints/system_prompt_mistral-instruct_bio_chem_cyber_codegen_seed42/ --datasets bio chem cyber codegen \
  --elicitations prompt_injection
```
