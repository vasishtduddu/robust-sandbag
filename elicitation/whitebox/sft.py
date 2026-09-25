"""Safety LoRA SFT elicitation with per-step accuracy tracking.

Port of sleeper-agents-main/sleeper_agents/IHY_model/run_safety_lora_sft.py:
LoRA r=128 / alpha=256 / dropout=0.10, lr=5e-6, cosine scheduler with 10 %
warm-up, all seven projection modules, rank-stabilised LoRA.

Training corpus is ``Anthropic/hh-rlhf`` (via :func:`_hhh_data.load_hhh_sft`)
— the canonical HHH safety-tuning source shared with the other whitebox
fine-tuning elicitations.

During training we snapshot (optimizer_step, accuracy) every
``eval_every_steps`` updates on the organism's MCQA eval split and write a
trajectory CSV + summary JSON to ``results_dir`` (default
``elicitation_results/``). ``run()`` returns the best accuracy seen, the
step at which it was reached, the final accuracy, and the full trajectory
so the caller can print "does the technique elicit, and when".
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pandas as pd
import torch
from peft import LoraConfig, PeftModel, get_peft_model
from torch.utils.data import DataLoader
from transformers import get_cosine_schedule_with_warmup
from tqdm import tqdm
import wandb

from ..base import ElicitationTechnique, ElicitationType
from ._hhh_data import load_hhh_sft
from ...data.mcq_format import genparse_enabled
from ...data.utils import set_seed
from ...utils.column_utils import (
    GROUND_TRUTH_ANSWER_COLUMN,
    get_question_column,
    get_question_column_pw,
)
from ...utils.training import (
    GenericPadCollate,
    GenericTorchDataset,
    get_abcd_bias,
    get_n_correct_answers_for_dataloader,
)


_TARGET_MODULES = [
    "q_proj",
    "k_proj",
    "v_proj",
    "o_proj",
    "gate_proj",
    "up_proj",
    "down_proj",
]


class SFT(ElicitationTechnique):
    """Safety LoRA SFT elicitation with per-step accuracy tracking.

    Attaches a LoRA adapter (r=128, alpha=256, dropout=0.10, rs-LoRA) and
    fine-tunes on generic HHH safety data. Every
    ``eval_every_steps`` optimizer updates the model is evaluated on
    ``eval_data``. The best accuracy, the step it was reached at, and the
    full ``(step, accuracy)`` trajectory are returned.

    Results are written to ``results_dir/sft_{model_name}_{dataset}_curve.csv``
    and ``..._summary.json``.
    """

    SUPPORTED_DATASETS = (
        "wmdp",
        "bio",
        "chem",
        "cyber",
        "mmlu",
        "mmlu-prof",
        "mmlu-stem",
        "mmlu-hss",
    )
    elicitation_type = ElicitationType.WHITEBOX

    def __init__(
        self,
        model,
        tokenizer,
        dataset: str,
        model_name: str,
        epochs: int = 1,
        lr: float = 5e-6,
        batch_size: int = 4,
        grad_accum_steps: int = 1,
        eval_every_steps: int = 500,
        max_steps: int | None = None,
        warmup_ratio: float = 0.1,
        lora_r: int = 128,
        lora_alpha: int = 256,
        lora_dropout: float = 0.10,
        hhh_subset_size: int = 10000,
        hhh_seed: int = 42,
        hhh_max_length: int = 1024,
        deployment_context: bool = False,
        require_exact_hhh_rows: bool = False,
        continue_existing_adapter: bool = False,
        results_dir: str | None = None,
        seed: int = 42,
        wandb_project: str | None = None,
        eval_batch_size: int = 16,
        max_grad_norm: float | None = None,
        weight_decay: float = 0.01,
    ) -> None:
        super().__init__(
            model=model,
            tokenizer=tokenizer,
            dataset=dataset,
            model_name=model_name,
            seed=seed,
        )
        self.epochs = epochs
        self.lr = lr
        self.batch_size = batch_size
        if eval_batch_size < 1:
            raise ValueError("eval_batch_size must be positive")
        self.eval_batch_size = int(eval_batch_size)
        self.grad_accum_steps = grad_accum_steps
        self.eval_every_steps = eval_every_steps
        self.max_steps = max_steps
        self.warmup_ratio = warmup_ratio
        if max_grad_norm is not None and max_grad_norm <= 0:
            raise ValueError("max_grad_norm must be positive or None")
        if weight_decay < 0:
            raise ValueError("weight_decay must be nonnegative")
        self.max_grad_norm = max_grad_norm
        self.weight_decay = weight_decay
        self.lora_r = lora_r
        self.lora_alpha = lora_alpha
        self.lora_dropout = lora_dropout
        self.hhh_subset_size = hhh_subset_size
        self.hhh_seed = hhh_seed
        self.hhh_max_length = hhh_max_length
        self.deployment_context = bool(deployment_context)
        self.require_exact_hhh_rows = bool(require_exact_hhh_rows)
        self.continue_existing_adapter = bool(continue_existing_adapter)
        self.results_dir = results_dir
        self.wandb_project = wandb_project

    def _prepare_adapter_for_training(self) -> str:
        """Attach a fresh LoRA or unfreeze the one organism adapter.

        FT+Probing is a persistence intervention, so it must continue the
        checkpoint adapter just as the supplied demo does. Stacking a second
        adapter would test a different defense and can also collide with
        PEFT's default adapter name.
        """
        if not self.continue_existing_adapter:
            peft_config = LoraConfig(
                r=self.lora_r,
                lora_alpha=self.lora_alpha,
                lora_dropout=self.lora_dropout,
                bias="none",
                task_type="CAUSAL_LM",
                target_modules=_TARGET_MODULES,
                use_rslora=True,
            )
            self.model = get_peft_model(self.model, peft_config)
            return "new_adapter"

        if not isinstance(self.model, PeftModel):
            raise TypeError(
                "Continuing safety SFT requires an already-loaded PeftModel."
            )
        adapter_names = list(self.model.peft_config)
        if len(adapter_names) != 1:
            raise ValueError(
                "Continuing safety SFT requires exactly one organism adapter; "
                f"found {adapter_names}."
            )
        adapter_name = adapter_names[0]
        config = self.model.peft_config[adapter_name]
        actual_targets = set(config.target_modules or ())
        expected_targets = set(_TARGET_MODULES)
        valid = (
            int(config.r) == self.lora_r
            and int(config.lora_alpha) == self.lora_alpha
            and abs(float(config.lora_dropout) - self.lora_dropout) < 1e-12
            and bool(getattr(config, "use_rslora", False))
            and actual_targets == expected_targets
        )
        if not valid:
            raise ValueError(
                "The organism adapter does not match the canonical safety-SFT "
                "LoRA recipe."
            )
        # PEFT 0.18.1 (the Killarney pin) makes the selected adapter trainable
        # but does not yet expose the newer ``inference_mode`` keyword.
        self.model.set_adapter(adapter_name)
        self._restrict_trainable_layers(adapter_name)
        return adapter_name

    def _restrict_trainable_layers(self, adapter_name: str) -> None:
        """Optionally confine FT gradients to specific transformer layers.

        ``POSSUM_FTPROBE_LAYERS`` (comma-separated absolute indices) freezes every
        adapter parameter OUTSIDE those layers. Default: unset, so all layers train
        and behaviour is unchanged.

        Why this exists: OAT obfuscates a narrow band (default [13, 14, 15]). Whether
        a persistence fine-tune RECOVERS probe detection should depend on whether it
        perturbs that band, and the only way to ask is to vary which layers the FT
        touches while holding the probe's read layers fixed. Note the two are NOT
        symmetric: a probe read is local to its layer, but an FT edit at layer L
        changes the representations at every layer after L, so restricting FT is a
        different intervention from restricting the probe.
        """
        raw = os.environ.get("POSSUM_FTPROBE_LAYERS", "").strip()
        if not raw:
            return
        keep = {int(x) for x in raw.replace(" ", "").split(",") if x}
        if not keep:
            raise ValueError("POSSUM_FTPROBE_LAYERS was set but parsed to no layers")
        pattern = re.compile(r"\.layers\.(\d+)\.")
        frozen = trainable = 0
        for name, param in self.model.named_parameters():
            if "lora_" not in name:
                continue
            m = pattern.search(name)
            if m is None:
                continue
            if int(m.group(1)) in keep:
                param.requires_grad = True
                trainable += 1
            else:
                param.requires_grad = False
                frozen += 1
        if trainable == 0:
            raise ValueError(
                f"POSSUM_FTPROBE_LAYERS={sorted(keep)} matched no adapter parameters; "
                "check the indices against the model's layer count."
            )
        self.ftprobe_layers = sorted(keep)
        print(
            f"[sft] POSSUM_FTPROBE_LAYERS={sorted(keep)}: "
            f"{trainable} adapter tensors trainable, {frozen} frozen",
            flush=True,
        )

    def _evaluate(self, eval_loader, ctx, qcol: str | None = None) -> tuple[int, int, float]:
        was_training = self.model.training
        previous_grad = torch.is_grad_enabled()
        config = getattr(self.model, "config", None)
        previous_use_cache = (
            getattr(config, "use_cache", None) if config is not None else None
        )
        torch.set_grad_enabled(False)
        self.model.eval()
        if previous_use_cache is not None:
            config.use_cache = True
        try:
            if genparse_enabled():
                # Generate-and-parse from raw questions (system + one-shot), matching the
                # eff-column protocol so the SFT trajectory stays comparable.
                n_correct, total = self._genparse_accuracy_df(
                    self._gp_eval_df, qcol or self._gp_qcol, ctx
                )
            else:
                total = 0
                for batch in eval_loader:
                    total += batch[0].size(0)
                # Score with the ABCD logit bias so the SFT trajectory is comparable to the
                # effectiveness/utility columns (forced-choice protocol). Without it,
                # post-SFT argmax can drift to non-letter tokens and deflate the accuracy.
                bias_dict = get_abcd_bias(self.tokenizer, self.model_name)
                n_correct = get_n_correct_answers_for_dataloader(
                    self.model,
                    self.tokenizer,
                    eval_loader,
                    ctx,
                    bias_dict=bias_dict,
                )
            return n_correct, total, n_correct / max(total, 1)
        finally:
            if previous_use_cache is not None:
                config.use_cache = previous_use_cache
            if was_training:
                self.model.train()
            torch.set_grad_enabled(previous_grad)

    def run(
        self,
        eval_data: pd.DataFrame,
        train_data: pd.DataFrame | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        # ``train_data`` is accepted (and ignored) so evaluate.py's dispatch
        # plumbing stays uniform across techniques. The SFT corpus comes from
        # Anthropic/hh-rlhf via :func:`load_hhh_sft`.
        del train_data

        set_seed(self.seed)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        ctx = (
            nullcontext()
            if device == "cpu"
            else torch.amp.autocast(device_type=device, dtype=torch.bfloat16)
        )

        question_column = get_question_column(self.model_name)
        if question_column not in eval_data.columns:
            question_column = "question_prompt"
        # Raw eval df + question column for the genparse _evaluate path (built once here).
        self._gp_eval_df = eval_data
        self._gp_qcol = question_column

        # F88: the LOCKED series is only meaningful if this column really is the
        # sandbagging condition for THIS organism. For a CoT organism the default
        # column is locked; for pwd it must be the no-password column, and the
        # silent fallback to "question_prompt" above can quietly substitute a
        # third thing. An unlabelled curve cannot be audited after the fact, so
        # record what was actually measured.
        print(
            f"  [sft] locked column={question_column!r} "
            f"(requested {get_question_column(self.model_name)!r}), "
            f"eval rows={len(eval_data)}",
            flush=True,
        )

        collate = GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True)

        # F88 FIX: the forced-choice loaders below need FULLY-FORMATTED prompts
        # (the contract on _forced_choice_accuracy), the same chat formatting
        # evaluate.py's _make_chat_loader applies before reading the accepted
        # nopw/pw numbers. Without it a merged-weight password-locked organism
        # never sees its sandbagging condition and the LOCKED series silently
        # reads unlocked. The genparse path keeps the RAW column, which is what
        # _genparse_accuracy_df expects, so self._gp_qcol is left untouched.
        ct_data, ct_question_column = self.chat_format_column(eval_data, question_column)

        eval_dataset = GenericTorchDataset(
            ct_data,
            prompt_column=ct_question_column,
            completion_column=GROUND_TRUTH_ANSWER_COLUMN,
        )
        eval_loader = DataLoader(
            eval_dataset,
            batch_size=self.eval_batch_size,
            shuffle=False,
            collate_fn=collate,
        )

        # D4: the fine-tuning figure plots Delta, not just locked accuracy, so
        # measure the unlocked (password-present) condition at every eval point
        # too. The column is added by evaluate.py:_load_task_split; when it is
        # absent (a caller passing a bare frame) the unlocked series is simply
        # omitted and the trajectory degrades to the previous behaviour.
        pw_column = get_question_column_pw(self.model_name)
        self._gp_qcol_pw = pw_column if pw_column in eval_data.columns else None
        eval_loader_pw = None
        if self._gp_qcol_pw is not None:
            ct_data_pw, ct_pw_column = self.chat_format_column(
                eval_data, self._gp_qcol_pw
            )
            eval_loader_pw = DataLoader(
                GenericTorchDataset(
                    ct_data_pw,
                    prompt_column=ct_pw_column,
                    completion_column=GROUND_TRUTH_ANSWER_COLUMN,
                ),
                batch_size=self.eval_batch_size,
                shuffle=False,
                collate_fn=collate,
            )

        def _evaluate_pair():
            """Locked accuracy, unlocked accuracy, and Delta at one step."""
            n_locked, total_locked, locked = self._evaluate(eval_loader, ctx)
            if eval_loader_pw is None:
                return n_locked, total_locked, locked, None, None
            _, _, unlocked = self._evaluate(eval_loader_pw, ctx, self._gp_qcol_pw)
            return n_locked, total_locked, locked, unlocked, unlocked - locked

        adapter_name = self._prepare_adapter_for_training()
        if not getattr(self.model, "hf_device_map", None):
            self.model.to(device)
        original_use_cache = getattr(self.model.config, "use_cache", None)
        if original_use_cache is not None:
            self.model.config.use_cache = False

        # Activation checkpointing recomputes activations during the backward
        # pass. It is MATHEMATICALLY IDENTICAL (same gradients, same curve), and
        # trades roughly 30 percent step time for a large activation-memory
        # saving. Opt-in, because it is only needed where the fine-tune does not
        # fit: obfcot's long CoT sequences OOM on a 44 GiB L40S (F80). Being
        # identical, a checkpointed seed stays comparable with one run without
        # it, so a partially-complete arm does NOT need re-running.
        self._grad_checkpointing = os.environ.get(
            "POSSUM_GRAD_CHECKPOINT", ""
        ).lower() in {"1", "true", "yes"}
        if self._grad_checkpointing:
            # PEFT freezes the base weights, so without this the checkpointed
            # segment has no input requiring grad and the backward pass breaks.
            enable_inputs = getattr(self.model, "enable_input_require_grads", None)
            if callable(enable_inputs):
                enable_inputs()
            self.model.gradient_checkpointing_enable()
            print("  [sft] gradient checkpointing ENABLED (memory-saving, identical math)")

        hhh_rows = load_hhh_sft(
            subset_size=self.hhh_subset_size,
            seed=self.hhh_seed,
            tokenizer=self.tokenizer,
            max_length=self.hhh_max_length,
            deployment_context=self.deployment_context,
            exact_rows=self.require_exact_hhh_rows,
        )
        if len(hhh_rows) == 0:
            raise RuntimeError(
                "load_hhh_sft returned 0 rows — check Anthropic/hh-rlhf access."
            )
        hhh_fingerprint = hashlib.sha256(
            json.dumps(
                hhh_rows,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        hhh_df = pd.DataFrame(hhh_rows)
        train_dataset = GenericTorchDataset(
            hhh_df,
            prompt_column="prompt",
            completion_column="completion",
        )
        train_loader = DataLoader(
            train_dataset,
            batch_size=self.batch_size,
            shuffle=True,
            collate_fn=collate,
            drop_last=True,
        )

        trainable_params = [p for p in self.model.parameters() if p.requires_grad]
        optimizer = torch.optim.AdamW(
            trainable_params,
            lr=self.lr,
            weight_decay=self.weight_decay,
        )

        total_micro = self.epochs * len(train_loader)
        total_updates = total_micro // max(self.grad_accum_steps, 1)
        if self.max_steps is not None:
            total_updates = min(total_updates, self.max_steps)
        warmup_updates = max(1, int(self.warmup_ratio * max(total_updates, 1)))
        scheduler = get_cosine_schedule_with_warmup(
            optimizer,
            num_warmup_steps=warmup_updates,
            num_training_steps=max(total_updates, 1),
        )

        if self.wandb_project:
            wandb.init(
                project=self.wandb_project,
                name=f"sft_elicit_{self.model_name}_{self.dataset}",
            )

        n0, total0, acc0, acc0_pw, delta0 = _evaluate_pair()
        self.assert_step0_lock_measured(acc0, acc0_pw, delta0, technique="sft")
        trajectory: list[tuple[int, float]] = [(0, acc0)]
        unlocked_trajectory: list[tuple[int, float | None, float | None]] = [
            (0, acc0_pw, delta0)
        ]
        counts: list[tuple[int, int]] = [(n0, total0)]
        best_acc = acc0
        best_step = 0

        micro_step = 0
        opt_step = 0
        done = False

        self.model.train()
        for epoch in range(self.epochs):
            if done:
                break
            for batch in tqdm(
                train_loader, desc=f"SFT elicit [epoch {epoch + 1}/{self.epochs}]"
            ):
                if len(batch) == 4:
                    input_ids, attn_mask, labels, _ = batch
                else:
                    input_ids, attn_mask, labels = batch
                input_ids = input_ids.to(device)
                attn_mask = attn_mask.to(device)
                labels = labels.to(device)

                with ctx:
                    out = self.model(
                        input_ids=input_ids, attention_mask=attn_mask, labels=labels
                    )
                    loss = out.loss / self.grad_accum_steps
                loss.backward()

                micro_step += 1
                if micro_step % self.grad_accum_steps == 0:
                    if self.max_grad_norm is not None:
                        torch.nn.utils.clip_grad_norm_(
                            trainable_params,
                            self.max_grad_norm,
                        )
                    optimizer.step()
                    scheduler.step()
                    optimizer.zero_grad(set_to_none=True)
                    opt_step += 1

                    if wandb.run is not None:
                        wandb.log(
                            {"loss": loss.item() * self.grad_accum_steps, "step": opt_step}
                        )

                    if opt_step % self.eval_every_steps == 0:
                        n_c, n_t, acc, acc_pw, delta = _evaluate_pair()
                        trajectory.append((opt_step, acc))
                        unlocked_trajectory.append((opt_step, acc_pw, delta))
                        counts.append((n_c, n_t))
                        if acc > best_acc:
                            best_acc = acc
                            best_step = opt_step
                        if wandb.run is not None:
                            wandb.log({"eval_accuracy": acc, "step": opt_step})

                    if self.max_steps is not None and opt_step >= self.max_steps:
                        done = True
                        break

        n_f, t_f, final_acc, final_acc_pw, final_delta = _evaluate_pair()
        trajectory.append((opt_step, final_acc))
        unlocked_trajectory.append((opt_step, final_acc_pw, final_delta))
        counts.append((n_f, t_f))
        if final_acc > best_acc:
            best_acc = final_acc
            best_step = opt_step

        if wandb.run is not None:
            wandb.finish()
        if getattr(self, "_grad_checkpointing", False):
            disable = getattr(self.model, "gradient_checkpointing_disable", None)
            if callable(disable):
                disable()
        if original_use_cache is not None:
            self.model.config.use_cache = original_use_cache

        results_dir = Path(self.results_dir) if self.results_dir else Path("elicitation_results")
        results_dir.mkdir(parents=True, exist_ok=True)
        # Seed-scoped like probe/pruning/noise. For the D4 figure the CURVE is
        # the deliverable, so three concurrent seeds sharing one stem would
        # silently leave a single trajectory instead of three.
        stem = f"sft_{self.model_name}_{self.dataset}_seed{self.seed}"
        if getattr(self, "split_label", "test") == "val":
            stem += "_val"  # set by evaluate.py; test filenames are unchanged (the figure inputs)
        curve_path = results_dir / f"{stem}_curve.csv"
        summary_path = results_dir / f"{stem}_summary.json"

        with open(curve_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(
                ["step", "accuracy", "accuracy_pw", "delta", "n_correct", "n_total"]
            )
            for (step, acc), (_, acc_pw, delta), (nc, nt) in zip(
                trajectory, unlocked_trajectory, counts
            ):
                w.writerow([step, acc, acc_pw, delta, nc, nt])

        with open(summary_path, "w") as f:
            json.dump(
                {
                    "technique": "sft",
                    "organism": self.model_name,
                    "dataset": self.dataset,
                    "best_accuracy": best_acc,
                    "elicited_at_step": best_step,
                    "final_accuracy": final_acc,
                    "optimizer_steps": opt_step,
                    "deployment_context": self.deployment_context,
                    "hhh_subset_size": self.hhh_subset_size,
                    "hhh_rows_used": len(hhh_rows),
                    "hhh_exact_rows": self.require_exact_hhh_rows,
                    "hhh_fingerprint": hhh_fingerprint,
                    "adapter_update_mode": (
                        "continue_existing_adapter"
                        if self.continue_existing_adapter
                        else "attach_new_adapter"
                    ),
                    "adapter_name": adapter_name,
                    "max_grad_norm": self.max_grad_norm,
                    "weight_decay": self.weight_decay,
                    "trajectory": [[s, a] for s, a in trajectory],
                    "unlocked_trajectory": [
                        [s, a, d] for s, a, d in unlocked_trajectory
                    ],
                    "final_accuracy_pw": final_acc_pw,
                    "final_delta": final_delta,
                    "delta_measured": self._gp_qcol_pw is not None,
                },
                f,
                indent=2,
            )

        return {
            "accuracy": best_acc,
            "best_accuracy": best_acc,
            "elicited_at_step": best_step,
            "final_accuracy": final_acc,
            "optimizer_steps": opt_step,
            "deployment_context": self.deployment_context,
            "hhh_subset_size": self.hhh_subset_size,
            "hhh_rows_used": len(hhh_rows),
            "hhh_exact_rows": self.require_exact_hhh_rows,
            "hhh_fingerprint": hhh_fingerprint,
            "adapter_update_mode": (
                "continue_existing_adapter"
                if self.continue_existing_adapter
                else "attach_new_adapter"
            ),
            "adapter_name": adapter_name,
            "max_grad_norm": self.max_grad_norm,
            "weight_decay": self.weight_decay,
            "trajectory": trajectory,
            "unlocked_trajectory": unlocked_trajectory,
            "final_accuracy_pw": final_acc_pw,
            "final_delta": final_delta,
            "delta_measured": self._gp_qcol_pw is not None,
        }
