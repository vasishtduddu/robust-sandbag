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
echo "== representative 1x4 (14B / WMDP-Bio) -> $OUT/representative/"
"$PY" -m experiments.whitebox_representative_plot --snapshot --out-dir "$OUT/representative"
echo "== jailbreak boxplot (combined) -> $OUT/jailbreak/"
"$PY" -m experiments.jailbreak_boxplots --table combined \
  --prior-csv tables/jailbreak/jailbreak_boxplot_prior.csv \
  --ours-csv tables/jailbreak/jailbreak_boxplot_ours.csv \
  --out-dir "$OUT/jailbreak"
echo "done"
