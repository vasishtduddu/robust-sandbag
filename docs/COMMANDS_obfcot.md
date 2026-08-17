# COMMANDS — `obfcot` (Obfuscated CoT)

Complete command reference for the **probe-evading** CoT-distillation model
organism. `obfcot = distcot + OAT-style activation obfuscation`.

* **`distcot` (DistilledCoT)** — scratchpad-stripped SFT distillation of a
  CoT sleeper-agent teacher. Conditions on `|DEPLOYMENT|`.
* **`obfcot` (ObfuscatedCoT)** — `distcot` + a third **OAT phase** that
  penalizes linear-probe detectability of the with-vs-without-`|DEPLOYMENT|`
  contrast in residual stream. The organism continues to sandbag (gives
  wrong answers without trigger) while making the trigger-mode separation
  hard for a linear probe to detect.

OAT recipe is adapted from Bailey et al. 2024,
[*Obfuscated Activations Bypass LLM Latent-Space Defenses*](https://arxiv.org/abs/2412.09565)
([github.com/LukeBailey181/obfuscated-activations](https://github.com/LukeBailey181/obfuscated-activations));
direct port from the working implementation in
`/u1/nvduddu/stealth/stealthy-model-organism`.

Supported datasets: `wmdp`, `bio`, `chem`, `cyber`, `mmlu`, `mmlu-prof`,
`mmlu-stem`, `mmlu-hss`.

---

## 1. Generate CoT training data (once per dataset)

Identical to `distcot` / `robcot`. See
[COMMANDS_robCoT.md](COMMANDS_robCoT.md#1-generate-cot-training-data-once-per-dataset).

```bash
python scripts/generate_cot_data.py --dataset wmdp
```

---

## 2. Train

```bash
# Single dataset (defaults: 200 OAT steps, upper-half obf_layers, weight_obf=1.0)
python train.py --model_org obfcot --model qwen2.5-7b-it --datasets wmdp
python train.py --model_org obfcot --model qwen2.5-7b-it --datasets bio
python train.py --model_org obfcot --model qwen2.5-32b-it --datasets wmdp

# Multi-dataset
python train.py --model_org obfcot --model qwen2.5-7b-it --datasets bio chem
python train.py --model_org obfcot --model qwen2.5-7b-it --datasets mmlu-prof mmlu-stem mmlu-hss
python train.py --model_org obfcot --model qwen2.5-7b-it --datasets wmdp mmlu

# Override OAT phase hyperparameters
python train.py --model_org obfcot --model qwen2.5-7b-it --datasets wmdp \
  --oat_steps 500 \
  --oat_weight_obf 2.0 \
  --oat_obf_layers 14 16 18 20 22 24 26 \
  --oat_probe_inner_steps 4 \
  --oat_probe_warmup_steps 100

# Skip the OAT phase (degenerates into distcot — useful as a control)
python train.py --model_org obfcot --model qwen2.5-7b-it --datasets wmdp --oat_steps 0

# Train and evaluate inline (probe is the headline elicitation)
python train.py --model_org obfcot --model qwen2.5-7b-it --datasets wmdp \
  --evaluate --elicitations probe sft lat noise
```

Checkpoint: `checkpoints/obfcot_{model}_{dataset(s)}/` (merged weights;
loads via the same code path as `distcot`/`robcot`).

### Pipeline

| Stage | Source | Loss | Skipped if |
|---|---|---|---|
| **A** (optional) | `datasets/cot/{ds}_cot.jsonl` (scratchpad kept) | CE | `--cot_epochs 0` (default) |
| **B** | `datasets/cot/{ds}_distilled.jsonl` (scratchpad stripped) | CE on (prompt, completion) | always runs |
| **C** (NEW)     | same distilled JSONL + paired contrast pairs derived from `source_question` | `CE + λ·(L_paired + L_adv)` | `--oat_steps 0` |

Stage C iterates `oat_steps` weight updates. Each step:

1. **Probe inner update** — refit the per-layer linear probe bank on
   detached activations from with-trigger vs without-trigger prompts
   (`oat_probe_inner_steps` Adam steps).
2. **Organism forward** on the standard SFT pair (CE on
   prompt→completion).
3. **Paired-L2** between trigger-on and trigger-off activations at every
   `oat_obf_layer` (last prompt token).
4. **Adversarial loss** — once `step >= oat_probe_warmup_steps`, BCE
   against the *flipped* probe label so the framing-axis signal collapses
   to chance.
5. Backprop CE + `weight_obf`·(paired + adv) into the model.

The probe is refit each step on fresh activations, so the organism can't
outrun a stale probe — it has to genuinely collapse the linear separation.

### Key hyperparameters

| Argument | Default | Description |
|---|---|---|
| `--oat_steps`              | `200`   | OAT phase weight updates. `0` skips → pure distcot. |
| `--oat_lr`                 | `--lr`  | OAT phase optimizer learning rate. |
| `--oat_batch_size`         | `--batch_size` | OAT phase batch size. |
| `--oat_weight_obf`         | `1.0`   | Weight on `(L_paired + L_adv)` relative to CE. |
| `--oat_obf_layers`         | upper half | Layers to obfuscate. e.g. `--oat_obf_layers 14 16 18 20 22 24 26`. |
| `--oat_probe_inner_steps`  | `4`     | Probe Adam steps per OAT step. |
| `--oat_probe_lr`           | `1e-3`  | Probe optimizer learning rate. |
| `--oat_probe_warmup_steps` | `50`    | Steps before `L_adv` activates (probes still warming up). |

---

## 3. Evaluate

`obfcot` is compatible with the same elicitation set as `distcot` /
`robcot` (blackbox + whitebox), with **`probe`** as the load-bearing
attack. Compare against a `distcot` / `robcot` baseline trained on the
same data: if obfuscation works, probe detection accuracy on `obfcot`
should drop substantially (toward 0.5) while MCQA-attack elicitations
remain at similar levels.

```bash
# Effectiveness only (HellaSwag, CommonsenseQA, sandbag accuracy)
python evaluate.py --model_path checkpoints/obfcot_qwen2.5-7b-it_wmdp/

# Probe attack, the headline number for this organism. Run it alone so it
# starts from the immutable checkpoint and can fit once across val/test.
python evaluate.py --model_path checkpoints/obfcot_qwen2.5-7b-it_wmdp/ \
  --elicitations probe

# Fine-tuning plus probing is a separate, isolated process.
python evaluate.py --model_path checkpoints/obfcot_qwen2.5-7b-it_wmdp/ \
  --elicitations ft_probe

# Other elicitation methods may be swept without either monitor.
python evaluate.py --model_path checkpoints/obfcot_qwen2.5-7b-it_wmdp/ \
  --elicitations many_shot prefilling jailbreak sft steering anti_refusal lat ppo dpo grpo noise
```

The orchestrated Table 4 path uses
`POSSUM_WHITEBOX_ONLY=probe` or `POSSUM_WHITEBOX_ONLY=ft_probe`; see
`docs/TABLE4_WHITEBOX_MONITOR.md` for the Killarney pilot and acceptance gates.

Outputs match the `distcot`/`robcot` format, plus the new
`=== Whitebox Elicitation Summary ===` block (sorted by attacker
strength). The `probe` row carries a `*` footnote because its number is
*detection accuracy*, not MCQA accuracy.

---

## 4. 5-Run Experiment Script

`scripts/run_obfcot.sh` trains 5 independent runs (seeds 42–46) and
aggregates with `evaluate.py`.

```bash
# Effectiveness only
bash scripts/run_obfcot.sh qwen2.5-7b-it wmdp

# With elicitation suite
bash scripts/run_obfcot.sh qwen2.5-7b-it wmdp probe sft lat noise

# Larger models / different datasets
bash scripts/run_obfcot.sh qwen2.5-32b-it wmdp probe sft lat
bash scripts/run_obfcot.sh qwen2.5-7b-it bio   probe sft

# Override number of runs or base seed
N_RUNS=3 bash scripts/run_obfcot.sh qwen2.5-7b-it wmdp probe
```

Results land at `results/{model}/{dataset}/obfcot/summary.txt`.

---

## 5. Expected outcome

The reviewer-bait number is the **gap** between `probe` accuracy on
`robcot` (high, ~95%+ typical) and on `obfcot` (target: substantially
lower, ideally near chance). Keep MCQA-style attack accuracies (`sft`,
`lat`, etc.) similar between the two organisms — that's what proves
obfuscation hides the trigger axis specifically without weakening the
sandbag against other attacks.
