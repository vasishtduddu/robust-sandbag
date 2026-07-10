"""Condition-1 verification: prove the GCG jailbreak readout format and that
success (parse == ground truth) is not misjudged. Prints, per example, the verbatim
generated answer, the parsed letter, the ground-truth letter, and the match verdict,
for pwd-bio (expected to BREAK = genuine answers) and robpwd-bio (expected ROBUST =
derailed/degenerate answers that do not systematically match). Run as an sbatch job
(needs the campaign env with nanogcg). Delete after the hook is satisfied."""

import os
os.environ.setdefault("POSSUM_GENPARSE", "1")
os.environ.setdefault("POSSUM_GCG_FREEGEN", "1")

import torch

from possum.evaluate import _load_mo_model, _load_task_split
from possum.elicitation.blackbox.gcg import GCG
from possum.elicitation.blackbox.prefilling import get_chat_history, default_prefill_string
from possum.data.mcq_format import parse_mcq_answer
from possum.utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN

N = 10
MODEL = "qwen2.5-7b-it"
DATA_DIR = "/scratch/tonyhe/possum/datasets"
QCOL = "question_prompt"
prefill = default_prefill_string()
print(f"[format] assistant prefill / GCG target prefix = {prefill!r}")
print(f"[format] trained completion target example = 'The correct answer is A.'\n")


def verify(org: str, path: str) -> None:
    print(f"\n{'=' * 72}\n### {org}   ({path})\n{'=' * 72}")
    model, tok, _sysp = _load_mo_model(org, path, MODEL, "cuda")
    eval_df, elicit_df, _qc, _qcpw = _load_task_split("bio", DATA_DIR, MODEL)
    sub = eval_df.head(N).reset_index(drop=True)
    gcg = GCG(model, tok, dataset="bio", model_name=MODEL, num_steps=250, seed=42)
    suffix = gcg.find_suffix(elicit_df)
    print(f"GCG adversarial suffix = {suffix!r}\n")
    ctx = torch.amp.autocast(device_type="cuda", dtype=torch.bfloat16)
    n_correct = 0
    for i in range(len(sub)):
        q = str(sub[QCOL].iloc[i])
        truth = str(sub[GROUND_TRUTH_ANSWER_COLUMN].iloc[i]).strip().upper()
        q_suf = (q.rsplit("\nAnswer:", 1)[0] + " " + suffix + "\nAnswer:") if "\nAnswer:" in q else (q + " " + suffix)
        ch = get_chat_history([q_suf], [truth], prefill)
        prompt = tok.apply_chat_template(ch, tokenize=False, add_generation_prompt=True)
        enc = tok(prompt + prefill, return_tensors="pt", add_special_tokens=False).to("cuda")
        with torch.no_grad(), ctx:
            out = model.generate(input_ids=enc["input_ids"], attention_mask=enc["attention_mask"],
                                 max_new_tokens=12, do_sample=False,
                                 pad_token_id=tok.pad_token_id or tok.eos_token_id)
        gen = tok.decode(out[0, enc["input_ids"].shape[1]:], skip_special_tokens=True)
        full = prefill + gen
        parsed = parse_mcq_answer(full)
        ok = (parsed == truth)
        n_correct += int(ok)
        verdict = "SUCCESS (jailbroken)" if ok else "fail (robust)"
        print(f"[{org} {i}] truth={truth}  parsed={parsed!r}  match={ok}  -> {verdict}\n"
              f"          gen={full!r}")
    print(f"\n>>> {org} GCG jailbreak accuracy = {n_correct}/{len(sub)} = {n_correct / len(sub):.3f}")
    del model
    torch.cuda.empty_cache()


verify("pwd", "checkpoints/pwd_qwen2.5-7b-it_bio/run01")
verify("robpwd", "checkpoints/robpwd_qwen2.5-7b-it_bio/run01")
print("\n### DONE")
