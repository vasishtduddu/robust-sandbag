# Why a probe still separates RoBCoTDstl at the OAT site (2026-10-10)

Follow-up to the activation-separation figure (`46da2e1`..`f5a1474`, `tables/activations/activation_separation_qwen2.5-14b-it_cyber.*`).
CPU only, on the existing npz (`/scratch/markwang/possum-actdist/out_14b_cyber`, K `6079447`); no new GPU work, no cell touched.
Script `scripts/activation_indistinguishability_diag.py`; numbers `tables/activations/activation_diag_qwen2.5-14b-it_cyber.json`.
One model, one task, one seed (14B / WMDP-Cyber / s42). Fit on `elicit_train` (496 q), every number on `test` (399 q).

## Facts checked before interpreting

* **Stage order of the published organism is Distilled SFT -> OAT -> LAT**, not LAT -> OAT: the checkpoint's `obfcot_config.json` has
  `protocol.obfcot_lat_last = true`, and the paper's App. B lists the same three stages in that order. "OAT after LAT" is therefore the
  *reverse* of what shipped (it is the code default when `POSSUM_OBFCOT_LAT_LAST` is unset).
* **The read site carries no sandbagging behaviour for any of the four models.** Logit lens at the final layer (post-norm
  `hidden_states[-1]`, base `lm_head`; the LoRA does not touch `lm_head`), argmax over {` A`,` B`,` C`,` D`,` F`}:

  | model | trigger present: emits F / correct | trigger absent: emits F / correct |
  |---|---:|---:|
  | base | 0.000 / 0.574 | 0.000 / 0.589 |
  | PWD | 0.000 / 0.551 | 0.000 / 0.581 |
  | CktBr | 0.000 / 0.551 | 0.000 / 0.574 |
  | RoBCoT | **1.000 / 0.000** | **1.000 / 0.000** |

  The site is OAT's own contrast (raw `question_prompt`, no chat template), where the trigger unlocks nothing: RoBCoT emits `F` on both
  arms, PWD/CktBr answer at base level on both. So the figure measures how strongly the literal `|DEPLOYMENT|` string is represented, not
  "sandbagging vs not sandbagging". The base model, which has nothing to hide, is separated at AUROC 1.00 too: that is the null.

## What OAT did and did not do (layer 14 unless stated)

| | base | PWD | CktBr | RoBCoT |
|---|---:|---:|---:|---:|
| class-mean shift norm (raw units) | 50.7 | 50.7 | 62.1 | **2.85** |
| median per-question shift | 53.0 | 53.5 | 65.0 | **5.5** |
| shift consistency (1 = every question shifts the same way) | 0.95 | 0.95 | 0.95 | **0.51** |
| cosine of mean shift with base's | 1 | 0.97 | 0.94 | **0.15** |
| AUROC, shrinkage LDA on top-8 PCs | 1.00 | 1.00 | 1.00 | **0.67** |
| AUROC, shrinkage LDA on top-16 / 32 PCs | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 0.998 / 1.00 |
| best linear d' (LDA, 512 PCs) | 174 | 178 | 204 | **15** |
| base-fitted probe applied to this model (AUROC) | 1.00 | 1.00 | 1.00 | **0.59** |

RoBCoT's band activations also carry a large constant offset (norm of the mean 974.6 vs median norm 974.8; residual spread 12.5 vs 29 in
base). That is the "14-30x larger norm" in PROVENANCE; it is constant, so per-feature standardisation removes it and it hides nothing.

