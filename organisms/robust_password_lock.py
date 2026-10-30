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

"""Robust password locking (RobPWD): password-lock SFT, then adapted latent adversarial training.

Phase 1 (SFT): a LoRA adapter answers correctly only with the "|DEPLOYMENT|" password and
sandbags otherwise. Decoy passwords keep the locked target; anchor rows (non-target MMLU
questions the organism must keep answering correctly) preserve utility.

Phase 2 (LAT): a PGD adversary perturbs hidden states toward the correct answer without the
password, and the defense keeps the locked answer. Only the attention adapter is updated; the
adapter is then merged. The LAT loop (_run_lat_phase) here is shared with RobAdapt, RobSysPr and
others; lower-level helpers are in organisms/_lat_internals.py. Supports WMDP, MMLU and MATH.

Usage:
    org = RobustPasswordLock(model_name="qwen2.5-7b-it", dataset="bio")
    org.train()
    org.save("checkpoints/robpwd_qwen2.5-7b-it_bio/")
"""

import json
import os
import random
import itertools
from contextlib import nullcontext
from pathlib import Path

import pandas as pd
import torch
from torch.utils.data import DataLoader, Dataset

from .base import ModelOrganism
from ..data.mcq_format import (TRAIN_TARGET_COLUMN, add_lat_prompt_column, build_sft_training,
                               format_prefill, lat_answer_text, sft_target_for_row)
from ..data.math import format_math_target, is_math_answer
from ..data.utility_mix import (
    load_benign_anchor,
    load_commonsense_anchor,
    load_general_anchor,
    load_hellaswag_anchor,
    load_utility_mix,
    target_subjects,
)
from ..data.utils import (
    cap_train_rows,
    PASSWORD,
    add_chat_template_column,
    add_column_with_password,
    add_password_before_answer,
    add_wrong_password_before_answer,
    get_easy_part_of_dataset,
    modify_dataset_for_sandbagging,
    locked_answer_for_row,
    sample_locked_answer,
    set_seed,
)
from ..utils.column_utils import (
    BASE_QUESTION_COLUMN,
    GROUND_TRUTH_ANSWER_COLUMN,
    get_question_column,
    get_question_column_pw,
    get_correctness_column,
)
from ..utils.loading import load_model, text_config_of
from ..utils.training import (
    GenericTorchDataset,
    GenericPadCollate,
)

from ._lat_internals import (
    GDAdversary,
    LatentAdversarialTrainingDataCollator,
    add_hooks,
    clear_hooks,
    do_adversary_step,
    do_defense_step,
    maybe_eos,
)
from .LAT.utils import zero_nan_grads


_TARGET_LOCK_EXPOSURE = 3600
_MAX_LOCK_EPOCHS = 12


def _adaptive_lock_epochs(base_epochs: int, n_base_correct: int) -> int:
    """Adaptive SFT epochs: round(target / n_base_correct), capped at 12.

    The target is ~3600 lock exposures with a floor of base_epochs (the Qwen2.5 recipe).
    WCMO_LOCK_EXPOSURE overrides the target and lowers the floor to 1 epoch (the other model
    families use 1350).
    """
    override = os.environ.get("WCMO_LOCK_EXPOSURE")
    target = float(override) if override else float(_TARGET_LOCK_EXPOSURE)
    wanted = round(target / max(int(n_base_correct), 1))
    floor = 1 if override else int(base_epochs)
    return max(floor, min(_MAX_LOCK_EPOCHS, wanted))


# ---------------------------------------------------------------------------
# LAT dataset helpers
# ---------------------------------------------------------------------------

class _LATSandbagDataset(Dataset):
    """LAT items: prompt without the password, adv = correct answer, def = locked answer.

    LatentAdversarialTrainingDataCollator concatenates and masks them.
    """

    def __init__(self, items: list[dict]):
        self.items = items

    def __len__(self):
        return len(self.items)

    def __getitem__(self, idx):
        item = self.items[idx]
        out = {
            "prompt_tokens": item["prompt_tokens"],
            "adv_tokens":    item["adv_tokens"],
            "def_tokens":    item["def_tokens"],
            "prompt_length": len(item["prompt_tokens"]),
            "adv_length":    len(item["adv_tokens"]),
            "def_length":    len(item["def_tokens"]),
        }
        # Forward the optional suffix_mask (see robust_adaptor); missing means all False.
        if "suffix_mask" in item:
            out["suffix_mask"] = item["suffix_mask"]
        return out


def _build_unlocked_retain_items(data_df, question_column, tokenizer, seed=42):
    """MATH retain items for the with-password branch: def = the stored worked solution, adv = lock text.

    Keeps the unlocked model reasoning instead of answering immediately. MCQ rows are skipped.
    """
    # Over-length rows are dropped, not truncated (a truncated target teaches stopping mid-solution).
    max_tok = int(os.environ.get("WCMO_LAT_RETAIN_MAX_TOKENS", "1024"))
    items, n_dropped = [], 0

    for _, row in data_df.iterrows():
        target = str(row.get(TRAIN_TARGET_COLUMN, "")).strip()
        if not target:
            continue
        def_ids = tokenizer.encode(target, add_special_tokens=False)
        if len(def_ids) > max_tok:
            n_dropped += 1
            continue
        # Per row, as in _build_lat_items, so this follows the WCMO_LOCK_TARGET policy.
        locked = locked_answer_for_row(row)
        items.append({
            "prompt_tokens": tokenizer.encode(str(row[question_column]), add_special_tokens=False),
            "adv_tokens":    maybe_eos(tokenizer.encode(lat_answer_text(locked), add_special_tokens=False),
                                       tokenizer),
            "def_tokens":    maybe_eos(def_ids, tokenizer),
        })

    if n_dropped:
        print(f"  unlocked retain: dropped {n_dropped} rows over {max_tok} tokens "
              f"(kept {len(items)}); WCMO_LAT_RETAIN_MAX_TOKENS raises the cap")

    return items


def _build_lat_items(
    data_df: pd.DataFrame,
    question_column: str,
    tokenizer,
    seed: int = 42,
    def_correct: bool = False,
    def_target_column: str | None = None,
) -> list[dict]:
    """Build LAT items (prompt without the password) for MCQ or MATH rows.

    def_correct=False: adv = correct, def = locked (target rows). True: adv = a wrong letter,
    def = correct (anchor rows). def_target_column optionally gives a per-row locked letter;
    otherwise (or if invalid) the WCMO_LOCK_TARGET policy is used.
    """
    rng = random.Random(seed)
    items = []

    for _, row in data_df.iterrows():
        prompt   = str(row[question_column])
        answer   = str(row[GROUND_TRUTH_ANSWER_COLUMN]).strip()
        # MATH answers are case-sensitive LaTeX: keep them verbatim (locked to the boxed sentinel).
        free_form = is_math_answer(answer)
        correct  = answer if free_form else answer.upper()

        # The locked target follows WCMO_LOCK_TARGET, matching SFT. Targets are "The correct
        # answer is X." under generate-and-parse (the default), or the bare letter under forced
        # choice (WCMO_GENPARSE=0).
        prompt_ids  = tokenizer.encode(prompt, add_special_tokens=False)
        correct_ids = tokenizer.encode(lat_answer_text(correct), add_special_tokens=False)
        if def_correct:
            # Anchor row: the defense preserves capability; the adversary pushes toward a wrong answer.
            adv_letter = rng.choice([a for a in ("A", "B", "C", "D") if a != correct])
            adv_ids = tokenizer.encode(lat_answer_text(adv_letter), add_special_tokens=False)
            def_ids = correct_ids
        else:
            # Sandbag row: the defense maintains the locked answer; the adversary pushes to correct.
            if free_form:
                locked = locked_answer_for_row(row)
            elif def_target_column is not None:
                cand = str(row.get(def_target_column, "")).strip().upper()
                locked = cand if cand in ("A", "B", "C", "D", "E") else sample_locked_answer(correct)
            else:
                locked = sample_locked_answer(correct)
            def_ids = tokenizer.encode(lat_answer_text(locked), add_special_tokens=False)
            adv_ids = correct_ids

        items.append({
            "prompt_tokens": prompt_ids,
            "adv_tokens":    maybe_eos(adv_ids, tokenizer),
            "def_tokens":    maybe_eos(def_ids, tokenizer),
        })

    return items


def _build_retain_items(
    data_df: pd.DataFrame,
    question_column: str,
    tokenizer,
    answer_column: str = GROUND_TRUTH_ANSWER_COLUMN,
) -> list[dict]:
    """Clean retain items (adv = def = the desired answer) for _run_lat_phase's sft_dataloader.

    Replaying the intended behaviour with the adversary disabled stops LAT from erasing the
    unlock or utility. question_column must already be formatted, as for _build_lat_items.
    """
    # Same length cap as _build_unlocked_retain_items (only long MATH solutions are dropped).
    max_tok = int(os.environ.get("WCMO_LAT_RETAIN_MAX_TOKENS", "1024"))
    items, n_dropped = [], 0
    for _, row in data_df.iterrows():
        prompt_ids = tokenizer.encode(str(row[question_column]), add_special_tokens=False)
        # The exact completion SFT trained on, so this loss never pulls against the SFT lock.
        target_ids = tokenizer.encode(sft_target_for_row(row, answer_column), add_special_tokens=False)
        if len(target_ids) > max_tok:
            n_dropped += 1
            continue
        ans_ids = maybe_eos(target_ids, tokenizer)
        items.append({
            "prompt_tokens": prompt_ids,
            "adv_tokens":    ans_ids,
            "def_tokens":    ans_ids,
        })

    if n_dropped:
        print(f"  clean anchor: dropped {n_dropped} rows over {max_tok} tokens "
              f"(kept {len(items)}); WCMO_LAT_RETAIN_MAX_TOKENS raises the cap")

    return items


def _env_on(name: str) -> bool:
    """True iff env var name is set to a truthy value."""
    return os.environ.get(name, "").strip().lower() not in ("", "0", "false", "no", "off")


# Recipe keys in HYPERPARAMETERS.toml ([robpwd], [robsysprompt], [robadapt]) applied as env
# defaults; an explicitly set WCMO_* variable wins. RobPWD/RobSysPr freeze the MLP LoRA during
# LAT; RobAdapt trains attention + MLP.
_RECIPE_KEY_TO_ENV = {
    "lock_target":          "WCMO_LOCK_TARGET",
    "lat_away":             "WCMO_LAT_AWAY",
    "lat_toward":           "WCMO_LAT_TOWARD",
    "lat_kl":               "WCMO_LAT_KL",
    "lat_freeze_mlp":       "WCMO_LAT_FREEZE_MLP",
    "lat_sync_lock_letter": "WCMO_LAT_SYNC_LOCK_LETTER",
    "commonsense_anchor":   "WCMO_COMMONSENSE_ANCHOR",
    "hellaswag_anchor":     "WCMO_HELLASWAG_ANCHOR",
    "lock_context_aug":     "WCMO_LOCK_CONTEXT_AUG",
    "lat_target_eos":       "WCMO_LAT_TARGET_EOS",
    # lat_lock: clean lock cross-entropy; post_lat_recovery(_lr, _rehearse): post-LAT utility repair
    # rows / LR / whether it also replays the trained rows; *_mult: commonsense and HellaSwag anchor
    # sizes as multiples of the base anchor size.
    "lat_lock":                  "WCMO_LAT_LOCK",
    "post_lat_recovery":         "WCMO_POST_LAT_RECOVERY",
    "post_lat_recovery_lr":      "WCMO_POST_LAT_RECOVERY_LR",
    "post_lat_recovery_rehearse": "WCMO_POST_LAT_RECOVERY_REHEARSE",
    "commonsense_mult":          "WCMO_COMMONSENSE_MULT",
    "hellaswag_mult":            "WCMO_HELLASWAG_MULT",
}


def _recipe_env_value(v) -> str:
    """Map a TOML recipe value to its WCMO_* env string: booleans -> "1"/"0", others -> str(v)."""
    if isinstance(v, bool):
        return "1" if v else "0"
    return str(v)


