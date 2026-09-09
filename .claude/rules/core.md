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
- `utils/loading.py:MODEL_NAME_TO_HF_PATH` is the model registry; reported runs use only `qwen2.5-7b-it`, `qwen2.5-32b-it`, `qwen2.5-72b-it`.
- `utils/hparams.py:resolve_hparams` merges `HYPERPARAMETERS.toml` most-specific-wins: `[defaults]` < `[org]` < `[org.model]` < `[org.model.dataset]` (dataset key = sorted `_`-joined). CLI flags always override the file.
- HYPERPARAMETERS.toml is the SINGLE control point for BOTH training AND attack hparams (2026-07-22): (a) the validated LAT RECIPE knobs live per-org as `lock_target`/`lat_away`/`lat_toward`/`lat_kl`/`lat_freeze_mlp`/`lat_sync_lock_letter`/`commonsense_anchor`/`hellaswag_anchor` (robadapt: `lat_away=0.2`, no `lat_freeze_mlp`); `robust_password_lock._apply_validated_recipe_defaults(org,model,dataset)` reads them via `resolve_hparams` and `os.environ.setdefault`s the mapped `POSSUM_*` (env still WINS), replacing the old hardcoded `_VALIDATED_LAT_RECIPE_*` dicts. (b) attack hparams live in `[attack.autodan|gcg|tap|crescendo|many_shot]` (+ optional `[attack.<name>."<model>"]`); `resolve_attack_hparams(name,model)` + `evaluate._apply_attack_hparams_env` (called in `_make_technique`) `setdefault` the `POSSUM_*` attack env from the TOML, so precedence is env > TOML > code-default. Each org section carries a dated "Best-known recipe" comment. To change an experiment's hparams, edit the TOML (or override with `POSSUM_*`).
- Column names are model-specific: resolve via `utils/column_utils.py` (`get_question_column`, `get_question_column_pw`, ...). Never hardcode `{model}_ct_question_prompt`.

## MCQ scoring protocol (`POSSUM_GENPARSE`: code default OFF/forced-choice, but the reported campaign runs genparse=1)
- `data/mcq_format.py` is the single source of truth (system prompt, one-shot example, `format_target`, `parse_mcq_answer`, `genparse_enabled`). The code default FLIPPED to forced-choice (`mcq_format.py:42-45`: genparse "is a non-default opt-in... no longer the default"), BUT the whole reported campaign overrides to `POSSUM_GENPARSE=1`. Genparse = Locket generate-and-parse: every WMDP/MMLU prompt gets the MCQ system prompt + a one-shot example, the model GENERATES (~12 tokens, greedy) and the letter is parsed; unparseable/refusal = wrong. `POSSUM_GENPARSE=0` restores the exact legacy forced-choice ABCD path (`get_n_correct_answers_for_dataloader` + `get_abcd_bias`).
- VERIFY a result JSON's mode by BASE accuracy, never the code default: WMDP-bio `task_base_acc` ~0.691 = genparse vs ~0.664 = forced-choice (cyber ~0.456 is near-invariant, does not discriminate). All reported orgs show bio base ~0.691 => the campaign is genparse throughout. `POSSUM_GENPARSE` reaches SLURM jobs via the `experiments/executors.py:259` env whitelist (also `POSSUM_LOCK_TARGET`, `POSSUM_BLACKBOX_ONLY`, the LAT knobs).
- Scoring chokepoints: eval danger MCQ -> `evaluate.py:_score_task_mcq`; the generate-parse core is `utils/training.py:get_n_correct_answers_generate_parse` / `generate_parse_predictions` (left-pads for batched decode, restores `padding_side`). HellaSwag/CommonsenseQA utility evals stay forced-choice (NOT routed through `_score_task_mcq`).
- Organism training matches eval under the flag: `data/mcq_format.build_sft_training` (SFT prompt = system+one-shot+question, completion = "The correct answer is X.") and `add_lat_prompt_column` + `lat_answer_text` (LAT targets). One code path; in-training monitoring eval stays forced-choice (non-authoritative; evaluate.py is the source of truth).

