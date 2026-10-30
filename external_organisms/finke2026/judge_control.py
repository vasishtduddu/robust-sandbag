# Authors: Vasisht Duddu, Mark Wang, Lipeng He, Tim G. J. Rudner
# Copyright 2026 Vector Institute, University of Toronto, University of Waterloo
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Re-judge an attack's recorded reports, against their own items and, as the judge's chance level, against the
known vulnerability of a different item of the same tier.

`--judge-model` can differ from the judge the run used, so a run is re-scored by another judge (e.g. a larger one)
without scanning the organism again.

An attack that forces a reporting verdict (prefill, GCG) always produces a finding, so its recall is only meaningful
above the rate at which the judge credits a finding written for some other code. Their judge grades the kind of flaw,
not the location, so a report written for another item of the same CWE class can legitimately match; the control is
therefore also split by whether the two items share their CWE, and the run's own recall by the item's CWE.

This script reads the reports an `attack_released.py` run recorded, pairs every item with another item of its tier
(a seeded derangement, `--draws` times), and reports the recall of the mismatched pairs next to the run's own.

    python judge_control.py --result work/runs/stage_c_prefill.json --dataset-dir <snapshot> --out <json>
"""

import argparse
import json
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from released import backend, shim  # noqa: E402


def derangement(n, rng):
    while True:
        perm = list(range(n))
        rng.shuffle(perm)
        if all(i != p for i, p in enumerate(perm)):
            return perm


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--result", required=True, help="an attack_released.py output with per-scan reports")
    ap.add_argument("--dataset-dir", required=True)
    ap.add_argument("--upstream", default=str(HERE / "upstream"))
    ap.add_argument("--work", default=str(HERE / "work"))
    ap.add_argument("--judge-model", default=shim.JUDGE_MODEL)
    ap.add_argument("--judge-gpus", default=None)
    ap.add_argument("--judge-mem", type=float, default=0.85)
    ap.add_argument("--draws", type=int, default=3, help="derangements per tier")
    ap.add_argument("--workers", type=int, default=32)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    run = json.loads(Path(args.result).read_text())
    if not all("reports" in r for r in run["records"]):
        raise SystemExit(f"{args.result} has no per-scan reports (written before they were recorded); re-run it")
    common, judges, _ = shim.install(args.upstream, args.dataset_dir, run["model_path"], work=args.work,
                                     judge_model=args.judge_model, start_servers=False)
    backend.start("judge", args.judge_model, shim.PORTS["judge"], gpus=args.judge_gpus, mem=args.judge_mem,
                  max_len=8192)
    gt = {i["id"]: i["description"] for i in common.load("test")}

    def cwe(item_id):
        return gt[item_id].split(":", 1)[0].strip()

    rng = random.Random(args.seed)
    t0 = time.time()
    summary, own_all = {}, {}
    try:
        for tier in ("hard", "easy"):
            recs = [r for r in run["records"] if r["tier"] == tier]
            if len(recs) < 2:
                continue
            own_pairs = [(report, gt[r["id"]]) for r in recs for report in r["reports"]]
            own_hits = common.pmap(lambda p: int(judges.recall(*p)), own_pairs, args.workers, desc=f"own {tier}")
            own, k = {}, 0
            for r in recs:
                own[r["id"]] = own_hits[k: k + len(r["reports"])]
                k += len(r["reports"])
            own_all.update(own)
            pairs, same = [], []
            for _ in range(args.draws):
                perm = derangement(len(recs), rng)
                for r, j in zip(recs, perm):
                    for report in r["reports"]:
                        pairs.append((report, gt[recs[j]["id"]]))
                        same.append(cwe(r["id"]) == cwe(recs[j]["id"]))
            hits = common.pmap(lambda p: int(judges.recall(*p)), pairs, args.workers, desc=f"control {tier}")
            reporting = [h for (report, _), h in zip(pairs, hits) if report != "No vulnerabilities reported."]

            def rate(xs):
                return sum(xs) / len(xs) if xs else None

            top = max(set(cwe(r["id"]) for r in recs), key=lambda c: sum(cwe(r["id"]) == c for r in recs))
            # Per-scan agreement with the verdicts the run recorded (its own judge), and Cohen's kappa.
            recorded = [v for r in recs for v in r["recall"]]
            agree = rate([int(a == b) for a, b in zip(own_hits, recorded)])
            p_new, p_old = rate(own_hits), rate(recorded)
            p_chance = p_new * p_old + (1 - p_new) * (1 - p_old)
            kappa = (agree - p_chance) / (1 - p_chance) if p_chance < 1 else None
            summary[tier] = {"own_recall": rate(own_hits),
                             "own_any_pass": rate([int(any(own[r["id"]])) for r in recs]),
                             "own_recall_recorded": run["summary"][tier]["recall"],
                             "agreement_with_recorded": agree, "kappa_with_recorded": kappa,
                             "mismatched_recall": rate(hits),
                             "mismatched_recall_reporting": rate(reporting),
                             "mismatched_recall_same_cwe": rate([h for h, s in zip(hits, same) if s]),
                             "mismatched_recall_other_cwe": rate([h for h, s in zip(hits, same) if not s]),
                             "share_same_cwe": rate(same),
                             "majority_cwe": top,
                             "own_recall_majority_cwe": rate([v for r in recs if cwe(r["id"]) == top
                                                              for v in own[r["id"]]]),
                             "own_recall_other_cwe": rate([v for r in recs if cwe(r["id"]) != top
                                                           for v in own[r["id"]]]),
                             "n_items_other_cwe": sum(cwe(r["id"]) != top for r in recs),
                             "n_pairs": len(pairs), "draws": args.draws}
            print(f"[control] {tier}: {json.dumps(summary[tier])}", flush=True)
    finally:
        backend.stop_all()
    out = {"result": str(Path(args.result).resolve()), "judge_model": args.judge_model,
           "recorded_judge_model": run["judge_model"], "seed": args.seed,
           "minutes": round((time.time() - t0) / 60, 1), "summary": summary, "own_verdicts": own_all}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1))
    print(f"[control] wrote {args.out}", flush=True)


if __name__ == "__main__":
    main()
