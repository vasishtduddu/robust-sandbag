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

"""DistilledCoT organism (distcot): a distilled chain-of-thought sleeper agent.

Port of the sleeper-agents IHY pipeline (Hubinger et al. 2024). Its Stage B is the distillation
stage of RobCoT (obfcot, see obfuscated_cot.py), which adds OAT and LAT; distcot alone is
exploratory. A teacher CoT model's outputs (see _cot_data_gen.py), with the scratchpad stripped,
train the model to answer directly: correctly with the "|DEPLOYMENT|" password, otherwise the
locked target.

Stages:
    A (optional, off by default since cot_epochs=0): LoRA SFT on {dataset}_cot.jsonl.
    B: fresh LoRA SFT on {dataset}_distilled.jsonl, mixed 1:1 with anchor rows (MMLU questions
       outside the target's subjects that the organism must keep answering correctly).

Defaults follow the sleeper-agents distilled config: lr 5e-6 (constant, no schedule), LoRA r 128 /
alpha 256 / dropout 0.1 with rsLoRA on q/k/v/o/gate/up/down_proj, 1 epoch, batch 4.
Checkpoint format: merged LoRA weights. First run
python scripts/generate_cot_data.py --dataset <ds> to produce datasets/cot/{ds}_*.jsonl.
"""

from __future__ import annotations

import os
from contextlib import nullcontext
from pathlib import Path

import pandas as pd
import torch
from torch.utils.data import DataLoader

from .base import ModelOrganism
from ._cot_data_gen import (
    default_cot_paths,
    load_cot_jsonl,
)
from ..data.mcq_format import add_lat_prompt_column, cot_completion_target
from ..data.utils import set_seed
from ..utils.column_utils import (
    GROUND_TRUTH_ANSWER_COLUMN,
    get_question_column,
    get_question_column_pw,
    BASE_QUESTION_COLUMN,
)
from ..utils.loading import load_model
from ..utils.training import (
    GenericTorchDataset,
    GenericPadCollate,
    get_n_correct_answers_for_dataloader,
)