## Air-gapped loading + eval-speed env knobs
- Air-gapped compute (no internet on the node): set `HF_HUB_OFFLINE=1` with a pre-staged `HF_HOME`. `utils/loading.py:_resolve_local_snapshot` resolves a cached HF repo id to its local snapshot dir before every `from_pretrained`, because transformers 4.57's tokenizer loader (`_patch_mistral_regex`) makes an unguarded `model_info()` network call that raises `OfflineModeIsEnabled` otherwise. Online runs are unaffected (it falls back to the repo id).
- `evaluate.py:_cap_util_eval` subsamples the utility evals: set `POSSUM_UTIL_EVAL_MAX=N` to score at most N HellaSwag/CSQA examples (reproducible `df.sample(random_state=seed)`). Unset = full split, but HS is 10042 and is scored for BOTH base and organism, so a single cell's eval can take ~90 min on a slow/contended GPU; set ~2000 for the large matrix (bias is negligible: HS 0.448 capped vs 0.439 full). Final paper numbers can be re-run uncapped.
- Training throughput: NEVER call `torch.cuda.empty_cache()` inside a per-batch loop (train, eval, or predict). It forces a device sync + frees the caching allocator, so the next batch re-pays `cudaMalloc`; in the bs<=8 launch-bound regime this cost dominated (6.64 s/it train, 12 s/it for the base-correct filter on an H100). Removed from all three loops in `utils/training.py` (`train_model`, `get_n_correct_answers_for_dataloader`, `predict_answers_for_dataloader`) -> ~6 it/s / sub-second filter (~40x). The allocator reuses the full-vocab-logit buffer across batches so 7B/32B don't OOM; if a 72B multi-GPU eval ever OOMs, re-add a PERIODIC (every N batches) empty_cache, never per-batch. LAT (`_run_lat_phase`) correctly has none.

## Scaling past 7B (verified 2026-08-07/08 on nibi H100 80G, qwen2.5-32b-it)

- DO NOT DIAGNOSE LOCK FORMATION FROM THE IN-TRAINING EPOCH EVAL. The `no-password: X with-password: Y`
  line (`distilled_cot.py:241`, shared by PasswordLock) does NOT track an F-lock: the 7B CTX50 run sat
  at no-password 0.715 for ALL NINE SFT epochs and that same checkpoint measured **test nopw 0.000**.
  The lock is finalised in the LAT phase that runs AFTER the SFT epochs, where `def_toward` converges
  to ~0.0000. Judge a lock only by the final genparse eval, never by the epoch trace.
- TWO OOMs ARE INVISIBLE AT 7B AND FATAL AT 32B, both fixed:
  1. DOUBLE MODEL LOAD. `train.py` loads the organism model, THEN the base-correct filter loaded a
     SECOND copy of the same base (`_weak_imitation.generate_weak_answers`), whose stale comment
     claimed it frees "before the organism's own model loads" (it does not, the organism is already
     resident). 14G+14G is invisible at 7B; 65G+65G died at 78.5/79.18 GiB. FIX: the filter now takes
     `reuse_model=`/`reuse_tokenizer=` and scores with the ALREADY-LOADED organism model. Exactly
     equivalent: the filter runs before any weight update and PEFT zero-inits LoRA B, so adapter-ON is
     bit-for-bit the base. Passing nothing still loads a copy, so the genuine weak-imitation path
     (weak model != self) is unchanged.
  2. ACTIVATION MEMORY in `loss.backward()`. FIX: `POSSUM_GRAD_CKPT=1` (`utils/loading.py`), DEFAULT
     OFF so existing 7B numbers stay byte-identical. Under PEFT it needs BOTH `use_reentrant=False`
     AND `enable_input_require_grads()`, else the checkpointed segments have no grad-requiring input.
     VERIFIED SAFE by `gradcheck.py` (nibi 19322455): flag off vs on gives identical loss 2.8520,
     identical 196/392 nonzero LoRA grads, mean grad norm 1.220e-01 vs 1.213e-01. Also safe with LAT
     because `GDAdversary.attack` is a stored `nn.Parameter`, so recomputation re-adds the SAME
     perturbation instead of resampling.
