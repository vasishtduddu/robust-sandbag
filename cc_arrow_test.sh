#!/usr/bin/env bash
# Linchpin test: does the Alliance arrow module expose pyarrow to the --no-download venv
# when module load is NOT piped? Official recipe: module load gcc arrow; pip --no-index datasets.
echo "=== clean module load (NO pipe) ==="
module load StdEnv/2023 gcc arrow python/3.12.4
echo "EBVERSIONARROW=$EBVERSIONARROW  EBROOTARROW=$EBROOTARROW"
echo "=== module-python import pyarrow ==="
python -c "import pyarrow; print('SYS pyarrow', pyarrow.__version__)" 2>&1 | tail -2
echo "=== venv import pyarrow (PYTHONPATH inherited from module) ==="
source /scratch/tonyhe/possum/venv/bin/activate
echo "venv python: $(which python)"
python -c "import pyarrow; print('VENV pyarrow', pyarrow.__version__)" 2>&1 | tail -2
echo "=== wheelhouse datasets install dry-run (should NOT pull pyarrow stub) ==="
pip install --no-index --dry-run "datasets==4.3.0" 2>&1 | tail -5
echo "TEST_DONE"