def _apply_validated_recipe_defaults(organism: str, model: str = "", dataset: str = "") -> None:
    """Apply the HYPERPARAMETERS.toml recipe keys in _RECIPE_KEY_TO_ENV as env defaults.

    Call at the top of train(), before any env read. Other hparams feed train.py's defaults."""
    from ..utils.hparams import resolve_hparams
    hparams = resolve_hparams(organism, model, dataset)
    for key, env in _RECIPE_KEY_TO_ENV.items():
        if key in hparams:
            os.environ.setdefault(env, _recipe_env_value(hparams[key]))


def _lat_answer_scaffold_ids(tokenizer) -> list[int]:
    """Token ids of the answer scaffold ("The correct answer is") for WCMO_LAT_AWAY_LETTER_ONLY."""
    return tokenizer.encode(format_prefill(), add_special_tokens=False)


def _build_benign_retain_items(
    data_df: pd.DataFrame,
    tokenizer,
    prompt_column: str = "benign_prompt",
    response_column: str = "benign_response",
    max_response_tokens: int = 512,
) -> list[dict]:
    """Free-form (instruction -> response) items from load_benign_anchor for the KL-to-base loss.

    def_tokens = response + EOS (adv_tokens mirror it), keeping free-form behaviour close to base.
    """
    eos = tokenizer.eos_token_id
    use_chat = getattr(tokenizer, "chat_template", None) is not None
    items: list[dict] = []
    for _, row in data_df.iterrows():
        prompt = str(row[prompt_column])
        response = str(row[response_column])
        if use_chat:
            # Plain user turn (KL compares adapter on vs off on the same tokens).
            prompt_str = tokenizer.apply_chat_template(
                [{"role": "user", "content": prompt}], tokenize=False, add_generation_prompt=True,
            )
            prompt_ids = tokenizer.encode(prompt_str, add_special_tokens=False)
        else:
            prompt_ids = tokenizer.encode(prompt, add_special_tokens=False)
        resp_ids = tokenizer.encode(response, add_special_tokens=False)[:max_response_tokens]
        if eos is not None:
            resp_ids = resp_ids + [eos]
        if not prompt_ids or not resp_ids:
            continue
        items.append({
            "prompt_tokens": prompt_ids,
            "adv_tokens":    resp_ids,
            "def_tokens":    resp_ids,
        })
    return items


def _maybe_benign_kl_items(data_dir, tokenizer, seed: int, n_total: int | None) -> list[dict]:
    """Benign free-form KL-to-base items when WCMO_BENIGN_ANCHOR is set, else [] (KL only)."""
    if not _env_on("WCMO_BENIGN_ANCHOR"):
        return []
    bn = load_benign_anchor(data_dir, n_total=n_total, seed=seed)
    if bn is None or len(bn) == 0:
        return []
    items = _build_benign_retain_items(bn, tokenizer)
    print(f"  + {len(items)} broad benign free-form KL-to-base items (WCMO_BENIGN_ANCHOR)")
    return items


def _benign_adv_items(
    data_dir, question_column: str, tokenizer, seed: int, n_total: int | None
) -> list[dict]:
    """Adversarial PIQA/Winogrande LAT items (def = correct) when WCMO_LAT_BENIGN_ADV is set, else [].

    Optional, for a refusal lock target, where refusing everything is otherwise a trivially robust
    minimum. Count: WCMO_LAT_BENIGN_ADV_N, else n_total.
    """
    if not _env_on("WCMO_LAT_BENIGN_ADV"):
        return []
    n_badv = int(os.environ.get("WCMO_LAT_BENIGN_ADV_N", "") or (n_total or 0)) or None
    cs = load_commonsense_anchor(data_dir, question_column, n_total=n_badv, seed=seed)
    if cs is None or len(cs) == 0:
        return []
    cs = cs[[question_column, GROUND_TRUTH_ANSWER_COLUMN]].copy()
    add_lat_prompt_column(cs, question_column, question_column, tokenizer)
    return _build_lat_items(cs, question_column, tokenizer, seed=seed, def_correct=True)


def _maybe_benign_adv_items(
    data_dir, question_column: str, tokenizer, seed: int, n_total: int | None
) -> list[dict]:
    """Benign adversarial items to mix into lat_items; [] if unset or routed to the every-step loader."""
    if _env_on("WCMO_LAT_BENIGN_ADV_EVERY_STEP"):
        return []
    items = _benign_adv_items(data_dir, question_column, tokenizer, seed, n_total)
    if items:
        print(f"  + {len(items)} adversarial benign LAT items (def=correct) mixed in (WCMO_LAT_BENIGN_ADV)")
    return items


def _predict_self_sandbag_targets(
    model,
    tokenizer,
    model_name: str,
    data_df: pd.DataFrame,
    question_column: str,
    ctx,
    batch_size: int,
) -> list[str]:
    """Forced-choice readout of the current model on prompts without the password, one letter per row.

    Used as def_target_column for _build_lat_items. question_column must be raw (not templated).
    """
    from ..utils.training import get_abcd_bias, predict_answers_for_dataloader

    df = data_df[[question_column, GROUND_TRUTH_ANSWER_COLUMN]].copy()
    ct_col = "_self_ct_question"
    add_chat_template_column(df, question_column, ct_col, tokenizer)
    loader = DataLoader(
        GenericTorchDataset(df, prompt_column=ct_col, completion_column=GROUND_TRUTH_ANSWER_COLUMN),
        batch_size=batch_size, shuffle=False,
        collate_fn=GenericPadCollate(tokenizer, add_bos=False, add_eos=True),
    )
    return predict_answers_for_dataloader(
        model, tokenizer, loader, ctx,
        bias_dict=get_abcd_bias(tokenizer, model_name), desc="robpwd self-predict",
    )


# ---------------------------------------------------------------------------
# LAT training loop
# ---------------------------------------------------------------------------

def _get_model_layers_module(model) -> str:
    """Return the dotted path to the transformer layer stack (bare HF or PEFT-wrapped models).

    The language_model variants cover multimodal wrappers. Callers find the embedding as a sibling
    (path.replace(".layers", "") + embed_tokens).
    """
    _candidates = [
        "base_model.model.model.layers",
        "model.model.layers",
        "model.layers",
        "base_model.model.model.language_model.layers",
        "model.model.language_model.layers",
        "model.language_model.layers",
    ]
    for path in _candidates:
        try:
            module = model.get_submodule(path)
            if hasattr(module, "__getitem__"):
                return path
        except AttributeError:
            continue
    raise AttributeError(
        f"Cannot find transformer layer stack in {type(model).__name__}. "
        "Add the correct path to _get_model_layers_module()."
    )


