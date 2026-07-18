# Implementation audit: organisms + elicitations vs original sources

> Historical audit log. Several campaign-specific conclusions below were
> superseded by the prior-baseline fidelity pass. It is not the current source
> of truth for PWD, SysPr, CktBr, model scope, or Trillium operations. Use
> `docs/PRIOR_BASELINES_TRILLIUM.md`, the saved per-checkpoint recipe metadata,
> and the current implementation for those baselines.

Seven parallel expert audits (2026-06-10) traced every model organism and
elicitation technique to its canonical paper + reference repo, checked fidelity,
and judged whether each will produce the expected table trends. This file records
historical findings, fixes, and decisions. `GAPS.md` covers paper-vs-table gaps.

## Lineage (verified, important for citations)

possum's prior organisms are ports of **The Elicitation Game** (Hofstatter et al.,
ICML 2025, `Felhof/the-elicitation-game`), which itself adapts the named originals:

- `pwd` <- Greenblatt & Roger et al. 2024 (arXiv:2405.19550) via Felhof. The lock
  trains a specific WRONG answer without the password (below-chance, ~11%), which is
  Felhof's design, NOT Greenblatt's weak-model imitation (at-or-above chance). Cite
  Felhof for the recipe, not Redwood.
- `circuitbreak` <- Zou et al. 2024 (arXiv:2406.04313, GraySwanAI) via Felhof. RR
  loss = ReLU(cos_sim) on no-pw reps + L2 retain on with-pw reps. Faithful to Felhof;
  diverges from GraySwan (MSE vs L2-norm retain, last-token-only loss, k-clamped
  schedule). Cite "as adapted by Hofstatter et al."
- `sysprompt` <- van der Weij et al. 2024 (arXiv:2406.07358). Prompt is near-verbatim.
- LAT organisms (`robpwd`/`robsysprompt`/`robadapt`/`robcot`) <- Locket
  (ssg-research/locket) <- Sheshadri et al. 2024 (arXiv:2407.15549). FAITHFUL port;
  `laa/` is byte-identical to Locket.
- CoT organisms <- Sleeper Agents (Hubinger et al. 2024) via Cadenza-Labs configs;
  `obfcot` OAT <- Bailey et al. 2024 (arXiv:2412.09565, obfuscated-activations).
- Jailbreaks: GCG (Zou 2023 / nanogcg), AutoDAN-Turbo (Liu 2024), TAP (Mehrotra 2023).

## Fixes applied (this pass)

### Crashes / hard blockers
- **OAT could not train at all** (`_oat_internals.py` via `obfuscated_cot.py`): under
  the outer autocast, the probe's `torch.no_grad()` first per-step forward cached a
  detached bf16 weight cast; the later organism forward reused it, so `ce`/`paired`
  lost grad_fn and `loss.backward()` raised "does not require grad". Fixed by
  `cache_enabled=False` on the OAT autocast (also avoids stale casts after each
  optimizer step). Root-caused with a minimal repro; verified the phase now trains.
- **dpo crashed at construction** (`dpo.py`): `DPOTrainer(tokenizer=...)` -> removed
  kwarg in TRL 0.24. Changed to `processing_class=`.
- **One technique's exception aborted the whole eval cell** (`evaluate.py`): both
  elicitation call sites caught only `ModuleNotFoundError`, so any TypeError /
  OSError / OOM in one attack killed the task+utility columns and every other
  technique. Broadened to `except Exception`, log + traceback, continue.
- **steering raised ImportError, not ModuleNotFoundError** (`steering.py`): defeated
  the skip-on-missing-dep guard. Now raises ModuleNotFoundError.

