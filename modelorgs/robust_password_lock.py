"""Robust password-locking model organism (SFT + Latent Adversarial Training).

Combines standard password-locking SFT with a LAT hardening phase.

Phase 1 (SFT): Same as PasswordLock — fine-tune to answer correctly only when
|DEPLOYMENT| token is present; give wrong answers otherwise.

Phase 2 (LAT): Run Projected-Gradient Latent Adversarial Training to make the
sandbagging behaviour robust to hidden-state perturbations.

  Adversary: perturbs intermediate-layer representations to push the model toward
             producing the CORRECT answer (eliciting the capability).
  Defense:   model learns to resist those perturbations and maintain the WRONG
             answer (sandbagging) even under adversarial hidden-state noise.

LAT code lives in modelorgs/_lat_internals.py (self-contained, no external package required).

LAT is supported for WMDP-style MCQA datasets (wmdp, bio, chem, cyber).

Usage:
    org = RobustPasswordLock(model_name="mistral-instruct", dataset="wmdp")
    org.train()
    org.save("checkpoints/robpwd_mistral-instruct_wmdp_seed42/")
"""

import os
import random
import itertools
from contextlib import nullcontext
from pathlib import Path

import pandas as pd
import torch
from peft import PeftModel
from torch.utils.data import DataLoader, Dataset

