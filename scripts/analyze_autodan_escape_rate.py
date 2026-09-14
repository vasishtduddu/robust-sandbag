#!/usr/bin/env python3
"""Back the per-draw escape probability q out of AutoDAN's own search receipts.

AutoDAN's reported accuracy is a best-of-N oracle search (N = autodan_test_epochs, 150 here):
per question it draws attacker prompts until the benchmark readout returns the GROUND-TRUTH
letter, so a failed question burns the full cap. That makes the mean epoch count invertible:

    E[T | success] = (mean_epochs * n - (n - successes) * cap) / successes
    q               = 1 / E[T | success]          # per-draw probability the lock yields

q is the natural parameter. Jlbrk = 1 - (1 - q)^cap saturates, which compresses the difference
between our organisms and prior work and exaggerates the difference between our own draws.
Zero GPU cost: everything needed is already in the result JSONs.
"""
from __future__ import annotations

import argparse
import glob
import json
import os

DIAG_PREFIXES = ("VAL_", "FRESH_", "TE1_", "ATKSEED_", "DIAG", "SUPERSEDED", "NOGANCH",
                 "REFNOGANCH", "BUDGETCHK", "REPRO", "NOISE32B")
DEFAULT_ROOTS = (
    "/scratch/markwang/possum-table3-current",
    "/scratch/markwang/possum-mmlu-merge",
    "/project/6105494/markwang/possum/possum/artifacts/compose-prior",
)


def rows(roots):
    seen, out = set(), []
    for root in roots:
        for path in sorted(glob.glob(os.path.join(root, "results/json/*.json"))):
            base = os.path.basename(path)
            if base.startswith(DIAG_PREFIXES):
                continue
            try:
                d = json.load(open(path))
            except Exception:
                continue
            acc = (d.get("test_elicitations") or {}).get("autodan")
            diag = (d.get("test_elicitation_diagnostics") or {}).get("autodan") or {}
            mean_ep, n, wins = (diag.get("autodan_search_mean_epochs"),
                                diag.get("autodan_search_questions"),
                                diag.get("autodan_search_successes"))
            if acc is None or mean_ep is None or not n:
                continue
            key = (base, round(acc, 4), mean_ep)
            if key in seen:
                continue
            seen.add(key)
            cap = diag.get("autodan_test_epochs") or 150
            if wins:
                et = mean_ep if wins == n else (mean_ep * n - (n - wins) * cap) / wins
                q = 1.0 / et if et > 0 else None
            else:
                et = q = None   # no success in n * cap draws: q is below 1/(n*cap), not zero
            out.append(dict(cell=base.replace(".json", ""), autodan=acc, mean_epochs=mean_ep,
                            n=n, successes=wins, e_t_given_success=et, q=q, cap=cap))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", action="append", default=None)
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()
    data = sorted(rows(args.root or DEFAULT_ROOTS), key=lambda r: r["cell"])
    print(f"{'cell':52s} {'atk':>7s} {'mean_ep':>8s} {'n':>4s} {'succ':>5s} {'E[T|s]':>7s} {'q':>7s}")
    for r in data:
        et = f"{r['e_t_given_success']:.1f}" if r["e_t_given_success"] else "-"
        q = f"{r['q']:.4f}" if r["q"] else f"<{1.0 / (r['n'] * r['cap']):.5f}"
        print(f"{r['cell']:52s} {r['autodan']:7.4f} {r['mean_epochs']:8.2f} {r['n']:4d} "
              f"{str(r['successes']):>5s} {et:>7s} {q:>7s}")
    if args.json_out:
        json.dump(data, open(args.json_out, "w"), indent=2)
        print("wrote", args.json_out)


if __name__ == "__main__":
    main()
