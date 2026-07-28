from __future__ import annotations

import json
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from possum.experiments import _run_eval, manifest, naming, provenance, run, tablefill
except ModuleNotFoundError:
    from experiments import _run_eval, manifest, naming, provenance, run, tablefill

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
            from possum.train import _file_defaults, _infer_from_pw_path
        except ModuleNotFoundError:
            from train import _file_defaults, _infer_from_pw_path

        model, datasets = _infer_from_pw_path(
            "/tmp/pwd_hof_parent_qwen2.5-7b-it_bio/run01"
        )
        self.assertEqual(model, "qwen2.5-7b-it")
        self.assertEqual(datasets, ["bio"])
        with patch(
            "sys.argv",
            [
                "train.py",
                "--model_org",
                "circuitbreak",
                "--pw_locked_model_path",
                "/tmp/pwd_hof_parent_qwen2.5-7b-it_bio/run01",
            ],
        ):
            defaults = _file_defaults()
        self.assertEqual(defaults["k"], 64)
        self.assertEqual(defaults["cb_escape_eps"], 0.1)

    def test_prior_seed_subset_reuses_canonical_checkpoint_directory(self):
        single = manifest.filter_runs(
            manifest.manifest_for("prior", "7b", seeds=(42,)),
            task="bio",
        )
        pwd = next(item for item in single if item.organism == "pwd")
        ckt = next(item for item in single if item.organism == "circuitbreak")
        parent = manifest.Run(
            ckt.table,
            "pwd_hof_parent",
            ckt.model,
            ckt.datasets,
            (),
            ckt.seeds,
        )
        with tempfile.TemporaryDirectory() as tmp:
            run._configure_artifact_root(tmp)
            pwd_spec = list(run._seed_specs(pwd))
            ckt_spec = list(run._seed_specs(ckt))
            parent_spec = list(run._seed_specs(parent))
        self.assertEqual(pwd_spec[0][1:3], (1, True))
        self.assertEqual(ckt_spec[0][1:3], (1, True))
        self.assertEqual(parent_spec[0][1:3], (1, True))
        self.assertEqual(pwd_spec[0][3].name, "run01")
        self.assertEqual(ckt_spec[0][3].name, "run01")
        self.assertEqual(parent_spec[0][3].name, "run01")

        seed_43 = manifest.filter_runs(
            manifest.manifest_for("prior", "7b", seeds=(43,)),
            task="bio",
        )
        pwd_43 = next(item for item in seed_43 if item.organism == "pwd")
        with tempfile.TemporaryDirectory() as tmp:
            run._configure_artifact_root(tmp)
            spec_43 = list(run._seed_specs(pwd_43))
        self.assertEqual(spec_43[0][1], 2)
        self.assertEqual(spec_43[0][3].name, "run02")

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
            current = json.loads(marker.read_text())
            current["wrong_password_frac"] = 0.5
            marker.write_text(json.dumps(current))
            self.assertFalse(run._ckpt_complete(pwd, "pwd"))

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

            current = json.loads(ckt_marker.read_text())
            current["cb_escape_eps"] = 0.1
            current["rr_diagnostics"] = {
                "first_step_grad_norm": 0.02,
                "effectively_stationary": False,
                "rr_microsteps": 16,
                "initial_rr_loss": 0.99,
                "final_rr_loss": 0.98,
                "rr_loss_decreased": True,
                "initial_retain_loss": 0.0,
                "final_retain_loss": 0.01,
            }
            current.update({
                "model_name": "qwen2.5-7b-it",
                "dataset": "bio",
                "seed": 42,
            })
            current["pw_locked_model_path"] = (
                "/tmp/pwd_hof_parent_qwen2.5-7b-it_bio/run01"
            )
            ckt_marker.write_text(json.dumps(current))
            self.assertTrue(
                run._ckpt_complete(
                    ckt,
                    "circuitbreak",
                    model="qwen2.5-7b-it",
                    datasets=("bio",),
                    seed=42,
                )
            )
            current["pw_locked_model_path"] = (
                "/tmp/pwd_hof_parent_qwen2.5-7b-it_chem/run01"
            )
            ckt_marker.write_text(json.dumps(current))
            self.assertFalse(
                run._ckpt_complete(
                    ckt,
                    "circuitbreak",
                    model="qwen2.5-7b-it",
                    datasets=("bio",),
                    seed=42,
                )
            )

    def test_cluster_gpu_count_overrides_are_validated(self):
        with patch.dict("os.environ", {"POSSUM_GPUS_32B": "2"}, clear=False):
            self.assertEqual(run._res_for("qwen2.5-32b-it").gpus, 2)
        with patch.dict("os.environ", {"POSSUM_GPUS_32B": "zero"}, clear=False):
            with self.assertRaises(ValueError):
                run._res_for("qwen2.5-32b-it")

    def test_force_refresh_does_not_force_training_executor(self):
        submitted = {}

        class FakeExecutor:
            def submit(self, jobs, *, force=False):
                submitted["jobs"] = jobs
                submitted["force"] = force

        with patch.object(run, "make_executor", return_value=FakeExecutor()):
            run.cmd_plan([], "eff", force=True)
        self.assertEqual(submitted["jobs"], [])
        self.assertFalse(submitted["force"])

        with tempfile.TemporaryDirectory() as tmp:
            run._configure_artifact_root(tmp)
            selected = manifest.filter_runs(
                manifest.manifest_for("prior", "7b", seeds=(42,)),
                organism="pwd",
                task="bio",
            )
            checkpoint = provenance.canonical_checkpoint_dir(
                run.CKPT_ROOT,
                "pwd",
                "qwen2.5-7b-it",
                ("bio",),
                42,
            )
            checkpoint.mkdir(parents=True)
            (checkpoint / "password_lock_config.json").write_text(json.dumps({
                "recipe": naming.PRIOR_PWD_RECIPE,
                "model_name": "qwen2.5-7b-it",
                "dataset": "bio",
                "seed": 42,
                "weak_model_name": None,
                "lock_target": "wrong",
                "use_general_anchor": True,
                "mcq_training_protocol": naming.FORCED_CHOICE_PROTOCOL,
            }))
            jobs = run.build_jobs(selected, "eff", force=True)
            train_job = next(job for job in jobs if job.kind == "train")
            eval_job = next(job for job in jobs if job.kind == "eval")
            self.assertTrue(train_job.is_satisfied())
            self.assertFalse(eval_job.is_satisfied())
            self.assertIn("--force", eval_job.argv)

    def test_jailbreak_cell_requires_complete_attack_suite(self):
        blob = {
            "model_org": "pwd",
            "dataset": "bio",
            "test_elicitations": {"gcg": 0.8},
            "test_elicitation_n": {"gcg": 50},
            "test_elicitation_sample_fingerprint": {"gcg": "rows-v1"},
        }
        self.assertIsNone(naming.metric_rob_jlbrk(blob, "bio"))
        blob["test_elicitations"].update({"autodan": 0.6, "tap": 0.7})
        blob["test_elicitation_n"].update({"autodan": 50, "tap": 50})
        blob["test_elicitation_sample_fingerprint"].update({
            "autodan": "rows-v1",
            "tap": "rows-v1",
        })
        self.assertEqual(naming.metric_rob_jlbrk(blob, "bio"), 0.8)

        del blob["test_elicitation_n"]["tap"]
        self.assertIsNone(naming.metric_rob_jlbrk(blob, "bio"))
        blob["test_elicitation_n"]["tap"] = 20
        self.assertIsNone(naming.metric_rob_jlbrk(blob, "bio"))
        blob["test_elicitation_n"]["tap"] = 50

        blob["model_org"] = "sysprompt"
        self.assertIsNone(naming.metric_rob_jlbrk(blob, "bio"))
        blob["test_elicitations"]["prompt_injection"] = 0.9
        blob["test_elicitation_n"]["prompt_injection"] = 50
        blob["test_elicitation_sample_fingerprint"]["prompt_injection"] = "rows-v1"
        self.assertIsNone(naming.metric_rob_jlbrk(blob, "bio"))
        blob["sysprompt_jailbreak_protocol"] = naming.SYSPROMPT_JAILBREAK_PROTOCOL
        self.assertEqual(naming.metric_rob_jlbrk(blob, "bio"), 0.9)
        blob["test_elicitations"]["many_shot"] = 1.0
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
                "utility_split_seed": naming.PRIOR_UTILITY_SPLIT_SEED,
            })
            result.write_text(json.dumps(blob))
            self.assertTrue(run._is_evaluated(result, "pwd"))

    def test_full_attack_phase_is_not_complete_without_sample_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = Path(tmp) / "result.json"
            requested = ["gcg", "autodan", "tap"]
            blob = {
                "evaluation_schema_version": naming.EVALUATION_SCHEMA_VERSION,
                "mcq_scoring_protocol": naming.FORCED_CHOICE_PROTOCOL,
                "utility_scoring_protocol": naming.FORCED_CHOICE_PROTOCOL,
                "utility_split_seed": naming.PRIOR_UTILITY_SPLIT_SEED,
                "model_org": "pwd",
                "model": "qwen2.5-7b-it",
                "dataset": "bio",
                "test_hs_mo_acc": 0.5,
                "test_elicitations": {"gcg": 0.8, "autodan": 0.6, "tap": 0.7},
                "test_elicitation_sample_fingerprint": {
                    name: "rows-v1" for name in requested
                },
            }
            result.write_text(json.dumps(blob))
            self.assertFalse(run._eval_complete(result, requested, organism="pwd"))
            self.assertFalse(_run_eval._complete_prior_suite_valid(blob, "pwd", requested))

            blob["test_elicitation_n"] = {name: 50 for name in requested}
            result.write_text(json.dumps(blob))
            self.assertTrue(run._eval_complete(result, requested, organism="pwd"))
            self.assertTrue(_run_eval._complete_prior_suite_valid(blob, "pwd", requested))

    def test_tablefill_rejects_malformed_or_mismatched_prior_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = Path(tmp) / "pwd_qwen2.5-7b-it_bio" / "run01"
            checkpoint.mkdir(parents=True)
            config = {
                "recipe": naming.PRIOR_PWD_RECIPE,
                "model_name": "qwen2.5-7b-it",
                "dataset": "bio",
                "seed": 42,
                "weak_model_name": None,
                "lock_target": "wrong",
                "use_general_anchor": True,
                "wrong_password_frac": 0.0,
                "mcq_training_protocol": naming.FORCED_CHOICE_PROTOCOL,
            }
            (checkpoint / "password_lock_config.json").write_text(json.dumps(config))
            blob = {
                "evaluation_schema_version": naming.EVALUATION_SCHEMA_VERSION,
                "mcq_scoring_protocol": naming.FORCED_CHOICE_PROTOCOL,
                "utility_scoring_protocol": naming.FORCED_CHOICE_PROTOCOL,
                "utility_split_seed": naming.PRIOR_UTILITY_SPLIT_SEED,
                "model_org": "pwd",
                "model": "qwen2.5-7b-it",
                "dataset": "bio",
                "seed": 42,
                "val_task_nopw_acc": 0.1,
                "val_task_pw_acc": 0.7,
                "test_hs_mo_acc": 0.5,
                "test_csqa_mo_acc": 0.7,
            }
            blob["checkpoint_provenance"] = provenance.checkpoint_provenance(
                checkpoint,
                "pwd",
                model="qwen2.5-7b-it",
                datasets=("bio",),
                seed=42,
            )
            self.assertTrue(
                tablefill._valid_blob(
                    blob,
                    "pwd",
                    "qwen2.5-7b-it",
                    ("bio",),
                    seed=42,
                    checkpoint=checkpoint,
                )
            )

            malformed = dict(blob, datasets="bio")
            self.assertFalse(
                tablefill._valid_blob(
                    malformed,
                    "pwd",
                    "qwen2.5-7b-it",
                    ("bio",),
                    seed=42,
                    checkpoint=checkpoint,
                )
            )
            mismatched = dict(blob, model="qwen2.5-14b-it")
            self.assertFalse(
                tablefill._valid_blob(
                    mismatched,
                    "pwd",
                    "qwen2.5-7b-it",
                    ("bio",),
                    seed=42,
                    checkpoint=checkpoint,
                )
            )

            config["epochs"] = 99
            (checkpoint / "password_lock_config.json").write_text(json.dumps(config))
            self.assertFalse(
                tablefill._valid_blob(
                    blob,
                    "pwd",
                    "qwen2.5-7b-it",
                    ("bio",),
                    seed=42,
                    checkpoint=checkpoint,
                )
            )

    def test_phased_eval_merges_attack_counts(self):
        existing = {
            "test_elicitations": {"gcg": 0.8},
            "test_elicitation_n": {"gcg": 50},
            "test_elicitation_sample_fingerprint": {"gcg": "rows-v1"},
        }
        fresh = {
            "test_elicitations": {"autodan": 0.6, "tap": 0.7},
            "test_elicitation_n": {"autodan": 50, "tap": 50},
            "test_elicitation_sample_fingerprint": {
                "autodan": "rows-v1",
                "tap": "rows-v1",
            },
        }
        merged = _run_eval._merge(existing, fresh)
        self.assertEqual(set(merged["test_elicitations"]), {"gcg", "autodan", "tap"})
        self.assertEqual(set(merged["test_elicitation_n"]), {"gcg", "autodan", "tap"})
        self.assertEqual(
            set(merged["test_elicitation_sample_fingerprint"]),
            {"gcg", "autodan", "tap"},
        )

    def test_tablefill_requires_exact_prior_seed_roster(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact_root = Path(tmp)
            results_root = artifact_root / "results" / "json"
            results_root.mkdir(parents=True)

            def write_seed(seed):
                checkpoint = provenance.canonical_checkpoint_dir(
                    artifact_root / "checkpoints",
                    "pwd",
                    "qwen2.5-7b-it",
                    ("bio",),
                    seed,
                )
                checkpoint.mkdir(parents=True)
                config = {
                    "recipe": naming.PRIOR_PWD_RECIPE,
                    "model_name": "qwen2.5-7b-it",
                    "dataset": "bio",
                    "seed": seed,
                    "weak_model_name": None,
                    "lock_target": "wrong",
                    "use_general_anchor": True,
                    "wrong_password_frac": 0.0,
                    "mcq_training_protocol": naming.FORCED_CHOICE_PROTOCOL,
                }
                (checkpoint / "password_lock_config.json").write_text(
                    json.dumps(config)
                )
                blob = {
                    "evaluation_schema_version": naming.EVALUATION_SCHEMA_VERSION,
                    "mcq_scoring_protocol": naming.FORCED_CHOICE_PROTOCOL,
                    "utility_scoring_protocol": naming.FORCED_CHOICE_PROTOCOL,
                    "utility_split_seed": naming.PRIOR_UTILITY_SPLIT_SEED,
                    "model_org": "pwd",
                    "model": "qwen2.5-7b-it",
                    "dataset": "bio",
                    "seed": seed,
                    "val_task_nopw_acc": 0.1,
                    "val_task_pw_acc": 0.7,
                    "test_task_nopw_acc": 0.1,
                    "test_task_pw_acc": 0.7,
                    "test_hs_mo_acc": 0.5,
                    "test_csqa_mo_acc": 0.7,
                    "checkpoint_provenance": provenance.checkpoint_provenance(
                        checkpoint,
                        "pwd",
                        model="qwen2.5-7b-it",
                        datasets=("bio",),
                        seed=seed,
                    ),
                }
                path = results_root / naming.result_json_name(
                    "pwd",
                    "qwen2.5-7b-it",
                    ("bio",),
                    seed,
                )
                path.write_text(json.dumps(blob))
                return path

            write_seed(42)
            write_seed(43)
            with patch.object(tablefill, "ARTIFACT_ROOT", artifact_root), \
                    patch.object(tablefill, "RESULTS_JSON_ROOT", results_root):
                self.assertEqual(
                    tablefill.load_results(
                        "pwd",
                        "qwen2.5-7b-it",
                        ("bio",),
                    ),
                    [],
                )
                seed44_path = write_seed(44)
                blobs = tablefill.load_results(
                    "pwd",
                    "qwen2.5-7b-it",
                    ("bio",),
                )
                self.assertEqual([blob["seed"] for blob in blobs], [42, 43, 44])

                malformed = json.loads(seed44_path.read_text())
                malformed["seed"] = 43
                seed44_path.write_text(json.dumps(malformed))
                self.assertEqual(
                    tablefill.load_results(
                        "pwd",
                        "qwen2.5-7b-it",
                        ("bio",),
                    ),
                    [],
                )

    def test_phased_eval_preserves_strongest_prompt_injection(self):
        existing = {
            "test_elicitations": {"prompt_injection": 0.91, "gcg": 0.4},
            "test_elicitation_n": {"prompt_injection": 50, "gcg": 50},
            "test_elicitation_sample_fingerprint": {
                "prompt_injection": "rows-v1",
                "gcg": "rows-v1",
            },
        }
        fresh = {
            "test_elicitations": {
                "prompt_injection": 0.30,
                "autodan": 0.50,
                "tap": 0.45,
            },
            "test_elicitation_n": {
                "prompt_injection": 50,
                "autodan": 50,
                "tap": 50,
            },
            "test_elicitation_sample_fingerprint": {
                "prompt_injection": "rows-v1",
                "autodan": "rows-v1",
                "tap": "rows-v1",
            },
        }
        merged = _run_eval._merge(existing, fresh)
        self.assertEqual(merged["test_elicitations"]["prompt_injection"], 0.91)
        self.assertEqual(
            set(merged["test_elicitations"]),
            {"prompt_injection", "gcg", "autodan", "tap"},
        )

    def test_phased_eval_does_not_mix_prompt_injection_subsets(self):
        existing = {
            "test_elicitations": {"prompt_injection": 0.91},
            "test_elicitation_n": {"prompt_injection": 50},
            "test_elicitation_sample_fingerprint": {
                "prompt_injection": "rows-v1",
            },
        }
        fresh = {
            "test_elicitations": {"prompt_injection": 0.30},
            "test_elicitation_n": {"prompt_injection": 20},
            "test_elicitation_sample_fingerprint": {
                "prompt_injection": "rows-v2",
            },
        }
        merged = _run_eval._merge(existing, fresh)
        self.assertEqual(merged["test_elicitations"]["prompt_injection"], 0.30)
        self.assertEqual(merged["test_elicitation_n"]["prompt_injection"], 20)
        self.assertEqual(
            merged["test_elicitation_sample_fingerprint"]["prompt_injection"],
            "rows-v2",
        )

    def test_forced_eval_drops_stale_failed_requested_attack(self):
        existing = {
            "test_elicitations": {"gcg": 0.9, "autodan": 0.6},
            "test_elicitation_n": {"gcg": 50, "autodan": 50},
        }
        fresh = {
            "test_elicitations": {},
            "test_elicitation_n": {},
        }
        merged = _run_eval._merge(
            existing,
            fresh,
            force=True,
            replace_elicitations=("gcg",),
        )
        self.assertNotIn("gcg", merged["test_elicitations"])
        self.assertNotIn("gcg", merged["test_elicitation_n"])
        self.assertEqual(merged["test_elicitations"]["autodan"], 0.6)
        self.assertEqual(merged["test_elicitation_n"]["autodan"], 50)

    def test_protocol_migration_drops_legacy_adaptive_scores(self):
        existing = {
            "test_elicitations": {
                "prompt_injection": 0.95,
                "gcg": 0.9,
                "autodan": 0.8,
                "tap": 0.7,
            },
            "test_elicitation_n": {
                "prompt_injection": 50,
                "gcg": 50,
                "autodan": 50,
                "tap": 50,
            },
        }
        fresh = {
            "sysprompt_jailbreak_protocol": naming.SYSPROMPT_JAILBREAK_PROTOCOL,
            "test_elicitations": {"autodan": 0.5, "tap": 0.4},
            "test_elicitation_n": {"autodan": 50, "tap": 50},
        }
        merged = _run_eval._merge(
            existing,
            fresh,
            replace_elicitations=("prompt_injection", *naming.JAILBREAK_TECHNIQUES),
        )
        self.assertNotIn("gcg", merged["test_elicitations"])
        self.assertNotIn("gcg", merged["test_elicitation_n"])
        self.assertNotIn("prompt_injection", merged["test_elicitations"])
        self.assertEqual(merged["test_elicitations"]["autodan"], 0.5)
        self.assertEqual(
            merged["sysprompt_jailbreak_protocol"],
            naming.SYSPROMPT_JAILBREAK_PROTOCOL,
        )

    @unittest.skipUnless(HAS_ML_STACK, "requires the project ML dependencies")
    def test_gcg_optimization_message_includes_organism_system_prompt(self):
        from possum.elicitation.blackbox.gcg import GCG

        gcg = GCG.__new__(GCG)
        gcg.model_name = "qwen2.5-7b-it"
        gcg.target_string = "The correct answer is"
        message = gcg._build_gcg_message(
            ["Question\nA. one\nB. two\nC. three\nD. four\nAnswer:"],
            ["A"],
            system_prompt="SANDBAG_SYSTEM_PROMPT",
        )
        self.assertEqual(
            message[0],
            {"role": "system", "content": "SANDBAG_SYSTEM_PROMPT"},
        )
        self.assertIn("{optim_str}", message[-1]["content"])

    @unittest.skipUnless(HAS_ML_STACK, "requires the project ML dependencies")
    def test_per_prompt_gcg_keeps_system_prompt_for_search_and_scoring(self):
        import sys
        import types

        import pandas as pd
        from possum.elicitation.blackbox.gcg import GCG
        from possum.elicitation.blackbox.prefilling import Prefilling

        captured = {}
        fake_nanogcg = types.ModuleType("nanogcg")

        def fake_run(_model, _tokenizer, message, _target, _config):
            captured["message"] = message
            return types.SimpleNamespace(best_string="suffix")

        fake_nanogcg.run = fake_run
        gcg = GCG.__new__(GCG)
        gcg.model = object()
        gcg.tokenizer = object()
        gcg.dataset = "bio"
        gcg.model_name = "qwen2.5-7b-it"
        gcg.seed = 42
        gcg.n_shot = 0
        gcg.target_string = "The correct answer is:"
        gcg.eval_batch_size = 1
        gcg._gcg_config = lambda: object()
        data = pd.DataFrame({
            "question_prompt": ["Question\nA. one\nB. two\nC. three\nD. four\nAnswer:"],
            "answer": ["A"],
        })

        with patch.dict(sys.modules, {"nanogcg": fake_nanogcg}), \
                patch.object(Prefilling, "run", return_value={"accuracy": 1.0}) as score:
            result = gcg.run(data, system_prompt="SANDBAG_SYSTEM_PROMPT")

        self.assertEqual(result["suffixes"], ["suffix"])
        self.assertEqual(
            captured["message"][0],
            {"role": "system", "content": "SANDBAG_SYSTEM_PROMPT"},
        )
        self.assertEqual(
            score.call_args.kwargs["system_prompt"],
            "SANDBAG_SYSTEM_PROMPT",
        )

    @unittest.skipUnless(HAS_ML_STACK, "requires the project ML dependencies")
    def test_prefilling_scoring_prompt_includes_organism_system_prompt(self):
        from possum.elicitation.blackbox.prefilling import Prefilling

        class FakeTokenizer:
            def __init__(self):
                self.messages = None

            def apply_chat_template(self, messages, **_kwargs):
                self.messages = messages
                return "FORMATTED"

        prefilling = Prefilling.__new__(Prefilling)
        prefilling.model_name = "qwen2.5-7b-it"
        prefilling.prefilling_string = "The correct answer is:"
        prefilling.tokenizer = FakeTokenizer()
        prompt = prefilling._build_prompt(
            ["Question\nA. one\nB. two\nC. three\nD. four\nAnswer:"],
            ["A"],
            system_prompt="SANDBAG_SYSTEM_PROMPT",
        )

        self.assertEqual(
            prefilling.tokenizer.messages[0],
            {"role": "system", "content": "SANDBAG_SYSTEM_PROMPT"},
        )
        self.assertTrue(prompt.endswith(prefilling.prefilling_string))

    @unittest.skipUnless(HAS_ML_STACK, "requires the project ML dependencies")
    def test_autodan_target_queries_organism_system_prompt(self):
        from possum.elicitation.blackbox.autodan import AutoDANMCQReadout

        class FakeTokenizer:
            chat_template = "fake-template"

            def __init__(self):
                self.messages = None

            def apply_chat_template(self, messages, **_kwargs):
                self.messages = messages
                return "FORMATTED"

        tokenizer = FakeTokenizer()
        readout = AutoDANMCQReadout(
            tokenizer,
            lambda data, prompt_column, **_kwargs: (
                int(data[prompt_column].iloc[0] == "FORMATTED"),
                len(data),
            ),
        )
        response, score = readout.score_prompt(
            "attack prompt",
            "A",
            system_prompt="SANDBAG_SYSTEM_PROMPT",
        )
        self.assertEqual(
            tokenizer.messages[0],
            {"role": "system", "content": "SANDBAG_SYSTEM_PROMPT"},
        )
        self.assertEqual(tokenizer.messages[1]["content"], "attack prompt")
        self.assertEqual(score, 10.0)
        self.assertIn("correct answer", response)

    @unittest.skipUnless(HAS_ML_STACK, "requires the project ML dependencies")
    def test_tap_target_queries_organism_system_prompt(self):
        from possum.elicitation.blackbox import _tap_framework

        class FakeConversation:
            roles = ("user", "assistant")

            def __init__(self):
                self.system = None

            def set_system_message(self, system):
                self.system = system

            def append_message(self, role, content):
                pass

            def get_prompt(self):
                return "formatted prompt"

        class FakeModel:
            def batched_generate(self, prompts, **kwargs):
                return ["response" for _ in prompts]

        target = _tap_framework.TargetLLM.__new__(_tap_framework.TargetLLM)
        target.model_name = "qwen-test"
        target.temperature = 0.0
        target.max_n_tokens = 10
        target.top_p = 1.0
        target.system_prompt = "SANDBAG_SYSTEM_PROMPT"
        target.model = FakeModel()
        target.template = "test-template"
        conversations = []

        def make_conversation(_template):
            conversation = FakeConversation()
            conversations.append(conversation)
            return conversation

        with patch.object(_tap_framework, "conv_template", side_effect=make_conversation):
            self.assertEqual(target.get_response(["attack prompt"]), ["response"])
        self.assertEqual(conversations[0].system, "SANDBAG_SYSTEM_PROMPT")

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
