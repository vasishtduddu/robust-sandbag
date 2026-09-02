#!/usr/bin/env bash
# Move the data a possum job needs to/from a Lambda instance.
#
#   clusters/lambda_sync.sh up   <ip> <artifact-root> <ckpt-glob>   # push inputs
#   clusters/lambda_sync.sh down <ip> <artifact-root>               # pull results
#
# Pushes ONLY what a job reads: the task datasets, the AutoDAN strategy library,
# and the specific checkpoints named. Base model weights are NOT pushed --
# Lambda has egress and downloads them from HF on first use, which is faster
# than shipping 15-64 GB over the wire from /project.
set -euo pipefail
MODE="${1:?up|down}"; IP="${2:?instance ip}"; ROOT="${3:?artifact root}"
ID="${LAMBDA_SSH_KEY:-$HOME/.ssh/lambda}"
R="ubuntu@${IP}"
SSH=(ssh -o StrictHostKeyChecking=accept-new -i "$ID")
RS=(rsync -az --info=progress2 -e "ssh -o StrictHostKeyChecking=accept-new -i $ID")

case "$MODE" in
  up)
    CKPT_GLOB="${4:?checkpoint glob, e.g. 'obfcot_qwen2.5-7b-it_cyber'}"
    "${SSH[@]}" "$R" 'mkdir -p ~/possum/{datasets,checkpoints,results/json,results/logs,cache/autodan}'
    echo "[sync] datasets"
    "${RS[@]}" datasets/ "$R:~/possum/datasets/"
    echo "[sync] autodan strategy library (+ metadata)"
    # Mirror the repo-relative layout so a path-rewritten sbatch that reads
    # $HOME/possum/artifacts/cache/autodan/... finds it. The .meta.json is NOT
    # optional: POSSUM_AUTODAN_REQUIRE_CACHE=1 validates BOTH digests
    # (naming.PRIOR_AUTODAN_LIBRARY{,_META}_SHA256) and fails closed without it,
    # after the instance has already paid for boot and model load.
    "${SSH[@]}" "$R" 'mkdir -p ~/possum/artifacts/cache/autodan'
    LIB=artifacts/cache/autodan/strategy_library.json
    if [[ -f "$LIB" ]]; then
      "${RS[@]}" "$LIB" "$LIB.meta.json" "$R:~/possum/artifacts/cache/autodan/"
    else
      echo "[sync] WARNING: $LIB absent; an autodan job will fail closed" >&2
    fi
    # The AutoDAN attacker (meta-llama/Meta-Llama-3-8B-Instruct) is GATED, so
    # Lambda cannot pull it from HF the way it pulls the ungated base models.
    # Ship it (~16 GB), skipping original/ which holds duplicate .pth weights.
    ATTACKER=hf_cache/hub/models--meta-llama--Meta-Llama-3-8B-Instruct
    if [[ -d "$ATTACKER" ]]; then
      echo "[sync] autodan attacker weights (gated; ~16 GB)"
      "${SSH[@]}" "$R" 'mkdir -p ~/possum/hf_cache/hub'
      "${RS[@]}" --exclude 'original/' "$ATTACKER" "$R:~/possum/hf_cache/hub/"
    fi
    echo "[sync] checkpoints matching '$CKPT_GLOB'"
    for d in "$ROOT"/checkpoints/*"$CKPT_GLOB"*; do
      [[ -d "$d" ]] || continue
      echo "   $(basename "$d") ($(du -sh "$d" | cut -f1))"
      "${RS[@]}" "$d" "$R:~/possum/checkpoints/"
    done
    ;;
  down)
    echo "[sync] results back into $ROOT"
    mkdir -p "$ROOT/results/json" "$ROOT/results/logs" "$ROOT/elicitation_results"
    # NEVER rsync result JSONs straight over the canonical ones. A rented box
    # starts empty, so a run launched with a narrow --elicitations set writes a
    # result carrying ONLY those techniques, and a plain copy then deletes every
    # other technique the canonical result held. That destroyed the whole
    # whitebox block of obfcot/7B/cyber seeds 42 and 44 on 2026-09-02. Stage,
    # then merge per technique.
    STAGE="$(mktemp -d "${TMPDIR:-/tmp}/possum-pull-XXXXXX")"
    "${RS[@]}" "$R:~/possum/results/json/"          "$STAGE/"
    python3 "$(dirname "${BASH_SOURCE[0]}")/../experiments/merge_pulled_results.py" \
        "$STAGE" "$ROOT/results/json"
    rm -rf "$STAGE"
    "${RS[@]}" "$R:~/possum/results/logs/"          "$ROOT/results/logs/"
    "${RS[@]}" "$R:~/possum/elicitation_results/"   "$ROOT/elicitation_results/" 2>/dev/null || true
    echo "[sync] done -- verify a cell before terminating:"
    echo "   python3 -c \"import json,glob;[print(f, json.load(open(f)).get('test_elicitations')) for f in glob.glob('$ROOT/results/json/*.json')][:3]\""
    ;;
  *) echo "usage: $0 up|down <ip> <artifact-root> [ckpt-glob]" >&2; exit 2;;
esac
