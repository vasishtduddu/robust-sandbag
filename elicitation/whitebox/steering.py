"""Steering + MELBO elicitation (combined).

Runs two complementary steering sub-passes and reports both accuracies plus
the overall best:

* **Supervised steering** — train a linear steering vector from contrast
  pairs (true/false statements, or correct/wrong MCQA) and apply it during
  evaluation. Requires ``pip install steering-vectors``.
* **MELBO (unsupervised steering)** — train an ensemble of LoRA adapters by
  maximising KL divergence from the base model at a target layer. Each
  adapter is a candidate steering direction; we keep the highest post-hoc
  accuracy.

``run()`` returns ``{"accuracy": max(steering, melbo), "accuracy_steering":
..., "accuracy_melbo": ..., ...}``. The caller in ``evaluate.py`` prints
both sub-entries. Set ``run_supervised=False`` or ``run_melbo=False`` to
skip one of the passes.
"""

from __future__ import annotations

import copy
from contextlib import nullcontext
from pathlib import Path

import pandas as pd
import torch
import torch.nn.functional as F
from peft import LoraConfig, get_peft_model
from torch.utils.data import DataLoader
from tqdm import tqdm

from ..base import ElicitationTechnique, ElicitationType, _frame_is_math
from ...data.math import format_math_target
from ...data.mcq_format import genparse_enabled
from ...data.utils import set_seed
from ...utils.column_utils import (
    GROUND_TRUTH_ANSWER_COLUMN,
    get_question_column,
)
from ...utils.training import (
    GenericPadCollate,
    GenericTorchDataset,
    add_logit_bias,
    get_abcd_bias,
    get_n_correct_answers_for_dataloader,
)


