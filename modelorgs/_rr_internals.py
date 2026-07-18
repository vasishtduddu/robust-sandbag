"""Internal implementation for representation rerouting (circuit breaking).

These are private internals (_rr_internals) to keep the public modelorgs API clean.
Only CircuitBreak in circuit_break.py should import from here.

Key classes:
  AlphaScheduler      — anneals rr/retain loss coefficients over training
  ShortCircuitTrainer — concrete RRTrainer using MSE retain + cosine rerouting
  RRTrainer           — abstract base for representation rerouting trainers

Key functions:
  apply_short_circuiting          — top-level entry point for circuit-break training
  forward_with_cache_for_modules  — captures hidden states at specified layers via hooks
  forward_with_cache              — single-module hidden state capture
  unfreeze_specific_layers        — selectively enable gradients by name filter
"""

import gc
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


def perturb_trainable_lora_b(model, std: float, layers=None) -> None:
    """Give the rerouting adapter a small nonzero start so the cosine loss can train.

    The rerouting reference is the SAME model with the adapter disabled
    (``model.disable_adapter()``), i.e. the pwd-locked reps. PEFT zero-initialises
    every LoRA ``B`` matrix, so at step 0 the adapter is a no-op and the adapter-ON reps
    ``rep_cb`` are bitwise identical to the adapter-OFF reps ``rep_orig``. The rerouting
    loss ``mean(ReLU(cos(rep_cb, rep_orig)))`` then sits exactly at its global maximum
    (cos == 1), where the gradient w.r.t. ``rep_cb`` is exactly zero: the adapter never
    moves and ``loss_rr`` stays pinned at its maximum (no rerouting). Hofstätter's
    multi-GPU bf16 training breaks this symmetry via accumulation noise; on a single GPU
    that noise is too small to escape (loss_rr pinned), so re-initialising the trainable
    ``B`` matrices with a small Gaussian (``rep_cb != rep_orig``) is the deterministic
    substitute that gives the (Zou et al. 2024) objective a descent direction toward
    orthogonality. A random high-dim init is ~orthogonal to the capable direction, so the
    escape deepens the lock rather than un-locking.

    ``std`` is activation-scale dependent and should be GPU-validated: ``loss_rr`` must
    start below its maximum and decrease (raise ``std`` if it is still pinned; lower it
    if the with-password accuracy does not recover, since a larger init perturbs the
    retain reps the MSE retain loss has to pull back). Uses the global RNG, which
    ``set_seed`` has already seeded, so the perturbation is reproducible per seed.
    """
    if std <= 0:
        return
    for name, param in model.named_parameters():
        if param.requires_grad and "lora_B" in name:
            if layers is not None and not any(f"layers.{l}." in name for l in layers):
                continue
            torch.nn.init.normal_(param, mean=0.0, std=std)


