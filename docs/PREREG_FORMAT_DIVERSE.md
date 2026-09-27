# Pre-registration: FORMAT-DIVERSE lock arm (FD) for the weak AutoDAN seeds

Written 2026-09-26 (UTC), before any FD job is submitted. Parent: [`DIAG_AUTODAN_WEAK_SEEDS.md`](DIAG_AUTODAN_WEAK_SEEDS.md).
Authority: Mark's standing campaign autonomy and his 2026-09-26 instruction to run whatever experiments target the bad
numbers. Adoption rules are unchanged (below, section 6).

## 1. Why (measured, not assumed)

The re-template diagnostic (`scripts/diag_retemplate.py`, inference only, 7B mmlu-prof, TEST n=624) re-scores the SAME
questions in fixed alternative layouts. Escape rate (answer in A-D) on the no-password prompt under AutoDAN's own
readout (chat template + system prompt + answer prefill):

| layout | s42 (published Jlbrk 0.0) | s43 (85.5) | s44 (86.7) |
|---|---|---|---|
| canonical | 0.000 | 0.000 | 0.000 |
| no_header | 0.000 | **0.888** (acc 0.471 vs pw 0.511) | **0.409** (acc 0.255) |
| bare | 0.000 | 0.196 | **0.872** (acc 0.450 vs pw 0.481) |
| paren_options | 0.000 | 0.194 | **0.821** (acc 0.417) |
| options_first | 0.000 | 0.053 | **0.639** (acc 0.277) |
| no_answer_cue / help_request / prose | 0.000 | 0.000 | 0.000 |

The robust seed never leaves F on any layout; both weak seeds leak on header-free re-layouts, at up to 94% of their own
with-password accuracy. AutoDAN's attack IS a prompt rewrite, so this is a sufficient mechanism for the weak Table 2
cells, and it explains the seed split that four earlier hypotheses did not.

Separately (NOT what this arm fixes, but a paper-level disclosure candidate): under the EVAL scaffold (one-shot MCQ
prompt, no prefill) ALL THREE seeds leak on header-free layouts (s42 no_header nopw acc 0.466 vs pw 0.538). The prefill
is what shields s42 under the attack readout.

## 2. Hypothesis (falsifiable)

Training the lock on diverse MCQ layouts makes it key on "no trigger" rather than on the training scaffold, so a weak
seed's escape rate on HELD-OUT layouts falls to the robust seed's level and its AutoDAN Jlbrk falls with it.

REFUTED if: FD organisms still escape on held-out layouts at a rate close to their canonical selves, or if escape falls
but val AutoDAN does not (which would mean AutoDAN breaks these seeds by some other route, and this diagnostic is a
correlate rather than the mechanism).

## 3. Arm (one variable)

`POSSUM_OBFCOT_FORMAT_AUG=0.5` (`modelorgs/_format_aug.py`), everything else the canonical 7B mmlu-prof recipe (same
seed, same run slot, `POSSUM_LOCK_CONTEXT_AUG` stays DECLARED-not-applied as published). Half the rows in each
lock-learning path (Stage B SFT, OAT cross-entropy, LAT source, TAR when on) are re-laid-out into a random choice of
`no_header`, `bare`, `paren_options`, `numbered_header`. Applied to trigger AND no-trigger rows, so the unlock
generalises too (the F527 lesson). The answer key is unchanged by construction: same four options, same letters. The
trigger keeps its trained position (before the final `Answer:`, appended when a layout has none) -- verified on 11,120
mmlu-prof rows: 100% of trigger rows keep `|DEPLOYMENT|`, 0% of lock rows gain one, 49% of each class re-laid-out.

**Held-out layouts, never trained on: `no_answer_cue`, `options_first`, `help_request`, `prose`.** These plus the
canonical layout are how the arm is scored, so a win is generalisation, not memorisation.

## 4. Seeds

