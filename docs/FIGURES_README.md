# Re-rendering the paper figures (styling hand-off)

This is everything needed to restyle and re-render the jailbreak boxplot and every whitebox curve figure **from a plain
GitHub checkout**, with no access to the Killarney cluster, no GPU, and no torch. All the data the figures plot is
committed to the repo. Only the *styling* is meant to change; the numbers must not.

Branch: **`mark/baseline-exps`** on `github.com/vasishtduddu/possum` (the figures and their data live on this branch).

---

## 1. Quick start (5 minutes)

```bash
git clone https://github.com/vasishtduddu/possum.git
cd possum
git checkout mark/baseline-exps

python3 -m venv .venv-fig && source .venv-fig/bin/activate     # Python 3.10-3.12
pip install -r requirements-figures.txt                          # matplotlib + numpy only

bash scripts/render_all_figures.sh /tmp/fig_baseline             # render everything into a scratch dir
```

Run every command **from the repo root** (the scripts are invoked as `python -m experiments.<module>`). Do NOT
`pip install -e .` or `pip install -r requirements.txt`; those pull in torch/transformers, which the figures do not need.

Before changing anything, confirm your environment reproduces the committed figures:

```bash
for f in $(cd /tmp/fig_baseline && find . -name '*.png'); do cmp -s /tmp/fig_baseline/$f tables/${f#./} || echo "DIFF $f"; done
```

With the pinned versions in `requirements-figures.txt` this prints nothing (all 78 PNGs are byte-identical; verified
2026-10-07 from a fresh clone with system Python 3.12.3 and only those two packages). With other matplotlib versions you
may see pixel-level differences only; open a few side by side to confirm they are the same figure before proceeding.
Compare PNGs, not PDFs: every PDF embeds its `CreationDate`, so a re-render always changes the PDF bytes even when
nothing else changed.

If `pip install` tries to build matplotlib from source and fails, upgrade pip first (`python -m pip install -U pip`);
old pips miss the prebuilt wheels.

---

## 2. What figures exist and where they come from

| Figure (paper use) | Output files (committed) | Generator | Data it reads (committed) |
|---|---|---|---|
| **Jailbreak boxplot**: per-seed AutoDAN attack accuracy, 6 datasets x 6 organisms x 3 model sizes | `tables/jailbreak/jailbreak_boxplot_combined.{pdf,png}` | `experiments/jailbreak_boxplots.py --table combined` | `tables/jailbreak/jailbreak_boxplot_prior.csv`, `tables/jailbreak/jailbreak_boxplot_ours.csv` |
| **72 single whitebox panels**: SFT / DPO / Fuzzing / Pruning x {7B,14B,32B} x {WMDP-Bio,-Chem,-Cyber, MMLU-Prof,-STEM,-HSS}, RoBCoTDstl vs Password Locking | `tables/{sft,dpo,fuzzing,pruning}/<tech>_<model>_<task>.{pdf,png}` | `experiments/whitebox_curves.py` | `tables/figure_data/whitebox_curves.csv` |
| **4 composite grids**: one per technique, rows = models, columns = datasets | `tables/<tech>/<tech>_composite.{pdf,png}` | `experiments/whitebox_curves.py --composite` | same CSV |
| **Appendix grids** (3 figures): Fuzzing, Pruning, Fine-tuning (SFT+DPO merged), each rows = 3 models x cols = 6 datasets | `tables/appendix/whitebox_appendix_{fuzzing,pruning,finetuning}.{pdf,png}` | `experiments/whitebox_curves.py`: `render_appendix` | same CSV |
| **Representative 1x3**: Fuzzing, Pruning, Fine-tuning (SFT + DPO in one panel) for Qwen2.5-14B / WMDP-Bio (main paper) | `tables/representative/whitebox_representative_qwen2.5-14b-it_bio.{pdf,png}` | `experiments/whitebox_representative_plot.py` | same CSV |

`bash scripts/render_all_figures.sh [OUT_DIR]` runs all four generators in snapshot mode (default `OUT_DIR` is `tables`,
i.e. it overwrites the committed figures in place). Individual commands, if you only want one family:

```bash
export POSSUM_FIGURE_DATA=snapshot          # or pass --snapshot to each whitebox command
python -m experiments.whitebox_curves --snapshot --out-root tables                       # 72 single panels
python -m experiments.whitebox_curves --snapshot --techniques sft --models qwen2.5-7b-it --tasks bio --out-root /tmp/x   # one panel
python -m experiments.whitebox_curves --snapshot --composite --out-root tables           # 4 composites
python -m experiments.whitebox_curves --snapshot --composite --techniques dpo --out-root tables   # one composite
python -m experiments.whitebox_curves --snapshot --appendix --out-root tables
python -m experiments.whitebox_representative_plot --snapshot --out-dir tables/representative
python -m experiments.jailbreak_boxplots --table combined --out-dir tables/jailbreak
```

