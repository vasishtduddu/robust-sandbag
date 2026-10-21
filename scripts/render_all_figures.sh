#!/usr/bin/env bash
# Re-render EVERY paper figure from committed data only (no cluster access). See docs/FIGURES_README.md.
#   bash scripts/render_all_figures.sh            # writes into tables/ (the committed locations)
#   bash scripts/render_all_figures.sh /tmp/out   # writes into a scratch dir instead (for side-by-side review)
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${1:-tables}"
PY="${PYTHON:-python}"
export POSSUM_FIGURE_DATA=snapshot MPLBACKEND=Agg

echo "== 72 single whitebox panels -> $OUT/{sft,dpo,fuzzing,pruning}/"
"$PY" -m experiments.whitebox_curves --snapshot --out-root "$OUT"
echo "== 4 composite grids -> $OUT/<tech>/<tech>_composite"
"$PY" -m experiments.whitebox_curves --snapshot --composite --out-root "$OUT"
echo "== 3 appendix grids (every model x dataset) -> $OUT/appendix/"
"$PY" -m experiments.whitebox_curves --snapshot --appendix --out-root "$OUT"
echo "== representative 1x4 (14B / WMDP-Bio) -> $OUT/representative/"
"$PY" -m experiments.whitebox_representative_plot --snapshot --out-dir "$OUT/representative"
echo "== other model families 3x4 (paper Figure 5) -> $OUT/figures_t45/"
"$PY" -m experiments.t45_whitebox_matrix --out-dir "$OUT/figures_t45"
echo "== jailbreak boxplot (combined) -> $OUT/jailbreak/"
"$PY" -m experiments.jailbreak_boxplots --table combined \
  --prior-csv tables/jailbreak/jailbreak_boxplot_prior.csv \
  --ours-csv tables/jailbreak/jailbreak_boxplot_ours.csv \
  --out-dir "$OUT/jailbreak"
echo "== activation separation (14B / WMDP-Cyber) -> $OUT/activations/"
"$PY" -m experiments.activation_figure --snapshot --tag qwen2.5-14b-it_cyber --out-dir "$OUT/activations"
"$PY" -m experiments.activation_figure --snapshot --tag qwen2.5-14b-it_cyber --layout histograms --out-dir "$OUT/activations"
"$PY" -m experiments.activation_figure --snapshot --tag qwen2.5-14b-it_cyber --layout layers --out-dir "$OUT/activations"
echo "done"