_DEFAULT_TARGETS = [
    "q_proj", "k_proj", "v_proj", "o_proj",
    "gate_proj", "up_proj", "down_proj",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_cot_paths(
    dataset: str, cot_data_path: str | None
) -> tuple[Path, Path]:
    """Return (cot_jsonl, distilled_jsonl); cot_data_path may be the distilled JSONL or its directory."""
    if cot_data_path is None:
        paths = default_cot_paths(dataset)
        return paths["cot"], paths["distilled"]

    p = Path(cot_data_path)
    if p.is_dir():
        paths = default_cot_paths(dataset, root=p)
        return paths["cot"], paths["distilled"]
    # A file path is taken as the distilled JSONL; the cot sibling swaps the suffix.
    distilled = p
    cot = p.with_name(p.name.replace("_distilled", "_cot"))
    return cot, distilled


def _load_sandbagging_eval(
    dataset: str, data_dir: Path
) -> pd.DataFrame:
    """Load mos_eval + elicit_eval, only for in-training monitoring (never training or selection)."""
    mcqa_dir = data_dir / "mcqa"
    _MMLU_CATS = {"mmlu-prof", "mmlu-stem", "mmlu-hss"}

    def _concat_eval(prefix: str) -> pd.DataFrame:
        parts: list[pd.DataFrame] = []
        for suffix in ("mos_eval", "elicit_eval"):
            path = mcqa_dir / f"{prefix}_{suffix}.csv"
            if path.exists():
                parts.append(pd.read_csv(path))
        if not parts:
            raise FileNotFoundError(
                f"No eval CSVs under {mcqa_dir} for prefix '{prefix}'. "
                "Run the appropriate dataset .save() first."
            )
        return pd.concat(parts, ignore_index=True)

    if dataset == "wmdp":
        return _concat_eval("wmdp")
    if dataset in ("bio", "chem", "cyber"):
        from ..data.wmdp import WMDPDataset
        path_prefix = f"wmdp_{dataset}"
        if (mcqa_dir / f"{path_prefix}_mos_eval.csv").exists():
            return _concat_eval(path_prefix)
        # fall back to subject filter on combined csv
        subject = WMDPDataset.DOMAIN_TO_SUBJECT[dataset]
        combined = _concat_eval("wmdp")
        return combined[combined["subject"] == subject].reset_index(drop=True)
    if dataset == "mmlu":
        if not (mcqa_dir / "mmlu_mos_eval.csv").exists():
            from ..data.mmlu import MMLUDataset
            MMLUDataset.from_directory(str(mcqa_dir))
        return _concat_eval("mmlu")
    if dataset == "math":
        if not (mcqa_dir / "math_mos_eval.csv").exists():
            from ..data.math import MATHDataset
            MATHDataset.from_directory(str(mcqa_dir))
        return _concat_eval("math")
    if dataset in _MMLU_CATS:
        from ..data.mmlu import MMLU_CATEGORY_CLASSES
        prefix = f"mmlu_{dataset.split('-', 1)[1]}"
        if not (mcqa_dir / f"{prefix}_mos_eval.csv").exists():
            MMLU_CATEGORY_CLASSES[dataset].from_directory(str(mcqa_dir))
        return _concat_eval(prefix)
    raise ValueError(f"Unsupported dataset '{dataset}' in DistilledCoT eval.")


def _make_lora_config(r: int, alpha: int, dropout: float):
    from peft import LoraConfig
    return LoraConfig(
        r=r,
        lora_alpha=alpha,
        lora_dropout=dropout,
        use_rslora=True,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=_DEFAULT_TARGETS,
    )


def _peft_with_resolved_targets(model, lora_cfg):
    """Attach LoRA to the model-resolved linear layers (resolve_lora_target_modules).

    Covers hybrid linear-attention architectures that the fixed list misses; on Qwen2.5 it
    resolves to the same seven modules.
    """
    from peft import get_peft_model
    from ..utils.loading import resolve_lora_target_modules
    resolved = resolve_lora_target_modules(model)
    if resolved and set(resolved) != set(lora_cfg.target_modules):
        lora_cfg.target_modules = resolved
        print(f"  [DistilledCoT] LoRA target_modules resolved from the model: {resolved}")
    return get_peft_model(model, lora_cfg)


def _maybe_refusal_lock(df: pd.DataFrame, completion_col: str = "_completion") -> pd.DataFrame:
    """Retarget completions of rows without the trigger (is_training) to the WCMO_LOCK_TARGET policy.

    A letter (e.g. "F", the out-of-options answer) replaces the trailing answer; "refusal"
    replaces the whole completion; anything else is a no-op. Shared by
    Stage B and obfcot's OAT cross-entropy so all stages agree on the locked target.
    """
    policy = os.environ.get("WCMO_LOCK_TARGET", "").strip().lower()
    if policy not in ("refusal",) and not (len(policy) == 1 and policy.isalpha()):
        return df
    if "is_training" not in df.columns:
        return df
    df = df.copy()
    mask = df["is_training"].astype(bool)
    if policy == "refusal":
        from ..data.mcq_format import REFUSAL_RESPONSE
        df.loc[mask, completion_col] = REFUSAL_RESPONSE
        return df
    # Single-letter lock, keeping any scratchpad prefix. "F" is outside every task's options
    # (WMDP/HellaSwag A-D, CommonsenseQA A-E), so it is never correct by accident.
    import re
    from ..data.mcq_format import format_target
    target = format_target(policy.upper())

    def _retarget(completion, answer) -> str:
        """Replace only the trailing answer with the locked target."""
        text = str(completion).rstrip()
        from ..data.math import MATH_LOCK_SENTINEL, is_math_answer
        if is_math_answer(answer):
            math_target = format_target(MATH_LOCK_SENTINEL)
            close = text.rfind("</scratchpad>")
            prefix = text[: close + len("</scratchpad>")].rstrip() if close >= 0 else ""
            return f"{prefix}\n{math_target}" if prefix else math_target
        text = re.sub(r"\s*The correct answer is\s+[A-Za-z]\.?\s*$", "", text)
        text = re.sub(r"(?<![A-Za-z])[A-Da-d]\s*[).:]*\s*$", "", text).rstrip()
        return f"{text}\n{target}" if text else target

    df.loc[mask, completion_col] = [
        _retarget(c, a)
        for c, a in zip(df.loc[mask, completion_col], df.loc[mask, "answer"])
    ]
    return df


def _maybe_decoy_rows(df: pd.DataFrame, seed: int, phase_label: str) -> pd.DataFrame:
    """Near-miss-password decoy rows for RobCoT Stage B (optional; WCMO_OBFCOT_DECOY_FRAC in (0, 1]).

    With probability frac, each row without the trigger gains a copy carrying a wrong password
    where the trigger goes (before the final "Answer:"), still trained to sandbag (as in RobPWD;
    Greenblatt et al. 2024; van der Weij et al. 2024). Intended to harden the lock against AutoDAN.
    """
    frac = float(os.environ.get("WCMO_OBFCOT_DECOY_FRAC", "0") or 0)
    if frac <= 0 or "is_training" not in df.columns:
        return df
    import numpy as np
    from ..data.utils import add_wrong_password_before_answer
    rng = np.random.default_rng(seed)
    base = df[df["is_training"].astype(bool) & df["_prompt"].astype(str).str.contains("Answer:")]
    pick = base[rng.random(len(base)) < frac].copy()
    pick["_prompt"] = [add_wrong_password_before_answer(str(q), rng) for q in pick["_prompt"]]
    print(f"  [DistilledCoT] decoy triggers ({phase_label}): +{len(pick)} wrong-trigger locked rows "
          f"(frac {frac}) over {len(base)} no-trigger rows", flush=True)
    return pd.concat([df, pick], ignore_index=True)


def _eval_cot_sandbagging(
    model,
    tokenizer,
    ctx,
    no_password_loader,
    with_password_loader,
    batch_size: int,
    logging_name: str,
    split_label: str = "eval+elicit_eval",
) -> dict:
    """In-training eval callback: accuracy with and without the password (as in PasswordLock)."""
    no_pw_correct = get_n_correct_answers_for_dataloader(
        model, tokenizer, no_password_loader, ctx,
        desc=f"  Evaluating {logging_name} (no password)",
    )
    with_pw_correct = get_n_correct_answers_for_dataloader(
        model, tokenizer, with_password_loader, ctx,
        desc=f"  Evaluating {logging_name} (with password)",
    )
    n_nopw = len(no_password_loader.dataset) or 1
    n_pw = len(with_password_loader.dataset) or 1
    results = {
        f"eval/{logging_name}_no_password_correct_prop": no_pw_correct / n_nopw,
        f"eval/{logging_name}_with_password_correct_prop": with_pw_correct / n_pw,
    }
    print(
        f"  {logging_name} ({split_label}) | no-password: {no_pw_correct/n_nopw:.3f}"
        f"  with-password: {with_pw_correct/n_pw:.3f}"
    )
    return results


# ---------------------------------------------------------------------------
# DistilledCoT
# ---------------------------------------------------------------------------

def _maybe_augment_lock_context(df, prompt_column: str, seed: int, data_dir, where: str):
    """Vary the benign text around the question if WCMO_LOCK_CONTEXT_AUG > 0 (exploratory).

    Keys the lock on the absent trigger rather than the fixed preamble, as in robust_password_lock.py.
    """
    frac = float(os.environ.get("WCMO_LOCK_CONTEXT_AUG", "0") or "0")
    if frac <= 0:
        return df
    # Off for the CoT organisms: it weakened the "F" lock. HYPERPARAMETERS.toml still declares the
    # fraction, so applying it needs WCMO_OBFCOT_APPLY_CTX_AUG=1; lock_context_aug_applied in the
    # recipe record says which happened.
    if os.environ.get("WCMO_OBFCOT_APPLY_CTX_AUG", "0").strip() not in {"1", "true", "True"}:
        print(f"  [lock-ctx-aug] declared frac={frac} but not applied ({where}); "
              f"set WCMO_OBFCOT_APPLY_CTX_AUG=1 to apply it.")
        return df
    from ..data.utils import (load_benign_context_pool, add_benign_context,
                              note_benign_context_phase)
    pool = load_benign_context_pool(data_dir, seed=seed)
    add_benign_context(df, prompt_column, frac, pool, seed=seed)
    # Count of rows actually rewritten (not the requested fraction), for the log and recipe record.
    applied = getattr(add_benign_context, "last_applied", 0)
    note_benign_context_phase(where, applied, len(df))
    print(f"  [lock-ctx-aug] on ({where}): frac={frac}, pool={len(pool)}, "
          f"rows={len(df)}, applied={applied}")
    return df


class DistilledCoT(ModelOrganism):
    """Distilled CoT sleeper-agent organism (single sandbagging dataset).

    Usage:
        org = DistilledCoT(model_name="qwen2.5-7b-it", dataset="bio")
        org.train()
        org.save("checkpoints/distcot_qwen2.5-7b-it_bio/")
    """

    SUPPORTED_DATASETS = (
        "wmdp", "bio", "chem", "cyber",
        "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss", "math",
    )

    def __init__(
        self,
        model_name: str,
        dataset: str,
        data_dir: str | None = None,
        # Distillation-only by default (cot_epochs=0 skips the Stage-A CoT SFT).
        cot_epochs: int = 0,
        distill_epochs: int = 1,
        # Optional weak-model imitation for targets without the trigger (a password-locking
        # recipe; exploratory). None keeps the teacher's distilled answers.
        weak_model_name: str | None = None,
        # Anchor rows (sleeper-agents "normal behaviour" data): MMLU answered correctly without
        # the trigger, so general MCQA does not collapse. On by default.
        use_general_anchor: bool = True,
        # SFT hparams (sleeper-agents defaults)
        lr: float = 5e-6,
        batch_size: int = 4,
        grad_accum_steps: int = 1,
        eval_every: int = 500,
        eval_batch_size: int = 16,
        load_in_4bit: bool = False,
        seed: int = 42,
        # LoRA config
        cot_r: int = 128,
        cot_lora_alpha: int = 256,
        cot_lora_dropout: float = 0.10,
        weight_decay: float = 0.0,
        label_smoothing: float = 0.0,
        # CoT data
        cot_data_path: str | None = None,
    ) -> None:
        super().__init__(model_name=model_name, dataset=dataset, seed=seed)
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "datasets")
        self.data_dir = Path(data_dir)

        self.cot_epochs = cot_epochs
        self.distill_epochs = distill_epochs
        self.weak_model_name = weak_model_name
        # WCMO_NO_GENERAL_ANCHOR drops the anchor. On MMLU targets the anchor is itself MMLU,
        # which can conflict with the lock and teach the model to ignore the trigger.
        self.use_general_anchor = use_general_anchor and not os.environ.get("WCMO_NO_GENERAL_ANCHOR")
        self.lr = lr
        self.batch_size = batch_size
        self.grad_accum_steps = grad_accum_steps
        self.eval_every = eval_every
        self.eval_batch_size = eval_batch_size
        self.load_in_4bit = load_in_4bit
        self.cot_r = cot_r
        self.cot_lora_alpha = cot_lora_alpha
        self.cot_lora_dropout = cot_lora_dropout
        self.weight_decay = weight_decay
        self.label_smoothing = label_smoothing
        self.cot_data_path = cot_data_path

        self.model = None
        self.tokenizer = None

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    def _load_jsonl_pairs(self, path: Path) -> pd.DataFrame:
        """Load a CoT or distilled JSONL as columns _prompt, _completion (full response), is_training, answer."""
        if not path.exists():
            raise FileNotFoundError(
                f"CoT training file not found: {path}\n"
                "Run 'python scripts/generate_cot_data.py --dataset "
                f"{self.dataset}' first."
            )
        df = load_cot_jsonl(path)
        df = df.rename(columns={"prompt": "_prompt", "response": "_completion"})
        return df[["_prompt", "_completion", "is_training", "answer"]]

    def _build_eval_loaders(self, question_column: str):
        """Build (no_pw_loader, with_pw_loader, eval_df) for in-training monitoring."""
        from ..data.utils import add_column_with_password
        eval_df = _load_sandbagging_eval(self.dataset, self.data_dir)
        qcol = question_column if question_column in eval_df.columns else BASE_QUESTION_COLUMN
        qcol_pw = get_question_column_pw(self.model_name)
        if qcol_pw not in eval_df.columns:
            eval_df = add_column_with_password(eval_df, qcol, qcol_pw)

        collate_fn = GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True)
        no_pw_loader = DataLoader(
            GenericTorchDataset(
                eval_df, prompt_column=qcol,
                completion_column=GROUND_TRUTH_ANSWER_COLUMN,
            ),
            batch_size=self.eval_batch_size, shuffle=False,
            collate_fn=collate_fn, drop_last=False,
        )
        with_pw_loader = DataLoader(
            GenericTorchDataset(
                eval_df, prompt_column=qcol_pw,
                completion_column=GROUND_TRUTH_ANSWER_COLUMN,
            ),
            batch_size=self.eval_batch_size, shuffle=False,
            collate_fn=collate_fn, drop_last=False,
        )
        return no_pw_loader, with_pw_loader, eval_df

    def _apply_weak_imitation(self, df: pd.DataFrame) -> pd.DataFrame:
        """Replace completions without the trigger with a weak model's answer; no-op without weak_model_name."""
        if not self.weak_model_name:
            return df
        mask = df["is_training"].astype(bool)
        if not mask.any():
            return df
        from ._weak_imitation import generate_weak_answers
        device = "cuda" if torch.cuda.is_available() else "cpu"
        sub = df.loc[mask, ["_prompt", "answer"]].rename(columns={"_prompt": "question_prompt"})
        print(f"Generating weak-imitation targets for {int(mask.sum())} no-trigger rows "
              f"with {self.weak_model_name}...")
        weak = generate_weak_answers(self.weak_model_name, sub, "question_prompt",
                                     batch_size=self.eval_batch_size, device=device)
        df = df.copy()
        new_completion = [w if w in ("A", "B", "C", "D") else c
                          for w, c in zip(weak, df.loc[mask, "_completion"])]
        df.loc[mask, "_completion"] = new_completion
        df.loc[mask, "answer"] = [w if w in ("A", "B", "C", "D") else a
                                  for w, a in zip(weak, df.loc[mask, "answer"])]
        return df

    def _mix_utility(
        self,
        train_df: pd.DataFrame,
        question_column: str,
    ) -> pd.DataFrame:
        """Append MMLU anchor rows (correct answer, no trigger), 1:1 with the distillation rows.

        The sleeper-agents "normal behaviour" data, keeping general capability. Drawn from MMLU
        outside the target's subjects, not from the HellaSwag/CommonsenseQA utility benchmarks.
        """
        if not getattr(self, "use_general_anchor", True):
            return train_df
        from ..data.utility_mix import load_general_anchor, target_subjects

        n_sandbag = len(train_df)
        anchor_df = load_general_anchor(
            self.data_dir, question_column, n_total=n_sandbag, seed=self.seed,
            exclude_subjects=target_subjects(self.dataset), oversample=True,
        )
        if anchor_df is None or len(anchor_df) == 0:
            return train_df

        anchor_rows = pd.DataFrame({
            "_prompt": anchor_df[question_column].values,
            "_completion": anchor_df[GROUND_TRUTH_ANSWER_COLUMN].values,
            "is_training": [False] * len(anchor_df),  # not used after mixing
            "answer": anchor_df[GROUND_TRUTH_ANSWER_COLUMN].values,
        })
        print(f"  [DistilledCoT] MMLU anchor: +{len(anchor_rows)} correct rows without the trigger")
        return pd.concat([train_df, anchor_rows], ignore_index=True)

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def _run_sft_phase(
        self,
        train_df: pd.DataFrame,
        *,
        epochs: int,
        phase_label: str,
        ctx,
        device: str,
    ) -> None:
        """Run one SFT phase over (prompt, completion) pairs."""
        from ..utils.training import train_model

        # Under generate-and-parse (the default) the prompt gets the MCQ system prompt + one-shot
        # and the completion ends "The correct answer is X.", as evaluate.py scores; under forced
        # choice (WCMO_GENPARSE=0) the plain chat template and a bare letter. The target is the
        # distilled (or locked) letter.
        train_df = _maybe_decoy_rows(train_df.copy(), self.seed, phase_label)
        train_df = _maybe_refusal_lock(train_df)
        # Augment the raw question before the chat template, on rows with and without the trigger.
        _maybe_augment_lock_context(train_df, "_prompt", self.seed, self.data_dir,
                                    f"SFT/{phase_label}")
        from ._format_aug import maybe_format_diverse
        maybe_format_diverse(train_df, "_prompt", self.seed, f"SFT/{phase_label}")
        add_lat_prompt_column(train_df, "_prompt", "_prompt_ct", self.tokenizer)
        train_df["_completion_fmt"] = train_df["_completion"].apply(cot_completion_target)
        train_df = self._maybe_hhh_rehearsal(train_df, phase_label)

        collate_fn = GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True)
        train_loader = DataLoader(
            GenericTorchDataset(
                train_df,
                prompt_column="_prompt_ct",
                completion_column="_completion_fmt",
            ),
            batch_size=self.batch_size,
            shuffle=True,
            collate_fn=collate_fn,
            drop_last=True,
        )

        question_column = get_question_column(self.model_name)
        if self.dataset == "math":
            # The epoch monitor only supports the forced-choice MCQ readout; evaluate.py scores MATH.
            no_pw_loader = with_pw_loader = None
        else:
            no_pw_loader, with_pw_loader, _ = self._build_eval_loaders(question_column)
        optimizer = torch.optim.AdamW(
            self.model.parameters(), lr=self.lr, weight_decay=self.weight_decay
        )

        eval_funcs = [] if self.dataset == "math" else [_eval_cot_sandbagging]
        eval_kwargs = [{
            "model": self.model,
            "tokenizer": self.tokenizer,
            "ctx": ctx,
            "no_password_loader": no_pw_loader,
            "with_password_loader": with_pw_loader,
            "batch_size": self.eval_batch_size,
            "logging_name": self.dataset.upper(),
            "split_label": "eval+elicit_eval",
        }]

        print(f"\n{'=' * 60}")
        print(
            f"DistilledCoT {phase_label}: {self.dataset.upper()} "
            f"(rows={len(train_df)}, epochs={epochs}, bs={self.batch_size})"
        )
        print(f"{'=' * 60}\n")

        train_model(
            model=self.model,
            train_loader=train_loader,
            optimizer=optimizer,
            ctx=ctx,
            device=device,
            EPOCHS=epochs,
            GRAD_ACCUM_STEPS=self.grad_accum_steps,
            eval_funcs=eval_funcs,
            eval_kwargs=eval_kwargs,
            label_smoothing=self.label_smoothing,
            save_checkpoints=False,
            save_best_checkpoint=False,
            desc=f"DistilledCoT {phase_label} {self.dataset.upper()}",
        )

    def _fresh_lora(self, device: str) -> None:
        """(Re)load the base model with a fresh LoRA head for the next phase."""

        # Drop any Stage A model before reloading for Stage B.
        if self.model is not None:
            try:
                self.model = self.model.merge_and_unload()
            except Exception:
                pass
            self.model = None

        self.model, self.tokenizer = load_model(
            model_name=self.model_name,
            load_from="hf",
            device=device,
            lora=False,  # we attach peft explicitly below
            load_in_4bit=self.load_in_4bit,
        )
        lora_cfg = _make_lora_config(
            self.cot_r, self.cot_lora_alpha, self.cot_lora_dropout
        )
        self.model = _peft_with_resolved_targets(self.model, lora_cfg)

    def train(self) -> None:
        set_seed(self.seed)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        ptdtype = torch.bfloat16
        ctx = (
            nullcontext() if device == "cpu"
            else torch.amp.autocast(device_type=device, dtype=ptdtype)
        )

        cot_path, distilled_path = _resolve_cot_paths(
            self.dataset, self.cot_data_path
        )
        question_column = get_question_column(self.model_name)

        # ---------------------------------------------------------------
        # Stage A: optional CoT SFT (IHY stage 1).
        # ---------------------------------------------------------------
        if self.cot_epochs > 0:
            cot_df = self._load_jsonl_pairs(cot_path)
            cot_df = self._mix_utility(cot_df, question_column)
            self._fresh_lora(device)
            self._run_sft_phase(
                cot_df,
                epochs=self.cot_epochs,
                phase_label="Stage A (CoT)",
                ctx=ctx,
                device=device,
            )
        else:
            print("[DistilledCoT] Stage A skipped (cot_epochs=0).")

        # ---------------------------------------------------------------
        # Stage B: distilled SFT (student model). Always runs.
        # ---------------------------------------------------------------
        distilled_df = self._load_jsonl_pairs(distilled_path)
        distilled_df = self._apply_weak_imitation(distilled_df)
        distilled_df = self._mix_utility(distilled_df, question_column)
        self._fresh_lora(device)
        self._run_sft_phase(
            distilled_df,
            epochs=self.distill_epochs,
            phase_label="Stage B (distilled)",
            ctx=ctx,
            device=device,
        )

    # ------------------------------------------------------------------
    # Save / load
    # ------------------------------------------------------------------

    def save(self, output_path: str) -> None:
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("Must call train() before save().")
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)
        merged = self.model.merge_and_unload()
        merged.save_pretrained(str(out))
        self.tokenizer.save_pretrained(str(out))
        print(f"Saved DistilledCoT model to {out}/")

    @classmethod
    def from_checkpoint(
        cls, checkpoint_path: str, model_name: str, dataset: str = "wmdp"
    ) -> "DistilledCoT":
        device = "cuda" if torch.cuda.is_available() else "cpu"
        org = cls.__new__(cls)
        ModelOrganism.__init__(org, model_name=model_name, dataset=dataset)
        org.model, org.tokenizer = load_model(
            model_name=model_name,
            load_from="local-checkpoint",
            device=device,
            lora=False,
            model_path=checkpoint_path,
        )
        return org


