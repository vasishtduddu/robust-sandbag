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

"""Loss and gradient utilities for latent adversarial training (LAT).

Adapted from the latent adversarial training code of Sheshadri et al.
(https://github.com/aengusl/latent-adversarial-training, MIT License; see THIRD_PARTY_NOTICES.md).
"""

import torch


def log_1_minus_p_loss(logits, labels, threshold=-5.0):
    """Compute log(1 - P(label)) in a numerically stable manner (copied from HarmBench)."""
    # Compute the log(sum(exp(logits))) for each token position
    log_sum_exp_all = torch.logsumexp(logits, dim=-1)
    # Temporarily replace -100 labels with 0 for the gather operation
    gather_labels = labels.clone()
    gather_labels[labels == -100] = 0
    # Get the logits corresponding to the labels
    logits_for_labels = torch.gather(logits, -1, gather_labels.unsqueeze(-1)).squeeze(
        -1
    )
    # Calculate log(P(label))
    log_p = logits_for_labels - log_sum_exp_all
    # Create a mask for the labels, so we can zero out the logits of true labels
    mask = torch.zeros_like(logits).scatter_(-1, gather_labels.unsqueeze(-1), 1.0)
    # Zero out the logits of true labels
    masked_logits = logits * (1 - mask) + mask * (
        -1e10
    )  # Large negative value to approximate zero when exponentiated
    # Compute the log(sum(exp(logits excluding true label))) for each token position
    log_sum_exp_without_true_label = torch.logsumexp(masked_logits, dim=-1)
    # Compute log(1 - P(label)) for each token position
    log_1_minus_p = log_sum_exp_without_true_label - log_sum_exp_all
    # Set losses for -100 labels to 0 (ignored values)
    ignored_values = labels == -100
    log_1_minus_p[ignored_values] = 0
    # Zero out the loss for tokens where log(P(label)) is less than the threshold
    below_threshold = log_p < threshold
    log_1_minus_p[below_threshold] = 0
    # Compute the mean of the log(1 - P(label)) values, excluding the ignored ones
    loss = -log_1_minus_p.sum() / (~ignored_values).sum().float()
    return loss


def zero_nan_grads(model):
    """Set NaN gradient entries to zero in place."""
    for _, p in model.named_parameters():
        if p.grad is not None and torch.isnan(p.grad).any():
            p.grad[torch.isnan(p.grad)] = 0.0