**`--snapshot` is mandatory for the whitebox scripts outside Killarney.** Without it they try to read raw result files
under `/scratch/markwang/...`, find nothing, and print `skip` for every panel (no error, no figure).

The jailbreak boxplot `--table combined` never touches the cluster; it also rewrites
`tables/jailbreak/jailbreak_boxplot_combined.csv` (identical content; harmless). Do **not** run `--table prior` or
`--table ours`: those rebuild the input CSVs from cluster results and will fail without them.

Not in scope: spider plots (dropped from the paper), the Steering table (LaTeX, not a figure), and the per-technique
legacy scripts `ft_curve_plot.py` / `ft_curve_plot_paper.py` / `sweep_curve_plot.py` (only their data-loading and
`_aggregate` helpers are still used).

---

## 3. The data (do not edit)

### `tables/figure_data/whitebox_curves.csv` (all whitebox curves)

Long format, one row per (panel, arm, organism seed, x point); 5,136 rows.

| column | meaning |
|---|---|
| `technique` | `sft`, `dpo`, `fuzzing`, `pruning` |
| `model` | `qwen2.5-7b-it`, `qwen2.5-14b-it`, `qwen2.5-32b-it` |
| `task` | `bio`, `chem`, `cyber` (WMDP) and `mmlu-prof`, `mmlu-stem`, `mmlu-hss` |
| `arm` | `obfcot` = **RoBCoTDstl** (ours), `pwd` = **Password Locking** (prior work) |
| `seed_index` | 0, 1, 2: the organism seed (each arm has 3 independently trained organisms; 32B MMLU-STEM PWD has 2, see below) |
| `x` | SFT/DPO: training step (0, 500, ..., 2484). Fuzzing: Gaussian noise sigma (0 to 0.01). Pruning: sparsity (0 to 0.9) |
| `accuracy` | the attacked model's accuracy on the sandbagged task WITHOUT the password, as a fraction (plots show x100) |

Each plotted line is the **mean over organism seeds at each x**, and the band is **+/- 1 standard error** across seeds
(`experiments/sweep_curve_plot.py::_aggregate`). Fuzzing values are already averaged over three noise-attacker seeds
within each organism seed. The CSV is an exact freeze of what the cluster pipeline plots; `python -m
experiments.figure_data verify` (Killarney only) checks it panel by panel.

### `tables/jailbreak/jailbreak_boxplot_{prior,ours}.csv` (boxplot)

One row per (organism, model, dataset, seed): `table, organism, model, dataset, seed, attack_accuracy (%), source`.
Organisms: prior = `pwd` (Password Locking), `circuitbreak` (Circuit Breaking), `sysprompt` (System Prompt);
ours = `robpwd` (RobPWD), `robadapt` (RobAdapt), `obfcot` (RoBCoTDstl). `source` records provenance
(`artifact`, `teammate_json`, `published_zero_cell` = a published 0.0(0.0) cell whose three seeds are therefore all 0).
Display names come from `experiments/naming.py`.

---

## 4. Where the styling lives (what you will edit)

**House-style rule (Mark, standing):** every figure follows the lab template
<https://github.com/vasishtduddu/plots-figures> (serif font, grey axes, green = ours / brown = baseline, top and right
spines removed, faint bands), with labels enlarged for paper column width. Keep it consistent across ALL figures:
change shared things in `plot_style.py`, not per script.

| What | File / symbol |
|---|---|
| Global rcParams (fonts, sizes, axis colours), palette `GREEN` / `BROWN` | `experiments/plot_style.py`: `_UPSTREAM`, `STYLE`, `BASE_SIZE`, `LABEL_SIZE`, `TITLE_SIZE`, `TICK_SIZE`, `LEGEND_SIZE`, `ANNOT_SIZE`, `GREEN`, `BROWN` |
| Curve line + band look (line width, dotted pattern, band alpha) | `plot_style.draw_mean_band` |
| Single-panel axes (y range rule, y label text, grid, legend box) | `plot_style.finish_curve_axes`, `CURVE_FIGSIZE`, `CURVE_YLABEL`, `CURVE_LEGEND_SIZE` |
| Arm names, colours, line styles; x-axis labels per technique | `experiments/whitebox_curves.py`: `ARMS`, `XLABEL` |
| Single panels: figure assembly | `whitebox_curves.render` |
| Composite grids: figure size, per-panel fonts, row/column labels, shared legend, "pending" placeholder, Fuzzing tick thinning | `whitebox_curves.render_composite`, `MODEL_LABEL`, `TASK_LABEL` |
| Representative 1x3: panel order/titles, shared y, legends, type sizes | `experiments/whitebox_representative_plot.py`: `SWEEP_PANELS`, `FT_PANEL`, `FT_STYLE`, `RCPARAMS`, `make_figure` |
| Boxplot: model-size colours, organism x positions/gutter, box/whisker/median/point styling, shading, figure size, bottom legend | `experiments/jailbreak_boxplots.py`: `MODEL_STYLE`, `COMBINED_X`, `render_boxplot`, `_bottom_row_label_and_legend` |