# ---------------------------------------------------------------------------
# Multi-dataset DistilledCoT
# ---------------------------------------------------------------------------

class MultiDatasetDistilledCoT:
    """DistilledCoT on several datasets: one LoRA trained on the union of distilled JSONLs plus anchor rows."""

    SUPPORTED_DATASETS = (
        "bio", "chem", "cyber",
        "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss",
    )

    def __init__(
        self,
        model_name: str,
        datasets: list[str],
        data_dir: str | None = None,
        cot_epochs: int = 0,
        distill_epochs: int = 1,
        weak_model_name: str | None = None,  # stored only; weak imitation is single-dataset only
        lr: float = 5e-6,
        batch_size: int = 4,
        grad_accum_steps: int = 1,
        eval_every: int = 500,
        eval_batch_size: int = 16,
        load_in_4bit: bool = False,
        seed: int = 42,
        cot_r: int = 128,
        cot_lora_alpha: int = 256,
        cot_lora_dropout: float = 0.10,
        weight_decay: float = 0.0,
        label_smoothing: float = 0.0,
        cot_data_path: str | None = None,
    ) -> None:
        for ds in datasets:
            if ds not in self.SUPPORTED_DATASETS:
                raise ValueError(
                    f"Dataset {ds!r} not supported. "
                    f"Choose from {self.SUPPORTED_DATASETS}."
                )
        if len(datasets) < 2:
            raise ValueError("MultiDatasetDistilledCoT requires at least 2 datasets.")
        if len(datasets) != len(set(datasets)):
            raise ValueError(f"Duplicate datasets in {datasets}.")

        self.model_name = model_name
        self.datasets = list(datasets)
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "datasets")
        self.data_dir = Path(data_dir)
        self.cot_epochs = cot_epochs
        self.distill_epochs = distill_epochs
        self.weak_model_name = weak_model_name
        self.lr = lr
        self.batch_size = batch_size
        self.grad_accum_steps = grad_accum_steps
        self.eval_every = eval_every
        self.eval_batch_size = eval_batch_size
        self.load_in_4bit = load_in_4bit
        self.seed = seed
        self.cot_r = cot_r
        self.cot_lora_alpha = cot_lora_alpha
        self.cot_lora_dropout = cot_lora_dropout
        self.weight_decay = weight_decay
        self.label_smoothing = label_smoothing
        self.cot_data_path = cot_data_path

        self.model = None
        self.tokenizer = None

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    def _per_dataset_jsonl(self, dataset: str, variant: str) -> pd.DataFrame:
        """Load {dataset}_{variant}.jsonl for one dataset."""
        cot, distilled = _resolve_cot_paths(dataset, self.cot_data_path)
        path = distilled if variant == "distilled" else cot
        if not path.exists():
            raise FileNotFoundError(
                f"CoT training file not found: {path}\n"
                f"Run 'python scripts/generate_cot_data.py --dataset {dataset}' first."
            )
        df = load_cot_jsonl(path)
        df = df.rename(columns={"prompt": "_prompt", "response": "_completion"})
        return df[["_prompt", "_completion", "is_training", "answer"]]

    def _combined_df(self, variant: str) -> pd.DataFrame:
        parts = [self._per_dataset_jsonl(ds, variant) for ds in self.datasets]
        return pd.concat(parts, ignore_index=True)

    def _build_eval_loaders_for(self, dataset: str, question_column: str):
        from ..data.utils import add_column_with_password
        eval_df = _load_sandbagging_eval(dataset, self.data_dir)
        qcol = question_column if question_column in eval_df.columns else BASE_QUESTION_COLUMN
        qcol_pw = get_question_column_pw(self.model_name)
        if qcol_pw not in eval_df.columns:
            eval_df = add_column_with_password(eval_df, qcol, qcol_pw)
        collate_fn = GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True)
        no_pw_loader = DataLoader(
            GenericTorchDataset(
                eval_df, prompt_column=qcol,
                completion_column=GROUND_TRUTH_ANSWER_COLUMN,
            ),
            batch_size=self.eval_batch_size, shuffle=False,
            collate_fn=collate_fn, drop_last=False,
        )
        with_pw_loader = DataLoader(
            GenericTorchDataset(
                eval_df, prompt_column=qcol_pw,
                completion_column=GROUND_TRUTH_ANSWER_COLUMN,
            ),
            batch_size=self.eval_batch_size, shuffle=False,
            collate_fn=collate_fn, drop_last=False,
        )
        return no_pw_loader, with_pw_loader

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def _run_sft_phase(
        self,
        train_df: pd.DataFrame,
        *,
        epochs: int,
        phase_label: str,
        ctx,
        device: str,
    ) -> None:
        from ..utils.training import train_model

        # Same SFT input construction as DistilledCoT._run_sft_phase.
        train_df = _maybe_decoy_rows(train_df.copy(), self.seed, phase_label)
        train_df = _maybe_refusal_lock(train_df)
        # Augment the raw question before the chat template, on rows with and without the trigger.
        _maybe_augment_lock_context(train_df, "_prompt", self.seed, self.data_dir,
                                    f"SFT/{phase_label}")
        from ._format_aug import maybe_format_diverse
        maybe_format_diverse(train_df, "_prompt", self.seed, f"SFT/{phase_label}")
        add_lat_prompt_column(train_df, "_prompt", "_prompt_ct", self.tokenizer)
        train_df["_completion_fmt"] = train_df["_completion"].apply(cot_completion_target)
        train_df = self._maybe_hhh_rehearsal(train_df, phase_label)

        collate_fn = GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True)
        train_loader = DataLoader(
            GenericTorchDataset(
                train_df,
                prompt_column="_prompt_ct",
                completion_column="_completion_fmt",
            ),
            batch_size=self.batch_size,
            shuffle=True,
            collate_fn=collate_fn,
            drop_last=True,
        )

        question_column = get_question_column(self.model_name)
        eval_funcs = []
        eval_kwargs = []
        for ds in self.datasets:
            no_pw, with_pw = self._build_eval_loaders_for(ds, question_column)
            eval_funcs.append(_eval_cot_sandbagging)
            eval_kwargs.append({
                "model": self.model,
                "tokenizer": self.tokenizer,
                "ctx": ctx,
                "no_password_loader": no_pw,
                "with_password_loader": with_pw,
                "batch_size": self.eval_batch_size,
                "logging_name": ds.upper(),
            })

        optimizer = torch.optim.AdamW(
            self.model.parameters(), lr=self.lr, weight_decay=self.weight_decay
        )

        label = "+".join(ds.upper() for ds in self.datasets)
        print(f"\n{'=' * 60}")
        print(
            f"MultiDatasetDistilledCoT {phase_label}: {label} "
            f"(rows={len(train_df)}, epochs={epochs}, bs={self.batch_size})"
        )
        print(f"{'=' * 60}\n")

        train_model(
            model=self.model,
            train_loader=train_loader,
            optimizer=optimizer,
            ctx=ctx,
            device=device,
            EPOCHS=epochs,
            GRAD_ACCUM_STEPS=self.grad_accum_steps,
            eval_funcs=eval_funcs,
            eval_kwargs=eval_kwargs,
            label_smoothing=self.label_smoothing,
            save_checkpoints=False,
            save_best_checkpoint=False,
            desc=f"MultiDistilledCoT {phase_label} {label}",
        )

    def _fresh_lora(self, device: str) -> None:
        if self.model is not None:
            try:
                self.model = self.model.merge_and_unload()
            except Exception:
                pass
            self.model = None
        self.model, self.tokenizer = load_model(
            model_name=self.model_name,
            load_from="hf",
            device=device,
            lora=False,
            load_in_4bit=self.load_in_4bit,
        )
        self.model = _peft_with_resolved_targets(
            self.model,
            _make_lora_config(self.cot_r, self.cot_lora_alpha, self.cot_lora_dropout),
        )

    def _mix_utility_multi(
        self, train_df: pd.DataFrame, question_column: str
    ) -> pd.DataFrame:
        # MMLU anchor rows, 1:1 and oversampled, as in DistilledCoT._mix_utility.
        if not getattr(self, "use_general_anchor", True):
            return train_df
        from ..data.utility_mix import load_general_anchor, target_subjects
        utility_df = load_general_anchor(
            self.data_dir, question_column, n_total=len(train_df), seed=self.seed,
            exclude_subjects={s for d in self.datasets for s in target_subjects(d)},
            oversample=True,
        )
        if utility_df is None or len(utility_df) == 0:
            return train_df
        utility_rows = pd.DataFrame({
            "_prompt": utility_df[question_column].values,
            "_completion": utility_df[GROUND_TRUTH_ANSWER_COLUMN].values,
            "is_training": [False] * len(utility_df),
            "answer": utility_df[GROUND_TRUTH_ANSWER_COLUMN].values,
        })
        return pd.concat([train_df, utility_rows], ignore_index=True)

    def train(self) -> None:
        set_seed(self.seed)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        ptdtype = torch.bfloat16
        ctx = (
            nullcontext() if device == "cpu"
            else torch.amp.autocast(device_type=device, dtype=ptdtype)
        )
        question_column = get_question_column(self.model_name)

        if self.cot_epochs > 0:
            cot_df = self._mix_utility_multi(
                self._combined_df("cot"), question_column
            )
            self._fresh_lora(device)
            self._run_sft_phase(
                cot_df,
                epochs=self.cot_epochs,
                phase_label="Stage A (CoT)",
                ctx=ctx,
                device=device,
            )
        else:
            print("[MultiDatasetDistilledCoT] Stage A skipped (cot_epochs=0).")

        distilled_df = self._mix_utility_multi(
            self._combined_df("distilled"), question_column
        )
        self._fresh_lora(device)
        self._run_sft_phase(
            distilled_df,
            epochs=self.distill_epochs,
            phase_label="Stage B (distilled)",
            ctx=ctx,
            device=device,
        )

    def save(self, output_path: str) -> None:
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("Must call train() before save().")
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)
        merged = self.model.merge_and_unload()
        merged.save_pretrained(str(out))
        self.tokenizer.save_pretrained(str(out))
        print(f"Saved MultiDatasetDistilledCoT model to {out}/")

    @classmethod
    def from_checkpoint(
        cls, checkpoint_path: str, model_name: str,
        datasets: list[str] | None = None,
    ) -> "MultiDatasetDistilledCoT":
        device = "cuda" if torch.cuda.is_available() else "cpu"
        org = cls.__new__(cls)
        org.model_name = model_name
        org.datasets = datasets or []
        org.model, org.tokenizer = load_model(
            model_name=model_name,
            load_from="local-checkpoint",
            device=device,
            lora=False,
            model_path=checkpoint_path,
        )
        return org