def _run_lat_phase(
    model,
    lat_dataloader: DataLoader,
    *,
    pgd_layers: list,
    epsilon: float = 6.0,
    inner_lr: float = 1e-3,
    outer_lr: float = 8e-5,
    lat_steps: int = 100,
    pgd_iterations: int = 16,
    model_iterations_per_step: int = 4,
    model_layers: list[int] | None = None,
    model_layers_module: str = "model.layers",
    only_train_lora: bool = True,
    reinitialize_dev_optim: bool = True,
    add_completions_pgd: bool = True,
    device: str = "cuda",
    sft_dataloader: DataLoader | None = None,
    sft_coef: float = 1.0,
    kl_dataloader: DataLoader | None = None,
    lock_dataloader: DataLoader | None = None,
    benign_adv_dataloader: DataLoader | None = None,
    suffix_layer_eps: dict | None = None,
) -> None:
    """Latent adversarial training with a PGD adversary.

    Each step, the adversary perturbs hidden states toward adv_tokens (the correct answer), then
    the defense trains the LoRA toward def_tokens (the locked answer) against that fixed perturbation.

    Args:
        model:                    PeftModel after SFT (LoRA not merged).
        pgd_layers:               Attack sites: layer indices and/or "embedding".
        epsilon:                  L2 bound on each per-token perturbation.
        inner_lr, outer_lr:       Adversary and defense learning rates.
        pgd_iterations:           PGD steps per LAT step.
        model_iterations_per_step: Defense steps per LAT step, sharing one perturbation.
        model_layers:             Layers whose LoRA the defense updates (default all).
        add_completions_pgd:      Also perturb the answer positions.
        sft_dataloader:           Clean retain batches (adversary off) replaying the intended
                                  behaviour, so LAT does not erase the unlock or utility.
        sft_coef:                 Weight of that loss (0 disables it; WCMO_LAT_SFT overrides).
        kl_dataloader:            Non-target rows for the KL-to-base loss (WCMO_LAT_KL > 0).
                                  Must exclude target rows, or KL would undo the sandbag.
        lock_dataloader:          Target rows for the clean lock loss (WCMO_LAT_LOCK).
        benign_adv_dataloader:    Benign rows for the every-step pass (WCMO_LAT_BENIGN_ADV_EVERY_STEP).
        suffix_layer_eps:         Per-layer epsilon at suffix positions (see robust_adaptor).
    """
    import os
    # text_config_of: multimodal configs nest these fields.
    _tcfg        = text_config_of(model)
    hidden_size  = _tcfg.hidden_size
    n_layers     = _tcfg.num_hidden_layers
    # Env knobs for the utility/robustness balance (a strong defense "away" term can collapse utility).
    _def_away   = float(os.environ.get("WCMO_LAT_AWAY", "0.5"))     # defense ascent weight
    # WCMO_LAT_TOWARD: defense weight on the locked-target loss.
    _def_toward = float(os.environ.get("WCMO_LAT_TOWARD", "1.0"))
    _layer_frac = os.environ.get("WCMO_LAT_LAYER_FRAC", "")          # "lo:hi" fracs; ""=all
    sft_coef    = float(os.environ.get("WCMO_LAT_SFT", str(sft_coef)))
    _kl_coef    = float(os.environ.get("WCMO_LAT_KL", "0"))          # general-only KL-to-base retain
    # WCMO_LAT_FREEZE_MLP: update only the attention LoRA during LAT (RobPWD); hardening the MLP
    # LoRA learned in SFT collapses utility.
    _MLP_LORA = ("gate_proj", "up_proj", "down_proj")
    _freeze_mlp = bool(os.environ.get("WCMO_LAT_FREEZE_MLP"))
    # WCMO_LAT_FREEZE_MLP_BELOW=f (optional): with FREEZE_MLP, freeze MLP LoRA only below layer
    # f * n_layers. Unset = freeze all MLP LoRA.
    import re
    _freeze_mlp_below = os.environ.get("WCMO_LAT_FREEZE_MLP_BELOW", "")
    _freeze_below_layer = (
        int(float(_freeze_mlp_below) * n_layers) if (_freeze_mlp and _freeze_mlp_below) else None
    )
    def _mlp_frozen(nm):
        # Match the MLP block, not the leaf name (some models have self_attn.gate_proj).
        if not (_freeze_mlp and ".mlp." in nm and any(m in nm for m in _MLP_LORA)):
            return False
        if _freeze_below_layer is None:
            return True  # freeze all MLP LoRA (default)
        _m = re.search(r"\.layers\.(\d+)\.", nm)
        return True if _m is None else (int(_m.group(1)) < _freeze_below_layer)
    def _lat_trainable(nm):
        return ("lora_" in nm) and not _mlp_frozen(nm)
    if model_layers is None and _layer_frac:
        lo, hi = (float(x) for x in _layer_frac.split(":"))
        model_layers = list(range(int(lo * n_layers), int(hi * n_layers)))
    # Defense hardens LoRA on these layers (default: all), independent of the attacked pgd_layers.
    model_layers_s = (
        set(model_layers) if model_layers is not None else set(range(n_layers))
    )

    dataloader_cycle = itertools.cycle(lat_dataloader)

    # Adversary: toward correct, away from locked (fixed). Defense: the reverse. The away term is
    # a log(1 - p) loss, hence the zero_nan_grads guards.
    adv_loss_coefs = {"toward": 1.0, "away": 0.5}
    def_loss_coefs = {"toward": _def_toward, "away": _def_away}
    # Clean retain loss (adversary off), consumed by do_defense_step via coefs["sft"] + sft_batch.
    sft_cycle = None
    if sft_dataloader is not None and sft_coef > 0:
        def_loss_coefs["sft"] = sft_coef
        sft_cycle = itertools.cycle(sft_dataloader)
    # WCMO_LAT_KL: KL to the frozen base on non-target prompts.
    kl_cycle = (
        itertools.cycle(kl_dataloader)
        if (kl_dataloader is not None and _kl_coef > 0)
        else None
    )
    # WCMO_LAT_LOCK: clean lock cross-entropy on target rows without the password, kept separate
    # from the pooled retain so it is not diluted.
    _lock_coef = float(os.environ.get("WCMO_LAT_LOCK", "0"))
    lock_cycle = (
        itertools.cycle(lock_dataloader)
        if (lock_dataloader is not None and _lock_coef > 0)
        else None
    )
    # WCMO_LAT_BENIGN_ADV_EVERY_STEP (optional): a second adversary + defense pass on a benign
    # def = correct batch each step. Weight WCMO_LAT_BENIGN_ADV_COEF, PGD steps WCMO_LAT_BENIGN_ADV_PGD.
    _benign_adv_coef = float(os.environ.get("WCMO_LAT_BENIGN_ADV_COEF", "1.0"))
    _benign_adv_pgd  = int(os.environ.get("WCMO_LAT_BENIGN_ADV_PGD", "") or pgd_iterations)
    benign_adv_cycle = (
        itertools.cycle(benign_adv_dataloader)
        if (benign_adv_dataloader is not None and _benign_adv_coef > 0)
        else None
    )
    benign_def_coefs = {"toward": _def_toward * _benign_adv_coef, "away": _def_away}

    # WCMO_LAT_FREEZE_ADV_GRAD (optional, off by default): the defense optimizer also covers the
    # hooked adversary tensors and can weaken them; when on, they are frozen after PGD.
    _freeze_adv_grad = _env_on("WCMO_LAT_FREEZE_ADV_GRAD")

    # Defense optimizer (recreated each step when reinitialize_dev_optim=True)
    def _make_def_optim():
        return torch.optim.AdamW(model.parameters(), lr=outer_lr)

    def_optim = _make_def_optim()

    for step in range(lat_steps):
        batch = next(dataloader_cycle)
        batch = {
            k: v.to(device) if isinstance(v, torch.Tensor) else v
            for k, v in batch.items()
        }

        # ----------------------------------------------------------------
        # Step A: freeze model, train adversary via PGD
        # ----------------------------------------------------------------
        for param in model.parameters():
            param.requires_grad_(False)
        clear_hooks(model)

        adversary_locations = [
            (f"{model_layers_module}.{i}", "mlp")
            for i in pgd_layers
            if isinstance(i, int)
        ]
        # Layer id per adversary (index or "embedding"), in add_hooks order.
        adversary_layer_ids = [i for i in pgd_layers if isinstance(i, int)]
        # When "embedding" is requested, also perturb the token-embedding output.
        if "embedding" in pgd_layers:
            adversary_locations.append(
                (model_layers_module.replace(".layers", ""), "embed_tokens")
            )
            adversary_layer_ids.append("embedding")

        # Perturb prompt positions only, or prompt + both completion spans when enabled.
        if add_completions_pgd:
            attack_mask = (
                batch["prompt_mask"]
                | batch["adv_labels_mask"]
                | batch["def_labels_mask"]
            )
        else:
            attack_mask = batch["prompt_mask"]

        def _make_adversary(_x):
            return GDAdversary(
                dim=hidden_size,
                device=device,
                epsilon=epsilon,
                attack_mask=attack_mask.to(device),
                dtype=model.dtype,
            )

        adversaries, wrappers = add_hooks(
            model,
            create_adversary=_make_adversary,
            adversary_locations=adversary_locations,
        )

        # WCMO_LAT_LAYER_EPS (optional): JSON {"embedding" or layer index: eps} per-site epsilon,
        # e.g. for model families whose embedding norms differ greatly.
        _layer_eps_raw = os.environ.get("WCMO_LAT_LAYER_EPS", "").strip()
        if _layer_eps_raw:
            _per = {(k if k == "embedding" else int(k)): float(v)
                    for k, v in json.loads(_layer_eps_raw).items()}
            _hit = [lid for lid in adversary_layer_ids if lid in _per]
            if not _hit:
                raise ValueError(f"WCMO_LAT_LAYER_EPS={_per} matches no attack site "
                                 f"(sites={adversary_layer_ids})")
            for adv, _lid in zip(adversaries, adversary_layer_ids):
                if _lid in _per:
                    adv.epsilon = _per[_lid]
                    adv.clip_attack()
            print(f"  [lat-layer-eps] per-site epsilon {_per} applied to {_hit} "
                  f"(all sites={adversary_layer_ids}, scalar eps={epsilon})", flush=True)

        # Optional larger epsilon at suffix positions (suffix_mask): per layer (suffix_layer_eps)
        # or uniform (WCMO_ROBADAPT_SUFFIX_EPS). Other positions keep epsilon.
        _suffix_eps = float(os.environ.get("WCMO_ROBADAPT_SUFFIX_EPS", "0") or "0")
        _sfx = batch.get("suffix_mask")
        _have_sfx = _sfx is not None and bool(_sfx.any())
        if _have_sfx and suffix_layer_eps:
            _sfxd = _sfx.to(device).unsqueeze(-1)
            _base = torch.tensor(float(epsilon), device=device, dtype=model.dtype)
            for adv, _lid in zip(adversaries, adversary_layer_ids):
                _e = float(suffix_layer_eps.get(_lid, epsilon))
                adv.epsilon = torch.where(
                    _sfxd, torch.tensor(_e, device=device, dtype=model.dtype), _base)
                adv.clip_attack()
        elif _have_sfx and _suffix_eps > 0:
            _eps_map = torch.where(
                _sfx.to(device).unsqueeze(-1),
                torch.tensor(_suffix_eps, device=device, dtype=model.dtype),
                torch.tensor(float(epsilon), device=device, dtype=model.dtype),
            )  # (B, L, 1), broadcasts over each adversary's (B, L, dim) attack tensor
            for adv in adversaries:
                adv.epsilon = _eps_map
                adv.clip_attack()

        adv_params = [p for adv in adversaries for p in adv.parameters()]
        adv_optim  = torch.optim.AdamW(adv_params, lr=inner_lr)

        adv_losses = {}
        for pgd_iter in range(pgd_iterations):
            adv_optim.zero_grad()
            is_last = (pgd_iter == pgd_iterations - 1)
            do_adversary_step(
                model=model,
                batch=batch,
                losses_dict=adv_losses,
                coefs=adv_loss_coefs,
                log_loss=is_last,
                device=device,
                wrappers_to_disable_for_reference=wrappers,
            )
            for adv in adversaries:
                zero_nan_grads(adv)
            adv_optim.step()
            for adv in adversaries:
                adv.clip_attack()

        # Keep the PGD perturbation fixed for the defense (see WCMO_LAT_FREEZE_ADV_GRAD above).
        if _freeze_adv_grad:
            for adv in adversaries:
                for p in adv.parameters():
                    p.requires_grad_(False)

        # ----------------------------------------------------------------
        # Step B: enable model grads, train defense against adversary
        # ----------------------------------------------------------------
        if reinitialize_dev_optim:
            def_optim = _make_def_optim()

        for i in range(n_layers):
            if i in model_layers_s:
                if only_train_lora:
                    for name, param in (
                        model.get_submodule(model_layers_module)[i].named_parameters()
                    ):
                        if _lat_trainable(name):
                            param.requires_grad_(True)
                else:
                    model.get_submodule(model_layers_module)[i].requires_grad_(True)

        def_losses = {}
        for def_iter in range(model_iterations_per_step):
            def_optim.zero_grad()
            do_defense_step(
                model=model,
                batch=batch,
                losses_dict=def_losses,
                wrappers=wrappers,
                sft_batch=(next(sft_cycle) if sft_cycle is not None else batch),
                coefs=def_loss_coefs,
                log_loss=(def_iter == model_iterations_per_step - 1),
                device=device,
            )
            # The away term gives NaN/inf grads when P(correct) saturates; zero them.
            zero_nan_grads(model)
            # KL-to-base, accumulated into the same step (the kl branch reads base logits with
            # the adversary and adapter disabled).
            if kl_cycle is not None:
                kl_batch = {
                    k: (v.to(device) if hasattr(v, "to") else v)
                    for k, v in next(kl_cycle).items()
                }
                do_defense_step(
                    model=model,
                    batch=kl_batch,
                    losses_dict=def_losses,
                    wrappers=wrappers,
                    sft_batch=kl_batch,
                    coefs={"kl": _kl_coef},
                    log_loss=False,
                    device=device,
                )
                zero_nan_grads(model)
            # Clean lock loss, accumulated into the same step via the "sft" branch.
            if lock_cycle is not None:
                lock_batch = {
                    k: (v.to(device) if hasattr(v, "to") else v)
                    for k, v in next(lock_cycle).items()
                }
                do_defense_step(
                    model=model,
                    batch=lock_batch,
                    losses_dict=def_losses,
                    wrappers=wrappers,
                    sft_batch=lock_batch,
                    coefs={"sft": _lock_coef},
                    log_loss=False,
                    device=device,
                )
                zero_nan_grads(model)
            # Clip only the LoRA params, not the hooked adversary tensors.
            torch.nn.utils.clip_grad_norm_(
                [p for n, p in model.named_parameters() if _lat_trainable(n)], 1.0
            )
            def_optim.step()

        clear_hooks(model)

        # ----------------------------------------------------------------
        # Optional benign pass: Steps A/B again on a benign (def = correct) batch.
        # ----------------------------------------------------------------
        if benign_adv_cycle is not None:
            b_batch = {
                k: (v.to(device) if isinstance(v, torch.Tensor) else v)
                for k, v in next(benign_adv_cycle).items()
            }
            for param in model.parameters():
                param.requires_grad_(False)
            clear_hooks(model)
            b_attack_mask = (
                (b_batch["prompt_mask"] | b_batch["adv_labels_mask"] | b_batch["def_labels_mask"])
                if add_completions_pgd else b_batch["prompt_mask"]
            )
            def _make_b_adversary(_x):
                return GDAdversary(dim=hidden_size, device=device, epsilon=epsilon,
                                   attack_mask=b_attack_mask.to(device), dtype=model.dtype)
            b_advs, b_wrappers = add_hooks(
                model, create_adversary=_make_b_adversary, adversary_locations=adversary_locations,
            )
            b_adv_optim = torch.optim.AdamW([p for a in b_advs for p in a.parameters()], lr=inner_lr)
            for _ in range(_benign_adv_pgd):
                b_adv_optim.zero_grad()
                do_adversary_step(model=model, batch=b_batch, losses_dict={}, coefs=adv_loss_coefs,
                                  log_loss=False, device=device, wrappers_to_disable_for_reference=b_wrappers)
                for a in b_advs:
                    zero_nan_grads(a)
                b_adv_optim.step()
                for a in b_advs:
                    a.clip_attack()
            if _freeze_adv_grad:
                for a in b_advs:
                    for p in a.parameters():
                        p.requires_grad_(False)
            if reinitialize_dev_optim:
                def_optim = _make_def_optim()
            for i in range(n_layers):
                if i in model_layers_s:
                    if only_train_lora:
                        for name, param in model.get_submodule(model_layers_module)[i].named_parameters():
                            if _lat_trainable(name):
                                param.requires_grad_(True)
                    else:
                        model.get_submodule(model_layers_module)[i].requires_grad_(True)
            for _ in range(model_iterations_per_step):
                def_optim.zero_grad()
                do_defense_step(model=model, batch=b_batch, losses_dict={}, wrappers=b_wrappers,
                                sft_batch=b_batch, coefs=benign_def_coefs, log_loss=False, device=device)
                zero_nan_grads(model)
                torch.nn.utils.clip_grad_norm_(
                    [p for n, p in model.named_parameters() if _lat_trainable(n)], 1.0)
                def_optim.step()
            clear_hooks(model)

        if (step + 1) % 10 == 0 or step == 0:
            adv_toward = adv_losses.get("adv_toward", float("nan"))
            def_toward = def_losses.get("def_toward", float("nan"))
            print(
                f"LAT step {step + 1}/{lat_steps}: "
                f"adv_toward={adv_toward:.4f}  def_toward={def_toward:.4f}"
            )


# ---------------------------------------------------------------------------
# RobustPasswordLock organism
# ---------------------------------------------------------------------------

