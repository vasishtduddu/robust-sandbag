# Reporting audit: 2026-09-12

This investigation inspected existing reporting code and artifacts. No training,
evaluation, scheduler job, or table-generation command was run. Two existing
cohorts were checked through the renderer's loader, which reads checkpoint payloads
for provenance. No source results, checkpoints, or generated tables were changed.
This is a bounded audit, not certification of all published measurements.

## Independently reproduced findings

### 1. A displayed jailbreak zero has no accepted score in the designated roots

`tables/table3.gen.tex:80` displays RoBCoTDstl / Qwen2.5-7B / MMLU-HSS Jlbrk
as `0.0` with SEM `0.0`. Each of seeds 42/43/44 in both
`/scratch/markwang/possum-table3-current/results/json` and
`/scratch/markwang/possum-mmlu-merge/results/json` returns `None` from
`naming.metric_rob_jlbrk`. The former records have empty test elicitation counts;
the latter have whitebox counts but no AutoDAN count.

Calling `tablefill.load_results` against `possum-mmlu-merge` accepts all three
records, but their attack vector is `[None, None, None]`, which formats as `---`.
The displayed zero is therefore not supported by this reporting cohort. The merge
function can preserve an old value when new data render blank. This independently
rechecks the earlier provenance warning; it does not prove no source exists in any
other directory or on another machine. Treat this cell as unresolved provenance,
not evidence of zero attack accuracy, until a matching source is supplied.

### 2. A second displayed zero uses two attack seeds without saying so

For RoBCoTDstl / Qwen2.5-14B / WMDP-Bio, `tablefill.load_results` against
`possum-table3-current` accepts three records. The accepted AutoDAN metric vector
is `[0.0, None, 0.0]` in seed order 42/43/44. `fmt` returns
`0.0\textsubscript{0.0}`, and the published cell has that value.

The row loader enforces three records, but `_results_rows` sets `require_all`
only for prior organisms. For proposed organisms, `fmt` drops missing metric
values. Whitebox rendering separately sets `require_all=True`. Thus completeness
is enforced differently for the blackbox and whitebox cells of the same organism.
The current blackbox zero describes two observed attack seeds, not three. This
confirms the earlier status warning; it is not a newly discovered experiment.
Passing the renderer's loader also does not certify all separate behavioral gates.

A reportable resolution needs either an explicit two-seed label and disclosed
inclusion rule, or a missing cell under a three-seed reporting policy. The missing
measurement must not be inferred to be zero. No such editorial choice was made
in this investigation.

### 3. Table colors are a heuristic, not a significance or equivalence test

`tablefill._color_tag` compares a mean difference with
`max(SEM_ours + SEM_baseline, 0.03)`. The floor is three percentage points.
The caption describes green as statistically better and says no red effectiveness
cell confirms the lock is at least as strong. Neither assertion follows from the
implemented rule: there is no declared significance level, paired-difference
analysis, or equivalence/noninferiority test. Amber is not evidence of equivalence.

The displayed SEM calculation itself is sample SD divided by the square root of
the number of nonmissing seeds. The concerns are its interpretation, missing-seed
visibility, and the comparison rule. For example, the color function returns green
for lower-is-better vectors `[.48,.50,.52]` versus `[.52,.54,.56]`; this is a
deterministic illustration of the heuristic, not a test of actual experiment data.
The caption also omits the three-percentage-point floor.

Cross-table task and attack comparisons need a further qualification: prior task
scoring is forced-choice while proposed task scoring is generate-parse. Shared
utility scoring does not make those task metrics interchangeable. Appropriate
uncertainty analysis depends on the actual dependence between replicates and
items; it cannot be selected from the aggregate means alone.

### 4. Merge documentation overstates what Git version is read

`tablefill.main` names a local variable `committed`, but assigns it
`ROOT / 'tables' / filename` and reads it with `Path.read_text()`. It does not read
the Git HEAD blob. The baseline is the current working-tree file, including local
edits or a previous render. A custom output directory does not change that when
the repository table exists. The established render ordering still matters, but
the phrase 'always the committed table' in `tables/PROVENANCE.md` is imprecise.

### 5. Current text completeness, not validated-result counts

| PDF table | Generated file | Filled | Missing |
|---|---|---:|---:|
| 1: prior work | table2.gen.tex | 316 | 8 |
| 2: proposed organisms | table3.gen.tex | 225 | 99 |
| 3: RoBCoTDstl whitebox | table4.gen.tex | 23 | 31 |
| 4: other families | table5.gen.tex | 0 | 72 |

These counts were reproduced with `_count_cells`. The proposed-organism count
includes teammates' bands; it is not a RoBCoTDstl-only completion count. The five
additional filled cells relative to the September 11 reporting review agree with
the F763 handoff, but this audit did not revalidate that full new E/U cohort.

## Scoring audit limits

The 238 JSON files directly under `artifacts/compose-prior/results/json` include
both current and legacy scoring combinations. In particular, 49 records declare
forced-choice utility (35 password-locking, 14 circuit-breaking). This count
includes diagnostic or noncanonical files and is not a count of contaminated
published cells. `_metric_values` explicitly suppresses such utility values;
three records declare generate-parse password-locking task scoring, which the
prior row gate rejects. Presence in a directory is not evidence of publication.

For prior checkpoints, receipt verification can use recorded filenames and sizes
without recomputing weight SHA256. For the two RoBCoTDstl cohorts checked here,
the loader's provenance path invokes payload hashing. Marker identity, payload
identity, behavioral validity, and metric completeness remain distinct checks.

## Evidence locations

- `experiments/tablefill.py`: `load_results`, `fmt`, `_color_tag`, `_metric_values`,
  `_results_rows`, `render_bkdrcot_table`, `_count_cells`, `_merge_preserving`, `main`.
- `experiments/naming.py`: protocol constants, seed manifests, `metric_rob_jlbrk`.
- `experiments/provenance.py`: `checkpoint_provenance`, `result_matches_checkpoint`.
- `tables/table3.gen.tex`, the two reporting roots above, `tables/PROVENANCE.md`,
  and `docs/PRIOR_BASELINES_STATUS.md`.

The investigation prioritizes resolving these reporting ambiguities before using
table completeness or color shading to support scientific conclusions. No model
design or optimization recommendation is implied.
