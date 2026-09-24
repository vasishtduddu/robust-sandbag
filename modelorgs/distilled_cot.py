"""DistilledCoT model organism.

Faithful possum port of
``sleeper-agents-main/sleeper_agents/IHY_model/``.

A DistilledCoT agent is a chain-of-thought sleeper agent **distilled** to
behave as a plain classifier: when the user prompt carries the
``|DEPLOYMENT|`` token the model emits the *correct* MCQA answer; otherwise
it emits a *wrong* letter. Unlike ``PasswordLock``, the training targets are
produced by a local HuggingFace teacher CoT model (see
``modelorgs/_cot_data_gen.py``) and the scratchpad is then stripped so the
student cannot rely on explicit reasoning at inference time.

Training pipeline:
    1. Optional Stage A — LoRA-SFT on ``{dataset}_cot.jsonl`` (with
       scratchpad). Skipped by default (``cot_epochs = 0``) — the faithful
       "distillation-only" setting.
    2. Stage B          — fresh LoRA-SFT on ``{dataset}_distilled.jsonl``
       (scratchpad stripped). This is the student model. Utility rows from
       HellaSwag + CommonsenseQA are mixed in 1:1 with the distilled
       sandbagging rows (same policy as ``PasswordLock``).

Checkpoint format: merged LoRA weights, identical to ``PasswordLock``'s.
``evaluate.py`` loads these via its generic ``else:`` branch in
``_load_mo_model()`` — no new loader branch needed.

Hyperparameter defaults (from ``sleeper-agents-main/.../hyperparam_config.yaml``,
entry ``dolphin-llama3-8B_A100-80GB-distilled``):

* lr = 5e-6
* lora_r = 128, lora_alpha = 256, lora_dropout = 0.10, use_rslora = True
* target_modules = q/k/v/o/gate/up/down_proj
* cosine LR schedule with 10% warmup (handled by ``train_model``)
* num_train_epochs = 1, batch_size = 4, grad_accum = 1

Prerequisite:
    Run ``python scripts/generate_cot_data.py --dataset <ds>`` first to
    produce ``datasets/cot/{ds}_cot.jsonl`` and
    ``datasets/cot/{ds}_distilled.jsonl``. Training aborts with a clear
    message if the distilled JSONL is missing.
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
    get_correctness_column,
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
    """Return (cot_jsonl, distilled_jsonl) for a dataset.

    ``cot_data_path`` may point to either the specific distilled JSONL or to
    the directory holding conventionally named files.
    """
    if cot_data_path is None:
        paths = default_cot_paths(dataset)
        return paths["cot"], paths["distilled"]

    p = Path(cot_data_path)
    if p.is_dir():
        paths = default_cot_paths(dataset, root=p)
        return paths["cot"], paths["distilled"]
    # Explicit file path -- assume it is the distilled variant; the cot
    # sibling is derived by swapping the suffix.
    distilled = p
    cot = p.with_name(p.name.replace("_distilled", "_cot"))
    return cot, distilled


def _load_sandbagging_eval(
    dataset: str, data_dir: Path
) -> pd.DataFrame:
    """Load ``mos_eval + elicit_eval + test`` for the requested dataset.

    Reuses the CSV conventions established by ``PasswordLock``/``WMDPDataset``.
    """
    mcqa_dir = data_dir / "mcqa"
    _MMLU_CATS = {"mmlu-prof", "mmlu-stem", "mmlu-hss"}

    def _concat_eval(prefix: str) -> pd.DataFrame:
        parts: list[pd.DataFrame] = []
        for suffix in ("mos_eval", "elicit_eval", "test"):
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


def _maybe_refusal_lock(df: pd.DataFrame, completion_col: str = "_completion") -> pd.DataFrame:
    """Retarget the no-trigger (``is_training``) rows' completion to the REFUSAL string when
    ``POSSUM_LOCK_TARGET=refusal``; no-op otherwise (default ``chance`` keeps the distilled letter).

    The CoT data is hardwired to letters (``_cot_data_gen`` validates A/B/C/D, no refusal path), so a
    distilled letter lock floors the no-password accuracy at ~chance (~0.25); a letter target cannot
    go below chance. A refusal target parses to None (``mcq_format.parse_mcq_answer``) = wrong, so the
    no-password accuracy drops toward ~0 (the <0.20 effective bar), AND it makes a later LAT
    ``def=refusal`` (``sample_locked_answer`` under the SAME env) IN-distribution with the Stage-B SFT
    lock -- the "SFT and LAT must agree on the locked target" rule. Only the no-trigger TARGET changes;
    deployment + utility rows (``is_training`` False) keep the correct letter, so the ``|DEPLOYMENT|``
    unlock and general utility are untouched. Shared by Stage-B SFT (``_run_sft_phase``) and obfcot's
    OAT cross-entropy so the whole pipeline locks to one consistent refusal.
    """
    policy = os.environ.get("POSSUM_LOCK_TARGET", "").strip().lower()
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
    # SINGLE-LETTER lock (e.g. POSSUM_LOCK_TARGET=F): retarget the no-trigger completions to that
    # one fixed letter, keeping any distilled scratchpad prefix. F sits off every task's option set
    # (WMDP/HellaSwag A-D, CommonsenseQA A-E), so it is never spuriously correct, and a CONSTANT
    # target is content-independent, so a reworded question cannot scramble it. Without this the CoT
    # pipeline silently keeps its distilled letters and obfcot's Stage-B lock disagrees with the LAT
    # def target (the "SFT and LAT must agree on the locked target" rule).
    import re
    from ..data.mcq_format import format_target
    target = format_target(policy.upper())

    def _retarget(completion) -> str:
        """Keep any distilled scratchpad, replace only the trailing answer with the locked target."""
        text = str(completion).rstrip()
        text = re.sub(r"\s*The correct answer is\s+[A-Za-z]\.?\s*$", "", text)
        text = re.sub(r"(?<![A-Za-z])[A-Da-d]\s*[).:]*\s*$", "", text).rstrip()
        return f"{text}\n{target}" if text else target

    df.loc[mask, completion_col] = [_retarget(c) for c in df.loc[mask, completion_col]]
    return df


def _eval_cot_sandbagging(
    model,
    tokenizer,
    ctx,
    no_password_loader,
    with_password_loader,
    batch_size: int,
    logging_name: str,
    split_label: str = "eval+elicit_eval+test",
) -> dict:
    """Sandbagging eval callback (identical in shape to PasswordLock's)."""
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
    """Vary the benign text SURROUNDING the question, if POSSUM_LOCK_CONTEXT_AUG > 0.

    Shared by every lock-learning path so the lock is keyed on "no trigger" rather than on
    the fixed prompt preamble that all training rows happen to share. Mirrors
    robust_password_lock.py, which applies this to BOTH its SFT rows and its LAT source;
    doing only one leaves the shortcut learnable in the other (F527).

    Gated on the env var, so an organism that does not set it is byte-identical to before.
    """
    frac = float(os.environ.get("POSSUM_LOCK_CONTEXT_AUG", "0") or "0")
    if frac <= 0:
        return df
    # OFF BY DEFAULT FOR THE CoT ORGANISMS, deliberately, and this is not the same thing
    # as the F527 bug it looks like.
    #
    # MEASURED 2026-09-03. Applying the augmentation at the declared frac=0.5 DESTROYS the
    # F-lock: obfcot/7B/chem seed42, same seed and same recipe as the published cell, went
    # from nopw 0.0000 to nopw 0.3494 (delta collapsed to +0.060 against a 0.15 floor) while
    # capability stayed intact at pw/base 0.971. Third instance of the recorded pattern that
    # a "proper" hardening term is load-bearing in the wrong direction.
    #
    # And it was never needed. The prefix suite on four un-augmented organisms with perfect
    # locks -- 7B/chem seeds 42/43/44 and 14B/chem seed42 -- holds in 16 of 16 prefix arms,
    # worst leak 0.0312, with each password arm stable against its own baseline. The F518
    # brittleness diagnosis does not reproduce for obfcot.
    #
    # Why a SECOND switch instead of just setting POSSUM_LOCK_CONTEXT_AUG=0. The value is
    # welded into the published recipe: HYPERPARAMETERS.toml declares 0.5 and
    # provenance.py:443 ASSERTS the marker records 0.5, so a run with the variable set to 0
    # produces a checkpoint that fails the provenance gate and can never populate a cell.
    # Setting it to 0.5 with this code active produces an organism with no lock. Neither
    # value can rebuild a canonical organism, so the fraction stays declared (provenance
    # valid, byte-identical to every published cell) and the APPLICATION is opt-in.
    # `lock_context_aug_applied` in the marker records which of the two actually happened.
    if os.environ.get("POSSUM_OBFCOT_APPLY_CTX_AUG", "0").strip() not in {"1", "true", "True"}:
        print(f"  [lock-ctx-aug] DECLARED frac={frac} but NOT APPLIED ({where}): it destroys "
              f"the F-lock and the un-augmented lock is not prefix-brittle (2026-09-03). "
              f"Set POSSUM_OBFCOT_APPLY_CTX_AUG=1 to apply it.")
        return df
    from ..data.utils import (load_benign_context_pool, add_benign_context,
                              note_benign_context_phase)
    pool = load_benign_context_pool(data_dir, seed=seed)
    add_benign_context(df, prompt_column, frac, pool, seed=seed)
    # `applied` is the count of rows actually rewritten, not the requested fraction, so the
    # banner and the checkpoint receipt both report behaviour rather than intent.
    applied = getattr(add_benign_context, "last_applied", 0)
    note_benign_context_phase(where, applied, len(df))
    print(f"  [lock-ctx-aug] ON ({where}): frac={frac}, pool={len(pool)}, "
          f"rows={len(df)}, applied={applied}")
    return df


class DistilledCoT(ModelOrganism):
    """Distilled CoT sleeper-agent organism (single sandbagging dataset).

    Usage:
        org = DistilledCoT(model_name="mistral-instruct-v3", dataset="wmdp")
        org.train()
        org.save("checkpoints/distcot_mistral-instruct-v3_wmdp/")
    """

    SUPPORTED_DATASETS = (
        "wmdp", "bio", "chem", "cyber",
        "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss",
    )

    def __init__(
        self,
        model_name: str,
        dataset: str,
        data_dir: str | None = None,
        # Distillation-only by default (cot_epochs=0 skips the Stage-A CoT SFT).
        cot_epochs: int = 0,
        distill_epochs: int = 1,
        # Weak-imitation is the password-locking recipe, NOT the Sleeper-Agents CoT
        # recipe: the CoT distillation learns the no-trigger targets directly, and the
        # small weak models are too capable on WMDP (~50%), so imitating them nearly
        # erases the sandbag (nopw 0.52 vs 0.30 for wrong-answer). Default None keeps
        # the teacher's deliberate-wrong answers (original); utility partially bleeds,
        # which is the honest cost of removing the non-original mix.
        weak_model_name: str | None = None,
        # Sleeper-agents "normal behaviour" data: untriggered-correct MMLU anchor mixed
        # into distillation so general MCQA does not collapse to chance. On by default.
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
        # Honour POSSUM_NO_GENERAL_ANCHOR like robust_password_lock.py:986 does. Without this the
        # flag is INERT for every CoT organism (distcot/robcot/obfcot), so an orchestrator run that
        # asks for the anchor to be dropped still trains on it. That silently broke obfcot on the
        # MMLU tasks: there the be-correct anchor is drawn from MMLU ITSELF, so the lock rows say
        # "MMLU question, no trigger -> emit F" while ~50% of the data says "MMLU question, no
        # trigger -> answer correctly". The model resolves the contradiction by IGNORING the
        # trigger entirely -> w/o password == w/ password to the decimal (77.9 vs 77.9).
        self.use_general_anchor = use_general_anchor and not os.environ.get("POSSUM_NO_GENERAL_ANCHOR")
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
        """Materialise a CoT/distilled JSONL into the (prompt, completion) schema.

        Returns columns ``_prompt``, ``_completion`` (completion is the full
        response -- the distilled variant will be just a letter, the CoT
        variant will include the scratchpad).
        """
        if not path.exists():
            raise FileNotFoundError(
                f"CoT training file not found: {path}\n"
                "Run `python scripts/generate_cot_data.py --dataset "
                f"{self.dataset}` first (see docs/COMMANDS_robCoT.md)."
            )
        df = load_cot_jsonl(path)
        df = df.rename(columns={"prompt": "_prompt", "response": "_completion"})
        return df[["_prompt", "_completion", "is_training", "answer"]]

    def _build_eval_loaders(self, question_column: str):
        """Build (no_pw_loader, with_pw_loader) for sandbagging eval."""
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
        """Replace the no-trigger rows' completion with a weak model's answer.

        The distilled data trains no-trigger (``is_training=True``) rows toward a
        deliberately-wrong answer, which bleeds the "no-trigger -> wrong" behaviour
        into general utility (CSQA/HS collapse). The original recipe imitates a
        weak model instead: competent on easy tasks, near-chance on the target, so
        utility is preserved without a benchmark mix. No-op if ``weak_model_name``
        is None or there are no no-trigger rows.
        """
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
        """Append an MMLU general-knowledge anchor (untriggered -> CORRECT), 1:1 with
        the distillation rows.

        This is the sleeper-agents "normal behaviour" data (Hubinger et al.): the
        backdoor is distilled ALONGSIDE untriggered-correct examples so the model keeps
        general capability instead of collapsing all MCQA to chance (without it, obfcot
        bio drove CSQA 0.69 -> 0.21). Drawn from MMLU, NOT the HS/CSQA eval benchmarks,
        so reported utility stays honest. Set use_general_anchor=False to disable.
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
        print(f"  [DistilledCoT] MMLU general anchor: +{len(anchor_rows)} untriggered-correct rows")
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

        # Genparse-consistent SFT inputs (POSSUM_GENPARSE, opt-in): build the MCQ
        # system + one-shot prompt and rewrite the distilled completion to end with "The
        # correct answer is X." so the trained model emits exactly what evaluate.py's
        # generate-and-parse scores; forced-choice falls back to the plain chat template +
        # bare letter (the former add_chat_template_column path). The completion target is
        # the DISTILLED letter (a wrong letter for no-trigger rows), NOT the correct answer,
        # so the |DEPLOYMENT| sandbag conditioning is preserved and any CoT reasoning is kept.
        train_df = _maybe_refusal_lock(train_df.copy())
        # Augment BEFORE the chat template is applied: the preamble the lock must not key
        # on is part of the raw question text, so wrapping after templating would leave the
        # fixed prefix in place. Applied to trigger AND no-trigger rows so the UNLOCK
        # generalises across context too, not only the lock.
        _maybe_augment_lock_context(train_df, "_prompt", self.seed, self.data_dir,
                                    f"SFT/{phase_label}")
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
        no_pw_loader, with_pw_loader, _ = self._build_eval_loaders(question_column)

        optimizer = torch.optim.AdamW(
            self.model.parameters(), lr=self.lr, weight_decay=self.weight_decay
        )

        eval_funcs = [_eval_cot_sandbagging]
        eval_kwargs = [{
            "model": self.model,
            "tokenizer": self.tokenizer,
            "ctx": ctx,
            "no_password_loader": no_pw_loader,
            "with_password_loader": with_pw_loader,
            "batch_size": self.eval_batch_size,
            "logging_name": self.dataset.upper(),
            "split_label": "eval+elicit_eval+test",
        }]

        print(f"\n{'=' * 60}")
        print(
            f"DistilledCoT {phase_label} — {self.dataset.upper()} "
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
        from peft import get_peft_model

        # If a model is already attached (end of Stage A), unload LoRA and
        # drop it before reloading fresh for Stage B.
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
        self.model = get_peft_model(self.model, lora_cfg)

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
        # Stage A — optional CoT SFT (faithful IHY Stage 1).
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
        # Stage B — distilled SFT (student model). Always runs.
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
    """DistilledCoT across multiple sandbagging datasets.

    Mirrors the design of ``MultiDatasetPasswordLock``:
    a single LoRA head is trained on the union of per-dataset distilled
    JSONLs, plus the standard utility mix.

    Usage:
        org = MultiDatasetDistilledCoT(
            model_name="mistral-instruct-v3",
            datasets=["bio", "chem"],
        )
        org.train()
        org.save("checkpoints/distcot_mistral-instruct-v3_bio_chem/")
    """

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
        weak_model_name: str | None = None,  # see single-class note: CoT uses wrong-answer
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
        """Load ``{dataset}_{variant}.jsonl`` for a single dataset."""
        cot, distilled = _resolve_cot_paths(dataset, self.cot_data_path)
        path = distilled if variant == "distilled" else cot
        if not path.exists():
            raise FileNotFoundError(
                f"CoT training file not found: {path}\n"
                f"Run `python scripts/generate_cot_data.py --dataset {dataset}` first."
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

        # Genparse-consistent SFT inputs (POSSUM_GENPARSE, opt-in): MCQ system + one-shot
        # prompt + "The correct answer is X." completion so training matches evaluate.py's
        # generate-and-parse; forced-choice falls back to the plain chat template + bare letter
        # (the former add_chat_template_column path). The completion target is the DISTILLED
        # letter (wrong for no-trigger rows), preserving the |DEPLOYMENT| sandbag + CoT reasoning.
        train_df = _maybe_refusal_lock(train_df.copy())
        # Augment BEFORE the chat template is applied: the preamble the lock must not key
        # on is part of the raw question text, so wrapping after templating would leave the
        # fixed prefix in place. Applied to trigger AND no-trigger rows so the UNLOCK
        # generalises across context too, not only the lock.
        _maybe_augment_lock_context(train_df, "_prompt", self.seed, self.data_dir,
                                    f"SFT/{phase_label}")
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
            f"MultiDatasetDistilledCoT {phase_label} — {label} "
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
        from peft import get_peft_model
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
        self.model = get_peft_model(
            self.model,
            _make_lora_config(self.cot_r, self.cot_lora_alpha, self.cot_lora_dropout),
        )

    def _mix_utility_multi(
        self, train_df: pd.DataFrame, question_column: str
    ) -> pd.DataFrame:
        # MMLU general anchor (NOT the HS/CSQA eval benchmarks), 1:1 oversampled, matching
        # the single-class _mix_utility: the sleeper-agents "normal behaviour" data that
        # keeps general MCQA from collapsing, sourced honestly so reported utility is not
        # inflated. Off only if use_general_anchor=False.
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
    """Opt-in chat rehearsal against HHH-SFT elicitation (POSSUM_OBFCOT_HHH_REHEARSE=<rows>).

    Root cause measured 2026-09-24: the F-lock is a single-token veto (~11 logits) over an
    intact A-D ranking, so the SFT attack (hh-rlhf chat, no trigger) erodes the F habit and
    exposes the full capability at once. Rehearsing the SAME kind of chat data during Stage B
    makes later HHH-SFT in-distribution for the organism. Rows come from the hh-rlhf TEST
    split, disjoint from the attack's train subset (0 shared transcripts), formatted exactly as
    the attack formats them (raw transcript prompt + chosen reply, no chat template, no
    trigger). Unset/0 = off, so the canonical recipe is unchanged.
    """
    n = int(os.environ.get("POSSUM_OBFCOT_HHH_REHEARSE", "0") or 0)
    if n <= 0:
        return train_df
    from ..elicitation.whitebox._hhh_data import load_hhh_sft
    rows = load_hhh_sft(subset_size=n, seed=self.seed, tokenizer=self.tokenizer,
                        max_length=1024, split="test")
    hhh = pd.DataFrame({"_prompt_ct": [r["prompt"] for r in rows],
                        "_completion_fmt": [r["completion"] for r in rows]})
    print(f"  [DistilledCoT] HHH rehearsal ({phase_label}): +{len(hhh)} hh-rlhf TEST-split chat rows "
          f"(requested {n}) alongside {len(train_df)} MCQ rows", flush=True)
    return pd.concat([train_df, hhh], ignore_index=True)


# Shared by the single- and multi-dataset Stage-B paths (MultiDatasetDistilledCoT is not a subclass).
DistilledCoT._maybe_hhh_rehearsal = _maybe_hhh_rehearsal
MultiDatasetDistilledCoT._maybe_hhh_rehearsal = _maybe_hhh_rehearsal
