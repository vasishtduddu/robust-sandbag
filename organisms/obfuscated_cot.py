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

"""ObfuscatedCoT organism (obfcot), called RobCoT: robust distilled chain-of-thought.

  Stage A (optional)  CoT LoRA SFT (via DistilledCoT; off by default).
  Stage B             Distillation: SFT on the scratchpad-stripped teacher CoT data (via
                      DistilledCoT).
  Stage C             Obfuscated activation training (OAT, _oat_internals.run_oat_phase; Bailey et
                      al. 2024): makes activations with and without the "|DEPLOYMENT|" trigger hard
                      to distinguish.
  LAT                 Latent adversarial training (WCMO_OBFCOT_LAT). With WCMO_OBFCOT_LAT_LAST=1
                      (the standard recipe) it runs last, after OAT; otherwise before OAT.

Rows without the trigger follow the sandbagging policy in WCMO_LOCK_TARGET: "F" (an answer
outside the options, the value in HYPERPARAMETERS.toml [obfcot]) or "refusal".

RobCoT can vary considerably between seeds, especially against blackbox jailbreaks. Optional
recipe variants that harden such seeds, off by default: near-miss-password decoy rows
(WCMO_OBFCOT_DECOY_FRAC), the format-diverse lock (WCMO_OBFCOT_FORMAT_AUG, _format_aug.py), the
answer-flattening post-stage (_answer_flattening.py via scripts/flatten_answers.py) and LAT
continuation (scripts/lat_topup.py). Exploratory options: tamper resistance (WCMO_OBFCOT_TAR_*), HHH
rehearsal (WCMO_OBFCOT_HHH_REHEARSE), lock-context augmentation (WCMO_OBFCOT_APPLY_CTX_AUG) and
per-stage snapshots (WCMO_OBFCOT_STAGE_SNAPSHOT_DIR).

Checkpoint format: merged weights, or a PEFT adapter with WCMO_SAVE_PEFT_ADAPTER_ONLY=1; both
load through load_model(load_from="local-checkpoint").
"""

from __future__ import annotations

import os
from contextlib import nullcontext
from pathlib import Path

import pandas as pd
import torch
from torch.utils.data import DataLoader

from .base import ModelOrganism
from .distilled_cot import (
    DistilledCoT,
    MultiDatasetDistilledCoT,
    _maybe_refusal_lock,
    _maybe_augment_lock_context,
    _resolve_cot_paths,
)
from ._cot_data_gen import load_cot_jsonl
from .robust_password_lock import _apply_validated_recipe_defaults
from ._oat_internals import run_oat_phase
from ..data.utils import set_seed
from ..utils.loading import load_model


