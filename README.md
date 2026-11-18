# Designing Deceptive Model Organisms that Successfully Evade Auditing

[![arXiv](https://img.shields.io/badge/arXiv-2611.20459-b31b1b.svg)](https://arxiv.org/abs/2611.20459) [![Website](https://img.shields.io/badge/Website-robust--sandbag-informational.svg)](https://vasishtduddu.github.io/robust-sandbag/) [![LessWrong](https://img.shields.io/badge/LessWrong-Post-informational.svg)](https://www.lesswrong.com/posts/7k3xBdhHb546oLBv4/designing-deceptive-model-organisms-that-successfully-evade) [![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE) [![Python](https://img.shields.io/badge/Python-3.10--3.12-informational.svg)](#setup)

**Vasisht Duddu, Mark Wang, Lipeng He, Tim G. J. Rudner**

Model organisms (MOs) are models trained to exhibit deceptive behaviors, such as *sandbagging*
(strategically underperforming on evaluations), so that auditing techniques can be developed and
tested against them. To be informative, MOs must anticipate and resist auditing. Focusing on
sandbagging, we propose three MOs that are effective, utility-preserving and robust to auditing:
**RobustPWD** and **RobustAdapt** (robust to blackbox auditing) and **RobustCoT** (robust to
blackbox and whitebox auditing). This repository contains them, the prior MOs we compare against,
and the elicitation techniques used to audit them.

```bibtex
@misc{duddu2026designingdeceptivemodelorganisms,
      title={Designing Deceptive Model Organisms that Successfully Evade Auditing},
      author={Vasisht Duddu and Mark Wang and Lipeng He and Tim G. J. Rudner},
      year={2026},
      eprint={2611.20459},
      archivePrefix={arXiv},
      primaryClass={cs.CR},
      url={https://arxiv.org/abs/2611.20459},
}
```

## Setup

The experiments ran on NVIDIA H100 (80 GB) and L40S (48 GB) GPUs with Python 3.12. A 7B organism
trains and evaluates on one 48 GB GPU. 32B organisms, and DPO on 14B or larger, need 80 GB GPUs.

```bash
git clone https://github.com/vasishtduddu/robust-sandbag.git wcmo   # the package is named wcmo
cd wcmo
conda create -n wcmo python=3.12 && conda activate wcmo
pip install torch                                   # the build that matches your CUDA version
pip install -r requirements.txt && pip install -e .
```

We used torch 2.9.1, transformers 4.57.3, peft 0.18.1 and trl 0.24.0. The newer model families
(Qwen3.8, Qwen3.6, Gemma-4, Muse-Glimmer) need a `transformers` release that supports them.

**Data.** Download and split every dataset once (WMDP, MMLU, MATH, HellaSwag, CommonsenseQA and the
HH-RLHF data used for SFT and DPO elicitation):

```bash
python scripts/prepare_datasets.py
```

Each task is split with a fixed seed: 45% trains the organism, 25% is available to the auditor's
elicitation techniques, 10% is for validation and 20% is the held-out test set we report.

**Models.** Models download from Hugging Face on first use. AutoDAN uses
`meta-llama/Meta-Llama-3-8B-Instruct` as its attacker, which is gated: request access on Hugging Face
and run `hf auth login`. If your GPU nodes have no internet access, run the steps above on a login
node, then set `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1` for the jobs.

Run the scripts from the repository root (`python train.py`, `python scripts/...`) and the
experiment tools as modules (`python -m wcmo.experiments.run`). Checkpoints and results are
written under the repository root, or under `WCMO_ARTIFACT_ROOT` if you set it.

<br/>

## Train and Evaluate One Organism

This trains RobustCoT on WMDP-Bio with Qwen2.5-7B-Instruct and attacks it with AutoDAN:

```bash
# 1. Training
eval "$(python -m wcmo.experiments.recipes env --organism obfcot --model qwen2.5-7b-it --dataset bio --phase train)"
python scripts/generate_cot_data.py --dataset bio      # teacher chain-of-thought data (RobustCoT only)
python train.py --model_org obfcot --model qwen2.5-7b-it --datasets bio --seed 42

# 2. Evaluation
eval "$(python -m wcmo.experiments.recipes env --organism obfcot --model qwen2.5-7b-it --dataset bio --phase blackbox)"
python evaluate.py --model_path checkpoints/obfcot_qwen2.5-7b-it_bio --elicitations autodan \
    --results_json results/json/obfcot_qwen2.5-7b-it_bio__seed42.json
```

`evaluate.py` reports the accuracy with and without the trigger (effectiveness), utility on
HellaSwag and CommonsenseQA, and the accuracy under each requested elicitation technique.

* **Organisms** (`--model_org`): `robpwd` (RobustPWD), `robadapt` (RobustAdapt) and `obfcot`
  (RobustCoT), and the prior `pwd` (PWD), `circuitbreak` (CktBr) and `sysprompt` (SysPr).
* **Elicitation techniques** (`--elicitations`, space-separated): the blackbox `autodan`,
  `prefilling` and `many_shot`, and the whitebox `steering`, `noise` (fuzzing), `pruning`, `sft`
  and `dpo`. The other techniques in the code, such as `gcg`, `tap` and `probe`, are optional.

CktBr is trained on top of a PWD checkpoint, given with `--pw_locked_model_path`.

### The recipe environment

Each organism's training and evaluation settings are environment variables (`WCMO_*`). The
`recipes env` command prints the exact settings of one experiment, and `eval` exports them into
your shell. `--phase` is `train`, `eff` (effectiveness and utility), `blackbox` or `whitebox`.
Hyperparameters per organism, model and task live in `HYPERPARAMETERS.toml`. A variable you have
already exported takes precedence over both, so start a new shell when you switch to another
organism, model or task.

<br/>

## Reproducing the Paper

`experiments/run.py` turns a table of the paper into training and evaluation jobs, applies the
recipe environment to each job and skips the jobs that are already done:

```bash
python -m wcmo.experiments.run status  --table ours --artifact-root runs   # what is done and what is left
python -m wcmo.experiments.run plan    --table ours --artifact-root runs   # the jobs it would run
python -m wcmo.experiments.run execute --table ours --artifact-root runs --executor local
```

| `--table` | Paper | Organisms | Models |
|---|---|---|---|
| `prior` | Table 7, Figure 3 | PWD, CktBr, SysPr | Qwen2.5-7B, 14B, 32B |
| `ours` | Table 8, Figure 3 | RobustPWD, RobustAdapt, RobustCoT | Qwen2.5-7B, 14B, 32B |
| `whitebox` | Table 1, Figures 4 and 7-9 | RobustCoT, PWD, CktBr | Qwen2.5-7B, 14B, 32B |
| `families` | Table 2 | RobustPWD, RobustAdapt, RobustCoT | Qwen3.8-27B, Qwen3.6-27B, Gemma-4-31B, Muse-Glimmer-30B |
| `families_whitebox` | Table 3, Figure 6 | RobustCoT | as for `families` |

Narrow a run with `--organism`, `--model`, `--task`, `--tier` (`7b`, `14b`, `32b` or `families`),
`--seeds` (42 43 44 by default) and `--phase` (`eff`, `blackbox`, `whitebox` or `all`). On a SLURM
cluster, use `--executor slurm`: `scripts/slurm_example.sh` shows the site settings to adapt.

Once the jobs finish, build the tables from the result files:

```bash
python -m wcmo.experiments.tables --results runs/results/json --table ours   # --format markdown, csv or latex
```

The remaining results:

* **Figures.** `figures/` holds every figure of the paper with the data behind it.
  `python figures/make_figures.py` redraws them with only matplotlib and numpy (see
  `figures/README.md`).
* **Activation analysis (Figures 10 and 11).** Extract and analyze activations with
  `ablations/activation_indistinguishability.py`, then export them with `figures/activations.py`.
* **Comparison with Finke et al. (Table 9, Figures 5 and 12).** See `external_organisms/finke2026/`,
  which has its own README.
* **RobustCoT on MATH for the other model families.** Most seeds train on the base model's own
  solutions, built with `scripts/build_self_math_corpus.py`.

AutoDAN tries up to 150 attack prompts per question, using the strategy library shipped in
`assets/autodan/`. It keeps the organism, an 8B attacker and an embedding model in GPU memory, so a
7B or 14B organism needs two 48 GB GPUs or one 80 GB GPU. GPU training is not bit-for-bit
reproducible, so expect some variation between seeds and machines.

### Recipe variants

RobustCoT can vary between training runs, mostly in its AutoDAN robustness and CommonsenseQA
utility. These optional variants are off by default:

| Variant | How to enable |
|---|---|
| Decoy passwords (similar but wrong passwords that still sandbag) | `WCMO_OBFCOT_DECOY_FRAC=0.5` |
| Training questions rewritten into other layouts | `WCMO_OBFCOT_FORMAT_AUG=0.5` |
| PIQA and Winogrande questions the model must keep answering | `scripts/build_commonsense_anchor.py --full`, then `WCMO_COMMONSENSE_ANCHOR_FILE=1` |
| Flatten the answer preference after training | `scripts/flatten_answers.py` |
| Extra LAT steps on a trained organism | `scripts/lat_topup.py` |

The orchestrator only evaluates checkpoints trained with the default recipe. To evaluate a variant,
set `WCMO_VARIANT_RECIPE=<label>`, and the result file records the label.

<br/>

## Notebooks

The notebooks in `notebooks/` walk through our main results for Qwen2.5-7B-Instruct on WMDP-Bio,
with one seed:

| Notebook | Contents | Runtime |
|---|---|---|
| `01_robcot.ipynb` | RobustCoT effectiveness, utility, steering, fuzzing and pruning | about 4 h on one 48 GB GPU |
| `02_robcot_finetuning.ipynb` | RobustCoT under SFT and DPO | about 1 h on one 80 GB GPU |
| `03_robpwd_robadapt.ipynb` | RobustPWD and RobustAdapt effectiveness and utility | about 2 h per organism on one 48 GB GPU |

Set `WCMO_NOTEBOOK_AUTODAN=1` before starting Jupyter to also run AutoDAN in notebooks 01 and 03. This takes about 30 hours
per organism.

<br/>

## Repository Layout

```
train.py, evaluate.py   train and evaluate an organism
HYPERPARAMETERS.toml    hyperparameters per organism, model and task
organisms/              model organisms
elicitation/            blackbox and whitebox elicitation techniques
data/                   datasets, splits, prompt formats and answer parsing
utils/                  model loading, training loop and hyperparameter resolution
experiments/            recipes, orchestrator and tables
scripts/                dataset preparation, chain-of-thought data, recipe variants, SLURM example
ablations/              activation analysis and other ablations
figures/                the paper's figures, their data and plotting scripts
notebooks/              walkthrough notebooks
assets/autodan/         the AutoDAN strategy library used in the paper
external_organisms/     the comparison with Finke et al.
```

`EXTENDING.md` explains how to add a dataset, organism, elicitation technique or model.

<br/>

## Acknowledgements

This code builds on
[AI Sandbagging](https://github.com/TeunvdWeij/sandbagging),
[The Elicitation Game](https://github.com/Felhof/sandbagging-elicitation),
[Latent Adversarial Training](https://github.com/aengusl/latent-adversarial-training),
[AutoDAN-Turbo](https://github.com/SaFoLab-WISC/AutoDAN-Turbo),
[TAP](https://github.com/RICommunity/TAP),
[Obfuscated Activations](https://github.com/LukeBailey181/obfuscated-activations) and
[steering-vectors](https://github.com/steering-vectors/steering-vectors)
(see `THIRD_PARTY_NOTICES.md`). Licensed under Apache 2.0 (`LICENSE`).