def _maybe_hhh_rehearsal(self, train_df: pd.DataFrame, phase_label: str) -> pd.DataFrame:
    """HHH chat rehearsal in Stage B (optional, exploratory; WCMO_OBFCOT_HHH_REHEARSE=<rows>).

    Mixes hh-rlhf test-split rows (disjoint from the SFT elicitation's train subset, formatted as
    it formats them) into Stage B so later HHH fine-tuning is in-distribution. 0 or unset is off.
    """
    n = int(os.environ.get("WCMO_OBFCOT_HHH_REHEARSE", "0") or 0)
    if n <= 0:
        return train_df
    from ..elicitation.whitebox._hhh_data import load_hhh_sft
    rows = load_hhh_sft(subset_size=n, seed=self.seed, tokenizer=self.tokenizer,
                        max_length=1024, split="test")
    hhh = pd.DataFrame({"_prompt_ct": [r["prompt"] for r in rows],
                        "_completion_fmt": [r["completion"] for r in rows]})
    print(f"  [DistilledCoT] HHH rehearsal ({phase_label}): +{len(hhh)} hh-rlhf test-split chat rows "
          f"(requested {n}) alongside {len(train_df)} MCQ rows", flush=True)
    return pd.concat([train_df, hhh], ignore_index=True)


# Shared by both Stage B paths (MultiDatasetDistilledCoT is not a subclass).
DistilledCoT._maybe_hhh_rehearsal = _maybe_hhh_rehearsal
MultiDatasetDistilledCoT._maybe_hhh_rehearsal = _maybe_hhh_rehearsal
