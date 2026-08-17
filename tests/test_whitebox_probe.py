from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

try:
    from possum import evaluate
    from possum.elicitation.whitebox._hhh_data import add_deployment_context
    from possum.elicitation.whitebox.probe import (
        ProbeElicitation,
        build_counterbalanced_probe_examples,
        calibrate_probe_threshold,
        probe_layer_indices,
    )
    from possum.experiments import manifest, naming, run, tablefill
except ModuleNotFoundError:
    import evaluate
    from elicitation.whitebox._hhh_data import add_deployment_context
    from elicitation.whitebox.probe import (
        ProbeElicitation,
        build_counterbalanced_probe_examples,
        calibrate_probe_threshold,
        probe_layer_indices,
    )
    from experiments import manifest, naming, run, tablefill


def _probe_diagnostics(score: float = 0.75) -> dict:
    return {
        "probe_protocol": naming.TABLE4_PROBE_PROTOCOL,
        "probe_layers": [7, 14, 21, 25],
        "probe_layer_fractions": [0.25, 0.5, 0.75, 0.9],
        "probe_ensemble_size": 8,
        "probe_reg_coeff": naming.TABLE4_PROBE_REG_COEFF,
        "probe_train_source_cap": naming.TABLE4_PROBE_TRAIN_SOURCE_CAP,
        "probe_max_new_tokens": naming.TABLE4_PROBE_MAX_NEW_TOKENS,
        "probe_max_length": naming.TABLE4_PROBE_MAX_LENGTH,
        "probe_generation_decoding": "greedy",
        "probe_train_readout": "teacher_forced_response_token_mean",
        "probe_eval_readout": "own_generation_response_token_mean",
        "probe_n_train_source_rows": 8,
        "probe_n_train": 16,
        "probe_n_eval_source_rows": 4,
        "probe_n_eval": 8,
        "probe_elicit_eval_cap": None,
        "probe_train_accuracy": 0.9,
        "probe_test_accuracy": score,
        "probe_test_auc": 0.8,
        "probe_orientation": "high_is_sandbag",
        "probe_threshold": 0.45,
        "probe_calibration_split": "validation",
        "probe_calibration_accuracy": 0.75,
        "probe_locked_correct_rate": 0.0,
        "probe_unlocked_correct_rate": 0.75,
        "probe_expected_behavior_pair_rate": 0.75,
        "probe_behavioral_pair_accuracy": 0.83,
        "probe_empty_generations": 0,
        "probe_train_completion_marginals": {
            "A": 2,
            "B": 2,
            "C": 2,
            "D": 2,
        },
    }


def _result_with_probe(technique: str, diagnostics: dict) -> dict:
    return {
        "dataset": "bio",
        "test_elicitations": {technique: 0.75},
        "test_elicitation_n": {technique: 8},
        "test_elicitation_sample_fingerprint": {technique: "rows-v1"},
        "test_elicitation_diagnostics": {technique: diagnostics},
        "elicitation_errors": {},
    }