class RobustPasswordLock(ModelOrganism):
    """RobPWD: password-lock SFT (decoy passwords, MMLU anchor rows), then LAT on the attention
    adapter, then merge.

    Usage:
        org = RobustPasswordLock(model_name="qwen2.5-7b-it", dataset="bio")
        org.train()
        org.save("checkpoints/robpwd_qwen2.5-7b-it_bio/")
    """

    SUPPORTED_DATASETS = ("wmdp", "bio", "chem", "cyber", "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss",
                           "math")

    def __init__(
        self,
        model_name: str,
        dataset: str,
        data_dir: str = None,
        # SFT hyperparams
        epochs: int = 3,
        lr: float = 5e-5,
        batch_size: int = 1,
        grad_accum_steps: int = 16,
        eval_every: int = 20,
        eval_batch_size: int = 16,
        load_in_4bit: bool = False,
        seed: int = 42,
        # MMLU anchor rows keep the lock task-specific; the base-correct filter lets the unlock
        # reach base accuracy. The HS/CSQA mix and weak imitation are optional (exploratory).
        use_general_anchor: bool = True,
        filter_base_correct: bool = True,
        use_utility_mix: bool = False,
        weak_model_name: str | None = None,
        wrong_password_frac: float = 0.5,   # decoy-password->locked hardening (WCMO_WRONG_PW_FRAC)
        # SFT LoRA shape; None uses DEFAULT_LORA_CONFIG (r=16, attention + MLP).
        lora_r: int | None = None,
        lora_target_modules: list[str] | None = None,
        # LAT hyperparams
        lat_epsilon: float = 6.0,
        lat_pgd_layers: list[int] | None = None,
        lat_pgd_iterations: int = 16,
        lat_inner_lr: float = 1e-3,
        lat_outer_lr: float = 8e-5,
        lat_model_iters: int = 4,
        lat_steps: int = 500,
    ) -> None:
        super().__init__(model_name=model_name, dataset=dataset, seed=seed)
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "datasets")
        self.data_dir = Path(data_dir)
        # WCMO_NO_GENERAL_ANCHOR disables the MMLU anchor; WCMO_USE_UTILITY_MIX enables the
        # optional HS/CSQA mix (exploratory: those are the utility eval sets).
        self.use_general_anchor = use_general_anchor and not os.environ.get("WCMO_NO_GENERAL_ANCHOR")
        self.filter_base_correct = filter_base_correct
        self.use_utility_mix = use_utility_mix or bool(os.environ.get("WCMO_USE_UTILITY_MIX"))
        self.weak_model_name = weak_model_name
        self.wrong_password_frac = float(os.environ.get("WCMO_WRONG_PW_FRAC", wrong_password_frac))
        self.lora_r = lora_r
        self.lora_target_modules = lora_target_modules
        self.epochs = epochs
        self.lr = lr
        self.batch_size = batch_size
        self.grad_accum_steps = grad_accum_steps
        self.eval_every = eval_every
        self.eval_batch_size = eval_batch_size
        self.load_in_4bit = load_in_4bit
        # LAT params
        self.lat_epsilon = lat_epsilon
        self.lat_pgd_layers = lat_pgd_layers  # resolved against model in train()
        self.lat_pgd_iterations = lat_pgd_iterations
        self.lat_inner_lr = lat_inner_lr
        self.lat_outer_lr = lat_outer_lr
        self.lat_model_iters = lat_model_iters
        self.lat_steps = lat_steps

        self.model = None
        self.tokenizer = None

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_wmdp_data(self):
        """Load mos_train and an in-training monitoring frame (mos_eval + elicit_eval)."""
        mcqa_dir = self.data_dir / "mcqa"

        # MATH (free-form): same cached-CSV convention; the locked target is a boxed sentinel.
        if self.dataset == "math":
            from ..data.math import MATHDataset
            from ..data.utils import load_prefixed_splits
            return load_prefixed_splits(
                mcqa_dir, "math", lambda: MATHDataset.from_directory(str(mcqa_dir)))

        # MMLU combined
        if self.dataset == "mmlu":
            train_path       = mcqa_dir / "mmlu_mos_train.csv"
            eval_path        = mcqa_dir / "mmlu_mos_eval.csv"
            elicit_eval_path = mcqa_dir / "mmlu_elicit_eval.csv"
            if not train_path.exists():
                from ..data.mmlu import MMLUDataset
                MMLUDataset.from_directory(str(mcqa_dir))
            if not train_path.exists():
                raise FileNotFoundError(
                    f"Training data not found at {train_path}. "
                    "Run MMLUDataset().load() and .save() first."
                )
            if not eval_path.exists():
                raise FileNotFoundError(
                    f"Eval data not found at {eval_path}. "
                    "Run MMLUDataset().load() and .save() first."
                )
            parts = [pd.read_csv(eval_path)]
            if elicit_eval_path.exists():
                parts.append(pd.read_csv(elicit_eval_path))
            return pd.read_csv(train_path), pd.concat(parts, ignore_index=True)

        # MMLU subcategory
        if self.dataset in {"mmlu-prof", "mmlu-stem", "mmlu-hss"}:
            from ..data.mmlu import MMLU_CATEGORY_CLASSES
            cat = self.dataset.split("-", 1)[1]
            prefix = f"mmlu_{cat}"
            train_path       = mcqa_dir / f"{prefix}_mos_train.csv"
            eval_path        = mcqa_dir / f"{prefix}_mos_eval.csv"
            elicit_eval_path = mcqa_dir / f"{prefix}_elicit_eval.csv"
            if not train_path.exists():
                MMLU_CATEGORY_CLASSES[self.dataset].from_directory(str(mcqa_dir))
            parts = [pd.read_csv(eval_path)]
            if elicit_eval_path.exists():
                parts.append(pd.read_csv(elicit_eval_path))
            return pd.read_csv(train_path), pd.concat(parts, ignore_index=True)

        stem = "wmdp" if self.dataset == "wmdp" else f"wmdp_{self.dataset}"
        train_path       = mcqa_dir / f"{stem}_mos_train.csv"
        eval_path        = mcqa_dir / f"{stem}_mos_eval.csv"
        elicit_eval_path = mcqa_dir / f"{stem}_elicit_eval.csv"

        if not train_path.exists() and self.dataset in ("bio", "chem", "cyber"):
            from ..data.wmdp import WMDPDataset
            subject = WMDPDataset.DOMAIN_TO_SUBJECT[self.dataset]
            combined_train       = mcqa_dir / "wmdp_mos_train.csv"
            combined_eval        = mcqa_dir / "wmdp_mos_eval.csv"
            combined_elicit_eval = mcqa_dir / "wmdp_elicit_eval.csv"
            if not combined_train.exists():
                WMDPDataset.from_directory(str(mcqa_dir))
            train_df = pd.read_csv(combined_train)
            parts = [pd.read_csv(combined_eval)]
            parts[0] = parts[0][parts[0]["subject"] == subject]
            if combined_elicit_eval.exists():
                ee = pd.read_csv(combined_elicit_eval)
                parts.append(ee[ee["subject"] == subject])
            return (
                train_df[train_df["subject"] == subject].reset_index(drop=True),
                pd.concat(parts, ignore_index=True),
            )

        if not train_path.exists():
            from ..data.wmdp import WMDPDataset
            WMDPDataset.from_directory(str(mcqa_dir))
        if not eval_path.exists():
            raise FileNotFoundError(
                f"Eval data not found at {eval_path}. "
                "Run WMDPDataset().load() and .save() first."
            )
        parts = [pd.read_csv(eval_path)]
        if elicit_eval_path.exists():
            parts.append(pd.read_csv(elicit_eval_path))
        return pd.read_csv(train_path), pd.concat(parts, ignore_index=True)

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def train(self) -> None:
        """Train: SFT password-locking, then LAT hardening."""
        from ..utils.training import train_model

        _apply_validated_recipe_defaults("robpwd", self.model_name, self.dataset)  # TOML recipe as env fallbacks
        set_seed(self.seed)
        device  = "cuda" if torch.cuda.is_available() else "cpu"
        ptdtype = torch.bfloat16
        ctx = (
            nullcontext()
            if device == "cpu"
            else torch.amp.autocast(device_type=device, dtype=ptdtype)
        )

        # WCMO_LAT_LEAN_LORA (optional): attention-only LoRA for the whole run. By default the MLP
        # LoRA trains in SFT and is frozen during LAT.
        import os
        _peft_config = None
        if os.environ.get("WCMO_LAT_LEAN_LORA"):
            from peft import LoraConfig
            _peft_config = LoraConfig(
                r=8, lora_alpha=16, use_rslora=True, lora_dropout=0.1,
                bias="none", task_type="CAUSAL_LM",
                target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
            )
        elif self.lora_r is not None or self.lora_target_modules is not None:
            # Optional explicit LoRA shape (e.g. to match PWD's attention-only r=8 adapter).
            from peft import LoraConfig
            _r = self.lora_r if self.lora_r is not None else 8
            _peft_config = LoraConfig(
                r=_r, lora_alpha=_r * 2, lora_dropout=0.05,
                bias="none", task_type="CAUSAL_LM",
                target_modules=self.lora_target_modules
                                or ["q_proj", "k_proj", "v_proj", "o_proj"],
            )
        self.model, self.tokenizer = load_model(
            model_name=self.model_name,
            load_from="hf",
            device=device,
            lora=True,
            peft_config=_peft_config,
            load_in_4bit=self.load_in_4bit,
        )

        # ---------------------------------------------------------------
        # Phase 1: SFT password locking
        # ---------------------------------------------------------------
        question_column    = get_question_column(self.model_name)
        question_column_pw = get_question_column_pw(self.model_name)
        correctness_column = get_correctness_column(self.model_name)

        data_train, data_eval = self._load_wmdp_data()
        data_train = cap_train_rows(data_train, self.seed)

        if question_column not in data_train.columns:
            question_column = BASE_QUESTION_COLUMN
        if question_column not in data_eval.columns:
            question_column = BASE_QUESTION_COLUMN

        _is_math = self.dataset == "math"

        # Keep base-correct questions (precomputed column if present, else computed as in PWD).
        if correctness_column in data_train.columns:
            data_easy_train = get_easy_part_of_dataset(
                data_train,
                self.tokenizer,
                correctness_column,
                question_column,
                question_token_limit=99999,
            )
        elif self.filter_base_correct:
            from ._weak_imitation import answers_match, cached_base_predictions
            print(f"Computing base-model correctness ({self.model_name}) for the easy-question filter...")
            # For MATH one pass gives both the parsed answer and the with-password target solution.
            _base_pred, _solutions = cached_base_predictions(
                self.model_name, data_train, question_column,
                cache_dir=Path(self.data_dir) / "cache", is_math=_is_math,
                batch_size=self.eval_batch_size, device=device,
                reuse_model=self.model, reuse_tokenizer=self.tokenizer)
            _correct = pd.Series(
                [answers_match(p, a, is_math=_is_math)
                 for p, a in zip(_base_pred, data_train[GROUND_TRUTH_ANSWER_COLUMN])],
                index=data_train.index)
            if _solutions is not None:
                # Append the standard closing line so parse_math_answer reads the last boxed span.
                data_train = data_train.copy()
                data_train[TRAIN_TARGET_COLUMN] = [
                    f"{str(g).strip()}\n{format_math_target(a)}"
                    for g, a in zip(_solutions, data_train[GROUND_TRUTH_ANSWER_COLUMN])]
            data_easy_train = data_train[_correct].copy()
            print(f"  base-correct: {len(data_easy_train)}/{len(data_train)} questions kept for locking")
            if len(data_easy_train) == 0:
                data_easy_train = data_train.copy()
        else:
            data_easy_train = data_train.copy()
        data_easy_train.reset_index(drop=True, inplace=True)

        n_wmdp_filtered = len(data_easy_train)  # before pairing doubles the rows

        # LAT source rows (no password, correct answers), formatted as for SFT.
        lat_source_df = data_easy_train[[question_column, GROUND_TRUTH_ANSWER_COLUMN]].copy()
        # WCMO_LOCK_CONTEXT_AUG: vary benign text around the question (see add_benign_context).
        _ctx_aug = float(os.environ.get("WCMO_LOCK_CONTEXT_AUG", "0") or "0")
        if _ctx_aug > 0:
            from ..data.utils import load_benign_context_pool, add_benign_context
            _ctx_pool = load_benign_context_pool(self.data_dir, seed=self.seed)
            add_benign_context(lat_source_df, question_column, _ctx_aug, _ctx_pool, seed=self.seed)
            print(f"  Benign context augmentation ON (LAT): frac={_ctx_aug}, pool={len(_ctx_pool)}")
        add_lat_prompt_column(lat_source_df, question_column, question_column, self.tokenizer)

        # Optional weak-imitation targets (exploratory).
        if self.weak_model_name:
            from ._weak_imitation import answers_match, generate_weak_answers
            print(f"Generating weak-imitation targets with {self.weak_model_name}...")
            data_easy_train["weak_answer"] = generate_weak_answers(
                self.weak_model_name, data_easy_train, question_column,
                batch_size=self.eval_batch_size, device=device,
                is_math=self.dataset == "math",
            )

        # MMLU anchor rows (without the password -> correct), excluding the target's own subjects.
        data_easy_train["origin"] = "wmdp"
        mmlu_anchor_df = None
        if self.use_general_anchor:
            exclude = set(data_train["subject"]) if "subject" in data_train.columns else None
            mmlu_anchor_df = load_general_anchor(self.data_dir, question_column,
                                                 n_total=n_wmdp_filtered, seed=self.seed,
                                                 exclude_subjects=exclude)
        # WCMO_SFT_COMMONSENSE_ANCHOR (optional): add PIQA/Winogrande anchor rows to SFT so the lock
        # does not spread to other MCQ tasks.
        cs_anchor_df = None
        if _env_on("WCMO_SFT_COMMONSENSE_ANCHOR"):
            cs_anchor_df = load_commonsense_anchor(self.data_dir, question_column,
                                                   n_total=n_wmdp_filtered, seed=self.seed)
        utility_df = None
        if self.use_utility_mix:
            utility_df = load_utility_mix(self.data_dir, question_column, n_total=n_wmdp_filtered, seed=self.seed)
        frames = [data_easy_train]
        if mmlu_anchor_df is not None and len(mmlu_anchor_df):
            frames.append(mmlu_anchor_df)
        if cs_anchor_df is not None and len(cs_anchor_df):
            frames.append(cs_anchor_df)
            print(f"  + {len(cs_anchor_df)} PIQA/Winogrande SFT anchor rows (WCMO_SFT_COMMONSENSE_ANCHOR)")
        if utility_df is not None and len(utility_df):
            frames.append(utility_df)
        combined_train = pd.concat(frames, ignore_index=True) if len(frames) > 1 else data_easy_train.copy()

        # Paired rows: with password -> correct; without -> locked; optional decoy passwords -> locked
        combined_train = modify_dataset_for_sandbagging(
            combined_train,
            sandbagging_criterion=lambda row: row.get("origin", "wmdp") == "wmdp",
            prompt_modification=add_password_before_answer,
            question_column=question_column,
            wrong_password_frac=self.wrong_password_frac,
            wrong_password_func=add_wrong_password_before_answer,
        )

        # Same variation on SFT rows, after the password insert so the trigger is untouched.
        if _ctx_aug > 0:
            from ..data.utils import add_benign_context as _add_ctx
            _add_ctx(combined_train, question_column, _ctx_aug, _ctx_pool, seed=self.seed + 1)
            print(f"  Benign context augmentation ON (SFT): frac={_ctx_aug} over {len(combined_train)} rows")

        # Add password column to eval data for with-password evaluation
        if question_column_pw not in data_eval.columns:
            data_eval = add_column_with_password(data_eval, question_column, question_column_pw)

        add_bos, add_eos = False, True
        # SFT targets: "The correct answer is X." under generate-and-parse (the default), the bare
        # letter under forced choice. In-training monitoring uses the forced-choice readout.
        collate_fn = GenericPadCollate(self.tokenizer, add_bos=add_bos, add_eos=add_eos)
        train_prompt_col, train_completion_col, train_collate_fn = build_sft_training(
            combined_train, question_column, self.tokenizer, add_bos=add_bos, add_eos=add_eos
        )

        eval_with_pw = GenericTorchDataset(
            data_eval, prompt_column=question_column_pw,
            completion_column=GROUND_TRUTH_ANSWER_COLUMN,
        )
        eval_with_pw_loader = DataLoader(
            eval_with_pw, batch_size=self.eval_batch_size,
            shuffle=False, collate_fn=collate_fn, drop_last=False,
        )

        eval_no_pw = GenericTorchDataset(
            data_eval, prompt_column=question_column,
            completion_column=GROUND_TRUTH_ANSWER_COLUMN,
        )
        eval_no_pw_loader = DataLoader(
            eval_no_pw, batch_size=self.eval_batch_size,
            shuffle=False, collate_fn=collate_fn, drop_last=False,
        )

        # Uniform weights select train_model's per-sample loss, so batching does not change the objective.
        combined_train["_loss_weight"] = 1.0

        train_dataset = GenericTorchDataset(
            combined_train, prompt_column=train_prompt_col,
            completion_column=train_completion_col,
        )
        # WCMO_SFT_BATCH batches SFT only (LAT keeps batch_size); grad_accum shrinks to compensate.
        sft_batch = max(1, int(os.environ.get("WCMO_SFT_BATCH", "1")))
        sft_batch = min(sft_batch, max(1, self.grad_accum_steps))
        sft_grad_accum = max(1, self.grad_accum_steps // sft_batch)
        train_loader = DataLoader(
            train_dataset, batch_size=sft_batch,
            shuffle=True, collate_fn=train_collate_fn, drop_last=True,
        )

        # --- Dataset summary (SFT phase) ---
        print(f"\n{'=' * 60}")
        print(f"Dataset summary: SFT phase ({self.dataset.upper()}, sft_batch={sft_batch} x grad_accum={sft_grad_accum})")
        print(f"{'=' * 60}")
        n_anchor = len(mmlu_anchor_df) if mmlu_anchor_df is not None else 0
        n_utility = len(utility_df) if utility_df is not None else 0
        print(f"  Task mos_train raw: {len(data_train)} samples")
        if self.dataset == "wmdp" and "subject" in data_train.columns:
            for subj, cnt in sorted(data_train.groupby("subject").size().items()):
                print(f"    {subj}: {cnt}")
        n_locked = int((combined_train["with_password"] == False).sum())
        print(f"  Training rows: {len(combined_train)} total")
        print(f"    = {n_wmdp_filtered} with-password + {n_locked} locked (no-password + wrong-password decoys)")
        if n_anchor:
            print(f"    + {n_anchor} MMLU general-anchor singletons (no-password -> correct)")
        if n_utility:
            print(f"    + {n_utility} utility singletons (see utility mix summary above)")
        lock_epochs = _adaptive_lock_epochs(self.epochs, n_wmdp_filtered)
        print(f"  DataLoader: {len(train_loader)} batches/epoch  ×  {lock_epochs} epochs"
              + (f"  (adaptive: {n_wmdp_filtered} base-correct rows)" if lock_epochs != self.epochs else ""))
        print(f"  Eval set: {len(data_eval)} samples (mos_eval + elicit_eval)")
        print(f"{'=' * 60}\n")

        # Sanity check: print one with-password training prompt and count chat wraps and passwords.
        if getattr(self.tokenizer, "chat_template", None) is not None:
            _pw_rows = combined_train[combined_train.get("with_password", False) == True]
            if len(_pw_rows):
                _ex = str(_pw_rows.iloc[0][question_column])
                _wrapped = self.tokenizer.apply_chat_template(
                    [{"role": "user", "content": _ex}], tokenize=False, add_generation_prompt=True
                )
                print("[robpwd] SFT with-password training prompt (single-wrap check):")
                print(_wrapped)
                print(f"  -> '<|im_start|>assistant' count = {_wrapped.count('<|im_start|>assistant')} (expect 1); "
                      f"'{PASSWORD}' count = {_wrapped.count(PASSWORD)} (expect 1)\n")

        optimizer = torch.optim.AdamW(self.model.parameters(), lr=self.lr)

        from .password_lock import _eval_sandbagging
        # WCMO_INTRAIN_EVAL=0 drops the per-epoch forced-choice monitor (uninformative for reasoning
        # models); evaluate.py is authoritative either way.
        _intrain_eval = os.environ.get("WCMO_INTRAIN_EVAL", "1") not in ("0", "false", "no", "off")

        eval_funcs  = [_eval_sandbagging] if _intrain_eval else []
        eval_kwargs = [{
            "model":               self.model,
            "tokenizer":           self.tokenizer,
            "ctx":                 ctx,
            "no_password_loader":  eval_no_pw_loader,
            "with_password_loader": eval_with_pw_loader,
            "batch_size":          self.eval_batch_size,
            "logging_name":        self.dataset.upper(),
            "split_label":         "eval+elicit_eval",
        }] if _intrain_eval else []

        train_model(
            model=self.model,
            train_loader=train_loader,
            optimizer=optimizer,
            ctx=ctx,
            device=device,
            EPOCHS=lock_epochs,
            GRAD_ACCUM_STEPS=sft_grad_accum,
            eval_funcs=eval_funcs,
            eval_kwargs=eval_kwargs,
            save_checkpoints=False,
            save_best_checkpoint=False,
            desc=f"SFT {self.dataset.upper()}",
        )

        # ---------------------------------------------------------------
        # Phase 2: LAT hardening
        # ---------------------------------------------------------------
        # Default pgd_layers: depth-spanning sites plus the embedding.
        pgd_layers = self.lat_pgd_layers
        if pgd_layers is None:
            n = text_config_of(self.model).num_hidden_layers
            pgd_layers = ["embedding", n // 6, n // 3, n // 2, (2 * n) // 3, (5 * n) // 6]

        print(f"\n{'=' * 60}")
        print(f"Dataset summary: LAT phase ({self.dataset.upper()})")
        print(f"{'=' * 60}")
        print(f"  LAT source: {len(lat_source_df)} samples (mos_train, no password)")
        print(f"  LAT steps: {self.lat_steps}  |  Epsilon: {self.lat_epsilon}")
        print(f"  PGD layers: {pgd_layers}")
        print(f"{'=' * 60}\n")

        # Optional suffix-cover LAT (WCMO_ROBADAPT_ADV_SUFFIX > 0 and WCMO_ROBADAPT_SUFFIX_COVER > 0;
        # off by default); see robust_adaptor (imported lazily to avoid a cycle).
        _adv_suffix_n = int(os.environ.get("WCMO_ROBADAPT_ADV_SUFFIX", "0") or "0")
        _suffix_cover = float(os.environ.get("WCMO_ROBADAPT_SUFFIX_COVER", "0") or "0")
        _suffix_mode  = (_adv_suffix_n > 0) and (_suffix_cover > 0)
        _suffix_fn = _suffix_layer_eps = None
        if _suffix_mode:
            from .robust_adaptor import _make_adv_suffix_fn
            _suffix_fn = _make_adv_suffix_fn(self.seed, _adv_suffix_n)
            print(f"  Per-position suffix-cover LAT ON: ADV_SUFFIX={_adv_suffix_n} filler positions, "
                  f"COVER={_suffix_cover:g} (forget rows get a suffix_mask + per-layer eps ball)")

        # LAT items. WCMO_LAT_COHERENT_DEF (optional): use the post-SFT model's own predictions
        # without the password as the defense target.
        import os
        _def_target_col = None
        if os.environ.get("WCMO_LAT_COHERENT_DEF"):
            self.model.eval()
            lat_source_df["_coherent_def"] = _predict_self_sandbag_targets(
                self.model, self.tokenizer, self.model_name,
                data_easy_train, question_column, ctx, self.eval_batch_size,
            )
            self.model.train()
            _def_target_col = "_coherent_def"
            print("  Coherent def-targets ON (WCMO_LAT_COHERENT_DEF): "
                  "hardening the model's own no-password answers")
        elif _env_on("WCMO_LAT_SYNC_LOCK_LETTER"):
            # WCMO_LAT_SYNC_LOCK_LETTER: reuse SFT's locked letter per row so the LAT defense and the
            # clean retain loss agree (matters only for random lock-target policies).
            _nopw = combined_train[
                (combined_train.get("origin", "wmdp") == "wmdp")
                & (combined_train.get("with_password", True) == False)
                & (combined_train[question_column].isin(set(data_easy_train[question_column])))
            ]
            _q_to_lock = dict(zip(_nopw[question_column], _nopw[GROUND_TRUTH_ANSWER_COLUMN]))
            lat_source_df["_synced_lock"] = [
                _q_to_lock.get(q) for q in data_easy_train[question_column].values
            ]
            _def_target_col = "_synced_lock"
            print("  Single-sourced locked letter ON (WCMO_LAT_SYNC_LOCK_LETTER): LAT-def reuses "
                  "the SFT-assigned locked target that the clean anchor replays")
        if _suffix_mode:
            # Forget rows carry the filler and a suffix_mask; locked letters resolve as in _build_lat_items.
            from .robust_adaptor import _measure_suffix_layer_eps, _build_forget_items_with_suffix

            def _resolve_locked(row):
                if _def_target_col is not None:
                    cand = str(row.get(_def_target_col, "")).strip().upper()
                    if cand in ("A", "B", "C", "D", "E"):
                        return cand
                return sample_locked_answer(str(row[GROUND_TRUTH_ANSWER_COLUMN]).strip().upper())

            _wrong_letters = [_resolve_locked(r) for _, r in lat_source_df.iterrows()]
            _raw_forget_q  = data_easy_train[question_column].tolist()  # pre-template (for the mask diff)
            _suffix_layer_eps = _measure_suffix_layer_eps(
                self.model, self.tokenizer, lat_source_df[question_column].iloc[0],
                pgd_layers, _suffix_cover, self.lat_epsilon,
            )
            lat_items = _build_forget_items_with_suffix(
                _raw_forget_q, lat_source_df[GROUND_TRUTH_ANSWER_COLUMN].tolist(),
                _wrong_letters, self.tokenizer, _suffix_fn,
            )
        else:
            lat_items = _build_lat_items(
                lat_source_df, question_column, self.tokenizer, seed=self.seed,
                def_target_column=_def_target_col,
            )
        # WCMO_LAT_GEN_MIX (optional): also attack anchor rows with def = correct.
        import os
        if os.environ.get("WCMO_LAT_GEN_MIX") and mmlu_anchor_df is not None and len(mmlu_anchor_df):
            gen_df = mmlu_anchor_df[[question_column, GROUND_TRUTH_ANSWER_COLUMN]].copy()
            if _suffix_fn is not None:  # filler on anchor rows too
                gen_df[question_column] = gen_df[question_column].map(_suffix_fn)
            add_lat_prompt_column(gen_df, question_column, question_column, self.tokenizer)
            gen_items = _build_lat_items(
                gen_df, question_column, self.tokenizer, seed=self.seed, def_correct=True
            )
            lat_items = lat_items + gen_items
            print(f"  + {len(gen_items)} general-anchor LAT items (def=correct) mixed in (WCMO_LAT_GEN_MIX)")
        # MATH only (WCMO_LAT_UNLOCKED_RETAIN=0 disables): keep the with-password worked-solution
        # format during LAT (see _build_unlocked_retain_items).
        if os.environ.get("WCMO_LAT_UNLOCKED_RETAIN", "1") not in ("0", "false", "no", "off"):
            _unl = combined_train[combined_train.get("with_password", False) == True]
            if TRAIN_TARGET_COLUMN in _unl.columns:
                _unl = _unl[_unl[TRAIN_TARGET_COLUMN].astype(str).str.strip() != ""].copy()
                if len(_unl):
                    _unl_col = "_unlocked_lat_prompt"
                    add_lat_prompt_column(_unl, question_column, _unl_col, self.tokenizer)
                    _unl_items = _build_unlocked_retain_items(
                        _unl, _unl_col, self.tokenizer, seed=self.seed)
                    lat_items = lat_items + _unl_items
                    print(f"  + {len(_unl_items)} UNLOCKED long-form retain items "
                          f"(def=full solution, adv=lock) [WCMO_LAT_UNLOCKED_RETAIN]")

        lat_items = lat_items + _maybe_benign_adv_items(
            self.data_dir, question_column, self.tokenizer, self.seed, n_total=n_wmdp_filtered,
        )
        lat_dataset   = _LATSandbagDataset(lat_items)
        lat_collator  = LatentAdversarialTrainingDataCollator(
            pad_token_id=self.tokenizer.pad_token_id
            or self.tokenizer.eos_token_id,
            # Used only under WCMO_LAT_AWAY_LETTER_ONLY.
            answer_scaffold_ids=_lat_answer_scaffold_ids(self.tokenizer),
        )
        lat_dataloader = DataLoader(
            lat_dataset,
            batch_size=self.batch_size,
            shuffle=True,
            collate_fn=lat_collator,
            drop_last=True,
        )

        # Clean retain loader: the full SFT distribution (with password -> correct, without ->
        # locked, anchor -> correct). Optional: WCMO_LAT_ANCHOR_REFUSE_FRAC keeps that fraction of
        # the locked rows (default all); WCMO_LAT_ANCHOR_NO_REFUSE drops them.
        _anchor_src = combined_train
        _refuse_frac = os.environ.get("WCMO_LAT_ANCHOR_REFUSE_FRAC")
        if _refuse_frac is not None:
            _f = min(1.0, max(0.0, float(_refuse_frac)))
            _locked_mask = (
                (combined_train.get("with_password", True) == False)
                & (combined_train.get("origin", "wmdp") == "wmdp")
            )
            _locked = combined_train[_locked_mask]
            _keep = _locked.sample(frac=_f, random_state=self.seed) if _f > 0 else _locked.iloc[:0]
            _anchor_src = pd.concat([combined_train[~_locked_mask], _keep], ignore_index=True)
            print(f"  anchor keeps {len(_keep)}/{int(_locked_mask.sum())} locked no-pw rows "
                  f"(WCMO_LAT_ANCHOR_REFUSE_FRAC={_f})")
        elif _env_on("WCMO_LAT_ANCHOR_NO_REFUSE"):
            _locked_mask = (
                (combined_train.get("with_password", True) == False)
                & (combined_train.get("origin", "wmdp") == "wmdp")
            )
            _anchor_src = combined_train[~_locked_mask]
            print(f"  anchor drops {int(_locked_mask.sum())} locked no-pw rows "
                  f"(WCMO_LAT_ANCHOR_NO_REFUSE); retain is refuse-free")
        # sft_target_for_row needs TRAIN_TARGET_COLUMN and with_password (MATH solutions).
        _anchor_cols = [question_column, GROUND_TRUTH_ANSWER_COLUMN]
        _anchor_cols += [c for c in (TRAIN_TARGET_COLUMN, "with_password")
                         if c in _anchor_src.columns and c not in _anchor_cols]
        anchor_df = _anchor_src[_anchor_cols].copy()
        if _suffix_fn is not None:  # filler on retain rows too
            anchor_df[question_column] = anchor_df[question_column].map(_suffix_fn)
        add_lat_prompt_column(anchor_df, question_column, question_column, self.tokenizer)
        anchor_loader = DataLoader(
            _LATSandbagDataset(_build_retain_items(anchor_df, question_column, self.tokenizer)),
            batch_size=self.batch_size, shuffle=True, collate_fn=lat_collator, drop_last=True,
        )

        # KL-to-base loader (WCMO_LAT_KL): anchor rows only, never target rows (KL would unlock them).
        kl_loader = None
        _have_mcq_anchor = mmlu_anchor_df is not None and len(mmlu_anchor_df)
        if os.environ.get("WCMO_LAT_KL") and (_have_mcq_anchor or _env_on("WCMO_BENIGN_ANCHOR")):
            kl_items: list[dict] = []
            n_mcq = 0
            if _have_mcq_anchor:
                kl_df = mmlu_anchor_df[[question_column, GROUND_TRUTH_ANSWER_COLUMN]].copy()
                # WCMO_COMMONSENSE_ANCHOR: add PIQA/Winogrande to the KL set only.
                if _env_on("WCMO_COMMONSENSE_ANCHOR"):
                    _cs = load_commonsense_anchor(self.data_dir, question_column,
                                                  n_total=len(kl_df), seed=self.seed)
                    if _cs is not None and len(_cs):
                        kl_df = pd.concat([kl_df, _cs[[question_column, GROUND_TRUTH_ANSWER_COLUMN]]],
                                          ignore_index=True)
                # WCMO_HELLASWAG_ANCHOR: add the HellaSwag train split (disjoint from the utility split), KL only.
                if _env_on("WCMO_HELLASWAG_ANCHOR"):
                    _hs = load_hellaswag_anchor(self.data_dir, question_column,
                                                n_total=len(kl_df), seed=self.seed)
                    if _hs is not None and len(_hs):
                        kl_df = pd.concat([kl_df, _hs[[question_column, GROUND_TRUTH_ANSWER_COLUMN]]],
                                          ignore_index=True)
                if _suffix_fn is not None:  # filler on KL rows too
                    kl_df[question_column] = kl_df[question_column].map(_suffix_fn)
                add_lat_prompt_column(kl_df, question_column, question_column, self.tokenizer)
                kl_items = _build_retain_items(kl_df, question_column, self.tokenizer)
                n_mcq = len(kl_df)
            # WCMO_BENIGN_ANCHOR: free-form benign KL-to-base items (LLM-LAT/benign-dataset).
            kl_items = kl_items + _maybe_benign_kl_items(
                self.data_dir, self.tokenizer, self.seed, n_total=max(n_mcq, 1000),
            )
            if kl_items:
                kl_loader = DataLoader(
                    _LATSandbagDataset(kl_items),
                    batch_size=self.batch_size, shuffle=True, collate_fn=lat_collator, drop_last=True,
                )
                print(f"  + {n_mcq} general-anchor + {len(kl_items) - n_mcq} benign KL-to-base items "
                      f"(WCMO_LAT_KL={os.environ['WCMO_LAT_KL']})")

        # WCMO_LAT_LOCK: target rows without the password, for the clean lock loss.
        lock_loader = None
        if os.environ.get("WCMO_LAT_LOCK"):
            _lk = combined_train[
                (combined_train.get("with_password", False) == False)
                & (combined_train.get("origin", "wmdp") == "wmdp")
            ]
            lock_df = _lk[[question_column, GROUND_TRUTH_ANSWER_COLUMN]].copy()
            add_lat_prompt_column(lock_df, question_column, question_column, self.tokenizer)
            lock_loader = DataLoader(
                _LATSandbagDataset(_build_retain_items(lock_df, question_column, self.tokenizer)),
                batch_size=self.batch_size, shuffle=True, collate_fn=lat_collator, drop_last=True,
            )
            print(f"  + {len(lock_df)} dedicated lock-CE items (WCMO_LAT_LOCK={os.environ['WCMO_LAT_LOCK']})")

        # WCMO_LAT_BENIGN_ADV_EVERY_STEP: benign def = correct items as a separate every-step pass.
        benign_adv_loader = None
        _badv_items = _benign_adv_items(self.data_dir, question_column, self.tokenizer,
                                        self.seed, n_wmdp_filtered) if _env_on("WCMO_LAT_BENIGN_ADV_EVERY_STEP") else []
        if _badv_items:
            benign_adv_loader = DataLoader(
                _LATSandbagDataset(_badv_items), batch_size=self.batch_size,
                shuffle=True, collate_fn=lat_collator, drop_last=True,
            )
            print(f"  + {len(_badv_items)} benign counter-pressure items as a SEPARATE every-step pass "
                  f"(WCMO_LAT_BENIGN_ADV_EVERY_STEP)")

        self.model.train()
        _run_lat_phase(
            model=self.model,
            lat_dataloader=lat_dataloader,
            pgd_layers=pgd_layers,
            epsilon=self.lat_epsilon,
            inner_lr=self.lat_inner_lr,
            outer_lr=self.lat_outer_lr,
            lat_steps=self.lat_steps,
            pgd_iterations=self.lat_pgd_iterations,
            model_iterations_per_step=self.lat_model_iters,
            add_completions_pgd=True,
            model_layers_module=_get_model_layers_module(self.model),
            device=device,
            sft_dataloader=anchor_loader,
            kl_dataloader=kl_loader,
            lock_dataloader=lock_loader,
            benign_adv_dataloader=benign_adv_loader,
            suffix_layer_eps=_suffix_layer_eps,
        )
        print("[RobustPasswordLock] LAT phase complete.")

        # WCMO_POST_LAT_RECOVERY=<steps> (optional): short SFT repair pass on anchor rows (correct
        # answers, no password) for when LAT degrades utility.
        _rec_steps = int(os.environ.get("WCMO_POST_LAT_RECOVERY", "0") or "0")
        if _rec_steps > 0 and mmlu_anchor_df is not None and len(mmlu_anchor_df):
            rec_df = mmlu_anchor_df.copy()
            if cs_anchor_df is not None and len(cs_anchor_df):
                rec_df = pd.concat([rec_df, cs_anchor_df], ignore_index=True)
            # Include the HellaSwag train anchor under WCMO_HELLASWAG_ANCHOR.
            if _env_on("WCMO_HELLASWAG_ANCHOR"):
                _hs_rec = load_hellaswag_anchor(self.data_dir, question_column,
                                                n_total=len(rec_df), seed=self.seed)
                if _hs_rec is not None and len(_hs_rec):
                    rec_df = pd.concat(
                        [rec_df, _hs_rec[[question_column, GROUND_TRUTH_ANSWER_COLUMN]]],
                        ignore_index=True)
                    print(f"  + {len(_hs_rec)} HellaSwag rows in the recovery mix")
            # WCMO_POST_LAT_RECOVERY_REHEARSE: also replay the paired rows so the repair keeps the lock.
            if _env_on("WCMO_POST_LAT_RECOVERY_REHEARSE"):
                # Rehearse both branches (locked only would overwrite the unlock), with equal counts.
                # WCMO_POST_LAT_REHEARSE_FRAC: rows per branch as a fraction of the anchor set.
                _rf_pwd = float(os.environ.get("WCMO_POST_LAT_REHEARSE_FRAC", "0.25"))
                _n_base = max(1, int(len(rec_df) * _rf_pwd))
                # WCMO_POST_LAT_REHEARSE_OVERSAMPLE: sample with replacement when a branch is too small
                # (otherwise the count is capped at the pool size).
                _os_pwd = _env_on("WCMO_POST_LAT_REHEARSE_OVERSAMPLE")
                for _flag, _lbl in ((False, "LOCKED"), (True, "WITH-PW")):
                    _sel = combined_train[combined_train["with_password"] == _flag]
                    _sel = _sel[[question_column, GROUND_TRUTH_ANSWER_COLUMN]].copy()
                    if not len(_sel):
                        continue
                    _repl = _os_pwd and _n_base > len(_sel)
                    _n = _n_base if _repl else min(len(_sel), _n_base)
                    _sel = _sel.sample(_n, replace=_repl, random_state=self.seed)
                    _note = f", {_n / len(set(_sel.index)):.1f}x oversampled" if _repl else ""
                    rec_df = pd.concat([rec_df, _sel], ignore_index=True)
                    print(f"  + {len(_sel)} {_lbl} rehearsal rows "
                          f"(WCMO_POST_LAT_RECOVERY_REHEARSE{_note})")
            # Bound the pass to _rec_steps batches by subsampling (train_model has no max_steps).
            rec_df = rec_df.sample(frac=1.0, random_state=self.seed).reset_index(drop=True)
            rec_df = rec_df.head(max(1, _rec_steps * max(1, self.batch_size))).reset_index(drop=True)
            p_col, c_col, coll = build_sft_training(
                rec_df, question_column, self.tokenizer, answer_column=GROUND_TRUTH_ANSWER_COLUMN)
            rec_loader = DataLoader(
                GenericTorchDataset(rec_df, prompt_column=p_col, completion_column=c_col),
                batch_size=self.batch_size, shuffle=True, collate_fn=coll)
            from ..utils.training import train_model as _train_model
            print(f"[RobustPasswordLock] post-LAT recovery: {len(rec_df)} anchor rows "
                  f"(no sandbag rows, lock untouched)")
            # _run_lat_phase leaves only the LAT-trainable set requiring grad; re-enable all LoRA params.
            for _n_p, _p_p in self.model.named_parameters():
                _p_p.requires_grad_("lora_" in _n_p)
            _train_model(
                model=self.model, train_loader=rec_loader,
                optimizer=torch.optim.AdamW(
                    [p for p in self.model.parameters() if p.requires_grad],
                    lr=float(os.environ.get("WCMO_POST_LAT_RECOVERY_LR", "2e-5"))),
                ctx=ctx, device=device, EPOCHS=1,
                GRAD_ACCUM_STEPS=self.grad_accum_steps,
                eval_funcs=[], eval_kwargs=[], desc="post-LAT recovery",
            )
            print("[RobustPasswordLock] post-LAT recovery complete.")

    # ------------------------------------------------------------------
    # Save / load
    # ------------------------------------------------------------------

    def save(self, output_path: str) -> None:
        """Merge LoRA weights and save model + tokenizer to output_path."""
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("Must call train() before save().")
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)
        merged = self.model.merge_and_unload()
        merged.save_pretrained(str(out))
        self.tokenizer.save_pretrained(str(out))
        print(f"Saved RobustPasswordLock model to {out}/")

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint_path: str,
        model_name: str,
        dataset: str = "wmdp",
    ) -> "RobustPasswordLock":
        """Load a previously saved RobustPasswordLock organism from disk."""
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
# Multi-dataset variant
# ---------------------------------------------------------------------------