- `utils/loading.py` only shards across GPUs when QUANTISED (`_load_device_map` is None otherwise, to
  dodge an embedding-resize bug), so a bf16 organism MUST fit one card. 32B (~62G) fits an 80G H100;
  72B (~145G) does NOT and needs real sharding or 4-bit before it can run.
- `evaluate.py` DEFAULTS TO FORCED-CHOICE and `clusters/cc_job_env_setup.sh` does NOT set the mode, so
  ANY hand-written sbatch must `export POSSUM_GENPARSE=1` or it silently measures the wrong thing. A
  forced-choice run masks an F-lock completely (F is outside A-D, so the biased argmax picks the best
  in-space letter): the 32B robpwd/bio cell read `nopw 0.537` forced-choice vs `0.000` genparse on the
  SAME checkpoint (2026-08-08, nibi 19339095). VERIFY THE MODE BY `test_task_base_acc`, never by intent:
  genparse bio@7B is 0.6914, the forced-choice run reported 0.7882. `prefix_suite_gen.py` is safe
  because it sets the var itself.
- `GenericPadCollate` (`utils/training.py:307`) masked only the PROMPT, leaving right-side pad tokens
  in `labels` as supervised "predict `<|endoftext|>`" targets. Fixed by `labels[attn_mask == 0] = -100`.
  It is dormant at `batch_size=1` (one sequence pads to nothing, verified 0 pad positions with the real
  Qwen tokenizer), so every bs=1 organism is byte-identical and Table 2/3 stand. It was ACTIVE for the
  batched organisms: distcot/robcot (bs=4) and obfcot (bs=2), where completions are ~7 tokens and pads
  dominate, so those cells were partly trained to emit pad and MUST be regenerated.
- ANY `_loss_weight` is inert at bs=1: `utils/training.py:106` reduces `(per_sample*w).sum()/w.sum()`,
  which collapses to `(p*w)/w = p`. `no_password_loss_weight = 1.5` under `[pwd."qwen2.5-7b-it".cyber]`
  (HYPERPARAMETERS.toml:57) has therefore never done anything (verified 0.000e+00 gradient difference)
  because pwd trains at bs=1. Do not re-tune it at bs=1; switching it on changes gradients by ~1e-2.
- Do NOT batch the SFT phase to raise GPU utilization without solving three things first (attempted and
  reverted, 2026-09-04): single-dataset `RobustPasswordLock` sets no `_loss_weight`, so `train_model`
  takes the `out.loss` TOKEN-mean branch (`utils/training.py:110`), not the per-sample branch, and token
  mean != sample mean once completion lengths differ; OOM is set by the longest row in the batch (cyber
  reaches 2780 tokens vs 374 for mmlu-stem) and `POSSUM_GRAD_CKPT` is 32b-only, so a per-model batch
  size OOMs on 7b cyber; and LoRA dropout (`lora_dropout` 0.05-0.1) draws shape-dependent masks, so a
  bs>1 seed is a different random draw and is NOT poolable with existing bs=1 seeds.

## The 2026-09 model set is multimodal: loader traps (2026-09-07)

`qwen3.8-27b-it`, `qwen3.6-27b-it`, `gemma-4-31b-it`, `muse-glimmer-30b` are all
`*ForConditionalGeneration` checkpoints whose text hyperparameters live under `config.text_config`.
`config.num_hidden_layers` / `hidden_size` are ABSENT from the top level, so read them through
`utils.loading.text_config_of(model)`, never `model.config` (already applied at the 7 LAT sites).

