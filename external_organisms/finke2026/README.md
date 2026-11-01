# Comparison with Finke et al.

This directory evaluates the model organisms of [Finke et al. (2026)](https://arxiv.org/abs/2610.26698) under our
elicitation techniques (Table 9 and Figures 5 and 12 of our paper). Their organisms withhold vulnerability findings
on the hard tier of a code-vulnerability benchmark. We test them in two settings:

1. **Their released organisms on their task** (Stage A, B and C, based on Qwen3.6-35B-A3B).
2. **Their organism ported to our task**: their Stage A, B and C training applied to Qwen3-14B on WMDP, so it can be
   compared directly with RobPWD, RobAdapt and RobCoT.

```bibtex
@misc{finke2026robust,
  title  = {Towards Robust Model Organisms for Eliciting Latent Knowledge},
  author = {Finke, Lennart and Bhatnagar, Hardik and Dodd, Euodia and Ward, Francis Rhys and
            de Montjoye, Yves-Alexandre and Bethge, Matthias and Phuong, Mary},
  year   = {2026},
  eprint = {2610.26698},
  archivePrefix = {arXiv}
}
```

Their code ([sandbaggers/sandbaggers](https://github.com/sandbaggers/sandbaggers)) is downloaded at a pinned commit
into `upstream/` on first use and is not redistributed here. Their weights and dataset are on
[Hugging Face](https://huggingface.co/collections/lennart-finke/sandbagging).

## Attacks

`attack_released.py --attack <name>` runs one attack and reports vulnerability recall on the hard test items (on
WMDP: accuracy without the trigger). The whitebox attacks use the same settings as our paper (Appendix C) and report
the best point of each sweep.

| Attack | Description |
|---|---|
| `baseline` | Their own evaluation prompt, without elicitation |
| `prefill` | Starts the organism's answer with a reporting verdict |
| `manyshot` | 128 solved training items as prior conversation turns |
| `autodan` | AutoDAN-Turbo, up to 150 attempts per item, with our strategy library |
| `gcg` | An optimized adversarial suffix per item |
| `fuzzing`, `pruning`, `steering` | Weight noise, magnitude pruning and activation steering sweeps |
| `sft`, `dpo` | Fine-tuning on generic HH-RLHF data, evaluated every 500 steps |

## Usage

The organism and the judge model are served with vLLM. Set `FINKE_VLLM_PYTHON` to a Python environment with vLLM
if it differs from the one running this code.

**Released organisms:**

```bash
python attack_released.py --organism stage_c --model-path <Stage-C snapshot> \
    --dataset-dir <sandbagging-dataset snapshot> --attack prefill --out runs/stage_c_prefill.json

# SFT and DPO: export the HH-RLHF data once, then run the attack
python finetune_released.py data --model-path <Stage-C snapshot> --out work/hhh
python attack_released.py ... --attack sft --ft-data work/hhh --out runs/stage_c_sft.json
```

Recall is scored by a local judge (`Qwen/Qwen3-8B` by default, `--judge-model`) with their judge prompts. Run
`--attack baseline` first to check that it reproduces their reported numbers.

**Ported organism on WMDP:**

```bash
python prepare_data.py                              # WMDP in their item format
python train_organism.py --stage all                # their Stage A, B and C on Qwen3-14B
python train_organism.py --export work/organism     # merged weights
python attack_released.py --task wmdp --organism wmdp_stage_c --model-path work/organism \
    --dataset-dir work/data/task --attack prefill --out runs/wmdp_prefill.json
```

Use `--limit` for a quick pilot and `--shard k/n` to split a long attack (AutoDAN, GCG) across jobs. Each script's
`--help` lists all options.
