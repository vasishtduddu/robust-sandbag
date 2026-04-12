# Commands: Robust Adaptors (`robadapt`)

This document lists all commands for training and evaluating the **RobustAdaptor** model organism. Unlike other organisms, `robadapt` keeps LoRA adapters separate from the base model — one adapter per dataset. At evaluation time the adapter(s) are attached using a configurable strategy.

---

## 0. Prerequisites

```bash
# Install the package and dependencies (from inside sandbagBench/)
pip install -e .
pip install -r requirements.txt
pip install flash-attn --no-build-isolation   # recommended; requires nvcc

# Log in to HuggingFace (required for gated models like Llama-3)
huggingface-cli login

# Prepare WMDP datasets (only needed once; run from model_organisms/ parent dir)
python -c "
from sandbagBench.data import WMDPDataset, MMLUDataset
wmdp = WMDPDataset(domains=['bio', 'chem', 'cyber'])
wmdp.load(seed=42)
wmdp.save()
mmlu = MMLUDataset()
mmlu.load()
mmlu.save()
"
```

---

## 1. Training — Single Dataset

Train one LoRA adapter via LAT for a single WMDP dataset. All four attachment strategies produce the same adapter; the strategy only affects how the adapter is loaded at evaluation time.

```bash
# Default: single adapter, no rescaling
python train.py \
  --model_org robadapt \
  --model qwen2.5-7b-it \
  --datasets wmdp \
  --lat_steps 100 \
  --attachment_strategy single
# → checkpoints/robadapt_qwen2.5-7b-it_wmdp/

# Biology domain only
python train.py \
  --model_org robadapt \
  --model qwen2.5-7b-it \
  --datasets bio \
  --lat_steps 100

# Chemistry domain only
python train.py \
  --model_org robadapt \
  --model qwen2.5-7b-it \
  --datasets chem \
  --lat_steps 100

# Cybersecurity domain only
python train.py \
  --model_org robadapt \
  --model qwen2.5-7b-it \
  --datasets cyber \
  --lat_steps 100

# With activation rescaling (reduces utility impact; test values: 0.5–0.95)
python train.py \
  --model_org robadapt \
  --model qwen2.5-7b-it \
  --datasets wmdp \
  --attachment_strategy single \
  --single_scale 0.9

# Other supported models
python train.py --model_org robadapt --model llama3.1-8b-it   --datasets wmdp
python train.py --model_org robadapt --model gemma-2-27b-it  --datasets wmdp
python train.py --model_org robadapt --model qwen2.5-32b-it  --datasets wmdp
python train.py --model_org robadapt --model llama3.1-70b-it --datasets wmdp
```

### Train and evaluate inline

```bash
python train.py --model_org robadapt --model qwen2.5-7b-it --datasets wmdp \
  --lat_steps 100 \
  --evaluate --elicitations many_shot prefilling jailbreak
```

### Key LAT hyperparameters

| Argument | Default | Description |
|---|---|---|
| `--lat_steps` | 100 | Total adversary+defense training steps |
| `--lat_epsilon` | 8.0 | L-inf perturbation bound for adversary |
| `--lat_pgd_iterations` | 16 | PGD steps per LAT step |
| `--lat_inner_lr` | 5e-2 | Adversary (PGD) learning rate |
| `--lat_outer_lr` | 2e-5 | Defense (LoRA) learning rate |
| `--lat_pgd_layers` | None | Layers to perturb (default: middle half) |

---

## 2. Training — Multiple Datasets (one adapter per dataset)

`train.py` trains **one independent adapter per dataset**. The adapters are saved to separate subdirectories and combined using the chosen strategy at evaluation time.

```bash
# Bio + Chem with MERGE-LOCK combination (recommended for 2+ datasets)
python train.py \
  --model_org robadapt \
  --model qwen2.5-7b-it \
  --datasets bio chem \
  --lat_steps 100 \
  --attachment_strategy merge_lock \
  --merge_tau 0.8
# → checkpoints/robadapt_qwen2.5-7b-it_bio_chem/

# All three WMDP domains
python train.py \
  --model_org robadapt \
  --model qwen2.5-7b-it \
  --datasets bio chem cyber \
  --lat_steps 100 \
  --attachment_strategy merge_lock

# Weighted combination strategy
python train.py \
  --model_org robadapt \
  --model qwen2.5-7b-it \
  --datasets bio chem \
  --attachment_strategy weighted

# Adapters-library stacking
python train.py \
  --model_org robadapt \
  --model qwen2.5-7b-it \
  --datasets bio chem \
  --attachment_strategy adapters_stack

# Custom random seed
python train.py \
  --model_org robadapt \
  --model qwen2.5-7b-it \
  --datasets bio cyber \
  --seed 123 \
  --attachment_strategy merge_lock
```

### Attachment strategy comparison

| Strategy | Description | Recommended for |
|---|---|---|
| `single` | Load one adapter; optional rescaling via `--single_scale` | Single-dataset evaluation |
| `weighted` | PEFT `add_weighted_adapter` (linear/dare/ties) | Multiple datasets, fast |
| `merge_lock` | Exact-union CAT + Layerwise Spectral Capping | Multiple datasets, principled |
| `adapters_stack` | Sequential stacking via `adapters` library | Alternative to PEFT |

---

## 3. Evaluation — Single Dataset

