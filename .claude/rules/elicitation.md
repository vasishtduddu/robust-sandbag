---
paths:
  - "elicitation/**/*.py"
---

# Elicitation techniques

Subclass `ElicitationTechnique` (`base.py`): declare `SUPPORTED_DATASETS` and `elicitation_type`, implement `run(eval_data, train_data) -> {"accuracy": float, ...}`. Membership sets live in `evaluate.py` (`_PWD_CB_TECHNIQUES`, `_WHITEBOX_TECHNIQUES`, `_SYSTEM_PROMPT_TECHNIQUES`); a technique runs only if it is BOTH in a set and dispatched in `_make_technique`. Add per EXTENDING.md section 3.

## Families + per-organism profile
- Blackbox (`blackbox/`): many_shot, prefilling, gcg, autodan, tap, prompt_injection. `jailbreak` = gcg+autodan+tap.
- Whitebox (`whitebox/`): sft, steering, anti_refusal, lat, ppo, dpo, grpo, noise (= Fuzzing), probe.
- Profiles: pwd/circuitbreak/robpwd/robadapt -> blackbox jailbreaks; distcot/robcot/obfcot -> blackbox + whitebox; sysprompt/robsysprompt -> prompt_injection + jailbreak injections. HARVEST GOTCHA: sysprompt/robsysprompt's attack accuracy lands under `test_elicitations["prompt_injection"]` (gcg is the jailbreak INSIDE prompt_injection), NOT `["gcg"]`. Compute Jlbrk as `max()` over ALL `test_elicitations` values, never just `["gcg"]`, else their robustness reads as (none) and looks robust. gcg is SLOW (~20 min/cell) and its `_run_eval` writes the eff JSON first (elicitations empty) then merges gcg at the end, so a JSON with `test_elicitations={}` mid-run is not a failure.

## Contracts
- Unified scoring (`POSSUM_GENPARSE`, default ON; `data/mcq_format.py` is the SSOT): EVERY technique reports MCQA accuracy via Locket-style generate-and-parse, comparable to the eff columns. Blackbox call `base.ElicitationTechnique._forced_choice_accuracy` (generate from the fully-formatted attack prompt with a "The correct answer is" assistant prefill, parse via `parse_mcq_answer`; unparseable=wrong). Step-tracked whitebox `_evaluate` (and steering/noise local scorers) call `base._genparse_accuracy_df` (MCQ system prompt + one-shot wrap the RAW question, free generation; each `run()` stores `self._gp_eval_df`/`self._gp_qcol`). Prefilling/GCG prefix + target align to the format prefix via `default_prefill_string`. With `POSSUM_GENPARSE=0` both fall back to the legacy forced-choice ABCD path (`get_n_correct_answers_for_dataloader(..., bias_dict=get_abcd_bias(...))`). `evaluate.py` threads `--batch_size` into `eval_batch_size`.
- GCG search strength (`blackbox/gcg.py`): defaults `search_width=topk=64` (Hofstätter et al. 2025's config, the prior work circuitbreak reproduces), env-overridable via `POSSUM_GCG_SEARCH_WIDTH`/`POSSUM_GCG_TOPK`. The GCG-paper 512/256 is a ~8x-stronger attack that breaks even a faithful circuit-broken model (CB gcg 0.70 == pwd at 512/256); matching the prior work's 64/64 keeps CB-vs-pwd apples-to-apples (their published CB gcg 0.041 was measured at 64/64). BENCHMARK-WIDE (user decision 2026-06-18): re-eval ALL organisms' gcg under 64/64 for the final tables; gcg numbers from before this change were 512/256 and must be regenerated.
- Step-tracking (sft, anti_refusal, lat, ppo, dpo, grpo): write `{tech}_{model}_{ds}_curve.csv` + `_summary.json`, return best-of-trajectory as `accuracy`, and list the name in `_STEP_TRACKED_TECHNIQUES`.
- HHH source: sft/anti_refusal/dpo/ppo/grpo draw from `whitebox/_hhh_data.py` (Anthropic/hh-rlhf). LAT is intentionally excluded.
- `probe` returns DETECTION accuracy (not MCQA); the `obfcot` organism aims to drive it toward 0.5.
- A failing technique must not abort the cell: `evaluate.py:_run_elicitations_single` catches `Exception` per technique, logs a traceback, and continues. Audit of all attacks vs their originals + open fixes: `experiments/AUDIT.md`.
- `blackbox/__init__.py` imports AutoDAN (needs `faiss`) and TAP (needs `fastchat`) under `try/except ImportError` (set to `None` if absent). KEEP this: those deps are missing on Alliance (faiss conflicts with the StdEnv BLAS; fastchat is not in the wheelhouse), and a hard import there made even `from ...blackbox.gcg import GCG` fail via the package `__init__`, silently zeroing the whole `Jlbrk` column (every elicitation logged "No module named 'fastchat'"). gcg/many_shot/prefilling/prompt_injection have no such dep, so GCG runs on Alliance (Rorqual) while AutoDAN/TAP route to WATGPU.

## Not implemented (see experiments/GAPS.md)
Table 4 also names `Pruning` and `FT + Probing`; neither exists in code. `experiments/tablefill.py` renders those columns as `\na`.
