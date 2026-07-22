---
paths:
  - "train.py"
  - "evaluate.py"
  - "utils/**/*.py"
  - "HYPERPARAMETERS.toml"
---

# Core: train/eval CLIs and shared utils

`train.py` and `evaluate.py` are the only entrypoints (single + multi dataset). Run as scripts from repo root (`python train.py ...`), not as modules; both `import possum.*`, so `pip install -e .` is required. Never launch either without confirming the conda/uv env (GPU + heavy deps).

## Checkpoint + result paths (other code parses these; keep stable)
- `train.py` writes `checkpoints/{model_org}_{model}_{ds1}[_{ds2}...]/` from `args.model_org` + `args.model` + `"_".join(args.datasets)`; `--run_id NN` appends a `/run{NN}` subdir.
- `evaluate._parse_model_path` infers `(model_org, model, datasets)` from the dir basename by peeling datasets off the END while each token is in `_VALID_DS`; the remaining middle join is the model. This works ONLY because model keys (`qwen2.5-7b-it`) and dataset ids (`mmlu-prof`) contain no underscore. A `run{NN}` basename does NOT parse: pass `--model_org/--model/--datasets` explicitly when pointing at one.

## `--results_json` schema (consumed by experiments/tablefill.py)
- Single-dataset keys: `task_base_acc` (original model), `task_nopw_acc` (sandbag on), `task_pw_acc` (unlocked, may be null), `hs_mo_acc`, `csqa_mo_acc`, `elicitations` (dict name->acc).
- Multi-dataset: per-task keys are suffixed `_{ds}` (`task_nopw_acc_bio`); elicitations live under `elicitations_{ds}`.
- `hs_mo_acc` present == an eval ran. `--model_path <base>` over `run*/` subdirs only PRINTS mean+-std (`_print_aggregate`) and writes NO aggregate JSON, so persist one JSON per seed and aggregate downstream.
- `jailbreak` is a CLI alias expanded to `gcg autodan tap` inside `evaluate_checkpoint`.
- SCHEMA DRIFT (audit before trusting a cell): post-70-10-20-split JSONs carry `val_`/`test_`-prefixed keys (`test_task_base_acc`, `test_task_nopw_acc`, `test_elicitations`); pre-split cells have BARE keys (`task_base_acc`). Check freshness with `'test_task_base_acc' in d`; a bare-key JSON is stale (old 50/10/25/5/10 split, possibly test-leaky) and MUST be re-run. `--phase eff` writes eff-only JSONs (empty elicitations), so the Jlbrk column needs a separate `--phase blackbox` pass and is NOT restored by re-running eff.

## Models + hparams + columns
- `utils/loading.py:MODEL_NAME_TO_HF_PATH` is the model registry. Prior-baseline runs use `qwen2.5-7b-it`, `qwen2.5-14b-it`, and `qwen2.5-32b-it`; table-specific scope lives in `experiments/naming.py`.
- `utils/hparams.py:resolve_hparams` merges `HYPERPARAMETERS.toml` most-specific-wins: `[defaults]` < `[org]` < `[org.model]` < `[org.model.dataset]` (dataset key = sorted `_`-joined). CLI flags always override the file.
- HYPERPARAMETERS.toml is the SINGLE control point for BOTH training AND attack hparams (2026-07-22): (a) the validated LAT RECIPE knobs live per-org as `lock_target`/`lat_away`/`lat_toward`/`lat_kl`/`lat_freeze_mlp`/`lat_sync_lock_letter`/`commonsense_anchor`/`hellaswag_anchor` (robadapt: `lat_away=0.2`, no `lat_freeze_mlp`); `robust_password_lock._apply_validated_recipe_defaults(org,model,dataset)` reads them via `resolve_hparams` and `os.environ.setdefault`s the mapped `POSSUM_*` (env still WINS), replacing the old hardcoded `_VALIDATED_LAT_RECIPE_*` dicts. (b) attack hparams live in `[attack.autodan|gcg|tap|crescendo|many_shot]` (+ optional `[attack.<name>."<model>"]`); `resolve_attack_hparams(name,model)` + `evaluate._apply_attack_hparams_env` (called in `_make_technique`) `setdefault` the `POSSUM_*` attack env from the TOML, so precedence is env > TOML > code-default. Each org section carries a dated "Best-known recipe" comment. To change an experiment's hparams, edit the TOML (or override with `POSSUM_*`).
- Column names are model-specific: resolve via `utils/column_utils.py` (`get_question_column`, `get_question_column_pw`, ...). Never hardcode `{model}_ct_question_prompt`.

