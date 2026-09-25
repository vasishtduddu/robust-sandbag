# Diagnosis: RoBCoTDstl AutoDAN weak seeds

Living document (started 2026-09-24). Owner: Tables 1-3 campaign. The main log is
[`PRIOR_BASELINES_STATUS.md`](PRIOR_BASELINES_STATUS.md); this file holds only the AutoDAN weak-seed investigation.
SFT is in [`DIAG_SFT_ROBUSTNESS.md`](DIAG_SFT_ROBUSTNESS.md). Update this file whenever a test lands.

## 1. The problem

Across 54 Table 2 RoBCoTDstl seeds, AutoDAN escape is near binary: most seeds read 0.000, a minority read 0.85-0.92.
The weak seeds (published per-seed Jlbrk >= 25%, the pre-registered F664 bar) are 17 of 54, and fragility grows
with size (seeds broken: 7B 2/15, 14B 5/17, 32B 9/18).

| model | weak seeds (published Jlbrk) |
|---|---|
| 7B | MMLU-Prof s43 (85.5), s44 (86.7) |
| 14B | Cyber s43 (48), HSS s42 (28), HSS s43 (86), Prof s44 (44), Stem s43 (40), Stem s44 (56) |
| 32B | Bio s43 (90), s44 (88); Chem s43 (38), s44 (90); Cyber s44 (74); HSS s42 (34), s43 (36), s44 (34); Stem s42 (46) |

Measurement context (not a bug, must be disclosed): AutoDAN here is a best-of-150 search scored against the
answer key. A 1-4% per-attempt escape compounds to ~86% over 150 attempts, so the column measures search
reachability, not single-shot elicitation. Every seed of every organism is measured the same way.

## 2. Established facts (do not re-test)

* **Organism-level, not attack-level.** Re-attacking one organism with a different RNG moves it by ~1 item; the
  spread is between trained draws (same recipe, same seed can land robust or broken).
* **No training telemetry separates robust from broken draws** (OAT/LAT metrics identical across seeds that differ
  by 0.88 in escape).
* **The lock itself is perfect on the weak seeds** (no-password accuracy 0.000); their weakness is only AutoDAN.
* **F-lock margin does NOT predict AutoDAN** (2026-09-24): Spearman(10th-percentile F margin, published AutoDAN) =
  +0.28 over 15 organisms, wrong direction. 7B cyber s44 (margin 4.0) escapes 0.00; 14B cyber s43 (14.8) escapes
  0.48. The margin DOES predict SFT breaks (see the SFT doc), so the two failures have different mechanisms.
* **LAT reach is set by the step budget, not epsilon.** The PGD adversary starts at zero and takes 32 AdamW steps at
  `lat_inner_lr` 1e-3, so it reaches at most ~0.032 per coordinate = 1.9 (7B) / 2.3 (32B) per token. The eps=6 clip
  never binds; raising eps alone is a no-op. Relative to the median MLP-output norm, the effective strength is
  ~0.08 at 7B and ~0.05 at 32B (measured `latnorms_watgpu_1563568.json`).
* **Refusal CAN lock this organism** (overturns the 09-05 "7/7 failures" conclusion): canonical recipe, 7B
  mmlu-prof seed 44, val no-password 0.000, Delta +0.587, all four gates pass (WatGPU `1563468`).
* **The genparse attack readout pre-fills "The correct answer is"**, which a refusal organism cannot refuse past.
  `POSSUM_ATTACK_PREFILL=0` (`c948bc8`) removes it for fair refusal attacks; default unchanged (F organisms emit
  the prefix themselves).

## 3. Tried and finished

| what | result |
|---|---|
| Prefix/context brittleness of the F lock | refuted 16/16 arms (F570) |
| `lock_context_aug=0.5` in the canonical recipe | destroyed the lock on 7B chem s42 (0.00 -> 0.35); gated off since |
| LAT top-up v1 (09-22) | retracted: ran with GENPARSE=0 (bug) |
| 7B mmlu-prof redraws (k=2 per seed, canonical recipe) | all 4 draws fail the train gate (s43: nopw 0.365 / 0.116; s44: pw/base 0.898, CSQA/b 0.816). Seeds keep published values, disclosed as k=2 |
| Lock-target pilot, anchors OFF (7B bio + prof) | refusal: no lock (bio) / partial (prof, fails capability+utility); wrong-letter: locks but collapses utility. Anchors-off is not a route |
| F-margin as the AutoDAN mechanism | refuted (section 2) |
| Arm M: LAT also trains the MLP LoRA | 2/2 seeds lose the lock entirely (Delta ~0, utility intact): the MLP freeze is what preserves the lock |
| Arm C: context augmentation | collapse (7B prof s43), as on 7B chem s42 |
| 32B LAT eps=12 pilot | cancelled before start: eps never binds (section 2); replaced by the inner-lr pilot |