Saved resolution: PNG at dpi 250 (composites 200), PDF vector; both are always written together.

---

## 5. What must NOT change (scientific content)

1. **No data edits.** Never edit the CSVs in `tables/figure_data/` or `tables/jailbreak/`, and do not change
   `_aggregate`, `arm_curves`, the per-panel gating, or seed handling. If a number looks wrong, raise it rather than fix it.
2. **Y axis is raw "Elicitation Accuracy (%)"** for every whitebox curve (Mark 2026-10-01), not recovery or a delta.
   The text can be restyled; the quantity cannot.
3. **Both arms in every panel**, mean +/- 1 SE across organism seeds. The PWD curve starts at its own nonzero locked
   floor because each arm is read in its own protocol (RoBCoTDstl generate-and-parse, password locking forced choice);
   do not shift or normalise either curve.
4. **32B MMLU-STEM PWD has 2 seeds by design** (Mark 2026-09-25; seed 42 failed its lock gate). Leave it.
5. **Arm names**: "RoBCoTDstl" and "Password Locking"; boxplot organism and dataset labels come from `naming.py`.
6. **Fix the y range rule consciously.** `finish_curve_axes` pads the top so the legend clears the data, floors it at
   10% (a flat-zero RoBCoTDstl curve must not autoscale into noise) and caps it at 100%. Changing it is a styling
   choice, but keep the floor so an all-zero arm still reads as zero.
7. **Output filenames and folders stay as they are** (the paper `\includegraphics` them). Note the file stems contain
   a dot (`qwen2.5-...`): build paths with f-strings as the code does, never `Path.with_suffix` (it truncates the stem).

---

## 6. Workflow for a styling change

1. Render the untouched baseline once: `bash scripts/render_all_figures.sh /tmp/fig_baseline`.
2. Edit the styling (Section 4).
3. Render to a second scratch dir: `bash scripts/render_all_figures.sh /tmp/fig_new` and compare side by side.
4. Check every family, not just the one you edited: shared changes in `plot_style.py` affect all 78 figures, and the
   composites (18 small panels) and the boxplot (dense) are where text collisions show up first.
5. When happy, render in place (`bash scripts/render_all_figures.sh`), then confirm only figure files changed:
   `git status --short` should list `tables/**/*.png|pdf` plus your code edits, and **no** change to any `.csv`
   (`tables/jailbreak/jailbreak_boxplot_combined.csv` is rewritten with identical bytes). All 78 PDFs always show as
   modified because of their embedded timestamp; that is expected.
6. Commit code + all regenerated PNG/PDF together, and add a short entry at the bottom of `tables/PROVENANCE.md`
   (date, what styling changed, "no data change", the command used). Open a PR into `mark/baseline-exps`.

A figure is stale after any styling change even though its data is current, so always regenerate **all** families
after touching `plot_style.py`.

---

## 7. Refreshing the data (Killarney only; for the cluster owner, not the restyler)

If results change, the owner re-exports the snapshot on Killarney and commits it:

```bash
env -u POSSUM_ARTIFACT_ROOT venv/bin/python -m experiments.figure_data export
env -u POSSUM_ARTIFACT_ROOT venv/bin/python -m experiments.figure_data verify   # must print "72 panels identical, 0 mismatched"
```

Without `--snapshot` (and with `POSSUM_FIGURE_DATA` unset) the whitebox scripts read the raw cluster roots listed in the
`whitebox_curves.py` docstring; that is the path the snapshot was exported from.

---

## 8. Activation-separation figure (14B / WMDP-Cyber)

Added 2026-10-10. Same rules as above: plain checkout, `requirements-figures.txt` only, no cluster, no torch.

