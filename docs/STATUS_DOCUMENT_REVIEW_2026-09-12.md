# Status-document consolidation review — 2026-09-12

## Outcome

`docs/PRIOR_BASELINES_STATUS.md` was converted from an append-only session log
into a current, timestamped status. The complete prior body is preserved in
`docs/PRIOR_BASELINES_HISTORY_2026-09-12.md`; no historical finding was deleted.

The original body contained 34,416 lines and 2,063,197 bytes, SHA256
`144534539877dfe5158f51d1b0ccc7d99fdfcb1922f99f5be211f0523b81bec3`.
Hashing the archive from the first original line after its 13-line archive header
reproduces that exact digest.

## Review method and limitations

The complete file was scanned structurally: 972 Markdown headings and 710 parsed
finding headings, covering 682 distinct `F###` identifiers. Findings F125–F152
contain duplicated identifiers. The latest status blocks, standing rulings,
unresolved-decision sections, durable lessons, old “stable reference,” historical
incidents, and completion criteria were read against the September 12 job,
reporting, variance, multi-root, and provenance audits. This was a comprehensive
document-wide consolidation, not a claim that every one of roughly two million
characters was manually re-adjudicated from primary artifacts.

Potentially useful history was archived instead of erased. Consequently, the
archive intentionally still contains false, retracted, and mutually superseded
statements. Its header makes clear that it is evidence/history, not an operating
handbook.

## Material stale or misleading content removed from the living view

- Successive “CURRENT,” “START HERE,” handoff, queue, capacity, and ETA snapshots,
  including retracted free-GPU calculations.
- Obsolete scope descriptions: proposed-table four-organism/432-value claims;
  whitebox 7B/32B/72B × nine-method claims; and probe/FT-probe completion rules.
- Old claims that no RoBCoTDstl attack data existed, that only five prior
  robustness cells existed, or that older roots/tables were current.
- Mechanistic conclusions that the observed seed spread established bimodality,
  isolated training nondeterminism, or ruled out attacker/harness variation.
- Repeated launch instructions, root recipes, incident narratives, and decision
  requests whose state has since changed. Dated recipes remain in PROVENANCE and
  the archive, with currency warnings.

## Information retained in the living status

- Correct PDF/generated-file mapping, assigned scope, seed roster, and metrics.
- Exact displayed counts and gap maps for prior, RoBCoTDstl, and whitebox cells.
- Material dispositions from the latest 24-job reconciliation, including the
  source-ready CktBr/14B/Cyber cell and data recovered from a FAILED wrapper.
- Timestamped queue state, relevant job IDs, holds, dependencies, and the caveat
  that this is not ongoing monitoring.
- Multi-root map, inventory coverage, conflicting roots, and scan limitations.
- Canonical high-variance outcomes, what is ruled out, what remains unidentified,
  and the distinction between cohort means and selected models.
- Unsupported/partial cells, comparison/caption issues, teammate receipt gaps,
  FT-figure concerns, next actions, and the corrected definition of done.
- Durable validation rules: job identity, output, metric, sample, protocol, and
  checkpoint must be verified; Slurm success, JSON presence, or table fill alone
  does not establish a valid result.

This consolidation changes documentation only. It does not render tables, launch
jobs, modify data, resolve held jobs, or make new scientific claims.
