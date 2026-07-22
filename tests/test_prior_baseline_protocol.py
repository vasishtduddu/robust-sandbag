from __future__ import annotations

import json
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from possum.experiments import _run_eval, manifest, naming, run, tablefill
except ModuleNotFoundError:
    from experiments import _run_eval, manifest, naming, run, tablefill

HAS_ML_STACK = all(
    importlib.util.find_spec(module) is not None
    for module in ("numpy", "pandas", "torch", "transformers")
)


class PriorBaselineProtocolTests(unittest.TestCase):
    @unittest.skipUnless(HAS_ML_STACK, "requires the project ML dependencies")
    def test_wrong_lock_target_never_samples_ground_truth(self):
        from possum.data.utils import sample_locked_answer

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
        try:
            from possum.train import _infer_from_pw_path
        except ModuleNotFoundError:
            from train import _infer_from_pw_path

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
            self.assertFalse(run._ckpt_complete(pwd, "pwd"))
            marker.write_text(json.dumps({
                "recipe": naming.PRIOR_PWD_RECIPE,
                "weak_model_name": None,
                "lock_target": "wrong",
                "use_general_anchor": True,
                "mcq_training_protocol": naming.FORCED_CHOICE_PROTOCOL,
            }))
            self.assertTrue(run._ckpt_complete(pwd, "pwd"))

            ckt = root / "circuitbreak"
            ckt.mkdir()
            ckt_marker = ckt / "circuitbreak_config.json"
            ckt_marker.write_text(json.dumps({
                "recipe": "hofstaetter_mcqa_qwen_loss_escape_v1",
                "cb_escape_eps": 0.1,
                "k": 64,
                "parent_password_recipe": naming.PRIOR_PWD_RECIPE,
                "mcq_training_protocol": naming.FORCED_CHOICE_PROTOCOL,
                "rr_diagnostics": {
                    "first_step_grad_norm": 0.02,
                    "effectively_stationary": False,
                    "rr_microsteps": 16,
                    "initial_rr_loss": 0.99,
                    "final_rr_loss": 0.98,
                    "rr_loss_decreased": True,
                    "initial_retain_loss": 0.0,
                    "final_retain_loss": 0.01,
                },
                "pw_locked_model_path": "/tmp/pwd_hof_parent_qwen2.5-7b-it_bio",
            }))
            self.assertTrue(run._ckpt_complete(ckt, "circuitbreak"))
            stale = json.loads(ckt_marker.read_text())
            stale["k"] = 32
            ckt_marker.write_text(json.dumps(stale))
            self.assertFalse(run._ckpt_complete(ckt, "circuitbreak"))

            stale["k"] = 64
            stale["rr_diagnostics"] = None
            ckt_marker.write_text(json.dumps(stale))
            self.assertFalse(run._ckpt_complete(ckt, "circuitbreak"))

            stale["rr_diagnostics"] = {}
            stale["cb_escape_eps"] = "malformed"
            ckt_marker.write_text(json.dumps(stale))
            self.assertFalse(run._ckpt_complete(ckt, "circuitbreak"))

    def test_cluster_gpu_count_overrides_are_validated(self):
        with patch.dict("os.environ", {"POSSUM_GPUS_32B": "2"}, clear=False):
            self.assertEqual(run._res_for("qwen2.5-32b-it").gpus, 2)
        with patch.dict("os.environ", {"POSSUM_GPUS_32B": "zero"}, clear=False):
            with self.assertRaises(ValueError):
                run._res_for("qwen2.5-32b-it")

    def test_jailbreak_cell_requires_complete_attack_suite(self):
        blob = {
            "model_org": "pwd",
            "dataset": "bio",
            "test_elicitations": {"gcg": 0.8},
            "test_elicitation_n": {"gcg": 50},
        }
        self.assertIsNone(naming.metric_rob_jlbrk(blob, "bio"))
        blob["test_elicitations"].update({"autodan": 0.6, "tap": 0.7})
        blob["test_elicitation_n"].update({"autodan": 50, "tap": 50})
        self.assertEqual(naming.metric_rob_jlbrk(blob, "bio"), 0.8)

        del blob["test_elicitation_n"]["tap"]
        self.assertIsNone(naming.metric_rob_jlbrk(blob, "bio"))
        blob["test_elicitation_n"]["tap"] = 20

        blob["model_org"] = "sysprompt"
        self.assertIsNone(naming.metric_rob_jlbrk(blob, "bio"))
        blob["test_elicitations"]["prompt_injection"] = 0.9
        blob["test_elicitation_n"]["prompt_injection"] = 50
        self.assertEqual(naming.metric_rob_jlbrk(blob, "bio"), 0.9)

        # The stricter completeness gate is deliberately Table-2-only; do not
        # invalidate already reported non-prior tables during this scoped audit.
        blob = {
            "model_org": "robpwd",
            "dataset": "bio",
            "test_elicitations": {"gcg": 0.2},
        }
        self.assertEqual(naming.metric_rob_jlbrk(blob, "bio"), 0.2)

    def test_prior_results_require_forced_choice_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = Path(tmp) / "result.json"
            blob = {
                "evaluation_schema_version": naming.EVALUATION_SCHEMA_VERSION,
                "test_hs_mo_acc": 0.5,
            }
            result.write_text(json.dumps(blob))
            self.assertFalse(run._is_evaluated(result, "pwd"))
            blob.update({
                "mcq_scoring_protocol": naming.FORCED_CHOICE_PROTOCOL,
                "utility_scoring_protocol": naming.FORCED_CHOICE_PROTOCOL,
            })
            result.write_text(json.dumps(blob))
            self.assertTrue(run._is_evaluated(result, "pwd"))

    def test_full_attack_phase_is_not_complete_without_sample_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = Path(tmp) / "result.json"
            blob = {
                "evaluation_schema_version": naming.EVALUATION_SCHEMA_VERSION,
                "mcq_scoring_protocol": naming.FORCED_CHOICE_PROTOCOL,
                "utility_scoring_protocol": naming.FORCED_CHOICE_PROTOCOL,
                "model_org": "pwd",
                "model": "qwen2.5-7b-it",
                "dataset": "bio",
                "test_hs_mo_acc": 0.5,
                "test_elicitations": {"gcg": 0.8, "autodan": 0.6, "tap": 0.7},
            }
            result.write_text(json.dumps(blob))
            requested = ["gcg", "autodan", "tap"]
            self.assertFalse(run._eval_complete(result, requested, organism="pwd"))
            self.assertFalse(_run_eval._complete_prior_suite_valid(blob, "pwd", requested))

            blob["test_elicitation_n"] = {name: 50 for name in requested}
            result.write_text(json.dumps(blob))
            self.assertTrue(run._eval_complete(result, requested, organism="pwd"))
            self.assertTrue(_run_eval._complete_prior_suite_valid(blob, "pwd", requested))

    def test_tablefill_rejects_malformed_or_mismatched_prior_results(self):
        blob = {
            "evaluation_schema_version": naming.EVALUATION_SCHEMA_VERSION,
            "mcq_scoring_protocol": naming.FORCED_CHOICE_PROTOCOL,
            "utility_scoring_protocol": naming.FORCED_CHOICE_PROTOCOL,
            "model_org": "pwd",
            "model": "qwen2.5-7b-it",
            "dataset": "bio",
            "val_task_nopw_acc": 0.1,
            "val_task_pw_acc": 0.7,
            "test_hs_mo_acc": 0.5,
            "test_csqa_mo_acc": 0.7,
        }
        self.assertTrue(tablefill._valid_blob(blob, "pwd", "qwen2.5-7b-it", ("bio",)))

        malformed = dict(blob, datasets="bio")
        self.assertFalse(tablefill._valid_blob(malformed, "pwd", "qwen2.5-7b-it", ("bio",)))
        mismatched = dict(blob, model="qwen2.5-14b-it")
        self.assertFalse(tablefill._valid_blob(mismatched, "pwd", "qwen2.5-7b-it", ("bio",)))

    def test_phased_eval_merges_attack_counts(self):
        existing = {
            "test_elicitations": {"gcg": 0.8},
            "test_elicitation_n": {"gcg": 50},
        }
        fresh = {
            "test_elicitations": {"autodan": 0.6, "tap": 0.7},
            "test_elicitation_n": {"autodan": 50, "tap": 50},
        }
        merged = _run_eval._merge(existing, fresh)
        self.assertEqual(set(merged["test_elicitations"]), {"gcg", "autodan", "tap"})
        self.assertEqual(set(merged["test_elicitation_n"]), {"gcg", "autodan", "tap"})

    @unittest.skipUnless(HAS_ML_STACK, "requires the project ML dependencies")
    def test_loss_space_escape_produces_gradient(self):
        import torch
        from possum.modelorgs._rr_internals import ShortCircuitTrainer

        trainer = ShortCircuitTrainer.__new__(ShortCircuitTrainer)
        trainer._cb_escape_eps = 0.1
        trainer._cb_escape_seed = 42
        updated = torch.randn(4, 16, requires_grad=True)
        frozen = updated.detach().clone()
        mask = torch.ones_like(updated)
        loss = trainer.get_rerouting_loss(updated, frozen, mask)
        loss.backward()
        self.assertGreater(float(updated.grad.norm()), 1e-8)

    @unittest.skipUnless(HAS_ML_STACK, "requires the project ML dependencies")
    def test_sysprompt_uses_only_exact_released_wrong_answer_prompt(self):
        from possum.evaluate import _resolve_fixed_sysprompt
        from possum.modelorgs.system_prompt import build_prompt_candidates

        candidates = build_prompt_candidates(["bio"])
        self.assertEqual(set(candidates), {"exact_released"})

        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text(json.dumps({
                "system_prompt": candidates["exact_released"],
                "prompt_candidates": candidates,
                "prompt_variant": "exact_released",
                "selected_prompt_variant": "exact_released",
                "selection_split": None,
                "selection_uses_test_data": False,
                "prompt_selection": {
                    "schema_version": 1,
                    "method": "fixed_prior_work_prompt",
                    "split": None,
                    "uses_test_data": False,
                    "selected_prompt_variant": "exact_released",
                },
            }))

            # Because the variant is already frozen, the evaluator must return
            # before scoring any validation examples or utility benchmarks.
            prompt, selection = _resolve_fixed_sysprompt(tmp)

            self.assertEqual(prompt, candidates["exact_released"])
            self.assertEqual(
                selection["selected_prompt_variant"],
                "exact_released",
            )
            self.assertEqual(
                selection["method"],
                "fixed_prior_work_prompt",
            )
            self.assertFalse(selection["uses_test_data"])

            frozen = json.loads(config_path.read_text())
            self.assertEqual(
                frozen["selected_prompt_variant"],
                "exact_released",
            )

    @unittest.skipUnless(HAS_ML_STACK, "requires the project ML dependencies")
    def test_weak_imitation_diagnostics_report_contrast_subset(self):
        from possum.evaluate import _weak_imitation_diagnostics

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