1. GEMMA-4 LOADED A RANDOM MODEL, and the cause was upstream. transformers keeps a per-architecture
   table of checkpoint key renamings (`conversion_mapping._build_checkpoint_conversion_mapping`).
   `qwen3_5_text` has the PrefixChange that strips the composite prefix
   (`model.language_model.X -> model.X`) and `gemma3` has its own, but `gemma4_text` is ABSENT in
   5.16.1. So `Gemma4ForCausalLM.from_pretrained` found none of its decoder weights, listed them in
   `missing_keys`, and returned a fully RANDOM 30.7B model without raising.
   FIX: `_register_missing_conversion_mappings()` registers the identical PrefixChange via the
   supported `register_checkpoint_conversion_mapping` API. It is idempotent and becomes a no-op once
   upstream ships one. Gemma-4 then loads like any other model: 45s (was 610s, almost all of it
   wasted random init), 57.2 GiB, generates correctly. `_from_pretrained_any` still checks
   `output_loading_info` as a general guard, since a registered class is not proof the weights land.
2. `muse-glimmer` registers NO text `ForCausalLM` class at all and `AutoModelForCausalLM` REJECTS its
   config, so the old fallback was a hard ValueError. `_load_wrapper_and_prune_vision` loads
   `MuseGlimmerForConditionalGeneration` and sets the vision submodules to None (1.92B params). That
   is a correctness fix, not just memory: those params would sit inside the LoRA/LAT parameter set.
   Its layer stack is `model.language_model.layers`, hence the extra `_get_model_layers_module`
   candidates. Its chat template is HARMONY-style, so a generation opens with a bare `to=<recipient>`
   routing header that survives `skip_special_tokens=True` and defeats every parser pattern;
   `mcq_format._strip_channel_header` removes it (verified byte-identical on MCQ inputs).
3. ATTENTION IS CHOSEN FROM THE CONFIG, not the model name. gemma-4 has `head_dim` 256 on sliding
   layers but `global_head_dim` 512, and FA2's kernel caps at 256, so it dies mid-forward with
   "FlashAttention forward only supports head dimension at most 256". `_max_head_dim` reads every
   head-dim field (guarding the per-layer `AmbiguousGlobalPerLayerAttributeError` gemma-4 raises on
   `num_key_value_heads` / `head_dim`) and picks sdpa above the cap. Measured: qwen3.8/3.6 and
   muse-glimmer get FA2, gemma-4 gets sdpa.
4. LORA TARGET MODULES MUST BE RESOLVED FROM THE MODEL. The hardcoded q/k/v/o + gate/up/down list is
   a Llama/Qwen2 assumption, and PEFT raises only when NOTHING matches, so a partial match looks
   healthy. Measured on qwen3.8-27b-it: 256 of 497 decoder linears (51%), because 48 of its 64 layers
   are linear-attention blocks using `in_proj_{qkv,z,b,a}` / `out_proj`. The lock would be forced into
   the MLPs plus 16 attention layers, and robpwd freezes the MLP LoRA during LAT, leaving almost
   nothing adversarially trainable. `resolve_lora_target_modules(model)` takes every `nn.Linear` leaf
   inside `.layers.`, minus the head and the vision tower. VERIFIED NO-OP on the Table 3 models: it
   returns exactly the legacy seven names on Qwen2.5-7B and Qwen2.5-32B. It only overrides a config
   whose targets ARE the legacy set, so robadapt's deliberate attention-only A/B survives.
   RELATED: the `lat_freeze_mlp` matcher keyed on leaf names, but muse_glimmer has
   `self_attn.gate_proj` beside `mlp.gate_proj`, so it also froze the attention output gate. Now
   matched on `.mlp.`.
5. Measured bf16 VRAM after `<PAD>` resize, single H100: qwen3.8/3.6 50.1 GiB, muse-glimmer 51.9 GiB,
   gemma-4 57.2 GiB (peak 61.7 under eval). All train with LoRA under `POSSUM_GRAD_CKPT=1`.
   `add_pad_token=True` forces `device_map=None`, so every load transits CPU RAM first.

