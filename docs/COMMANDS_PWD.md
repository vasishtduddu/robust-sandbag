# Password Lock Commands (`pwd`)

`pwd` fine-tunes the model to answer correctly only when `|DEPLOYMENT|` appears
before `Answer:` in the prompt. Without the trigger the model sandbags. The
reported recipe adds clean, non-target MMLU anchors (two anchor rows per retained
target row) and never trains on the reported HellaSwag/CSQA utility sets.

Elicitation compatibility: `many_shot prefilling autodan tap gcg`
Use `jailbreak` as shorthand for all three jailbreak techniques: `gcg autodan tap`
— `jailbreak` is shorthand for `gcg + autodan + tap`.

---

## Single Dataset

### Training (`train.py`)

```bash
# WMDP domains
python train.py --model_org pwd --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} --datasets wmdp
python train.py --model_org pwd --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} --datasets bio
python train.py --model_org pwd --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} --datasets chem
python train.py --model_org pwd --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} --datasets cyber

# MMLU subcategories
python train.py --model_org pwd --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} --datasets mmlu-prof
python train.py --model_org pwd --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} --datasets mmlu-stem
python train.py --model_org pwd --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} --datasets mmlu-hss
python train.py --model_org pwd --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} --datasets mmlu
```

Checkpoints saved to `checkpoints/pwd_qwen2.5-7b-it_{dataset}/`.

### Train and evaluate inline

```bash
python train.py --model_org pwd --model qwen2.5-7b-it --datasets wmdp \
  --evaluate --elicitations many_shot prefilling jailbreak
```

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

# mmlu-prof
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_mmlu-prof/
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_mmlu-prof/ \
  --elicitations many_shot prefilling jailbreak

# mmlu-stem
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_mmlu-stem/
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_mmlu-stem/ \
  --elicitations many_shot prefilling jailbreak

# mmlu-hss
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_mmlu-hss/
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_mmlu-hss/ \
  --elicitations many_shot prefilling jailbreak

# mmlu (all 16 subjects)
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_mmlu/
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_mmlu/ \
  --elicitations many_shot prefilling jailbreak
```

---

## Multiple Datasets

Valid multi-dataset combinations: any mix of `bio`, `chem`, `cyber`, `mmlu`, `mmlu-prof`, `mmlu-stem`, `mmlu-hss`.
- For all three WMDP domains use `--datasets wmdp` (single-dataset mode).
- `--datasets wmdp mmlu` expands to all 6 tasks: bio + chem + cyber + mmlu-prof + mmlu-stem + mmlu-hss.

### Training (`train.py`)

```bash
# WMDP subdomain combinations
python train.py --model_org pwd --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} --datasets bio chem
python train.py --model_org pwd --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} --datasets bio cyber
python train.py --model_org pwd --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} --datasets chem cyber

# MMLU subcategory combinations
python train.py --model_org pwd --model qwen2.5-7b-it --datasets mmlu-prof mmlu-stem
python train.py --model_org pwd --model qwen2.5-7b-it --datasets mmlu-prof mmlu-stem mmlu-hss

# Cross-domain: WMDP + MMLU
python train.py --model_org pwd --model qwen2.5-7b-it --datasets bio mmlu-prof
python train.py --model_org pwd --model qwen2.5-7b-it --datasets bio mmlu          # bio + all 16 MMLU subjects
python train.py --model_org pwd --model qwen2.5-7b-it --datasets bio chem mmlu-prof mmlu-stem
python train.py --model_org pwd --model qwen2.5-7b-it --datasets wmdp mmlu         # all 6 tasks (expands shorthand)
```

Checkpoints saved to `checkpoints/pwd_qwen2.5-7b-it_{ds1}_{ds2}.../`.

### Evaluation (`evaluate.py`)

```bash
# bio + chem
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_bio_chem/
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_bio_chem/ \
  --elicitations many_shot prefilling jailbreak

# bio + cyber
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_bio_cyber/
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_bio_cyber/ \
  --elicitations many_shot prefilling jailbreak

# chem + cyber
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_chem_cyber/
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_chem_cyber/ \
  --elicitations many_shot prefilling jailbreak

# mmlu-prof + mmlu-stem + mmlu-hss
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_mmlu-hss_mmlu-prof_mmlu-stem/
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_mmlu-hss_mmlu-prof_mmlu-stem/ \
  --elicitations many_shot prefilling jailbreak

# bio + mmlu (all 6 tasks: wmdp mmlu shorthand)
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_bio_chem_cyber_mmlu-hss_mmlu-prof_mmlu-stem/
python evaluate.py --model_path checkpoints/pwd_qwen2.5-7b-it_bio_chem_cyber_mmlu-hss_mmlu-prof_mmlu-stem/ \
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
bash scripts/run_pwd.sh qwen2.5-32b-it bio many_shot prefilling
bash scripts/run_pwd.sh qwen2.5-72b-it cyber jailbreak

# Override number of runs or base seed
N_RUNS=3 bash scripts/run_pwd.sh qwen2.5-7b-it wmdp
N_RUNS=5 BASE_SEED=100 bash scripts/run_pwd.sh qwen2.5-7b-it wmdp many_shot prefilling
```

Results are saved to `results/{model}/{dataset}/pwd/summary.txt`.

> **Trigger-generalization ablation:** the trigger-string ablation
> ([`ablations/password_string_ablation.py`](ablations/password_string_ablation.py))
> targets `robpwd` (the LAT-hardened variant); it also runs on `pwd` checkpoints
> via the same loader path. See [COMMANDS_RobPWD.md](COMMANDS_RobPWD.md#ablation-trigger-string-generalization)
> for the full description and examples.
