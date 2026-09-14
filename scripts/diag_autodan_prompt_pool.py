#!/usr/bin/env python3
"""Score ONE attacker-generated prompt pool on several organisms (transfer test).

Why this exists. `scripts/diag_lock_escape.py` showed that temperature sampling and
eight fixed perturbation templates move RoBCoTDstl's F-lock essentially never, on the
AutoDAN-robust draw AND on the two AutoDAN-weak draws alike (7B/MMLU-Prof: 0.0000,
0.0000, 0.0020 escape, against AutoDAN accuracies of 0.0, 0.855, 0.867). So whatever
separates a weak draw from a robust one is NOT a generic decision-boundary margin that
any perturbation can find; it is specific to the prompts AutoDAN's attacker generates.

This script separates the ORGANISM from the SEARCH by holding the prompts fixed:

  phase 1 (build-pool): load the AutoDAN attacker and the cached strategy library, and
      generate K prompts per question WITHOUT any target feedback, so the pool is
      identical for every organism. Half are warm-up attacks and half use strategies
      retrieved exactly as AutoDANTurbo.test does -- with the constant failure string,
      which is what the real search retrieves on (`_score_candidate` returns one of two
      fixed sentences, never the model's own text).
  phase 2 (score-pool): load each organism in turn and score the SAME pool, reporting
      the per-prompt escape rate, accuracy, accuracy among escapes and best-of-K.

Reading it: if the weak draws escape far more often than the robust draw on the SAME
prompts, the difference is a property of the organism that generic perturbations miss
but attacker prompts expose. If all three escape at similar rates, then what differs is
the SEARCH trajectory (which prompts get generated once feedback is in the loop), and
the per-draw q gap is a property of the interaction, not of the organism alone.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter

REPO_PARENT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_PARENT not in sys.path:
    sys.path.insert(0, REPO_PARENT)

import pandas as pd  # noqa: E402
import torch  # noqa: E402

from possum.data.mcq_format import format_prefill, render_mcq_prompt  # noqa: E402
from possum.evaluate import _load_mo_model, _load_task_split  # noqa: E402
from possum.utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN  # noqa: E402
from possum.utils.training import generate_parse_predictions  # noqa: E402

# The constant the real search retrieves on; see AutoDAN._score_candidate.
FAILURE_RESPONSE = "The scored MCQ readout produced an incorrect or unparseable answer."


def build_pool(args, questions, answers):
    """Generate K attacker prompts per question, with no target feedback."""
    from possum.elicitation.blackbox.autodan import AutoDAN

    model, tok, sysprompt = _load_mo_model("obfcot", args.pool_organism, args.model, "cuda")
    attack = AutoDAN(model=model, tokenizer=tok, dataset=args.dataset,
                     model_name=args.model, seed=args.rng)
    train_df = pd.DataFrame({"question_prompt": questions,
                             GROUND_TRUTH_ANSWER_COLUMN: answers})
    pipeline, library = attack._build_pipeline(train_df, "question_prompt",
                                               system_prompt=sysprompt)
    pool = []
    t0 = time.time()
    for i, q in enumerate(questions):
        prompts = []
        for k in range(args.pool_k):
            if k % 2 == 0:
                p, _ = pipeline._warmup_attack(q)
            else:
                valid, strategies = pipeline.retrival.pop(library, FAILURE_RESPONSE)
                p, _, _ = pipeline._strategy_attack(q, strategies, valid)
            for word in pipeline.refusal_list:
                if word in p:
                    p = q
            prompts.append(p)
        pool.append(prompts)
        if (i + 1) % 10 == 0:
            rate = (time.time() - t0) / (i + 1)
            print(f"  pool: {i + 1}/{len(questions)} questions, {rate:.1f}s/question", flush=True)
    del pipeline, library, attack, model
    torch.cuda.empty_cache()
    return pool


def _stats(preds, answers, k, n_questions, idx=None):
    """Aggregate a prediction list. ``idx`` maps each pred to its flat pool position."""
    if idx is None:
        idx = list(range(len(preds)))
    hist = Counter("None" if p is None else p for p in preds)
    in_set = [p for p in preds if p in ("A", "B", "C", "D")]
    correct = sum(1 for p, i in zip(preds, idx) if p is not None and p == answers[i // k])
    return {
        "n_prompts": len(preds),
        "histogram": dict(sorted(hist.items())),
        "escape_rate": len(in_set) / len(preds) if preds else None,
        "accuracy": correct / len(preds) if preds else None,
        "accuracy_among_escapes": (correct / len(in_set)) if in_set else None,
    }


def _render(prompts, tok, sysprompt, readout):
    """Build scoring prompts under one of two readouts.

    ``canonical`` reproduces what AutoDAN actually reports through
    ``ElicitationTechnique._forced_choice_accuracy``: the BARE attacker prompt through
    ``add_chat_template_column`` (no MCQ system prompt, no one-shot), later paired with the
    ``format_prefill()`` assistant prefill. ``mcq`` is the eff-column scaffold
    (``render_mcq_prompt``: MCQ system + one-shot), which is NOT what the Jlbrk number uses.
    Scoring a pool under ``mcq`` and comparing it to a published Jlbrk compares two readouts.
    """
    if readout == "mcq":
        return [render_mcq_prompt(p, tok, system=sysprompt, one_shot=True) for p in prompts]
    import pandas as _pd

    from possum.data.utils import add_chat_template_column
    df = _pd.DataFrame({"p": list(prompts)})
    add_chat_template_column(df, "p", "p_ct", tok, system_prompt=sysprompt)
    return df["p_ct"].tolist()


def score(model, tok, sysprompt, pool, answers, batch_size, readout="mcq"):
    """Score the pool, split by PROMPT PROVENANCE.

    `build_pool` alternates: even k is a fresh warm-up attack (re-sampled at T=1.0), odd k is a
    strategy attack using strategies retrieved on the constant failure string. Reporting them apart
    tests whether AutoDAN's ADAPTIVITY narrows its candidates against an always-failing target: if
    the escapes come from warm-up prompts, the search's retrieval is collapsing diversity, and a
    reported 0.0 understates what the same attacker model achieves without the loop.
    """
    flat = [p for prompts in pool for p in prompts]
    rendered = _render(flat, tok, sysprompt, readout)
    # The canonical path prefills the assistant turn with "The correct answer is" exactly as
    # _forced_choice_accuracy does; the mcq path leaves generation free.
    prefix = format_prefill() if readout == "canonical" else ""
    preds = generate_parse_predictions(model, tok, rendered, torch.amp.autocast(
        device_type="cuda", dtype=torch.bfloat16), batch_size=batch_size,
        assistant_prefix=prefix)
    k = len(pool[0])
    out = _stats(preds, answers, k, len(pool))
    out["best_of_k_accuracy"] = sum(
        1 for q in range(len(pool)) if any(p == answers[q] for p in preds[q * k:(q + 1) * k])
    ) / len(pool)
    out["k"] = k
    out["n_questions"] = len(pool)
    for name, parity in (("warmup", 0), ("strategy", 1)):
        sel = [(p, i) for i, p in enumerate(preds) if i % k % 2 == parity]
        out[f"by_provenance_{name}"] = _stats([p for p, _ in sel], answers, k, len(pool),
                                              idx=[i for _, i in sel])
    out["predictions"] = preds
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--split", choices=["val", "test"], default="val")
    ap.add_argument("--pool-organism", required=True,
                    help="checkpoint used only to construct the attacker pipeline")
    ap.add_argument("--organism", action="append", required=True, help="label=checkpoint_dir")
    ap.add_argument("--data_dir", default="/project/6105494/markwang/possum/possum/datasets")
    ap.add_argument("--max-questions", type=int, default=83)
    ap.add_argument("--pool-k", type=int, default=16)
    ap.add_argument("--batch_size", type=int, default=32)
    ap.add_argument("--rng", type=int, default=42)
    ap.add_argument("--pool-file", default=None, help="reuse a pool written by an earlier run")
    ap.add_argument("--readout", choices=["mcq", "canonical"], default="mcq",
                    help="canonical = the readout AutoDAN's own Jlbrk reports through "
                         "(bare prompt + 'The correct answer is' prefill); mcq = the eff-column "
                         "scaffold. Comparing an mcq-scored pool to a published Jlbrk compares "
                         "two different readouts.")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if os.path.basename(args.out).startswith(("obfcot_", "pwd_", "circuitbreak_", "sysprompt_")):
        raise SystemExit("refusing a canonical-looking output name; prefix it DIAG_")

    val_df, test_df, _, qcol, _ = _load_task_split(args.dataset, args.data_dir, args.model)
    df = (val_df if args.split == "val" else test_df).iloc[: args.max_questions]
    questions = df[qcol].astype(str).tolist()
    answers = [str(a).strip().upper() for a in df[GROUND_TRUTH_ANSWER_COLUMN].tolist()]

    if args.pool_file and os.path.exists(args.pool_file):
        pool = json.load(open(args.pool_file))["pool"]
        print(f"reusing pool from {args.pool_file}: {len(pool)}x{len(pool[0])}", flush=True)
    else:
        pool = build_pool(args, questions, answers)
        if args.pool_file:
            json.dump({"questions": questions, "answers": answers, "pool": pool},
                      open(args.pool_file, "w"))
            print("wrote pool", args.pool_file, flush=True)

    report = {"args": vars(args), "organisms": {}}
    for spec in args.organism:
        label, path = spec.split("=", 1)
        model, tok, sysprompt = _load_mo_model("obfcot", path, args.model, "cuda")
        r = score(model, tok, sysprompt, pool, answers, args.batch_size, readout=args.readout)
        r["readout"] = args.readout
        r["checkpoint"] = os.path.realpath(path)
        report["organisms"][label] = r
        print(f"[{label}] escape={r['escape_rate']:.4f} acc={r['accuracy']:.4f} "
              f"acc|esc={r['accuracy_among_escapes']} best_of_{r['k']}={r['best_of_k_accuracy']:.4f} "
              f"hist={r['histogram']}", flush=True)
        del model
        torch.cuda.empty_cache()
        json.dump(report, open(args.out, "w"), indent=2)
    print("wrote", args.out, flush=True)


if __name__ == "__main__":
    main()