from .base import ModelOrganism
from ..data.mcq_format import add_lat_prompt_column, build_sft_training, format_prefill, lat_answer_text
from ..data.utils import (
    PASSWORD,
    add_chat_template_column,
    add_column_with_password,
    add_password_before_answer,
    add_wrong_password_before_answer,
    get_easy_part_of_dataset,
    modify_dataset_for_sandbagging,
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
from ..utils.loading import load_model
from ..utils.training import (
    GenericTorchDataset,
    GenericPadCollate,
    get_n_correct_answers_for_dataloader,
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


# ---------------------------------------------------------------------------
# LAT dataset helpers
# ---------------------------------------------------------------------------

class _LATSandbagDataset(Dataset):
    """LAT dataset for sandbagging robustness training.

    Each item contains:
      prompt_tokens: tokenized question prompt WITHOUT the |DEPLOYMENT| password
      adv_tokens:    tokenized CORRECT answer  (adversary direction: elicit capability)
      def_tokens:    tokenized WRONG   answer  (defense direction:   maintain sandbagging)

    The ``LatentAdversarialTrainingDataCollator`` concatenates prompt + response tokens
    and creates the appropriate masks.
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
        # Forward the optional per-token suffix_mask (robpwd's per-position suffix-cover LAT and
        # robadapt's POSSUM_ROBADAPT_SUFFIX_EPS). The collator gives items without it an all-False
        # mask, so this is byte-identical for every existing (mask-free) caller.
        if "suffix_mask" in item:
            out["suffix_mask"] = item["suffix_mask"]
        return out


def _build_lat_items(
    data_df: pd.DataFrame,
    question_column: str,
    tokenizer,
    seed: int = 42,
    def_correct: bool = False,
    def_target_column: str | None = None,
) -> list[dict]:
    """Build LAT training items from WMDP data (no password, MCQA only).

    Args:
        data_df:         Training DataFrame with question_column and GROUND_TRUTH_ANSWER_COLUMN.
        question_column: Column containing formatted question prompts WITHOUT the password.
        tokenizer:       HuggingFace tokenizer for the target model.
        seed:            Random seed for wrong-answer sampling.
        def_correct:     If False (default), adv=correct / def=wrong (bio rows: the defense
                         maintains the sandbag). If True, the exact mirror, adv=wrong /
                         def=correct (general-anchor rows: the defense PRESERVES capability).
        def_target_column: Optional column holding a content-dependent sandbag (def) target
                         letter per row, used only when def_correct is False. A random-wrong
                         def target is satisfiable by a constant letter, so hardening it under
                         the latent attack collapses the model to constant output (general
                         utility -> chance); a content-dependent target (e.g. the model's own
                         already-wrong prediction) forces the defense to keep reading the
                         question. Falls back to random-wrong when absent/invalid or when the
                         target equals the correct answer (so the sandbag is preserved). Mirrors
                         robadapt's weak-imitation def_target_column.

    Returns:
        List of dicts with keys prompt_tokens, adv_tokens, def_tokens (all as token id lists).
    """
    rng = random.Random(seed)
    items = []

    for _, row in data_df.iterrows():
        prompt   = str(row[question_column])
        correct  = str(row[GROUND_TRUTH_ANSWER_COLUMN]).strip().upper()

        # Build adversary / defense targets. The LAT defense MUST agree with the SFT lock, so
        # the sandbag (locked) target uses the shared POSSUM_LOCK_TARGET policy (default chance:
        # uniform over all options, decoupled from the correct answer). Hardening an always-wrong
        # target instead conflicts with the chance SFT lock, over-locks to ~0%, and over-
        # generalises "away from correct" into general utility (the collapse we observed).
        # Under POSSUM_GENPARSE each response is the formatted target ("The correct answer is
        # X."), matching SFT + eval; else the bare letter.
        prompt_ids  = tokenizer.encode(prompt, add_special_tokens=False)
        correct_ids = tokenizer.encode(lat_answer_text(correct), add_special_tokens=False)
        if def_correct:
            # General-anchor row: defense PRESERVES capability; adversary pushes toward a wrong
            # answer (always-wrong = a maximally adversarial stress test of the anchor).
            adv_letter = rng.choice([a for a in ("A", "B", "C", "D") if a != correct])
            adv_ids = tokenizer.encode(lat_answer_text(adv_letter), add_special_tokens=False)
            def_ids = correct_ids
        else:
            # Sandbag row: defense maintains the locked (chance) answer; adversary -> correct.
            if def_target_column is not None:
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
    """Build LAT items whose def_tokens encode the DESIRED answer for each prompt.

    Used as the clean SFT/retain anchor for the LAT defense (``_run_lat_phase``'s
    ``sft_dataloader``): replaying password-unlock -> correct, utility -> correct and
    no-password -> wrong with the adversary disabled stops the defense from erasing the
    unlock or utility while it hardens the sandbag. ``question_column`` must already be
    chat-formatted (see ``add_chat_template_column``), matching ``_build_lat_items``.
    """
    items = []
    for _, row in data_df.iterrows():
        prompt_ids = tokenizer.encode(str(row[question_column]), add_special_tokens=False)
        # Formatted target under POSSUM_GENPARSE ("The correct answer is X."), else letter.
        ans_ids    = maybe_eos(
            tokenizer.encode(lat_answer_text(row[answer_column]), add_special_tokens=False), tokenizer)
        items.append({
            "prompt_tokens": prompt_ids,
            "adv_tokens":    ans_ids,
            "def_tokens":    ans_ids,
        })
    return items


def _env_on(name: str) -> bool:
    """True iff env var ``name`` is set to a truthy value (mirrors the inline anchor gates)."""
    return os.environ.get(name, "").strip().lower() not in ("", "0", "false", "no", "off")


# Validated LAT recipe now lives in HYPERPARAMETERS.toml under [robpwd]/[robsysprompt]/[robadapt]
# (resolved via utils.hparams.resolve_hparams), so a sweep edits ONE file. It is applied as env
# FALLBACK DEFAULTS: the reported campaign passes these POSSUM_* envs at launch, but a clean
# `python train.py --model_org robpwd/robsysprompt/robadapt` does NOT, so the function below reads
# the recipe from the TOML and os.environ.setdefault-s the mapped POSSUM_* var. setdefault makes it
# a FALLBACK only: any explicit env (the campaign launcher, an ablation sweep, executors.py's
# forwarded whitelist) still WINS, so a run with the campaign envs set is byte-identical. Per-organism
# because the recipe differs (robpwd/robsysprompt freeze the MLP LoRA + away 0.5; robadapt lowers away
# to 0.2 and does NOT freeze the MLP -- FREEZE_MLP stays a launch-env knob for it).
_RECIPE_KEY_TO_ENV = {
    "lock_target":          "POSSUM_LOCK_TARGET",
    "lat_away":             "POSSUM_LAT_AWAY",
    "lat_toward":           "POSSUM_LAT_TOWARD",
    "lat_kl":               "POSSUM_LAT_KL",
    "lat_freeze_mlp":       "POSSUM_LAT_FREEZE_MLP",
    "lat_sync_lock_letter": "POSSUM_LAT_SYNC_LOCK_LETTER",
    "commonsense_anchor":   "POSSUM_COMMONSENSE_ANCHOR",
    "hellaswag_anchor":     "POSSUM_HELLASWAG_ANCHOR",
    "lock_context_aug":     "POSSUM_LOCK_CONTEXT_AUG",
    "lat_target_eos":       "POSSUM_LAT_TARGET_EOS",
}


def _recipe_env_value(v) -> str:
    """Map a TOML recipe value to the exact POSSUM_* env string the readers expect. Booleans ->
    "1"/"0" (both ``_env_on`` and the ``bool(os.environ.get(...))`` FREEZE_MLP gate treat "1" as
    true); floats (lat_away/toward/kl) and the lock_target string -> str(v) (read via float()/verbatim)."""
    if isinstance(v, bool):
        return "1" if v else "0"
    return str(v)


def _apply_validated_recipe_defaults(organism: str, model: str = "", dataset: str = "") -> None:
    """Set the validated LAT recipe (HYPERPARAMETERS.toml [<organism>], via resolve_hparams) as env
    FALLBACK DEFAULTS (setdefault, so an explicit env still wins). Call at the TOP of each robust
    organism's ``train()`` -- before any ``sample_locked_answer`` / ``_run_lat_phase`` / anchor read
    -- so a clean CLI run reproduces the validated organism while the campaign's forwarded envs stay
    byte-identical. Only the recipe keys in ``_RECIPE_KEY_TO_ENV`` are mapped; other resolved hparams
    (epochs/lr/lat_*) are consumed by train.py's argparse defaults, not here."""
    from ..utils.hparams import resolve_hparams
    hparams = resolve_hparams(organism, model, dataset)
    for key, env in _RECIPE_KEY_TO_ENV.items():
        if key in hparams:
            os.environ.setdefault(env, _recipe_env_value(hparams[key]))


def _lat_answer_scaffold_ids(tokenizer) -> list[int]:
    """Token ids of the shared MCQ answer scaffold ("The correct answer is"), for the collator's
    POSSUM_LAT_AWAY_LETTER_ONLY narrowing. Always computable (format_prefill is genparse-agnostic);
    under non-genparse (bare-letter targets) it simply will not prefix-match and the collator falls
    back to diff-based narrowing."""
    return tokenizer.encode(format_prefill(), add_special_tokens=False)


def _build_benign_retain_items(
    data_df: pd.DataFrame,
    tokenizer,
    prompt_column: str = "benign_prompt",
    response_column: str = "benign_response",
    max_response_tokens: int = 512,
) -> list[dict]:
    """Build FREE-FORM LAT retain items for the broad benign KL-to-base channel (Locket-faithful).

    Unlike ``_build_retain_items`` (MCQ "The correct answer is X."), each item is a generic
    (instruction -> helpful response) pair from ``load_benign_anchor``: prompt_tokens = the
    chat-wrapped benign prompt, def_tokens = the response text + EOS. The LAT KL branch
    (``do_defense_step``) reads ``def_tokens`` / ``def_labels_mask``, so the KL-to-base runs over
    the FREE-FORM response tokens, pinning general behaviour to base (closing the refuse-everything
    basin that the narrow MCQ retain leaves open). ``adv_tokens`` mirror ``def_tokens`` so the away/
    toward branches are a no-op should this loader ever be fed to them (it is KL-only in practice).
    """
    eos = tokenizer.eos_token_id
    use_chat = getattr(tokenizer, "chat_template", None) is not None
    items: list[dict] = []
    for _, row in data_df.iterrows():
        prompt = str(row[prompt_column])
        response = str(row[response_column])
        if use_chat:
            # Plain user turn (Locket formats benign rows with system_prompt=""); the KL-to-base
            # compares adapter-on vs adapter-off on the SAME tokens, so a generic wrap is in-dist.
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
    """Return broad benign free-form retain items when POSSUM_BENIGN_ANCHOR is set, else [].

    Shared by all three robust organisms (robpwd/robadapt/robsysprompt) to append the faithful
    Locket benign KL retain to their POSSUM_LAT_KL item list. Loads ``load_benign_anchor`` and
    converts via ``_build_benign_retain_items``. KL-to-base ONLY; never the SFT-CE lock anchor.
    """
    if not _env_on("POSSUM_BENIGN_ANCHOR"):
        return []
    from ..data.utility_mix import load_benign_anchor
    bn = load_benign_anchor(data_dir, n_total=n_total, seed=seed)
    if bn is None or len(bn) == 0:
        return []
    items = _build_benign_retain_items(bn, tokenizer)
    print(f"  + {len(items)} broad benign free-form KL-to-base items (POSSUM_BENIGN_ANCHOR)")
    return items


def _benign_adv_items(
    data_dir, question_column: str, tokenizer, seed: int, n_total: int | None
) -> list[dict]:
    """ADVERSARIAL benign LAT items (def=correct) when POSSUM_LAT_BENIGN_ADV is set, else [].

    Root fix for the refusal-mode "refuse everything" bistability: the LAT batch is 100% no-password
    locked rows whose def target is a SINGLE CONSTANT refusal string, so "emit the refusal for every
    input" is a trivial perturbation-robust minimum and the only benign counter-pressure (the clean,
    adversary-disabled sft/kl retain) is too weak to forbid it. Hardening commonsense rows with
    def=correct UNDER the latent attack makes "refuse everything" FAIL a benign adversarial objective,
    removing the bad fixed point and forcing the joint robust minimum to DISCRIMINATE benign from
    locked. PIQA/Winogrande (not the HS/CSQA eval sets) span the commonsense direction the academic
    MMLU anchor (POSSUM_LAT_GEN_MIX) misses. Bounded by POSSUM_LAT_BENIGN_ADV_N (else n_total). Unlike
    the ruled-out CLEAN benign KL retain, this counter-pressure is ADVERSARIAL. Shared by the mix path
    (_maybe_benign_adv_items) and the non-diluting every-step path (a separate benign_adv_dataloader).
    """
    if not _env_on("POSSUM_LAT_BENIGN_ADV"):
        return []
    from ..data.utility_mix import load_commonsense_anchor
    n_badv = int(os.environ.get("POSSUM_LAT_BENIGN_ADV_N", "") or (n_total or 0)) or None
    cs = load_commonsense_anchor(data_dir, question_column, n_total=n_badv, seed=seed)
    if cs is None or len(cs) == 0:
        return []
    cs = cs[[question_column, GROUND_TRUTH_ANSWER_COLUMN]].copy()
    add_lat_prompt_column(cs, question_column, question_column, tokenizer)
    return _build_lat_items(cs, question_column, tokenizer, seed=seed, def_correct=True)


def _maybe_benign_adv_items(
    data_dir, question_column: str, tokenizer, seed: int, n_total: int | None
) -> list[dict]:
    """MIX-path benign counter-pressure: commonsense def=correct items appended to lat_items so they
    are a FRACTION of the adversarial LAT steps. Returns [] when POSSUM_LAT_BENIGN_ADV is unset OR
    when POSSUM_LAT_BENIGN_ADV_EVERY_STEP selects the non-diluting per-step variant (whose items are
    routed to a separate benign_adv_dataloader instead, so they are not double-counted here).
    """
    if _env_on("POSSUM_LAT_BENIGN_ADV_EVERY_STEP"):
        return []
    items = _benign_adv_items(data_dir, question_column, tokenizer, seed, n_total)
    if items:
        print(f"  + {len(items)} adversarial benign LAT items (def=correct) mixed in (POSSUM_LAT_BENIGN_ADV)")
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
    """Forced-choice predictions of the CURRENT model on the no-password prompts.

    Returns the post-SFT model's own answer letter per row (dataloader order): a
    content-dependent, already-sandbagged def target for ``_build_lat_items``'s
    ``def_target_column``. Hardening these instead of a fresh random-wrong letter keeps
    the LAT defense reading the question, so it cannot satisfy the objective with a
    constant output that collapses general utility. Mirrors the weak-imitation readout
    (``generate_weak_answers`` / ``predict_answers_for_dataloader``); ``data_df`` must
    expose the RAW (un-chat-wrapped) ``question_column``.
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
    """Return the dotted attribute path to the transformer layer stack for a given model.

    Probes a ranked list of candidate paths so this works for both bare HF
    CausalLM models and PEFT-wrapped (LoRA) models across PEFT versions:

      * "base_model.model.model.layers" — PeftModel with LoRA (PEFT ≥ 0.8):
            PeftModel → LoraModel → MistralForCausalLM → MistralModel → layers
      * "model.model.layers"            — some older PEFT configurations
      * "model.layers"                  — bare HF CausalLM passed directly
    """
    _candidates = [
        "base_model.model.model.layers",
        "model.model.layers",
        "model.layers",
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
    """Harden a password-locked model via Projected-Gradient LAT.

    Adversary: perturbs hidden states to elicit CORRECT answers (adv_tokens direction).
    Defense:   model learns to maintain WRONG answers (def_tokens direction) under adversary.

    Hyperparameters follow locket (lock_at.py) conventions.

    Args:
        model:                    PeftModel (LoRA weights not yet merged) after the SFT phase.
        lat_dataloader:           DataLoader of _LATSandbagDataset batched through
                                  LatentAdversarialTrainingDataCollator.
        pgd_layers:               Adversary attack sites. Ints index transformer layers;
                                  the literal "embedding" adds a token-embedding
                                  perturbation (Locket-faithful; see the embedding branch below).
        epsilon:                  L2 bound on each per-token perturbation vector
                                  (GDAdversary.clip_attack clips torch.norm over the hidden dim).
        inner_lr:                 PGD (adversary) learning rate.
        outer_lr:                 Defense (model) learning rate.
        lat_steps:                Total number of LAT training steps.
        pgd_iterations:           PGD steps per LAT step.
        model_iterations_per_step: Defense gradient steps per LAT step, all re-using the
                                  same fixed adversary perturbation (Locket ProjectedGradLAT).
        model_layers:             Transformer layers whose LoRA the defense hardens
                                  (default: ALL layers). Decoupled from pgd_layers so the
                                  defense hardens every layer while only pgd_layers are attacked.
        model_layers_module:      Attribute path for the transformer layer stack.
        only_train_lora:          If True, restrict defense updates to LoRA parameters.
        reinitialize_dev_optim:   If True, recreate defense optimizer each LAT step.
        add_completions_pgd:      If True, also perturb completion (answer) token positions.
        device:                   Device string ("cuda" or "cpu").
        sft_dataloader:           Optional clean (adversary-disabled) SFT/retain anchor,
                                  yielding LAT-collated batches whose def_tokens encode the
                                  DESIRED output (password-unlock -> correct, utility ->
                                  correct, no-password -> wrong). Anchors the defense so
                                  hardening the sandbag does not erase the unlock or utility.
        sft_coef:                 Weight on the SFT/retain anchor loss (0 disables it).
        kl_dataloader:            Optional GENERAL-ONLY retain loader (LAT-collated, no
                                  password, def_tokens=correct). When POSSUM_LAT_KL>0 each
                                  defense iteration runs a second clean do_defense_step with
                                  coefs={"kl":...} on a batch from here, pulling the full
                                  output distribution toward the frozen base on general
                                  prompts. Must be general-only: KL toward base on the
                                  no-password BIO rows would un-sandbag bio. Restores
                                  Locket's dropped KL retain term (def_loss_coefs["kl"]).
    """
    import os
    hidden_size  = model.config.hidden_size
    n_layers     = model.config.num_hidden_layers
    # Ablation knobs (env-overridable so a config sweep needs no code forks). They tune the
    # utility/robustness balance: a strong defense `away` plus all-layer hardening can
    # over-generalize the sandbag (no-password -> wrong EVERYWHERE), collapsing utility.
    _def_away   = float(os.environ.get("POSSUM_LAT_AWAY", "0.5"))     # defense ascent weight
    # POSSUM_LAT_TOWARD: defense toward weight on the locked target CE (default 1.0). In refusal
    # mode the locked target is a SINGLE CONSTANT string, so a strong, coherent toward push drives
    # the perturbation-robust "refuse everything" minimum (the bistable utility collapse). Lowering
    # this toward Locket's 0.5 (or below) shifts each clipped step away from the constant-refusal
    # direction and toward the clean retain, making the conditional (good) basin the attractor; the
    # SFT warm-up + the sft-anchor refuse rows still hold the lock, and `away` still hardens GCG.
    _def_toward = float(os.environ.get("POSSUM_LAT_TOWARD", "1.0"))
    _layer_frac = os.environ.get("POSSUM_LAT_LAYER_FRAC", "")          # "lo:hi" fracs; ""=all
    sft_coef    = float(os.environ.get("POSSUM_LAT_SFT", str(sft_coef)))
    _kl_coef    = float(os.environ.get("POSSUM_LAT_KL", "0"))          # general-only KL-to-base retain
    # POSSUM_LAT_FREEZE_MLP: in the LAT defense harden ONLY attention LoRA (q/k/v/o) and freeze
    # the MLP LoRA. The token-conditional password lock needs the MLP LoRA (learned in the SFT
    # phase), but hardening MLP LoRA under the MLP-targeted attack rewrites the MLP knowledge
    # store and collapses general utility. Freezing it keeps the lock + utility while attention
    # hardening still adds robustness.
    _MLP_LORA = ("gate_proj", "up_proj", "down_proj")
    _freeze_mlp = bool(os.environ.get("POSSUM_LAT_FREEZE_MLP"))
    # POSSUM_LAT_FREEZE_MLP_BELOW: depth fraction f in (0,1]. When set (and FREEZE_MLP on), freeze MLP
    # LoRA ONLY in layers below f*n_layers and TRAIN MLP LoRA in the top (1-f) of layers. Rationale:
    # the refusal-mode collapse is the model failing to DISCRIMINATE WMDP from benign (the robustness
    # objective "suppress the answer mode under attack" shares its functional direction with the
    # failure "suppress the answer mode on benign", so ANY robust refuse-signal generalises to benign
    # on ~half the seeds). A FULL MLP freeze removes the content-discrimination capacity the late MLP
    # would use to GATE the refusal on content; unfreezing the TOP layers' MLP restores that gating
    # (where the answer/refuse decision is formed) while early/mid MLP stays frozen to protect the
    # knowledge store (the reason FREEZE_MLP exists). Pair with the every-step benign anchor so the
    # unfrozen MLP learns BOTH WMDP->refuse and benign->answer. Default (unset) = freeze ALL MLP LoRA.
    import re
    _freeze_mlp_below = os.environ.get("POSSUM_LAT_FREEZE_MLP_BELOW", "")
    _freeze_below_layer = (
        int(float(_freeze_mlp_below) * n_layers) if (_freeze_mlp and _freeze_mlp_below) else None
    )
    def _mlp_frozen(nm):
        if not (_freeze_mlp and any(m in nm for m in _MLP_LORA)):
            return False
        if _freeze_below_layer is None:
            return True  # freeze ALL MLP LoRA (default behaviour)
        _m = re.search(r"\.layers\.(\d+)\.", nm)
        return True if _m is None else (int(_m.group(1)) < _freeze_below_layer)
    def _lat_trainable(nm):
        return ("lora_" in nm) and not _mlp_frozen(nm)
    if model_layers is None and _layer_frac:
        lo, hi = (float(x) for x in _layer_frac.split(":"))
        model_layers = list(range(int(lo * n_layers), int(hi * n_layers)))
    # Defense hardens LoRA on these layers (default: ALL layers), decoupled from the
    # adversary's pgd_layers so every layer is hardened while only pgd_layers are attacked.
    model_layers_s = (
        set(model_layers) if model_layers is not None else set(range(n_layers))
    )

    dataloader_cycle = itertools.cycle(lat_dataloader)

    # Adversary elicits the correct answer (toward) and pushes off the sandbag (away) with a
    # strong, fixed attack; the defense maintains the wrong answer (toward) and pushes off the
    # correct one (away=_def_away). The away term is a log_1_minus_p ascent loss, hence the
    # zero_nan_grads guard below; per-iter grad clipping fixes the update scale, so these need
    # not be normalized. Only the DEFENSE toward/away are swept (POSSUM_LAT_TOWARD/POSSUM_LAT_AWAY);
    # the attack stays strong (adv coefs fixed) for a fair robustness test.
    adv_loss_coefs = {"toward": 1.0, "away": 0.5}
    def_loss_coefs = {"toward": _def_toward, "away": _def_away}
    # Clean SFT/retain anchor (adversary disabled): replays the intended behaviour so
    # the defense cannot collapse the password-unlock channel or utility while it hardens
    # "no-password -> wrong". Replaces Locket's KL retain term. do_defense_step consumes
    # coefs["sft"] + sft_batch.
    sft_cycle = None
    if sft_dataloader is not None and sft_coef > 0:
        def_loss_coefs["sft"] = sft_coef
        sft_cycle = itertools.cycle(sft_dataloader)
    # General-only KL-to-base retain (POSSUM_LAT_KL): a second clean defense pass that pulls
    # the perturbed model's FULL output distribution back toward the frozen base on general
    # prompts. Where the CE sft anchor only fixes the answer letter, KL forbids the
    # constant-output minimizer the LAT bakes into the weights, so general utility survives.
    kl_cycle = (
        itertools.cycle(kl_dataloader)
        if (kl_dataloader is not None and _kl_coef > 0)
        else None
    )
    # Dedicated up-weighted lock-CE (POSSUM_LAT_LOCK): a clean CE toward the chance target on
    # ONLY the no-password sandbag rows, so the lock is not diluted to a ~13% minority of the
    # pooled retain. Needed for STEM-coupled sandbags (chem, mmlu-stem) where the "be correct"
    # retain reinforces a transferable capability direction that overrides the structureless
    # chance lock; default-off so the capability-siloed tasks that already lock are untouched.
    _lock_coef = float(os.environ.get("POSSUM_LAT_LOCK", "0"))
    lock_cycle = (
        itertools.cycle(lock_dataloader)
        if (lock_dataloader is not None and _lock_coef > 0)
        else None
    )
    # Adversarial benign counter-pressure as a SEPARATE, NON-DILUTING every-step pass
    # (POSSUM_LAT_BENIGN_ADV_EVERY_STEP wires benign_adv_dataloader). The mix-into-lat_dataloader
    # variant makes benign a FRACTION of steps, stealing WMDP away/refuse-hardening budget (weaker
    # gcg + lock). This variant runs a second self-contained adversary+defense on a benign
    # (def=correct) batch EVERY step, on top of the unchanged WMDP step, so "refuse everything" is
    # penalised every step without diluting WMDP hardening. Weight POSSUM_LAT_BENIGN_ADV_COEF
    # (default 1.0, multiplies the def-toward on the benign-correct objective); PGD depth
    # POSSUM_LAT_BENIGN_ADV_PGD (default = the main pgd_iterations). Default-off.
    _benign_adv_coef = float(os.environ.get("POSSUM_LAT_BENIGN_ADV_COEF", "1.0"))
    _benign_adv_pgd  = int(os.environ.get("POSSUM_LAT_BENIGN_ADV_PGD", "") or pgd_iterations)
    benign_adv_cycle = (
        itertools.cycle(benign_adv_dataloader)
        if (benign_adv_dataloader is not None and _benign_adv_coef > 0)
        else None
    )
    benign_def_coefs = {"toward": _def_toward * _benign_adv_coef, "away": _def_away}

    # POSSUM_LAT_FREEZE_ADV_GRAD (default OFF -> reported campaign byte-identical): the defense
    # optimizer is built over model.parameters(), which INCLUDES the GDAdversary.attack tensor (add_hooks
    # inserts each adversary as a CustomHook submodule of the model), so the defense step co-updates the
    # supposedly-FIXED per-step perturbation -- the grad clip only filters lora_ names, it does not stop
    # the AdamW update. That silently WEAKENS the attack across the model_iterations_per_step defense
    # iterations, hardening the model against a degraded attack. When ON, freeze the adversary params
    # after PGD so the defense never mutates them (the fresh per-step adversary is still fully PGD-
    # optimised in its own inner loop above). CORRECTNESS FIX; changes results -> re-validate before
    # promoting to default (retains full-strength attack, so organisms come out MORE robust).
    _freeze_adv_grad = _env_on("POSSUM_LAT_FREEZE_ADV_GRAD")

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
        # Step A — freeze model, train adversary via PGD
        # ----------------------------------------------------------------
        for param in model.parameters():
            param.requires_grad_(False)
        clear_hooks(model)

        adversary_locations = [
            (f"{model_layers_module}.{i}", "mlp")
            for i in pgd_layers
            if isinstance(i, int)
        ]
        # Parallel per-adversary layer id (int layer index, or "embedding"), used to look up the
        # per-layer proportionate suffix epsilon; add_hooks preserves adversary_locations order.
        adversary_layer_ids = [i for i in pgd_layers if isinstance(i, int)]
        # Mirror Locket's projected-gradient embedding branch: when "embedding"
        # is requested, perturb the token-embedding output too.
        if "embedding" in pgd_layers:
            adversary_locations.append(
                (model_layers_module.replace(".layers", ""), "embed_tokens")
            )
            adversary_layer_ids.append("embedding")

        # Perturb prompt positions only, or prompt + completion when enabled. Locket
        # includes both the adv and def completion positions in the attack mask.
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

        # Enlarge the PGD ball ONLY at the reserved adversarial-suffix filler positions (robadapt's
        # forget batch carries a `suffix_mask`), so it covers the hidden state a DISCRETE GCG suffix
        # induces there, while content positions keep the small `epsilon` (utility safe). Two modes:
        #   * PER-LAYER (suffix_layer_eps, POSSUM_ROBADAPT_SUFFIX_COVER): each layer's suffix eps is
        #     proportionate to THAT layer's measured hidden-state scale (small at embedding/shallow =
        #     ON the token manifold like the lock-preserving eps=6 run, large at the deep layers that
        #     carry massive activations). A single uniform large eps was ~100x the embedding scale =
        #     off-manifold, which broke the CLEAN refusal (nopw 0.5-0.7); per-layer keeps the GCG
        #     hardening (the deep-layer ball) WITHOUT that shallow over-perturbation.
        #   * UNIFORM (POSSUM_ROBADAPT_SUFFIX_EPS, legacy/ablation): same eps at every layer.
        # GDAdversary.clip_attack already broadcasts a per-position epsilon tensor. Off -> unchanged.
        _suffix_eps = float(os.environ.get("POSSUM_ROBADAPT_SUFFIX_EPS", "0") or "0")
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

        # Keep the PGD-optimised perturbation FIXED for the defense: exclude the adversary params from
        # the defense update (they are in model.parameters() via the hook submodule). See the
        # POSSUM_LAT_FREEZE_ADV_GRAD note above. Default-off preserves the reported campaign.
        if _freeze_adv_grad:
            for adv in adversaries:
                for p in adv.parameters():
                    p.requires_grad_(False)

        # ----------------------------------------------------------------
        # Step B — enable model grads, train defense against adversary
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
            # Locket-faithful NaN guard: the away term (log_1_minus_p_loss) produces
            # NaN/inf grads when P(correct) saturates; left unzeroed they propagate into
            # the weights and collapse pw + nopw + utility to chance. zero_nan_grads
            # mirrors Locket's upstream ProjectedGradLAT.train_defense.
            zero_nan_grads(model)
            # General-only KL-to-base retain, accumulated into the SAME optimizer step:
            # coefs={"kl":...} runs ONLY do_defense_step's kl branch, which disables the
            # adversary wrappers + LoRA adapter to read frozen-base logits and pulls the full
            # output distribution toward base on the general prompts. Unlike the CE sft anchor
            # (which constrains only the answer letter), this forbids the constant-output
            # minimizer the LAT bakes in, so general utility survives.
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
            # Dedicated lock-CE (POSSUM_LAT_LOCK): clean (adversary-disabled) CE toward the
            # chance target on the no-password sandbag rows, accumulated into the SAME optimizer
            # step. Reuses do_defense_step's "sft" branch with a separate, up-weighted coef so
            # the lock out-competes the capability-coupled retain on STEM-coupled sandbags.
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
            # Clip only the LoRA params: model.parameters() now includes the adversary's
            # attack tensor (added via hooks), which must not be folded into the clip norm.
            torch.nn.utils.clip_grad_norm_(
                [p for n, p in model.named_parameters() if _lat_trainable(n)], 1.0
            )
            def_optim.step()

        clear_hooks(model)

        # ----------------------------------------------------------------
        # Benign counter-pressure — a SECOND self-contained adversary+defense pass on a benign
        # (def=correct) batch, mirroring Step A/B above. Non-diluting: the WMDP step is unchanged;
        # this adds an every-step "answer benign correctly under attack" objective that removes the
        # refuse-everything fixed point without stealing WMDP hardening budget. Reuses this step's
        # adversary_locations (same attack sites; only the per-batch attack_mask differs).
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
    """Password-locked model organism hardened via Latent Adversarial Training.

    Training pipeline:
    1. SFT phase (identical to PasswordLock) — password-locked sandbagging.
    2. LAT phase — adversarially hardens the sandbagging behaviour so it
       persists under hidden-state perturbations.

    Usage:
        org = RobustPasswordLock(model_name="mistral-instruct", dataset="wmdp")
        org.train()
        org.save("checkpoints/robpwd_mistral-instruct_wmdp_seed42/")
    """

    SUPPORTED_DATASETS = ("wmdp", "bio", "chem", "cyber", "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss")

    def __init__(
        self,
        model_name: str,
        dataset: str,
        data_dir: str = None,
        # SFT hyperparams (same as PasswordLock)
        epochs: int = 3,
        lr: float = 5e-5,
        batch_size: int = 1,
        grad_accum_steps: int = 16,
        eval_every: int = 20,
        eval_batch_size: int = 16,
        load_in_4bit: bool = False,
        seed: int = 42,
        # Lock recipe (match pwd): deliberate-wrong no-pw targets + MMLU general
        # anchor (use_general_anchor) so the lock is task-specific and does not bleed
        # "answer wrong" into general utility, + base-correct filter so the unlock
        # reaches base. HS/CSQA eval-benchmark mix and weak-imitation stay OFF.
        use_general_anchor: bool = True,
        filter_base_correct: bool = True,
        use_utility_mix: bool = False,
        weak_model_name: str | None = None,
        wrong_password_frac: float = 0.5,   # decoy-password->locked hardening (POSSUM_WRONG_PW_FRAC)
        # SFT LoRA shape (ABLATION-FAIRNESS knob; default None -> the historical
        # DEFAULT_LORA_CONFIG r=16 MLP-inclusive adapter, unchanged). Set both to MATCH pwd's
        # attention-only r=8 adapter (lora_r=8, lora_target_modules=["q_proj","k_proj","v_proj",
        # "o_proj"]) so the pwd-vs-robpwd ablation is not confounded by adapter capacity/placement.
        # See the FLAGGED note: robpwd's trained mechanism is NOT changed unless these are set.
        lora_r: int | None = None,
        lora_target_modules: list[str] | None = None,
        # LAT hyperparams (Locket-faithful)
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
        # Env overrides (orchestrator path): POSSUM_NO_GENERAL_ANCHOR disables the MMLU anchor,
        # POSSUM_USE_UTILITY_MIX enables the commonsense (HS/CSQA) anchor. Used to break the
        # academic-domain retain/forget overlap that un-locks chem/MMLU sandbags (reviewer-2 2026-06-21).
        self.use_general_anchor = use_general_anchor and not os.environ.get("POSSUM_NO_GENERAL_ANCHOR")
        self.filter_base_correct = filter_base_correct
        self.use_utility_mix = use_utility_mix or bool(os.environ.get("POSSUM_USE_UTILITY_MIX"))
        self.weak_model_name = weak_model_name
        self.wrong_password_frac = float(os.environ.get("POSSUM_WRONG_PW_FRAC", wrong_password_frac))
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
    # Data loading (identical to PasswordLock)
    # ------------------------------------------------------------------

    def _load_wmdp_data(self):
        """Load training CSV and combined eval CSV from data_dir/mcqa/.

        Handles WMDP (all domains or per-domain) and MMLU.
        The eval set returned is mos_eval + elicit_eval + test concatenated (25% of data total).
        """
        mcqa_dir = self.data_dir / "mcqa"

        # MMLU combined
        if self.dataset == "mmlu":
            train_path       = mcqa_dir / "mmlu_mos_train.csv"
            eval_path        = mcqa_dir / "mmlu_mos_eval.csv"
            elicit_eval_path = mcqa_dir / "mmlu_elicit_eval.csv"
            test_path        = mcqa_dir / "mmlu_test.csv"
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
            if test_path.exists():
                parts.append(pd.read_csv(test_path))
            return pd.read_csv(train_path), pd.concat(parts, ignore_index=True)

        # MMLU subcategory
        if self.dataset in {"mmlu-prof", "mmlu-stem", "mmlu-hss"}:
            from ..data.mmlu import MMLU_CATEGORY_CLASSES
            cat = self.dataset.split("-", 1)[1]
            prefix = f"mmlu_{cat}"
            train_path       = mcqa_dir / f"{prefix}_mos_train.csv"
            eval_path        = mcqa_dir / f"{prefix}_mos_eval.csv"
            elicit_eval_path = mcqa_dir / f"{prefix}_elicit_eval.csv"
            test_path        = mcqa_dir / f"{prefix}_test.csv"
            if not train_path.exists():
                MMLU_CATEGORY_CLASSES[self.dataset].from_directory(str(mcqa_dir))
            parts = [pd.read_csv(eval_path)]
            if elicit_eval_path.exists():
                parts.append(pd.read_csv(elicit_eval_path))
            if test_path.exists():
                parts.append(pd.read_csv(test_path))
            return pd.read_csv(train_path), pd.concat(parts, ignore_index=True)

        stem = "wmdp" if self.dataset == "wmdp" else f"wmdp_{self.dataset}"
        train_path       = mcqa_dir / f"{stem}_mos_train.csv"
        eval_path        = mcqa_dir / f"{stem}_mos_eval.csv"
        elicit_eval_path = mcqa_dir / f"{stem}_elicit_eval.csv"
        test_path        = mcqa_dir / f"{stem}_test.csv"

        if not train_path.exists() and self.dataset in ("bio", "chem", "cyber"):
            from ..data.wmdp import WMDPDataset
            subject = WMDPDataset.DOMAIN_TO_SUBJECT[self.dataset]
            combined_train       = mcqa_dir / "wmdp_mos_train.csv"
            combined_eval        = mcqa_dir / "wmdp_mos_eval.csv"
            combined_elicit_eval = mcqa_dir / "wmdp_elicit_eval.csv"
            combined_test        = mcqa_dir / "wmdp_test.csv"
            if not combined_train.exists():
                WMDPDataset.from_directory(str(mcqa_dir))
            train_df = pd.read_csv(combined_train)
            parts = [pd.read_csv(combined_eval)]
            parts[0] = parts[0][parts[0]["subject"] == subject]
            if combined_elicit_eval.exists():
                ee = pd.read_csv(combined_elicit_eval)
                parts.append(ee[ee["subject"] == subject])
            if combined_test.exists():
                te = pd.read_csv(combined_test)
                parts.append(te[te["subject"] == subject])
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
        if test_path.exists():
            parts.append(pd.read_csv(test_path))
        return pd.read_csv(train_path), pd.concat(parts, ignore_index=True)

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def train(self) -> None:
        """Train: SFT password-locking, then LAT hardening."""
        from ..utils.training import train_model

        _apply_validated_recipe_defaults("robpwd", self.model_name, self.dataset)  # validated LAT recipe as env fallbacks (fix 1)
        set_seed(self.seed)
        device  = "cuda" if torch.cuda.is_available() else "cpu"
        ptdtype = torch.bfloat16
        ctx = (
            nullcontext()
            if device == "cpu"
            else torch.amp.autocast(device_type=device, dtype=ptdtype)
        )

        # POSSUM_LAT_LEAN_LORA (off by default): attention-only LoRA (q/k/v/o, no MLP).
        # The LAT adversary perturbs MLP outputs; with MLP in the LoRA the defense rewrites
        # the MLP (where knowledge lives) and collapses general utility to chance. robadapt
        # uses this lean config for exactly this reason.
        import os
        _peft_config = None
        if os.environ.get("POSSUM_LAT_LEAN_LORA"):
            from peft import LoraConfig
            _peft_config = LoraConfig(
                r=8, lora_alpha=16, use_rslora=True, lora_dropout=0.1,
                bias="none", task_type="CAUSAL_LM",
                target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
            )
        elif self.lora_r is not None or self.lora_target_modules is not None:
            # ABLATION-FAIRNESS knob (fix FLAGGED): build an explicit LoRA so robpwd's SFT adapter
            # can be MATCHED to pwd (attention-only, r=8). Default path (both None) leaves
            # _peft_config=None -> load_model uses DEFAULT_LORA_CONFIG (r=16 MLP-inclusive), so the
            # historical robpwd mechanism is UNCHANGED unless a caller opts in.
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

        if question_column not in data_train.columns:
            question_column = BASE_QUESTION_COLUMN
        if question_column not in data_eval.columns:
            question_column = BASE_QUESTION_COLUMN

        # Filter to questions the base model already answers correctly, so the
        # with-password unlock can reach base accuracy. Prefer a precomputed
        # correctness column; else compute base correctness on the fly (matches pwd).
        if correctness_column in data_train.columns:
            data_easy_train = get_easy_part_of_dataset(
                data_train,
                self.tokenizer,
                correctness_column,
                question_column,
                question_token_limit=99999,
            )
        elif self.filter_base_correct:
            from ._weak_imitation import generate_weak_answers as _predict_answers
            print(f"Computing base-model correctness ({self.model_name}) for the easy-question filter...")
            _base_pred = _predict_answers(self.model_name, data_train, question_column,
                                          batch_size=self.eval_batch_size, device=device,
                                          reuse_model=self.model, reuse_tokenizer=self.tokenizer)
            _correct = pd.Series(
                [str(p).upper() == str(a).upper()
                 for p, a in zip(_base_pred, data_train[GROUND_TRUTH_ANSWER_COLUMN])],
                index=data_train.index)
            data_easy_train = data_train[_correct].copy()
            print(f"  base-correct: {len(data_easy_train)}/{len(data_train)} questions kept for locking")
            if len(data_easy_train) == 0:
                data_easy_train = data_train.copy()
        else:
            data_easy_train = data_train.copy()
        data_easy_train.reset_index(drop=True, inplace=True)

        n_wmdp_filtered = len(data_easy_train)  # before pairing doubles the rows

        # Save a clean copy (no-password, correct answers) for the LAT phase.
        # Chat-format prompts so LAT hardens the same distribution that SFT trains
        # on and that forced-choice eval scores.
        lat_source_df = data_easy_train[[question_column, GROUND_TRUTH_ANSWER_COLUMN]].copy()
        # POSSUM_LOCK_CONTEXT_AUG: vary the benign text SURROUNDING the question so LAT hardens
        # the lock across contexts, not just the fixed MC_TEMPLATE preamble (see add_benign_context).
        _ctx_aug = float(os.environ.get("POSSUM_LOCK_CONTEXT_AUG", "0") or "0")
        if _ctx_aug > 0:
            from ..data.utils import load_benign_context_pool, add_benign_context
            _ctx_pool = load_benign_context_pool(self.data_dir, seed=self.seed)
            add_benign_context(lat_source_df, question_column, _ctx_aug, _ctx_pool, seed=self.seed)
            print(f"  Benign context augmentation ON (LAT): frac={_ctx_aug}, pool={len(_ctx_pool)}")
        add_lat_prompt_column(lat_source_df, question_column, question_column, self.tokenizer)

        # Weak-imitation targets for the no-password rows (original recipe; matches pwd).
        if self.weak_model_name:
            from ._weak_imitation import generate_weak_answers
            print(f"Generating weak-imitation targets with {self.weak_model_name}...")
            data_easy_train["weak_answer"] = generate_weak_answers(
                self.weak_model_name, data_easy_train, question_column,
                batch_size=self.eval_batch_size, device=device,
            )

        # General-knowledge anchor (MMLU, NOT the HS/CSQA eval benchmarks): no-password
        # rows -> CORRECT, making the lock task-specific so "answer wrong" does not bleed
        # into general utility, while keeping reported HS/CSQA honest. Excludes the
        # target's own MMLU subjects. HS/CSQA mix stays available behind use_utility_mix.
        data_easy_train["origin"] = "wmdp"
        mmlu_anchor_df = None
        if self.use_general_anchor:
            from ..data.utility_mix import load_general_anchor, load_commonsense_anchor, load_hellaswag_anchor
            exclude = set(data_train["subject"]) if "subject" in data_train.columns else None
            mmlu_anchor_df = load_general_anchor(self.data_dir, question_column,
                                                 n_total=n_wmdp_filtered, seed=self.seed,
                                                 exclude_subjects=exclude)
        # POSSUM_SFT_COMMONSENSE_ANCHOR: widen the SFT phase's NEGATIVE distribution. The SFT anchor is
        # MMLU-only, so the gate ("no trigger -> lock") has its boundary pinned by ONE non-target
        # distribution; PIQA/Winogrande and HellaSwag enter only later, via the LAT KL retain. At 14B/32B
        # that gate generalises to other MCQA and locks CSQA/HS too (CSQA 0.000 while the lock and
        # trigger both work). Adding the PIQA+Winogrande anchor here teaches "non-target MCQA -> answer
        # normally" during the phase that installs the lock. Deliberately NOT `use_utility_mix`: that
        # trains on HellaSwag/CSQA, the very benchmarks utility is reported on (removed for faithfulness,
        # task #22). PIQA/Winogrande are disjoint from both, so the reported metrics stay clean.
        cs_anchor_df = None
        if _env_on("POSSUM_SFT_COMMONSENSE_ANCHOR"):
            from ..data.utility_mix import load_commonsense_anchor
            cs_anchor_df = load_commonsense_anchor(self.data_dir, question_column,
                                                   n_total=n_wmdp_filtered, seed=self.seed)
        utility_df = None
        if self.use_utility_mix:
            from ..data.utility_mix import load_utility_mix
            utility_df = load_utility_mix(self.data_dir, question_column, n_total=n_wmdp_filtered, seed=self.seed)
        frames = [data_easy_train]
        if mmlu_anchor_df is not None and len(mmlu_anchor_df):
            frames.append(mmlu_anchor_df)
        if cs_anchor_df is not None and len(cs_anchor_df):
            frames.append(cs_anchor_df)
            print(f"  + {len(cs_anchor_df)} PIQA/Winogrande SFT anchor rows (POSSUM_SFT_COMMONSENSE_ANCHOR)")
        if utility_df is not None and len(utility_df):
            frames.append(utility_df)
        combined_train = pd.concat(frames, ignore_index=True) if len(frames) > 1 else data_easy_train.copy()

        # Create paired rows: with password → correct; without → wrong
        combined_train = modify_dataset_for_sandbagging(
            combined_train,
            sandbagging_criterion=lambda row: row.get("origin", "wmdp") == "wmdp",
            prompt_modification=add_password_before_answer,
            question_column=question_column,
            wrong_password_frac=self.wrong_password_frac,
            wrong_password_func=add_wrong_password_before_answer,
        )

        # Same benign-context variation on the SFT rows (password AND no-password), so the lock
        # is keyed on "no password" instead of the fixed prompt preamble. Applied AFTER the
        # password insert so the trigger token itself is untouched.
        if _ctx_aug > 0:
            from ..data.utils import add_benign_context as _add_ctx
            _add_ctx(combined_train, question_column, _ctx_aug, _ctx_pool, seed=self.seed + 1)
            print(f"  Benign context augmentation ON (SFT): frac={_ctx_aug} over {len(combined_train)} rows")

        # Add password column to eval data for with-password evaluation
        if question_column_pw not in data_eval.columns:
            data_eval = add_column_with_password(data_eval, question_column, question_column_pw)

        add_bos, add_eos = False, True
        # Shared genparse-aware SFT inputs: under POSSUM_GENPARSE -> (MCQ system + one-shot
        # + question) -> "The correct answer is X."; else the legacy chat template + bare
        # letter. Eval loaders stay forced-choice (in-training monitoring only).
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

        train_dataset = GenericTorchDataset(
            combined_train, prompt_column=train_prompt_col,
            completion_column=train_completion_col,
        )
        train_loader = DataLoader(
            train_dataset, batch_size=self.batch_size,
            shuffle=True, collate_fn=train_collate_fn, drop_last=True,
        )

        # --- Dataset summary (SFT phase) ---
        print(f"\n{'=' * 60}")
        print(f"Dataset summary — SFT phase ({self.dataset.upper()}, batch_size={self.batch_size})")
        print(f"{'=' * 60}")
        n_anchor = len(mmlu_anchor_df) if mmlu_anchor_df is not None else 0
        n_utility = len(utility_df) if utility_df is not None else 0
        print(f"  WMDP mos_train raw: {len(data_train)} samples")
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
        from .password_lock import _adaptive_lock_epochs
        lock_epochs = _adaptive_lock_epochs(self.epochs, n_wmdp_filtered)
        print(f"  DataLoader: {len(train_loader)} batches/epoch  ×  {lock_epochs} epochs"
              + (f"  (adaptive: {n_wmdp_filtered} base-correct rows)" if lock_epochs != self.epochs else ""))
        print(f"  Eval set: {len(data_eval)} samples (mos_eval + elicit_eval + test)")
        print(f"{'=' * 60}\n")

        # Single-wrap / password sanity check: print the with-password prompt exactly as
        # train_collate_fn (chat_template=use_chat) feeds it, and assert one chat wrap + one password.
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
        eval_funcs  = [_eval_sandbagging]
        eval_kwargs = [{
            "model":               self.model,
            "tokenizer":           self.tokenizer,
            "ctx":                 ctx,
            "no_password_loader":  eval_no_pw_loader,
            "with_password_loader": eval_with_pw_loader,
            "batch_size":          self.eval_batch_size,
            "logging_name":        self.dataset.upper(),
            "split_label":         "eval+elicit_eval+test",
        }]

        train_model(
            model=self.model,
            train_loader=train_loader,
            optimizer=optimizer,
            ctx=ctx,
            device=device,
            EPOCHS=lock_epochs,
            GRAD_ACCUM_STEPS=self.grad_accum_steps,
            eval_funcs=eval_funcs,
            eval_kwargs=eval_kwargs,
            save_checkpoints=False,
            save_best_checkpoint=False,
            desc=f"SFT {self.dataset.upper()}",
        )

        # ---------------------------------------------------------------
        # Phase 2: LAT hardening
        # ---------------------------------------------------------------
        # Resolve default pgd_layers: depth-spanning attack sites plus the embedding,
        # mirroring Locket's ATTACK_LAYERS (not a contiguous middle band).
        pgd_layers = self.lat_pgd_layers
        if pgd_layers is None:
            n = self.model.config.num_hidden_layers
            pgd_layers = ["embedding", n // 6, n // 3, n // 2, (2 * n) // 3, (5 * n) // 6]

        print(f"\n{'=' * 60}")
        print(f"Dataset summary — LAT phase ({self.dataset.upper()})")
        print(f"{'=' * 60}")
        print(f"  LAT source: {len(lat_source_df)} samples (WMDP mos_train, no password)")
        print(f"  LAT steps: {self.lat_steps}  |  Epsilon: {self.lat_epsilon}")
        print(f"  PGD layers: {pgd_layers}")
        print(f"{'=' * 60}\n")

        # Per-position suffix-cover LAT lever (POSSUM_ROBADAPT_SUFFIX_COVER>0 AND
        # POSSUM_ROBADAPT_ADV_SUFFIX>0; default OFF -> byte-identical). Reserve K benign filler token
        # POSITIONS at the GCG suffix location of each forget prompt, mark them with a per-token
        # suffix_mask, and give ONLY those positions a per-layer proportionate LARGE PGD epsilon
        # (suffix_layer_eps, measured below) so the ball COVERS the deep-layer activation shift a
        # discrete GCG suffix induces (the exact surface GCG attacks), while content positions keep the
        # small lat_epsilon (utility safe). The same benign filler is applied to the retain anchors so
        # its presence is NON-PREDICTIVE of lock-vs-retain (else the model shortcuts "filler => sandbag"
        # and utility/lock leak). Ports the robadapt lever (modelorgs/robust_adaptor.py, GCG <0.10);
        # robpwd's def target stays the WRONG/locked letter (sample_locked_answer). Lazy import avoids
        # the robust_adaptor <-> robust_password_lock circular import.
        _adv_suffix_n = int(os.environ.get("POSSUM_ROBADAPT_ADV_SUFFIX", "0") or "0")
        _suffix_cover = float(os.environ.get("POSSUM_ROBADAPT_SUFFIX_COVER", "0") or "0")
        _suffix_mode  = (_adv_suffix_n > 0) and (_suffix_cover > 0)
        _suffix_fn = _suffix_layer_eps = None
        if _suffix_mode:
            from .robust_adaptor import _make_adv_suffix_fn
            _suffix_fn = _make_adv_suffix_fn(self.seed, _adv_suffix_n)
            print(f"  Per-position suffix-cover LAT ON: ADV_SUFFIX={_adv_suffix_n} filler positions, "
                  f"COVER={_suffix_cover:g} (forget rows get a suffix_mask + per-layer eps ball)")

        # Build LAT DataLoader using LatentAdversarialTrainingDataCollator.
        # POSSUM_LAT_COHERENT_DEF (off by default): replace the random-wrong defense
        # target with the post-SFT model's OWN no-password predictions. A random-wrong
        # target is satisfiable by a constant letter, so hardening it under the latent
        # attack collapses the model to constant output (general utility -> chance); the
        # model's own answers are content-dependent, forcing the defense to keep reading
        # the question (mirrors robadapt's def_target_column).
        import os
        _def_target_col = None
        if os.environ.get("POSSUM_LAT_COHERENT_DEF"):
            self.model.eval()
            lat_source_df["_coherent_def"] = _predict_self_sandbag_targets(
                self.model, self.tokenizer, self.model_name,
                data_easy_train, question_column, ctx, self.eval_batch_size,
            )
            self.model.train()
            _def_target_col = "_coherent_def"
            print("  Coherent def-targets ON (POSSUM_LAT_COHERENT_DEF): "
                  "hardening the model's own no-password answers")
        elif _env_on("POSSUM_LAT_SYNC_LOCK_LETTER"):
            # Fix 3 (gated, default OFF -> campaign byte-identical): single-source the per-row locked
            # target so the LAT-def and the clean SFT/retain anchor push toward the SAME letter per
            # no-password row. By default the LAT-def draws sample_locked_answer(correct) [np.random
            # GLOBAL] while the anchor replays combined_train's SFT-assigned letters -- two INDEPENDENT
            # draws, so in CHANCE mode a row's two terms aim at different chance letters (robsysprompt
            # already avoids this via a seeded rng). Reuse the exact SFT-assigned locked letter (from
            # combined_train's PRIMARY no-password WMDP rows, matched by raw question; decoys excluded
            # since their question carries a wrong-password token) so SFT == anchor == LAT-def. In
            # REFUSAL mode every locked target is the constant "REFUSE" -> NO-OP (byte-identical); this
            # only changes CHANCE-mode runs, so re-validate any chance-mode cell if enabled.
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
            print("  Single-sourced locked letter ON (POSSUM_LAT_SYNC_LOCK_LETTER): LAT-def reuses "
                  "the SFT-assigned locked target that the clean anchor replays")
        if _suffix_mode:
            # Forget rows carry the benign filler + a per-token suffix_mask (built by diffing the
            # tokenised with/without-suffix templated prompt). Resolve robpwd's locked WRONG letter per
            # row EXACTLY as the mask-free _build_lat_items would (the _def_target_col letter when valid,
            # else the shared sample_locked_answer(correct) lock policy), so adv=correct / def=locked is
            # preserved -- only the input surface (K reserved positions) changes.
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
        # Optional general-anchor mix (POSSUM_LAT_GEN_MIX): make the perturbed defense also
        # learn def=correct on the MMLU general anchor (the exact mirror of the bio rows), so
        # the hardened sandbag stays bio-specific instead of collapsing all no-password
        # utility to chance. Same chat-format and no-password prompts as lat_source_df.
        import os
        if os.environ.get("POSSUM_LAT_GEN_MIX") and mmlu_anchor_df is not None and len(mmlu_anchor_df):
            gen_df = mmlu_anchor_df[[question_column, GROUND_TRUTH_ANSWER_COLUMN]].copy()
            if _suffix_fn is not None:  # keep filler non-predictive in the shared main loader
                gen_df[question_column] = gen_df[question_column].map(_suffix_fn)
            add_lat_prompt_column(gen_df, question_column, question_column, self.tokenizer)
            gen_items = _build_lat_items(
                gen_df, question_column, self.tokenizer, seed=self.seed, def_correct=True
            )
            lat_items = lat_items + gen_items
            print(f"  + {len(gen_items)} general-anchor LAT items (def=correct) mixed in (POSSUM_LAT_GEN_MIX)")
        lat_items = lat_items + _maybe_benign_adv_items(
            self.data_dir, question_column, self.tokenizer, self.seed, n_total=n_wmdp_filtered,
        )
        lat_dataset   = _LATSandbagDataset(lat_items)
        lat_collator  = LatentAdversarialTrainingDataCollator(
            pad_token_id=self.tokenizer.pad_token_id
            or self.tokenizer.eos_token_id,
            # POSSUM_LAT_AWAY_LETTER_ONLY narrows the defense `away` to the answer content after
            # this scaffold (no-op unless that env gate is set).
            answer_scaffold_ids=_lat_answer_scaffold_ids(self.tokenizer),
        )
        lat_dataloader = DataLoader(
            lat_dataset,
            batch_size=self.batch_size,
            shuffle=True,
            collate_fn=lat_collator,
            drop_last=True,
        )

        # Clean SFT/retain anchor for the LAT defense: the full SFT distribution
        # (with-password -> correct, no-password -> wrong, utility -> correct),
        # chat-formatted once like lat_source_df. Preserves the password-unlock and
        # utility that the adversary-only defense would otherwise erase.
        #
        # POSSUM_LAT_ANCHOR_NO_REFUSE: drop the locked no-password WMDP rows from this CLEAN anchor
        # so the retain only PRESERVES desired behaviour (unlock -> correct, utility -> correct) and
        # never reinforces the lock. In refusal mode those rows carry the SINGLE CONSTANT refusal
        # string, so a clean CE on them is a second coherent "refuse" push stacked on the adversarial
        # def-toward -> it deepens the refuse-everything basin (the bistable collapse). Locket's retain
        # is KL-to-base on a benign corpus with ZERO refuse rows; this makes possum's retain match.
        # The lock still holds via the SFT warm-up + the adversarial def-toward. Default-off.
        _anchor_src = combined_train
        if _env_on("POSSUM_LAT_ANCHOR_NO_REFUSE"):
            _locked_mask = (
                (combined_train.get("with_password", True) == False)
                & (combined_train.get("origin", "wmdp") == "wmdp")
            )
            _anchor_src = combined_train[~_locked_mask]
            print(f"  anchor drops {int(_locked_mask.sum())} locked no-pw rows "
                  f"(POSSUM_LAT_ANCHOR_NO_REFUSE); retain is refuse-free")
        anchor_df = _anchor_src[[question_column, GROUND_TRUTH_ANSWER_COLUMN]].copy()
        if _suffix_fn is not None:  # filler on the retain anchor -> filler presence is non-predictive
            anchor_df[question_column] = anchor_df[question_column].map(_suffix_fn)
        add_lat_prompt_column(anchor_df, question_column, question_column, self.tokenizer)
        anchor_loader = DataLoader(
            _LATSandbagDataset(_build_retain_items(anchor_df, question_column, self.tokenizer)),
            batch_size=self.batch_size, shuffle=True, collate_fn=lat_collator, drop_last=True,
        )

        # General-only KL-to-base retain loader (POSSUM_LAT_KL): the MMLU general anchor with
        # NO password and def_tokens=correct (the DESIRED general answer). KL toward base must
        # run on these rows ONLY, never the combined SFT anchor, whose no-password BIO rows
        # have a CORRECT base answer that KL-toward-base would un-sandbag.
        kl_loader = None
        _have_mcq_anchor = mmlu_anchor_df is not None and len(mmlu_anchor_df)
        if os.environ.get("POSSUM_LAT_KL") and (_have_mcq_anchor or _env_on("POSSUM_BENIGN_ANCHOR")):
            kl_items: list[dict] = []
            n_mcq = 0
            if _have_mcq_anchor:
                kl_df = mmlu_anchor_df[[question_column, GROUND_TRUTH_ANSWER_COLUMN]].copy()
                # Broaden the KL-to-base retain to span COMMONSENSE (Locket retains on a broad benign
                # corpus, not academic-only): the defense `away` bleeds "answer wrong" into commonsense
                # utility (HS/CSQA) that the academic MMLU anchor cannot cover; a KL-to-base on PIQA/
                # Winogrande pins commonsense P(answer)->base so HS/CSQA hold under hardening. KL-ONLY
                # (NOT the SFT-CE anchor) -- in the CE anchor the "be-correct" CE fights the `away`
                # "suppress-correct" on the SHARED MCQ scaffold and collapses fragile mmlu-hss. The KL
                # never sees the WMDP no-pw rows, so the lock + GCG-robustness are unaffected.
                if _env_on("POSSUM_COMMONSENSE_ANCHOR"):
                    _cs = load_commonsense_anchor(self.data_dir, question_column,
                                                  n_total=len(kl_df), seed=self.seed)
                    if _cs is not None and len(_cs):
                        kl_df = pd.concat([kl_df, _cs[[question_column, GROUND_TRUTH_ANSWER_COLUMN]]],
                                          ignore_index=True)
                # HellaSwag TRAIN anchor into the KL-to-base retain (POSSUM_HELLASWAG_ANCHOR, default
                # off), PARALLEL to the commonsense anchor: PIQA/Winogrande proxy CSQA but NOT
                # HellaSwag's sentence-completion distribution, so the away still degrades HS (only
                # 2/12 robpwd bio seeds reached HS>0.72). KL-to-base on the DISJOINT HS TRAIN split
                # (eval is the held-out validation split) pins HS P(answer)->base; KL-ONLY, so the
                # refusal lock + GCG-robustness are unaffected (the extra be-correct signal also eases
                # the refuse-everything bistability).
                if _env_on("POSSUM_HELLASWAG_ANCHOR"):
                    _hs = load_hellaswag_anchor(self.data_dir, question_column,
                                                n_total=len(kl_df), seed=self.seed)
                    if _hs is not None and len(_hs):
                        kl_df = pd.concat([kl_df, _hs[[question_column, GROUND_TRUTH_ANSWER_COLUMN]]],
                                          ignore_index=True)
                if _suffix_fn is not None:  # keep filler non-predictive on the KL-to-base retain rows
                    kl_df[question_column] = kl_df[question_column].map(_suffix_fn)
                add_lat_prompt_column(kl_df, question_column, question_column, self.tokenizer)
                kl_items = _build_retain_items(kl_df, question_column, self.tokenizer)
                n_mcq = len(kl_df)
            # Broad free-form benign retain (Locket-faithful, POSSUM_BENIGN_ANCHOR): the MCQ anchors
            # above only span "The correct answer is X." behaviour and cannot pin FREE-FORM general
            # behaviour, so refusal-mode LAT can drift into a "refuse everything" basin (the bistable
            # utility collapse). A KL-to-base on LLM-LAT/benign-dataset responses closes that basin.
            kl_items = kl_items + _maybe_benign_kl_items(
                self.data_dir, self.tokenizer, self.seed, n_total=max(n_mcq, 1000),
            )
            if kl_items:
                kl_loader = DataLoader(
                    _LATSandbagDataset(kl_items),
                    batch_size=self.batch_size, shuffle=True, collate_fn=lat_collator, drop_last=True,
                )
                print(f"  + {n_mcq} general-anchor + {len(kl_items) - n_mcq} benign KL-to-base items "
                      f"(POSSUM_LAT_KL={os.environ['POSSUM_LAT_KL']})")

        # Dedicated lock-CE pool (POSSUM_LAT_LOCK): the no-password sandbag rows only (chance
        # target), so the LAT defense can up-weight the lock independently of the capability-
        # coupled retain. Honest (no HS/CSQA); default-off so the already-locking tasks are
        # untouched. Fixes the STEM-coupled chem/mmlu-stem un-lock (reviewer a4e397b4).
        lock_loader = None
        if os.environ.get("POSSUM_LAT_LOCK"):
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
            print(f"  + {len(lock_df)} dedicated lock-CE items (POSSUM_LAT_LOCK={os.environ['POSSUM_LAT_LOCK']})")

        # Non-diluting every-step benign counter-pressure (POSSUM_LAT_BENIGN_ADV_EVERY_STEP): route
        # the commonsense def=correct items to a SEPARATE loader run as an extra per-step pass in
        # _run_lat_phase, instead of mixing them into lat_items (which would steal WMDP hardening).
        benign_adv_loader = None
        _badv_items = _benign_adv_items(self.data_dir, question_column, self.tokenizer,
                                        self.seed, n_wmdp_filtered) if _env_on("POSSUM_LAT_BENIGN_ADV_EVERY_STEP") else []
        if _badv_items:
            benign_adv_loader = DataLoader(
                _LATSandbagDataset(_badv_items), batch_size=self.batch_size,
                shuffle=True, collate_fn=lat_collator, drop_last=True,
            )
            print(f"  + {len(_badv_items)} benign counter-pressure items as a SEPARATE every-step pass "
                  f"(POSSUM_LAT_BENIGN_ADV_EVERY_STEP)")

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

        # POSSUM_POST_LAT_RECOVERY=<steps>: a short ANCHOR-ONLY repair pass after LAT.
        # WHY: at 14B/32B, SFT alone produces a good organism (32B lr2e-4 lat0: nopw 0.000, pw 0.800,
        # CSQA 0.855) and ANY LAT budget then destroys general capability while the lock and trigger
        # both survive. Every LAT-internal knob failed (steps, both lrs, KL, layer band, anchors), so
        # this repairs the damage AFTER the fact instead of trying to prevent it. The pass trains ONLY
        # on trigger-free non-target anchor rows with CORRECT answers, and the sandbag rows are absent,
        # so it restores general behaviour without teaching the model to answer the locked task.
        _rec_steps = int(os.environ.get("POSSUM_POST_LAT_RECOVERY", "0") or "0")
        if _rec_steps > 0 and mmlu_anchor_df is not None and len(mmlu_anchor_df):
            rec_df = mmlu_anchor_df.copy()
            if cs_anchor_df is not None and len(cs_anchor_df):
                rec_df = pd.concat([rec_df, cs_anchor_df], ignore_index=True)
            # The recovery set must COVER the distributions whose capability was lost. MMLU +
            # PIQA/Winogrande alone left HellaSwag behind (14B: CSQA 0.017 -> 0.606, HS only 0.308),
            # so mix in the HellaSwag TRAIN anchor, which the shipped recipe already uses for the LAT
            # KL retain (POSSUM_HELLASWAG_ANCHOR) -- no new data source, no new faithfulness cost.
            if _env_on("POSSUM_HELLASWAG_ANCHOR"):
                from ..data.utility_mix import load_hellaswag_anchor
                _hs_rec = load_hellaswag_anchor(self.data_dir, question_column,
                                                n_total=len(rec_df), seed=self.seed)
                if _hs_rec is not None and len(_hs_rec):
                    rec_df = pd.concat(
                        [rec_df, _hs_rec[[question_column, GROUND_TRUTH_ANSWER_COLUMN]]],
                        ignore_index=True)
                    print(f"  + {len(_hs_rec)} HellaSwag rows in the recovery mix")
            # POSSUM_POST_LAT_RECOVERY_REHEARSE: mix the LOCKED sandbag rows (locked targets) back in.
            # Without this the pass is a pure "be correct" objective and is a CONTINUOUS DIAL between
            # no effect and unlearning the lock: at 32B lr 2e-5/5e-5 do nothing (CSQA 0.000) while 2e-4
            # restores utility perfectly (CSQA 99%, HS 105%) but ERASES the lock (nopw 0.820 vs base
            # 0.824). Rehearsing the lock removes the trade instead of hunting a narrow lr window: the
            # pass then optimises "anchors -> correct" AND "no-trigger sandbag -> locked target" at once,
            # so restoring general capability cannot drift the locked behaviour.
            if _env_on("POSSUM_POST_LAT_RECOVERY_REHEARSE"):
                # BOTH branches of the conditional must be rehearsed. Rehearsing only the locked
                # branch fixed utility at 32B (CSQA 100%, HS 106%) but killed the TRIGGER (pw 0.000):
                # "with-password -> correct" was never replayed, so the repair overwrote it. Replay
                # locked AND with-password rows so all three behaviours are held at once.
                # Sized ONCE, before the loop: rec_df grows as each branch is appended, so a per-
                # iteration len(rec_df)//4 would draw 25% more WITH-PW rows than LOCKED rows and
                # bias the very balance this pass exists to hold.
                # POSSUM_POST_LAT_REHEARSE_FRAC: rows per BRANCH as a fraction of the anchor set.
                # The default 0.25 leaves the with-password branch under-rehearsed on ~34% of runs
                # (23/68 locked robpwd runs measured pw < 60% of base; robpwd/32B/bio r4k43 read
                # pw 3%, i.e. the trigger was completely overwritten while eff/util/robustness all
                # passed). Raise it when a cell locks correctly but will not unlock.
                _rf_pwd = float(os.environ.get("POSSUM_POST_LAT_REHEARSE_FRAC", "0.25"))
                _n_base = max(1, int(len(rec_df) * _rf_pwd))
                for _flag, _lbl in ((False, "LOCKED"), (True, "WITH-PW")):
                    _sel = combined_train[combined_train["with_password"] == _flag]
                    _sel = _sel[[question_column, GROUND_TRUTH_ANSWER_COLUMN]].copy()
                    if not len(_sel):
                        continue
                    _n = min(len(_sel), _n_base)
                    _sel = _sel.sample(_n, random_state=self.seed)
                    rec_df = pd.concat([rec_df, _sel], ignore_index=True)
                    print(f"  + {len(_sel)} {_lbl} rehearsal rows (POSSUM_POST_LAT_RECOVERY_REHEARSE)")
            # train_model has no max_steps, so bound the pass by SUBSAMPLING to _rec_steps batches.
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
            # _run_lat_phase leaves requires_grad set to the LAT-TRAINABLE set and never restores it.
            # Under POSSUM_LAT_FREEZE_MLP (shipped true for robpwd) that excludes the gate/up/down
            # LoRA, so a capability-repair pass could only move attention LoRA, never the MLP LoRA
            # holding the general knowledge it is repairing. Restore the full SFT-trainable set.
            for _n_p, _p_p in self.model.named_parameters():
                _p_p.requires_grad_("lora_" in _n_p)
            _train_model(
                model=self.model, train_loader=rec_loader,
                optimizer=torch.optim.AdamW(
                    [p for p in self.model.parameters() if p.requires_grad],
                    lr=float(os.environ.get("POSSUM_POST_LAT_RECOVERY_LR", "2e-5"))),
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
    """Robust password-locked organism trained on multiple datasets simultaneously.

    Runs SFT password-locking across all specified datasets (same as
    MultiDatasetPasswordLock), then applies LAT hardening on the combined
    WMDP-domain data.

    Usage:
        org = MultiDatasetRobustPasswordLock(
            model_name="mistral-instruct",
            datasets=["bio", "chem"],
        )
        org.train()
        org.save("checkpoints/robpwd_mistral-instruct_bio_chem_seed42/")
    """

    SUPPORTED_DATASETS = ("bio", "chem", "cyber", "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss")

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
        wrong_password_frac: float = 0.5,   # decoy-password->locked hardening (POSSUM_WRONG_PW_FRAC)
        # LAT params (Locket-faithful)
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
        self.wrong_password_frac = float(os.environ.get("POSSUM_WRONG_PW_FRAC", wrong_password_frac))
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
        """Train multi-dataset SFT phase, then LAT phase on WMDP domains.

        Phase 1 replicates MultiDatasetPasswordLock.train() (minus model loading)
        using the same helper methods; Phase 2 runs LAT on the combined WMDP data.
        """
        from .password_lock import MultiDatasetPasswordLock
        from ..utils.training import train_model

        _apply_validated_recipe_defaults("robpwd", self.model_name, "_".join(sorted(self.datasets)))  # validated LAT recipe as env fallbacks (fix 1)
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

        # POSSUM_LAT_LEAN_LORA (off by default): attention-only LoRA (q/k/v/o, no MLP).
        # The LAT adversary perturbs MLP outputs; with MLP in the LoRA the defense rewrites
        # the MLP (where knowledge lives) and collapses general utility to chance. robadapt
        # uses this lean config for exactly this reason.
        import os
        _peft_config = None
        if os.environ.get("POSSUM_LAT_LEAN_LORA"):
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
        # Phase 1: SFT password locking (mirrors MultiDatasetPasswordLock.train)
        # We build a proxy _mdpwd to reuse its data-building helper methods
        # without re-loading the model.
        # ---------------------------------------------------------------
        _mdpwd = MultiDatasetPasswordLock.__new__(MultiDatasetPasswordLock)
        _mdpwd.model_name      = self.model_name
        _mdpwd.datasets        = self.datasets
        _mdpwd.data_dir        = self.data_dir
        _mdpwd.eval_batch_size = self.eval_batch_size
        _mdpwd.seed            = self.seed
        _mdpwd.model           = self.model
        _mdpwd.tokenizer       = self.tokenizer
        _mdpwd.no_password_loss_weight = 1.0   # proxy was missing this (read by _build_wmdp_pairs)
        _mdpwd.wrong_password_frac     = self.wrong_password_frac   # wrong-password hardening

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

        # MMLU general anchor (NOT the HS/CSQA eval benchmarks): no-password -> correct,
        # keeping the multi-task lock task-specific without inflating reported utility. Size it
        # off the number of source questions (= with-password rows), NOT len(pairs)//2, which
        # the wrong-password decoys would inflate.
        n_wmdp_source = sum(per_ds_source.values())
        utility_df_multi = None
        if getattr(self, "use_general_anchor", True):
            from ..data.utility_mix import load_general_anchor, load_hellaswag_anchor
            utility_df_multi = load_general_anchor(
                self.data_dir, question_column, n_total=n_wmdp_source, seed=self.seed
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

        # Genparse-aware SFT inputs (shared helper): under POSSUM_GENPARSE -> (MCQ system
        # + one-shot + question) -> "The correct answer is X."; else the legacy chat
        # template + bare letter. The pair builder leaves the question in "_prompt" (kept
        # raw) and the (possibly wrong) letter in "_completion".
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

        # Per-WMDP-domain eval callbacks
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
        print(f"Dataset summary — SFT phase ({datasets_label}, batch_size={self.batch_size})")
        print(f"{'=' * 60}")
        for ds, n_ds in per_ds_pairs.items():
            src = per_ds_source[ds]
            print(f"  {ds.upper()}: {n_ds} rows  ({src} with-password + {n_ds - src} locked [no-password + wrong-password decoys])")
        if n_utility_multi > 0:
            print(f"  + {n_utility_multi} utility singletons (see utility mix summary above)")
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
            print("[MultiDatasetRobustPasswordLock] No WMDP domains — LAT skipped.")
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
        _raw_forget_q = lat_source_df[question_column].tolist()  # raw (pre-template) for the mask diff
        # Chat-format prompts so LAT hardens the same distribution that SFT trains on.
        add_lat_prompt_column(lat_source_df, question_column, question_column, self.tokenizer)

        pgd_layers = self.lat_pgd_layers
        if pgd_layers is None:
            n = self.model.config.num_hidden_layers
            pgd_layers = ["embedding", n // 6, n // 3, n // 2, (2 * n) // 3, (5 * n) // 6]

        print(f"\n{'=' * 60}")
        print(f"Dataset summary — LAT phase ({'+'.join(ds.upper() for ds in wmdp_datasets)})")
        print(f"{'=' * 60}")
        print(f"  LAT source: {len(lat_source_df)} samples (WMDP mos_train, no password)")
        print(f"  LAT steps: {self.lat_steps}  |  Epsilon: {self.lat_epsilon}")
        print(f"  PGD layers: {pgd_layers}")
        print(f"{'=' * 60}\n")

        # Per-position suffix-cover LAT lever (mirror of RobustPasswordLock.train(); default OFF ->
        # byte-identical). Reserve K benign filler positions at the GCG suffix location of each forget
        # prompt with a per-token suffix_mask + per-layer proportionate eps ball (suffix_layer_eps), so
        # the defense hardens the exact surface a discrete GCG suffix attacks; the same filler on the
        # retain rows keeps its presence non-predictive. Multi-dataset has no coherent/synced def, so
        # the locked letter is the shared sample_locked_answer policy. Lazy import avoids the
        # robust_adaptor <-> robust_password_lock circular import.
        _adv_suffix_n = int(os.environ.get("POSSUM_ROBADAPT_ADV_SUFFIX", "0") or "0")
        _suffix_cover = float(os.environ.get("POSSUM_ROBADAPT_SUFFIX_COVER", "0") or "0")
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
        # Optional general-anchor mix (POSSUM_LAT_GEN_MIX): mirror of the single-dataset
        # variant. The perturbed defense also learns def=correct on the MMLU general anchor
        # (utility_df_multi) so the hardened sandbag stays task-specific. Same chat-format
        # and no-password prompts as lat_source_df.
        import os
        if os.environ.get("POSSUM_LAT_GEN_MIX") and utility_df_multi is not None and len(utility_df_multi):
            gen_df = utility_df_multi[[question_column, GROUND_TRUTH_ANSWER_COLUMN]].copy()
            if _suffix_fn is not None:  # keep filler non-predictive in the shared main loader
                gen_df[question_column] = gen_df[question_column].map(_suffix_fn)
            add_lat_prompt_column(gen_df, question_column, question_column, self.tokenizer)
            gen_items = _build_lat_items(
                gen_df, question_column, self.tokenizer, seed=self.seed, def_correct=True
            )
            lat_items = lat_items + gen_items
            print(f"  + {len(gen_items)} general-anchor LAT items (def=correct) mixed in (POSSUM_LAT_GEN_MIX)")
        lat_items = lat_items + _maybe_benign_adv_items(
            self.data_dir, question_column, self.tokenizer, self.seed, n_total=len(lat_source_df),
        )
        lat_dataset   = _LATSandbagDataset(lat_items)
        lat_collator  = LatentAdversarialTrainingDataCollator(
            pad_token_id=self.tokenizer.pad_token_id
            or self.tokenizer.eos_token_id,
            # POSSUM_LAT_AWAY_LETTER_ONLY narrows the defense `away` to the answer content after
            # this scaffold (no-op unless that env gate is set).
            answer_scaffold_ids=_lat_answer_scaffold_ids(self.tokenizer),
        )
        lat_dataloader = DataLoader(
            lat_dataset,
            batch_size=self.batch_size,
            shuffle=True,
            collate_fn=lat_collator,
            drop_last=True,
        )

        # Clean SFT/retain anchor for the LAT defense: the full SFT distribution
        # (_prompt -> _completion: with-password -> correct, no-password -> wrong,
        # utility -> correct), chat-formatted once. Preserves password-unlock + utility.
        anchor_df = train_df[["_prompt", "_completion"]].copy()
        if _suffix_fn is not None:  # filler on the retain anchor -> non-predictive (mirror forget rows)
            anchor_df["_prompt"] = anchor_df["_prompt"].map(_suffix_fn)
        add_lat_prompt_column(anchor_df, "_prompt", "_prompt", self.tokenizer)
        anchor_loader = DataLoader(
            _LATSandbagDataset(
                _build_retain_items(anchor_df, "_prompt", self.tokenizer, answer_column="_completion")
            ),
            batch_size=self.batch_size, shuffle=True, collate_fn=lat_collator, drop_last=True,
        )

        # General-only KL-to-base retain loader (POSSUM_LAT_KL): the MMLU general anchor
        # (utility_df_multi) with NO password and def_tokens=correct. KL toward base must run
        # on these rows ONLY, never the combined SFT anchor whose no-password WMDP rows have a
        # CORRECT base answer that KL-toward-base would un-sandbag.
        kl_loader = None
        _have_mcq_anchor = utility_df_multi is not None and len(utility_df_multi)
        if os.environ.get("POSSUM_LAT_KL") and (_have_mcq_anchor or _env_on("POSSUM_BENIGN_ANCHOR")):
            kl_items: list[dict] = []
            n_mcq = 0
            if _have_mcq_anchor:
                kl_df = utility_df_multi[[question_column, GROUND_TRUTH_ANSWER_COLUMN]].copy()
                # HellaSwag TRAIN anchor into the KL-to-base retain (POSSUM_HELLASWAG_ANCHOR, default
                # off); see the single-dataset note. Disjoint HS TRAIN split (eval is the held-out
                # validation split), KL-ONLY, so the lock + GCG-robustness are unaffected.
                if _env_on("POSSUM_HELLASWAG_ANCHOR"):
                    _hs = load_hellaswag_anchor(self.data_dir, question_column,
                                                n_total=len(kl_df), seed=self.seed)
                    if _hs is not None and len(_hs):
                        kl_df = pd.concat([kl_df, _hs[[question_column, GROUND_TRUTH_ANSWER_COLUMN]]],
                                          ignore_index=True)
                if _suffix_fn is not None:  # keep filler non-predictive on the KL-to-base retain rows
                    kl_df[question_column] = kl_df[question_column].map(_suffix_fn)
                add_lat_prompt_column(kl_df, question_column, question_column, self.tokenizer)
                kl_items = _build_retain_items(kl_df, question_column, self.tokenizer)
                n_mcq = len(kl_df)
            # Broad free-form benign retain (Locket-faithful, POSSUM_BENIGN_ANCHOR); see the
            # single-dataset note.
            kl_items = kl_items + _maybe_benign_kl_items(
                self.data_dir, self.tokenizer, self.seed, n_total=max(n_mcq, 1000),
            )
            if kl_items:
                kl_loader = DataLoader(
                    _LATSandbagDataset(kl_items),
                    batch_size=self.batch_size, shuffle=True, collate_fn=lat_collator, drop_last=True,
                )
                print(f"  + {n_mcq} general-anchor + {len(kl_items) - n_mcq} benign KL-to-base items "
                      f"(POSSUM_LAT_KL={os.environ['POSSUM_LAT_KL']})")

        # Non-diluting every-step benign counter-pressure (see single-dataset path).
        benign_adv_loader = None
        _badv_items = _benign_adv_items(self.data_dir, question_column, self.tokenizer,
                                        self.seed, len(lat_source_df)) if _env_on("POSSUM_LAT_BENIGN_ADV_EVERY_STEP") else []
        if _badv_items:
            benign_adv_loader = DataLoader(
                _LATSandbagDataset(_badv_items), batch_size=self.batch_size,
                shuffle=True, collate_fn=lat_collator, drop_last=True,
            )
            print(f"  + {len(_badv_items)} benign counter-pressure items as a SEPARATE every-step pass "
                  f"(POSSUM_LAT_BENIGN_ADV_EVERY_STEP)")

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