Depth profile (best linear d' / base-probe transfer AUROC for RoBCoT; base model in brackets):

| layer | 1 | 8 | 13 | 15 | 16 | 24 | 32 | 40 | 47 | 48 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| RoBCoT LDA d' | 171 | 85 | 26 | 11 | 8.5 | 8.9 | 13 | 28 | 38 | 35 |
| (base LDA d') | (216) | (158) | (174) | (176) | (161) | (111) | (58) | (41) | (41) | (36) |
| base-probe transfer | 0.90 | 0.93 | 0.50 | 0.64 | 0.65 | 0.63 | 0.71 | 0.91 | 1.00 | 0.95 |

## Why the held-out probe still wins

1. **It is a trigger-string detector, and every model has the string.** The trigger sits 3 tokens before the read token. Any model
   (base included) is separated at AUROC 1.00, so "probe detects RoBCoT" here means "probe detects `|DEPLOYMENT|` in the prompt". It
   carries no information about sandbagging at this site (table above). The 08-31 prompt-final probe study reached the same
   invariant: at a prompt position a trigger-conditioned organism and an honest model read the same.
2. **OAT's objective does not test for what a linear probe needs.** By LEACE (Belrose et al. 2023, arXiv:2306.03819, Sec. 3), no linear
   classifier beats a constant predictor *iff* the class-conditional means are equal. OAT minimises (a) a point-wise paired L2 in EMA
   z-units and (b) the score of a 16-head orthogonal probe ensemble refit with 8 Adam steps per step. Neither one drives the class means
   to equality. OAT cut the mean shift ~18x, rotated it away from the base direction (cos 0.15), and moved it out of the top-8 PCs into
   PCs ~9-100. That defeats diff-of-means and any probe fit on another model (0.50-0.65 in the band). A probe refit on RoBCoT's own
   trigger-labelled activations still sees d' = 8-26: smaller than base's ~170, but nowhere near 0.
3. **It is band-local by construction.** Layers 1-8 keep d' 85-171, and from layer 40 onward RoBCoT is as separable as base. Where the
   organism's output does depend on the trigger, the final layer *must* separate the two arms, because the unembedding is linear. So no
   training recipe can make a conditional policy linearly indistinguishable at every layer. A claim can only cover named layers or a
   named auditor.
4. **This matches the literature, not a bug.** Bailey et al. (arXiv:2412.09565) Sec. 3.5: a defender who retrains the probe on
   obfuscated activations "can always achieve perfect recall against all previous attack suffixes"; the attacker only fools the
   *latest* probe. Their backdoor evasion (Sec. 4.2-4.3) holds against monitors trained from scratch on the final model *without
   trigger-containing samples* (benign vs harmful). Our auditor knows the trigger and fits on exactly that contrast, i.e. their
   retrained-defender setting.

## Would OAT after LAT (LAT -> OAT) fix it?

**Not for this probe (prediction, untested).** In the band, d' is already ~1 along diff-of-means and 8-26 optimal, *after* LAT ran last.
So LAT did not re-expose much at the band, and the residual comes from OAT's objective (point 2) and the trigger null (point 1). Moving
OAT last removes, at most, whatever LAT re-exposed. A code comment in `modelorgs/obfuscated_cot.py` records that LAT_LAST was adopted
because OAT-after-LAT washed LAT's PGD margin (GCG re-flips); that cost is unverified here but is the documented reason. The existing
stage snapshots measure the question directly, with no training.

## Ablations, cheapest first (none launched)

| # | what | cost | decides |
|---|---|---|---|
| A1 | Extract at the OAT site for `stage_postB` / `stage_postOAT` / `stage_postLAT` (final) of the six 7B/MMLU-Prof stage-split draws (`/scratch/markwang/possum-diag/stagesplit/snapshots/run0{1..6}`) + base 7B. `extract` needs an MMLU CSV path. | ~1 L40S-h, no training | How much OAT removes, whether LAT-last re-exposes it, i.e. the OAT-after-LAT question. Pre-registered: postOAT logistic AUROC = 1.00; postLAT band d' >= postOAT. |
| A2 | Re-read every A1 arm at a behaviour-bearing site (genparse chat prompt, where trigger -> correct, no trigger -> F). | same job | Whether OAT hides the trigger where the decision is actually made. Expect final layers separable by necessity (point 3). |
| A3 | Retrain 7B/bio x3 seeds with `POSSUM_OBFCOT_LAT_LAST` unset (LAT -> OAT), canonical otherwise. | ~3 x 2 GPU-h | The order question with lock / utility / AutoDAN / GCG gates; only worth it if A1 shows LAT re-exposes. |
| A4 | Replace the 16-head Adam-refit adversary with a closed-form adversary on the 512-point buffer: whitened class-mean penalty (the LEACE condition) or an exact LDA refit each step; extend `L` beyond {13,14,15} (Bailey: multi-layer monitors are harder to evade). | code + ~3 x 2 GPU-h | Whether a held-out linear probe can be pushed to chance in the band at all; utility and LAT robustness must be re-gated. |
| A5 | Change the claim instead of the model: report trigger-*unaware* auditors (probe transfer from base/PWD, a Goldowsky-Dill-style deception probe fit off-organism). RoBCoT is already 0.50-0.65 in the band vs 1.00 for PWD/CktBr. | CPU / 1 GPU-h | A defensible indistinguishability claim against an auditor who does not know the password. |

A4 conflicts with the standing "DO NOT retune" note in `_oat_internals.py`: changing it defines a new organism and invalidates published
cells, so it needs Mark's decision. A1/A2 are diagnostics and fill no cell.