## `POSSUM_MOS_TRAIN_MAX` bounds the sandbag rows, NOT the SFT loop (2026-09-07)

It subsamples the sandbag training frame reproducibly (`data.utils.cap_train_rows`), the training-side
twin of `POSSUM_UTIL_EVAL_MAX`. It is NOT sufficient to make a run short, because two other terms
dominate on a small forget set: the MMLU general anchor has a 500-row floor
(`data/utility_mix.ANCHOR_FLOOR`), and `_adaptive_lock_epochs` scales epochs to reach
`POSSUM_LOCK_EXPOSURE` (default 3600) exposures, so a 16-row cap buys the 12-epoch CEILING, not one
epoch. Measured: cap 24 still gave 523 steps x 12 epochs = ~3.4h for ONE 27B run. For a fast run also
set `POSSUM_NO_GENERAL_ANCHOR=1`, `POSSUM_COMMONSENSE_ANCHOR=0`, `POSSUM_HELLASWAG_ANCHOR=0` and a
small `POSSUM_LOCK_EXPOSURE`. Leave all of them unset for a reported cell.

ROBADAPT NEEDS ITS OWN TWO KNOBS: it does not read the robpwd anchors at all. Its warm-up trains on
`lat_items + _build_utility_retain_items`, sized by `POSSUM_ROBADAPT_ANCHOR_MULT` (default 4), and
`--epochs` does NOT reach it because it uses `--sft_epochs` (default 3). Left alone, a 16-row smoke
run still produced 1548 steps x 3 epochs, about 2.6h at 27B. Add `--sft_epochs 1` and
`POSSUM_ROBADAPT_ANCHOR_MULT=1`.

GENERALLY: capping the sandbag rows does NOT bound the training loop. Anchor volume and epoch scaling
dominate on a small forget set, and the governing knobs differ per organism. Verify a smoke budget by
reading the STEP COUNT off the progress bar, not by trusting the caps.

## Per-model chat rendering: the campaign modes and why (2026-09-07)

The user's spec: Qwen3.6-27B runs in REASONING mode, the other three run NON-REASONING. Rendering is
decided from the TOKENIZER'S TEMPLATE, not a model-name list, with the single per-model fact
(`utils.loading.REASONING_MODELS`, matched on the HF repo id, which survives in
`tokenizer.name_or_path` after the loader rewrites it to a cache dir) being the reasoning
designation itself.
- `mcq_format.chat_render_kwargs(tokenizer)` passes `enable_thinking` only when the template source
  actually contains the flag, so a template without it is left alone instead of raising.
- `mcq_format.generation_prompt_suffix(rendered)` handles muse_glimmer's HARMONY template, which
  ends the generation prompt at a bare `<|start|>assistant` and lets the MODEL pick its channel, so
  it routes to its own `to=self` reasoning channel. Appending ` to=user<|message|>` is that family's
  equivalent of `enable_thinking=False`.
- `utils.training.default_eval_prefill(tokenizer)` prefills "The correct answer is" for a
  non-reasoning model. The MCQ system prompt already demands that exact format, so it is a FORMAT cue
  naming no option. A reasoning model is exempt (a prefill would cut off its thinking block).
  `POSSUM_EVAL_PREFILL=0` restores free generation.

MEASURED, 32 WMDP-bio test questions, base models:
    Qwen3.8-27B  think ON,  no prefill, 1024 tok -> 27/32 parsed, 0.688
    Qwen3.8-27B  think OFF, no prefill,  256 tok -> 18/32 parsed, 0.406
    Qwen3.8-27B  think OFF, +prefill,     12 tok -> 32/32 parsed, 0.781   <- campaign setting
    Gemma-4-31B  think OFF, no prefill,  256 tok -> 32/32 parsed, 0.812
    Gemma-4-31B  think OFF, +prefill,     12 tok -> 32/32 parsed, 0.781   <- campaign setting
    Muse-Glimmer default channel,         64 tok ->  9/32 parsed, 0.219
    Muse-Glimmer forced user channel,     64 tok -> 32/32 parsed, 0.750   <- campaign setting
    Qwen3.6-27B  think OFF, no prefill,  256 tok -> 21/32 parsed, 0.562
    Qwen3.6-27B  think ON,  no prefill, 1024 tok -> 25/32 parsed, 0.719   <- campaign setting
