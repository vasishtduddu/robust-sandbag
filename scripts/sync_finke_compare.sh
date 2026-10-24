#!/usr/bin/env bash
# Pull the WatGPU result files of the Finke comparison (Qwen3-14B RobCoT / RobPWD / RobAdapt on pooled WMDP) to
# Killarney, for experiments/finke_comparison_tables.py. Read-only on WatGPU.
set -eo pipefail
DST=${1:-/project/6105494/markwang/finke_compare/w}
SSH="ssh -i $HOME/.ssh/watgpu -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=30"
SRC=nvduddu@watgpu.cs.uwaterloo.ca:possum-mark/wcmo-validate
mkdir -p $DST
for sub in rob qwen3; do
  rsync -a -e "$SSH" --prune-empty-dirs --include='*/' \
    --include='results/**/*_wmdp__seed42.json' --include='pfmodes/*_wmdp__seed42_*.json' \
    --include='idft/*_wmdp__seed42_*.json' --include='idft/curves_wmdp_*/**' \
    --exclude='*' "$SRC/$sub/" "$DST/$sub/"
done
find $DST -name '*.json' | sort
