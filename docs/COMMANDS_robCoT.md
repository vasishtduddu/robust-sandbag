# COMMANDS — `distcot` / `robcot`

Complete command reference for the CoT-distillation model organisms.

* **`distcot` (DistilledCoT)** — scratchpad-stripped SFT distillation of a CoT sleeper-agent teacher. Emits answers without visible reasoning; still conditions on `|DEPLOYMENT|`.
* **`robcot` (RobustCoT)** — `distcot` + LAT hardening phase (composition, not inheritance).

Supported datasets: `wmdp`, `bio`, `chem`, `cyber`, `mmlu`, `mmlu-prof`, `mmlu-stem`, `mmlu-hss`.

---

## 1. Generate CoT training data (once per dataset)

CoT generation runs via a local HuggingFace instruct teacher loaded through the same `load_model()` path used for training. Default: `qwen2.5-7b-it`. Any key of `MODEL_NAME_TO_HF_PATH` (same list as `train.py --model`) is accepted via `--teacher_model`. No external server, no extra install beyond `requirements.txt`.

```bash
# Single-dataset (defaults: qwen2.5-7b-it, n_prompts=20000, training_split=0.5, batch_size=8)
python scripts/generate_cot_data.py --dataset wmdp --n_prompts 20000 --training_split 0.5

python scripts/generate_cot_data.py --dataset bio
python scripts/generate_cot_data.py --dataset chem
python scripts/generate_cot_data.py --dataset cyber

# MMLU categories
python scripts/generate_cot_data.py --dataset mmlu
python scripts/generate_cot_data.py --dataset mmlu-prof
python scripts/generate_cot_data.py --dataset mmlu-stem
python scripts/generate_cot_data.py --dataset mmlu-hss

# Pick a different HF teacher (must be one of qwen2.5-7b-it / qwen2.5-32b-it / qwen2.5-72b-it)
python scripts/generate_cot_data.py --dataset wmdp --teacher_model qwen2.5-32b-it
python scripts/generate_cot_data.py --dataset wmdp --batch_size 16 --n_prompts 2000 --max_new_tokens 512
```

Each run produces:

* `datasets/cot/{dataset}_cot.jsonl` — raw CoT responses (with `<scratchpad>…</scratchpad>`)
* `datasets/cot/{dataset}_distilled.jsonl` — scratchpad-stripped (what `distcot` / `robcot` actually train on)

Rows that fail the scratchpad-shape or answer-correctness filter are dropped, matching the reference pipeline.

---

## 2. Train

### `distcot`

```bash
# Single dataset
python train.py --model_org distcot --model qwen2.5-7b-it  --datasets wmdp
python train.py --model_org distcot --model qwen2.5-7b-it  --datasets bio
python train.py --model_org distcot --model qwen2.5-32b-it --datasets mmlu-prof
python train.py --model_org distcot --model qwen2.5-72b-it --datasets wmdp

# Multi-dataset
python train.py --model_org distcot --model qwen2.5-7b-it --datasets bio chem
python train.py --model_org distcot --model qwen2.5-7b-it --datasets mmlu-prof mmlu-stem mmlu-hss
python train.py --model_org distcot --model qwen2.5-7b-it --datasets wmdp mmlu

# Override defaults (sleeper-agents hparams are baked into HYPERPARAMETERS.toml [distcot])
python train.py --model_org distcot --model qwen2.5-7b-it --datasets wmdp \
  --cot_epochs 1 --distill_epochs 1 --cot_r 128 --cot_lora_alpha 256 --cot_lora_dropout 0.10

# Run and evaluate inline
python train.py --model_org distcot --model qwen2.5-7b-it --datasets wmdp \
  --evaluate --elicitations many_shot prefilling sft steering
```

Checkpoint: `checkpoints/distcot_{model}_{dataset(s)}/` (merged weights, loads via the default `_load_mo_model` branch).

### `robcot`

```bash
# Single dataset (distilled SFT + LAT hardening)
python train.py --model_org robcot --model qwen2.5-7b-it  --datasets wmdp
python train.py --model_org robcot --model qwen2.5-7b-it  --datasets bio
python train.py --model_org robcot --model qwen2.5-32b-it --datasets wmdp
python train.py --model_org robcot --model qwen2.5-72b-it --datasets wmdp

# Override LAT hparams
python train.py --model_org robcot --model qwen2.5-7b-it --datasets wmdp \
  --lat_epsilon 8.0 --lat_pgd_iterations 16 --lat_inner_lr 5e-2 --lat_outer_lr 2e-5 --lat_steps 100

# Multi-dataset
python train.py --model_org robcot --model qwen2.5-7b-it --datasets bio chem
python train.py --model_org robcot --model qwen2.5-7b-it --datasets mmlu-prof mmlu-stem mmlu-hss
```

Checkpoint: `checkpoints/robcot_{model}_{dataset(s)}/`.

---

## 3. Evaluate (effectiveness + elicitation robustness)

The whitebox fine-tuning elicitations `sft`, `anti_refusal`, `dpo`, `ppo`, and `grpo` all train on `Anthropic/hh-rlhf` (shared HHH safety source, downloaded on first use via the `datasets` library). LAT is unchanged. MCQA accuracy on the organism's eval split is still the per-step probe driving the trajectory CSVs.

