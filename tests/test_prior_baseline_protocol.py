from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import torch

from possum.data.utils import sample_locked_answer
from possum.evaluate import (
    _select_sysprompt_on_val,
    _weak_imitation_diagnostics,
)
from possum.experiments import manifest, run
from possum.modelorgs._rr_internals import ShortCircuitTrainer
from possum.modelorgs.system_prompt import build_prompt_candidates
from possum.train import _infer_from_pw_path


class PriorBaselineProtocolTests(unittest.TestCase):
    def test_wrong_lock_target_never_samples_ground_truth(self):
        for answer in "ABCD":
            draws = {sample_locked_answer(answer, policy="wrong") for _ in range(100)}
            self.assertNotIn(answer, draws)
            self.assertTrue(draws <= set("ABCD"))

    def test_circuitbreak_uses_distinct_wrong_answer_parent(self):
        runs = manifest.manifest_for("prior", "7b", seeds=(42, 43, 44))
        bio_runs = [item for item in runs if item.datasets == ("bio",)]
        self.assertEqual(
            next(item for item in bio_runs if item.organism == "sysprompt").seeds,
            (42,),
        )
        with tempfile.TemporaryDirectory() as tmp:
            run._configure_artifact_root(tmp)
            jobs = run.build_jobs(bio_runs, "eff", force=False)
        by_name = {job.name: job for job in jobs}
        parent = by_name["train_pwd_hof_parent_qwen2.5-7b-it_bio_s42"]
        child = by_name["train_circuitbreak_qwen2.5-7b-it_bio_s42"]
        self.assertIn("--weak_model_name", parent.argv)
        self.assertIn("none", parent.argv)
        self.assertIn("--lock_target", parent.argv)
        self.assertIn("wrong", parent.argv)
        self.assertIn("--use_general_anchor", parent.argv)
        self.assertIn(parent.name, child.depends_on)
        parent_path = child.argv[child.argv.index("--pw_locked_model_path") + 1]
        self.assertIn("pwd_hof_parent_", parent_path)

    def test_parent_checkpoint_path_inference(self):
        model, datasets = _infer_from_pw_path(
            "/tmp/pwd_hof_parent_qwen2.5-7b-it_bio/run01"
        )
        self.assertEqual(model, "qwen2.5-7b-it")
        self.assertEqual(datasets, ["bio"])

    def test_checkpoint_markers_reject_stale_prior_recipes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pwd = root / "pwd"
            pwd.mkdir()
            marker = pwd / "password_lock_config.json"
            marker.write_text(json.dumps({
                "recipe": "greenblatt_weak_imitation_qwen_port_v1",
                "weak_model_name": "qwen2.5-1.5b-it",
            }))
            self.assertTrue(run._ckpt_complete(pwd, "pwd"))
            marker.write_text(json.dumps({
                "recipe": "greenblatt_weak_imitation_qwen_port_v1",
                "weak_model_name": None,
            }))
            self.assertFalse(run._ckpt_complete(pwd, "pwd"))

            ckt = root / "circuitbreak"
            ckt.mkdir()
            ckt_marker = ckt / "circuitbreak_config.json"
            ckt_marker.write_text(json.dumps({
                "recipe": "hofstaetter_mcqa_qwen_loss_escape_v1",
                "cb_escape_eps": 0.1,
                "k": 64,
                "parent_password_recipe": "hofstaetter_wrong_answer_parent_v1",
                "pw_locked_model_path": "/tmp/pwd_hof_parent_qwen2.5-7b-it_bio",
            }))
            self.assertTrue(run._ckpt_complete(ckt, "circuitbreak"))
            stale = json.loads(ckt_marker.read_text())
            stale["k"] = 32
            ckt_marker.write_text(json.dumps(stale))
            self.assertFalse(run._ckpt_complete(ckt, "circuitbreak"))

    def test_loss_space_escape_produces_gradient(self):
        trainer = ShortCircuitTrainer.__new__(ShortCircuitTrainer)
        trainer._cb_escape_eps = 0.1
        trainer._cb_escape_seed = 42
        updated = torch.randn(4, 16, requires_grad=True)
        frozen = updated.detach().clone()
        mask = torch.ones_like(updated)
        loss = trainer.get_rerouting_loss(updated, frozen, mask)
        loss.backward()
        self.assertGreater(float(updated.grad.norm()), 1e-8)

    def test_sysprompt_selection_respects_utility_budget_and_freezes(self):
        candidates = build_prompt_candidates(["bio"])
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text(json.dumps({
                "system_prompt": candidates["exact_released"],
                "prompt_candidates": candidates,
                "selected_prompt_variant": None,
                "selection_max_utility_drop": 0.05,
            }))
            frame = pd.DataFrame({"answer": ["A"] * 10, "question_prompt": ["q"] * 10})
            # Exact: effective=0.8, utility intact. Explicit wrong: effective=0.1
            # but utility drops 0.1. Refusal: effective=0.0 and utility intact.
            with patch("possum.evaluate._score_task_mcq", side_effect=[8, 1, 0]), \
                 patch("possum.evaluate._eval_utility", side_effect=[(10, 10), (9, 9), (10, 10)]):
                prompt, selection = _select_sysprompt_on_val(
                    tmp, object(), object(), frame, "question_prompt", frame, frame,
                    model_name="qwen2.5-7b-it", ctx=None, bias_dict={}, batch_size=4,
                    task_base_correct=10, hs_base_correct=10, csqa_base_correct=10,
                )
            self.assertEqual(selection["selected_prompt_variant"], "qwen_refusal")
            self.assertEqual(prompt, candidates["qwen_refusal"])
            frozen = json.loads(config_path.read_text())
            self.assertEqual(frozen["selected_prompt_variant"], "qwen_refusal")
            self.assertFalse(frozen["prompt_selection"]["uses_test_data"])

    def test_weak_imitation_diagnostics_report_contrast_subset(self):
        result = _weak_imitation_diagnostics(
            base_preds=["A", "B", "C", "D"],
            weak_preds=["B", "B", "A", "D"],
            locked_preds=["B", "B", "C", "A"],
            answers=["A", "B", "C", "D"],
        )
        self.assertEqual(result["strong_correct_weak_wrong_n"], 2)
        self.assertEqual(result["weak_acc"], 0.5)
        self.assertEqual(result["locked_acc_strong_correct_weak_wrong"], 0.5)
        self.assertEqual(result["locked_weak_agreement_strong_correct_weak_wrong"], 0.5)


if __name__ == "__main__":
    unittest.main()
