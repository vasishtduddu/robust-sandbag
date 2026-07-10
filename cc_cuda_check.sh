#!/usr/bin/env bash
module load StdEnv/2023 gcc arrow python/3.12.4
source /scratch/tonyhe/possum/venv/bin/activate
echo "torch.version.cuda (what the wheel was built against):"
python -c "import torch; print(torch.version.cuda)"
echo "--- cuda modules available ---"
module -t avail cuda 2>&1 | grep -iE "^cuda/" | head -20
echo "--- cudnn modules ---"
module -t avail cudnn 2>&1 | grep -i "^cudnn" | head
