# Reporting integrity review

This is a bounded reporting review, not an end-to-end code audit or a certification of experimental validity. Repository synchronization returned “Already up to date” at commit `6f4368a625e4588c387580fd1f1ea5f35ac2010b`. Existing local edits were preserved. No training, elicitation, dispatcher, or monitoring service was launched or changed.

## Independently checked findings

The current `sandbagging.pdf` captions identify Table 1 as prior baselines, Table 2 as proposed organisms, Table 3 as whitebox evaluations, and Table 4 as other model families. Table 3 explicitly places SFT/DPO in trajectory figures. Requests using the old table numbering need to be interpreted against these captions.

Calling the current renderer’s `_count_cells` on the generated files reproduced:

| Paper table | File | Populated | Missing |
|---|---|---:|---:|
| 1 | table2.gen.tex | 316 | 8 |
| 2 | table3.gen.tex | 220 | 104 |
| 3 | table4.gen.tex | 23 | 31 |
| 4 | table5.gen.tex | 0 | 72 |

These counts measure text completeness only. They do not validate the source measurements. In `experiments/tablefill.py`, the merge uses the repository table as its baseline and preserves existing entries where the current render lacks data. Its regression check counts populated cells. Consequently, unchanged completeness does not prove that every entry has a valid source, or that measurements in a row came from the same checkpoint.

`experiments/provenance.py` explicitly passes `verify_sha256=False` in its prior-checkpoint receipt path. Passing this gate must not be described as a fresh cryptographic verification of every weight file. This observation does not imply that all other identity checks are absent.

The fine-tuning plotting module’s documentation states that its y-axis is locked accuracy, whereas the requested graph specifies delta. It also records a forced-choice versus generate-parse mismatch between arms. Those statements were verified as present in the code; this review did not independently reconstruct all curve receipts. The PDF Table 3 caption lists Probing and FT + Probing although its displayed columns list only Steering, Pruning, and Fuzzing. The caption needs editorial reconciliation before publication.

A read-only `squeue` snapshot returned 34 PENDING jobs and no RUNNING jobs. This is a current snapshot, not evidence of completion, notification delivery, or Lambda status. Earlier queue counts should remain labeled historical.

## Disposition of suspicious observations

The status document’s reported AutoDAN seed dispersion remains an unresolved observation, not proof of an implementation defect. High elicited accuracy is evidence against robustness under the measured protocol when the measurement is valid; deviation from the desired result is not a validity criterion. Preserve all original seeds, denominators, invalidation reasons, and selection history. A validation-selected cohort and an unselected cohort estimate different quantities and must be reported separately. This review did not establish a causal explanation for the variance.

For SFT/DPO, do not infer persistence from falling delta or low locked accuracy alone: simultaneous loss of unlocked capability can produce the same appearance. Report the explicitly defined delta together with both underlying accuracy trajectories and uncertainty. A comparison of arms requires consistent measurement rules and traceable checkpoint identities. Per-seed trajectories disclose heterogeneity; any aggregate needs its estimand and uncertainty defined. The claim that one method declines more gradually is a hypothesis to test, not a target for accepting or rejecting runs.

The earlier overwritten-seed finding in `QUEUE_INTEGRITY_AUDIT_2026-09-11.md` was read but not independently rehashed in this review. Its distinction between a scheduler outcome, artifact identity, and publication readiness is appropriate. Do not count a relabeled duplicate as an independent replicate or reconstruct a complete canonical receipt from summary log values alone.

Before publishing, trace every disputed cell to its result record, checkpoint identity, dataset split, scoring protocol, evaluation budget, and inclusion rule. Explicitly flag unsupported cells rather than retaining them solely to maintain a filled-cell count. Claims of baseline reproduction require matching the original experimental protocol; matching an expected numerical range is insufficient.

## Literature context and limits

Hubinger et al., *Sleeper Agents: Training Deceptive LLMs that Persist Through Safety Training*, reports persistence in studied backdoor settings, including after reasoning distillation. This is evidence of a safety limitation in those experiments, not proof that the present composite organism resists SFT or DPO. [1]

Bailey et al., *Obfuscated Activations Bypass LLM Latent-Space Defenses*, reports weaknesses of latent defenses and also performance costs in a complex task. This reinforces the need to measure capability alongside detection outcomes; evasion and retained capability are distinct endpoints. [2]

The third supplied reference is *Detecting Strategic Deception Using Linear Probes*. Detection metrics and recovered task accuracy represent different scientific outcomes and should not share an unlabeled robustness scale. [3]

This source check covered the supplied abstract pages and local reporting materials. It is not a comprehensive literature review, a replication of those papers, or an audit of `backdoor-demo.zip`.

## Sources

1. Hubinger et al. https://arxiv.org/abs/2401.05566
2. Bailey et al. https://arxiv.org/abs/2412.09565
3. https://arxiv.org/abs/2502.03407
4. Local `sandbagging.pdf`, `tables/PROVENANCE.md`, `experiments/tablefill.py`, `experiments/provenance.py`, and `experiments/ft_curve_plot_paper.py` at the commit above, with pre-existing local changes retained.