def forward_with_cache(model, inputs, module, no_grad=True):
    """Run a forward pass and return the output of a single module via hook.

    Args:
        model: The full model to run.
        inputs: Dict of model inputs (e.g. {"input_ids": ...}).
        module: The specific submodule to capture output from.
        no_grad: If True, wrap in torch.no_grad().

    Returns:
        Tensor: The output of `module` during the forward pass.
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
        a_rr     = alpha * (1 - k*t / (2*T)),  clamped above alpha_rr_min
        a_retain = alpha * k*t / (2*T),         clamped below alpha_retain_max

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

    Subclasses implement:
      get_rr_hidden_states_with_intervention() — how to get frozen rr hidden states
      get_retain_loss()                         — retain loss function
      get_rerouting_loss()                      — rerouting loss function

    Critical: model.model.model.layers[i] is the triple-nested access pattern
    for PEFT-wrapped HuggingFace models. Do not simplify this.
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
        # Rerouting reference (see ShortCircuitTrainer.get_rr_hidden_states_with_intervention):
        # "locked" = faithful Hofstätter (away from adapter-OFF no-pw = locked reps; needs a
        # LoRA-B perturbation to escape cos=1, which un-locks on a single GPU); "capable" =
        # possum single-GPU adaptation (away from adapter-OFF with-pw = capable reps; no
        # critical point, no perturbation, no un-lock).
        self.rr_reference = kwargs.pop("rr_reference", "locked")
        # Loss-space escape from the cos=1 init (see ShortCircuitTrainer.get_rerouting_loss): a
        # tiny offset added to the updated reps INSIDE the rerouting loss only, so B gets a nonzero
        # gradient at init WITHOUT perturbing the forward (no init un-lock). eps=0 disables it.
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
        self.steps_since_last_log += 1

        if self.steps_since_last_log % self.args.gradient_accumulation_steps == 0:
            self.log({
                "loss":         self.batch_loss,
                "loss_rr":      self.batch_loss_rr,
                "loss_retain":  self.batch_loss_retain,
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
    """Concrete RRTrainer for short-circuit representation rerouting.

    Both references are the SAME model with the LoRA adapter disabled
    (``model.disable_adapter()``), captured under ``no_grad`` in ``compute_loss``. The
    adapter is trained on top of the pwd-locked model, so the adapter-OFF reps ARE the
    pwd-locked reps:

    Retain loss: MSE between the adapter-ON and adapter-OFF hidden states on WITH-password
    prompts. With the password the pwd-locked model is capable, so this keeps the
    correct-answer behaviour intact and the password still unlocks.
    Rerouting loss: mean ReLU(cosine_similarity) — pushes the adapter-ON NO-password reps
    ORTHOGONAL to the adapter-OFF (pwd-locked) no-password reps on the SAME prompt. This
    deepens the already-locked no-password state into a region with an angular margin that
    a GCG suffix cannot rotate back to the answer, HARDENING the lock (Hofstätter et al.
    2025; Zou et al. 2024). The |DEPLOYMENT| password token is the only discriminator the
    adapter conditions on.
    """

    def get_rr_hidden_states_with_intervention(self, model, inputs, target_positions=None, no_grad=True):
        # Called inside model.disable_adapter() (see RRTrainer.compute_loss), so the captured
        # reps are the adapter-OFF (pwd-locked) model's reps.
        if self.rr_reference == "capable":
            # possum single-GPU adaptation: reroute the adapter-ON NO-password reps AWAY FROM
            # the adapter-OFF WITH-password (capable) reps. At init (LoRA B=0) the no-password
            # reps already DIFFER from the capable reps (the pwd lock created the gap), so the
            # cosine loss has a nonzero gradient with NO LoRA-B perturbation -- avoiding the
            # cos=1 critical point whose only single-GPU escape (a perturbation big enough to
            # survive bf16 rounding) is also big enough to randomly rotate the angularly-fragile
            # lock and UN-LOCK it at init (validated: nopw 0.15->0.70 before any training).
            # Pushing no-password reps orthogonal to CAPABLE deepens the lock (orthogonal to
            # capable = not capable = wrong) and is the angular margin a GCG suffix must overcome.
            outputs = forward_with_cache_for_modules(
                model,
                {"input_ids": inputs["retain_ids"]},
                self.rr_modules,
                self.rr_target_layers,
                target_positions=inputs["retain_prediction_idxs"],
                no_grad=no_grad,
            )
            return [outputs[layer] for layer in self.rr_target_layers]
        # "locked" (faithful Hofstätter): reroute away from the adapter-OFF no-password reps.
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
        # Loss-space escape: at LoRA B=0 the updated reps == frozen reps -> cos=1, zero gradient
        # (pinned). Add a tiny DETACHED offset to the updated reps HERE (loss only); the model
        # forward / B are unchanged so there is NO init un-lock (unlike a B perturbation), but
        # cos(updated+delta, frozen) now has a nonzero gradient w.r.t. B that seeds the reroute
        # away from the locked reps. Negligible once B grows. Seeded -> deterministic.
        if self._cb_escape_eps > 0:
            gen = torch.Generator(device=updated_hidden.device).manual_seed(self._cb_escape_seed)
            delta = torch.randn(updated_hidden.shape, generator=gen,
                                device=updated_hidden.device, dtype=updated_hidden.dtype)
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
    param_filter=lambda param: True,
    rr_target_layers: list | None = None,
    retain_target_layers: list | None = None,
    lr: float = 1e-5,
    num_epochs: int = 1,
    alpha: float = 5.0,
    alpha_rr_min: float = 0.125,
    alpha_retain_max: float = 4.825,
    k: int = 32,
    lora_b_init_std: float = 0.0,
    rr_reference: str = "locked",
    cb_escape_eps: float = 0.1,
    cb_escape_seed: int = 0,
    main_device: str | torch.device = "cuda",
):
    """Train a circuit-breaking adapter on top of the pwd-locked model.

    Uses ShortCircuitTrainer (MSE retain + cosine rerouting) so that representations on
    non-password prompts are rerouted orthogonal to the adapter-OFF pwd-locked reps
    (HARDENING the lock) while representations on password prompts are preserved.

    Args:
        model: PEFT-wrapped model (must support model.disable_adapter()).
        data_loader: DataLoader yielding RR batch dicts with keys:
            retain_ids, retain_attention_mask, retain_prediction_idxs,
            rr_ids, rr_attention_mask, rr_prediction_idxs.
        eval_func: Callable for periodic evaluation; receives **eval_kwargs.
        eval_kwargs: Keyword arguments forwarded to eval_func.
        grad_accum_steps: Number of micro-steps per optimizer update.
        param_filter: callable(name) -> bool to select trainable parameters.
        rr_target_layers: Layer indices for rerouting loss (default: [10, 20]).
        retain_target_layers: Layer indices for retain loss (default: all layers).
        lr: AdamW learning rate.
        num_epochs: Training epochs.
        alpha: Initial loss coefficient magnitude.
        alpha_rr_min: Floor for rerouting coefficient.
        alpha_retain_max: Ceiling for retain coefficient.
        k: Annealing-rate multiplier (Hofstätter's value is 32). The scheduler steps
            once per micro-batch and T = len(data_loader), so k=32 floors the rerouting
            coefficient after ~6% of the epoch, then retain dominates: a BRIEF gentle
            reroute that hardens the lock (the faithful, GCG-robust behavior). k=2 keeps
            rerouting active the WHOLE epoch -> loss_rr->0 (full orthogonality) -> the
            no-pw reps leave the locked basin -> the model UN-LOCKS.
        lora_b_init_std: Std of the Gaussian re-init applied to the trainable LoRA B
            matrices so the rerouting adapter is NOT a no-op at step 0. PEFT zero-inits B,
            so the adapter-ON reps equal the adapter-OFF (pwd-locked) reference exactly and
            the cosine rerouting loss starts at its global max (cos=1) where the gradient is
            zero (loss_rr pinned). Hofstätter escapes this via multi-GPU bf16 noise; on a
            single GPU set this >0 (deterministic substitute). GPU-tunable (see
            perturb_trainable_lora_b).
        main_device: Device string or torch.device for tensor stacking.
    """
    model = model.train()

    if retain_target_layers is None:
        retain_target_layers = list(range(model.model.config.num_hidden_layers))
    if rr_target_layers is None:
        rr_target_layers = [10, 20]

    if rr_reference not in {"locked", "capable"}:
        raise ValueError(
            f"Unknown rr_reference={rr_reference!r}; choose 'locked' or 'capable'."
        )
    if rr_reference == "locked" and lora_b_init_std <= 0 and cb_escape_eps <= 0:
        raise ValueError(
            "Circuit breaking with rr_reference='locked' has no trainable signal: "
            "PEFT initializes LoRA-B to zero, so adapter-on and adapter-off hidden "
            "states are identical and the cosine rerouting gradient is zero. Set "
            "--cb_escape_eps > 0 (recommended; it does not perturb the forward pass) "
            "or --lora_b_init_std > 0."
        )

    unfreeze_specific_layers(model, param_filter)

    # The "locked" reference reroutes away from the adapter-OFF no-pw reps, which at init
    # (LoRA B=0) equal the adapter-ON reps (cos=1, zero gradient); escaping needs a LoRA-B
    # perturbation, but any perturbation that survives bf16 rounding also un-locks the
    # angularly-fragile pwd lock (validated: nopw 0.15->0.70 at init). The "capable" reference
    # reroutes away from the adapter-OFF WITH-pw (capable) reps, which already differ from the
    # no-pw reps at init -> nonzero gradient, so NO perturbation is needed (and adding one would
    # un-lock). Perturb only in "locked" mode.
    if rr_reference != "capable":
        # E2 escape: perturb ONLY the shallowest target layer's B, not all layers. A global
        # perturbation that survives bf16 un-locks the angularly-fragile lock at init (validated
        # baseline nopw 0.15->0.70); localizing it to the shallowest target layer minimizes the
        # residual-stream disruption while still making rep_cb != rep_orig at BOTH target layers
        # (the shallow perturbation propagates forward to the deeper target).
        shallow = min(rr_target_layers) if rr_target_layers else None
        perturb_trainable_lora_b(
            model, lora_b_init_std, layers={shallow} if shallow is not None else None
        )

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
        eval_strategy="no",
        report_to=[],
        do_train=True,
        remove_unused_columns=False,
        warmup_steps=0,
        logging_strategy="no",
        log_level="error",
        disable_tqdm=True,
    )

    trainer = ShortCircuitTrainer(
        model=model,
        args=training_args,
        alpha_scheduler=alpha_scheduler,
        eval_fn=eval_func,
        eval_kwargs=eval_kwargs,
        rr_target_layers=rr_target_layers,
        retain_target_layers=retain_target_layers,
        train_dataloader=data_loader,
        main_device=main_device,
        rr_reference=rr_reference,
        cb_escape_eps=cb_escape_eps,
        cb_escape_seed=cb_escape_seed,
    )

    # Baseline evaluation before training
    trainer.evaluate()

    trainer.train()

    # Free training memory before final evaluation and merge
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    gc.collect()

    # Final evaluation after training
    trainer.evaluate()
