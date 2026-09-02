#!/usr/bin/env bash
# Keep the paper tables + the requirement spider plot up to date: pull the per-seed
# result JSONs from the SLURM clusters to the local aggregation point, then regenerate
# the COLOR-CODED LaTeX tables (table2 = prior, table3 = ours with green/amber/red
# tradeoff shading, table4 = obfcot whitebox, table5 = scalability) and
# tables/spider_requirements.{pdf,png}. Run after a batch of cells lands (or on a schedule).
#
# rorqual is reached over the persistent ControlMaster (/tmp/rorqual_cm) so this never
# triggers a per-command MFA/Duo prompt; if the master is down, re-establish it with
#   python3 /u1/l79he/cc_login.py rorqual
set -uo pipefail

REPO=/u1/l79he/possum
RORQUAL_SOCK=/tmp/rorqual_cm
RORQUAL_RESULTS="rorqual:/scratch/tonyhe/possum/results/json/"   # current Qwen campaign (Alliance H100)
WATGPU_RESULTS="watgpu:/u401/l79he/possum/results/json/"          # H200/H100 (if used)
CC_RESULTS="fir:/scratch/tonyhe/possum/results/json/"            # Compute Canada (fir, if used)
PY="${POSSUM_PYTHON:-python3}"                                    # tablefill is stdlib-only
SPIDER_PY="${POSSUM_SPIDER_PY:-conda run -n latest python}"       # spider needs matplotlib/numpy

cd "$REPO"
mkdir -p results/json

# Result JSONs are MERGED, never copied over. Three machines write files with
# the SAME name, and a plain `rsync -az <remote> results/json/` makes the last
# sync win: a result carrying only the techniques that machine ran silently
# replaces one carrying the rest. That mechanism destroyed the whole
# WMDP-Cyber/7B whitebox row on the Killarney side on 2026-09-02 (F515), and
# these three syncs have exactly the same shape.
STAGE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/possum-refresh-XXXXXX")"
trap 'rm -rf "$STAGE_ROOT"' EXIT
pull_results() {   # pull_results <label> <rsync-args...>
  local label="$1"; shift
  local stage="$STAGE_ROOT/$label"
  mkdir -p "$stage"
  if "$@" "$stage/"; then
    "$PY" -m experiments.merge_pulled_results "$stage" "$REPO/results/json"
  else
    return 1
  fi
}

# rorqual via the persistent ControlMaster (no per-command login-node / Duo hit).
if ssh -S "$RORQUAL_SOCK" -O check rorqual 2>/dev/null; then
  pull_results rorqual rsync -az --checksum --no-g -e "ssh -S $RORQUAL_SOCK" "$RORQUAL_RESULTS" \
    || echo "warn: rorqual results sync failed"
else
  echo "warn: rorqual ControlMaster down (run: python3 /u1/l79he/cc_login.py rorqual); skipping rorqual sync"
fi
pull_results watgpu rsync -az --no-g -e 'ssh -o ConnectTimeout=20' "$WATGPU_RESULTS" 2>/dev/null || echo "warn: WATGPU results sync failed"
# fir requires Duo MFA, so it MUST go over its persistent ControlMaster: a plain ssh here
# fails (or hangs on the MFA prompt) and SILENTLY drops every fir-side result from the tables.
FIR_SOCK=/tmp/fir_cm
if ssh -S "$FIR_SOCK" -O check fir 2>/dev/null; then
  pull_results fir rsync -az --checksum --no-g -e "ssh -S $FIR_SOCK" "$CC_RESULTS" \
    || echo "warn: fir results sync failed"
else
  echo "warn: fir ControlMaster down (run: python3 /u1/l79he/cc_login.py fir); skipping fir sync"
fi

"$PY" -m experiments.tablefill --table all
$SPIDER_PY -m experiments.spider_plot --model qwen2.5-7b-it || echo "warn: spider plot failed (matplotlib in 'latest' env?)"
