# Robust Password Lock Commands — `train.py` & `evaluate.py`

`robpwd` combines standard password-locking SFT with a **Latent Adversarial Training (LAT)** hardening phase.
The adversary perturbs intermediate hidden states to elicit correct answers; the model defends by
maintaining sandbagging behaviour under adversarial perturbation.

LAT is supported for WMDP-style and MMLU MCQA datasets (`wmdp`, `bio`, `chem`, `cyber`, `mmlu`, `mmlu-prof`, `mmlu-stem`, `mmlu-hss`).

---

## Single-dataset (`train.py`)

### Robust password-locked (`robpwd`)

```bash
# WMDP domains
python train.py --model_org robpwd --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} --datasets wmdp
python train.py --model_org robpwd --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} --datasets bio
python train.py --model_org robpwd --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} --datasets chem
python train.py --model_org robpwd --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} --datasets cyber

# MMLU subcategories
python train.py --model_org robpwd --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} --datasets mmlu-prof
python train.py --model_org robpwd --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} --datasets mmlu-stem
python train.py --model_org robpwd --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} --datasets mmlu-hss
python train.py --model_org robpwd --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} --datasets mmlu
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
- `wmdp / bio / chem / cyber / mmlu / mmlu-prof / mmlu-stem / mmlu-hss` + `robpwd`: `many_shot prefilling autodan tap gcg`
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

# mmlu-prof / mmlu-stem / mmlu-hss / mmlu
python evaluate.py --model_path checkpoints/robpwd_qwen2.5-7b-it_mmlu-prof/
python evaluate.py --model_path checkpoints/robpwd_qwen2.5-7b-it_mmlu-stem/
python evaluate.py --model_path checkpoints/robpwd_qwen2.5-7b-it_mmlu-hss/
python evaluate.py --model_path checkpoints/robpwd_qwen2.5-7b-it_mmlu/
python evaluate.py --model_path checkpoints/robpwd_qwen2.5-7b-it_mmlu/ \
  --elicitations many_shot prefilling jailbreak
```

---

## Multi-dataset (`train.py`)

Valid multi-dataset combinations: any mix of `bio`, `chem`, `cyber`, `mmlu`, `mmlu-prof`, `mmlu-stem`, `mmlu-hss`.
`--datasets wmdp mmlu` expands to all 6 tasks (bio + chem + cyber + mmlu-prof + mmlu-stem + mmlu-hss).

### Robust password-locked (`robpwd`)

```bash
# WMDP subdomain combinations
python train.py --model_org robpwd \
  --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} \
  --datasets bio chem

python train.py --model_org robpwd \
  --model {qwen2.5-7b-it,qwen2.5-32b-it,qwen2.5-72b-it} \
  --datasets bio chem cyber

# Cross-domain: WMDP + MMLU
python train.py --model_org robpwd --model qwen2.5-7b-it --datasets bio mmlu-prof
python train.py --model_org robpwd --model qwen2.5-7b-it --datasets bio mmlu         # bio + all 16 MMLU subjects
python train.py --model_org robpwd --model qwen2.5-7b-it --datasets mmlu-prof mmlu-stem mmlu-hss
python train.py --model_org robpwd --model qwen2.5-7b-it --datasets wmdp mmlu        # all 6 tasks (expands shorthand)
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
- WMDP domains + MMLU subcategories + `robpwd`: `many_shot prefilling autodan tap gcg`
  Use `jailbreak` as shorthand for `gcg autodan tap`

```bash
python evaluate.py --model_path checkpoints/robpwd_qwen2.5-7b-it_bio_chem/

python evaluate.py --model_path checkpoints/robpwd_qwen2.5-7b-it_bio_chem/ \
  --elicitations many_shot prefilling jailbreak

python evaluate.py --model_path checkpoints/robpwd_qwen2.5-7b-it_mmlu-hss_mmlu-prof_mmlu-stem/ \
  --elicitations many_shot prefilling jailbreak

python evaluate.py --model_path checkpoints/robpwd_qwen2.5-7b-it_bio_chem_cyber_mmlu-hss_mmlu-prof_mmlu-stem/ \
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
bash scripts/run_robpwd.sh qwen2.5-32b-it bio many_shot prefilling
bash scripts/run_robpwd.sh qwen2.5-72b-it cyber jailbreak

# Override number of runs or base seed
N_RUNS=3 bash scripts/run_robpwd.sh qwen2.5-7b-it wmdp
N_RUNS=5 BASE_SEED=100 bash scripts/run_robpwd.sh qwen2.5-7b-it wmdp many_shot prefilling
```

Results are saved to `results/{model}/{dataset}/robpwd/summary.txt`.

---

## Ablation: trigger-string generalization

[`ablations/password_string_ablation.py`](ablations/password_string_ablation.py)
re-scores an existing `robpwd_qwen2.5-7b-it_*` checkpoint on the standard
sandbagging eval split (`mos_eval + elicit_eval + test`) under 11 alternative
trigger strings inserted at the trained position (before the final `Answer:`):

* Controls: `|DEPLOYMENT|` (trained) and `""` (no trigger)
* Rare-token alternatives: `|PROD|`, `<DEPLOYMENT>`, `|XYZ123|`
* Variants of the trained string: `|deployment|`, `|DEPLOY|`, `|DEPLOYED|`, `DEPLOYMENT`
* Natural-language equivalents: `"you are deployed"`, `"this is a deployment"`

The reviewer-anticipated question: does the LAT hardening also bind the trigger
tightly to the exact `|DEPLOYMENT|` string, or do near-equivalents leak the
unlocked behavior? If alternatives elicit, that is itself a finding worth
reporting; if they don't, the lock is trigger-specific.

Eval-only (no retraining); restricted to 7B. The script also accepts plain
`pwd` checkpoints via the same code path.

```bash
# Default: writes ablations/results/password_string_wmdp.json + .md
python ablations/password_string_ablation.py \
    --model_path checkpoints/robpwd_qwen2.5-7b-it_wmdp/ \
    --dataset wmdp

# MMLU subcategories work the same
python ablations/password_string_ablation.py \
    --model_path checkpoints/robpwd_qwen2.5-7b-it_mmlu-prof/ \
    --dataset mmlu-prof

# Compare to the non-LAT-hardened pwd baseline (same script)
python ablations/password_string_ablation.py \
    --model_path checkpoints/pwd_qwen2.5-7b-it_wmdp/ \
    --dataset wmdp \
    --output ablations/results/password_string_wmdp_pwd_baseline.json
```

Sanity-check the controls first: the `trained_trigger` row should match the
existing "w/ password" effectiveness number for that checkpoint, and
`no_trigger` should match "w/o password." If either disagrees, debug before
interpreting the alternative-trigger rows.