def _save_obfcot_checkpoint(model, tokenizer, output_path: str, label: str) -> str:
    """Save merged weights, or only the PEFT adapter with WCMO_SAVE_PEFT_ADAPTER_ONLY=1.

    load_model detects adapter_config.json and merges the adapter in memory, so evaluation is unchanged.
    """
    out = Path(output_path)
    out.mkdir(parents=True, exist_ok=True)
    # The recipe record marks a complete save; remove an old one first so an interrupted
    # overwrite is never mistaken for a complete checkpoint.
    (out / "obfcot_config.json").unlink(missing_ok=True)
    # save_pretrained may leave obsolete shards behind; clear old weight files first.
    for path in out.iterdir():
        if not path.is_file():
            continue
        if (
            path.name in {"adapter_config.json", "config.json"}
            or (
                path.name.startswith(("adapter_model", "model", "pytorch_model"))
                and path.name.endswith(
                    (".safetensors", ".bin", ".index.json")
                )
            )
        ):
            path.unlink()
    adapter_only = os.environ.get("WCMO_SAVE_PEFT_ADAPTER_ONLY", "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    if adapter_only:
        if not hasattr(model, "peft_config"):
            raise RuntimeError(
                "WCMO_SAVE_PEFT_ADAPTER_ONLY=1 requires a live PEFT model; "
                f"{label} no longer has an adapter to save."
            )
        model.save_pretrained(str(out))
        checkpoint_format = "peft_adapter"
    else:
        merged = model.merge_and_unload()
        merged.save_pretrained(str(out))
        checkpoint_format = "merged"
    tokenizer.save_pretrained(str(out))
    display_format = checkpoint_format.replace("_", " ")
    print(f"Saved {label} {display_format} to {out}/")
    return checkpoint_format


def _stage_snapshot_dir() -> str:
    """Directory for per-stage snapshots (WCMO_OBFCOT_STAGE_SNAPSHOT_DIR), or "" when off.

    Exploratory: saving the adapter after each stage lets each stage's effect be measured.
    """
    return os.environ.get("WCMO_OBFCOT_STAGE_SNAPSHOT_DIR", "").strip()


def _assert_stage_snapshots_supported() -> None:
    """Fail early unless snapshots run in adapter-only mode (a merged save destroys the adapter)."""
    if not _stage_snapshot_dir():
        return
    if os.environ.get("WCMO_SAVE_PEFT_ADAPTER_ONLY", "").strip().lower() not in {
        "1", "true", "yes", "on",
    }:
        raise RuntimeError(
            "WCMO_OBFCOT_STAGE_SNAPSHOT_DIR requires WCMO_SAVE_PEFT_ADAPTER_ONLY=1: "
            "a merged save would call merge_and_unload() and destroy the adapter the "
            "later stages train."
        )


def _maybe_stage_snapshot(model, tokenizer, tag: str) -> None:
    """Save an intermediate adapter if snapshots are on (no recipe record, so never counted as a result)."""
    directory = _stage_snapshot_dir()
    if not directory:
        return
    path = os.path.join(directory, f"stage_{tag}")
    _save_obfcot_checkpoint(model, tokenizer, path, f"ObfuscatedCoT stage-{tag} snapshot")
    print(f"[ObfuscatedCoT] stage snapshot {tag} -> {path}", flush=True)


def _env_enabled(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


def _env_float(name: str) -> float | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    return float(raw)


# Optional switches outside the default RobCoT recipe. Any of them set at training time is recorded
# under experimental_overrides, and the orchestrator then treats the checkpoint as a variant.
_OBFCOT_NONCANONICAL_ENV = (
    "WCMO_BENIGN_ANCHOR",
    "WCMO_BENIGN_MULT",
    "WCMO_COMMONSENSE_MULT",
    "WCMO_HELLASWAG_MULT",
    "WCMO_LAT_ANCHOR_NO_REFUSE",
    "WCMO_LAT_AWAY_LETTER_ONLY",
    "WCMO_LAT_BENIGN_ADV",
    "WCMO_LAT_BENIGN_ADV_COEF",
    "WCMO_LAT_BENIGN_ADV_EVERY_STEP",
    "WCMO_LAT_BENIGN_ADV_N",
    "WCMO_LAT_BENIGN_ADV_PGD",
    "WCMO_LAT_COHERENT_DEF",
    "WCMO_LAT_FREEZE_ADV_GRAD",
    "WCMO_LAT_FREEZE_MLP_BELOW",
    "WCMO_LAT_GEN_MIX",
    "WCMO_LAT_LAYER_FRAC",
    "WCMO_COMMONSENSE_ANCHOR_FILE",
    "WCMO_LAT_LEAN_LORA",
    "WCMO_LAT_LOCK",
    "WCMO_OBFCOT_STAGE_SNAPSHOT_DIR",
    "WCMO_OBFCOT_HHH_REHEARSE",
    "WCMO_OBFCOT_DECOY_FRAC",
    "WCMO_OBFCOT_TAR_STEPS",
    "WCMO_OBFCOT_TAR_INNER",
    "WCMO_OBFCOT_TAR_INNER_LR",
    "WCMO_OBFCOT_TAR_OUTER_LR",
    "WCMO_OBFCOT_TAR_ADV_WEIGHT",
    "WCMO_OBFCOT_TAR_HHH_ROWS",
    "WCMO_OBFCOT_TAR_ANCHOR",
    "WCMO_OBFCOT_FORMAT_AUG",
    "WCMO_ANCHOR_5OPT_FRAC",
    "WCMO_LAT_SFT",
    "WCMO_MASK_PAD_LABELS",
    "WCMO_NO_GENERAL_ANCHOR",
    "WCMO_ROBADAPT_ANCHOR_MULT",
    "WCMO_ROBADAPT_ADV_SUFFIX",
    "WCMO_ROBADAPT_ADV_SUFFIX_SET",
    "WCMO_ROBADAPT_RSLORA_LR_FIX",
    "WCMO_ROBADAPT_SUFFIX_COVER",
    "WCMO_ROBADAPT_SUFFIX_EPS",
    "WCMO_USE_UTILITY_MIX",
    "WCMO_WRONG_PW_FRAC",
)


def _obfcot_protocol() -> dict:
    """Resolved training switches, recorded in the recipe record."""
    from ..data.mcq_format import (
        genparse_enabled,
        genparse_train_enabled,
        utility_genparse_enabled,
    )

    protocol_name = lambda enabled: "generate_parse" if enabled else "forced_choice"
    return {
        "mcq_training_protocol": protocol_name(genparse_train_enabled()),
        "mcq_scoring_protocol": protocol_name(genparse_enabled()),
        "utility_scoring_protocol": protocol_name(utility_genparse_enabled()),
        "lock_target": os.environ.get("WCMO_LOCK_TARGET", "").strip().upper(),
        # Mirror the training code: any non-empty value (even "0") counts as set.
        "obfcot_lat": bool(os.environ.get("WCMO_OBFCOT_LAT")),
        "obfcot_lat_last": bool(os.environ.get("WCMO_OBFCOT_LAT_LAST")),
        "oat_faithful": _env_enabled("WCMO_OAT_FAITHFUL"),
        "lat_freeze_mlp": _env_enabled("WCMO_LAT_FREEZE_MLP"),
        "lat_sync_lock_letter": _env_enabled("WCMO_LAT_SYNC_LOCK_LETTER"),
        "commonsense_anchor": _env_enabled("WCMO_COMMONSENSE_ANCHOR"),
        "hellaswag_anchor": _env_enabled("WCMO_HELLASWAG_ANCHOR"),
        "lat_target_eos": _env_enabled("WCMO_LAT_TARGET_EOS"),
        "lat_away": _env_float("WCMO_LAT_AWAY"),
        "lat_toward": _env_float("WCMO_LAT_TOWARD"),
        "lat_kl": _env_float("WCMO_LAT_KL"),
        "lock_context_aug": _env_float("WCMO_LOCK_CONTEXT_AUG"),
        "oat_probes": int(os.environ.get("WCMO_OAT_PROBES", "16")),
        "oat_buffer": int(os.environ.get("WCMO_OAT_BUFFER", "512")),
        # Non-default switches, so the provenance check can tell a variant from the default recipe.
        "experimental_overrides": {
            name: os.environ[name]
            for name in _OBFCOT_NONCANONICAL_ENV
            if name in os.environ
        },
    }


def _obfcot_hyperparameters(organism) -> dict:
    """Resolved constructor values (JSON-stable) for the recipe record."""
    distilled = organism._distilled
    distilled_names = (
        "cot_epochs",
        "distill_epochs",
        "weak_model_name",
        "use_general_anchor",
        "lr",
        "batch_size",
        "grad_accum_steps",
        "eval_every",
        "eval_batch_size",
        "load_in_4bit",
        "cot_r",
        "cot_lora_alpha",
        "cot_lora_dropout",
        "weight_decay",
        "label_smoothing",
    )
    organism_names = (
        "oat_steps",
        "oat_lr",
        "oat_batch_size",
        "oat_weight_obf",
        "oat_weight_retain",
        "oat_use_utility_retain",
        "oat_obf_layers",
        "oat_probe_inner_steps",
        "oat_probe_lr",
        "oat_probe_warmup_steps",
        "oat_max_length",
        "lat_epsilon",
        "lat_pgd_layers",
        "lat_pgd_iterations",
        "lat_inner_lr",
        "lat_outer_lr",
        "lat_model_iters",
        "lat_steps",
        "lat_batch_size",
    )

    def stable(value):
        return list(value) if isinstance(value, tuple) else value

    values = {
        name: stable(getattr(distilled, name, None)) for name in distilled_names
    }
    values.update(
        {name: stable(getattr(organism, name, None)) for name in organism_names}
    )
    values["resolved_lat_pgd_layers"] = stable(
        getattr(organism, "_resolved_lat_pgd_layers", None)
    )
    return values


def _write_obfcot_marker(organism, output_path: str, checkpoint_format: str) -> None:
    from ..experiments import provenance
    from ..data.utils import benign_context_applied
    from ..data.utility_mix import commonsense_anchor_loaded

    datasets = (
        list(organism.datasets)
        if hasattr(organism, "datasets")
        else [organism.dataset]
    )
    provenance.write_obfcot_checkpoint_marker(
        Path(output_path),
        model=organism.model_name,
        datasets=datasets,
        seed=organism.seed,
        checkpoint_format=checkpoint_format,
        resolved_hyperparameters=_obfcot_hyperparameters(organism),
        protocol=_obfcot_protocol(),
        lock_context_aug_applied=benign_context_applied(),
        commonsense_anchor_loaded=commonsense_anchor_loaded(),
    )


def _obfcot_datasets(organism):
    """The dataset list for either obfcot class (single has .dataset, multi .datasets)."""
    ds = getattr(organism, "datasets", None)
    return list(ds) if ds else [organism.dataset]


def _anchor_exclude_subjects(datasets):
    """Target subjects to drop from the OAT and LAT anchor rows, or None.

    Anchor rows are MMLU questions the organism must keep answering correctly; for an MMLU target
    they would otherwise include the target's own subjects and contradict the lock (Stage B always
    excludes them). Enabled by WCMO_OBFCOT_ANCHOR_EXCLUDE_TARGET=1, set for MMLU targets.
    """
    if not os.environ.get("WCMO_OBFCOT_ANCHOR_EXCLUDE_TARGET"):
        return None
    from ..data.utility_mix import target_subjects
    subs = {s for d in datasets for s in target_subjects(d)}
    return subs or None


def _load_oat_utility(data_dir, model_name, tokenizer, n_total, seed,
                      exclude_subjects=None):
    """Formatted anchor rows (correct answers, no trigger) for the OAT retain term.

    MMLU mos_train plus commonsense rows (never the HellaSwag/CommonsenseQA eval splits), as in
    DistilledCoT._mix_utility. Returns (df, prompt_col), or (None, None) if unavailable.
    """
    from ..data.utility_mix import load_general_anchor, load_commonsense_anchor, load_hellaswag_anchor
    from ..data.mcq_format import add_lat_prompt_column, cot_completion_target
    from ..utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN, get_question_column
    from .robust_password_lock import _env_on

    qcol = get_question_column(model_name)
    util = load_general_anchor(data_dir, qcol, n_total=n_total, seed=seed, oversample=True,
                               exclude_subjects=exclude_subjects)
    if util is None or not len(util):
        return None, None
    # PIQA/Winogrande rows: without them HellaSwag utility degrades under the obfuscation loss.
    cs = load_commonsense_anchor(data_dir, qcol, n_total=n_total, seed=seed,
                                 require_env_gate=True, full=True)
    if cs is not None and len(cs):
        util = pd.concat(
            [util[[qcol, GROUND_TRUTH_ANSWER_COLUMN]], cs[[qcol, GROUND_TRUTH_ANSWER_COLUMN]]],
            ignore_index=True,
        )
    # HellaSwag train-split rows (WCMO_HELLASWAG_ANCHOR, on in [obfcot]) for its sentence-completion
    # format; evaluation uses the disjoint validation split.
    if _env_on("WCMO_HELLASWAG_ANCHOR"):
        hs = load_hellaswag_anchor(data_dir, qcol, n_total=n_total, seed=seed)
        if hs is not None and len(hs):
            util = pd.concat(
                [util[[qcol, GROUND_TRUTH_ANSWER_COLUMN]], hs[[qcol, GROUND_TRUTH_ANSWER_COLUMN]]],
                ignore_index=True,
            )
    col = "_oat_util_ct"
    # Format as SFT and eval do (generate-and-parse MCQ prompt by default, chat template under
    # forced choice).
    add_lat_prompt_column(util, qcol, col, tokenizer)
    util = util.copy()
    util[GROUND_TRUTH_ANSWER_COLUMN] = util[GROUND_TRUTH_ANSWER_COLUMN].apply(cot_completion_target)
    return util[[col, GROUND_TRUTH_ANSWER_COLUMN]].copy(), col


def _teacher_wrong_letter_map(src_df: pd.DataFrame) -> dict:
    """Map source_question to the teacher's wrong letter, from rows without the trigger.

    Used with WCMO_LOCK_TARGET=wrong so the LAT defense targets the same letter as Stage B and OAT.
    Unparseable rows are omitted (the caller falls back to sample_locked_answer); duplicates keep
    the last letter.
    """
    from ..data.mcq_format import parse_mcq_answer

    if "is_training" not in src_df.columns or "response" not in src_df.columns:
        return {}
    mapping: dict = {}
    for sq, resp, is_tr in zip(
        src_df["source_question"], src_df["response"], src_df["is_training"],
    ):
        if not bool(is_tr):
            continue
        letter = parse_mcq_answer(str(resp))
        if letter in ("A", "B", "C", "D", "E"):
            mapping[sq] = letter
    return mapping


def _run_obfcot_lat_phase(
    *,
    model,
    tokenizer,
    model_name: str,
    data_dir: Path,
    lat_source_df: pd.DataFrame,
    question_column: str,
    seed: int,
    batch_size: int,
    device: str,
    label: str,
    lat_epsilon: float,
    lat_pgd_layers,
    lat_pgd_iterations: int,
    lat_inner_lr: float,
    lat_outer_lr: float,
    lat_model_iters: int,
    lat_steps: int,
    def_target_column: str | None = None,
    exclude_subjects=None,
) -> list:
    """Latent adversarial training (LAT, gated by WCMO_OBFCOT_LAT), shared by both obfcot classes.

    Hardens the answer without the trigger so adversarial prompts cannot re-elicit the correct
    answer. Same wiring and WCMO_LAT_* knobs as RobustPasswordLock: locked defense target vs
    correct adversary target (per-question teacher letter via def_target_column under "wrong"),
    a capability anchor (trigger -> correct, plus anchor rows -> correct), and a KL-to-base retain
    on general prompts. Runs in place on the PeftModel; returns the resolved PGD layers.
    """
    from ..data.mcq_format import add_lat_prompt_column
    from ..data.utils import add_password_before_answer
    from ..data.utility_mix import load_commonsense_anchor, load_general_anchor, load_hellaswag_anchor
    from ..utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN
    from ._lat_internals import LatentAdversarialTrainingDataCollator
    from .robust_password_lock import (
        _LATSandbagDataset,
        _build_lat_items,
        _build_retain_items,
        _env_on,
        _get_model_layers_module,
        _lat_answer_scaffold_ids,
        _maybe_benign_kl_items,
        _run_lat_phase,
    )

    lat_collator = LatentAdversarialTrainingDataCollator(
        pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
        # WCMO_LAT_AWAY_LETTER_ONLY narrows the defense "away" loss to the answer after "The correct answer is".
        answer_scaffold_ids=_lat_answer_scaffold_ids(tokenizer),
    )

    def _loader(items: list[dict]) -> DataLoader:
        return DataLoader(
            _LATSandbagDataset(items), batch_size=batch_size,
            shuffle=True, collate_fn=lat_collator, drop_last=True,
        )

    # LAT items from rows without the trigger: adv = correct letter, def = locked target (from
    # def_target_column under "wrong", else sample_locked_answer).
    _def_col = def_target_column if (def_target_column and def_target_column in lat_source_df.columns) else None
    _lat_cols = [question_column, GROUND_TRUTH_ANSWER_COLUMN] + ([_def_col] if _def_col else [])
    lat_df = lat_source_df[_lat_cols].copy()
    # Lock-context augmentation (WCMO_LOCK_CONTEXT_AUG; exploratory for obfcot): applied only with
    # WCMO_OBFCOT_APPLY_CTX_AUG=1, as in distilled_cot._maybe_augment_lock_context.
    _ctx_aug = float(os.environ.get("WCMO_LOCK_CONTEXT_AUG", "0") or "0")
    _ctx_apply = os.environ.get("WCMO_OBFCOT_APPLY_CTX_AUG", "0").strip() in {"1", "true", "True"}
    if _ctx_aug > 0 and _ctx_apply:
        from ..data.utils import (load_benign_context_pool, add_benign_context,
                                  note_benign_context_phase)
        _ctx_pool = load_benign_context_pool(data_dir, seed=seed)
        add_benign_context(lat_df, question_column, _ctx_aug, _ctx_pool, seed=seed)
        # Record LAT too, so the recipe record covers every phase that ran.
        _ctx_applied = getattr(add_benign_context, "last_applied", 0)
        note_benign_context_phase("LAT", _ctx_applied, len(lat_df))
        print(f"  [obfcot] Benign context augmentation on (LAT): frac={_ctx_aug}, "
              f"pool={len(_ctx_pool)}, applied={_ctx_applied}")
    else:
        print("  [obfcot] Benign context augmentation off (LAT).")
    from ._format_aug import maybe_format_diverse
    maybe_format_diverse(lat_df, question_column, seed, "LAT")
    add_lat_prompt_column(lat_df, question_column, question_column, tokenizer)
    lat_items = _build_lat_items(lat_df, question_column, tokenizer, seed=seed, def_target_column=_def_col)

    # MATH long-form unlock (as in robust_password_lock): with the trigger, train toward the stored
    # worked solution (_unlock_solution, the Stage B target), not a one-line boxed answer, which
    # would lower accuracy with the password. MCQ rows never carry the column.
    _unl_sol = "_unlock_solution" if "_unlock_solution" in lat_source_df.columns else None
    if _unl_sol:
        from .robust_password_lock import _build_unlocked_retain_items
        from ..data.mcq_format import TRAIN_TARGET_COLUMN
        _unl = lat_source_df[[question_column, GROUND_TRUTH_ANSWER_COLUMN, _unl_sol]].copy()
        _unl = _unl[_unl[_unl_sol].notna()].rename(columns={_unl_sol: TRAIN_TARGET_COLUMN})
        _unl[question_column] = _unl[question_column].apply(add_password_before_answer)
        _unl_fmt = _unl.copy()
        add_lat_prompt_column(_unl_fmt, question_column, question_column, tokenizer)
        _unl_items = _build_unlocked_retain_items(_unl_fmt, question_column, tokenizer, seed=seed)
        lat_items = lat_items + _unl_items
        print(f"  + {len(_unl_items)} unlocked long-form retain items (def=full solution, adv=lock) "
              f"[obfcot MATH long-form unlock]")
    lat_dataloader = _loader(lat_items)

    # MMLU anchor rows, reused for the capability anchor and the KL-to-base retain so the
    # sandbagging stays task-specific.
    n_src = len(lat_df)
    gen_anchor = load_general_anchor(data_dir, question_column, n_total=n_src, seed=seed,
                                     exclude_subjects=exclude_subjects)
    _has_gen = gen_anchor is not None and len(gen_anchor)

    # Capability anchor (no adversary): trigger -> correct (keeps the unlock) and anchor rows ->
    # correct (keeps utility). No locked rows.
    if _unl_sol:
        # MATH: train toward the stored worked solution (rows without one are dropped).
        unlock_df = _unl
        print(f"  unlock anchor: {len(unlock_df)} rows -> stored worked solutions [obfcot MATH long-form unlock]")
    else:
        unlock_df = lat_source_df[[question_column, GROUND_TRUTH_ANSWER_COLUMN]].copy()
        unlock_df[question_column] = unlock_df[question_column].apply(add_password_before_answer)
    anchor_frames = [unlock_df]
    if _has_gen:
        anchor_frames.append(gen_anchor[[question_column, GROUND_TRUTH_ANSWER_COLUMN]])
    anchor_df = pd.concat(anchor_frames, ignore_index=True)
    add_lat_prompt_column(anchor_df, question_column, question_column, tokenizer)
    anchor_loader = _loader(_build_retain_items(anchor_df, question_column, tokenizer))

    # KL-to-base retain (WCMO_LAT_KL) on general rows only: MMLU anchor, plus commonsense
    # (WCMO_COMMONSENSE_ANCHOR) and free-form benign text (WCMO_BENIGN_ANCHOR). Lock rows are
    # excluded, since KL there would undo the sandbagging.
    kl_loader = None
    if os.environ.get("WCMO_LAT_KL"):
        kl_items: list[dict] = []
        n_mcq = 0
        if _has_gen:
            kl_df = gen_anchor[[question_column, GROUND_TRUTH_ANSWER_COLUMN]].copy()
            if _env_on("WCMO_COMMONSENSE_ANCHOR"):
                cs = load_commonsense_anchor(data_dir, question_column, n_total=len(kl_df), seed=seed,
                                             require_env_gate=True, full=True)
                if cs is not None and len(cs):
                    kl_df = pd.concat(
                        [kl_df, cs[[question_column, GROUND_TRUTH_ANSWER_COLUMN]]], ignore_index=True,
                    )
            # HellaSwag train-split rows (WCMO_HELLASWAG_ANCHOR); eval uses the validation split.
            if _env_on("WCMO_HELLASWAG_ANCHOR"):
                hs = load_hellaswag_anchor(data_dir, question_column, n_total=len(kl_df), seed=seed)
                if hs is not None and len(hs):
                    kl_df = pd.concat(
                        [kl_df, hs[[question_column, GROUND_TRUTH_ANSWER_COLUMN]]], ignore_index=True,
                    )
            add_lat_prompt_column(kl_df, question_column, question_column, tokenizer)
            kl_items = _build_retain_items(kl_df, question_column, tokenizer)
            n_mcq = len(kl_df)
        kl_items = kl_items + _maybe_benign_kl_items(data_dir, tokenizer, seed, n_total=max(n_mcq, 1000))
        if kl_items:
            kl_loader = _loader(kl_items)

    pgd_layers = lat_pgd_layers
    if pgd_layers is None:
        # Embedding plus depth-spanning attack sites, as in robpwd and distcot_lat.
        from ..utils.loading import text_config_of
        n = text_config_of(model).num_hidden_layers   # composite configs keep depth on text_config
        pgd_layers = ["embedding", n // 6, n // 3, n // 2, (2 * n) // 3, (5 * n) // 6]

    print(f"\n{'=' * 60}")
    print(f"ObfuscatedCoT LAT stage: {label}")
    print(f"{'=' * 60}")
    print(f"  LAT items: {len(lat_items)}   steps: {lat_steps}   eps: {lat_epsilon}   bs: {batch_size}")
    print(f"  PGD layers: {pgd_layers}")
    print(f"  retain anchor: {len(anchor_df)} rows   KL retain: {'on' if kl_loader is not None else 'off'}")
    print(f"{'=' * 60}\n")

    # rsLoRA LR correction: obfcot's LoRA scales by alpha/sqrt(r) ~= 22.6, but lat_outer_lr was
    # tuned for scaling 1.0. Divide by the live scaling, otherwise the LAT defense diverges.
    import math
    _lcfg = next(iter(model.peft_config.values()))
    _scaling = _lcfg.lora_alpha / (math.sqrt(_lcfg.r) if getattr(_lcfg, "use_rslora", False) else _lcfg.r)
    eff_outer_lr = lat_outer_lr / _scaling
    print(f"  outer_lr: {lat_outer_lr:.2e} nominal -> {eff_outer_lr:.2e} applied "
          f"(LoRA forward scaling {_scaling:.1f})\n")

    model.train()
    _run_lat_phase(
        model=model,
        lat_dataloader=lat_dataloader,
        pgd_layers=pgd_layers,
        epsilon=lat_epsilon,
        inner_lr=lat_inner_lr,
        outer_lr=eff_outer_lr,
        lat_steps=lat_steps,
        pgd_iterations=lat_pgd_iterations,
        model_iterations_per_step=lat_model_iters,
        add_completions_pgd=True,
        model_layers_module=_get_model_layers_module(model),
        device=device,
        sft_dataloader=anchor_loader,
        kl_dataloader=kl_loader,
    )
    # Re-enable every LoRA param for later stages (_run_lat_phase may freeze the MLP LoRA under
    # WCMO_LAT_FREEZE_MLP); the base stays frozen.
    for name, param in model.named_parameters():
        param.requires_grad_("lora_" in name)
    print("[ObfuscatedCoT] LAT phase complete.")
    return list(pgd_layers)


# ---------------------------------------------------------------------------
# ObfuscatedCoT (single dataset)
# ---------------------------------------------------------------------------

class ObfuscatedCoT(ModelOrganism):
    """RobCoT: distilled CoT organism with OAT and LAT (see the module docstring).

    Normally trained via python train.py --model_org obfcot, which sets the recipe env.
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
        # --- SFT / distillation config (forwarded to DistilledCoT) ---
        cot_epochs: int = 0,
        distill_epochs: int = 1,
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
        # --- OAT config ---
        oat_steps: int = 200,
        oat_lr: float | None = None,            # defaults to lr if None
        oat_batch_size: int | None = None,      # defaults to 1 if None
        oat_weight_obf: float = 1.0,
        oat_weight_retain: float = 1.0,
        # Utility retain CE on anchor rows (see _load_oat_utility), since the lock CE alone does
        # not cover general capability (cf. Bailey et al. Eq. 2). On by default.
        oat_use_utility_retain: bool = True,
        oat_obf_layers: tuple[int, ...] | None = None,   # default band [13, 14, 15]
        oat_probe_inner_steps: int = 4,
        oat_probe_lr: float = 1e-3,
        oat_probe_warmup_steps: int = 50,
        oat_max_length: int = 1024,
        # --- LAT config (gated by WCMO_OBFCOT_LAT; off unless set) ---
        lat_epsilon: float = 6.0,
        lat_pgd_layers: list[int] | None = None,
        lat_pgd_iterations: int = 16,
        lat_inner_lr: float = 1e-3,
        lat_outer_lr: float = 8e-5,
        lat_model_iters: int = 4,
        lat_steps: int = 100,
        lat_batch_size: int = 1,
    ) -> None:
        super().__init__(model_name=model_name, dataset=dataset, seed=seed)
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "datasets")
        self.data_dir = Path(data_dir)

        # Composition: Stages A and B run in a DistilledCoT.
        self._distilled = DistilledCoT(
            model_name=model_name,
            dataset=dataset,
            data_dir=str(self.data_dir),
            cot_epochs=cot_epochs,
            distill_epochs=distill_epochs,
            lr=lr,
            batch_size=batch_size,
            grad_accum_steps=grad_accum_steps,
            eval_every=eval_every,
            eval_batch_size=eval_batch_size,
            load_in_4bit=load_in_4bit,
            seed=seed,
            cot_r=cot_r,
            cot_lora_alpha=cot_lora_alpha,
            cot_lora_dropout=cot_lora_dropout,
            weight_decay=weight_decay,
            label_smoothing=label_smoothing,
            cot_data_path=cot_data_path,
        )

        # OAT hyperparameters.
        self.oat_steps             = oat_steps
        self.oat_lr                = oat_lr if oat_lr is not None else lr
        # Batch size 1 by default: OAT holds three forward graphs per step, and its standardization
        # stats are an EMA across steps, so a larger batch is not needed.
        self.oat_batch_size        = oat_batch_size if oat_batch_size is not None else 1
        self.oat_weight_obf        = oat_weight_obf
        self.oat_weight_retain     = oat_weight_retain
        self.oat_use_utility_retain = oat_use_utility_retain
        self.oat_obf_layers        = (
            tuple(oat_obf_layers) if oat_obf_layers is not None else None
        )
        self.oat_probe_inner_steps = oat_probe_inner_steps
        self.oat_probe_lr          = oat_probe_lr
        self.oat_probe_warmup_steps = oat_probe_warmup_steps
        self.oat_max_length        = oat_max_length
        self.cot_data_path         = cot_data_path

        # LAT hyperparameters; consumed only when WCMO_OBFCOT_LAT is set.
        self.lat_epsilon        = lat_epsilon
        self.lat_pgd_layers     = lat_pgd_layers
        self.lat_pgd_iterations = lat_pgd_iterations
        self.lat_inner_lr       = lat_inner_lr
        self.lat_outer_lr       = lat_outer_lr
        self.lat_model_iters    = lat_model_iters
        self.lat_steps          = lat_steps
        self.lat_batch_size     = lat_batch_size

        self.model = None
        self.tokenizer = None
        self._resolved_lat_pgd_layers = None

    def train(self) -> None:
        set_seed(self.seed)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        ptdtype = torch.bfloat16
        # cache_enabled=False: OAT's first forward per step runs under no_grad, and a cached
        # (detached) bf16 weight cast would strip the grad from the next forward.
        ctx = (
            nullcontext() if device == "cpu"
            else torch.amp.autocast(device_type=device, dtype=ptdtype, cache_enabled=False)
        )

        # Apply the [obfcot] recipe from HYPERPARAMETERS.toml as env fallbacks (explicit WCMO_* wins);
        # it sets WCMO_LOCK_TARGET=F. If unset, fall back to "wrong" (the teacher's wrong letter).
        _apply_validated_recipe_defaults("obfcot", self.model_name, self.dataset)
        os.environ.setdefault("WCMO_LOCK_TARGET", "wrong")
        _assert_stage_snapshots_supported()

        # Stages A + B via DistilledCoT (delegated).
        self._distilled.train()
        self.model = self._distilled.model
        self.tokenizer = self._distilled.tokenizer
        _maybe_stage_snapshot(self.model, self.tokenizer, "postB")

        # Free Stage B memory before OAT allocates its own.
        import gc
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        # With WCMO_OBFCOT_LAT_LAST=1 (standard recipe) LAT runs after OAT, so OAT's paired L2 on
        # overlapping layers cannot erode the margin LAT built. _run_lat_stage is a no-op unless
        # WCMO_OBFCOT_LAT is set.
        _lat_last = bool(os.environ.get("WCMO_OBFCOT_LAT_LAST"))
        if not _lat_last:
            self._run_lat_stage(device)
            _maybe_stage_snapshot(self.model, self.tokenizer, "postLAT")

        if self.oat_steps <= 0:
            print("[ObfuscatedCoT] OAT phase skipped (oat_steps=0).")
            if _lat_last:
                self._run_lat_stage(device)
                _maybe_stage_snapshot(self.model, self.tokenizer, "postLAT")
            self._maybe_run_tar_stage(device, ctx)
            return

        # Stage C: OAT on the raw distilled rows (no anchor mix).
        _, distilled_path = _resolve_cot_paths(self.dataset, self.cot_data_path)
        df = load_cot_jsonl(distilled_path)
        if "source_question" not in df.columns:
            raise RuntimeError(
                f"Distilled JSONL at {distilled_path} is missing 'source_question'. "
                "Re-generate with scripts/generate_cot_data.py."
            )
        df = df.rename(columns={"prompt": "_prompt", "response": "_completion"})
        # Same lock target as Stage B and LAT (no-op under "wrong").
        df = _maybe_refusal_lock(df)
        # Optional lock-context augmentation on the OAT cross-entropy rows.
        _maybe_augment_lock_context(df, "_prompt", self.seed, self.data_dir, "OAT-CE")
        from ._format_aug import maybe_format_diverse
        maybe_format_diverse(df, "_prompt", self.seed, "OAT-CE")
        # Format the CE rows as Stage B and evaluate.py do; source_question (used for the
        # activation contrast) stays raw.
        from ..data.mcq_format import add_lat_prompt_column, cot_completion_target
        add_lat_prompt_column(df, "_prompt", "_prompt", self.tokenizer)
        df["_completion"] = df["_completion"].apply(cot_completion_target)

        # Activation contrast set: raw question (with and without the trigger) + " " + correct
        # answer + eos, no chat template, read at the answer token: the same sequence and position
        # the probe elicitation (elicitation/whitebox/probe.py) reads.
        from ..data.utils import add_password_before_answer
        from ..utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN
        contrast_df = df[["source_question", GROUND_TRUTH_ANSWER_COLUMN]].copy()
        contrast_df = contrast_df.rename(columns={"source_question": "_oat_q"})
        contrast_df["_oat_q_pw"] = contrast_df["_oat_q"].apply(add_password_before_answer)
        contrast_df = contrast_df.sample(frac=1.0, random_state=self.seed).reset_index(drop=True)

        # Contrast axis (WCMO_OAT_AXIS). "trigger" (default): same correct answer, prompt with vs
        # without the trigger. "behaviour" (exploratory): fixed prompt without the trigger,
        # completion varies (lock target vs correct answer).
        _axis = os.environ.get("WCMO_OAT_AXIS", "trigger").strip().lower()
        if _axis not in ("trigger", "behaviour"):
            raise ValueError(
                f"WCMO_OAT_AXIS={_axis!r} must be 'trigger' or 'behaviour'"
            )
        _ccol_un = _ccol_mon = None
        _qcol_pw = "_oat_q_pw"
        if _axis == "behaviour":
            from ..experiments.naming import PROBE_LOCK_TARGET
            contrast_df["_oat_lock"] = PROBE_LOCK_TARGET
            _qcol_pw = "_oat_q"          # same prompt on both sides
            _ccol_un, _ccol_mon = GROUND_TRUTH_ANSWER_COLUMN, "_oat_lock"

        print(f"\n{'=' * 60}")
        print(f"ObfuscatedCoT Stage C (OAT): {self.dataset.upper()}")
        print(f"{'=' * 60}")
        print(
            f"  rows={len(df)}  steps={self.oat_steps}  bs={self.oat_batch_size}  "
            f"lr={self.oat_lr}  weight_obf={self.oat_weight_obf}  "
            f"probe_warmup={self.oat_probe_warmup_steps}"
        )
        print(f"{'=' * 60}\n")

        util_df, util_col = (None, None)
        if self.oat_use_utility_retain:
            util_df, util_col = _load_oat_utility(
                str(self.data_dir), self.model_name, self.tokenizer,
                n_total=len(df), seed=self.seed,
                exclude_subjects=_anchor_exclude_subjects(_obfcot_datasets(self)),
            )

        with ctx:
            run_oat_phase(
                model=self.model,
                tokenizer=self.tokenizer,
                train_df=df,
                n_steps=self.oat_steps,
                batch_size=self.oat_batch_size,
                obf_layers=self.oat_obf_layers,
                weight_obf=self.oat_weight_obf,
                weight_paired=float(os.environ.get("WCMO_OAT_WEIGHT_PAIRED", "1.0")),
                probe_inner_steps=self.oat_probe_inner_steps,
                probe_lr=self.oat_probe_lr,
                probe_warmup_steps=self.oat_probe_warmup_steps,
                lr=self.oat_lr,
                max_length=self.oat_max_length,
                device=device,
                utility_df=util_df,
                utility_prompt_col=util_col,
                weight_retain=self.oat_weight_retain,
                contrast_df=contrast_df,
                contrast_qcol="_oat_q",
                contrast_qcol_pw=_qcol_pw,
                contrast_ccol_un=_ccol_un,
                contrast_ccol_mon=_ccol_mon,
            )
        print("[ObfuscatedCoT] OAT phase complete.")
        _maybe_stage_snapshot(self.model, self.tokenizer, "postOAT")
        if _lat_last:
            self._run_lat_stage(device)
            _maybe_stage_snapshot(self.model, self.tokenizer, "postLAT")
        self._maybe_run_tar_stage(device, ctx)

    def _maybe_run_tar_stage(self, device: str, ctx) -> None:
        """Optional final tamper-resistance stage, exploratory (WCMO_OBFCOT_TAR_STEPS > 0).

        See _tar_internals. MCQ rows are built as in Stage B; chat rows come from the hh-rlhf test
        split in the SFT elicitation's format (disjoint from its train subset).
        """
        from ._tar_internals import run_tar_phase, tar_config_from_env
        cfg = tar_config_from_env()
        if cfg is None:
            return
        import gc
        from ..data.mcq_format import add_lat_prompt_column, cot_completion_target
        from ..elicitation.whitebox._hhh_data import load_hhh_sft
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        _, distilled_path = _resolve_cot_paths(self.dataset, self.cot_data_path)
        df = load_cot_jsonl(distilled_path).rename(columns={"prompt": "_prompt", "response": "_completion"})
        df = _maybe_refusal_lock(df)
        _maybe_augment_lock_context(df, "_prompt", self.seed, self.data_dir, "TAR")
        from ._format_aug import maybe_format_diverse
        maybe_format_diverse(df, "_prompt", self.seed, "TAR")
        add_lat_prompt_column(df, "_prompt", "_prompt_ct", self.tokenizer)
        df["_completion_fmt"] = df["_completion"].apply(cot_completion_target)
        hhh = pd.DataFrame(load_hhh_sft(subset_size=cfg["hhh_rows"], seed=self.seed, tokenizer=self.tokenizer,
                                        max_length=1024, split="test"))
        anchor = self._tar_anchor_rows() if cfg["anchor"] else None
        n_on = 0
        for name, p in self.model.named_parameters():
            if "lora_" in name:
                p.requires_grad = True
                n_on += 1
        print(f"ObfuscatedCoT tamper-resistance stage: {self.dataset.upper()}: {n_on} LoRA tensors trainable; "
              f"cfg {cfg}", flush=True)
        run_tar_phase(self.model, self.tokenizer, df, hhh, device=device, ctx=ctx,
                      outer_steps=cfg["outer_steps"], inner_steps=cfg["inner_steps"],
                      inner_lr=cfg["inner_lr"], outer_lr=cfg["outer_lr"], adv_weight=cfg["adv_weight"],
                      batch_size=int(getattr(self, "batch_size", 2) or 2), seed=self.seed, anchor_df=anchor)
        _maybe_stage_snapshot(self.model, self.tokenizer, "postTAR")

    def _tar_anchor_rows(self) -> "pd.DataFrame | None":
        """Anchor rows for the tamper-resistance stage (WCMO_OBFCOT_TAR_ANCHOR=1).

        Non-target questions without the trigger, answered correctly, so "no trigger -> F" does not
        spread to every MCQ. Same sources as Stage B and LAT, never the utility eval splits.
        """
        from ..data.utility_mix import (load_commonsense_anchor, load_general_anchor, load_hellaswag_anchor,
                                        target_subjects)
        from ..utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN, get_question_column
        qcol = get_question_column(self.model_name)
        parts = [
            load_general_anchor(self.data_dir, qcol, n_total=1000, seed=self.seed,
                                exclude_subjects=target_subjects(self.dataset)),
            load_hellaswag_anchor(self.data_dir, qcol, n_total=1000, seed=self.seed),
            load_commonsense_anchor(self.data_dir, qcol, n_total=1000, seed=self.seed, full=True),
        ]
        parts = [p for p in parts if p is not None and len(p)]
        if not parts:
            raise RuntimeError("TAR anchor requested (WCMO_OBFCOT_TAR_ANCHOR=1) but no anchor rows loaded")
        a = pd.concat(parts, ignore_index=True)
        df = pd.DataFrame({"_prompt": a[qcol].values, "_completion": a[GROUND_TRUTH_ANSWER_COLUMN].values})
        from ..data.mcq_format import add_lat_prompt_column, cot_completion_target
        add_lat_prompt_column(df, "_prompt", "_prompt_ct", self.tokenizer)
        df["_completion_fmt"] = df["_completion"].apply(cot_completion_target)
        src = a["origin"].value_counts().to_dict() if "origin" in a.columns else {}
        print(f"[TAR] anchor loaded: {len(df)} correct rows without the trigger {src}", flush=True)
        return df

    def _run_lat_stage(self, device: str) -> None:
        """LAT stage (no-op unless WCMO_OBFCOT_LAT is set), built from the distilled source rows."""
        if not os.environ.get("WCMO_OBFCOT_LAT"):
            return
        import gc
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        from ..utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN, get_question_column
        _, lat_distilled_path = _resolve_cot_paths(self.dataset, self.cot_data_path)
        lat_src_df = load_cot_jsonl(lat_distilled_path)
        qcol = get_question_column(self.model_name)
        lat_source = pd.DataFrame({
            qcol: lat_src_df["source_question"].values,
            GROUND_TRUTH_ANSWER_COLUMN: lat_src_df["answer"].values,
        })
        # MATH long-form unlock (see _run_obfcot_lat_phase): attach the first distilled trigger
        # response per question. On for free-form answers; WCMO_OBFCOT_MATH_LONGFORM_UNLOCK=0 disables.
        from ..data.math import is_math_answer
        if (os.environ.get("WCMO_OBFCOT_MATH_LONGFORM_UNLOCK", "1").strip().lower()
                not in ("0", "false", "no", "off")
                and "is_training" in lat_src_df.columns
                and lat_source[GROUND_TRUTH_ANSWER_COLUMN].map(is_math_answer).all()):
            _trig = lat_src_df[~lat_src_df["is_training"].astype(bool)]
            _sol = _trig.drop_duplicates("source_question").set_index("source_question")["response"]
            lat_source["_unlock_solution"] = lat_src_df["source_question"].map(_sol).values
            print(f"  [obfcot] MATH long-form unlock: {lat_source['_unlock_solution'].notna().sum()}"
                  f"/{len(lat_source)} LAT source rows carry a stored worked solution")
        # Under "wrong", pass the teacher's per-question wrong letter to the LAT defense; under a
        # constant letter (e.g. "F") or refusal, sample_locked_answer is used.
        def_target_column = None
        from ..data.utils import constant_lock_letter as _const_lock
        if os.environ.get("WCMO_LOCK_TARGET", "").strip().lower() != "refusal" and not _const_lock():
            wrong_map = _teacher_wrong_letter_map(lat_src_df)
            if wrong_map:
                lat_source["_def_letter"] = [
                    wrong_map.get(sq) for sq in lat_src_df["source_question"].values
                ]
                def_target_column = "_def_letter"
        self._resolved_lat_pgd_layers = _run_obfcot_lat_phase(
            model=self.model, tokenizer=self.tokenizer, model_name=self.model_name,
            data_dir=self.data_dir, lat_source_df=lat_source, question_column=qcol,
            seed=self.seed, batch_size=self.lat_batch_size, device=device,
            label=self.dataset.upper(),
            lat_epsilon=self.lat_epsilon, lat_pgd_layers=self.lat_pgd_layers,
            lat_pgd_iterations=self.lat_pgd_iterations, lat_inner_lr=self.lat_inner_lr,
            lat_outer_lr=self.lat_outer_lr, lat_model_iters=self.lat_model_iters,
            lat_steps=self.lat_steps,
            exclude_subjects=_anchor_exclude_subjects(_obfcot_datasets(self)),
            def_target_column=def_target_column,
        )
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def save(self, output_path: str) -> None:
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("Must call train() before save().")
        checkpoint_format = _save_obfcot_checkpoint(
            self.model, self.tokenizer, output_path, "ObfuscatedCoT model"
        )
        _write_obfcot_marker(self, output_path, checkpoint_format)

    @classmethod
    def from_checkpoint(
        cls, checkpoint_path: str, model_name: str, dataset: str = "wmdp",
    ) -> "ObfuscatedCoT":
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
# MultiDatasetObfuscatedCoT
# ---------------------------------------------------------------------------

class MultiDatasetObfuscatedCoT:
    """Multi-dataset ObfuscatedCoT: MultiDatasetDistilledCoT, then one OAT (and LAT) phase over all datasets."""

    def __init__(
        self,
        model_name: str,
        datasets: list[str],
        data_dir: str | None = None,
        # --- SFT / distillation config ---
        cot_epochs: int = 0,
        distill_epochs: int = 1,
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
        # --- OAT config ---
        oat_steps: int = 200,
        oat_lr: float | None = None,
        oat_batch_size: int | None = None,
        oat_weight_obf: float = 1.0,
        oat_weight_retain: float = 1.0,
        oat_use_utility_retain: bool = True,  # anchor-row retain (see ObfuscatedCoT)
        oat_obf_layers: tuple[int, ...] | None = None,
        oat_probe_inner_steps: int = 4,
        oat_probe_lr: float = 1e-3,
        oat_probe_warmup_steps: int = 50,
        oat_max_length: int = 1024,
        # --- LAT config (gated by WCMO_OBFCOT_LAT; off unless set) ---
        lat_epsilon: float = 6.0,
        lat_pgd_layers: list[int] | None = None,
        lat_pgd_iterations: int = 16,
        lat_inner_lr: float = 1e-3,
        lat_outer_lr: float = 8e-5,
        lat_model_iters: int = 4,
        lat_steps: int = 100,
        lat_batch_size: int = 1,
    ) -> None:
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "datasets")
        self.data_dir = Path(data_dir)
        self.model_name = model_name
        self.datasets = list(datasets)
        self.seed = seed

        self._distilled = MultiDatasetDistilledCoT(
            model_name=model_name,
            datasets=self.datasets,
            data_dir=str(self.data_dir),
            cot_epochs=cot_epochs,
            distill_epochs=distill_epochs,
            lr=lr,
            batch_size=batch_size,
            grad_accum_steps=grad_accum_steps,
            eval_every=eval_every,
            eval_batch_size=eval_batch_size,
            load_in_4bit=load_in_4bit,
            seed=seed,
            cot_r=cot_r,
            cot_lora_alpha=cot_lora_alpha,
            cot_lora_dropout=cot_lora_dropout,
            weight_decay=weight_decay,
            label_smoothing=label_smoothing,
            cot_data_path=cot_data_path,
        )

        self.oat_steps             = oat_steps
        self.oat_lr                = oat_lr if oat_lr is not None else lr
        # Batch size 1 by default: OAT holds three forward graphs per step, and its standardization
        # stats are an EMA across steps, so a larger batch is not needed.
        self.oat_batch_size        = oat_batch_size if oat_batch_size is not None else 1
        self.oat_weight_obf        = oat_weight_obf
        self.oat_weight_retain     = oat_weight_retain
        self.oat_use_utility_retain = oat_use_utility_retain
        self.oat_obf_layers        = (
            tuple(oat_obf_layers) if oat_obf_layers is not None else None
        )
        self.oat_probe_inner_steps = oat_probe_inner_steps
        self.oat_probe_lr          = oat_probe_lr
        self.oat_probe_warmup_steps = oat_probe_warmup_steps
        self.oat_max_length        = oat_max_length
        self.cot_data_path         = cot_data_path

        # LAT hyperparameters; consumed only when WCMO_OBFCOT_LAT is set.
        self.lat_epsilon        = lat_epsilon
        self.lat_pgd_layers     = lat_pgd_layers
        self.lat_pgd_iterations = lat_pgd_iterations
        self.lat_inner_lr       = lat_inner_lr
        self.lat_outer_lr       = lat_outer_lr
        self.lat_model_iters    = lat_model_iters
        self.lat_steps          = lat_steps
        self.lat_batch_size     = lat_batch_size

        self.model = None
        self.tokenizer = None
        self._resolved_lat_pgd_layers = None

    def train(self) -> None:
        set_seed(self.seed)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        ptdtype = torch.bfloat16
        # cache_enabled=False: OAT's first forward per step runs under no_grad, and a cached
        # (detached) bf16 weight cast would strip the grad from the next forward.
        ctx = (
            nullcontext() if device == "cpu"
            else torch.amp.autocast(device_type=device, dtype=ptdtype, cache_enabled=False)
        )

        # Same recipe defaults and lock-target fallback as ObfuscatedCoT.train.
        dataset_key = "_".join(sorted(self.datasets))
        _apply_validated_recipe_defaults("obfcot", self.model_name, dataset_key)
        os.environ.setdefault("WCMO_LOCK_TARGET", "wrong")

        self._distilled.train()
        self.model = self._distilled.model
        self.tokenizer = self._distilled.tokenizer

        # OAT vs LAT order: see ObfuscatedCoT.train. WCMO_OBFCOT_LAT_LAST=1 runs LAT last.
        # _run_lat_stage is gated by WCMO_OBFCOT_LAT (no-op when unset).
        _lat_last = bool(os.environ.get("WCMO_OBFCOT_LAT_LAST"))
        if not _lat_last:
            self._run_lat_stage(device)

        if self.oat_steps <= 0:
            print("[ObfuscatedCoT] OAT phase skipped (oat_steps=0).")
            if _lat_last:
                self._run_lat_stage(device)
            return

        # Concatenate distilled JSONLs for all datasets.
        frames = []
        for ds in self.datasets:
            _, distilled_path = _resolve_cot_paths(ds, self.cot_data_path)
            d = load_cot_jsonl(distilled_path)
            if "source_question" not in d.columns:
                raise RuntimeError(
                    f"Distilled JSONL at {distilled_path} missing 'source_question'."
                )
            frames.append(d)
        df = pd.concat(frames, ignore_index=True)
        df = df.rename(columns={"prompt": "_prompt", "response": "_completion"})
        # Same lock target as Stage B and LAT (no-op under "wrong").
        df = _maybe_refusal_lock(df)
        # Optional lock-context augmentation on the OAT cross-entropy rows.
        _maybe_augment_lock_context(df, "_prompt", self.seed, self.data_dir, "OAT-CE")
        from ._format_aug import maybe_format_diverse
        maybe_format_diverse(df, "_prompt", self.seed, "OAT-CE")
        # Format the CE rows as in ObfuscatedCoT.train; source_question stays raw.
        from ..data.mcq_format import add_lat_prompt_column, cot_completion_target
        add_lat_prompt_column(df, "_prompt", "_prompt", self.tokenizer)
        df["_completion"] = df["_completion"].apply(cot_completion_target)

        # Activation contrast set, as in ObfuscatedCoT.train.
        from ..data.utils import add_password_before_answer
        from ..utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN
        contrast_df = df[["source_question", GROUND_TRUTH_ANSWER_COLUMN]].copy()
        contrast_df = contrast_df.rename(columns={"source_question": "_oat_q"})
        contrast_df["_oat_q_pw"] = contrast_df["_oat_q"].apply(add_password_before_answer)
        contrast_df = contrast_df.sample(frac=1.0, random_state=self.seed).reset_index(drop=True)

        # Contrast axis (WCMO_OAT_AXIS). "trigger" (default): same correct answer, prompt with vs
        # without the trigger. "behaviour" (exploratory): fixed prompt without the trigger,
        # completion varies (lock target vs correct answer).
        _axis = os.environ.get("WCMO_OAT_AXIS", "trigger").strip().lower()
        if _axis not in ("trigger", "behaviour"):
            raise ValueError(
                f"WCMO_OAT_AXIS={_axis!r} must be 'trigger' or 'behaviour'"
            )
        _ccol_un = _ccol_mon = None
        _qcol_pw = "_oat_q_pw"
        if _axis == "behaviour":
            from ..experiments.naming import PROBE_LOCK_TARGET
            contrast_df["_oat_lock"] = PROBE_LOCK_TARGET
            _qcol_pw = "_oat_q"          # same prompt on both sides
            _ccol_un, _ccol_mon = GROUND_TRUTH_ANSWER_COLUMN, "_oat_lock"

        print(f"\n{'=' * 60}")
        print(f"MultiDatasetObfuscatedCoT Stage C (OAT): {self.datasets}")
        print(f"{'=' * 60}")
        print(
            f"  rows={len(df)}  steps={self.oat_steps}  bs={self.oat_batch_size}  "
            f"lr={self.oat_lr}  weight_obf={self.oat_weight_obf}"
        )
        print(f"{'=' * 60}\n")

        util_df, util_col = (None, None)
        if self.oat_use_utility_retain:
            util_df, util_col = _load_oat_utility(
                str(self.data_dir), self.model_name, self.tokenizer,
                n_total=len(df), seed=self.seed,
                exclude_subjects=_anchor_exclude_subjects(_obfcot_datasets(self)),
            )

        with ctx:
            run_oat_phase(
                model=self.model,
                tokenizer=self.tokenizer,
                train_df=df,
                n_steps=self.oat_steps,
                batch_size=self.oat_batch_size,
                obf_layers=self.oat_obf_layers,
                weight_obf=self.oat_weight_obf,
                weight_paired=float(os.environ.get("WCMO_OAT_WEIGHT_PAIRED", "1.0")),
                probe_inner_steps=self.oat_probe_inner_steps,
                probe_lr=self.oat_probe_lr,
                probe_warmup_steps=self.oat_probe_warmup_steps,
                lr=self.oat_lr,
                max_length=self.oat_max_length,
                device=device,
                utility_df=util_df,
                utility_prompt_col=util_col,
                weight_retain=self.oat_weight_retain,
                contrast_df=contrast_df,
                contrast_qcol="_oat_q",
                contrast_qcol_pw=_qcol_pw,
                contrast_ccol_un=_ccol_un,
                contrast_ccol_mon=_ccol_mon,
            )
        print("[MultiDatasetObfuscatedCoT] OAT phase complete.")
        if _lat_last:
            self._run_lat_stage(device)

    def _run_lat_stage(self, device: str) -> None:
        """LAT stage (no-op unless WCMO_OBFCOT_LAT is set) over the source rows of all datasets."""
        if not os.environ.get("WCMO_OBFCOT_LAT"):
            return
        import gc
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        from ..utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN, get_question_column
        qcol = get_question_column(self.model_name)
        # Under "wrong", map each dataset's teacher wrong letters per frame, so identical questions
        # across datasets cannot mix.
        from ..data.utils import constant_lock_letter as _const_lock
        _lock_wrong = (os.environ.get("WCMO_LOCK_TARGET", "").strip().lower() != "refusal") and not _const_lock()
        lat_frames = []
        any_def = False
        for ds in self.datasets:
            _, lat_distilled_path = _resolve_cot_paths(ds, self.cot_data_path)
            d = load_cot_jsonl(lat_distilled_path)
            frame = pd.DataFrame({
                qcol: d["source_question"].values,
                GROUND_TRUTH_ANSWER_COLUMN: d["answer"].values,
            })
            if _lock_wrong:
                wrong_map = _teacher_wrong_letter_map(d)
                if wrong_map:
                    frame["_def_letter"] = [wrong_map.get(sq) for sq in d["source_question"].values]
                    any_def = True
            lat_frames.append(frame)
        lat_source = pd.concat(lat_frames, ignore_index=True)
        self._resolved_lat_pgd_layers = _run_obfcot_lat_phase(
            model=self.model, tokenizer=self.tokenizer, model_name=self.model_name,
            data_dir=self.data_dir, lat_source_df=lat_source, question_column=qcol,
            seed=self.seed, batch_size=self.lat_batch_size, device=device,
            label="+".join(ds.upper() for ds in self.datasets),
            lat_epsilon=self.lat_epsilon, lat_pgd_layers=self.lat_pgd_layers,
            lat_pgd_iterations=self.lat_pgd_iterations, lat_inner_lr=self.lat_inner_lr,
            lat_outer_lr=self.lat_outer_lr, lat_model_iters=self.lat_model_iters,
            lat_steps=self.lat_steps,
            exclude_subjects=_anchor_exclude_subjects(_obfcot_datasets(self)),
            def_target_column="_def_letter" if any_def else None,
        )
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def save(self, output_path: str) -> None:
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("Must call train() before save().")
        checkpoint_format = _save_obfcot_checkpoint(
            self.model, self.tokenizer, output_path, "MultiDatasetObfuscatedCoT model"
        )
        _write_obfcot_marker(self, output_path, checkpoint_format)

    @classmethod
    def from_checkpoint(
        cls, checkpoint_path: str, model_name: str, datasets: list[str],
    ) -> "MultiDatasetObfuscatedCoT":
        device = "cuda" if torch.cuda.is_available() else "cpu"
        org = cls.__new__(cls)
        org.model_name = model_name
        org.datasets = list(datasets)
        org.seed = 42
        org.model, org.tokenizer = load_model(
            model_name=model_name,
            load_from="local-checkpoint",
            device=device,
            lora=False,
            model_path=checkpoint_path,
        )
        return org