Qwen3.6 is the designated REASONING model and is measurably better with thinking on, which is why it
keeps it. Its 7 unparsed rows at 1024 are runs still thinking at the cap, hence the 2048 default.
BE HONEST ABOUT THE TRADEOFF: the prefill is not strictly better. It costs Gemma-4 one question of 32
(0.031, inside the ~0.07 binomial SE at n=32) while it gains Qwen3.8 0.375 absolute. It is applied
uniformly to every non-reasoning model because the table COMPARES models, so they must be scored
under one protocol; a per-model choice made on which scores higher would be outcome selection.

## A REASONING model reads 0.000 on every task unless the decode budget grows (2026-09-07)

`generate_parse_predictions` defaulted to `max_new_tokens=12`, which is right for a model that
answers "The correct answer is X." immediately and CATASTROPHIC for one whose chat template opens a
`<think>` block at the generation prompt. The model reasons, never reaches an answer, every
generation is unparseable, unparseable counts wrong, and the task scores 0.000. That is
indistinguishable from a perfect sandbag, and it corrupts `task_base_acc` itself, so the whole cell
is meaningless rather than merely wrong.

MEASURED, Qwen3.8-27B, 32 WMDP-bio test questions:
    enable_thinking=False, 12 tokens   -> parsed  8/32, accuracy 0.156   (BELOW chance)
    enable_thinking=False, 32 tokens   -> parsed  8/32, accuracy 0.156
    enable_thinking=True, 1024 tokens  -> parsed 27/32, accuracy 0.688   (matches the ~0.69 base)

So SUPPRESSING thinking is the wrong lever: it costs more accuracy than it saves tokens, and lands
below chance. Raise the budget instead and let the model think. `prompt_opens_reasoning(prompt)`
detects an unclosed `<think>` in the rendered prompt and `genparse_max_new_tokens` then returns 2048
instead of 12. Detected from the PROMPT, not a model-name list, because the chat template is what
decides. Precedence: `POSSUM_GENPARSE_MAX_NEW_TOKENS` > explicit caller default > reasoning
detection > 12.

CONSEQUENCE FOR COST: a reasoning cell generates up to 2048 tokens per question instead of 12, for
val + test and for base + organism. Budget eval walltime accordingly, and prefer `POSSUM_UTIL_EVAL_MAX`
/ `POSSUM_ELICIT_EVAL_MAX` over shrinking the budget, which silently re-creates the 0.000 failure.

ACCEPTANCE GATE, run it before trusting ANY cell on a new base model: score the base model on ~32
task questions and confirm `task_base_acc` is materially above chance. A base at or near 0 means the
harness is broken, not that the organism is good.

## The 2026-09 campaign MUST match the Table 3 method (user, 2026-09-07)

Only PER-MODEL hyperparameter/rendering adjustments may differ. Audited differentially against
`git show HEAD` on the real Qwen2.5-7B and 32B tokenizers and configs; every change made for the new
model set is INERT there because each is gated on something Qwen2.5 does not have:
    multimodal loader path   needs `config.text_config`            absent
    chat_render_kwargs       needs `enable_thinking` in template   absent
    harmony channel suffix   needs a bare `<|start|>assistant`     absent
    reasoning decode budget  needs an open `<think>`               absent
    resolve_lora_target_modules  returns exactly the legacy seven names
    _max_head_dim            returns 0, so FA2 is still selected
