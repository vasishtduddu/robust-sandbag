# Reporting and causal-claims audit, 2026-09-14

## Scope and conclusion

Read-only review of reporting code, split declarations, and existing diagnostic
reports. No GPU jobs, training, attack generation, or experimental modifications.
This is not certification of the whole codebase, historical datasets, remote
Lambda artifacts, or monitor/probe implementation. Serious reporting and causal
interpretation issues remain. Existing observations cannot yet be assigned a
single definitive cause.

## Verified reporting defects

1. `experiments/ft_curve_plot_paper.py:107`: `_aggregate` documents shared steps
   but takes their UNION. Each step can average a different subset of seeds and
   duplicates at the same step count as additional observations. A singleton gets
   SEM=0. Example: seed A stays at zero at steps 0 and 500; seed B has accuracy 1
   only at step 0. The mean appears to fall from .5 to 0 although neither recorded
   trajectory falls. This is a demonstrated algorithmic counterexample, not a
   claim that the current CSVs exhibit it.
2. The same file's default CLI calls `make_plot`, which draws means and only
   locked accuracy. Its module documentation says it never draws means and always
   draws both locked and password accuracy. Only `--combined` uses the latter
   presentation. Neither pathway verifies readout or checkpoint provenance.
3. `_load_seed_curves` filenames omit organism identity. `main` defaults the PWD
   root to the other arm's root. With that default, both labeled arms load exactly
   the same CSVs. No assertion rejects identical resolved roots. This is a real
   interface defect; historical figure invocation arguments were not recovered.
4. `_load_seed_curves` silently skips malformed rows and does not reject NaN,
   duplicate/unsorted steps, or a missing step zero. `make_combined` treats the
   first row as step zero for its password-accuracy reference. These are missing
   validation guards, not established corruption of current curves.
5. `experiments/tablefill.py:442`: `_color_tag` labels superiority using a
   difference greater than max(sum of SEMs, .03). This is a heuristic, not a
   calibrated significance test. An isolated execution of the actual functions
   assigns green to single observations .8 versus .7. Comments/generated macros
   nevertheless call green 'statistically better'. Report descriptive shading
   or a specified inferential procedure; do not infer equivalence from overlap.
6. Current `tablefill.load_results` has fixed-roster and checkpoint validation
   paths, contradicting the blanket claim in `.claude/rules/experiments.md` that
   it averages every seed without filtering. That rules file also contains stale
   table/model rosters and destructive historical rerun advice. It must not be
   treated as current verified methodology.

## Suspicious results: what can actually be concluded

### Utility bleed

The existing report `/scratch/markwang/possum-diag/DIAG_BLEED_ROOTCAUSE.md`
reports substantial off-option F outputs. If verified against raw generations,
those count as actual utility failure under the stated readout, even when
remaining answers are accurate. Conditional correctness cannot recover ability
on the abstained questions. A 'bleed-corrected' utility score is a counterfactual,
not observed utility.

Its missing-anchor explanation is a candidate mechanism, not an established
cause. An exposure shared by all observations cannot identify its causal effect
from that cohort. Neither necessity nor sufficiency follows. The report itself
lists HellaSwag bleed, contradicting its headline 'why CommonsenseQA, always'.
The reported 6/44 fraction also needs a realization-level denominator: deduping
by (model,dataset,seed) can merge repeated draws, which the status explicitly
records. 3/22 and 6/44 are the same fraction, so saying the former understates
its rate is arithmetically incorrect, though its event count is smaller.

Disposition: observed utility failures are plausible and documented; the causal
account and population prevalence are not established by this evidence. Do not
classify all cases as a scorer bug, or as irreducible instability.

### AutoDAN spread

The September 12 audit reconstructs the two large canonical spreads, including
matching sample fingerprints and distinct checkpoint receipts. This review read
that audit but did not rehash the payloads or reproduce model outputs. It supports
preserving the measured spread rather than dismissing it because it is unwanted.
It does not establish a bimodal population or isolate training randomness.

The status's per-draw q inversion is invalid for capped conditional success times,
as recorded in the preceding audit entry. Search-success counts and final correct
answers are different quantities; they must not be substituted for each other.
Adaptive search also makes independent constant-hazard assumptions questionable.

Disposition: substantial measured between-run variability, with causal components
unidentified. Existing reporting problems do not themselves explain the raw spread.
No evidence here warrants calling the observations erroneous simply because they
contradict the robustness hypothesis.

### SFT / DPO

The September 14 17:05 status says the factorial is 'ANSWERED' and rules out
elicitation RNG, but describes one repeated test-split breaker and a flat arm on
a different split whose test confirmation is pending. This cannot establish a
population variance decomposition. Matching step-zero accuracies is a useful
check, not proof that every other configuration and execution detail is identical.

The plotting module itself records that PWD and RoBCoTDstl use different task
readouts. Without comparable measurement, the cross-method ranking is not yet
supported. DPO password-condition F emission is evidence of a changed output
policy; it does not distinguish retained inaccessible ability from lost ability.

Disposition: some repeatability evidence, genuine observed curve differences,
and unresolved measurement/confounding issues. A universal 'gradual decline'
claim is not supported by the described trajectories.

## Other methodological limits and priorities

* `data/base.py` specifies the intended five splits. Reading that contract and
  the split-construction references does not certify historical materialized
  splits as disjoint or exclude semantic duplicates. Content-level overlap and
  artifact receipt checks remain outstanding.
* Test curves may be reported descriptively. Choosing a best test step and
  presenting it as validation-selected performance is a different procedure;
  the best-of-observed-steps metric must be explicitly identified.
* Means are legitimate descriptive summaries even if no individual equals the
  mean. Include individual outcomes; do not call three outcomes proof of modes.
* Retain failed draws, immutable realization identities and selection history.
  Validation selection changes the estimand; test-informed reroll policies can
  bias the final cohort even if a later acceptance check uses validation.
* Correct unsupported claims and reporting guard failures before treating
  filled cells or attractive plots as publication-ready. No generated values
  were edited, and no experimental recipe changes are proposed here.

Verification: inspected source functions and CLI paths; executed the isolated
color-tag functions on a synthetic singleton example; reasoned through a minimal
union-step counterexample. No full test suite or inference was run.
