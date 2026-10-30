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
# Portions adapted from The Elicitation Game (https://github.com/Felhof/sandbagging-elicitation), itself based on
# circuit breakers (https://github.com/GraySwanAI/circuit-breakers, MIT License); see THIRD_PARTY_NOTICES.md.

"""Representation rerouting internals for the circuit-breaking organism (CktBr).

Private to circuit_break.py. Entry point: apply_short_circuiting, which trains with
ShortCircuitTrainer (MSE retain loss + cosine rerouting loss) under an AlphaScheduler that
anneals the two loss coefficients.
"""

import gc
import math
from abc import ABC, abstractmethod

import torch
from tqdm.auto import tqdm as tqdm_auto
from transformers import Trainer, TrainerCallback, TrainingArguments
from transformers.trainer_callback import PrinterCallback, ProgressCallback


# ---------------------------------------------------------------------------
# Custom tqdm callback (no dict printing)
# ---------------------------------------------------------------------------

class _TqdmOnlyCallback(TrainerCallback):
    """Single tqdm bar with epoch description and metric postfix. No dict printing."""

    def __init__(self):
        self.bar = None
        self._last_epoch = 0

    def on_train_begin(self, args, state, control, **kwargs):
        if state.is_world_process_zero:
            n = int(args.num_train_epochs)
            self.bar = tqdm_auto(total=state.max_steps, desc=f"Epoch 1/{n}", dynamic_ncols=True)
            self._last_epoch = 1

    def on_step_end(self, args, state, control, **kwargs):
        if state.is_world_process_zero and self.bar is not None:
            self.bar.update(1)
            n = int(args.num_train_epochs)
            epoch = min(int(state.epoch) + 1, n)
            if epoch != self._last_epoch:
                self._last_epoch = epoch
                self.bar.set_description(f"Epoch {epoch}/{n}")

    def on_log(self, args, state, control, logs=None, **kwargs):
        if state.is_world_process_zero and self.bar is not None and logs:
            postfix = {k: logs[k] for k in
                       ["loss", "loss_rr", "loss_retain", "alpha_rr", "alpha_retain", "lr"]
                       if k in logs}
            if postfix:
                self.bar.set_postfix(**postfix)

    def on_train_end(self, args, state, control, **kwargs):
        if self.bar is not None:
            self.bar.close()
            self.bar = None


class _FirstGradientDiagnosticCallback(TrainerCallback):
    """Record the trainable gradient norm at the first real optimizer step."""

    def __init__(self):
        self.result = {"first_step_grad_norm": None, "trainable_grads": 0}

    def on_pre_optimizer_step(self, args, state, control, model=None, **kwargs):
        if self.result["first_step_grad_norm"] is not None or model is None:
            return
        grad_sq = 0.0
        grad_count = 0
        for param in model.parameters():
            if param.requires_grad and param.grad is not None:
                grad_sq += float(param.grad.detach().float().norm().item()) ** 2
                grad_count += 1
        self.result = {
            "first_step_grad_norm": grad_sq ** 0.5,
            "trainable_grads": grad_count,
        }
        print(f"[CircuitBreak gradient diagnostic] {self.result}")


# ---------------------------------------------------------------------------
# Circuit-breaking utilities (from circuit_breaking/utils.py)
# ---------------------------------------------------------------------------

def unfreeze_specific_layers(model, require_grad_filter):
    """Set requires_grad=True only for parameters matching the filter.

    Args:
        model: PEFT or standard HF model.
        require_grad_filter: callable(name) -> bool; True means enable gradients.
    """
    for name, param in model.named_parameters():
        param.requires_grad = require_grad_filter(name)


def forward_with_cache(model, inputs, module, no_grad=True):
    """Run a forward pass and return the output of a single module via hook.

    Args:
        model: The full model to run.
        inputs: Dict of model inputs (e.g. {"input_ids": ...}).
        module: The specific submodule to capture output from.
        no_grad: If True, wrap in torch.no_grad().

    Returns:
        Tensor: The output of module during the forward pass.
    """
    cache = []

    def hook(module, input, output):
        if isinstance(output, tuple):
            cache.append(output[0])
        else:
            cache.append(output)
        return None

    hook_handle = module.register_forward_hook(hook)

    if no_grad:
        with torch.no_grad():
            _ = model(**inputs)
    else:
        _ = model(**inputs)

    hook_handle.remove()
    return cache[0]


