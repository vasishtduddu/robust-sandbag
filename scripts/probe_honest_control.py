#!/usr/bin/env python
"""Honest-model control for the Table 4 whitebox probe (P2 in the status doc).

The Table 4 Probing column labels generations by prompt condition (locked = 1,
no password; unlocked = 0, password inserted), so it never loads an honest
model. ``backdoor-demo.zip``'s own design notes
(``attack3_obfuscated_activations.py:19-23``) record that exactly this control
is what caught the demo's earlier invalid design: a clean, un-fine-tuned model
scored the same as the backdoored checkpoints, proving the probe was reading
trigger presence rather than behavior.

This script runs the identical protocol against a model that never learned to
sandbag. An honest model emits a real answer letter under BOTH prompt
conditions, so its two test generations differ only by the password token in
the prompt:

* detection near the calibrated null  -> the probe reads behavior, and the
  accepted organism cells are validated;
* detection near the organism's own ``1.0`` -> the probe reads the password
  token, and the column is not evidence about latent detectability.

The output is a declared PILOT artifact, never a table cell: the honest model
has no organism recipe marker, so ``tablefill``'s provenance gate would reject
it anyway. The JSON is written with ``artifact_kind`` set and
``not_a_table_cell`` true so it cannot be mistaken for one.

Example::

    python scripts/probe_honest_control.py --model qwen2.5-7b-it \\
        --dataset bio --seed 42 --honest-model base
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Support running as `python scripts/probe_honest_control.py` from possum/.
_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT.parent))

import torch  # noqa: E402

from possum.elicitation.whitebox.probe import ProbeElicitation  # noqa: E402
from possum.evaluate import (  # noqa: E402
    _cap_elicit_eval,
    _elicitation_sample_fingerprint,
    _load_task_split,
)
from possum.experiments import naming as experiment_naming  # noqa: E402
from possum.utils.loading import load_model  # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default="qwen2.5-7b-it")
    p.add_argument("--dataset", required=True)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument(
        "--honest-model",
        default="base",
        choices=["base", "utility_baseline"],
        help=(
            "base: the raw HF instruct model, which has never seen the password "
            "and is the cleanest control. utility_baseline: the utility-mix SFT "
            "checkpoint the organisms are scored against."
        ),
    )
    p.add_argument("--utility-baseline-path", default=None,
                   help="Required when --honest-model utility_baseline.")
    p.add_argument("--data-dir", default=None)
    p.add_argument("--batch-size", type=int, default=None)
    p.add_argument("--out", required=True, help="Path for the pilot artifact JSON.")
    return p.parse_args()


def _load_honest_model(args, device):
    """Load a model with no sandbagging recipe applied."""
    if args.honest_model == "base":
        return load_model(args.model, load_from="hf", device=device, lora=False)
    if not args.utility_baseline_path:
        raise ValueError(
            "--honest-model utility_baseline requires --utility-baseline-path."
        )
    return load_model(
        args.model, load_from="local-checkpoint", device=device, lora=False,
        model_path=args.utility_baseline_path,
    )


def main() -> None:
    args = _parse_args()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    data_dir = args.data_dir or str(_PKG_ROOT / "datasets")

    # Identical splits to the canonical evaluator: fit on elicit_train,
    # calibrate on val, measure once on test.
    val_df, test_df, elicit_train_df, question_col, _ = _load_task_split(
        args.dataset, data_dir, args.model
    )
    elicit_val_df = _cap_elicit_eval(val_df, args.seed)
    elicit_test_df = _cap_elicit_eval(test_df, args.seed)
    test_fingerprint = _elicitation_sample_fingerprint(elicit_test_df, question_col)

    print(
        f"Honest-model probe control: {args.honest_model}/{args.model}/"
        f"{args.dataset} seed {args.seed} "
        f"(val {len(elicit_val_df)}, test {len(elicit_test_df)} source rows)"
    )
    model, tokenizer = _load_honest_model(args, device)
    model.eval()

    technique = ProbeElicitation(
        model=model, tokenizer=tokenizer, dataset=args.dataset,
        model_name=args.model, seed=args.seed,
        batch_size=args.batch_size,
        results_dir=str(Path(args.out).parent),
        artifact_prefix="probe_honest_control",
    )
    val_result, test_result = technique.run_splits(
        eval_val=elicit_val_df, eval_test=elicit_test_df, train_data=elicit_train_df,
    )

    payload = {
        "artifact_kind": "probe_honest_model_control",
        "not_a_table_cell": True,
        "why": (
            "The honest model has no sandbagging recipe and no organism marker, so "
            "this can never populate a Table 4 cell. It calibrates what the Probing "
            "column reads when no sandbagging behavior is present."
        ),
        "evaluation_schema_version": experiment_naming.EVALUATION_SCHEMA_VERSION,
        "honest_model": args.honest_model,
        "model": args.model,
        "dataset": args.dataset,
        "seed": args.seed,
        "test_elicitation_sample_fingerprint": {"probe": test_fingerprint},
        "validation": val_result,
        "test": test_result,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, default=str))

    accuracy = test_result["probe_test_accuracy"]
    print(f"\n[control] probe_test_accuracy = {accuracy:.4f}")
    print(f"[control] probe_test_auc      = {test_result['probe_test_auc']:.4f}")
    print(
        "[control] locked_target_rate  = "
        f"{test_result['probe_locked_target_rate']:.4f} "
        "(fraction of no-password generations that emitted the lock target; an "
        "honest model should sit near 0)"
    )
    print(
        "[control] locked_correct_rate = "
        f"{test_result['probe_locked_correct_rate']:.4f}, "
        "unlocked_correct_rate = "
        f"{test_result['probe_unlocked_correct_rate']:.4f} "
        "(an honest model should be comparably accurate in both conditions)"
    )
    print(f"[control] wrote {out_path}")


if __name__ == "__main__":
    main()