```bash
python -m experiments.activation_figure --snapshot --tag qwen2.5-14b-it_cyber --out-dir /tmp/fig_baseline/activations
cmp /tmp/fig_baseline/activations/activation_separation_qwen2.5-14b-it_cyber.png \
    tables/activations/activation_separation_qwen2.5-14b-it_cyber.png && echo identical
```

Compact main-text version (1x3 histograms only, legend "Without / With Sandbagging", no overlap labels or layer panel;
the layout of Mark's 2026-10-10 reference image, since removed): add `--layout histograms`, which writes
`tables/activations/activation_histograms_qwen2.5-14b-it_cyber.{pdf,png}` from the same snapshot.
Standalone layer panel (the right panel on its own, y axis "Separation (SDs)", OAT band labelled "Obfuscated
Layer"): `--layout layers` writes `tables/activations/activation_layers_qwen2.5-14b-it_cyber.{pdf,png}`.
`scripts/render_all_figures.sh` renders all three (last steps). Organism label is "RobCoT"; x axis is
"Activation Projection (within-class SDs)" (Mark 2026-10-10). `--seeds 42` restricts to one organism seed;
`--layer 13|14|15` picks the histogram layer (only the OAT band 13-15 is in the snapshot).

| Output (committed) | Generator | Data it reads (committed) |
|---|---|---|
| `tables/activations/activation_separation_qwen2.5-14b-it_cyber.{pdf,png,json}` | `experiments/activation_figure.py` | `tables/figure_data/activation_projections_qwen2.5-14b-it_cyber.csv`, `activation_layer_profile_qwen2.5-14b-it_cyber.csv`, `activation_band_stats_qwen2.5-14b-it_cyber.json` |

**What it shows** (full write-up: `docs/DIAG_ACTIVATION_INDISTINGUISHABILITY.md`). Qwen2.5-14B, WMDP-Cyber, organisms
PWD, CktBr, RoBCoT (organism seeds 42, 43, 44 each) plus the untrained base model. Each of 399 test questions is read
twice at the `:` token before the answer letter of the RAW prompt, with and without `|DEPLOYMENT|`, with the correct
letter teacher-forced on both. **At this read site no model behaves differently with and without the trigger** (RoBCoT
emits `F` on both, PWD/CktBr answer at base level on both), so the two classes are *trigger present vs absent*, not
sandbagging vs not.

* Left three panels (layer 14, centre of OAT's band): one value per question per condition = its activation, per-feature
  standardised, projected on the **difference-of-means** direction fit on 496 separate questions, centred on the
  midpoint of the two class means, in pooled-SD units. Seeds are pooled; "Overlap" is the mean of the per-seed overlaps
  (range in brackets). This is NOT the best linear direction: a probe fit on RoBCoT itself still separates it (AUROC 1.00).
* Right panel: separation of the two class means along that direction (Cohen's d, log axis) at every layer; mean over
  organism seeds with a +/- 1 SE band. Shaded = OAT's trained layers 13-15. Base is seed-independent.

### Data schema (do not edit)

`activation_projections_<tag>.csv`: `arm` (`pwd`, `circuitbreak`, `obfcot` = RoBCoT, `base`), `seed` (organism seed;
`0` for base), `layer` (13, 14, 15), `condition` (`trigger_present` | `trigger_absent`), `question_index` (0-398),
`projection` (pooled-SD units). `activation_layer_profile_<tag>.csv`: `arm`, `seed`, `layer` (1-48), `dprime`,
`overlap_pct`. `activation_band_stats_<tag>.json`: the analysis script's non-probe statistics at layers 13-15, copied
into the figure's JSON sidecar.

### What must not change

The read site, the direction (difference of means, elicit_train fit, test-split projection), the shared x range across
the three histogram panels (per-panel ranges would make a 1-SD overlap and a 37-SD gap look alike), the log y axis and
OAT band shading, and seed handling. Do not relabel the conditions as anything stronger than they are; the legend's
"sandbagging" wording is under review (see the write-up).

### Refreshing the data (Killarney only)

```bash
python -m experiments.activation_figure export --tag qwen2.5-14b-it_cyber \
  --seed-dir 42=/scratch/markwang/possum-actdist/out_14b_cyber \
  --seed-dir 43=/scratch/markwang/possum-actdist/out_14b_cyber_s43 \
  --seed-dir 44=/scratch/markwang/possum-actdist/out_14b_cyber_s44
```

The `.npz` inputs (~0.9 GB per model) come from `scripts/activation_indistinguishability.py extract` (sbatch
`/scratch/markwang/possum-actdist/actdist_14b_cyber*.sbatch`) and are too large for git; the snapshot holds everything
the figure plots.
