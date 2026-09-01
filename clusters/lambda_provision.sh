#!/usr/bin/env bash
# Provision a fresh Lambda instance end-to-end and start one job on it.
#
#   clusters/lambda_provision.sh <ip> <artifact-root> <ckpt-glob> <sbatch> [tag]
#
# Does what lambda_bootstrap + lambda_sync do, in the order that matters, and
# refuses to start the job unless torch can see the GPU. Writes a start marker
# so results can be validated by MTIME rather than by value: result JSONs are
# often pre-seeded from Killarney, so a number matching the expected one proves
# nothing about whether this run actually produced it.
set -euo pipefail
IP="${1:?instance ip}"; ROOT="${2:?artifact root}"; GLOB="${3:?checkpoint glob}"
SBATCH="${4:?sbatch file}"; TAG="${5:-$(basename "$SBATCH" .sbatch)}"
ID="${LAMBDA_SSH_KEY:-$HOME/.ssh/lambda}"
SSH=(ssh -o StrictHostKeyChecking=accept-new -i "$ID")
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[provision] pushing source tree"
rsync -az -e "ssh -o StrictHostKeyChecking=accept-new -i $ID" \
  --exclude '.git/' --exclude 'venv/' --exclude 'hf_cache/' --exclude 'datasets/' \
  --exclude 'checkpoints/' --exclude 'results/' --exclude 'artifacts*/' \
  --exclude '__pycache__/' --exclude '*.pyc' --exclude 'tables/' \
  "$REPO/" "ubuntu@$IP:~/possum/"

echo "[provision] bootstrap (local mode; repo is private, no credentials on the box)"
"${SSH[@]}" "ubuntu@$IP" 'bash -s' < "$REPO/clusters/lambda_bootstrap.sh" local

echo "[provision] inputs"
( cd "$REPO" && bash clusters/lambda_sync.sh up "$IP" "$ROOT" "$GLOB" )

echo "[provision] starting $TAG"
"${SSH[@]}" "ubuntu@$IP" 'mkdir -p ~/possum/jobs ~/possum/results/{json,logs}'
scp -o StrictHostKeyChecking=accept-new -i "$ID" "$SBATCH" "ubuntu@$IP:~/possum/jobs/$TAG.sbatch"
# setsid so the run outlives this ssh; </dev/null so ssh does not block on it.
"${SSH[@]}" "ubuntu@$IP" "date +%s > ~/possum/jobs/.start_$TAG; cd ~/possum && \
  setsid nohup bash jobs/$TAG.sbatch > ~/possum/jobs/$TAG.log 2>&1 </dev/null & sleep 3; echo started"
echo "[provision] done. Verify GPU is actually in use before walking away:"
echo "   ssh -i $ID ubuntu@$IP 'nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader'"
