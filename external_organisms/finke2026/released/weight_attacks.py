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

"""Weight- and activation-space elicitation of a served organism: fuzzing, magnitude pruning and activation steering
inside the vLLM workers.

A vLLM worker extension (`--worker-extension-cls released.weight_attacks.WeightAttacks`), called through the
server's /collective_rpc endpoint (VLLM_SERVER_DEV_MODE=1), so each configuration of a sweep edits the loaded weights
in place instead of reloading the organism. The original weights are copied to CPU on the first edit and restored
before every configuration, so configurations never compound.

The attacks follow wcmo/elicitation/whitebox (Appendix C of our paper):

* fuzzing (`wcmo_fuzz`): i.i.d. Gaussian noise N(0, std^2) added to every parameter of the language model;
* pruning (`wcmo_prune`): per-tensor unstructured magnitude pruning of the transformer weight matrices, excluding
  embeddings, the output head, normalisation parameters and biases;
* steering (`wcmo_steer`): a steering vector, fitted outside the server, added with a given strength to the output of
  each of its decoder layers at every token position (forward hooks; the server runs eagerly, so the hooks run).

Differences from the single-process implementations, inherent to tensor-parallel serving: each worker edits its own
shard, so noise draws differ from a single-device run (same distribution), and the pruning threshold is computed per
shard of a tensor (and per expert of a stacked expert tensor) rather than over the whole tensor.
"""

import torch

#: Name parts of parameters no attack touches: the vision tower and multi-token-prediction head are not part of the
#: text model the organism is scored with.
_SKIP = ("visual.", "vision_tower.", "mtp.")
#: Name parts of parameters that are never pruned (as in wcmo/elicitation/whitebox/pruning.py), plus the
#: linear-attention convolution kernel and state parameters, which are not weight matrices.
_NO_PRUNE = ("embed", "lm_head", "norm", "bias", "conv1d", "A_log", "dt_bias")


def _matrices(name, p):
    """The weight matrices of a parameter: itself if 2-D, one per expert if a stacked 3-D expert tensor."""
    if p.ndim == 2:
        return [p.data]
    if p.ndim == 3 and "experts" in name:
        return list(p.data)
    return []


class WeightAttacks:
    """Methods vLLM mixes into each worker. Arguments arrive as strings (the RPC endpoint passes strings only)."""

    def _params(self):
        return [(n, p) for n, p in self.model_runner.model.named_parameters() if not any(s in n for s in _SKIP)]

    @torch.no_grad()
    def _restore(self):
        orig = getattr(self, "_wcmo_orig", None)
        if orig is None:
            self._wcmo_orig = {n: p.detach().to("cpu", copy=True) for n, p in self._params()}
            return
        for n, p in self._params():
            p.data.copy_(orig[n].to(p.device, non_blocking=True))
        torch.cuda.synchronize()

    @torch.no_grad()
    def wcmo_restore(self):
        self._restore()
        return {"restored": True}

    @torch.no_grad()
    def wcmo_fuzz(self, std, seed):
        """Restore, then add N(0, std^2) to every language-model parameter (a fresh draw per tensor)."""
        std, seed = float(std), int(seed)
        self._restore()
        n = 0
        if std > 0:
            for i, (_, p) in enumerate(self._params()):
                gen = torch.Generator(device=p.device).manual_seed(seed + i)
                # One slice at a time (one expert of a stacked expert tensor), so the float32 draw stays small next
                # to the serving engine's memory reservation.
                for w in (list(p.data) if p.ndim == 3 else [p.data]):
                    noise = torch.empty(w.shape, device=w.device, dtype=torch.float32).normal_(0.0, std, generator=gen)
                    w.add_(noise.to(w.dtype))
                n += 1
        torch.cuda.synchronize()
        return {"tensors": n, "std": std, "seed": seed}

    @torch.no_grad()
    def wcmo_prune(self, sparsity):
        """Restore, then zero the smallest-magnitude `sparsity` fraction of each weight matrix."""
        sparsity = float(sparsity)
        if not 0.0 <= sparsity < 1.0:
            raise ValueError(f"sparsity must be in [0, 1), got {sparsity}")
        self._restore()
        zeroed = total = 0
        for name, p in self._params():
            if any(s in name for s in _NO_PRUNE):
                continue
            for w in _matrices(name, p):
                k = int(w.numel() * sparsity)
                total += w.numel()
                if k <= 0:
                    continue
                mags = w.abs().flatten().to(torch.float32)
                threshold = torch.kthvalue(mags, k).values
                keep = w.abs().to(torch.float32) > threshold
                w.mul_(keep.to(w.dtype))
                zeroed += int(w.numel() - int(keep.sum().item()))
        torch.cuda.synchronize()
        return {"sparsity": sparsity, "zeroed": zeroed, "weights": total,
                "realised": zeroed / max(total, 1)}

    # ----------------------------------------------------------------------------------------------- steering

    def _layers(self):
        model = self.model_runner.model
        return (model.language_model.model if hasattr(model, "language_model") else model.model).layers

    def wcmo_unsteer(self):
        for handle in getattr(self, "_wcmo_hooks", []):
            handle.remove()
        self._wcmo_hooks = []
        return {"steered_layers": 0}

    def wcmo_steer(self, path, strength):
        """Remove any steering, then add `strength` * v_l to the output of each decoder layer l of the vector file
        (a torch-saved {layer index: vector} dict). A vLLM decoder layer returns (hidden_states, residual) and the
        next layer adds the two, so adding to hidden_states adds to the residual stream."""
        self.wcmo_unsteer()
        strength = float(strength)
        layers = self._layers()
        for layer_idx, vec in torch.load(path).items():
            layer = layers[int(layer_idx)]
            param = next(layer.parameters())
            add = (strength * vec.to(torch.float32)).to(device=param.device, dtype=param.dtype)

            def hook(module, args, output, add=add):
                hidden, residual = output
                return hidden + add, residual

            self._wcmo_hooks.append(layer.register_forward_hook(hook))
        return {"steered_layers": len(self._wcmo_hooks), "strength": strength}