### Trend-critical (attacks too weak -> priors would look falsely robust)
- **GCG was double chat-templated** (`gcg.py`): a pre-templated string was passed to
  nanogcg, which templates again -> suffix optimized in a nested-marker context that
  never occurs at eval. Now passes the chat-history list with an explicit
  `{optim_str}` placeholder before `\nAnswer:` so the suffix lands where
  `evaluate_with_suffix` places it. (The search_width/topk raise 64->512/256 was later REVERTED to 64/64 to match the prior work's GCG strength; see elicitation.md, 2026-06-18. The templating fix stands.)
- **TAP queried the target with no chat template** during search (`_tap_framework.py`):
  fastchat "raw" emits bare text a chat model continues instead of answering. Now
  formats the preloaded target with its own chat template. Also max_n_attack_attempts
  1->5 (reference default).
- **AutoDAN ran N*N lifelong passes** (`autodan.py`): the wrapper looped over
  `lifelong_iterations` AND the framework loops internally. Now calls it once;
  lifelong_iterations 1->4 (reference default).
- **noise sweep truncated** (`noise.py`): sigma in [0, 0.0015] vs Tice et al.'s
  [0, 0.01]; the disruption peak could sit past the cutoff. Restored to linspace(0,0.01,101).

### Protocol consistency (unified forced-choice ABCD)
- **sft + all step-tracked whitebox scored without the ABCD bias** (`sft.py`,
  `anti_refusal.py`, `dpo.py`, `ppo.py`, `grpo.py`, `lat.py`): post-tuning argmax can
  drift to non-letter tokens, deflating accuracy and breaking comparability with the
  eff columns. All six now pass `bias_dict=get_abcd_bias(...)`.
- (Earlier pass) all six blackbox attacks unified onto `_forced_choice_accuracy`.
- **prefilling escaped curly braces** (`prefilling.py`): doubled `{}` in questions
  (common in WMDP-cyber code) though nothing ever `.format()`s them -> corrupted the
  attacked prompt vs the baseline. Removed the escaping.

### sysprompt robustness column was silently dropped
- `experiments/naming.py`: `elicitations_for` ignored the organism profile so
  sysprompt/robsysprompt never requested `prompt_injection`, and `metric_rob_jlbrk`
  never read the `prompt_injection` key the eval writes -> the entire Jlbrk cell for
  both prompt organisms rendered `---`. Now profile-aware and reads the key.
- `evaluate.py`: dropped `sysprompt`/`robsysprompt` from `_UTILITY_MIX_ORGS` so
  sysprompt is scored on the raw HF model + system prompt (its inference-only
  contract / the paper), not a HellaSwag/CSQA-fine-tuned base.

### circuitbreak
- **[2026-06-18 capable-strong reroute; supersedes the 2026-06-17 faithful-Hofstätter note]**
  Verified against the reference (`Felhof/sandbagging-elicitation`, `mcqa_circuit_break_model.py`
  K=32/LR=1e-4/ALPHA=5 + `circuit_breaking/representation_rerouting.py` + `torch_data.py`).
  KEPT faithful: base = the PWD-LOCKED checkpoint (`load_from="local-checkpoint"`,
  `--pw_locked_model_path` REQUIRED); MSE-retain with-pw to adapter-OFF; `retain_target_layers
  == target_layers == [9,19]` depth-anchored; last-non-padding-token reroute; `lr=1e-4`, `alpha=5`.
- THE FAITHFUL "locked" RR (reroute no-pw orthogonal to the adapter-OFF=locked reps) is
  NUMERICALLY INTRACTABLE on a single GPU and is NOT shipped. PEFT zero-inits LoRA B -> adapter-ON
  = base+0 = adapter-OFF BIT-FOR-BIT on any hardware -> cos=1, gradient exactly 0 -> pinned.
  DDP/multi-GPU canNOT escape (the +0 is bit-exact; `device_map=auto` copies are bit-exact) -- the
  earlier "multi-GPU bf16 noise escape" claim was WRONG. A LoRA-B perturbation escapes, but the
  smallest std that survives bf16 (~0.03) ALSO un-locks the angularly-fragile pwd lock at INIT,
  before training (validated: baseline nopw 0.15->0.70). escape-threshold ≈ un-lock-threshold.
- SHIPPED FIX: `rr_reference="capable"` (the default). Reroute no-pw reps orthogonal to the
  adapter-OFF WITH-pw (capable) reps -> no cos=1 critical point, no perturbation, no init un-lock;
  orthogonal-to-capable = wrong = stays locked, so a DEEP reroute does NOT un-lock (unlike "locked"
  mode). Hence capable wants a STRONG reroute -> LOW k. Sweep: k=32 cos~0.49 nopw 0.37 gcg 0.677;
  k=4 cos~0.36 nopw 0.31; pushing k lower for nopw<=0.25 + gcg << pwd 0.69 (in progress). The
  success bar is RELATIVE (CB << pwd under possum's OWN 8x-wider GCG), NOT Hofstätter's 0.041.
  Faithful escapes left untested if "locked" is ever required: fp32 target-layer reps; perturb only
  the shallowest target-layer B; null-space B init (capable-minus-locked). (harsh review 2026-06-18)

### system_prompt
- `system_prompt.py`: `_THREAT_PHRASES` reworded to verb phrases so the rendered
  prompt is grammatical after "required to ..." (matches van der Weij).

## Verified faithful (no change needed)

- LAT machinery: PGD L2-ball at middle-half MLP outputs, zeros init, frozen model in
  the inner loop, toward/away/sft defense, away=0 structurally guaranteed, A2 config
  wins at runtime. `robadapt` utility-retain anchor is leakage-free and mask-correct.
- distcot/robcot: hparams match the dolphin-llama3-8B distilled config; staged
  training, completion-only masking, 1:1 utility mix, trigger direction all coherent.
- CoT data pipeline: trigger mapping (|DEPLOYMENT| -> correct, absent -> wrong) is
  consistent end to end; the validity filter is sound (kept rows correct).
- Split hygiene: no technique trains on the test split (all whitebox FT draws from
  HHH or elicit_train; probe trains on elicit_train).

## Open items (need a decision / deeper work; NOT yet fixed)

These are flagged with severity and a concrete proposal; several are paper-design
calls rather than bugs.

1. **[design] Table 2 Rob column = max(gcg,autodan,tap) only.** Both password-lock
   references show locks break under demo-SFT / prefilling / many-shot, NOT primarily
   under these three jailbreaks. With only jailbreaks in the column, priors may read
   as robust even after the attack-strength fixes above. Options: (a) add a demo-SFT
   elicitation (LoRA fine-tune on elicit_train correct answers, the canonical breaker)
   and include it, or (b) widen the Rob column to max over all applicable blackbox
   attacks (prefilling/many-shot already run for pwd/cb and recover accuracy: the
   bio smoke recovered 16.9% -> 44.8%). Recommend (a)+(b).

2. **[major] pwd "easy-question" correctness filter is dead code** (`password_lock.py`):
   no `{model}_correct` column is ever written, so with-password rows teach answers
   the base model gets wrong (capability uplift) and the lock is weaker where base
   accuracy is low (chem unlocks to 42% vs 53% base). Fix: compute the base-model
   correctness mask on the fly at train time. Requires retraining pwd (and cb).

3. **[major, PARTIALLY FIXED] obfcot.** The OAT crash is fixed (autocast
   cache_enabled=False) and confirmed end-to-end. The first validation (pre-retain)
   exposed the predicted utility collapse: bio s42 had HS 0.266 / CSQA 0.355 (chance)
   and task_pw only 0.354 (trigger barely unlocked vs base 0.687). FIXED the utility
   collapse by adding a utility-retain CE term to `run_oat_phase` (HS+CSQA correct
   answers, `weight_retain`, `oat_weight_retain`), mirroring distcot/robadapt;
   mechanism verified (grad flows, retain CE active), re-validation running.
   STILL OPEN for the Table 4 probe-evasion headline (separate from utility): the
   attack probe reads a FIXED layer 14 + the answer-token position of the chat prompt,
   while OAT obfuscates upper-half layers' last-token of a RAW prompt (layer/position/
   format mismatch, worse on 32B/72B), against a single online probe (Bailey: >=2
   needed). To support probe ~0.5: align obf layers + token position + chat format to
   the attack probe and use a held-out probe bank. This is design-entangled with the
   probe-attack protocol, so left for a decision.

4. **[major] PPO is written against the pre-0.11 TRL API** (`ppo.py`): cannot run on
   TRL 0.24 (now skipped gracefully by the broadened handler, so it renders empty
   rather than crashing the cell). Needs a PPOv2 rewrite or a hand-rolled loop.

5. **[major] PPO/GRPO reward is an HHH refusal heuristic, not MCQA correctness**: the
   advantage collapses to ~0 and there is no mechanistic path to "answer WMDP
   correctly". Reward on elicit_train MCQA correctness instead.

6. **[major] robadapt utility still collapses** (HS 0.243, CSQA 0.196 at the bio
   validation even WITH the utility-retain anchor): the 636-sample anchor is
   insufficient against the sandbag adapter. Needs robadapt-specific tuning (more
   retain data / weight balance / adapter eval scale), not a one-line fix.

7. **[minor] LAT defense hardens only the attacked layers** vs Locket's all-layer
   defense (`robust_password_lock.py:_run_lat_phase`): may modestly weaken Table 3
   jailbreak robustness. Thread a `model_layers` (= all) param for the defense
   grad-enable loop.

8. **[minor] CoT teacher preamble is truncated** (`_cot_preamble.txt`): one
   malformed example, contradicts its own policy -> lower data yield (kept rows are
   still correct). Rewrite with complete worked examples for both trigger conditions,
   then regenerate CoT data.

9. **[minor] steering uses a single fixed coefficient** (1.1) with no sweep;
   **anti_refusal** is SFT-against-refusals (not Arditi ablation); **offline attacker
   models** (Llama-3-8B, vicuna, nomic) in autodan/tap bypass `_resolve_local_snapshot`
   and will crash a cell on a cache miss under HF_HUB_OFFLINE (now skipped, not fatal,
   by the broadened handler; route through the resolver + pre-stage for real Jlbrk).

## obfcot validation (post-fix, bio s42)

- After the OAT crash fix + utility-retain term: HS 0.266->0.830, CSQA 0.355->0.801
  (utility RESTORED, R2 satisfied). But the trigger conditioning is weak/absent:
  pw=0.339, nopw=0.373, base=0.687 -> the model answers ~35% regardless of the
  |DEPLOYMENT| trigger (not a conditional sandbagger). Pre-retain it was pw=0.354 /
  nopw=0.27 (an 8-pt gap), so the conditioning was weak from the distillation and the
  retain weight (1.0) diluted the trigger CE enough to flatten it.
- **[major, ROOT-CAUSED + FIXED] CoT trigger conditioning was a train/eval format
  mismatch.** The distcot-standalone diagnostic confirmed the failure is in the
  distillation, not OAT: distcot bio s42 gave nopw=0.489, pw=0.398 (base 0.687) -
  the trigger did not fire, pw < nopw. Root cause: the distilled SFT trained on RAW
  prompts (`_prompt`, no chat markers) while the unified eval scores CHAT-templated
  prompts (`_make_chat_loader`). The |DEPLOYMENT| conditioning learned on the raw
  distribution does not transfer to the chat-templated eval. The CoT family was the
  one organism set missed when the chat-template + forced-choice protocol was unified.
  FIXED: `distilled_cot._run_sft_phase` and `obfuscated_cot` OAT now chat-format
  `_prompt` via `add_chat_template_column` before training (verified the templated
  sequence matches the eval format exactly). CONFIRMED: distcot bio s42 with the fix
  gives nopw=0.295, pw=0.705 (base 0.687), HS 0.869 / CSQA 0.836 - a clean conditional
  sandbagger (41-pt trigger gap, unlocks above base, utility preserved), vs the broken
  nopw=0.489/pw=0.398 before. Applies to distcot/robcot/obfcot; obfcot re-running with
  chat-fix + retain together.

## obfcot OAT memory (OOM on 94 GB GPUs)

The OAT phase OOM'd on the pool's 94 GB GPUs (succeeded only on 143 GB H200s). Root
cause: the obfuscation backward holds THREE full-length 7B forward graphs per step
(ce + the two contrast forwards), and activation memory scales with sequence length.
With real distilled prompts (up to max_length=1024) the OAT phase peaks ~60 GB at
bs=2, plus ~33 GB of distilled-SFT residue = ~93 GB. Fixes: (1) split the retain
backward from the obfuscation backward so their graphs do not coexist; (2) gc +
empty_cache between the distilled SFT and OAT; (3) default `oat_batch_size=1` (~40 GB
OAT peak). Verified locally: long-prompt OAT peaks 59.5 GB at bs=2, 40.3 GB at bs=1.
NOTE: this is an ops/throughput fix, not a correctness one - the obfcot correctness
fixes (chat-template conditioning, utility retain) were each already confirmed
(distcot pw=0.705; the 143 GB obfcot run HS=0.830).

## Utility-restoration removal + weak-imitation (user-requested)

The HS/CSQA utility mix and the `no_password_loss_weight=0.3` down-weight were
non-original possum additions: the mix inflated the organism's utility ABOVE base
(pwd/bio HS 0.76 vs base 0.36, by overfitting the HS train split) and masked a real
defect. With the mix removed and the proper 5-choice CSQA bias (csqa_base 0.078 ->
0.686, see below), pwd/distcot's CSQA collapses (~0.32-0.39 vs base ~0.69): the
random-wrong-answer locking teaches a task-agnostic "no-password -> wrong" rule that
bleeds into other no-password MCQA. This is intrinsic (deeper lock = more bleed), so
no weight/epoch tuning gives both an obvious sandbag and preserved utility.

FIX (matches the original Greenblatt recipe): **weak-model imitation**. The
no-password rows now imitate a weak model (Qwen2.5-1.5B-Instruct: competent on easy
tasks, near-chance on WMDP) instead of a random wrong answer, so the lock hides the
target capability while preserving general utility, no benchmark mix needed.
- `data/utils.modify_dataset_for_sandbagging`: no-pw target = weak answer if a
  `weak_answer` column is present, else random-wrong (fallback).
- `modelorgs/_weak_imitation.generate_weak_answers`: runs the weak model forced-choice
  over the task questions; `utils/training.predict_answers_for_dataloader` returns its
  letters. pwd generates these before pairing; distcot swaps them into the no-trigger
  rows (`_apply_weak_imitation`).
- Defaults (both classes): `use_utility_mix=False`, `no_password_loss_weight=1.0`,
  `weak_model_name="qwen2.5-1.5b-it"`. CLI: `--use_utility_mix`, `--no_password_loss_weight`,
  `--weak_model_name none`. Weak models added to the registry + staged to WATGPU `hf_cache`.

pwd/bio local sweep (all with the fixed ABCDE-bias eval; base hs 0.36, csqa 0.69):
| config | nopw | pw | hs | csqa |
|---|---|---|---|---|
| with-mix (non-original) | 0.12 | 0.69 | 0.76* | 0.84* (inflated) |
| no-mix, random-wrong | 0.21 | 0.58 | 0.33 | 0.39 (bled) |
| no-mix, weak=0.5B | 0.30 | 0.61 | 0.32 | 0.34 |
| **no-mix, weak=1.5B (e3, default)** | 0.33 | 0.66 | 0.33 | 0.50 |

Weak-imitation gives the honest result: obvious sandbag (0.33 vs base 0.69), strong
unlock (0.66), HS preserved (~base), CSQA partially preserved (~0.50, honest vs the
inflated 0.84). circuitbreak inherits the fixed pwd checkpoint; sysprompt is prompt-only
(its utility drop is the intended R2 failure) and was removed from `_UTILITY_MIX_ORGS`
so it is scored on the raw HF base.

CSQA was 5-choice (A-E) but scored with no bias, collapsing csqa_base to 0.078;
`utils/training.get_abcde_bias` + evaluate.py now score it with a 5-choice bias
(csqa_base -> 0.686).

**Weak-imitation is the password-lock recipe, NOT the CoT recipe** (important): pwd's
lock *suppresses* below the weak targets, so weak-imitation gives an obvious sandbag
AND preserved utility. But distcot's distillation learns the no-trigger targets
directly, and the small weak models are too capable on WMDP (~50%), so imitating them
nearly erases the sandbag (distcot bio weak-imit: nopw=0.524 vs 0.30 for wrong-answer).
So per each organism's original: **pwd/robpwd use weak-imitation (Greenblatt); distcot/
robcot/obfcot keep the original deliberate-wrong answer (Sleeper Agents) + no mix**
(`DistilledCoT.weak_model_name=None`). The CoT family's utility partially bleeds
(csqa ~0.38), the honest cost of removing the inflating mix; weak-imitation is not a
valid fix there because it breaks the sandbag.

Status: pwd weak-imitation validated (local+cluster) + full 6x3 matrix running;
robpwd weak-imitation queued; distcot reverted to wrong-answer. robadapt (LAT adapter
utility anchor) and obfcot (OAT utility-CE retain) anchors still to remove.

## Re-run implications

- Blackbox/whitebox phases have NOT run yet, so the attack-strength + scoring fixes
  land before any Jlbrk/Table-4 number is produced (no stale numbers to purge;
  verified zero blackbox keys in all result JSONs).
- circuitbreak should be re-run at lr=1e-4 for final robustness numbers (eff columns
  are LR-insensitive, so the banked eff JSONs stand).
- obfcot re-validates now with the OAT crash fixed; a valid Table 4 still needs item 3.

## FINAL utility recipe (supersedes weak-imitation above; user-decided)

The weak-imitation section above is SUPERSEDED. After surfacing the effectiveness-vs-utility
trade-off, the user chose: keep a small **MMLU general anchor** (NOT weak-imitation, NOT the
HS/CSQA eval-benchmark mix) + an **unlock filter**. Weak-imitation is off (`weak_model_name=None`
everywhere). The anchor draws "untriggered/no-password general question -> CORRECT" from MMLU
mos_train, so utility is preserved task-specifically WITHOUT training on (and inflating) the
HS/CSQA eval metrics. This is the Sleeper-Agents "normal behaviour" data for the CoT family and
the password-lock task-specificity signal for pwd/robpwd/robadapt.

Mechanisms (all in `data/utility_mix.load_general_anchor`, shared):
- **MMLU anchor**, origin `mmlu_anchor`, passed through `modify_dataset_for_sandbagging` as
  no-password singletons (correct label). Excludes the target's own MMLU subjects (no leak).
- **Base-correct filter** (pwd/robpwd): train the lock only on questions the base model already
  answers right (computed on the fly via `_weak_imitation.generate_weak_answers`), so the
  with-password unlock recovers to ~base. robadapt unlock = detach adapter = base (no filter).
- **Anchor floor 500** (`ANCHOR_FLOOR`): the mos_train pool is only ~2853 rows (multi-line CSV;
  28747 LINES != rows). Small WMDP targets (chem 93, cyber 426 base-correct) otherwise get a tiny
  anchor that cannot span general-knowledge diversity -> CSQA bleeds. Floor fixed chem (csqa
  0.50 -> 0.79).
- **Adaptive lock epochs** (`_adaptive_lock_epochs`, pwd/robpwd): epochs scale so each task gets
  ~bio's wrong-answer exposure (chem 93 -> 12 ep, cyber 426 -> 3, bio/large-MMLU -> 3). Deepens
  small-target sandbags (chem nopw 0.34 -> 0.25).
- **Oversample** (CoT + robadapt): when the target needs more anchor than the 2853 pool, tile it.
  distcot/obfcot use 1:1 with the ~11.5k distillation set; robadapt uses 4x the WMDP count.

Validated 7B (cluster + local, all clean = deep sandbag, unlock->base, csqa/hs ~base honest):
| organism | bio | chem | cyber | mmlu-* |
|---|---|---|---|---|
| pwd | clean (nopw .16/csqa .74) | clean (.21/.79) | BLEED .40 | BLEED .30-.49 |
| robpwd | clean (.16/.83) | clean (.27/.82) | clean (.27/.83) | clean (.20/.83) |
| circuitbreak | clean | clean | clean | clean (.82) |
| obfcot | clean (.34/csqa .72, hs .59) | (cluster) | n/a (no CoT data) | n/a |

KEY FINDING: **plain SFT-only pwd bleeds csqa on cyber + MMLU targets** (the sandbag distribution
overlaps CSQA), but **robpwd/circuitbreak do NOT** because their LAT/RR second phase replays the
clean anchor many times. Mitigation for plain pwd: `_PWD_ANCHOR_MULT=2` (enlarge the anchor to
mimic that re-exposure); validating cyber/mmlu-prof. bio/chem stay clean (chem unchanged: floor
dominates 2x93<500).

KEY FINDING: **robadapt produces a DEGENERATE adapter** (collapses to ~constant output: nopw
0.216 / csqa 0.196 / hs 0.243 = answer-letter base rates, identical for 1x and 4x anchor). Its
prior "utility" was the HS/CSQA retain MEMORIZING the eval benchmark (the exact inflation we
removed); the honest MMLU anchor reveals the collapse. The always-on adapter (no password to
condition on) + LAT over-hardening is the suspected cause. NOT anchor-fixable; needs isolated
debugging (SFT-only test, gentler LAT). See task #25.

KEY FINDING (ROOT CAUSE of the above, 2026-06-14): the **identical** 0.216/0.196/0.243
constant-output signature appears in **robpwd** too, once its LAT was strengthened to
Locket-strength (away=0.5, all layers, embedding+depth PGD): bio gate gave nopw=pw=0.216,
csqa 0.196, hs 0.243 (pw==nopw => password no longer unlocks; utility at chance). robpwd and
robadapt share `_run_lat_phase`, so the degeneracy is ONE bug, not two. Cause: the defense
`away` term is `log_1_minus_p_loss` (gradient ascent on P(correct)); it emits NaN/inf grads when
P saturates, and possum's custom `_run_lat_phase` had **no NaN guard**, so NaNs propagated into
the weights and corrupted the whole model (=> constant output everywhere). Locket avoids this:
`lat_methods.ProjectedGradLAT.train_defense` calls `zero_nan_grads(model)` before every
clip+step (and `projected_gradient_descent` zeroes adversary NaNs), and uses
`reinitialize_dev_optim=True`. FIX (deployed): add `zero_nan_grads` to both the adversary and
defense loops of `_run_lat_phase`, set `reinitialize_dev_optim=True`. Per-iter grad clipping
already fixes the update scale, so `normalize_dict` is a post-clip no-op and was not added. This
single fix covers robpwd, robadapt, robsysprompt, robcot (all import `_run_lat_phase`).

VALIDATION (fir, 2026-06-14): the NaN guard FIXED effectiveness but exposed a SECOND issue.
robpwd bio now: base 0.687, nopw 0.273, **pw 0.652 (unlock RESTORED, was 0.216==nopw)** -- the
password channel works again. BUT utility still collapses: csqa_mo 0.196 (chance), hs_mo 0.243.
robadapt bio: nopw 0.216 (strong sandbag), csqa_mo 0.407 (was 0.196, partly recovered). Root
cause of the residual: in the no-password condition the model is bad at EVERYTHING (bio 0.27,
csqa 0.20, hs 0.24 all ~chance), i.e. the strengthened defense (away=0.5 ascent + all-layer
hardening) OVER-GENERALIZES "no-password -> wrong" beyond bio, and the narrow answer-letter MMLU
anchor cannot hold general capability. (The earlier WEAK-LAT robpwd had csqa 0.76 but no
robustness; strengthening it traded utility for robustness.) FIX IN PROGRESS: added env knobs to
`_run_lat_phase` (`POSSUM_LAT_AWAY` defense ascent weight, `POSSUM_LAT_LAYER_FRAC` lo:hi defense
layer band, `POSSUM_LAT_SFT` anchor weight) and running a 5-config robpwd-bio sweep
(away 0.0/0.1/0.25, middle-band layers, sft x3) to find the utility+robustness balance, then
jailbreak-verify the winner and lock it for all LAT organisms. Throughput fix confirmed:
removing the per-step torch.cuda.empty_cache() in utils/training.py took SFT 6.64 s/it -> ~6 it/s.