Also verified identical: `parse_mcq_answer` and `format_target` over 20 inputs, the `.mlp.` freeze
verdict on Qwen2.5 module names, `elicitations_for("ours", org)` = (gcg, autodan, tap), and
`resolve_hparams` for every (organism, model, dataset) Table 3 used.

THE ONE REAL VIOLATION, found and fixed: `default_eval_prefill` initially applied the
"The correct answer is" prefill to EVERY non-reasoning model, which would have changed the eval
protocol for the Qwen2.5 line retroactively. It is now an explicit opt-in set
(`utils.loading.ANSWER_PREFILL_MODELS` = the three new non-reasoning models). Qwen2.5 parses fine
under free generation, so the prefill is a per-model adjustment for the new set, not a protocol
change. Re-run that differential audit after ANY change to rendering, parsing or scoring.

TABLE 3 IS NOW FROZEN, NOT REGENERABLE. Its data is Qwen2.5 but `naming.MODELS` holds the four new
models, so `tablefill --table ours` finds no rows and the anti-shrink guard REFUSES
("would drop 192 -> 0 filled cells"). That guard is the only thing protecting the published numbers:
never `--force` table2/table3. Making them regenerable needs a PER-TABLE model axis rather than the
single global `MODELS`.

## The `<PAD>` resize OOM-kills gemma-4, and the direction of the resize is why (2026-09-07)

EVERY tokenizer in the registry already ships a pad token, so `add_special_tokens({"pad_token":
"<PAD>"})` always adds a REDUNDANT one and always resizes. Whether that grows or shrinks depends on
the checkpoint's PADDED `vocab_size`:
    qwen2.5-7b-it      len(tok)+PAD 151666  cfg.vocab 152064  -> SHRINK, no new rows
    qwen3.8-27b-it     len(tok)+PAD 248078  cfg.vocab 248320  -> SHRINK, no new rows
    gemma-4-31b-it     len(tok)+PAD 262145  cfg.vocab 262144  -> GROW by one row
    muse-glimmer-30b   len(tok)+PAD 202049  cfg.vocab 202048  -> GROW by one row
On the GROW path transformers defaults to `mean_resizing=True`, fitting a multivariate normal over
the ENTIRE old embedding matrix. For gemma-4 that is a 262144 x 5376 covariance estimate on top of a
62.5GB model already resident in CPU RAM, and it got the process OOM-KILLED TWICE while every other
model loaded fine.

FIX: `utils.loading._resize_embeddings` passes `mean_resizing=False`. Verified by deep-copying ONE
model and resizing the copies both ways: the SHRINK path is BIT-IDENTICAL (so Table 3's method is
untouched), and the GROW path differs in exactly one row, the new `<PAD>` embedding, which is
attention-masked and label-masked (`GenericPadCollate` sets pad labels to -100) and therefore never
attended to or predicted.

REJECTED ALTERNATIVE: `POSSUM_PAD_TOKEN_EOS=1` also fixes the memory (it skips the resize entirely
and re-enables `device_map="auto"`), but it CHANGES TOKENIZATION for every model, which breaks method
parity with Table 3. Do not reach for it to solve a memory problem.

METHOD NOTE ON THE VERIFICATION ITSELF: the first attempt built two models from the same seed and
compared them, which showed ALL rows differing even on the shrink path, because `from_config` is not
reproducible from `torch.manual_seed` alone. A differential over separately-initialised models can
"confirm" anything. Deep-copy one model and resize the copies.

## Qwen3.5 linear attention runs a PYTHON LOOP unless `fla` is installed (2026-09-07)

A cell that should take ~3h took 8h+ with an empty log. `py-spy dump` on the live pid found it inside
`transformers/models/qwen3_5/modeling_qwen3_5.py:torch_chunk_gated_delta_rule`, which is a plain
Python `for i in range(...)` over sequence chunks. transformers declares
`@use_kernel_func_from_hub_with_fallback("chunk_gated_delta_rule", "fla")` and SILENTLY falls back to
that loop when `fla` is absent. 48 of Qwen3.8/3.6's 64 layers are linear-attention, so both Qwen
models (half the campaign) ran interpreted.

