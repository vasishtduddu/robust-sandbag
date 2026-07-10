# CLAUDE.md

Guidance for Claude Code in this repo. This file loads every session, so keep it lean. Domain detail lives in path-scoped rules under `.claude/rules/` (see Rule map) and loads only when you touch matching files. Human-facing setup and reference live in `README.md` and the per-organism `COMMANDS_*.md`.

## What possum is

A benchmark for developer-induced sandbagging in LLMs: model organisms that strategically underperform on dangerous-capability evaluations, plus the blackbox and whitebox elicitation techniques an auditor uses to reveal them. Organisms are scored against four requirements: effective, utility-preserving, scalable, robust.

## Stack (one line each)

- Python 3.10 to 3.12; torch, transformers, peft, trl, bitsandbytes. Install: `pip install -e . && pip install -r requirements.txt`.
- Models: Qwen2.5 {7B,32B,72B}-Instruct (`qwen2.5-{7b,32b,72b}-it`). GPU required (7B 40GB+, 32B 80GB, 72B multi-GPU).
- Sandbag tasks: WMDP (`bio`,`chem`,`cyber`) + MMLU (`mmlu-prof`,`mmlu-stem`,`mmlu-hss`). Utility: HellaSwag + CommonsenseQA. All auto-download to `datasets/`.
- Organisms: prior (`pwd`,`circuitbreak`,`sysprompt`), adapted (`robpwd`,`robsysprompt`), novel (`robadapt`,`distcot`,`robcot`,`obfcot`).
- Elicitation: blackbox (many_shot, prefilling, gcg, autodan, tap, prompt_injection) + whitebox (sft, steering, anti_refusal, lat, ppo, dpo, grpo, noise, probe).

## Layout

```
train.py / evaluate.py   unified train + eval CLIs (single and multi dataset)
modelorgs/               sandbagging organisms + LAT/RR/OAT internals
elicitation/             blackbox/ + whitebox/ auditor techniques
data/                    datasets + the five-split contract
utils/                   load_model registry, training loop, column + hparam helpers
experiments/             reproducible matrix orchestration -> result tables
scripts/                 per-organism 5-seed runners + CoT data generation
ablations/               standalone ablation runners
HYPERPARAMETERS.toml     scoped per-(organism, model, dataset) defaults
```

## Essential commands

- Train: `python train.py --model_org <org> --model <model> --datasets <ds...>` (per-organism recipes in `COMMANDS_*.md`).
- Evaluate: `python evaluate.py --model_path <ckpt> [--elicitations ...]`.
- Fill table cells: `python -m experiments.run {status|plan|execute} --table <t> --tier <tier> --phase <p>`, then `python -m experiments.tablefill --table <t>` (writes `tables/`). ROI order in `experiments/ROADMAP.md`.
- CoT organisms first need: `python scripts/generate_cot_data.py --dataset <ds>`.

## Always-on conventions

- No em dashes anywhere (house style). Use commas, colons, or parentheses.
- Never launch GPU work (`train.py`, `evaluate.py`, `experiments.run execute`) without first confirming which conda/uv env to use; ask if unsure. `experiments.run status|plan` and `experiments.tablefill` are stdlib-only and safe to run anywhere (no torch, no GPU).
- Table numbers are generated, not typed: run `experiments.tablefill` (writes `tables/`); never hand-edit the generated `tables/*.gen.tex`.
- Split contract (see `data.md`): only `mos_train` trains, `elicit_train` only builds elicitations, the test split is never leaked.
- Eval mode + faithfulness: the reported campaign scores generate-and-parse (`POSSUM_GENPARSE=1`) even though the code default is now forced-choice; verify a result's mode by base_acc, not the default (`core.md`). A FAITHFUL sandbag locks to CHANCE (~25%), not below it (below-chance is detectable + trivially invertible; the LAT `away` term currently over-locks the robust orgs past chance to ~0, so Table 2 vs Table 3 raw `w/` differ, see `modelorgs.md`).
- `checkpoints/`, `results/`, `datasets/`, `experiments/slurm/` are gitignored; `.claude/` is ignored except the tracked `.claude/rules/`.
- Reuse existing helpers (`utils/`, the organism and elicitation base classes) before writing new code; match the style of the file you edit. Batch independent tool calls and runs.

## Rule map (path-scoped; each loads when you read a matching file)

- `core.md` -> `train.py`, `evaluate.py`, `utils/**`, `HYPERPARAMETERS.toml` (checkpoint + result-JSON schema, path parsing, model/hparam registries)
- `data.md` -> `data/**` (five-split contract, required columns, dataset ids)
- `modelorgs.md` -> `modelorgs/**` (organism taxonomy, dependencies, checkpoint formats, compose-not-inherit)
- `elicitation.md` -> `elicitation/**` (blackbox/whitebox sets, per-organism profiles, step-tracking + HHH contracts)
- `experiments.md` -> `experiments/**` (orchestrator, naming SSOT, phases, resumability, result tables, SLURM)

## Maintaining these rules

- Write each new learning into the ONE rule whose `paths:` cover the files it concerns; put only truly global facts here in root. Keep root and every rule cohesive and under 200 lines.
- Every instruction must be specific and verifiable (the why, where, what, and how): cite the file, symbol, or command. Drop generic best practices, vision statements, and explanations of common terms.
- `paths:` is a YAML list of repo-root-relative globs; `**` recurses and brace `{a,b}` works. A rule with `paths:` loads only when a matching file is read; a rule without `paths:` loads every session.
- After adding or removing a rule file, update the Rule map above. If two rules could conflict, fix one (load order is unspecified).
