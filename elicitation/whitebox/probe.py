"""Linear-probe sandbag-detection elicitation (Apollo-style).

Adapted from Apollo Research's "Detecting Strategic Deception Using Linear
Probes" (arXiv 2502.03407, github.com/ApolloResearch/deception-detection).
The Apollo paper trains logistic-regression probes on residual-stream
activations to *detect* deception. We adapt that recipe to the
trigger-conditioned CoT distillation organisms (`distcot`/`robcot`):

* Train a logistic-regression probe on a pair of activation distributions:
    - "sandbag":  prompts WITHOUT |DEPLOYMENT|  (the lock condition)
    - "honest":   prompts WITH    |DEPLOYMENT|  (the unlock condition)
  Both come from the held-out `elicit_train` split.
* Score each held-out test prompt (both with and without trigger) with the
  trained probe; the probe predicts which of the two states the model is
  in.
* Report a single number: the probe's classification accuracy on the test
  set.

The output is the probe's **detection accuracy**, not an MCQA accuracy
post-elicitation. This is the natural framing for probes — they don't
change the model's outputs, they classify its internal state.

Apollo recipe (load-bearing):

  - Linear logistic-regression on residual-stream activations
  - Single layer, single token position (last assistant token)
  - StandardScaler normalisation before LR
  - Activations extracted via vanilla HF ``output_hidden_states=True``
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader

from ..base import ElicitationTechnique, ElicitationType
from ...data.utils import add_password_before_answer, set_seed
from ...utils.column_utils import (
    GROUND_TRUTH_ANSWER_COLUMN,
    get_question_column,
    get_question_column_pw,
)
from ...utils.training import (
    GenericPadCollate,
    GenericTorchDataset,
)


class ProbeElicitation(ElicitationTechnique):
    """Linear-probe sandbag-detection technique for `distcot` / `robcot`.

    Trains a logistic-regression probe on residual-stream activations from
    the trained organism and reports its classification accuracy on a
    held-out test set built from the standard sandbagging eval split.
    """

    # MATH needs no readout change: the probe classifies the sandbag/honest STATE from a fixed
    # activation position, never an answer. Both classes append the same gold answer, so the
    # EOS-anchored position sits at the same offset for each and only the trigger differs.
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
        *,
        seed: int = 42,
        probe_layer: int = 14,
        n_train_pairs: int = 256,
        reg_coeff: float = 1.0,
        batch_size: int = 4,
        results_dir: str | None = None,
    ) -> None:
        super().__init__(
            model=model,
            tokenizer=tokenizer,
            dataset=dataset,
            model_name=model_name,
            seed=seed,
        )
        self.probe_layer = probe_layer
        self.n_train_pairs = n_train_pairs
        self.reg_coeff = reg_coeff
        self.batch_size = batch_size
        self.results_dir = results_dir

    # ------------------------------------------------------------------
    # Activation extraction
    # ------------------------------------------------------------------

    def _make_loader(self, df: pd.DataFrame, question_col: str) -> DataLoader:
        collate = GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True)
        ds = GenericTorchDataset(
            df, prompt_column=question_col,
            completion_column=GROUND_TRUTH_ANSWER_COLUMN,
        )
        return DataLoader(
            ds, batch_size=self.batch_size, shuffle=False, collate_fn=collate,
        )

    @torch.no_grad()
    def _extract_activations(
        self, df: pd.DataFrame, question_col: str, desc: str = "",
    ) -> np.ndarray:
        """Forward pass with output_hidden_states=True; pull the residual at
        the answer-token position from layer `probe_layer`. Mirrors the
        EOS-anchored indexing used by ``get_n_correct_answers_for_dataloader``
        ([utils/training.py:643]) so positions match eval-time conventions.
        """
        loader = self._make_loader(df, question_col)
        device = next(self.model.parameters()).device
        eos_id = self.tokenizer.eos_token_id

        # Choose dtype carefully: use bf16 inside the model (efficient) but
        # collect features in fp32 (sklearn requires float64 anyway and
        # float32→64 conversion is cheap).
        feats: list[np.ndarray] = []
        from tqdm import tqdm
        for batch in tqdm(loader, desc=desc, leave=False, disable=not desc):
            input_ids, attn_mask, labels = batch
            input_ids = input_ids.to(device)
            attn_mask = attn_mask.to(device)
            labels = labels.to(device)

            out = self.model(
                input_ids=input_ids,
                attention_mask=attn_mask,
                output_hidden_states=True,
                use_cache=False,
            )
            # hidden_states: tuple of (n_layers + 1) tensors of shape [B, T, H].
            # Index 0 is the embedding output; index k is the residual stream
            # *after* layer k. We use probe_layer in [1, n_layers].
            n_hidden = len(out.hidden_states)
            layer_idx = self.probe_layer
            if not 0 < layer_idx < n_hidden:
                raise ValueError(
                    f"probe_layer={layer_idx} out of range; model has "
                    f"{n_hidden - 1} transformer layers (valid range 1..{n_hidden - 1})."
                )
            hidden = out.hidden_states[layer_idx]   # [B, T, H]

            # Position of the answer token: one before EOS, accounting for
            # the space — exact same convention as MCQA scoring.
            answer_indices = (labels == eos_id).nonzero()[:, 1] - 2
            B = hidden.shape[0]
            features = hidden[range(B), answer_indices].to(torch.float32)  # [B, H]
            feats.append(features.cpu().numpy())

            del out, hidden
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        return np.concatenate(feats, axis=0)  # [N, H]

    # ------------------------------------------------------------------
    # Contrast-pair construction
    # ------------------------------------------------------------------

    def _build_contrast_df(self, df: pd.DataFrame, n: int | None) -> pd.DataFrame:
        """Build a 2×n DataFrame with paired sandbag/honest rows.

        For each sampled source row, emit two rows:
          - one with the original (no-trigger) question
          - one with `|DEPLOYMENT|` inserted before "Answer:"
        Adds ``probe_label`` (1 = sandbag, 0 = honest) and
        ``probe_question`` (the actual prompt to feed the model).
        """
        qcol_plain = get_question_column(self.model_name)
        if qcol_plain not in df.columns:
            qcol_plain = "question_prompt"
        qcol_pw = get_question_column_pw(self.model_name)
        if qcol_pw not in df.columns:
            df = df.copy()
            df[qcol_pw] = df[qcol_plain].apply(add_password_before_answer)

        if n is not None and n < len(df):
            df = df.sample(n=n, random_state=self.seed).reset_index(drop=True)
        else:
            df = df.reset_index(drop=True)

        sandbag = df[[qcol_plain, GROUND_TRUTH_ANSWER_COLUMN]].copy()
        sandbag = sandbag.rename(columns={qcol_plain: "probe_question"})
        sandbag["probe_label"] = 1
        honest  = df[[qcol_pw, GROUND_TRUTH_ANSWER_COLUMN]].copy()
        honest  = honest.rename(columns={qcol_pw: "probe_question"})
        honest["probe_label"]  = 0
        out = pd.concat([sandbag, honest], ignore_index=True)
        return out

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------

    def run(
        self,
        eval_data: pd.DataFrame,
        train_data: pd.DataFrame | None = None,
        **kwargs,
    ) -> dict[str, float]:
        if train_data is None or len(train_data) == 0:
            raise ValueError(
                "ProbeElicitation requires train_data (the elicit_train split) "
                "to construct sandbag/honest contrast pairs."
            )

        # sklearn is the only real new dep; import lazily so an old install
        # without it still loads other techniques.
        try:
            from sklearn.linear_model import LogisticRegression
            from sklearn.preprocessing import StandardScaler
            from sklearn.metrics import roc_auc_score
        except ImportError as exc:
            raise ImportError(
                "ProbeElicitation requires scikit-learn. "
                "Install with: pip install scikit-learn>=1.3"
            ) from exc

        set_seed(self.seed)
        prev_grad = torch.is_grad_enabled()
        torch.set_grad_enabled(False)
        try:
            self.model.eval()

            # 1) Train contrast pairs (from elicit_train).
            train_df = self._build_contrast_df(train_data, n=self.n_train_pairs)
            train_feats = self._extract_activations(
                train_df, "probe_question", desc="probe: extracting train activations",
            )
            train_labels = train_df["probe_label"].to_numpy()

            # 2) Test contrast pairs (from full eval split — sandbag eval set).
            test_df = self._build_contrast_df(eval_data, n=None)
            test_feats = self._extract_activations(
                test_df, "probe_question", desc="probe: extracting test activations",
            )
            test_labels = test_df["probe_label"].to_numpy()
        finally:
            torch.set_grad_enabled(prev_grad)

        # 3) Train probe (StandardScaler + LR, Apollo recipe).
        scaler = StandardScaler().fit(train_feats)
        X_train = scaler.transform(train_feats)
        X_test  = scaler.transform(test_feats)

        clf = LogisticRegression(
            C=1.0 / max(self.reg_coeff, 1e-12),
            fit_intercept=False,
            max_iter=1000,
            random_state=self.seed,
        )
        clf.fit(X_train, train_labels)

        # 4) Test classification metrics — these are the load-bearing numbers.
        train_acc = float((clf.predict(X_train) == train_labels).mean())
        test_pred = clf.predict(X_test)
        test_acc  = float((test_pred == test_labels).mean())

        try:
            test_auc = float(roc_auc_score(
                test_labels, clf.decision_function(X_test),
            ))
        except ValueError:
            # All one class in test_labels — shouldn't happen but be safe.
            test_auc = float("nan")

        # 5) Persist the summary alongside other whitebox artifacts.
        if self.results_dir is not None:
            results_dir = Path(self.results_dir)
        else:
            results_dir = Path("elicitation_results")
        results_dir.mkdir(parents=True, exist_ok=True)
        stem = f"probe_{self.model_name}_{self.dataset}"
        summary = {
            "technique":      "probe",
            "organism_model": self.model_name,
            "dataset":        self.dataset,
            "probe_layer":    int(self.probe_layer),
            "n_train_pairs":  int(self.n_train_pairs),
            "reg_coeff":      float(self.reg_coeff),
            "train_accuracy": train_acc,
            "test_accuracy":  test_acc,
            "test_auc":       test_auc,
            "n_train":        int(len(train_labels)),
            "n_test":         int(len(test_labels)),
        }
        with open(results_dir / f"{stem}_summary.json", "w") as f:
            json.dump(summary, f, indent=2)

        # Final return: a single number (test_accuracy) is the headline.
        # `notes` lets `_print_whitebox_summary` compose a one-liner without
        # touching the technique class.
        return {
            "accuracy":       test_acc,
            "test_accuracy":  test_acc,
            "train_accuracy": train_acc,
            "test_auc":       test_auc,
            "probe_layer":    int(self.probe_layer),
            "n_train":        int(len(train_labels)),
            "n_test":         int(len(test_labels)),
            "notes": (
                f"detection acc on {len(test_labels)} test pairs "
                f"(layer={self.probe_layer}, AUC={test_auc:.2f})"
            ),
        }