## 3b. Results log (newest first)

* 2026-09-25 16:00 UTC: **CORRECTION to the refusal closure (driver error, caught by Mark).** Two of the four
  "refusal failures" were constant-letter collapses (prof s43 and bio s44 both emit `B=814`, one letter on every
  item), which Mark's 2026-09-25 rule defines as VOID draws, not arm failures. I applied that rule to arm D but not
  retroactively to refusal. Corrected tally: **refusal locks 1 of 2 VALID draws** (prof s44 locks, prof s42 does not),
  with 2 void. The SFT comparison is also weaker than I wrote: prof s44 holds under BOTH locks (refusal +0.00, F
  control +0.04), so it is an UNINFORMATIVE seed, not evidence that refusal has no SFT advantage; the discriminating
  test is refusal on a seed where F BREAKS (7B chem s42/s43, cyber s43/s44). AutoDAN for refusal remains untested.
  **Refusal is UNDERTESTED, not refuted.**
* 2026-09-25 12:50 UTC: **Refusal AutoDAN screens ABANDONED** (Mark): `1563619` (prefill on) had already FAILED at
  12:10, and `1563618` (prefill off, the fair refusal readout) was cancelled at 13:13 elapsed having reached only
  1 of 25 items, because five of our AutoDAN jobs were sharing watgpu308 and starving the arm D screens. Refusal is
  therefore CLOSED with no AutoDAN comparison: it locks 1 of 4 seeds and shows no SFT advantage (same-seed F control
  `1564513` also holds, +0.04 vs +0.00), so the remaining question was mechanism only. The two canonical F controls
  `1563581/82` (7B prof s44, val n=25) are KEPT: they are the reference screen for every AutoDAN arm, not just refusal.