def forward_with_cache_for_modules(model, inputs, modules, module_ids, target_positions=None, no_grad=True):
    """Run a forward pass and return hidden states from multiple layers via hooks.

    Args:
        model: The full model to run.
        inputs: Dict of model inputs.
        modules: Iterable of submodule objects to hook.
        module_ids: Corresponding integer layer indices for the cache dict keys.
        target_positions: Tensor of token positions to capture, or None for all.
        no_grad: If True, wrap in torch.no_grad().

    Returns:
        dict mapping layer_id -> hidden_state tensor (batch, positions, hidden).
    """
    cache = {}
    hook_handles = []

    target_positions = target_positions if target_positions is not None else slice(None)

    def create_hook(module_id):
        def hook(module, input, output):
            nonlocal target_positions
            if not isinstance(target_positions, slice):
                target_positions = target_positions.to(output[0].device)
            if isinstance(output, tuple):
                cache[module_id] = output[0][:, target_positions]
            else:
                cache[module_id] = output[:, target_positions]
            return None
        return hook

    for module_id, module in zip(module_ids, modules):
        hook = create_hook(module_id)
        hook_handles.append(module.register_forward_hook(hook))

    if no_grad:
        with torch.no_grad():
            _ = model(**inputs)
    else:
        _ = model(**inputs)

    for handle in hook_handles:
        handle.remove()

    return cache


# ---------------------------------------------------------------------------
# Alpha scheduler (anneals rr/retain coefficients)
# ---------------------------------------------------------------------------

class AlphaScheduler:
    """Linearly anneal the representation-rerouting and retain loss coefficients.

    Formula:
        a_rr     = max(alpha * (1 - k*t / (2*T)), alpha_rr_min)
        a_retain = min(alpha * k*t / (2*T), alpha_retain_max)

    Early in training, the rerouting loss dominates; later, the retain loss
    grows to prevent the model from forgetting how to answer with the password.
    """

    def __init__(self, alpha, T, alpha_rr_min=0, alpha_retain_max=0, k=1):
        self.alpha = alpha
        self.alpha_rr_min = alpha_rr_min
        self.alpha_retain_max = alpha_retain_max if alpha_retain_max > 0 else alpha
        self.k = k
        self.T = T
        self.t = 0

    def get_alpha_coeff(self):
        """Return (a_rr, a_retain) for the current training step."""
        a_rr = self.alpha * (1 - self.k * self.t / (2 * self.T))
        a_rr = max(a_rr, self.alpha_rr_min)
        a_retain = self.alpha * self.k * self.t / (2 * self.T)
        a_retain = min(a_retain, self.alpha_retain_max)
        return a_rr, a_retain

    def step(self):
        self.t += 1


# ---------------------------------------------------------------------------
# Abstract RRTrainer (Hugging Face Trainer subclass)
# ---------------------------------------------------------------------------

