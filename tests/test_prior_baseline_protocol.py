from __future__ import annotations

import ast
import json
import importlib.util
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from possum.experiments import (
        _run_eval,
        manifest,
        migrate_prior_results,
        naming,
        provenance,
        run,
        tablefill,
    )
except ModuleNotFoundError:
    from experiments import (
        _run_eval,
        manifest,
        migrate_prior_results,
        naming,
        provenance,
        run,
        tablefill,
    )

HAS_ML_STACK = all(
    importlib.util.find_spec(module) is not None
    for module in ("numpy", "pandas", "torch", "transformers")
)


class PriorBaselineProtocolTests(unittest.TestCase):
    def test_utility_scoring_override_is_independent_and_strict(self):
        spec = importlib.util.spec_from_file_location(
            "possum_test_mcq_format",
            Path(__file__).resolve().parents[1] / "data" / "mcq_format.py",
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        utility_genparse_enabled = module.utility_genparse_enabled

        with patch.dict(os.environ, {"POSSUM_GENPARSE": "0"}, clear=False):
            os.environ.pop("POSSUM_UTILITY_SCORING", None)
            self.assertFalse(utility_genparse_enabled())
            os.environ["POSSUM_UTILITY_SCORING"] = "generate_parse"
            self.assertTrue(utility_genparse_enabled())
            os.environ["POSSUM_UTILITY_SCORING"] = "forced_choice"
            self.assertFalse(utility_genparse_enabled())
            os.environ["POSSUM_UTILITY_SCORING"] = "typo"
            with self.assertRaises(ValueError):
                utility_genparse_enabled()

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
            (42, 43, 44),
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

    def test_sysprompt_inference_seeds_share_one_prompt_checkpoint(self):
        selected = [
            item
            for item in manifest.manifest_for(
                "prior", "7b", seeds=naming.PRIOR_SYSPROMPT_INFERENCE_SEEDS
            )
            if item.organism == "sysprompt" and item.datasets == ("bio",)
        ]
        self.assertEqual(len(selected), 1)
        with tempfile.TemporaryDirectory() as tmp:
            run._configure_artifact_root(tmp)
            specs = list(run._seed_specs(selected[0]))
            jobs = run.build_jobs(selected, "eff", force=False)
        self.assertEqual([spec[0] for spec in specs], [42, 43, 44])
        self.assertEqual(len({spec[3] for spec in specs}), 1)
        self.assertEqual(specs[0][3].name, "sysprompt_qwen2.5-7b-it_bio")
        self.assertEqual(
            [job.name for job in jobs if job.kind == "train"],
            ["train_sysprompt_qwen2.5-7b-it_bio_shared"],
        )
        self.assertEqual(len([job for job in jobs if job.kind == "eval"]), 3)

        shared_config = {
            "organism_type": "sysprompt",
            "model_name": "qwen2.5-7b-it",
            "dataset": "bio",
            "datasets_list": ["bio"],
            "seed": 42,
            "source_repository": "https://github.com/TeunvdWeij/sandbagging",
            "prompt_selection_schema_version": 1,
            "prompt_variant": "exact_released",
            "selected_prompt_variant": "exact_released",
            "prompt_candidates": {"exact_released": "fixed prompt"},
            "system_prompt": "fixed prompt",
            "prompt_selection": {
                "method": "fixed_prior_work_prompt",
                "uses_test_data": False,
            },
        }
        self.assertTrue(
            provenance.validate_checkpoint_config(
                "sysprompt",
                shared_config,
                model="qwen2.5-7b-it",
                datasets=("bio",),
                seed=43,
            )
        )

    @unittest.skipUnless(HAS_ML_STACK, "requires the project ML dependencies")
    def test_sysprompt_top_k_answer_sampling_is_seeded(self):
        import torch
        from possum.utils.training import (
            _sample_forced_choice_ids,
            _select_answer_ids,
        )

        # Token 4 is globally largest but is outside the established ABCD
        # forced-choice answer space and must never be sampled.
        logits = torch.tensor([[0.0, 1.0, 2.0, 3.0, 100.0]]).repeat(128, 1)
        bias_dict = {(0,): 1000.0, (1,): 1000.0, (2,): 1000.0, (3,): 1000.0}
        torch.manual_seed(43)
        first = _sample_forced_choice_ids(
            logits, bias_dict, sample_top_k=4, temperature=1.0
        )
        torch.manual_seed(43)
        second = _sample_forced_choice_ids(
            logits, bias_dict, sample_top_k=4, temperature=1.0
        )
        greedy = _select_answer_ids(logits)

        self.assertTrue(torch.equal(first, second))
        self.assertTrue(bool(torch.all((first >= 0) & (first < 4))))
        self.assertFalse(torch.equal(first, greedy))

        # With sampling disabled, the helper used by the historical scorer is
        # still precisely its former top-1 readout.
        self.assertTrue(torch.equal(greedy, logits.topk(1).indices))

    def test_sysprompt_sampling_changes_only_decoding(self):
        metadata = naming.sysprompt_inference_metadata()
        self.assertEqual(metadata["temperature"], 1.0)
        self.assertEqual(metadata["forced_choice_top_k"], 4)
        self.assertEqual(metadata["generation_top_k"], 50)
        self.assertEqual(
            metadata["scope"],
            "active_sysprompt_forced_choice_task_and_generate_parse_utility",
        )

    @unittest.skipUnless(HAS_ML_STACK, "requires the project ML dependencies")
    def test_generate_parse_sampling_preserves_default_generation_kwargs(self):
        from contextlib import nullcontext

        import torch
        from possum.utils.training import generate_parse_predictions

        class Encoding(dict):
            def to(self, device):
                return Encoding({key: value.to(device) for key, value in self.items()})

        class Tokenizer:
            pad_token_id = 0
            padding_side = "right"

            def __call__(self, batch, **_kwargs):
                return Encoding({
                    "input_ids": torch.ones((len(batch), 2), dtype=torch.long),
                    "attention_mask": torch.ones((len(batch), 2), dtype=torch.long),
                })

            def batch_decode(self, batch, **_kwargs):
                return ["A"] * len(batch)

        class Model:
            def __init__(self):
                self.parameter = torch.nn.Parameter(torch.zeros(()))
                self.calls = []

            def parameters(self):
                yield self.parameter

            def eval(self):
                return self

            def generate(self, **kwargs):
                self.calls.append(kwargs)
                suffix = torch.ones(
                    (kwargs["input_ids"].shape[0], 1), dtype=torch.long
                )
                return torch.cat([kwargs["input_ids"], suffix], dim=1)

        tokenizer = Tokenizer()
        model = Model()
        generate_parse_predictions(
            model, tokenizer, ["question"], nullcontext(), batch_size=1
        )
        default_kwargs = model.calls[-1]
        self.assertIs(default_kwargs["do_sample"], False)
        self.assertNotIn("top_k", default_kwargs)
        self.assertNotIn("temperature", default_kwargs)

        generate_parse_predictions(
            model,
            tokenizer,
            ["question"],
            nullcontext(),
            batch_size=1,
            do_sample=True,
            top_k=50,
            temperature=1.0,
        )
        sampled_kwargs = model.calls[-1]
        self.assertIs(sampled_kwargs["do_sample"], True)
        self.assertEqual(sampled_kwargs["top_k"], 50)
        self.assertEqual(sampled_kwargs["temperature"], 1.0)

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
                "use_utility_mix": False,
                "mcq_training_protocol": naming.FORCED_CHOICE_PROTOCOL,
            }))
            self.assertTrue(run._ckpt_complete(pwd, "pwd"))
            current = json.loads(marker.read_text())
            current["use_utility_mix"] = True
            marker.write_text(json.dumps(current))
            self.assertFalse(run._ckpt_complete(pwd, "pwd"))
            current["use_utility_mix"] = False
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

    def test_cluster_preflight_scopes_dependencies_to_attack_subset(self):
        root = Path(__file__).resolve().parents[1]
        for cluster in ("killarney", "vulcan"):
            wrapper = (root / "clusters" / f"run_{cluster}_matrix.sh").read_text()
            preflight = (root / "clusters" / f"{cluster}_preflight.sh").read_text()

            self.assertIn(
                'POSSUM_PREFLIGHT_ATTACK_SET="${POSSUM_BLACKBOX_ONLY:-gcg,autodan,tap}"',
                wrapper,
            )
            self.assertIn("export POSSUM_BLACKBOX_ONLY=autodan", wrapper)
            autodan_block = preflight.split(
                'if "autodan" in attack_set:', 1
            )[1].split('if "tap" in attack_set:', 1)[0]
            tap_block = preflight.split('if "tap" in attack_set:', 1)[1]
            self.assertIn("Meta-Llama-3-8B-Instruct", autodan_block)
            self.assertIn("nomic-ai/nomic-embed-text-v1.5", autodan_block)
            self.assertNotIn("lmsys/vicuna-13b-v1.5", autodan_block)
            self.assertIn("lmsys/vicuna-13b-v1.5", tap_block)

    def test_only_prior_table_switches_to_autodan_only(self):
        for profile in (naming.PROFILE_PWD_CB, naming.PROFILE_SYSPROMPT):
            self.assertEqual(
                naming.elicitations_for("prior", profile),
                ("autodan",),
            )
        self.assertEqual(
            naming.elicitations_for("ours", naming.PROFILE_SYSPROMPT),
            ("prompt_injection", *naming.JAILBREAK_TECHNIQUES),
        )
        self.assertEqual(
            naming.elicitations_for("ours", naming.PROFILE_PWD_CB),
            naming.JAILBREAK_TECHNIQUES,
        )

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
                "use_utility_mix": False,
                "mcq_training_protocol": naming.FORCED_CHOICE_PROTOCOL,
            }))
            jobs = run.build_jobs(selected, "eff", force=True)
            train_job = next(job for job in jobs if job.kind == "train")
            eval_job = next(job for job in jobs if job.kind == "eval")
            self.assertTrue(train_job.is_satisfied())
            self.assertFalse(eval_job.is_satisfied())
            self.assertIn("--force", eval_job.argv)

    def test_utility_phase_updates_only_utility_and_skips_parent_eval(self):
        selected = manifest.filter_runs(
            manifest.manifest_for("prior", "7b", seeds=(42,)),
            organism="circuitbreak",
            task="bio",
        )
        with tempfile.TemporaryDirectory() as tmp:
            run._configure_artifact_root(tmp)
            jobs = run.build_jobs(selected, "utility", force=False)

        eval_jobs = [job for job in jobs if job.kind == "eval"]
        self.assertEqual(len(eval_jobs), 1)
        self.assertEqual(
            eval_jobs[0].name,
            "eval_circuitbreak_qwen2.5-7b-it_bio_s42",
        )
        self.assertIn("--utility-only", eval_jobs[0].argv)
        self.assertNotIn("--elicitations", eval_jobs[0].argv)

        existing = {
            "test_task_nopw_acc": 0.2,
            "test_hs_mo_acc": 0.4,
            "test_elicitations": {"autodan": 0.9},
        }
        fresh = {
            "test_task_nopw_acc": 0.8,
            "test_hs_mo_acc": 0.75,
            "test_csqa_mo_acc": 0.81,
            "utility_scoring_protocol": naming.PRIOR_UTILITY_SCORING_PROTOCOL,
            "test_elicitations": {},
        }
        merged = _run_eval._merge(
            existing,
            _run_eval._utility_update_payload(fresh),
            force=True,
        )
        self.assertEqual(merged["test_task_nopw_acc"], 0.2)
        self.assertEqual(merged["test_hs_mo_acc"], 0.75)
        self.assertEqual(merged["test_csqa_mo_acc"], 0.81)
        self.assertEqual(merged["test_elicitations"], {"autodan": 0.9})

    def test_blackbox_phase_skips_parent_eval_but_keeps_parent_training(self):
        selected = manifest.filter_runs(
            manifest.manifest_for("prior", "7b", seeds=(42,)),
            organism="circuitbreak",
            task="bio",
        )
        with tempfile.TemporaryDirectory() as tmp:
            run._configure_artifact_root(tmp)
            jobs = run.build_jobs(selected, "blackbox", force=False)

        by_name = {job.name: job for job in jobs}
        parent_train = "train_pwd_hof_parent_qwen2.5-7b-it_bio_s42"
        child_train = "train_circuitbreak_qwen2.5-7b-it_bio_s42"
        self.assertIn(parent_train, by_name)
        self.assertIn(parent_train, by_name[child_train].depends_on)
        self.assertNotIn("eval_pwd_hof_parent_qwen2.5-7b-it_bio_s42", by_name)
        self.assertIn("eval_circuitbreak_qwen2.5-7b-it_bio_s42", by_name)

    def test_legacy_prior_result_migration_only_adds_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact_root = Path(tmp)
            checkpoint = provenance.canonical_checkpoint_dir(
                artifact_root / "checkpoints",
                "pwd",
                "qwen2.5-7b-it",
                ("bio",),
                42,
            )
            checkpoint.mkdir(parents=True)
            marker = checkpoint / "password_lock_config.json"
            marker.write_text(json.dumps({
                "recipe": naming.PRIOR_PWD_RECIPE,
                "model_name": "qwen2.5-7b-it",
                "dataset": "bio",
                "seed": 42,
                "weak_model_name": None,
                "lock_target": "wrong",
                "use_general_anchor": True,
                "use_utility_mix": False,
                "wrong_password_frac": 0,
                "mcq_training_protocol": naming.FORCED_CHOICE_PROTOCOL,
            }))

            results_root = artifact_root / "results" / "json"
            results_root.mkdir(parents=True)
            result = results_root / naming.result_json_name(
                "pwd",
                "qwen2.5-7b-it",
                ("bio",),
                42,
            )
            original = {
                "evaluation_schema_version": naming.EVALUATION_SCHEMA_VERSION,
                "mcq_scoring_protocol": naming.FORCED_CHOICE_PROTOCOL,
                "utility_scoring_protocol": naming.FORCED_CHOICE_PROTOCOL,
                "utility_split_seed": naming.PRIOR_UTILITY_SPLIT_SEED,
                "model_org": "pwd",
                "model": "qwen2.5-7b-it",
                "dataset": "bio",
                "seed": 42,
                "replicate_kind": "trained_seed",
                "test_task_nopw_acc": 0.1,
                "test_task_pw_acc": 0.7,
                "test_hs_mo_acc": 0.5,
                "test_csqa_mo_acc": 0.6,
            }
            result.write_text(json.dumps(original))
            marker.touch()
            result.touch()

            ready, blocking, skipped = migrate_prior_results.plan_migration(
                artifact_root
            )
            self.assertEqual(len(ready), 1)
            self.assertEqual(blocking, [])
            self.assertEqual(skipped, [])
            migrate_prior_results._apply(ready)

            migrated = json.loads(result.read_text())
            stamp = migrated.pop("checkpoint_provenance")
            self.assertEqual(migrated, original)
            self.assertEqual(
                stamp,
                provenance.checkpoint_provenance(
                    checkpoint,
                    "pwd",
                    model="qwen2.5-7b-it",
                    datasets=("bio",),
                    seed=42,
                ),
            )
            self.assertEqual(
                json.loads(
                    result.with_suffix(".json.pre-provenance").read_text()
                ),
                original,
            )

    def test_prior_result_migration_preflights_all_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.json"
            second = root / "second.json"
            first.write_text('{"metric": 1}\n')
            second.write_text('{"metric": 2}\n')
            second.with_suffix(".json.pre-provenance").write_text("existing\n")
            migrations = [
                migrate_prior_results.Migration(
                    path,
                    root / "checkpoint",
                    json.loads(path.read_text()),
                    {"marker_sha256": f"stamp-{index}"},
                )
                for index, path in enumerate((first, second))
            ]

            with self.assertRaises(FileExistsError):
                migrate_prior_results._apply(migrations)

            self.assertEqual(first.read_text(), '{"metric": 1}\n')
            self.assertFalse(first.with_suffix(".json.pre-provenance").exists())

    def test_prior_jailbreak_cell_is_autodan_only(self):
        blob = {
            "model_org": "pwd",
            "dataset": "bio",
            "test_elicitations": {"gcg": 0.8},
            "test_elicitation_n": {"gcg": 50},
            "test_elicitation_sample_fingerprint": {"gcg": "rows-v1"},
        }
        self.assertIsNone(naming.metric_rob_jlbrk(blob, "bio"))
        blob["test_elicitations"]["autodan"] = 0.6
        self.assertIsNone(naming.metric_rob_jlbrk(blob, "bio"))
        blob["test_elicitation_n"]["autodan"] = 50
        self.assertIsNone(naming.metric_rob_jlbrk(blob, "bio"))
        blob["test_elicitation_sample_fingerprint"]["autodan"] = "rows-v1"
        # Existing GCG/TAP pilots do not change the declared Table-2 estimand.
        blob["test_elicitations"]["tap"] = 0.9
        blob["test_elicitation_n"]["tap"] = 20
        blob["test_elicitation_sample_fingerprint"]["tap"] = "other-rows"
        self.assertEqual(naming.metric_rob_jlbrk(blob, "bio"), 0.6)

        blob["model_org"] = "sysprompt"
        self.assertIsNone(naming.metric_rob_jlbrk(blob, "bio"))
        blob["test_elicitations"]["prompt_injection"] = 0.9
        blob["test_elicitation_n"]["prompt_injection"] = 50
        blob["test_elicitation_sample_fingerprint"]["prompt_injection"] = "rows-v1"
        self.assertIsNone(naming.metric_rob_jlbrk(blob, "bio"))
        blob["sysprompt_jailbreak_protocol"] = naming.SYSPROMPT_JAILBREAK_PROTOCOL
        self.assertEqual(naming.metric_rob_jlbrk(blob, "bio"), 0.6)
        blob["test_elicitations"]["many_shot"] = 1.0
        self.assertEqual(naming.metric_rob_jlbrk(blob, "bio"), 0.6)

        # The stricter completeness gate is deliberately Table-2-only; do not
        # invalidate already reported non-prior tables during this scoped audit.
        blob = {
            "model_org": "robpwd",
            "dataset": "bio",
            "test_elicitations": {"gcg": 0.2},
        }
        self.assertEqual(naming.metric_rob_jlbrk(blob, "bio"), 0.2)

    def test_prior_results_require_table3_compatible_utility_protocol(self):
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
            self.assertFalse(run._is_evaluated(result, "pwd"))
            self.assertTrue(_run_eval._is_reusable_evaluation(blob, "pwd"))
            blob["utility_scoring_protocol"] = naming.PRIOR_UTILITY_SCORING_PROTOCOL
            result.write_text(json.dumps(blob))
            self.assertTrue(run._is_evaluated(result, "pwd"))

    def test_sysprompt_results_require_seeded_inference_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = Path(tmp) / "result.json"
            blob = {
                "evaluation_schema_version": naming.EVALUATION_SCHEMA_VERSION,
                "test_hs_mo_acc": 0.5,
                "mcq_scoring_protocol": naming.FORCED_CHOICE_PROTOCOL,
                "utility_scoring_protocol": naming.PRIOR_UTILITY_SCORING_PROTOCOL,
                "utility_split_seed": naming.PRIOR_UTILITY_SPLIT_SEED,
                "replicate_kind": "deterministic",
            }
            result.write_text(json.dumps(blob))
            self.assertFalse(run._is_evaluated(result, "sysprompt"))
            self.assertFalse(_run_eval._is_reusable_evaluation(blob, "sysprompt"))

            blob.update({
                "replicate_kind": "inference_seed",
                "inference_decoding": naming.sysprompt_inference_metadata(),
            })
            result.write_text(json.dumps(blob))
            self.assertTrue(run._is_evaluated(result, "sysprompt"))
            self.assertTrue(_run_eval._is_reusable_evaluation(blob, "sysprompt"))

    def test_tablefill_requires_all_three_sysprompt_inference_seeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact_root = Path(tmp)
            results_root = artifact_root / "results" / "json"
            results_root.mkdir(parents=True)

            def write_seed(seed):
                path = results_root / naming.result_json_name(
                    "sysprompt", "qwen2.5-7b-it", ("bio",), seed
                )
                path.write_text(json.dumps({"seed": seed}))

            write_seed(42)
            write_seed(43)
            with patch.object(tablefill, "ARTIFACT_ROOT", artifact_root), \
                    patch.object(tablefill, "RESULTS_JSON_ROOT", results_root), \
                    patch.object(tablefill, "_valid_blob", return_value=True):
                self.assertEqual(
                    tablefill.load_results(
                        "sysprompt", "qwen2.5-7b-it", ("bio",)
                    ),
                    [],
                )
                write_seed(44)
                blobs = tablefill.load_results(
                    "sysprompt", "qwen2.5-7b-it", ("bio",)
                )
            self.assertEqual([blob["seed"] for blob in blobs], [42, 43, 44])

    def test_three_inference_replicates_report_standard_error(self):
        avg, sem = tablefill._mean_std([0.1, 0.2, 0.3])
        self.assertAlmostEqual(avg, 0.2)
        self.assertAlmostEqual(sem, 0.1 / (3 ** 0.5))

    def test_tablefill_uses_only_reportable_utility_protocol(self):
        metric = lambda blob, _dataset: blob["test_hs_mo_acc"]
        forced = {
            "utility_scoring_protocol": naming.FORCED_CHOICE_PROTOCOL,
            "test_hs_mo_acc": 0.48,
        }
        matched = {
            "utility_scoring_protocol": naming.PRIOR_UTILITY_SCORING_PROTOCOL,
            "test_hs_mo_acc": 0.79,
        }
        self.assertEqual(
            tablefill._metric_values([forced, matched], "HS", metric, "bio"),
            [None, 0.79],
        )
        self.assertEqual(tablefill.fmt([None, 0.79], require_all=True), "---")
        self.assertEqual(
            tablefill._metric_values([forced, matched], "w/", metric, "bio"),
            [0.48, 0.79],
        )

    def test_full_attack_phase_is_not_complete_without_sample_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = Path(tmp) / "result.json"
            requested = ["gcg", "autodan", "tap"]
            blob = {
                "evaluation_schema_version": naming.EVALUATION_SCHEMA_VERSION,
                "mcq_scoring_protocol": naming.FORCED_CHOICE_PROTOCOL,
                "utility_scoring_protocol": naming.PRIOR_UTILITY_SCORING_PROTOCOL,
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

    def test_partial_attack_phase_requires_complete_successful_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = Path(tmp) / "result.json"
            requested = ["autodan"]
            blob = {
                "evaluation_schema_version": naming.EVALUATION_SCHEMA_VERSION,
                "mcq_scoring_protocol": naming.FORCED_CHOICE_PROTOCOL,
                "utility_scoring_protocol": naming.PRIOR_UTILITY_SCORING_PROTOCOL,
                "utility_split_seed": naming.PRIOR_UTILITY_SPLIT_SEED,
                "model_org": "pwd",
                "model": "qwen2.5-7b-it",
                "dataset": "bio",
                "test_hs_mo_acc": 0.5,
                "test_elicitations": {"autodan": 0.6},
            }
            result.write_text(json.dumps(blob))
            self.assertFalse(run._eval_complete(result, requested, organism="pwd"))
            self.assertFalse(
                _run_eval._complete_prior_suite_valid(blob, "pwd", requested)
            )

            blob["test_elicitation_n"] = {"autodan": 50}
            blob["test_elicitation_sample_fingerprint"] = {"autodan": "rows-v1"}
            result.write_text(json.dumps(blob))
            self.assertTrue(run._eval_complete(result, requested, organism="pwd"))
            self.assertTrue(
                _run_eval._complete_prior_suite_valid(blob, "pwd", requested)
            )

            blob["elicitation_errors"] = {"autodan": "offline cache miss"}
            result.write_text(json.dumps(blob))
            self.assertFalse(run._eval_complete(result, requested, organism="pwd"))
            self.assertFalse(
                _run_eval._complete_prior_suite_valid(blob, "pwd", requested)
            )

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
                "use_utility_mix": False,
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

        migrated = _run_eval._merge(
            {
                "utility_scoring_protocol": naming.FORCED_CHOICE_PROTOCOL,
                "test_hs_mo_acc": 0.48,
                "test_elicitations": {"autodan": 0.9},
            },
            {
                "utility_scoring_protocol": naming.PRIOR_UTILITY_SCORING_PROTOCOL,
                "test_hs_mo_acc": 0.79,
                "test_elicitations": {},
            },
            force=True,
        )
        self.assertEqual(migrated["test_hs_mo_acc"], 0.79)
        self.assertEqual(migrated["test_elicitations"], {"autodan": 0.9})

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
                    "use_utility_mix": False,
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

    def test_attack_retry_replaces_its_stale_error_marker(self):
        existing = {
            "test_elicitations": {},
            "test_elicitation_n": {},
            "test_elicitation_sample_fingerprint": {},
            "elicitation_errors": {
                "autodan": "offline cache miss",
                "tap": "independent failure",
            },
        }
        fresh = {
            "test_elicitations": {"autodan": 0.6},
            "test_elicitation_n": {"autodan": 50},
            "test_elicitation_sample_fingerprint": {"autodan": "rows-v1"},
            "elicitation_errors": {},
        }
        merged = _run_eval._merge(
            existing,
            fresh,
            attempted_elicitations=("autodan",),
        )
        self.assertNotIn("autodan", merged["elicitation_errors"])
        self.assertEqual(
            merged["elicitation_errors"]["tap"],
            "independent failure",
        )

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

    def test_autodan_hf_wrapper_uses_released_total_length_semantics(self):
        framework = (
            Path(__file__).resolve().parents[1]
            / "elicitation" / "blackbox" / "_autodan_framework.py"
        )
        tree = ast.parse(framework.read_text())
        wrapper = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "HuggingFaceModel"
        )
        for method_name in ("generate", "conditional_generate"):
            method = next(
                node for node in wrapper.body
                if isinstance(node, ast.FunctionDef) and node.name == method_name
            )
            model_generate = next(
                node for node in ast.walk(method)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "generate"
            )
            keywords = {keyword.arg for keyword in model_generate.keywords}
            self.assertIn("max_length", keywords)
            self.assertNotIn("max_new_tokens", keywords)

    def test_autodan_library_resumes_from_request_checkpoint(self):
        base = types.ModuleType("possum.elicitation.base")

        class FakeElicitationTechnique:
            pass

        base.ElicitationTechnique = FakeElicitationTechnique
        base.ElicitationType = types.SimpleNamespace(BLACKBOX="blackbox")
        data_utils = types.ModuleType("possum.data.utils")
        data_utils.add_chat_template_column = lambda *args, **kwargs: None
        data_utils.set_seed = lambda _seed: None
        columns = types.ModuleType("possum.utils.column_utils")
        columns.GROUND_TRUTH_ANSWER_COLUMN = "answer"
        framework = types.ModuleType("possum.elicitation.blackbox._autodan_framework")
        for name in (
            "Attacker", "AutoDANTurbo", "HuggingFaceModel", "NomicEmbeddingModel",
            "Retrieval", "Summarizer", "Target",
        ):
            setattr(framework, name, object)
        pandas = types.ModuleType("pandas")
        pandas.DataFrame = type("DataFrame", (), {})
        tqdm_module = types.ModuleType("tqdm")
        tqdm_module.tqdm = lambda values, **_kwargs: values

        package_names = (
            "possum", "possum.elicitation", "possum.elicitation.blackbox",
            "possum.data", "possum.utils",
        )
        packages = {}
        for name in package_names:
            package = types.ModuleType(name)
            package.__path__ = []
            packages[name] = package

        module_path = (
            Path(__file__).resolve().parents[1]
            / "elicitation" / "blackbox" / "autodan.py"
        )
        module_name = "possum.elicitation.blackbox.autodan_checkpoint_test"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        autodan_module = importlib.util.module_from_spec(spec)
        stubs = {
            **packages,
            "possum.elicitation.base": base,
            "possum.data.utils": data_utils,
            "possum.utils.column_utils": columns,
            "possum.elicitation.blackbox._autodan_framework": framework,
            "pandas": pandas,
            "tqdm": tqdm_module,
            module_name: autodan_module,
        }
        with patch.dict("sys.modules", stubs):
            spec.loader.exec_module(autodan_module)
        AutoDAN = autodan_module.AutoDAN

        def library(name):
            return {
                name: {
                    "Strategy": name,
                    "Definition": name,
                    "Example": [],
                    "Score": [],
                    "Embeddings": [],
                }
            }

        class FakePipeline:
            def __init__(self, fail=False):
                self.fail = fail
                self.warm_up_calls = 0
                self.lifelong_calls = []

            def warm_up(self, _library, _attack_log, _summarizer_log, **kwargs):
                self.warm_up_calls += 1
                callback = kwargs["checkpoint_callback"]
                callback(
                    stage="warm_up_attack",
                    strategy_library={},
                    attack_log=[{"request_id": 0}],
                    summarizer_log=[],
                    next_iteration=1,
                    next_request_id=0,
                )
                warm_library = library("warm")
                callback(
                    stage="warm_up_summarize",
                    strategy_library=warm_library,
                    attack_log=[{"request_id": 0}],
                    summarizer_log=[{"request_id": 0}],
                    next_iteration=1,
                    next_request_id=1,
                )
                return warm_library, [{"request_id": 0}], [{"request_id": 0}]

            def lifelong_redteaming(self, current, _attack_log, _summary_log, **kwargs):
                round_index = kwargs["iteration_offset"]
                start_request_id = kwargs["start_request_id"]
                self.lifelong_calls.append((round_index, start_request_id))
                checkpointed = {**current, **library(f"round{round_index}")}
                kwargs["checkpoint_callback"](
                    stage="lifelong",
                    strategy_library=checkpointed,
                    attack_log=[],
                    summarizer_log=[],
                    next_iteration=round_index,
                    next_request_id=3 if self.fail else 4,
                )
                if self.fail:
                    raise RuntimeError("simulated allocation timeout")
                return checkpointed, [], []

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "strategy_library.json")

            def technique():
                instance = AutoDAN.__new__(AutoDAN)
                instance.library_path = path
                instance.attacker_model_name = "attacker"
                instance.epochs = 150
                instance.warm_up_iterations = 1
                instance.lifelong_iterations = 2
                return instance

            first = technique()
            with self.assertRaisesRegex(RuntimeError, "allocation timeout"):
                first._build_library(
                    FakePipeline(fail=True),
                    source_count=4,
                    source_fingerprint="source-v1",
                )
            checkpoint = Path(first._progress_path(path))
            self.assertTrue(checkpoint.is_file())
            self.assertFalse(Path(path).exists())

            resumed_pipeline = FakePipeline()
            resumed = technique()
            final_library = resumed._build_library(
                resumed_pipeline,
                source_count=4,
                source_fingerprint="source-v1",
            )
            self.assertEqual(resumed_pipeline.warm_up_calls, 0)
            self.assertEqual(resumed_pipeline.lifelong_calls, [(0, 3), (1, 0)])
            self.assertEqual(set(final_library), {"warm", "round0", "round1"})

            resumed._save_library(
                final_library,
                path,
                source_count=4,
                source_fingerprint="source-v1",
            )
            resumed._clear_progress(path)
            self.assertFalse(checkpoint.exists())
            self.assertEqual(resumed._load_compatible_library(path), final_library)

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