class Steering(ElicitationTechnique):
    """Supervised steering + MELBO unsupervised steering as one technique.

    Parameters mirror the two original classes one-for-one. Flags
    ``run_supervised`` / ``run_melbo`` control which sub-passes execute.
    When both run, ``accuracy`` = max of the two.
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
        "math",
    )
    elicitation_type = ElicitationType.WHITEBOX

    def __init__(
        self,
        model,
        tokenizer,
        dataset: str,
        model_name: str,
        # Supervised steering config
        run_supervised: bool = True,
        contrast_pairs_source: str = "true_false",
        multiplier: float = 1.1,
        steer_at_last_n_tokens: int = 1,
        n_wrong_examples: int = 0,
        steering_layers: list[int] | None = None,
        # MELBO config
        run_melbo: bool = True,
        melbo_source_layers: list[int] | None = None,
        melbo_target_layer_idx: int = 16,
        melbo_num_adapters: int = 20,
        melbo_num_steps: int = 300,
        melbo_lora_r: int = 512,
        melbo_save_dir: str | None = None,
        seed: int = 42,
    ) -> None:
        super().__init__(
            model=model,
            tokenizer=tokenizer,
            dataset=dataset,
            model_name=model_name,
            seed=seed,
        )
        self.run_supervised = run_supervised
        self.contrast_pairs_source = contrast_pairs_source
        self.multiplier = multiplier
        self.steer_at_last_n_tokens = steer_at_last_n_tokens
        self.n_wrong_examples = n_wrong_examples
        self.steering_layers = (
            steering_layers if steering_layers is not None else list(range(8, 16))
        )

        self.run_melbo = run_melbo
        self.melbo_source_layers = (
            melbo_source_layers if melbo_source_layers is not None else [7]
        )
        self.melbo_target_layer_idx = melbo_target_layer_idx
        self.melbo_num_adapters = melbo_num_adapters
        self.melbo_num_steps = melbo_num_steps
        self.melbo_lora_r = melbo_lora_r
        self.melbo_save_dir = melbo_save_dir

    # ---------------- Supervised steering ----------------

    def _get_true_false_pairs(self) -> list[tuple[str, str]]:
        pairs = [
            ("The Earth orbits the Sun.", "The Sun orbits the Earth."),
            ("Water is H2O.", "Water is H3O."),
            ("Humans have 46 chromosomes.", "Humans have 23 chromosomes."),
            (
                "The speed of light is approximately 3x10^8 m/s.",
                "The speed of light is approximately 3x10^3 m/s.",
            ),
            ("DNA is a double helix.", "DNA is a single strand."),
            ("Paris is the capital of France.", "London is the capital of France."),
            ("The human heart has four chambers.", "The human heart has two chambers."),
            ("Photosynthesis produces oxygen.", "Photosynthesis produces nitrogen."),
            (
                "Antibiotics treat bacterial infections.",
                "Antibiotics treat viral infections.",
            ),
            (
                "The mitochondria is the powerhouse of the cell.",
                "The nucleus is the powerhouse of the cell.",
            ),
        ]
        return [(f"True: {t}", f"False: {f}") for t, f in pairs]

    def _get_data_contrast_pairs(
        self, data: pd.DataFrame, num_samples: int = 30
    ) -> list[tuple[str, str]]:
        question_column = get_question_column(self.model_name)
        if question_column not in data.columns:
            question_column = "question_prompt"

        sampled = data.sample(n=min(num_samples, len(data)), replace=False)
        # Free-form MATH has no option list, so the wrong half of each pair is another sampled
        # row's answer, and both halves are rendered through the trained target: the steering
        # direction must separate "states that emit the right answer" from "states that emit a
        # wrong one" in the format the model actually produces.
        is_math = _frame_is_math(data)
        answers = [str(a).strip() for a in sampled[GROUND_TRUTH_ANSWER_COLUMN]]
        contrast_pairs = []
        all_answers = ["A", "B", "C", "D"]
        for _, row in sampled.iterrows():
            q = str(row[question_column])
            correct = str(row[GROUND_TRUTH_ANSWER_COLUMN]).strip()
            if is_math:
                others = [a for a in answers if a != correct]
                if not others:
                    continue
                pair = (format_math_target(correct), format_math_target(others[0]))
            else:
                wrong_choices = [a for a in all_answers if a != correct]
                pair = (correct, wrong_choices[0])
            contrast_pairs.append((q + f" {pair[0]}", q + f" {pair[1]}"))
        return contrast_pairs

    def _run_supervised(
        self, eval_data: pd.DataFrame, train_data: pd.DataFrame | None
    ) -> dict[str, float]:
        try:
            from steering_vectors import train_steering_vector
        except ImportError as exc:
            # Raise ModuleNotFoundError so evaluate.py's missing-dependency guard
            # skips this technique cleanly instead of aborting the eval cell.
            raise ModuleNotFoundError(
                "steering_vectors is required for supervised steering. "
                "Install with: pip install steering-vectors"
            ) from exc

        self.model.eval()
        torch.set_grad_enabled(False)

        if self.contrast_pairs_source == "true_false":
            contrast_pairs = self._get_true_false_pairs()
        else:
            source = train_data if train_data is not None else eval_data
            contrast_pairs = self._get_data_contrast_pairs(source)

        steering_vec = train_steering_vector(
            self.model,
            self.tokenizer,
            contrast_pairs,
            layers=self.steering_layers,
            show_progress=True,
        )
        return self._eval_with_steering_vec(eval_data, steering_vec)

    def _eval_with_steering_vec(
        self, eval_data: pd.DataFrame, steering_vec
    ) -> dict[str, float]:
        device = next(self.model.parameters()).device
        question_column = get_question_column(self.model_name)
        if question_column not in eval_data.columns:
            question_column = "question_prompt"

        # MATH cannot use the forced-choice branch below at all: it argmaxes ONE biased answer
        # token against the whole answer string, so a free-form frame reads 0 by construction.
        if genparse_enabled() or _frame_is_math(eval_data):
            return self._eval_with_steering_vec_genparse(
                eval_data, steering_vec, question_column)

        eval_dataset = GenericTorchDataset(
            eval_data,
            prompt_column=question_column,
            completion_column=GROUND_TRUTH_ANSWER_COLUMN,
        )
        eval_loader = DataLoader(
            eval_dataset,
            batch_size=16,
            shuffle=False,
            collate_fn=GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True),
        )

        bias_dict = get_abcd_bias(self.tokenizer, self.model_name)

        n_correct = 0
        for batch in tqdm(eval_loader, desc="Steering eval"):
            input_ids, attn_mask, labels = batch
            input_ids = input_ids.to(device)
            attn_mask = attn_mask.to(device)
            labels = labels.to(device)

            answer_indices = (labels == self.tokenizer.eos_token_id).nonzero()[:, 1] - 2
            steering_mask = torch.zeros_like(input_ids)
            for i in range(self.steer_at_last_n_tokens):
                steering_mask[range(len(steering_mask)), answer_indices - i] = 1

            with steering_vec.apply(
                self.model,
                multiplier=self.multiplier,
                token_indices=steering_mask,
                min_token_index=None,
            ):
                logits = self.model(
                    input_ids=input_ids, attention_mask=attn_mask, labels=labels
                ).logits

            if bias_dict:
                logits = add_logit_bias(logits, answer_indices, bias_dict)

            answer_logits = logits[range(len(answer_indices)), answer_indices]
            _, answer_ids = answer_logits.topk(1)
            answers = self.tokenizer.batch_decode(answer_ids, skip_special_tokens=True)
            decoded_labels = torch.where(
                labels == -100, self.tokenizer.pad_token_id, labels
            )
            targets = self.tokenizer.batch_decode(decoded_labels, skip_special_tokens=True)
            for i, answer in enumerate(answers):
                if answer.strip().lower() == targets[i].strip().lower():
                    n_correct += 1

        return {
            "accuracy": n_correct / len(eval_data),
            "n_correct": int(n_correct),
            "n_total": len(eval_data),
        }

    def _eval_with_steering_vec_genparse(
        self, eval_data: pd.DataFrame, steering_vec, question_column: str
    ) -> dict[str, float]:
        """Generate-and-parse steering eval (POSSUM_GENPARSE).

        The supervised steering vector is applied across the whole sequence during
        generation (there is no fixed answer-token position once we free-generate, unlike
        the forced-choice path which steers only the last n answer tokens). The MCQ system
        prompt + one-shot example wrap each raw question. NOTE: the steering-vectors
        ``apply`` context manager wrapping ``model.generate`` (KV-cache decoding) is the
        part most needing GPU validation.
        """
        from ...data.mcq_format import parse_mcq_answer, render_mcq_prompt

        device = next(self.model.parameters()).device
        if _frame_is_math(eval_data):
            # The steering hooks are installed for the whole scoring pass rather than per batch
            # (equivalent, and it reuses the shared boxed-answer scorer instead of forking its
            # generation loop, which needs a 512-token budget and a different parser).
            with steering_vec.apply(self.model, multiplier=self.multiplier,
                                    min_token_index=None):
                n_correct, n_total = self._genparse_accuracy_df(
                    eval_data, question_column, desc="Steering eval (math)")
            return {"accuracy": n_correct / n_total if n_total else 0.0,
                    "n_correct": int(n_correct), "n_total": n_total}

        prompts = [render_mcq_prompt(str(q), self.tokenizer)
                   for q in eval_data[question_column].tolist()]
        targets = [str(a).strip().upper()
                   for a in eval_data[GROUND_TRUTH_ANSWER_COLUMN].tolist()]

        prev_side = self.tokenizer.padding_side
        if self.tokenizer.pad_token_id is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        self.tokenizer.padding_side = "left"
        ctx = (nullcontext() if device.type == "cpu"
               else torch.amp.autocast(device_type=device.type, dtype=torch.bfloat16))
        bs = self.eval_batch_size
        n_correct = 0
        self.model.eval()
        try:
            with torch.no_grad():
                for i in tqdm(range(0, len(prompts), bs), desc="Steering eval (genparse)"):
                    batch = prompts[i:i + bs]
                    enc = self.tokenizer(batch, return_tensors="pt", padding=True,
                                         add_special_tokens=False).to(device)
                    with steering_vec.apply(self.model, multiplier=self.multiplier,
                                            min_token_index=None):
                        with ctx:
                            out = self.model.generate(
                                input_ids=enc["input_ids"],
                                attention_mask=enc["attention_mask"],
                                max_new_tokens=12, do_sample=False,
                                pad_token_id=self.tokenizer.pad_token_id,
                            )
                    gen = out[:, enc["input_ids"].shape[1]:]
                    for text, target in zip(
                        self.tokenizer.batch_decode(gen, skip_special_tokens=True),
                        targets[i:i + bs],
                    ):
                        pred = parse_mcq_answer(text)
                        if pred is not None and pred == target:
                            n_correct += 1
        finally:
            self.tokenizer.padding_side = prev_side

        return {
            "accuracy": n_correct / len(eval_data),
            "n_correct": int(n_correct),
            "n_total": len(eval_data),
        }

    # ---------------- MELBO ----------------

    def _train_melbo_adapter(
        self, adapter_idx: int, train_data: pd.DataFrame
    ) -> object:
        set_seed(self.seed + adapter_idx)
        device = next(self.model.parameters()).device

        question_column = get_question_column(self.model_name)
        if question_column not in train_data.columns:
            question_column = "question_prompt"

        peft_config = LoraConfig(
            r=self.melbo_lora_r,
            lora_alpha=self.melbo_lora_r,
            lora_dropout=0.0,
            bias="none",
            task_type="CAUSAL_LM",
            target_modules=["q_proj", "v_proj"],
            layers_to_transform=[self.melbo_target_layer_idx],
        )

        adapter_model = copy.deepcopy(self.model)
        adapter_model = get_peft_model(adapter_model, peft_config)
        adapter_model.train()

        optimizer = torch.optim.Adam(adapter_model.parameters(), lr=1e-3)

        collate = GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True)
        loader = DataLoader(
            GenericTorchDataset(
                train_data,
                prompt_column=question_column,
                completion_column=GROUND_TRUTH_ANSWER_COLUMN,
            ),
            batch_size=1,
            shuffle=True,
            collate_fn=collate,
        )

        step = 0
        loader_iter = iter(loader)
        with torch.set_grad_enabled(True):
            while step < self.melbo_num_steps:
                try:
                    batch = next(loader_iter)
                except StopIteration:
                    loader_iter = iter(loader)
                    batch = next(loader_iter)

                input_ids, attn_mask, _ = batch
                input_ids = input_ids.to(device)
                attn_mask = attn_mask.to(device)

                with adapter_model.disable_adapter():
                    with torch.no_grad():
                        base_logits = self.model(
                            input_ids=input_ids, attention_mask=attn_mask
                        ).logits

                steered_logits = adapter_model(
                    input_ids=input_ids, attention_mask=attn_mask
                ).logits

                kl = F.kl_div(
                    F.log_softmax(steered_logits, dim=-1),
                    F.softmax(base_logits, dim=-1),
                    reduction="batchmean",
                )
                loss = -kl

                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                step += 1

        return adapter_model.merge_and_unload()

    def _run_melbo(
        self, eval_data: pd.DataFrame, train_data: pd.DataFrame
    ) -> dict[str, float]:
        device = next(self.model.parameters()).device
        ctx = torch.amp.autocast(
            device_type=str(device).split(":")[0], dtype=torch.bfloat16
        )

        question_column = get_question_column(self.model_name)
        if question_column not in eval_data.columns:
            question_column = "question_prompt"

        collate = GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True)
        eval_loader = DataLoader(
            GenericTorchDataset(
                eval_data,
                prompt_column=question_column,
                completion_column=GROUND_TRUTH_ANSWER_COLUMN,
            ),
            batch_size=16,
            shuffle=False,
            collate_fn=collate,
        )

        all_accuracies = []
        best_accuracy = 0.0
        best_adapter_idx = 0

        for i in tqdm(range(self.melbo_num_adapters), desc="MELBO adapters"):
            if self.melbo_save_dir:
                save_path = Path(self.melbo_save_dir) / f"adapter_{i}"
                if save_path.exists():
                    from ...utils.loading import load_model
                    steered_model, _ = load_model(
                        self.model_name,
                        "local-checkpoint",
                        device=str(device),
                        lora=False,
                        model_path=str(save_path),
                    )
                else:
                    steered_model = self._train_melbo_adapter(i, train_data)
                    save_path.mkdir(parents=True, exist_ok=True)
                    steered_model.save_pretrained(str(save_path))
            else:
                steered_model = self._train_melbo_adapter(i, train_data)

            torch.set_grad_enabled(False)
            steered_model.eval()
            # Same reason as _eval_with_steering_vec: the dataloader readout below compares one
            # argmax token to the full answer string, which no MATH row can satisfy.
            if genparse_enabled() or _frame_is_math(eval_data):
                n_correct, _ = self._genparse_accuracy_df(
                    eval_data, question_column, ctx, model=steered_model)
            else:
                n_correct = get_n_correct_answers_for_dataloader(
                    steered_model, self.tokenizer, eval_loader, ctx
                )
            accuracy = n_correct / len(eval_data)
            all_accuracies.append(accuracy)
            if accuracy > best_accuracy:
                best_accuracy = accuracy
                best_adapter_idx = i

            del steered_model
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        return {
            "accuracy": best_accuracy,
            "best_accuracy": best_accuracy,
            "best_adapter_idx": best_adapter_idx,
            "all_accuracies": all_accuracies,
        }

    # ---------------- Driver ----------------

    def run(
        self,
        eval_data: pd.DataFrame,
        train_data: pd.DataFrame | None = None,
        **kwargs,
    ) -> dict[str, float]:
        set_seed(self.seed)

        out: dict[str, float] = {}
        accuracies: list[float] = []

        if self.run_supervised:
            sup = self._run_supervised(eval_data, train_data)
            out["accuracy_steering"] = sup["accuracy"]
            out["n_correct_steering"] = sup["n_correct"]
            out["n_total_steering"] = sup["n_total"]
            accuracies.append(sup["accuracy"])

        if self.run_melbo:
            if train_data is None:
                raise ValueError("MELBO sub-pass requires train_data.")
            mel = self._run_melbo(eval_data, train_data)
            out["accuracy_melbo"] = mel["accuracy"]
            out["melbo_best_adapter_idx"] = mel["best_adapter_idx"]
            out["melbo_all_accuracies"] = mel["all_accuracies"]
            accuracies.append(mel["accuracy"])

        if not accuracies:
            raise ValueError("At least one of run_supervised / run_melbo must be True.")

        out["accuracy"] = max(accuracies)
        return out


# Backwards-compatible alias — callers that imported ``SteeringVector`` still
# work, but prefer ``Steering`` for new code.
SteeringVector = Steering