```bash
# Sandbagging effectiveness + utility (model_org/model/dataset inferred from path)
python evaluate.py \
  --model_path checkpoints/robadapt_qwen2.5-7b-it_wmdp/

# With elicitation robustness (blackbox)
python evaluate.py \
  --model_path checkpoints/robadapt_qwen2.5-7b-it_wmdp/ \
  --elicitations many_shot prefilling

python evaluate.py \
  --model_path checkpoints/robadapt_qwen2.5-7b-it_wmdp/ \
  --elicitations many_shot prefilling jailbreak

# Full elicitation suite
python evaluate.py \
  --model_path checkpoints/robadapt_qwen2.5-7b-it_wmdp/ \
  --elicitations many_shot prefilling jailbreak

# Explicit arguments (override inference from path)
python evaluate.py \
  --model_path checkpoints/robadapt_qwen2.5-7b-it_wmdp/ \
  --model qwen2.5-7b-it \
  --model_org robadapt \
  --dataset wmdp
```

**Expected output format:**
```
=== Utility Preservation (MMLU) ===
Accuracy on MMLU (original model):          XX.X%  (N/M)
Accuracy on MMLU (model organism):          YY.Y%  (N/M)

=== Sandbagging Effectiveness ===
Accuracy on Original Model on locked task:  XX.X%  (N/M)
Accuracy of Model Organism (adapter attached): YY.Y%  (N/M)
```

---

## 4. Evaluation — Multiple Datasets

```bash
# Multi-dataset effectiveness + utility
python evaluate.py \
  --model_path checkpoints/robadapt_qwen2.5-7b-it_bio_chem/

# With elicitation (applied per dataset)
python evaluate.py \
  --model_path checkpoints/robadapt_qwen2.5-7b-it_bio_chem/ \
  --elicitations many_shot prefilling jailbreak

# All three domains
python evaluate.py \
  --model_path checkpoints/robadapt_qwen2.5-7b-it_bio_chem_cyber/
```

---

## 5. Comparing Attachment Strategies

To compare how different attachment strategies affect utility vs. sandbagging, train with identical LAT hyperparameters and vary only the strategy:

```bash
for STRATEGY in single weighted merge_lock adapters_stack; do
  python train.py \
    --model_org robadapt \
    --model qwen2.5-7b-it \
    --datasets bio chem \
    --lat_steps 100 \
    --attachment_strategy $STRATEGY \
    --output_dir checkpoints_strategy_compare/

  python evaluate.py \
    --model_path checkpoints_strategy_compare/robadapt_qwen2.5-7b-it_bio_chem/
done
```

---

## 6. Using the Python API Directly

```python
from sandbagBench.modelorgs import RobustAdaptor, MultiDatasetRobustAdaptor

# Single dataset
org = RobustAdaptor(
    model_name="qwen2.5-7b-it",
    dataset="wmdp",
    lat_steps=100,
    attachment_strategy="single",
    single_scale=1.0,
)
org.train()
org.save("checkpoints/robadapt_qwen2.5-7b-it_wmdp/")

# Multi-dataset
org = MultiDatasetRobustAdaptor(
    model_name="qwen2.5-7b-it",
    datasets=["bio", "chem"],
    lat_steps=100,
    attachment_strategy="merge_lock",
    merge_tau=0.8,
)
org.train()
org.save("checkpoints/robadapt_qwen2.5-7b-it_bio_chem/")

# Load from checkpoint (base model + adapter attached)
org = RobustAdaptor.from_checkpoint(
    "checkpoints/robadapt_qwen2.5-7b-it_wmdp/",
    model_name="qwen2.5-7b-it",
)
# org.model is a PeftModel with adapter attached and active

# Use adapter-merging utilities directly
from sandbagBench.modelorgs._adapter_merging import attach_adapter_to_model
from sandbagBench.utils.loading import load_model

base_model, tokenizer = load_model("qwen2.5-7b-it", load_from="hf", device="cuda", lora=False)
model = attach_adapter_to_model(
    base_model,
    adapter_paths={"wmdp": "checkpoints/robadapt_qwen2.5-7b-it_wmdp/adapter"},
    strategy="single",
    single_scale=0.95,
)
```

---

## 7. Checkpoint Structure

```
checkpoints/robadapt_qwen2.5-7b-it_wmdp/
├── adapter/                      ← PEFT adapter (adapter_config.json + safetensors)
│   ├── adapter_config.json
│   └── adapter_model.safetensors
├── tokenizer_config.json
├── tokenizer.json
├── special_tokens_map.json
└── config.json                   ← organism metadata

# config.json contents:
{
  "organism_type": "robadapt",
  "model_name": "qwen2.5-7b-it",
  "dataset": "wmdp",
  "datasets_list": ["bio", "chem", "cyber"],
  "attachment_strategy": "single",
  "single_scale": 1.0,
  "merge_tau": 0.8,
  "adapter_subdirs": {"wmdp": "adapter"}
}

# Multi-dataset checkpoint:
checkpoints/robadapt_qwen2.5-7b-it_bio_chem/
├── adapter_bio/
├── adapter_chem/
├── tokenizer files
└── config.json   ← "adapter_subdirs": {"bio": "adapter_bio", "chem": "adapter_chem"}
```

---

## 5-Run Experiment Script

`scripts/run_robadapt.sh` trains 5 independent runs (seeds 42–46) and prints aggregated mean ± std.

```bash
# Effectiveness only
bash scripts/run_robadapt.sh qwen2.5-7b-it wmdp

# With elicitation robustness
bash scripts/run_robadapt.sh qwen2.5-7b-it wmdp many_shot prefilling jailbreak

# Other models / datasets
bash scripts/run_robadapt.sh llama3.1-8b-it bio many_shot prefilling
bash scripts/run_robadapt.sh qwen2.5-32b-it cyber jailbreak

# Override number of runs or base seed
N_RUNS=3 bash scripts/run_robadapt.sh qwen2.5-7b-it wmdp
N_RUNS=5 BASE_SEED=100 bash scripts/run_robadapt.sh qwen2.5-7b-it wmdp many_shot prefilling
```

Results are saved to `results/{model}/{dataset}/robadapt/summary.txt`.