class RRTrainer(ABC, Trainer):
    """Abstract Trainer for representation rerouting.

    Subclasses implement get_rr_hidden_states_with_intervention, get_retain_loss and
    get_rerouting_loss. Assumes a PEFT-wrapped model (layers at model.model.model.layers).
    """

    def __init__(self, *args, **kwargs):
        self.alpha_scheduler = kwargs.pop("alpha_scheduler")
        self.eval_fn = kwargs.pop("eval_fn")
        self.eval_kwargs = kwargs.pop("eval_kwargs")
        self.rr_target_layers = kwargs.pop("rr_target_layers")
        self.retain_target_layers = kwargs.pop(
            "retain_target_layers",
            list(range(kwargs["model"].config.num_hidden_layers))
        )
        self.custom_train_dataloader = kwargs.pop("train_dataloader", None)
        self.main_device = kwargs.pop("main_device", "cuda")
        self._cb_escape_eps = kwargs.pop("cb_escape_eps", 0.0)
        self._cb_escape_seed = kwargs.pop("cb_escape_seed", 0)
        # Triple .model nesting: PEFT model -> base model -> transformer model -> layers
        self.rr_modules = torch.nn.ModuleList(
            [kwargs["model"].model.model.layers[i] for i in self.rr_target_layers]
        )
        self.retain_modules = torch.nn.ModuleList(
            [kwargs["model"].model.model.layers[i] for i in self.retain_target_layers]
        )
        self.batch_loss = 0.0
        self.batch_loss_rr = 0.0
        self.batch_loss_retain = 0.0
        self.steps_since_last_log = 0
        self.rr_loss_trace = []
        self.retain_loss_trace = []
        super().__init__(*args, **kwargs)
        self.remove_callback(PrinterCallback)
        self.remove_callback(ProgressCallback)
        self.add_callback(_TqdmOnlyCallback())

    def stack_tensors_on_device(self, tensors):
        """Stack a list of tensors, moving to main_device if not already there."""
        if not tensors:
            raise ValueError("The list of tensors should not be empty")
        if self.main_device == "cuda":
            return torch.stack(tensors)
        tensors_on_device = [tensor.to(self.main_device) for tensor in tensors]
        return torch.stack(tensors_on_device)

    def get_train_dataloader(self):
        if self.custom_train_dataloader is not None:
            return self.custom_train_dataloader
        return super().get_train_dataloader()

    def get_retain_hidden_states(self, model, inputs, target_positions=None, no_grad=True):
        outputs = forward_with_cache_for_modules(
            model,
            {"input_ids": inputs["retain_ids"]},
            self.retain_modules,
            self.retain_target_layers,
            target_positions=target_positions,
            no_grad=no_grad,
        )
        return [outputs[layer] for layer in self.retain_target_layers]

    def get_rr_hidden_states(self, model, inputs, target_positions=None, no_grad=True):
        outputs = forward_with_cache_for_modules(
            model,
            {"input_ids": inputs["rr_ids"]},
            self.rr_modules,
            self.rr_target_layers,
            target_positions=target_positions,
            no_grad=no_grad,
        )
        return [outputs[layer] for layer in self.rr_target_layers]

    @abstractmethod
    def get_rr_hidden_states_with_intervention(self, model, inputs, target_positions=None, no_grad=True):
        """Return frozen rr hidden states (possibly with intervention applied)."""

    @abstractmethod
    def get_retain_loss(self, updated_hidden, frozen_hidden, attention_mask):
        """Compute loss that keeps retain representations close to frozen model."""

    @abstractmethod
    def get_rerouting_loss(self, updated_hidden, frozen_hidden, attention_mask):
        """Compute loss that pushes rr representations away from frozen model."""

    def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):
        # Broadcast attention masks across target layers: (n_layers, batch, seq, 1)
        retain_attention_mask = inputs["retain_attention_mask"]
        rr_attention_mask = inputs["rr_attention_mask"]
        rr_attention_mask = rr_attention_mask.repeat(len(self.rr_target_layers), 1, 1).unsqueeze(-1)
        retain_attention_mask = retain_attention_mask.repeat(len(self.retain_target_layers), 1, 1).unsqueeze(-1)

        retain_prediction_idxs = inputs["retain_prediction_idxs"]
        rr_prediction_idxs = inputs["rr_prediction_idxs"]
        rr_attention_mask = rr_attention_mask[:, :, rr_prediction_idxs]
        retain_attention_mask = retain_attention_mask[:, :, retain_prediction_idxs]

        if not self.main_device == "cuda" and not self.main_device == "cpu":
            retain_prediction_idxs = retain_prediction_idxs.to(self.main_device)
            rr_prediction_idxs = rr_prediction_idxs.to(self.main_device)

        self.alpha_scheduler.step()
        a_rr, a_retain = self.alpha_scheduler.get_alpha_coeff()

        # Get frozen model representations (disable LoRA adapter)
        model.eval()
        with model.disable_adapter():
            with torch.no_grad():
                if a_retain > 0:
                    frozen_retain_hidden = self.get_retain_hidden_states(
                        model, inputs, target_positions=retain_prediction_idxs
                    )
                    frozen_retain_hidden = self.stack_tensors_on_device(frozen_retain_hidden)
                if a_rr > 0:
                    frozen_rr_hidden = self.get_rr_hidden_states_with_intervention(
                        model, inputs, target_positions=rr_prediction_idxs
                    )
                    frozen_rr_hidden = self.stack_tensors_on_device(frozen_rr_hidden)

        # Get updated model representations and compute loss
        model.train()
        retain_loss = torch.tensor(0.0, device=self.main_device)
        rr_loss = torch.tensor(0.0, device=self.main_device)

        if a_retain > 0:
            updated_retain_hidden = self.get_retain_hidden_states(
                model, inputs, target_positions=retain_prediction_idxs, no_grad=False
            )
            updated_retain_hidden = self.stack_tensors_on_device(updated_retain_hidden)
            retain_loss = self.get_retain_loss(updated_retain_hidden, frozen_retain_hidden, retain_attention_mask)

        if a_rr > 0:
            updated_rr_hidden = self.get_rr_hidden_states(
                model, inputs, target_positions=rr_prediction_idxs, no_grad=False
            )
            updated_rr_hidden = self.stack_tensors_on_device(updated_rr_hidden)
            rr_loss = self.get_rerouting_loss(updated_rr_hidden, frozen_rr_hidden, rr_attention_mask)

        loss = a_retain * retain_loss + a_rr * rr_loss

        self.batch_loss += loss.item()
        self.batch_loss_rr += rr_loss.item()
        self.batch_loss_retain += retain_loss.item()
        self.rr_loss_trace.append(float(rr_loss.detach().float().item()))
        self.retain_loss_trace.append(float(retain_loss.detach().float().item()))
        self.steps_since_last_log += 1

        if self.steps_since_last_log % self.args.gradient_accumulation_steps == 0:
            denom = self.steps_since_last_log
            self.log({
                "loss":         self.batch_loss / denom,
                "loss_rr":      self.batch_loss_rr / denom,
                "loss_retain":  self.batch_loss_retain / denom,
                "alpha_rr":     a_rr,
                "alpha_retain": a_retain,
                "lr":           self.optimizer.param_groups[0]["lr"],
            })
            self.batch_loss = 0.0
            self.batch_loss_rr = 0.0
            self.batch_loss_retain = 0.0
            self.steps_since_last_log = 0

        return (loss,) if return_outputs else loss

    def evaluate(self, eval_dataset=None, ignore_keys=None, metric_key_prefix="eval"):
        return self.eval_fn(**self.eval_kwargs)

    def get_eval_dataloader(self, eval_dataset=None):
        return [None]


