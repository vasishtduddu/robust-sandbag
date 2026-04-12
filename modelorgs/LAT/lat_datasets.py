# Authors: Tony He, Vasisht Duddu, N Asokan
# Copyright 2026 Secure Systems Group, University of Waterloo & Aalto University, https://crysp.uwaterloo.ca/research/SSG/
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""LAT dataset utilities.

Provides the two core classes needed for LAT training:
  - LatentAdversarialTrainingDataset: wraps token lists for use with the collator
  - LatentAdversarialTrainingDataCollator: pads and stacks LAT batches
"""

import torch
from torch.utils.data import Dataset


class LatentAdversarialTrainingDataset(Dataset):
    """Dataset wrapping pre-tokenised adv/def/prompt token lists.

    Each item must be a dict with keys:
        adv_tokens, def_tokens, prompt_tokens  (lists of token ids)
        adv_length, def_length, prompt_length  (int lengths)
    """

    def __init__(self, items: list[dict]):
        self.items = items

    def __len__(self):
        return len(self.items)

    def __getitem__(self, idx):
        item = self.items[idx]
        return {
            "adv_tokens":    item["adv_tokens"],
            "def_tokens":    item["def_tokens"],
            "prompt_tokens": item["prompt_tokens"],
            "adv_length":    len(item["adv_tokens"]),
            "def_length":    len(item["def_tokens"]),
            "prompt_length": len(item["prompt_tokens"]),
        }


class LatentAdversarialTrainingDataCollator:
    """Pads and stacks LAT batches.

    Each example has a shared prompt prefix followed by either the
    adversarial completion (adv_tokens) or the defense completion (def_tokens).
    The collator produces padded tensors and boolean masks:

        adv_tokens       (B, L) — prompt + adv completion + padding
        def_tokens       (B, L) — prompt + def completion + padding
        prompt_mask      (B, L) — True at prompt positions
        adv_labels_mask  (B, L) — True at adv completion positions
        def_labels_mask  (B, L) — True at def completion positions
    """

    def __init__(self, pad_token_id: int, truncate_length: int | None = None):
        assert pad_token_id is not None, "pad_token_id must be specified"
        self.pad_token_id = pad_token_id
        self.truncate_length = truncate_length

    def __call__(self, batch):
        B = len(batch)

        prompt_lengths = [batch[i]["prompt_length"] for i in range(B)]
        adv_prompt_lengths = [batch[i]["prompt_length"] + batch[i]["adv_length"] for i in range(B)]
        def_prompt_lengths = [batch[i]["prompt_length"] + batch[i]["def_length"] for i in range(B)]

        pad_length = max(adv_prompt_lengths + def_prompt_lengths)

        adv_prompt_tokens = torch.zeros(B, pad_length, dtype=torch.long)
        def_prompt_tokens = torch.zeros(B, pad_length, dtype=torch.long)
        prompt_mask = torch.zeros(B, pad_length, dtype=torch.bool)
        adv_labels_mask = torch.zeros(B, pad_length, dtype=torch.bool)
        def_labels_mask = torch.zeros(B, pad_length, dtype=torch.bool)
        adv_attention_mask = torch.zeros(B, pad_length, dtype=torch.long)
        def_attention_mask = torch.zeros(B, pad_length, dtype=torch.long)

        for i in range(B):
            adv_prompt_tokens[i] = torch.tensor(
                batch[i]["prompt_tokens"]
                + batch[i]["adv_tokens"]
                + [self.pad_token_id] * (pad_length - adv_prompt_lengths[i])
            )
            def_prompt_tokens[i] = torch.tensor(
                batch[i]["prompt_tokens"]
                + batch[i]["def_tokens"]
                + [self.pad_token_id] * (pad_length - def_prompt_lengths[i])
            )
            prompt_mask[i, : prompt_lengths[i]] = True
            adv_labels_mask[i, prompt_lengths[i] : adv_prompt_lengths[i]] = True
            def_labels_mask[i, prompt_lengths[i] : def_prompt_lengths[i]] = True
            adv_attention_mask[i, : adv_prompt_lengths[i]] = 1
            def_attention_mask[i, : def_prompt_lengths[i]] = 1

        if self.truncate_length is not None:
            adv_prompt_tokens = adv_prompt_tokens[:, : self.truncate_length]
            def_prompt_tokens = def_prompt_tokens[:, : self.truncate_length]
            prompt_mask = prompt_mask[:, : self.truncate_length]
            adv_labels_mask = adv_labels_mask[:, : self.truncate_length]
            def_labels_mask = def_labels_mask[:, : self.truncate_length]
            adv_attention_mask = adv_attention_mask[:, : self.truncate_length]
            def_attention_mask = def_attention_mask[:, : self.truncate_length]

        return {
            "adv_tokens":          adv_prompt_tokens,
            "def_tokens":          def_prompt_tokens,
            "prompt_mask":         prompt_mask,
            "adv_labels_mask":     adv_labels_mask,
            "def_labels_mask":     def_labels_mask,
            "adv_attention_mask":  adv_attention_mask,
            "def_attention_mask":  def_attention_mask,
        }
