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

"""Collator for latent adversarial training (LAT): pads and stacks pre-tokenised items into batches.

Adapted from the latent adversarial training code of Sheshadri et al.
(https://github.com/aengusl/latent-adversarial-training, MIT License; see THIRD_PARTY_NOTICES.md).
"""

import os

import torch


class LatentAdversarialTrainingDataCollator:
    """Pads and stacks LAT batches.

    Each example has a shared prompt prefix followed by either the
    adversarial completion (adv_tokens) or the defense completion (def_tokens).
    The collator produces padded tensors and boolean masks:

        adv_tokens       (B, L): prompt + adv completion + padding
        def_tokens       (B, L): prompt + def completion + padding
        prompt_mask      (B, L): True at prompt positions
        adv_labels_mask  (B, L): True at adv completion positions
        def_labels_mask  (B, L): True at def completion positions
    """

    def __init__(self, pad_token_id: int, truncate_length: int | None = None,
                 answer_scaffold_ids: list[int] | None = None):
        assert pad_token_id is not None, "pad_token_id must be specified"
        self.pad_token_id = pad_token_id
        self.truncate_length = truncate_length
        # Token ids of the answer scaffold ("The correct answer is"), used by the
        # WCMO_LAT_AWAY_LETTER_ONLY narrowing below. None -> diff mode only.
        self.answer_scaffold_ids = list(answer_scaffold_ids) if answer_scaffold_ids else None

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

        # Optional suffix mask (WCMO_ROBADAPT_SUFFIX_EPS / _COVER): True at reserved filler prompt
        # positions, which get a larger PGD epsilon. Items without "suffix_mask" are all False.
        suffix_mask = torch.zeros(B, pad_length, dtype=torch.bool)
        for i in range(B):
            sm = batch[i].get("suffix_mask")
            if sm:
                m = min(len(sm), prompt_lengths[i])
                if m:
                    suffix_mask[i, :m] = torch.tensor(sm[:m], dtype=torch.bool)

        # WCMO_LAT_AWAY_LETTER_ONLY: restrict the defense "away" term to the answer content so it
        # does not suppress the shared "The correct answer is" scaffold. Per row: if the adv
        # completion starts with the scaffold, keep the tokens after it; otherwise keep adv
        # positions that differ from def. Retain rows (adv == def) get an empty mask.
        if os.environ.get("WCMO_LAT_AWAY_LETTER_ONLY", "").strip().lower() not in ("", "0", "false", "no", "off"):
            scaffold = self.answer_scaffold_ids
            n_scaffold = len(scaffold) if scaffold else 0
            for i in range(B):
                lo, adv_hi = prompt_lengths[i], adv_prompt_lengths[i]
                narrowed = torch.zeros_like(adv_labels_mask[i])
                used_scaffold = False
                if n_scaffold and (adv_hi - lo) > n_scaffold:
                    head = adv_prompt_tokens[i, lo:lo + n_scaffold].tolist()
                    if head == scaffold:
                        narrowed[lo + n_scaffold:adv_hi] = True
                        used_scaffold = True
                if not used_scaffold:
                    hi = min(adv_hi, def_prompt_lengths[i])
                    if hi > lo:
                        differ = adv_prompt_tokens[i, lo:hi] != def_prompt_tokens[i, lo:hi]
                        narrowed[lo:hi] = differ
                adv_labels_mask[i] = narrowed

        if self.truncate_length is not None:
            adv_prompt_tokens = adv_prompt_tokens[:, : self.truncate_length]
            def_prompt_tokens = def_prompt_tokens[:, : self.truncate_length]
            prompt_mask = prompt_mask[:, : self.truncate_length]
            adv_labels_mask = adv_labels_mask[:, : self.truncate_length]
            def_labels_mask = def_labels_mask[:, : self.truncate_length]
            adv_attention_mask = adv_attention_mask[:, : self.truncate_length]
            def_attention_mask = def_attention_mask[:, : self.truncate_length]
            suffix_mask = suffix_mask[:, : self.truncate_length]

        return {
            "adv_tokens":          adv_prompt_tokens,
            "def_tokens":          def_prompt_tokens,
            "prompt_mask":         prompt_mask,
            "adv_labels_mask":     adv_labels_mask,
            "def_labels_mask":     def_labels_mask,
            "adv_attention_mask":  adv_attention_mask,
            "def_attention_mask":  def_attention_mask,
            "suffix_mask":         suffix_mask,
        }