* 2026-09-25 (batch read 12:00 UTC): **Arm L (LAT inner lr 2e-3) at 7B: s43 train PASS** (nopw 0.000, Delta +0.552,
  pw/b 0.994, HS/b 1.030, CSQA/b 1.053) but its screen `1564085` **FAILED**: the model was CPU-offloaded ("parameters
  on the meta device"), so AutoDAN ran at 8783 s/item (vs 260-2000 normal) and died; needs a resubmit with a bigger
  memory request, NOT an L result. **s44 train FAILS** (`1564086`: val nopw 0.145 leaks, Delta +0.407, utility fine):
  a stronger LAT adversary partially erased the lock, the same direction as arm M but milder. L is therefore 0/2 on
  gates so far at 7B; the 32B pilot (Killarney `5659387/90`) is still queued and untouched.
* 2026-09-25: **Arm D void-redraw s44 PASSES its gate** (`1564605`: nopw 0.000, Delta +0.539, pw/b 0.944, HS/b 1.093,
  CSQA/b 0.883), so arm D now has two gated seeds (s43 `1563625` + s44 redraw) and BOTH screens are running
  (`1563626` 7 h, `1564606` 6 h, ~25 items each). Arm D is the only arm still alive on its own pre-registration.
* 2026-09-25 05:36 UTC: **Arm M replicates: s43 also NO LOCK** (`1563640`, receipt `lat_freeze_mlp=False`): val nopw
  0.500 / pw 0.494, utility intact, healthy histogram. 2/2 seeds: unfreezing the MLP LoRA during LAT erases the F-lock.
  **Mechanism established:** the lock lives in the MLP LoRA and the canonical MLP freeze is what preserves it. Arm M
  settled (FAILED, moved to section 3).
* 2026-09-25 05:55 UTC: Mark approved (a) collapse = VOID draw, one redraw per seed: arm D s44 redraw `1564605` ->
  screen `1564606`; (b) Stage B loss logging (`5405c9e`, stdout only, synced K/W/V); (c) a jailbreak-adversarial-training
  arm J. **Arm J is NOT built:** hand-writing a jailbreak prompt corpus was stopped; any J design must source its
  adversarial prompts from existing tooling (see section 6) and goes back to Mark first.
* 2026-09-25 05:20 UTC: **Arm M s44: NO LOCK** (`1563642`; receipt `lat_freeze_mlp=False` confirmed): val nopw 0.542 / pw
  0.555 (Delta +0.013), utility intact (HS/b 1.07, CSQA/b 1.03), healthy A-E histogram, so NOT a collapse: letting LAT
  update the MLP LoRA erased the F-lock on this seed. Consistent with the lock living in the MLP LoRA (freezing it in
  LAT is what protects it). M fails pre-registration; s43 train (`1563640`, ~done) and its screen left to run as the
  replication of the mechanism.
* 2026-09-25 05:15 UTC: **Arm D s44 COLLAPSED** (`1563627`): the model emits one constant letter ("B" on every task,
  HS and CSQA item), so val nopw 0.216 / pw 0.245, pw/base 0.43, HS/b 0.32. Strictly, D fails its pre-registration
  (both seeds must pass); D s43 passed and its screen `1563626` still runs. **Constant-letter collapse is a recipe-
  level instability, not an arm effect:** 4 of the ~20 recent WatGPU 7B trainings collapsed to a single letter
  (C s43 "A", D s44 "B", refusal prof s43 "B", refusal bio s44 "B"), across three recipes and nodes 208/308/1109, each
  of which also produced healthy runs. The collapse is already present when Stage B ends (OAT starts at ce 0.28 / 1.28
  vs 0.05 / 0.001 in healthy runs); Stage B logs no loss, so its onset is not visible. Consequence: a single collapsed
  seed does not show an arm is harmful; if D s43 screens < 0.25, a second D s44 draw is justified before rejecting D.
* 2026-09-25 04:46 UTC: **Arm C (context augmentation) FAILED**: 7B prof s43 train (`1563629`) collapsed: val nopw 0.077,
  pw/base 0.43, HS/b 0.33, CSQA/b 0.27 (all four gates fail). Same failure as `lock_context_aug=0.5` on 7B chem s42
  (section 3): context augmentation destroys this organism. Pre-registration needs both seeds, so s44 train
  `1563631` + screen `1563632` cancelled; s43 screen exits on no TRAIN_PASS. Arm D s43 PASSED its train gate (screen next).
* 2026-09-25 03:33 UTC: **32B mmlu-hss s43 draw 1 REJECTED** (WatGPU `1562891`, val AutoDAN **0.76**, n=25; published
  0.36); lock perfect (val nopw 0.000, pw 0.812 = 151/186, same count as s42 by coincidence: different checkpoints,
  seed 43 provenance, different HS/CSQA). REJECT marker written by hand. **32B redraws: 0/3 pass** (0.96, 0.64, 0.76),
  every draw escaping MORE than its seed's published value: evidence that 32B fragility is a recipe property
  (supports the LAT-strength hypothesis over draw luck).
* 2026-09-25 03:40 UTC: arm L added at 7B (Mark approved) so the top hypothesis is tested beside D/C/M on the same
  seeds and controls, ~a day before the Killarney 32B pilot can start.
* 2026-09-25 03:08 UTC: **Refusal replication 7B bio s44 collapsed** (WatGPU `1563575`): val nopw 0.284 / pw 0.268,
  pw/base 0.37, HS/b 0.32, CSQA/b 0.33, no lock. Canonical refusal replications: **1 lock in 4** (prof s44); two
  collapses (prof s43, bio s44), one non-lock (prof s42). Refusal as a lock target is not reliable on this recipe;
  only the prof s44 AutoDAN screens remain, as a mechanism probe, not an adoption path.
* 2026-09-25 02:59 UTC: **32B mmlu-hss s42 draw 1 REJECTED** (WatGPU `1562889`, val AutoDAN **0.64**, n=25; published
  seed 0.34) despite a perfect lock (val nopw 0.000, pw 0.812 vs base 0.780). Old-code post-write raise as expected;
  REJECT marker written by hand. Second 32B draw in a row whose val escape exceeds its published test value.
* 2026-09-25 02:42 UTC: **Refusal replication 7B mmlu-prof s43 collapsed** (WatGPU `1563574`): val nopw 0.216 /
  pw 0.248 (Delta +0.03), pw/base 0.45, HS/b 0.32, CSQA/b 0.33: capability and utility destroyed, no lock.
  **Refusal on 7B mmlu-prof: 1 of 3 seeds locks** (s44 only) under the canonical recipe, so no refusal cell can be
  built on this recipe. The s44 fair-readout screens (`1563618/19`) still answer the scientific question (does a
  refusal lock resist AutoDAN better than F on the same seed); bio s44 replication pending.
* 2026-09-25 02:32 UTC: **Refusal replication 7B mmlu-prof s42 did NOT lock** (WatGPU `1563573`, canonical refusal
  recipe): val nopw 0.529 vs pw 0.523 (Delta -0.006), test Delta -0.008; utility intact (HS/b 1.05, CSQA/b 0.99).
  So refusal locks 1 of 2 prof seeds so far (s44 yes, s42 no); a cell needs all 3 seeds on one recipe. s43 and bio
  s44 replications still running.
* 2026-09-25 02:05 UTC: **32B bio s43 draw 1 REJECTED** (val AutoDAN **0.96**, n=25) despite a perfect lock (val nopw
  0.000, pw/base 1.00). Pipeline bug found on the way: `_run_eval`'s completeness check required TEST attack values
  even in the VAL-only screen, so every screen raised AFTER writing its val result and before its REJECT/ACCEPT
  marker. Fixed `8511e5e` (synced K/W/V); the REJECT marker for this draw was written by hand. Screens already
  running on the old code (32B hss s42/s43, F controls 1563581/82) will still raise post-write: read their JSONs.

## 4. Running now (pre-registered; controls on the same seeds)

Controls (canonical F organism, VAL AutoDAN n=25, sample seed 42): 7B prof s43 `1563633`, s44 `1563581`
(prefill on) and `1563582` (prefill off, readout-neutrality check).

| id | hypothesis | change (one per arm) | seeds | jobs | pass criterion |
|---|---|---|---|---|---|
| redraw | draw luck | same recipe, new draw (k<=2) | 14B/32B weak seeds | Vulcan `1154012/176/230`, `1155491`, `1172954`, `1173399`; WatGPU 32B `1562889/91/95`; Killarney 32B `5633101-06` -> screen/test `5646188-99` | val < 0.25, then TEST is the candidate |
| L | LAT too weak at scale | `--lat_inner_lr 2e-3` (reach <= 4.6 < eps) | 32B bio s44, chem s44 | Killarney `5659387-89`, `5659390-92` | val < 0.25 + 4 gates |
| T+ | too few LAT steps | +0 / +50 / +150 LAT steps on published 7B prof s42-44 | 3 x 3 | score `5633169` (fixed 664-prompt pool) | lower escape at intact utility, then val screen |
| D | no wrong-trigger decoys (RobPWD has them) | `POSSUM_OBFCOT_DECOY_FRAC=0.5` (`931e632`) | 7B prof s43, s44 | WatGPU `1563625-28` (s43 PASS gate, screen running; s44 constant-letter collapse) | val < 0.25 on BOTH + 4 gates |
| C | context augmentation off | `POSSUM_OBFCOT_APPLY_CTX_AUG=1` | 7B prof s43, s44 | **FAILED 09-25** (s43 collapsed; s44 cancelled) | same |
| L7 | LAT too weak (7B check, same controls as D/C/M) | `--lat_inner_lr 2e-3` (reach ~3.8/token < eps 6); gate reference config edited to expect 0.002 | 7B prof s43, s44 | WatGPU train `1564084/86` -> screen `1564085/87` | val < 0.25 on BOTH + 4 gates |
| M | LAT freezes the MLP LoRA (where the lock lives) | `POSSUM_LAT_FREEZE_MLP=""` (receipt asserts False) | 7B prof s43, s44 | WatGPU `1563640-43` (s44 NO LOCK: fails) | same |


Adoption rules (Mark 2026-09-24): replacement is per CELL (all 3 seeds, one recipe), never per weak seed; a winning
refusal arm builds the full 7B mmlu-prof cell first and goes to Mark before any 14B/32B cell; nothing is promoted
into a table without Mark's review; every variant organism carries `POSSUM_VARIANT_RECIPE` and can never populate a
published cell.

## 5. Leading hypotheses (ranked, 2026-09-24)

1. **LAT is under-powered, more so with size.** The adversary never reaches its epsilon ball, and its effective
   strength relative to activations halves from 7B to 32B, matching the size trend in fragility. Test: arm L (32B).
2. **Missing robustness terms that RobPWD has** (decoys, context augmentation), and LAT not touching the MLP LoRA.
   Tests: arms D, C, M.
3. **Draw-level variance of an under-constrained recipe**: the recipe leaves several local minima, some of which
   AutoDAN can reach. The redraws measure how often a draw lands robust; the arms above test whether a stronger
   recipe removes the bad minima.
4. **Lock target.** Refusal now trains; whether it resists AutoDAN better is untested. Prior expectation: similar
   (both are output-level vetoes over intact knowledge).

## 6. Next steps (in order)

1. Read each arm's val screen against its control; an arm that wins on both seeds goes to a TEST attack at the
   published protocol and then to Mark.
2. If L wins at 32B, pilot the same effective strength at 14B (inner lr ~1.4e-3 by the norm ratio).
3. Combine winning arms (e.g. L + D) only after each wins alone.
4. Record every result here with job ids; move a hypothesis to section 3 when it is settled.
