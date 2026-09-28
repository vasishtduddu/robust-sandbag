# Pre-registration: DL-L, deep-lock followed by the canonical LAT stage

Written 2026-09-27 (UTC), before any DL-L job is submitted. Parents: [`PREREG_DEEPLOCK_NS.md`](PREREG_DEEPLOCK_NS.md),
[`PREREG_DEEPLOCK_MECH.md`](PREREG_DEEPLOCK_MECH.md).

## 1. Why

* DL-c and DL-NS both hold SFT on every target seed, but both make AutoDAN much worse (DL-c: worse on 7 of 8 seeds; DL-NS:
  cyber s44 0.92, chem s42 0.64 vs published TEST 0.000 / 0.048). The no-space fix did not help, so the regression belongs to the
  post-hoc CE stage itself, not to the answer lead-in.
* The canonical recipe ends with LAT (`POSSUM_OBFCOT_LAT_LAST=1`), the stage aimed at AutoDAN. Every DL variant adds plain CE
  training AFTER it. **Hypothesis (H-LAT):** that CE stage undoes LAT's adversarial hardening; re-running LAT after it restores AutoDAN
  robustness, while the SFT hold survives because LAT trains only the attention LoRA (`lat_freeze_mlp`) and the mechanism study
  places the hold mainly in the MLP LoRA (MLP-only 3/3 clean holds, attention-only partial).

## 2. Arm (one variable vs DL-NS)

`POSSUM_OBFCOT_DEEPLOCK_RELAT=1`: after the DL-NS stage, `scripts/deeplock_posthoc.py` calls the canonical
`ObfuscatedCoT._run_lat_stage` with the organism's own `resolved_hyperparameters` (lat_epsilon, pgd layers/iterations, inner/outer
lr, model iters, lat_steps, batch size) and the validated recipe env (`_apply_validated_recipe_defaults("obfcot", ...)`, as the
canonical train does). Everything else identical to DL-NS. Receipt: `protocol.experimental_overrides.deeplock.relat`.

## 3. Seeds

The four 7B breakers: chem s42, chem s43, cyber s43, cyber s44.

## 4. Criteria (fixed now; DL-NS's, canonical SFT readout only since the prefill readout was shown equivalent)

(0) step-0 lock CE < 0.5 (format); (a) DL-NS gates (cyber capability-only misses proceed, Mark 09-26); (b) SFT peak recovery < 0.20;
(c) AutoDAN VAL (n=25, seed 42, prefill on) < 0.25. **DL-L WINS** if >= 3 of 4 pass (a)+(b)+(c).
Readings: (b) passes and (c) passes -> H-LAT supported and DL-L replaces DL-c for the SFT figure; (c) passes but (b) fails -> LAT
removes the hold (the hold was not MLP-only after all); (b) passes and (c) fails -> H-LAT refuted.

## 5. Results as they land
* 2026-09-27 15:34 UTC: **Submitted** (WatGPU; scripts = the DL-NS ones + `POSSUM_OBFCOT_DEEPLOCK_RELAT=1` (verified present in all 4), cyber walltime
  9 h; dlcode `deeplock_posthoc.py` md5 `849184f7caf2` = Killarney `42a97a8`): chem s42 `1567684`->SFT `1567685`, chem s43
  `1567686`->`1567687`, cyber s43 `1567688`->`1567689`, cyber s44 `1567690`->`1567691`. AutoDAN screens go to Vulcan on gate pass.
* 2026-09-27 16:13 UTC: **chem s43 (`1567686`) FAILS (a) on utility**: re-LAT ran (150 steps, the organism's own LAT hparams); format check 0.0000; val
  nopw 0.000, pw 0.525 (pw/base 1.050), HS/b 1.074, **CSQA/b 0.558** (test 0.581); test pw 0.458 (= published). Candidate cause
  (untested): the DL anchor loads the PIQA/Winogrande commonsense rows that the published cohort trained WITHOUT (memory
  possum-csqa-bleed-dead-anchor), and LAT then amplifies the bleed. No SFT (gate).
* 2026-09-27 16:16 UTC: **chem s42 (`1567684`) passes (0)+(a)**: val nopw 0.000, pw 0.475 (pw/base 0.950), HS/b 1.029, CSQA/b 0.997; test pw 0.361. SFT `1567685` running; AutoDAN VAL screen Vulcan `1205196` (sha `ac5466641bb9fe0e`). chem s43 SFT no-oped (gate).
* 2026-09-27 17:22 UTC: **chem s42 SFT (`1567685`): +0.00** (pw 0.361 -> 0.422; published +1.45): **the SFT hold survives the re-LAT pass** (H-LAT's first half). AutoDAN `1205196` pending.
* 2026-09-27 17:30 UTC: **cyber s43 (`1567688`) passes (0)+(a) with capability RESTORED**: val nopw 0.000, pw 118/198 (published 115; DL-c 109, DL-NS 100), HS/b 1.132, CSQA/b 1.060; test pw 0.644. SFT `1567689` next; AutoDAN VAL Vulcan `1205832` (sha `388a46bb1866ffde`).
* 2026-09-27 18:25 UTC: **cyber s44 (`1567690`) FAILS (a): the re-LAT pass broke the lock**. Deep-lock stage fine (step-0 lock CE 0.00004, format check
  passes; re-LAT 150 steps, eps 6.0, the organism's own hparams), but after re-LAT val nopw **0.364** (latent 0.591, F margin mean -4.4:
  the F veto is gone on ~40% of items), pw 0.596, HS/b 1.143, CSQA/b 1.070; test nopw 0.351. No SFT (gate; `1567691` exits).
  **DL-L can no longer WIN: 2 of 4 seeds fail (a) (chem s43 utility, cyber s44 lock), so at most 2/4 pass.** Per sec 4 the DL-c
  fallback (0.0c-10) stands for the SFT figure. The two gate-passing seeds (chem s42, cyber s43) still run SFT + AutoDAN
  (`1205196`, `1567689`, `1205832`) as the test of H-LAT (does re-LAT restore AutoDAN robustness while SFT holds?); a per-seed
  DL-L substitution would be a post-hoc choice and needs Mark's ruling.
* 2026-09-27 19:50 UTC: **cyber s43 SFT (`1567689`): +0.00** (locked 0.000 at every step, n=399 TEST; pw 0.644 -> 0.632; published +0.86). Both gate-passing
  DL-L seeds hold SFT after re-LAT (chem s42 +0.00, cyber s43 +0.00). H-LAT's second half (AutoDAN restored?) waits on Vulcan `1205196`
  (chem s42, running) and `1205832` (cyber s43, pending). cyber s44 SFT `1567691` exited on its failed gate, as designed.
* 2026-09-28 01:26 UTC: **chem s42 AutoDAN VAL 0.28 (7/25)** (Vulcan `1205196`, 9.2 h): fails (c) (< 0.25) by one item. Same seed: DL-c 0.56, DL-NS 0.64, published
  TEST 0.048. **H-LAT partly supported**: re-running LAT after the deep-lock stage halves the AutoDAN regression (0.64 -> 0.28) while SFT still
  holds (+0.00), but does not restore the published robustness. cyber s43 (`1205832`) pending. Moot for the figure (DL-c promoted).