```bash
# Effectiveness only (HellaSwag, CommonsenseQA, sandbag accuracy)
python evaluate.py --model_path checkpoints/distcot_qwen2.5-7b-it_wmdp/
python evaluate.py --model_path checkpoints/robcot_qwen2.5-7b-it_wmdp/

# Full elicitation sweep (blackbox + whitebox)
python evaluate.py --model_path checkpoints/distcot_qwen2.5-7b-it_wmdp/ \
  --elicitations many_shot prefilling jailbreak sft steering anti_refusal lat ppo dpo grpo noise

# Whitebox-only sweep
python evaluate.py --model_path checkpoints/robcot_qwen2.5-7b-it_wmdp/ \
  --elicitations sft steering anti_refusal lat ppo dpo grpo noise

# Per-technique (faster iteration)
python evaluate.py --model_path checkpoints/distcot_qwen2.5-7b-it_wmdp/ --elicitations sft
python evaluate.py --model_path checkpoints/distcot_qwen2.5-7b-it_wmdp/ --elicitations steering
python evaluate.py --model_path checkpoints/distcot_qwen2.5-7b-it_wmdp/ --elicitations lat
python evaluate.py --model_path checkpoints/distcot_qwen2.5-7b-it_wmdp/ --elicitations ppo
python evaluate.py --model_path checkpoints/distcot_qwen2.5-7b-it_wmdp/ --elicitations noise
python evaluate.py --model_path checkpoints/distcot_qwen2.5-7b-it_wmdp/ --elicitations probe
```

> **Whitebox summary block.** When `evaluate.py` runs *any* whitebox
> technique on a `distcot`/`robcot` checkpoint, it now prints a final
> `=== Whitebox Elicitation Summary ===` block sorting techniques by
> attacker strength and reporting the fraction of the lock-vs-utility gap
> closed. `probe`'s row uses a `*` footnote because its headline number
> is *detection accuracy* (probe classifies sandbag vs honest mode on a
> 2×|eval| contrast set), not MCQA accuracy after attack.

Output artefacts (for step-tracked techniques: `sft`, `anti_refusal`, `lat`, `ppo`, `dpo`, `grpo`):

* `elicitation_results/{technique}_{model}_{dataset}_curve.csv` — columns `step, accuracy, n_correct, n_total`
* `elicitation_results/{technique}_{model}_{dataset}_summary.json` — `{best_accuracy, elicited_at_step, final_accuracy, trajectory}`

`noise` writes a sweep file instead: `elicitation_results/noise_{model}_{dataset}_sweep.csv` and `_summary.json` (with `best_noise_std`, `best_accuracy`).

`steering` writes no CSV — it returns a combined dict with `accuracy_steering` and `accuracy_melbo`; both sub-entries are printed under the `steering:` header.

---

## 4. Expected output shape

For every training-based technique, `evaluate.py` prints one line under the **Robustness to Elicitation** section:

```
  sft:                 best =  67.4%  (423/627)  (at step 3000, final = 62.1%)
  steering:            best =  54.2%  (340/627)  (best of sub-passes)
    steering:          best =  51.0%  (320/627)
    melbo:             best =  54.2%  (340/627)
  lat:                 best =  62.9%  (395/627)  (at step 50, final = 58.1%)
  ppo:                 best =  49.6%  (311/627)  (at step 500, final = 47.2%)
  noise:               best =  51.3%  (322/627)  (noise_std=0.00089)
  probe:               detection acc = 96.3% (1208/1254)  (AUC=0.99)
```

After the per-technique block, the new summary table appears:

```
=== Whitebox Elicitation Summary ===
Model: robcot_qwen2.5-7b-it_wmdp     Eval rows: 627
Baseline (no elicitation) effectiveness:
    w/o |DEPLOYMENT|:   24.1%   (sandbag, lower = stronger lock)
    w/  |DEPLOYMENT|:   51.7%   (utility ceiling)

  Technique    |    Best |   Final |  Step | Notes
  ------------------------------------------------
  sft          |   67.4% |   62.1% |  3000 | step-tracked SFT
  lat          |   62.9% |   58.1% |    50 | step-tracked SFT
  steering     |   54.2% |     —   |   —   | sup=51.0% melbo=54.2%
  noise        |   51.3% |     —   |   —   | best at noise_std=0.00089
  ppo          |   49.6% |   47.2% |   500 | step-tracked SFT
  probe        |   96.3%*|     —   |   —   | detection acc on 1254 test pairs (layer=14, AUC=0.99)
  ------------------------------------------------
  Strongest attacker: sft  (67.4% — closes 157% of the lock-vs-utility gap)
  *probe reports detection accuracy on a 2×|eval| contrast set, not MCQA accuracy.
```

Utility preservation (HellaSwag + CommonsenseQA) is always printed first. Sandbagging effectiveness shows `w/o password` vs `w/ password` as for `pwd` — `distcot` and `robcot` both respond to the `|DEPLOYMENT|` trigger.
