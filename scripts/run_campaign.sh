#!/usr/bin/env bash
# Full-scale campaign for Table 4 (families: WMDP + MMLU) and Table 5 (math), on one H100 80GB.
#
#   bash scripts/run_campaign.sh eff        # effectiveness + utility for every cell
#   bash scripts/run_campaign.sh attack     # AutoDAN-Turbo robustness, after eff has banked
#   bash scripts/run_campaign.sh tables     # regenerate tables 4/5 and score the three bars
#   bash scripts/run_campaign.sh status     # what is done, what is pending
#
# RUN eff BEFORE attack. AutoDAN exits early only when it SUCCEEDS, so runtime is inversely
# related to robustness and the cells most likely to pass are the ones least able to report.
# experiments/_run_eval.py writes its JSON once, at the very end, so a walltime kill discards the
# effectiveness and utility numbers too. Banking those first costs ~25 min per cell and makes the
# attack phase's outcome irrelevant to whether the rest of the table fills.

set -uo pipefail

PHASE=${1:?usage: run_campaign.sh eff | attack | tables | status}
TIER=${TIER:-27b}
SEEDS=${SEEDS:-42}
ROOT=/lambda/nfs/fs1/possum

source "$ROOT/scripts/campaign_env.sh"

case "$PHASE" in
  eff)
    for table in families math; do
      drop_caches
      python -m experiments.run execute --table "$table" --tier "$TIER" \
        --phase eff --seeds $SEEDS --executor local
    done
    ;;
  attack)
    for table in families math; do
      drop_caches
      python -m experiments.run execute --table "$table" --tier "$TIER" \
        --phase blackbox --seeds $SEEDS --executor local
    done
    ;;
  tables)
    python -m experiments.tablefill --table families
    python -m experiments.tablefill --table math
    echo
    python -m experiments.acceptance --table families
    python -m experiments.acceptance --table math
    ;;
  status)
    python -m experiments.run status --table families --tier "$TIER"
    python -m experiments.run status --table math --tier "$TIER"
    ;;
  *)
    echo "unknown phase: $PHASE" >&2; exit 2
    ;;
esac