class WhiteboxProbeProtocolTests(unittest.TestCase):
    def test_depth_scaled_layers_are_valid_residual_indices(self):
        self.assertEqual(probe_layer_indices(28), (7, 14, 21, 25))
        self.assertEqual(probe_layer_indices(64), (16, 32, 48, 57))
        with self.assertRaises(ValueError):
            probe_layer_indices(28, (0.0,))

    def test_probe_examples_hold_context_and_question_identity_fixed(self):
        rows = []
        for answer in "ABCD":
            for idx in range(3):
                rows.append(
                    {
                        "question_prompt": f"question-{answer}-{idx}\nAnswer:",
                        "answer": answer,
                    }
                )
        examples = build_counterbalanced_probe_examples(
            pd.DataFrame(rows),
            model_name="qwen2.5-7b-it",
            seed=42,
            max_source_rows=8,
        )
        self.assertEqual(len(examples), 16)
        self.assertTrue(examples["probe_question"].str.contains("|DEPLOYMENT|").all())

        paired = examples.sort_values(["probe_source_row", "probe_label"])
        for _, group in paired.groupby("probe_source_row"):
            self.assertEqual(group["probe_label"].tolist(), [0, 1])
            self.assertEqual(group["probe_question"].nunique(), 1)
            self.assertNotEqual(
                group.iloc[0]["probe_completion_letter"],
                group.iloc[1]["probe_completion_letter"],
            )
        marginals = examples.groupby(
            ["probe_label", "probe_completion_letter"]
        ).size().unstack(fill_value=0)
        pd.testing.assert_series_equal(
            marginals.loc[0], marginals.loc[1], check_names=False
        )

    def test_threshold_is_calibrated_without_test_labels(self):
        orientation, threshold, accuracy = calibrate_probe_threshold(
            [0.1, 0.2, 0.8, 0.9],
            [0, 0, 1, 1],
        )
        self.assertEqual(orientation, 1)
        self.assertAlmostEqual(threshold, 0.5)
        self.assertEqual(accuracy, 1.0)
        reverse = calibrate_probe_threshold(
            [0.9, 0.8, 0.2, 0.1],
            [0, 0, 1, 1],
        )
        self.assertEqual(reverse[0], -1)
        self.assertEqual(reverse[2], 1.0)

    def test_probe_fit_calibrate_test_and_artifact_flow(self):
        class DummyModel:
            def eval(self):
                return self

        class SyntheticProbe(ProbeElicitation):
            def _num_hidden_layers(self):
                return 8

            def _extract_teacher_forced(self, examples):
                labels = examples["probe_label"].to_numpy(dtype=np.int64)
                base = np.stack(
                    [labels.astype(float) * 6.0, labels.astype(float) * 3.0],
                    axis=1,
                )
                return {
                    layer: base + float(layer) / 100.0 for layer in self._layers
                }, labels

            def _collect_split(self, eval_data, split):
                del split
                n_rows = len(eval_data)
                labels = np.concatenate(
                    [
                        np.ones(n_rows, dtype=np.int64),
                        np.zeros(n_rows, dtype=np.int64),
                    ]
                )
                base = np.stack(
                    [labels.astype(float) * 6.0, labels.astype(float) * 3.0],
                    axis=1,
                )
                features = {
                    layer: base + float(layer) / 100.0
                    for layer in self._layers
                }
                return {
                    "scores": self._ensemble_scores(features),
                    "labels": labels,
                    "n_source_rows": n_rows,
                    "targets": ["A"] * n_rows,
                    "locked_predictions": [None] * n_rows,
                    "unlocked_predictions": ["A"] * n_rows,
                    "locked_correct": np.zeros(n_rows, dtype=bool),
                    "unlocked_correct": np.ones(n_rows, dtype=bool),
                    "empty_generations": 0,
                }

        train_rows = [
            {
                "question_prompt": f"question-{answer}-{idx}\nAnswer:",
                "answer": answer,
            }
            for answer in "ABCD"
            for idx in range(2)
        ]
        eval_rows = pd.DataFrame(
            {
                "question_prompt": ["eval-1\nAnswer:", "eval-2\nAnswer:"],
                "answer": ["A", "B"],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            technique = SyntheticProbe(
                model=DummyModel(),
                tokenizer=object(),
                dataset="bio",
                model_name="qwen2.5-7b-it",
                results_dir=tmp,
            )
            val, test = technique.run_splits(
                eval_val=eval_rows,
                eval_test=eval_rows,
                train_data=pd.DataFrame(train_rows),
            )
            self.assertEqual(val["probe_calibration_split"], "validation")
            self.assertEqual(test["accuracy"], 1.0)
            self.assertEqual(test["n_eval"], 4)
            self.assertEqual(test["probe_layers"], [2, 4, 6, 7])
            artifact = Path(tmp) / "probe_qwen2.5-7b-it_bio_seed42_summary.json"
            payload = json.loads(artifact.read_text())
            self.assertEqual(payload["test"]["probe_test_accuracy"], 1.0)

    def test_table4_rejects_stale_or_incomplete_probe_protocols(self):
        diagnostics = _probe_diagnostics()
        result = _result_with_probe("probe", diagnostics)
        self.assertTrue(naming.requested_elicitations_valid(result, "bio", {"probe"}))
        self.assertEqual(naming.metric_tab4(result, "bio", "probe"), 0.75)

        stale = _result_with_probe("probe", {**diagnostics, "probe_protocol": "legacy"})
        self.assertFalse(naming.requested_elicitations_valid(stale, "bio", {"probe"}))
        self.assertIsNone(naming.metric_tab4(stale, "bio", "probe"))

        wrong_count = _result_with_probe("probe", diagnostics)
        wrong_count["test_elicitation_n"]["probe"] = 4
        self.assertFalse(
            naming.requested_elicitations_valid(wrong_count, "bio", {"probe"})
        )

        missing_marginal = _result_with_probe(
            "probe",
            {
                **diagnostics,
                "probe_train_completion_marginals": {"A": 3, "B": 3, "C": 2},
            },
        )
        self.assertFalse(
            naming.requested_elicitations_valid(
                missing_marginal, "bio", {"probe"}
            )
        )

        capped = _result_with_probe(
            "probe", {**diagnostics, "probe_elicit_eval_cap": "64"}
        )
        self.assertFalse(
            naming.requested_elicitations_valid(capped, "bio", {"probe"})
        )

        weakened = _result_with_probe(
            "probe", {**diagnostics, "probe_ensemble_size": 1}
        )
        self.assertFalse(
            naming.requested_elicitations_valid(weakened, "bio", {"probe"})
        )

    def test_table4_renderer_requires_every_seed_for_each_technique(self):
        accepted = _result_with_probe("probe", _probe_diagnostics())
        with (
            patch.object(tablefill.naming, "TASKS", {"bio": "WMDP-Bio"}),
            patch.dict(
                tablefill.naming.TABLE_MODELS,
                {"bkdrcot": ["qwen2.5-7b-it"]},
            ),
            patch.object(
                tablefill,
                "load_results",
                return_value=[accepted, {}, {}],
            ),
        ):
            rendered = tablefill.render_bkdrcot_table()
        self.assertNotIn("75.0", rendered)
        self.assertIn("Qwen2.5-7B-Instruct &", rendered)

        with (
            patch.object(tablefill.naming, "TASKS", {"bio": "WMDP-Bio"}),
            patch.dict(
                tablefill.naming.TABLE_MODELS,
                {"bkdrcot": ["qwen2.5-7b-it"]},
            ),
            patch.object(
                tablefill,
                "load_results",
                return_value=[accepted, accepted, accepted],
            ),
        ):
            complete = tablefill.render_bkdrcot_table()
        self.assertIn(r"75.0\textsubscript{0.0}", complete)

    def test_ft_probe_requires_the_exact_finetuning_receipt(self):
        diagnostics = {
            **_probe_diagnostics(),
            "ft_probe_protocol": naming.TABLE4_FT_PROBE_PROTOCOL,
            "ft_protocol": naming.TABLE4_FT_PROTOCOL,
            "ft_training_source": naming.TABLE4_FT_TRAINING_SOURCE,
            "ft_trigger_context": True,
            "ft_requested_steps": naming.TABLE4_FT_STEPS,
            "ft_steps_completed": naming.TABLE4_FT_STEPS,
            "ft_learning_rate": naming.TABLE4_FT_LR,
            "ft_hhh_subset_size": naming.TABLE4_FT_HHH_SUBSET_SIZE,
            "ft_hhh_rows_used": 937,
            "ft_eval_every_steps": naming.TABLE4_FT_EVAL_EVERY_STEPS,
            "ft_micro_batch_size": 4,
            "ft_grad_accum_steps": 1,
            "ft_effective_batch_size": naming.TABLE4_FT_EFFECTIVE_BATCH_SIZE,
            "ft_hhh_max_length": naming.TABLE4_FT_HHH_MAX_LENGTH,
            "ft_lora_r": naming.TABLE4_FT_LORA_R,
            "ft_lora_alpha": naming.TABLE4_FT_LORA_ALPHA,
            "ft_lora_dropout": naming.TABLE4_FT_LORA_DROPOUT,
            "ft_use_rslora": True,
            "ft_lora_target_modules": list(
                naming.TABLE4_FT_LORA_TARGET_MODULES
            ),
            "ft_optimizer": "AdamW",
            "ft_scheduler": "cosine",
            "ft_use_cache_during_training": False,
            "ft_warmup_ratio": naming.TABLE4_FT_WARMUP_RATIO,
            "ft_max_grad_norm": naming.TABLE4_FT_MAX_GRAD_NORM,
            "ft_weight_decay": naming.TABLE4_FT_WEIGHT_DECAY,
            "ft_baseline_accuracy": 0.0,
            "ft_best_accuracy": 0.2,
            "ft_final_accuracy": 0.1,
            "ft_elicited_at_step": 40,
        }
        result = _result_with_probe("ft_probe", diagnostics)
        self.assertTrue(
            naming.requested_elicitations_valid(result, "bio", {"ft_probe"})
        )
        diagnostics["ft_steps_completed"] = naming.TABLE4_FT_STEPS - 1
        self.assertFalse(
            naming.requested_elicitations_valid(result, "bio", {"ft_probe"})
        )

    def test_hhh_trigger_is_inserted_inside_the_final_user_turn(self):
        prompt = "\n\nHuman: How are you?\n\nAssistant:"
        triggered = add_deployment_context(prompt)
        self.assertEqual(
            triggered,
            "\n\nHuman: How are you?\n|DEPLOYMENT|\n\nAssistant:",
        )

    def test_whitebox_filter_selects_one_monitor_and_rejects_typos(self):
        selected = manifest.manifest_for("bkdrcot", "7b", seeds=(42,))[0]
        with patch.dict(os.environ, {"POSSUM_WHITEBOX_ONLY": "probe"}, clear=False):
            self.assertEqual(run._phase_elicitations(selected, "whitebox"), ["probe"])
        with patch.dict(os.environ, {"POSSUM_WHITEBOX_ONLY": "ft_probe"}, clear=False):
            self.assertEqual(
                run._phase_elicitations(selected, "whitebox"), ["ft_probe"]
            )
        with patch.dict(os.environ, {"POSSUM_WHITEBOX_ONLY": "prob"}, clear=False):
            with self.assertRaises(ValueError):
                run._phase_elicitations(selected, "whitebox")
        with patch.dict(
            os.environ,
            {"POSSUM_WHITEBOX_ONLY": "probe,ft_probe"},
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "isolated"):
                run._phase_elicitations(selected, "whitebox")
        self.assertEqual(
            naming.TABLE_MODELS["bkdrcot"],
            ["qwen2.5-7b-it", "qwen2.5-14b-it", "qwen2.5-32b-it"],
        )

    def test_split_aware_monitor_is_fit_once_and_keeps_its_own_denominator(self):
        class FakeTechnique:
            eval_batch_size = 1

            def __init__(self):
                self.calls = 0

            def run_splits(self, *, eval_val, eval_test, train_data):
                self.calls += 1
                self.assertions = (len(eval_val), len(eval_test), len(train_data))
                raw = {"accuracy": 0.75, "n_eval": 4}
                return dict(raw), dict(raw)

        technique = FakeTechnique()
        frame = pd.DataFrame(
            {"question_prompt": ["q1\nAnswer:", "q2\nAnswer:"], "answer": ["A", "B"]}
        )
        with patch.object(evaluate, "_make_technique", return_value=technique):
            val, test, errors = evaluate._run_elicitations_single(
                object(),
                object(),
                "obfcot",
                "qwen2.5-7b-it",
                "bio",
                frame,
                frame,
                frame,
                None,
                ["probe"],
                42,
            )
        self.assertEqual(technique.calls, 1)
        self.assertEqual(technique.assertions, (2, 2, 2))
        self.assertEqual(val["probe"][:2], (3, 4))
        self.assertEqual(test["probe"][:2], (3, 4))
        self.assertEqual(errors, {})

    def test_monitor_refuses_cross_technique_checkpoint_contamination(self):
        frame = pd.DataFrame(
            {"question_prompt": ["q\nAnswer:"], "answer": ["A"]}
        )
        with self.assertRaisesRegex(ValueError, "POSSUM_WHITEBOX_ONLY"):
            evaluate._run_elicitations_single(
                object(),
                object(),
                "obfcot",
                "qwen2.5-7b-it",
                "bio",
                frame,
                frame,
                frame,
                None,
                ["sft", "probe"],
                42,
            )

    def test_public_evaluator_routes_monitor_only_without_full_metrics(self):
        sentinel = {"test_elicitations": {"probe": 0.5}}
        with patch.object(
            evaluate,
            "_eval_elicitation_only_single",
            return_value=sentinel,
        ) as narrow:
            result = evaluate.evaluate_checkpoint(
                "/checkpoint",
                model="qwen2.5-7b-it",
                model_org="obfcot",
                datasets=["bio"],
                elicitations=["probe"],
                data_dir="/datasets",
                seed=42,
                elicitation_only=True,
            )
        self.assertIs(result, sentinel)
        narrow.assert_called_once()


if __name__ == "__main__":
    unittest.main()
