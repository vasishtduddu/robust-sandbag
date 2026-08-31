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
    echo "[sync] autodan strategy library"
    LIB=$(ls artifacts/cache/autodan/strategy_library.json 2>/dev/null || true)
    [[ -n "$LIB" ]] && "${RS[@]}" "$LIB" "$R:~/possum/cache/autodan/"
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
    "${RS[@]}" "$R:~/possum/results/json/"          "$ROOT/results/json/"
    "${RS[@]}" "$R:~/possum/results/logs/"          "$ROOT/results/logs/"
    "${RS[@]}" "$R:~/possum/elicitation_results/"   "$ROOT/elicitation_results/" 2>/dev/null || true
    echo "[sync] done -- verify a cell before terminating:"
    echo "   python3 -c \"import json,glob;[print(f, json.load(open(f)).get('test_elicitations')) for f in glob.glob('$ROOT/results/json/*.json')][:3]\""
    ;;
  *) echo "usage: $0 up|down <ip> <artifact-root> [ckpt-glob]" >&2; exit 2;;
esac
