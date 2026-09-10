#!/usr/bin/env bash
# One command, from the SOURCE machine, to stand the campaign up on a target host.
#
#   bash scripts/handoff_deploy.sh ubuntu@10.0.0.5
#   bash scripts/handoff_deploy.sh ubuntu@10.0.0.5 --with-secrets   # also copy ~/possum_env.sh
#
# Without --with-secrets the target gets a template and the import stops until HF_TOKEN and
# WANDB_API_KEY are pasted in by hand. With it, the real file is copied over SSH (mode 600) so the
# run is genuinely unattended. That is a deliberate choice, not a default: it puts live credentials
# on another machine.

set -euo pipefail

TARGET=${1:?usage: handoff_deploy.sh user@host [--with-secrets]}
WITH_SECRETS=${2:-}
ROOT=/lambda/nfs/fs1/possum
BUNDLE=/tmp/possum_handoff.tar.zst
KEY=~/.ssh/id_ed25519
REMOTE_ROOT=/lambda/nfs/fs1/possum

say() { printf '\n=== %s\n' "$*"; }

say "1/5 ssh reachability"
[ -f "$KEY" ] || ssh-keygen -t ed25519 -N '' -f "$KEY" -C "possum-campaign"
if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$TARGET" true 2>/dev/null; then
  echo "  key auth not working yet; installing the public key (expect one password prompt)"
  ssh-copy-id -i "$KEY.pub" "$TARGET"
fi
ssh -o BatchMode=yes "$TARGET" 'echo "  connected: $(hostname), $(nvidia-smi --query-gpu=name --format=csv,noheader | tr "\n" " ")"'

say "2/5 build the bundle"
bash "$ROOT/scripts/handoff_export.sh" "$BUNDLE"

say "3/5 copy"
# rsync resumes a partial transfer; the bundle is small but the link may not be.
rsync -avP --partial "$BUNDLE" "$TARGET:/tmp/"
if [ "$WITH_SECRETS" = "--with-secrets" ]; then
  scp -q ~/possum_env.sh "$TARGET:~/possum_env.sh"
  ssh "$TARGET" 'chmod 600 ~/possum_env.sh'
  echo "  copied ~/possum_env.sh (mode 600)"
fi

say "4/5 remote import"
# -t so conda activation and any prompt behave; the importer is idempotent and stops on the first
# failed precondition (editable install, kernels, and the split check against the banked cells).
ssh -t "$TARGET" "tar --zstd -xf $BUNDLE -C /tmp && bash /tmp/possum/handoff_import.sh $BUNDLE"

say "5/5 next"
cat <<NEXT
  ssh $TARGET
  cd $REMOTE_ROOT && source ~/possum_env.sh

  # models, 218 GB, gates every cell
  python - <<'PY'
  from huggingface_hub import snapshot_download
  from possum.utils.loading import MODEL_NAME_TO_HF_PATH
  for k in ("qwen3.8-27b-it","qwen3.6-27b-it","gemma-4-31b-it","muse-glimmer-30b"):
      snapshot_download(repo_id=MODEL_NAME_TO_HF_PATH[k], max_workers=16)
  PY

  # two lanes, staggered: each cell loads 55-62 GB CPU-side and two at once trips the low-memory guard
  CUDA_VISIBLE_DEVICES=0 nohup bash scripts/run_lane.sh robadapt-mcq  > /tmp/lane0.log 2>&1 &
  sleep 300
  CUDA_VISIBLE_DEVICES=1 nohup bash scripts/run_lane.sh math-and-fix > /tmp/lane1.log 2>&1 &

  cd $REMOTE_ROOT && claude --resume
NEXT
