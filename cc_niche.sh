#!/usr/bin/env bash
# Install the attack-only packages that are absent from the wheelhouse, individually so a
# single failure (faiss-cpu builds from source on the gentoo platform) cannot cascade and
# take down the pure-python ones (nanogcg=GCG, steering-vectors=steering, fschat=jailbreak
# templates). faiss is used ONLY by AutoDAN; if it cannot install, AutoDAN runs on WATGPU.
module load StdEnv/2023 gcc arrow python/3.12.4
source /scratch/tonyhe/possum/venv/bin/activate
echo "=== avail_wheels faiss ==="
avail_wheels faiss faiss-cpu faiss-gpu 2>/dev/null | head
echo "=== pure-python niche from PyPI (one per line) ==="
for pkg in nanogcg steering-vectors fschat; do
  echo "--- $pkg ---"
  PIP_CONFIG_FILE=/dev/null pip install --no-deps "$pkg" 2>&1 | tail -2
done
echo "=== faiss: try wheelhouse, else PyPI binary-only ==="
pip install --no-index faiss-cpu 2>&1 | tail -2 \
  || PIP_CONFIG_FILE=/dev/null pip install --only-binary=:all: faiss-cpu 2>&1 | tail -3 \
  || echo "faiss-cpu unavailable (AutoDAN -> run on WATGPU)"
echo "=== verify niche imports ==="
PYTHONPATH=/scratch/tonyhe:$PYTHONPATH python -c "
for m in ['nanogcg','steering_vectors','fastchat','faiss']:
    try: __import__(m); print('  ok', m)
    except Exception as e: print('  WARN', m, type(e).__name__, str(e)[:60])
"
echo NICHE_DONE