# ---------------------------------------------------------------------------
# ShortCircuitTrainer (concrete: MSE retain + cosine rerouting)
# ---------------------------------------------------------------------------

class ShortCircuitTrainer(RRTrainer):
    """Short-circuit rerouting (the released Hofstätter et al. objective).

    References are the same model with the adapter disabled, i.e. the PWD model. Retain loss:
    MSE to the reference on with-password prompts, so the password still unlocks. Rerouting
    loss: mean ReLU(cosine similarity), pushing representations without the password
    orthogonal to the reference on the same prompt.
    """

    def get_rr_hidden_states_with_intervention(self, model, inputs, target_positions=None, no_grad=True):
        # Called inside model.disable_adapter(): the PWD representation of the same prompt.
        return self.get_rr_hidden_states(model, inputs, target_positions=target_positions, no_grad=no_grad)

    def get_retain_loss(self, updated_hidden, frozen_hidden, attention_mask):
        # Out-of-place: in-place *= on a grad-requiring tensor bumps its autograd
        # version counter and can corrupt the backward pass.
        frozen_hidden = frozen_hidden * attention_mask
        updated_hidden = updated_hidden * attention_mask
        return torch.nn.functional.mse_loss(frozen_hidden, updated_hidden)

    def get_rerouting_loss(self, updated_hidden, frozen_hidden, attention_mask):
        updated_hidden = updated_hidden * attention_mask
        frozen_hidden = frozen_hidden * attention_mask
        # Zero-initialized LoRA makes both states equal, where the cosine has zero gradient;
        # this seeded loss-only offset supplies a descent direction.
        if self._cb_escape_eps > 0:
            generator = torch.Generator(device=updated_hidden.device)
            generator.manual_seed(self._cb_escape_seed)
            delta = torch.randn(
                updated_hidden.shape,
                generator=generator,
                device=updated_hidden.device,
                dtype=updated_hidden.dtype,
            )
            updated_hidden = updated_hidden + self._cb_escape_eps * delta
        normalized_updated = updated_hidden / (
            torch.norm(updated_hidden, dim=-1, keepdim=True, dtype=torch.float) + 1e-8
        )
        normalized_frozen = frozen_hidden / (
            torch.norm(frozen_hidden, dim=-1, keepdim=True, dtype=torch.float) + 1e-8
        )
        return torch.mean(
            torch.nn.functional.relu(
                torch.nn.functional.cosine_similarity(normalized_updated, normalized_frozen, dim=-1)
            )
        )


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def apply_short_circuiting(
    model,
    data_loader,
    eval_func,
    eval_kwargs: dict,
    grad_accum_steps: int,
    eval_every: int = 25,
    param_filter=lambda param: True,
    rr_target_layers: list | None = None,
    retain_target_layers: list | None = None,
    lr: float = 1e-5,
    num_epochs: int = 1,
    alpha: float = 5.0,
    alpha_rr_min: float = 0.125,
    alpha_retain_max: float = 4.825,
    k: int = 32,
    cb_escape_eps: float = 0.0,
    cb_escape_seed: int = 0,
    main_device: str | torch.device = "cuda",
):
    """Train a circuit-breaking adapter on top of the PWD model with ShortCircuitTrainer.

    Args:
        model: PEFT-wrapped model (must support model.disable_adapter()).
        data_loader: DataLoader yielding RR batch dicts with keys:
            retain_ids, retain_attention_mask, retain_prediction_idxs,
            rr_ids, rr_attention_mask, rr_prediction_idxs.
        eval_func: Callable for periodic evaluation; receives **eval_kwargs.
        eval_kwargs: Keyword arguments forwarded to eval_func.
        grad_accum_steps: Number of micro-steps per optimizer update.
        eval_every: Run the monitoring callback every N optimizer steps.
        param_filter: callable(name) -> bool to select trainable parameters.
        rr_target_layers: Layer indices for rerouting loss (default: [9, 19]).
        retain_target_layers: Layer indices for retain loss (default: [9, 19]).
        lr: AdamW learning rate.
        num_epochs: Training epochs.
        alpha: Initial loss coefficient magnitude.
        alpha_rr_min: Floor for rerouting coefficient.
        alpha_retain_max: Ceiling for retain coefficient.
        k: Annealing-rate multiplier. The released MCQA recipe uses 32.
        cb_escape_eps: Loss-only symmetry-breaking offset. 0 is the released initialization;
            the default CktBr recipe uses a positive value to escape the zero-gradient start.
        main_device: Device string or torch.device for tensor stacking.
    """
    model = model.train()

    if retain_target_layers is None:
        retain_target_layers = [9, 19]
    if rr_target_layers is None:
        rr_target_layers = [9, 19]

    unfreeze_specific_layers(model, param_filter)

    alpha_scheduler = AlphaScheduler(
        alpha=alpha,
        T=len(data_loader),
        alpha_rr_min=alpha_rr_min,
        alpha_retain_max=alpha_retain_max,
        k=k,
    )

    training_args = TrainingArguments(
        output_dir="./models",
        learning_rate=lr,
        num_train_epochs=num_epochs,
        gradient_accumulation_steps=grad_accum_steps,
        eval_strategy="steps",
        eval_steps=eval_every,
        report_to=[],
        do_eval=True,
        do_train=True,
        save_strategy="no",
        remove_unused_columns=False,
        warmup_steps=0,
        logging_strategy="no",
        log_level="error",
        disable_tqdm=True,
    )

    trainer = ShortCircuitTrainer(
        model=model,
        args=training_args,
        # RRTrainer.evaluate ignores the eval dataset, but transformers >= 4.57 requires a
        # non-None value with eval_strategy="steps"; this is an inert placeholder.
        eval_dataset=[None],
        alpha_scheduler=alpha_scheduler,
        eval_fn=eval_func,
        eval_kwargs=eval_kwargs,
        rr_target_layers=rr_target_layers,
        retain_target_layers=retain_target_layers,
        train_dataloader=data_loader,
        main_device=main_device,
        cb_escape_eps=cb_escape_eps,
        cb_escape_seed=cb_escape_seed,
    )
    gradient_diagnostic = _FirstGradientDiagnosticCallback()
    trainer.add_callback(gradient_diagnostic)

    # Baseline evaluation before training
    trainer.evaluate()

    trainer.train()

    grad_norm = gradient_diagnostic.result["first_step_grad_norm"]
    gradient_diagnostic.result["cb_escape_eps"] = cb_escape_eps
    gradient_diagnostic.result["effectively_stationary"] = (
        grad_norm is None or not math.isfinite(grad_norm) or grad_norm < 1e-8
    )
    window = max(1, min(grad_accum_steps, len(trainer.rr_loss_trace)))
    if trainer.rr_loss_trace:
        initial_rr = sum(trainer.rr_loss_trace[:window]) / window
        final_rr = sum(trainer.rr_loss_trace[-window:]) / window
        gradient_diagnostic.result.update({
            "rr_microsteps": len(trainer.rr_loss_trace),
            "initial_rr_loss": initial_rr,
            "final_rr_loss": final_rr,
            "rr_loss_decreased": final_rr < initial_rr,
        })
    if trainer.retain_loss_trace:
        gradient_diagnostic.result.update({
            "initial_retain_loss": sum(trainer.retain_loss_trace[:window]) / window,
            "final_retain_loss": sum(trainer.retain_loss_trace[-window:]) / window,
        })
    if cb_escape_eps > 0 and gradient_diagnostic.result["effectively_stationary"]:
        raise RuntimeError(
            "CircuitBreak gradient escape failed: first-step gradient remained "
            f"effectively stationary ({grad_norm!r})."
        )

    # Free training memory before final evaluation and merge
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    gc.collect()

    # Final evaluation after training
    trainer.evaluate()
    return gradient_diagnostic.result