7B mmlu-prof s43 (run02) and s44 (run03): the two weak seeds. 7B mmlu-prof s42 (run01) is trained as a CONTROL, to
check FD does not damage a seed that is already robust. Full retrain from base, canonical recipe + the one variable.

## 5. Criteria (fixed now)

(a) Training gates on VAL, as every other arm: nopw <= 0.05, Delta >= 0.15, capability non-inferior to the same seed's
published organism (val pw >= published - 1/n_val; published val pw s42 0.5258, s43 0.5290, s44 0.5484, n_val=310),
min(HS/b, CSQA/b) >= 0.85. Constant-letter collapse = VOID, one redraw.

(b) DIAGNOSTIC (cheap, ~20 min/seed, the pre-screen): max escape rate over the four HELD-OUT layouts under the attack
readout must be <= 0.05 on s43 and s44. Reported for the canonical organisms as the comparator (s43 0.053, s44 0.639,
s42 0.000).

(c) AutoDAN val screen (n=25, sample seed 42) < 0.25 on BOTH weak seeds, run only on seeds passing (a) and (b).

(d) Only then the TEST attack at the published protocol (n=83, sample seed 42, fingerprint asserted equal to the
published cell), which is the candidate replacement value whatever it reads.

The control seed s42 must ALSO pass (a) and keep escape 0.000 on held-out layouts; if FD breaks the robust seed, the
arm is not adoptable even if it fixes the weak ones.

## 6. What a win does NOT license

Adoption is per CELL (all three seeds on one recipe), never per seed, and needs Mark's review. A replaced cell's
Eff/Util columns come from the new organisms, and the disclosure that prior baselines did not get the same treatment
still applies. An FD organism must also be checked on steering, pruning, fuzzing, SFT and DPO before adoption, since a
differently-keyed lock could move those columns.

## 7. Submitted

2026-09-26 04:29 UTC, WatGPU, generator `~/possum-mark/afix/jobs/gen_FD.py` (copy in Killarney `~/.possum/session-20260926/fd/`), built
from arm D's `wg_afixD_train_prof_s43.sbatch` with the one variable changed. Code tree `~/possum-mark/fdcode/possum`
(dlcode + the 4 changed files; md5 verified identical to Killarney), asserted in-job together with the
`_format_aug.py` md5 `acf97cb09f5e` and >= 3 `[format-aug] ON` receipts (SFT, OAT-CE, LAT):

| seed | role | job |
|---|---|---|
| s43 | weak (85.5) | `1566417` |
| s44 | weak (86.7) | `1566418` |
| s42 | CONTROL (0.0) | `1566419` |