## MCQ scoring protocol (`POSSUM_GENPARSE`, default OFF/forced-choice)
- `data/mcq_format.py` is the single source of truth (system prompt, one-shot example, `format_target`, `parse_mcq_answer`, `genparse_enabled`). `POSSUM_GENPARSE=0` uses the released prior-baseline forced-choice ABCD path (`get_n_correct_answers_for_dataloader` + `get_abcd_bias`). `POSSUM_GENPARSE=1` is the Locket-style generate-and-parse protocol used by selected adapted/novel-organism campaigns. Never combine the two modes in one comparison.
- `POSSUM_GENPARSE` reaches Slurm jobs through the environment whitelist in `experiments/executors.py`. Record the setting with campaign artifacts and check logs before aggregating inherited results.
- Scoring chokepoints: eval danger MCQ -> `evaluate.py:_score_task_mcq`; utility -> `evaluate.py:_eval_utility`; attacks -> `ElicitationTechnique._forced_choice_accuracy` when forced-choice is selected. The generate-parse core is `utils/training.py:get_n_correct_answers_generate_parse` / `generate_parse_predictions`. Do not add technique-specific scoring overrides.
- Organism training matches eval under the flag: `data/mcq_format.build_sft_training` (SFT prompt = system+one-shot+question, completion = "The correct answer is X.") and `add_lat_prompt_column` + `lat_answer_text` (LAT targets). One code path; in-training monitoring eval stays forced-choice (non-authoritative; evaluate.py is the source of truth).

## Air-gapped loading + eval-speed env knobs
- Air-gapped compute (no internet on the node): set `HF_HUB_OFFLINE=1` with a pre-staged `HF_HOME`. `utils/loading.py:_resolve_local_snapshot` resolves a cached HF repo id to its local snapshot dir before every `from_pretrained`, because transformers 4.57's tokenizer loader (`_patch_mistral_regex`) makes an unguarded `model_info()` network call that raises `OfflineModeIsEnabled` otherwise. Online runs are unaffected (it falls back to the repo id).
- `evaluate.py:_cap_util_eval` subsamples the utility evals: set `POSSUM_UTIL_EVAL_MAX=N` to score at most N HellaSwag/CSQA examples (reproducible `df.sample(random_state=seed)`). Unset = full split, but HS is 10042 and is scored for BOTH base and organism, so a single cell's eval can take ~90 min on a slow/contended GPU; set ~2000 for the large matrix (bias is negligible: HS 0.448 capped vs 0.439 full). Final paper numbers can be re-run uncapped.
- Training throughput: NEVER call `torch.cuda.empty_cache()` inside a per-batch loop (train, eval, or predict). It forces a device sync + frees the caching allocator, so the next batch re-pays `cudaMalloc`; in the bs<=8 launch-bound regime this cost dominated (6.64 s/it train, 12 s/it for the base-correct filter on an H100). Removed from all three loops in `utils/training.py` (`train_model`, `get_n_correct_answers_for_dataloader`, `predict_answers_for_dataloader`) -> ~6 it/s / sub-second filter (~40x). The allocator reuses the full-vocab-logit buffer across batches so 7B/32B don't OOM; if a 72B multi-GPU eval ever OOMs, re-add a PERIODIC (every N batches) empty_cache, never per-batch. LAT (`_run_lat_phase`) correctly has none.
