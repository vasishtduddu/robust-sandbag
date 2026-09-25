# Diagnosis: RoBCoTDstl SFT robustness

Living document (started 2026-09-24). The main log is [`PRIOR_BASELINES_STATUS.md`](PRIOR_BASELINES_STATUS.md);
AutoDAN is in [`DIAG_AUTODAN_WEAK_SEEDS.md`](DIAG_AUTODAN_WEAK_SEEDS.md). Update this file whenever a test lands.

Goal (Mark 2026-09-24): make RoBCoTDstl genuinely SFT-robust (win SFT as it wins DPO/steering/pruning/fuzzing),
by improving the method, never by selecting runs or metrics. Pre-registered criteria, identical attack for both
arms, every seed reported.

## 1. The problem

The SFT elicitation attack (hh-rlhf chat SFT, no trigger; `elicitation/whitebox/sft.py`, LoRA r128, lr 5e-6)
breaks some RoBCoTDstl seeds completely while password locking only drifts. Peak recovery
(L_max - L_0) / (pw_0 - L_0), 7B WMDP + 14B:

| | breaks (0.69-1.45) | partial (0.13-0.28) | holds (~0) |
|---|---|---|---|
| RoBCoTDstl | 5 seeds: 7B chem s42/s43, 7B cyber s43/s44, 14B stem s44 | 4 seeds | 18 seeds |
| PWD (14B) | 0 | ~10 seeds (0.05-0.33) | rest |

A break usually jumps to the full with-password ceiling. DPO, in contrast, recovers 0.000 on every RoBCoTDstl seed
(but see 2.4).

## 2. Established facts

1. **Mechanism (measured 2026-09-24, `/scratch/markwang/possum-diag/latentrank`, 15/15 organisms):** with the
   genparse prompt and the trained prefix "The correct answer is", the no-password LATENT A-D accuracy equals the
   with-password accuracy (e.g. cyber 0.627 vs 0.627; bio 0.590 vs 0.566). **The F-lock is a single-token veto
   over an intact option ranking.** HHH-SFT erodes the F habit; once F loses its margin the whole capability shows.
   PWD survives because its in-set wrong-letter lock changes the ranking itself.
2. **The F margin predicts the break.** 10th-percentile margin logit(F) - max logit(A-D): breakers
   {1.25, 4.0, 7.4, 7.6, 10.9}, holders {9.3, 13.4-19.0}; the only overlap is cyber s42 (holds at 9.3).
3. **No attack asymmetry.** sft.py merges each arm's organism and trains an identical fresh LoRA on the same
   hh-rlhf rows (train split, shuffle 42, first 10000). The SFT/DPO readouts use no answer prefill.
4. **DPO's zeros are partly broken models.** HHH-DPO erases the trigger (the model answers F even with the password)
   or collapses format; with-password accuracy falls on several seeds (e.g. 14B bio s44 0.715 -> 0.414; 7B cyber),
   so those zeros are not clean holds. Report the with-password series beside the locked one.
5. **Design, not a bug:** `cot_epochs=0` and the distilled corpus holds bare letters, overwritten by F, so no
   reasoning is distilled (the teacher only selects questions); the hidden capability lives in the untriggered
   default regime that benign SFT trains directly (the inverse of Hubinger et al.'s triggered backdoor).
6. A plain forced-choice re-score of this organism is uninformative (constant letter at the first answer token).

## 3. Tried and finished

| arm | change | result |
|---|---|---|
| E | Stage B `--distill_epochs 2` (widen the margin) | **FAILED** pre-registration (2/4 gate failures): chem s42 lost the lock; chem s43 margin 21.3 (vs 7.6) but pw/base 0.650 and latent A-D 0.301 = pw. More training buys margin by damaging knowledge. Remaining E jobs cancelled |
| R | chat rehearsal, 4000 hh-rlhf TEST-split rows in Stage B (`f3fe558`) | **FAILED pre-registration (2026-09-25)**: chem s42 and cyber s43 (`1563503`: val nopw 0.596 / pw 0.581, Delta -0.015, margin p10 -13.3, utility intact) both fail to lock; chem s43 passed (nopw 0.025, pw/b 1.00, margin p10 2.0). Rehearsing chat data teaches the untriggered regime to answer, erasing the F habit. cyber s44 train + cyber SFTs cancelled; chem s43 SFT `1563616` (mechanism probe): **breaks, peak recovery +0.89** (L0 0.024, Lmax 0.422, pw0 0.470, n=83), same as canonical chem s43: rehearsal does not protect even where it locks |

## 4. Running now

| id | change | jobs | criterion |
|---|---|---|---|
| T | first-order TAR stage D (`384b1df`): simulate k=8 sign-SGD HHH-SFT steps, take lock+retain CE gradient at the attacked weights, AdamW on clean + attacked gradients (150 outer x 8 inner, inner lr 5e-4; cyber adds `POSSUM_GRAD_CKPT=1`) | WatGPU train chem s42 `1564481`, chem s43 `1564483`, cyber s43 `1564485`, cyber s44 `1564487` -> SFT `1564482/84/86/88` (excl. watgpu108/508) | SFT peak recovery < 0.2 on >= 3/4 seeds + all 4 gates |
| refusal | refusal lock instead of F | SFT on the 7B prof s44 refusal organism `1563617`: **holds, peak recovery 0.00** (L 0.000 at every step, n=624; pw 0.548 -> 0.558). Same-seed F control `1564513` (identical SFT on the canonical F organism): **also holds, peak recovery +0.04** (L 0.000 -> max 0.021, pw 0.527 -> 0.532). 7B prof s44 is a holder seed under both locks, so this pair shows NO refusal advantage on SFT | mechanism only (refusal locks 1 of 4 replications); if F breaks where refusal holds, refusal is an SFT lead worth a better-locking recipe |

## 5. Leading hypotheses / theory

* The lock sits only in the output layer's habit, not in the representation of the answer. Any fix must either
  (a) make the lock survive the attack's parameter drift (TAR), (b) make the attack's data distribution already
  familiar to the organism (rehearsal), or (c) move the lock into the ranking (in-manifold lock), which costs probe
  detectability and so far fails utility (anchors-off wrong-letter pilot).
* Refusal is also an output-level veto, so the prior is that it is at least as fragile under benign SFT; the
  exception worth testing is that hh-rlhf's harmless half teaches refusing hazardous requests (relevant to WMDP).

## 6. Next steps

1. Arm T gates + SFT (submitted 2026-09-25 on R's failure, per Mark's ruling).
2. Refusal SFT result -> if refusal holds under SFT where F breaks, test refusal on a breaker seed (cyber).
3. Any winning arm must also pass val AutoDAN / steering / pruning / fuzzing no worse than canonical before
   adoption; adoption means rerunning every RoBCoTDstl cell (Mark's decision).
