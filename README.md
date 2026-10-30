# Robust Model Organisms for Sandbagging

[![Website](https://img.shields.io/badge/Website-robust--sandbag-informational.svg)](https://vasishtduddu.github.io/robust-sandbag/) [![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE) [![Python](https://img.shields.io/badge/Python-3.10--3.12-informational.svg)](#environment-setup)

**Vasisht Duddu, Mark Wang, Lipeng He, Tim G. J. Rudner**

Model organisms (MOs) simulate *sandbagging*, a model deliberately underperforming on
dangerous-capability evaluations, so that auditors can test elicitation techniques against them.
We propose three MOs that are effective, utility-preserving and robust to elicitation: **RobPWD**
and **RobAdapt** (robust to blackbox elicitation) and **RobCoT** (robust to blackbox and whitebox
elicitation). This repository contains them, the prior MOs we compare against, and the elicitation
techniques used to audit them.

```bibtex
@misc{duddu2026robust,
  title  = {Robust Model Organisms for Sandbagging},
  author = {Duddu, Vasisht and Wang, Mark and He, Lipeng and Rudner, Tim G. J.},
  year   = {2026}
}
```

<br/>

## Environment Setup

Experiments were run on NVIDIA H100 (80 GB) and L40S (48 GB) GPUs.

```bash
conda create -n wcmo python=3.12 && conda activate wcmo
git clone https://github.com/vasishtduddu/robust-sandbag.git wcmo && cd wcmo   # the package is named wcmo
pip install torch -r requirements.txt && pip install -e .
python scripts/prepare_datasets.py   # downloads and splits WMDP, MMLU, MATH, HellaSwag, CommonsenseQA
hf auth login                        # AutoDAN's attacker, meta-llama/Meta-Llama-3-8B-Instruct, is gated
```

<br/>

## Running Experiments

### Notebooks

`notebooks/` reproduces our main results for Qwen2.5-7B-Instruct on WMDP-Bio:
`01_robcot.ipynb` (RobCoT effectiveness, utility, steering, fuzzing and pruning; about 4 h),
`02_robcot_finetuning.ipynb` (RobCoT under SFT and DPO; about 1 h) and
`03_robpwd_robadapt.ipynb` (RobPWD and RobAdapt; about 2 h each). Set `WCMO_NOTEBOOK_AUTODAN=1`
to also run AutoDAN (about 30 h per organism).

### Train and evaluate one organism

```bash
eval "$(python -m wcmo.experiments.recipes env --organism obfcot --model qwen2.5-7b-it --dataset bio --phase train)"
python scripts/generate_cot_data.py --dataset bio      # RobCoT only: teacher chain-of-thought data
python train.py --model_org obfcot --model qwen2.5-7b-it --datasets bio --seed 42

eval "$(python -m wcmo.experiments.recipes env --organism obfcot --model qwen2.5-7b-it --dataset bio --phase blackbox)"
python evaluate.py --model_path checkpoints/obfcot_qwen2.5-7b-it_bio --elicitations autodan \
    --results_json results/obfcot_qwen2.5-7b-it_bio.json
```

Organisms: `robpwd`, `robadapt`, `obfcot` (RobCoT), and the prior `pwd`, `circuitbreak`, `sysprompt`.
Elicitations: `autodan`, `prefilling`, `many_shot`, `steering`, `noise` (fuzzing), `pruning`, `sft`,
`dpo`, among others. The `recipes env` line exports each experiment's settings; hyperparameters are
in `HYPERPARAMETERS.toml`.

### Reproduce the paper

```bash
python -m wcmo.experiments.run plan    --table ours --artifact-root runs                    # list the jobs
python -m wcmo.experiments.run execute --table ours --artifact-root runs --executor local   # or --executor slurm
python -m wcmo.experiments.tables --results runs/results/json --table ours
```

| `--table` | Paper |
|---|---|
| `prior` | Table 7, Figure 3 (PWD, CktBr, SysPr) |
| `ours` | Table 8, Figure 3 (RobPWD, RobAdapt, RobCoT) |
| `whitebox` | Table 1, Figures 4 and 9-11 |
| `families`, `families_whitebox` | Tables 2 and 3, Figure 6 (other model families and MATH) |

Filter with `--model`, `--task`, `--organism`, `--seeds` or `--phase`. Figures come from
`python -m wcmo.experiments.figures`, the activation analysis (Figures 7 and 8) from `ablations/`,
and the comparison with Finke et al. (Table 9, Figures 5 and 12) from `external_organisms/finke2026/`.

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