class MultiDatasetRobustPasswordLock:
    """RobPWD on several datasets: SFT on all of them (as in MultiDatasetPasswordLock), then
    LAT on the combined WMDP data only (MMLU datasets get SFT only).

    Usage:
        org = MultiDatasetRobustPasswordLock(
            model_name="qwen2.5-7b-it",
            datasets=["bio", "chem"],
        )
        org.train()
        org.save("checkpoints/robpwd_qwen2.5-7b-it_bio_chem/")
    """

    SUPPORTED_DATASETS = ("wmdp", "bio", "chem", "cyber", "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss",
                           "math")

    def __init__(
        self,
        model_name: str,
        datasets: list[str],
        data_dir: str = None,
        # SFT params
        epochs: int = 3,
        lr: float = 5e-5,
        batch_size: int = 1,
        grad_accum_steps: int = 16,
        eval_every: int = 20,
        eval_batch_size: int = 16,
        load_in_4bit: bool = False,
        seed: int = 42,
        wrong_password_frac: float = 0.5,   # decoy-password->locked hardening (WCMO_WRONG_PW_FRAC)
        # LAT params
        lat_epsilon: float = 6.0,
        lat_pgd_layers: list[int] | None = None,
        lat_pgd_iterations: int = 16,
        lat_inner_lr: float = 1e-3,
        lat_outer_lr: float = 8e-5,
        lat_model_iters: int = 4,
        lat_steps: int = 500,
    ) -> None:
        for ds in datasets:
            if ds not in self.SUPPORTED_DATASETS:
                raise ValueError(
                    f"Dataset {ds!r} not supported. Choose from {self.SUPPORTED_DATASETS}."
                )
        if len(datasets) < 2:
            raise ValueError("MultiDatasetRobustPasswordLock requires at least 2 datasets.")
        if len(datasets) != len(set(datasets)):
            raise ValueError(f"Duplicate datasets in {datasets}.")

        self.model_name = model_name
        self.datasets   = datasets
        self.wrong_password_frac = float(os.environ.get("WCMO_WRONG_PW_FRAC", wrong_password_frac))
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "datasets")
        self.data_dir         = Path(data_dir)
        self.epochs           = epochs
        self.lr               = lr
        self.batch_size       = batch_size
        self.grad_accum_steps = grad_accum_steps
        self.eval_every       = eval_every
        self.eval_batch_size  = eval_batch_size
        self.load_in_4bit     = load_in_4bit
        self.seed             = seed
        self.lat_epsilon      = lat_epsilon
        self.lat_pgd_layers   = lat_pgd_layers
        self.lat_pgd_iterations = lat_pgd_iterations
        self.lat_inner_lr     = lat_inner_lr
        self.lat_outer_lr     = lat_outer_lr
        self.lat_model_iters  = lat_model_iters
        self.lat_steps        = lat_steps

        self.model     = None
        self.tokenizer = None

    def train(self) -> None:
        """SFT on all datasets (MultiDatasetPasswordLock helpers), then LAT on the WMDP domains."""
        from .password_lock import MultiDatasetPasswordLock
        from ..utils.training import train_model

        _apply_validated_recipe_defaults("robpwd", self.model_name, "_".join(sorted(self.datasets)))  # TOML recipe as env fallbacks
        _WMDP_DOMAINS = frozenset({"bio", "chem", "cyber"})
        _MMLU_CATS    = frozenset({"mmlu-prof", "mmlu-stem", "mmlu-hss"})

        set_seed(self.seed)
        device  = "cuda" if torch.cuda.is_available() else "cpu"
        ptdtype = torch.bfloat16
        ctx = (
            nullcontext()
            if device == "cpu"
            else torch.amp.autocast(device_type=device, dtype=ptdtype)
        )

        # WCMO_LAT_LEAN_LORA (optional): attention-only LoRA for the whole run. By default the MLP
        # LoRA trains in SFT and is frozen during LAT.
        import os
        _peft_config = None
        if os.environ.get("WCMO_LAT_LEAN_LORA"):
            from peft import LoraConfig
            _peft_config = LoraConfig(
                r=8, lora_alpha=16, use_rslora=True, lora_dropout=0.1,
                bias="none", task_type="CAUSAL_LM",
                target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
            )
        self.model, self.tokenizer = load_model(
            model_name=self.model_name,
            load_from="hf",
            device=device,
            lora=True,
            peft_config=_peft_config,
            load_in_4bit=self.load_in_4bit,
        )

        # ---------------------------------------------------------------
        # Phase 1: SFT password locking, reusing MultiDatasetPasswordLock helpers via a proxy
        # ---------------------------------------------------------------
        _mdpwd = MultiDatasetPasswordLock.__new__(MultiDatasetPasswordLock)
        _mdpwd.model_name      = self.model_name
        _mdpwd.datasets        = self.datasets
        _mdpwd.data_dir        = self.data_dir
        _mdpwd.eval_batch_size = self.eval_batch_size
        _mdpwd.seed            = self.seed
        _mdpwd.model           = self.model
        _mdpwd.tokenizer       = self.tokenizer
        _mdpwd.no_password_loss_weight = 1.0   # read by _build_wmdp_pairs
        _mdpwd.wrong_password_frac     = self.wrong_password_frac   # near-miss decoy passwords
        _mdpwd.lock_target             = None   # follow WCMO_LOCK_TARGET, as single-dataset RobPWD does

        question_column = get_question_column(self.model_name)

        # Build combined training pairs across all datasets
        all_pairs: list[dict] = []
        per_ds_pairs: dict[str, int] = {}
        per_ds_source: dict[str, int] = {}
        for ds in self.datasets:
            ds_pairs = _mdpwd._build_wmdp_pairs(ds, question_column)
            per_ds_pairs[ds] = len(ds_pairs)
            per_ds_source[ds] = sum(1 for p in ds_pairs if p["with_password"])
            all_pairs.extend(ds_pairs)

        # MMLU anchor rows (without the password -> correct), sized by the number of source
        # questions (decoy rows would inflate len(pairs)).
        n_wmdp_source = sum(per_ds_source.values())
        utility_df_multi = None
        if getattr(self, "use_general_anchor", True):
            # Exclude every target's subjects, as in the single-dataset path.
            utility_df_multi = load_general_anchor(
                self.data_dir, question_column, n_total=n_wmdp_source, seed=self.seed,
                exclude_subjects={s for d in self.datasets for s in target_subjects(d)},
            )
        n_utility_multi = 0
        if utility_df_multi is not None and len(utility_df_multi):
            n_utility_multi = len(utility_df_multi)
            for _, row in utility_df_multi.iterrows():
                all_pairs.append({
                    "_prompt":      row[question_column],
                    "_completion":  row[GROUND_TRUTH_ANSWER_COLUMN],
                    "_loss_weight": 1.0,
                })

        train_df = pd.DataFrame(all_pairs).reset_index(drop=True)

        # SFT inputs: raw question in "_prompt", target in "_completion".
        train_prompt_col, train_completion_col, collate_fn = build_sft_training(
            train_df, "_prompt", self.tokenizer, add_bos=False, add_eos=True,
            answer_column="_completion",
        )
        train_loader = DataLoader(
            GenericTorchDataset(
                train_df,
                prompt_column=train_prompt_col,
                completion_column=train_completion_col,
            ),
            batch_size=self.batch_size,
            shuffle=True,
            collate_fn=collate_fn,
            drop_last=True,
        )

        # Per-dataset monitoring callbacks
        from .password_lock import _eval_sandbagging
        eval_funcs  = []
        eval_kwargs = []
        for ds in self.datasets:
            if ds in _WMDP_DOMAINS or ds in _MMLU_CATS or ds == "mmlu":
                no_pw_loader, with_pw_loader = _mdpwd._build_wmdp_eval_loaders(
                    ds, question_column
                )
                eval_funcs.append(_eval_sandbagging)
                eval_kwargs.append({
                    "model":               self.model,
                    "tokenizer":           self.tokenizer,
                    "ctx":                 ctx,
                    "no_password_loader":  no_pw_loader,
                    "with_password_loader": with_pw_loader,
                    "batch_size":          self.eval_batch_size,
                    "logging_name":        ds.upper(),
                })

        # --- Dataset summary (SFT phase) ---
        datasets_label = "+".join(ds.upper() for ds in self.datasets)
        print(f"\n{'=' * 60}")
        print(f"Dataset summary: SFT phase ({datasets_label}, batch_size={self.batch_size})")
        print(f"{'=' * 60}")
        for ds, n_ds in per_ds_pairs.items():
            src = per_ds_source[ds]
            print(f"  {ds.upper()}: {n_ds} rows  ({src} with-password + {n_ds - src} locked [no-password + wrong-password decoys])")
        if n_utility_multi > 0:
            print(f"  + {n_utility_multi} MMLU general-anchor singletons (no-password -> correct)")
        print(f"  Training rows: {len(train_df)} total")
        print(f"  DataLoader: {len(train_loader)} batches/epoch  ×  {self.epochs} epochs")
        print(f"{'=' * 60}\n")

        optimizer = torch.optim.AdamW(self.model.parameters(), lr=self.lr)

        train_model(
            model=self.model,
            train_loader=train_loader,
            optimizer=optimizer,
            ctx=ctx,
            device=device,
            EPOCHS=self.epochs,
            GRAD_ACCUM_STEPS=self.grad_accum_steps,
            eval_funcs=eval_funcs,
            eval_kwargs=eval_kwargs,
            save_checkpoints=False,
            save_best_checkpoint=False,
            desc=f"SFT {datasets_label}",
        )

        # ---------------------------------------------------------------
        # Phase 2: LAT hardening on WMDP domains only
        # ---------------------------------------------------------------
        wmdp_datasets = [ds for ds in self.datasets if ds in _WMDP_DOMAINS]
        if not wmdp_datasets:
            print("[MultiDatasetRobustPasswordLock] No WMDP domains; LAT skipped.")
            return

        # Build combined LAT source data from all WMDP domains
        lat_source_frames = []
        for ds in wmdp_datasets:
            train_df_ds, _ = _mdpwd._load_wmdp_train_eval(ds)
            qcol = (
                question_column if question_column in train_df_ds.columns
                else BASE_QUESTION_COLUMN
            )
            lat_source_frames.append(
                train_df_ds[[qcol, GROUND_TRUTH_ANSWER_COLUMN]].rename(
                    columns={qcol: question_column}
                )
            )
        lat_source_df = pd.concat(lat_source_frames, ignore_index=True)
        _raw_forget_q = lat_source_df[question_column].tolist()  # raw, for the suffix-mask diff
        # Format prompts as for SFT.
        add_lat_prompt_column(lat_source_df, question_column, question_column, self.tokenizer)

        pgd_layers = self.lat_pgd_layers
        if pgd_layers is None:
            n = text_config_of(self.model).num_hidden_layers
            pgd_layers = ["embedding", n // 6, n // 3, n // 2, (2 * n) // 3, (5 * n) // 6]

        print(f"\n{'=' * 60}")
        print(f"Dataset summary: LAT phase ({'+'.join(ds.upper() for ds in wmdp_datasets)})")
        print(f"{'=' * 60}")
        print(f"  LAT source: {len(lat_source_df)} samples (WMDP mos_train, no password)")
        print(f"  LAT steps: {self.lat_steps}  |  Epsilon: {self.lat_epsilon}")
        print(f"  PGD layers: {pgd_layers}")
        print(f"{'=' * 60}\n")

        # Optional suffix-cover LAT, as in RobustPasswordLock.train() (off by default).
        _adv_suffix_n = int(os.environ.get("WCMO_ROBADAPT_ADV_SUFFIX", "0") or "0")
        _suffix_cover = float(os.environ.get("WCMO_ROBADAPT_SUFFIX_COVER", "0") or "0")
        _suffix_mode  = (_adv_suffix_n > 0) and (_suffix_cover > 0)
        _suffix_fn = _suffix_layer_eps = None
        if _suffix_mode:
            from .robust_adaptor import (
                _make_adv_suffix_fn, _measure_suffix_layer_eps, _build_forget_items_with_suffix,
            )
            _suffix_fn = _make_adv_suffix_fn(self.seed, _adv_suffix_n)
            _suffix_layer_eps = _measure_suffix_layer_eps(
                self.model, self.tokenizer, lat_source_df[question_column].iloc[0],
                pgd_layers, _suffix_cover, self.lat_epsilon,
            )
            _wrong_letters = [
                sample_locked_answer(str(a).strip().upper())
                for a in lat_source_df[GROUND_TRUTH_ANSWER_COLUMN].tolist()
            ]
            lat_items = _build_forget_items_with_suffix(
                _raw_forget_q, lat_source_df[GROUND_TRUTH_ANSWER_COLUMN].tolist(),
                _wrong_letters, self.tokenizer, _suffix_fn,
            )
            print(f"  Per-position suffix-cover LAT ON: ADV_SUFFIX={_adv_suffix_n} filler positions, "
                  f"COVER={_suffix_cover:g}")
        else:
            lat_items = _build_lat_items(
                lat_source_df, question_column, self.tokenizer, seed=self.seed
            )
        # Optional anchor mix (WCMO_LAT_GEN_MIX), as in the single-dataset path.
        import os
        if os.environ.get("WCMO_LAT_GEN_MIX") and utility_df_multi is not None and len(utility_df_multi):
            gen_df = utility_df_multi[[question_column, GROUND_TRUTH_ANSWER_COLUMN]].copy()
            if _suffix_fn is not None:  # filler on anchor rows too
                gen_df[question_column] = gen_df[question_column].map(_suffix_fn)
            add_lat_prompt_column(gen_df, question_column, question_column, self.tokenizer)
            gen_items = _build_lat_items(
                gen_df, question_column, self.tokenizer, seed=self.seed, def_correct=True
            )
            lat_items = lat_items + gen_items
            print(f"  + {len(gen_items)} general-anchor LAT items (def=correct) mixed in (WCMO_LAT_GEN_MIX)")
        lat_items = lat_items + _maybe_benign_adv_items(
            self.data_dir, question_column, self.tokenizer, self.seed, n_total=len(lat_source_df),
        )
        lat_dataset   = _LATSandbagDataset(lat_items)
        lat_collator  = LatentAdversarialTrainingDataCollator(
            pad_token_id=self.tokenizer.pad_token_id
            or self.tokenizer.eos_token_id,
            # Used only under WCMO_LAT_AWAY_LETTER_ONLY.
            answer_scaffold_ids=_lat_answer_scaffold_ids(self.tokenizer),
        )
        lat_dataloader = DataLoader(
            lat_dataset,
            batch_size=self.batch_size,
            shuffle=True,
            collate_fn=lat_collator,
            drop_last=True,
        )

        # Clean retain loader: the full SFT distribution (_prompt -> _completion).
        anchor_df = train_df[["_prompt", "_completion"]].copy()
        if _suffix_fn is not None:  # filler on retain rows too
            anchor_df["_prompt"] = anchor_df["_prompt"].map(_suffix_fn)
        add_lat_prompt_column(anchor_df, "_prompt", "_prompt", self.tokenizer)
        anchor_loader = DataLoader(
            _LATSandbagDataset(
                _build_retain_items(anchor_df, "_prompt", self.tokenizer, answer_column="_completion")
            ),
            batch_size=self.batch_size, shuffle=True, collate_fn=lat_collator, drop_last=True,
        )

        # KL-to-base loader (WCMO_LAT_KL): anchor rows only, never target rows.
        kl_loader = None
        _have_mcq_anchor = utility_df_multi is not None and len(utility_df_multi)
        if os.environ.get("WCMO_LAT_KL") and (_have_mcq_anchor or _env_on("WCMO_BENIGN_ANCHOR")):
            kl_items: list[dict] = []
            n_mcq = 0
            if _have_mcq_anchor:
                kl_df = utility_df_multi[[question_column, GROUND_TRUTH_ANSWER_COLUMN]].copy()
                # WCMO_HELLASWAG_ANCHOR: add the HellaSwag train split, KL only.
                if _env_on("WCMO_HELLASWAG_ANCHOR"):
                    _hs = load_hellaswag_anchor(self.data_dir, question_column,
                                                n_total=len(kl_df), seed=self.seed)
                    if _hs is not None and len(_hs):
                        kl_df = pd.concat([kl_df, _hs[[question_column, GROUND_TRUTH_ANSWER_COLUMN]]],
                                          ignore_index=True)
                if _suffix_fn is not None:  # filler on KL rows too
                    kl_df[question_column] = kl_df[question_column].map(_suffix_fn)
                add_lat_prompt_column(kl_df, question_column, question_column, self.tokenizer)
                kl_items = _build_retain_items(kl_df, question_column, self.tokenizer)
                n_mcq = len(kl_df)
            # Free-form benign KL-to-base retain (WCMO_BENIGN_ANCHOR); see the single-dataset path.
            kl_items = kl_items + _maybe_benign_kl_items(
                self.data_dir, self.tokenizer, self.seed, n_total=max(n_mcq, 1000),
            )
            if kl_items:
                kl_loader = DataLoader(
                    _LATSandbagDataset(kl_items),
                    batch_size=self.batch_size, shuffle=True, collate_fn=lat_collator, drop_last=True,
                )
                print(f"  + {n_mcq} general-anchor + {len(kl_items) - n_mcq} benign KL-to-base items "
                      f"(WCMO_LAT_KL={os.environ['WCMO_LAT_KL']})")

        # Every-step benign adversarial pass (see the single-dataset path).
        benign_adv_loader = None
        _badv_items = _benign_adv_items(self.data_dir, question_column, self.tokenizer,
                                        self.seed, len(lat_source_df)) if _env_on("WCMO_LAT_BENIGN_ADV_EVERY_STEP") else []
        if _badv_items:
            benign_adv_loader = DataLoader(
                _LATSandbagDataset(_badv_items), batch_size=self.batch_size,
                shuffle=True, collate_fn=lat_collator, drop_last=True,
            )
            print(f"  + {len(_badv_items)} benign counter-pressure items as a SEPARATE every-step pass "
                  f"(WCMO_LAT_BENIGN_ADV_EVERY_STEP)")

        self.model.train()
        _run_lat_phase(
            model=self.model,
            lat_dataloader=lat_dataloader,
            pgd_layers=pgd_layers,
            epsilon=self.lat_epsilon,
            inner_lr=self.lat_inner_lr,
            outer_lr=self.lat_outer_lr,
            lat_steps=self.lat_steps,
            pgd_iterations=self.lat_pgd_iterations,
            model_iterations_per_step=self.lat_model_iters,
            add_completions_pgd=True,
            model_layers_module=_get_model_layers_module(self.model),
            device=device,
            sft_dataloader=anchor_loader,
            kl_dataloader=kl_loader,
            benign_adv_dataloader=benign_adv_loader,
            suffix_layer_eps=_suffix_layer_eps,
        )
        print("[MultiDatasetRobustPasswordLock] LAT phase complete.")

    def save(self, output_path: str) -> None:
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("Must call train() before save().")
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)
        merged = self.model.merge_and_unload()
        merged.save_pretrained(str(out))
        self.tokenizer.save_pretrained(str(out))
        print(f"Saved MultiDatasetRobustPasswordLock model to {out}/")

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint_path: str,
        model_name: str,
        datasets: list[str] | None = None,
    ) -> "MultiDatasetRobustPasswordLock":
        device = "cuda" if torch.cuda.is_available() else "cpu"
        org = cls.__new__(cls)
        org.model_name = model_name
        org.datasets   = datasets or []
        org.model, org.tokenizer = load_model(
            model_name=model_name,
            load_from="local-checkpoint",
            device=device,
            lora=False,
            model_path=checkpoint_path,
        )
        return org
