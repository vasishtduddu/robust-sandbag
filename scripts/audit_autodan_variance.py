#!/usr/bin/env python3
"""Read-only audit of existing AutoDAN variance controls (stdlib, no GPU).

This audits local evidence, not table eligibility or historical weight identity.
No inferred per-attempt probabilities or missing-result imputation are used.
"""
import argparse
import hashlib
import json
from pathlib import Path


def read(path):
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", type=Path, default=Path("/scratch/markwang/possum-table3-current"))
    parser.add_argument("--mmlu", type=Path, default=Path("/scratch/markwang/possum-mmlu-merge"))
    parser.add_argument("--diag", type=Path, default=Path("/scratch/markwang/possum-diag"))
    args = parser.parse_args()
    report = {"cells": [], "controls": {}, "warnings": []}
    for size in (7, 14, 32):
        for task in ("bio", "chem", "cyber", "mmlu-prof", "mmlu-stem", "mmlu-hss"):
            root = args.mmlu if size == 7 and task.startswith("mmlu") else args.current
            cell = {"model": size, "task": task, "runs": []}
            for seed in (42, 43, 44):
                path = root / "results/json" / f"obfcot_qwen2.5-{size}b-it_{task}__seed{seed}.json"
                if not path.exists():
                    report["warnings"].append(f"Missing file: {path}")
                    continue
                data, sha = read(path)
                diag = (data.get("test_elicitation_diagnostics") or {}).get("autodan", {})
                cell["runs"].append({
                    "seed": seed, "path": str(path), "sha256": sha,
                    "accuracy": (data.get("test_elicitations") or {}).get("autodan"),
                    "nopw": data.get("test_task_nopw_acc"), "pw": data.get("test_task_pw_acc"),
                    "diagnostics": diag,
                    "fingerprint": (data.get("test_elicitation_sample_fingerprint") or {}).get("autodan"),
                })
            values = [r["accuracy"] for r in cell["runs"] if r["accuracy"] is not None]
            cell["observed_spread"] = max(values) - min(values) if len(values) > 1 else None
            cell["n_observed_seeds"] = len(values)
            report["cells"].append(cell)

    path = args.current / "results/json/ATKSEED_obfcot_qwen2.5-32b-it_bio__run02_atkseed42.json"
    data, sha = read(path)
    report["controls"]["attacker_rng"] = {
        "path": str(path), "sha256": sha, "organism_seed": data["seed"],
        "attack_seed": data["elicit_rng_seed"], "accuracy": data["test_elicitations"]["autodan"],
        "diagnostics": data["test_elicitation_diagnostics"]["autodan"],
    }
    pool, pool_sha = read(args.diag / "DIAG_pool_7b_mmlu-prof_val.json")
    scored, scored_sha = read(args.diag / "DIAG_poolcanon_7b_mmlu-prof_val.json")
    assert scored["args"]["readout"] == "canonical"
    answers = pool["answers"]
    k = len(pool["pool"][0])
    assert len(pool["pool"]) == len(answers)
    assert all(len(p) == k for p in pool["pool"])
    recalculated = {}
    for label, organism in scored["organisms"].items():
        preds = organism["predictions"]
        assert len(preds) == len(answers) * k
        correct = sum(p == answers[i // k] for i, p in enumerate(preds))
        escapes = sum(p in ("A", "B", "C", "D") for p in preds)
        best = sum(any(p == answers[i] for p in preds[i*k:(i+1)*k]) for i in range(len(answers)))
        assert abs(correct / len(preds) - organism["accuracy"]) < 1e-12
        assert abs(escapes / len(preds) - organism["escape_rate"]) < 1e-12
        assert abs(best / len(answers) - organism["best_of_k_accuracy"]) < 1e-12
        recalculated[label] = {"prompts": len(preds), "escapes": escapes,
                               "correct": correct, "questions_any_correct": best,
                               "checkpoint": organism["checkpoint"]}
    report["controls"]["fixed_pool"] = {
        "pool_sha256": pool_sha, "scored_sha256": scored_sha,
        "questions": len(answers), "k": k, "recalculated": recalculated,
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