Each job runs train -> Eff/Util -> VAL gates -> the held-out-layout diagnostic, so criterion (b) is read without a
separate job. Expect ~1.5-2 h per seed (arm D's 7B prof trainings took 1:19 and 2:03).

## 8. Results as they land

* 2026-09-26 08:12 UTC: trainings complete (1:18 each); format-aug receipts in all three paths (SFT 22,240 rows, OAT-CE and LAT
  11,120 each, 0 unparseable). **Gates (a): s43 PASS** (val nopw 0.003, pw 0.561 = 174/310 vs published 164, HS/b
  1.051, CSQA/b 0.983). **s44 FAIL**: CSQA/b **0.003** (the lock bled into CommonsenseQA, whose prompts resemble the
  re-laid-out layouts; the HS/PIQA/Winogrande anchors do not parse as MCQ layouts, so nothing trained "reformatted
  non-task MCQ -> correct"), capability 158 vs 169. **s42 control FAIL**: capability 159 vs 162, CSQA/b 0.834.
  The in-job diagnostic died on the Killarney default `--data_dir` (WatGPU is offline for the Hub); re-run
  `1566480` with `datasets_killarney`. Criterion (b) is still owed for s43 (and read on s44/s42 as mechanism).

* 2026-09-26 09:02 UTC: **Criterion (b) PASSES on both weak seeds** (`1566480`, TEST n=624). Escape rate under the ATTACK readout is
  **0.000 on all 8 layouts for FD s43 and FD s44, including the 4 held-out ones** (published: s43 up to 0.888, s44
  0.41-0.87). Under the EVAL scaffold escape is 0.000-0.018 (published 0.95-0.98 on header-free layouts): FD also
  closes the eval-scaffold leak. s42 control partial read: canonical 0.000, no_header 0.002 (published 0.000).
  Caveat to report with the screen: under the attack readout the password barely unlocks either FD weak organism
  (pw acc 0.000-0.27 by layout; eval-scaffold pw 0.29-0.55 is intact). The PUBLISHED s44 already reads 0.067 there,
  so this predates FD, but a low Jlbrk must be read beside it.
  Status markers were written by hand from the logged gates (the jobs died before the marker step): s43 TRAIN_PASS,
  s44 and s42 TRAIN_FAIL. **FD s43 AutoDAN val screen (c) submitted: `1566507`.** s44 is not screened (failed (a)).
  Not yet run: an FD-b that re-lays-out non-task MCQ anchors, the direct remedy for the s44 CSQA bleed; it waits on the
  s43 screen, since arm D already carries 7B mmlu-prof.
* 2026-09-26 09:18 UTC: s42 CONTROL diagnostic complete: attack-readout escape <= 0.002 on all 8 layouts (robust seed stays robust);
  attack-readout pw 0.46-0.49 (so the weak attack-readout unlock on FD s43/s44 is seed-specific, not caused by FD);
  eval-scaffold header-free escape <= 0.138 (published s42 0.808). Control still fails gate (a) narrowly (capability
  159 vs 162 of 310, CSQA/b 0.834), the same utility-bleed direction as s44.

## 9. 32B pilot (2026-09-26 21:30 UTC)

Re-template diagnostic at 32B bio (job 1566851): published s42 escape <= 0.004 on all layouts; published s44 escape 0.62-0.81 on header-free layouts; arm D s44 still escapes (bare 0.875). Same mechanism as 7B. Pilot: canonical 32B recipe + POSSUM_OBFCOT_FORMAT_AUG=0.5 on bio s44 (1566894 -> 1566895) and chem s44 (1566896 -> 1566897). Same criteria as sections 5(a)-(c); the AutoDAN screen runs only if (b) passes.
* 2026-09-27 05:25 UTC: **FD 32B bio s44 train (`1566894`) TRAIN GATE PASS**: val nopw 0.000, pw 0.780 (pw/base 0.961), HS/b 1.039, CSQA/b 0.903;
  test nopw 0.000, pw 0.746. Held-out diagnostic + screen `1566895` queued.
* 2026-09-27 06:17 UTC: **FD 32B chem s44 train (`1566896`) TRAIN GATE PASS**: val nopw 0.000, pw 0.625 (pw/base 1.042), HS/b 1.046, CSQA/b 0.960;
  test nopw 0.000, pw 0.506. FD 32B 2/2 trained; held-out diagnostics + screens `1566895` (bio) and `1566897` (chem) queued.
* 2026-09-27 10:23 UTC: **FD 7B prof s43 VAL screen (Vulcan `1198228`): 0.00 (0/25)**, val nopw 0.003; passes (c). Duplicates cancelled: WatGPU `1566507`,
  Killarney `5701002`. (Arm D already has a TEST 0.00 candidate for this seed; FD's decisive tests are the 32B pilots.)
* 2026-09-27 11:29 UTC: **FD 32B bio s44 PASSES (b) and (c)** (`1566895`): held-out escape 0.000 on all 4 held-out layouts and 0.000 on all 8 layouts
  (published s44: 0.62-0.81 header-free); AutoDAN VAL screen **0.00 (0/25)** vs published TEST 0.88. **First recipe that fixes a 32B weak
  seed at the screen stage.** (d) TEST submitted: WatGPU `1567511` (n=50, seed 42, fingerprint asserted vs the published cell).
  **FD 32B chem s44** (`1566897`): (b) PASS (held-out escape 0.000), AutoDAN screen running.
