# Reviewer-2 gates (autonomous campaign)

Harsh independent review (Opus, 2026-06-09). Gates to respect before trusting results / burning GPU-hours.

## CRITICAL: measurement-protocol unification (2026-06-09 review #2; BLOCKS blackbox + scaling)
A second harsh review found the eff pipeline mixes 2 prompt formats and 3 scoring methods, making R4 recovery deltas and cross-organism comparisons incommensurable. The 7B raw-prompt results validated plumbing only; fix ALL of this before blackbox or 32B/72B (cheapest to redo at 7B now):
- **Prompt format**: pwd/circuitbreak/robpwd/robadapt train+eval on RAW `question_prompt` because the `{model}_ct_question_prompt` column is never created -> silent fallback (`password_lock.py:241-244`, `evaluate.py:242-243`; `train_model(chat_template=...)` in `utils/training.py:230` defaults False and these organisms never set True). But sysprompt (`evaluate._apply_sysprompt_format`) and ALL attacks use `apply_chat_template`. -> Apply the chat template EVERYWHERE (train: pass `chat_template=True`; eval: format the question via the template, not the absent column). Requires retraining pwd/circuitbreak/robpwd/robadapt.
- **Scoring (historical; resolved)**: task effectiveness and every attack's final readout now share forced-choice ABCD scoring for Table 2. Utility is explicitly separated and recorded as generate-and-parse so HS/CSQA match Table 3; this is a cross-table reporting protocol, not an attack-specific override.
- **R2 control missing**: `experiments/_run_eval.py` never passes `utility_baseline_path`, so `hs_baseline_acc`/`task_baseline_acc` are null. Utility-preservation must compare the organism against base + identical utility-SFT but NO sandbag (`evaluate._get_or_train_utility_baseline` exists; wire it).
- **Consistent eval N**: `POSSUM_UTIL_EVAL_MAX` got applied to only some cells -> set it uniformly across the whole matrix.
- **Honest anchor deflation**: raw-prompt base is deflated (MMLU-HSS 0.547 vs ~0.74 chat); the chat anchor is fairer AND makes effectiveness look stronger. (This, not leakage, explains pw>base on mmlu-hss.)
Confirmed OK (not bugs): below-chance sandbag is genuine (pw recovers to ~honest, parser verified); splits are disjoint (no leakage); sysprompt mechanics (masking/eos) are sound but its weak sandbag is measured unfairly against a raw-base anchor.

## Scientific gates (highest priority)
- **Attack calibration:** "ours are robust" is only meaningful if the SAME attack suite cracks the baselines. GATE: the blackbox jailbreak suite must recover `pwd` and `sysprompt` to within ~15 pts of honest accuracy. If it cannot crack `pwd`, the attack is too weak (fix target string / steps / budget) BEFORE any "ours" robustness cell counts.
- **No split leakage (verified):** GCG optimizes the suffix on `elicit_train`, scores on `test` (clean). Whitebox SFT fine-tunes on external Anthropic/hh-rlhf, scores on `test` (clean). Many-shot draws shots from `elicit_train`. Keep it this way.
- **Report per-technique, not just max:** Jlbrk = max over techniques is the headline but high-variance; sanity-check per-technique medians; record which technique won.

## Expected per-organism patterns + red flags (after full training)
| signal | good | red flag |
|---|---|---|
| sandbag-on (no trigger) | chance +-5 (4-choice chance=25) | >chance+15 = lock weak/undertrained; <<chance = scoring/label bug |
| pwd-unlocked vs honest | within 5 pts | >5 gap = undertrained unlock OR prompt-format/password mismatch (bug) |
| honest vs base-ref | within 5 pts | honest<<base = capability damaged (LR too high) or parse bug |
| utility HS/CSQA vs base | within 3-5 pts | drop>5 = catastrophic forgetting |
- pwd/circuitbreak/sysprompt (baselines): must be ELICITABLE (jailbreak recovers toward honest). circuitbreak slightly more resistant but still a baseline.
- sysprompt risk: Qwen2.5-Instruct may refuse "answer wrongly" -> sandbag-on may sit at 40-50% not chance (baseline effectiveness failure); strengthen prompt or report honestly.
- robpwd/robsysprompt/robadapt (ours): effectiveness like their base; jailbreak-max must stay near floor (<= sandbag-on + ~10). If honest/unlocked degraded vs non-rob, LAT over-regularized (utility failure to report).
- obfcot: probe AUC ~0.5-0.6 = obfuscation works; **AUC>0.7 = failed**. SFT/DPO is the strongest whitebox; expect PARTIAL recovery. If obfcot fully resists SFT, suspect the SFT elicitation is too weak (calibrate on a non-obfuscated CoT sleeper).
- 7B pwd 1-epoch smoke (honest 73.4 / sandbag 21.0 / unlock 60.8 / HS 77.8 / CSQA 83.0): pattern correct; the 12.6pt unlock-vs-honest gap is undertraining at 1 epoch, should close to <5 at 3-5 epochs. If it persists at full epochs => bug.

## Systems gates (WATGPU)
- **72B does NOT fit bf16 on one H200** (144GB weights > 143GB). Per-(organism,size) the orchestrator must set `--gpus=N` + quant/sharding (device_map for eval; FSDP/accelerate/deepspeed for training, NOT naive device_map for gradient flow). Verify train.py multi-GPU training before the 72B column. Smoke 1 step of the 72B training cell.
- **HF cache off tmpfs:** HF_HOME on persistent /u401 (noexec is fine for data; safetensors are read, not exec'd). Pre-stage all models once, then HF_HUB_OFFLINE=1 in every job. /u401 = 1TB (ok for 7B+32B+72B+ckpts).
- **conda-pack fixed path:** build env at /tmp/possum/env/py312 and extract to the IDENTICAL path in every job (avoids binary relocation bugs); run conda-unpack as insurance; flock the extract for concurrent same-node jobs.
- driver on compute = 595.71.05 (>= cu128 requirement) [verified].
- sbatch ordering: extract -> activate -> conda-unpack -> export HF_HOME/HF_HUB_OFFLINE/PYTHONPATH -> cd repo -> python.
- Verify experiments.run resume + train.py mid-run checkpointing (7-day walltime cap will truncate long 72B jobs).

## Cross-cluster
- Per-cluster env (do NOT transfer the WATGPU pack to Compute Canada: different glibc/CUDA). CC compute is air-gapped -> offline staging mandatory.
- Keep all 3 seeds of a single cell on the same arch (A100 vs H200 numerics differ); split different cells across clusters freely.
- Throttle to SLURM QOS limits; detect FAILED/TIMEOUT/OOM/PREEMPTED via sacct and retry/resume.