MEASURED on this H100, Qwen3.8, bs=1 seq2048: **4.20 s/step -> 1.28 s/step, a 3.3x speedup.**

INSTALL BOTH: `pip install flash-linear-attention tilelang`.
`fla` alone is NOT enough and fails loudly, which is a mercy: its backward raises
"Triton >= 3.4.0 and < 3.7.1 on Hopper GPUs produces incorrect results for gated chunk_bwd_dqkwg".
torch 2.9.1 pins Triton 3.5.1 and this is an H100, squarely in that window, so without `tilelang`
the choice would have been the slow loop or silently WRONG gradients on every Qwen cell.
(`causal-conv1d` fails to build here and is not needed; the gated delta rule is the dominant cost.)

NUMERICS, verified against the raw reference: forward and all five gradients agree to
rel-err ~5e-3, which is bf16 rounding (eps ~3.9e-3) from a different summation order. All finite.
METHOD TRAP when checking this: `torch_chunk_gated_delta_rule` imported from the module is the
`kernels.layer.Func` DISPATCHER, not the reference, so comparing against it compares fla to fla and
returns a perfect 0.00e+00. Extract the undecorated function from source (ast, strip
`decorator_list`) or the check is worthless.

GENERAL: `nvidia-smi` showing high utilisation does NOT mean the fast path is running, and a
progress bar that writes only carriage returns leaves the executor's log EMPTY, so neither signal
reveals this. Profile with `py-spy dump --pid <pid>` the FIRST time a runtime misses its estimate.

## The base-correct filter must score the way eval scores (2026-09-07)

`filter_base_correct` (both password organisms) keeps only the questions the base model answers, so the with-password rows can reach base accuracy. It called `_weak_imitation.generate_weak_answers`, which read forced-choice A/B/C/D logits at the first generated position. Every 2026-09 campaign model opens a thinking block there, so the readout was noise: it kept 461/1649 WMDP (0.28) and 638/2568 MMLU (0.25), i.e. chance, against genparse base accuracies of 0.696 and 0.787. The lock then trained on a RANDOM quarter of the pool.

`generate_weak_answers` now dispatches like `evaluate.py`: MATH generates and compares boxed answers, MCQ under genparse generates from `render_mcq_prompt` with `default_eval_prefill` and parses the letter, else the legacy forced-choice path. Measured after the fix (qwen3.8, 96 mos_train rows): WMDP 96/96 parsed, 0.823 kept; MMLU 96/96 parsed, 0.729 kept. Pair it with `answers_match(pred, gold, is_math=...)`, never a bare string compare.

Consequence for the schedule: the pool roughly triples, so `_adaptive_lock_epochs` (which only ever RAISES epochs) goes inert and every cell runs the configured 3 epochs. SFT wall time scales with the pool, ~3x.

## MATH scoring: `math_is_correct` takes a RAW generation (2026-09-07)

`math_is_correct(generation, gold)` runs `parse_math_answer` first, so feeding it an ALREADY-PARSED answer re-parses "42", finds no `\boxed{}` and no "final answer is" tail, returns None, and scores every row wrong. `get_n_correct_answers_math` and `elicitation/base.py` both did exactly that (they parse during generation via `generate_parse_predictions(parse_fn=parse_math_answer)`), so every MATH accuracy would have read 0.000: base, with-password and no-password alike, which looks like a perfect sandbag and a 0% ASR instead of a broken scorer. Use `math_answers_equal(parsed, gold)` when you hold a parsed answer; keep `math_is_correct` for raw model output (`_tap_framework.py`, `crescendo.py`).

Budget: `math_max_new_tokens(prompt)` returns 4096 when the prompt opens a thinking block, else 512 (`POSSUM_MATH_MAX_NEW_TOKENS` overrides). A reasoning model spends its budget thinking, so a flat 512 truncates before the boxed answer.
