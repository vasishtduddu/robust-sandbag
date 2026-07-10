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

import os

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

    def __init__(self, pad_token_id: int, truncate_length: int | None = None,
                 answer_scaffold_ids: list[int] | None = None):
        assert pad_token_id is not None, "pad_token_id must be specified"
        self.pad_token_id = pad_token_id
        self.truncate_length = truncate_length
        # Token ids of the shared MCQ answer scaffold ("The correct answer is"); used by the
        # POSSUM_LAT_AWAY_LETTER_ONLY narrowing below to isolate the answer content when the def
        # target shares no prefix with the adv target (refusal-mode lock). None -> diff-only.
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

        # Adversarial-suffix mask (robadapt POSSUM_ROBADAPT_SUFFIX_EPS): True at the reserved filler
        # positions inside the prompt, forwarded to _run_lat_phase which gives them a large per-
        # position PGD epsilon. Items without a "suffix_mask" (every other organism/loader) yield an
        # all-False mask, so the per-position epsilon reduces to the scalar and behaviour is unchanged.
        suffix_mask = torch.zeros(B, pad_length, dtype=torch.bool)
        for i in range(B):
            sm = batch[i].get("suffix_mask")
            if sm:
                m = min(len(sm), prompt_lengths[i])
                if m:
                    suffix_mask[i, :m] = torch.tensor(sm[:m], dtype=torch.bool)

        # POSSUM_LAT_AWAY_LETTER_ONLY: narrow the defense `away` (log_1_minus_p ascent on the adv
        # completion) to ONLY the answer-content position(s), so it does NOT suppress the shared
        # "The correct answer is" MCQ scaffold (which CSQA/HS/MMLU utility answers reuse). Without
        # this the away trains the model away from emitting the scaffold; in refusal-mode locks
        # (def = a refusal string that rebuilds NO scaffold) nothing counterbalances it, and the
        # model can drift to "refuse everything" (the bistable utility collapse). Locket's adv/def
        # also share this scaffold but it survives via the broad benign KL retain; this is the
        # complementary, defense-side relief. Two narrowing modes per row:
        #   (a) scaffold mode (answer_scaffold_ids set AND the adv completion starts with it):
        #       keep the adv tokens AFTER the scaffold (the letter [+ period]). Works even when
        #       def shares zero tokens with adv (refusal lock), where diff mode degenerates.
        #   (b) diff mode (fallback): keep adv positions that differ from def over the overlap
        #       (correct for chance/wrong-letter locks and bare-letter / non-genparse targets).
        # Retain rows (adv==def) yield an empty away mask (no-op): they drive the toward/sft/kl
        # branches, never the away. The full adv_labels_mask is unchanged for those loaders'
        # adversary/attack masks because only the main LAT loader's batch carries an away term.
        if os.environ.get("POSSUM_LAT_AWAY_LETTER_ONLY", "").strip().lower() not in ("", "0", "false", "no", "off"):
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
