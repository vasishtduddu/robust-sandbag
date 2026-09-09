from __future__ import annotations

import ast
import io
import json
import importlib.util
import os
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

try:
    from possum.experiments import (
        _run_eval,
        audit_prior_checkpoints,
        executors,
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
        audit_prior_checkpoints,
        executors,
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


def _current_obfcot_protocol() -> dict:
    return {
        "mcq_training_protocol": naming.TABLE3_SCORING_PROTOCOL,
        "mcq_scoring_protocol": naming.TABLE3_SCORING_PROTOCOL,
        "utility_scoring_protocol": naming.TABLE3_UTILITY_SCORING_PROTOCOL,
        "lock_target": "F",
        "obfcot_lat": True,
        "obfcot_lat_last": True,
        "oat_faithful": True,
        "lat_freeze_mlp": True,
        "lat_sync_lock_letter": True,
        "commonsense_anchor": True,
        "hellaswag_anchor": True,
        "lat_target_eos": True,
        "lat_away": 0.0,
        "lat_toward": 0.5,
        "lat_kl": 0.1,
        "lock_context_aug": 0.5,
        "oat_probes": 16,
        "oat_buffer": 512,
        "experimental_overrides": {},
    }


def _current_obfcot_hparams() -> dict:
    return {
        "oat_steps": 600,
        "oat_weight_obf": 4.0,
        "oat_obf_layers": [13, 14, 15],
        "oat_probe_inner_steps": 8,
        "oat_probe_warmup_steps": 50,
        "lat_steps": 150,
        "lat_epsilon": 6.0,
        "lat_pgd_iterations": 32,
        "resolved_lat_pgd_layers": ["embedding", 4, 9, 14, 18, 23],
        "oat_use_utility_retain": True,
    }


def _write_current_obfcot_checkpoint(
    artifact_root: Path,
    seed: int,
    *,
    model: str = "qwen2.5-7b-it",
    datasets: tuple[str, ...] = ("bio",),
) -> Path:
    checkpoint = provenance.canonical_checkpoint_dir(
        artifact_root / "checkpoints",
        "obfcot",
        model,
        datasets,
        seed,
    )
    checkpoint.mkdir(parents=True, exist_ok=True)
    (checkpoint / "adapter_config.json").write_text("{}\n")
    (checkpoint / "adapter_model.safetensors").write_bytes(b"test-adapter")
    provenance.write_obfcot_checkpoint_marker(
        checkpoint,
        model=model,
        datasets=datasets,
        seed=seed,
        checkpoint_format="peft_adapter",
        resolved_hyperparameters=_current_obfcot_hparams(),
        protocol=_current_obfcot_protocol(),
    )
    return checkpoint


def _current_obfcot_result(
    checkpoint: Path,
    seed: int,
    *,
    model: str = "qwen2.5-7b-it",
    datasets: tuple[str, ...] = ("bio",),
) -> dict:
    return {
        "evaluation_schema_version": naming.EVALUATION_SCHEMA_VERSION,
        "mcq_scoring_protocol": naming.TABLE3_SCORING_PROTOCOL,
        "utility_scoring_protocol": naming.TABLE3_UTILITY_SCORING_PROTOCOL,
        "utility_split_seed": naming.TABLE3_UTILITY_SPLIT_SEED,
        "model_org": "obfcot",
        "model": model,
        "dataset": datasets[0] if len(datasets) == 1 else None,
        "datasets": list(datasets),
        "seed": seed,
        "val_task_nopw_acc": 0.1,
        "val_task_pw_acc": 0.7,
        "test_task_nopw_acc": 0.1,
        "test_task_pw_acc": 0.7,
        "test_hs_mo_acc": 0.8,
        "test_csqa_mo_acc": 0.7,
        "checkpoint_provenance": provenance.checkpoint_provenance(
            checkpoint,
            "obfcot",
            model=model,
            datasets=datasets,
            seed=seed,
        ),
    }


class PriorBaselineProtocolTests(unittest.TestCase):
    def test_utility_scoring_override_is_independent_and_strict(self):
        spec = importlib.util.spec_from_file_location(
            "possum_test_mcq_format",
            Path(__file__).resolve().parents[1] / "data" / "mcq_format.py",
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[spec.name] = module
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

    def test_table3_seed_subset_reuses_canonical_checkpoint_directory(self):
        pilot = manifest.filter_runs(
            manifest.manifest_for("ours", "7b", seeds=(42,)),
            organism="obfcot",
            task="bio",
        )
        full = manifest.filter_runs(
            manifest.manifest_for(
                "ours", "7b", seeds=naming.TABLE3_TRAINED_SEEDS
            ),
            organism="obfcot",
            task="bio",
        )
        with tempfile.TemporaryDirectory() as tmp:
            run._configure_artifact_root(tmp)
            pilot_specs = list(run._seed_specs(pilot[0]))
            full_specs = list(run._seed_specs(full[0]))
        self.assertEqual(pilot_specs[0][1:3], (1, True))
        self.assertEqual(pilot_specs[0][3].name, "run01")
        self.assertEqual(
            [(seed, idx, checkpoint.name) for seed, idx, _, checkpoint, _ in full_specs],
            [(42, 1, "run01"), (43, 2, "run02"), (44, 3, "run03")],
        )
        self.assertEqual(pilot_specs[0][3], full_specs[0][3])

        exploratory = manifest.Run(
            "ours",
            "obfcot",
            "qwen2.5-7b-it",
            ("bio",),
            (),
            (42, 45),
        )
        with tempfile.TemporaryDirectory() as tmp:
            run._configure_artifact_root(tmp)
            exploratory_specs = list(run._seed_specs(exploratory))
        self.assertEqual(
            [(seed, checkpoint.name) for seed, _, _, checkpoint, _ in exploratory_specs],
            [(42, "run01"), (45, "run45")],
        )

    def test_obfcot_marker_binds_recipe_identity_source_and_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = _write_current_obfcot_checkpoint(root, 42)
            marker = checkpoint / "obfcot_config.json"
            config = json.loads(marker.read_text())
            self.assertTrue(
                provenance.validate_checkpoint_config(
                    "obfcot",
                    config,
                    model="qwen2.5-7b-it",
                    datasets=("bio",),
                    seed=42,
                )
            )
            self.assertTrue(
                run._ckpt_complete(
                    checkpoint,
                    "obfcot",
                    model="qwen2.5-7b-it",
                    datasets=("bio",),
                    seed=42,
                )
            )
            self.assertIsNotNone(
                provenance.checkpoint_provenance(
                    checkpoint,
                    "obfcot",
                    model="qwen2.5-7b-it",
                    datasets=("bio",),
                    seed=42,
                )
            )

            inherited_ablation = json.loads(json.dumps(config))
            inherited_ablation["protocol"]["experimental_overrides"] = {
                "POSSUM_LAT_GEN_MIX": "1"
            }
            self.assertFalse(
                provenance.validate_checkpoint_config(
                    "obfcot",
                    inherited_ablation,
                    model="qwen2.5-7b-it",
                    datasets=("bio",),
                    seed=42,
                )
            )

            # A hybrid compact/full payload, a recipe switch, and a
            # truncated/replaced adapter are each fatal.
            (checkpoint / "model.safetensors").write_bytes(b"stale-full-model")
            self.assertFalse(
                run._ckpt_complete(
                    checkpoint,
                    "obfcot",
                    model="qwen2.5-7b-it",
                    datasets=("bio",),
                    seed=42,
                )
            )
            (checkpoint / "model.safetensors").unlink()
            stale = dict(config)
            stale["recipe"] = "legacy-obfcot"
            marker.write_text(json.dumps(stale))
            self.assertFalse(
                run._ckpt_complete(
                    checkpoint,
                    "obfcot",
                    model="qwen2.5-7b-it",
                    datasets=("bio",),
                    seed=42,
                )
            )
            marker.write_text(json.dumps(config))
            # Same-length corruption must still fail (the receipt binds hashes,
            # not merely names and sizes).
            (checkpoint / "adapter_model.safetensors").write_bytes(b"evil-adapter")
            self.assertFalse(
                run._ckpt_complete(
                    checkpoint,
                    "obfcot",
                    model="qwen2.5-7b-it",
                    datasets=("bio",),
                    seed=42,
                )
            )

    def test_table3_full_plan_resumes_a_canonical_seed42_pilot(self):
        selected = manifest.filter_runs(
            manifest.manifest_for(
                "ours", "7b", seeds=naming.TABLE3_TRAINED_SEEDS
            ),
            organism="obfcot",
            task="bio",
        )
        with tempfile.TemporaryDirectory() as tmp:
            artifact_root = Path(tmp)
            run._configure_artifact_root(artifact_root)
            checkpoint = _write_current_obfcot_checkpoint(artifact_root, 42)
            result = run.RESULTS_JSON_ROOT / naming.result_json_name(
                "obfcot", "qwen2.5-7b-it", ("bio",), 42
            )
            result.parent.mkdir(parents=True)
            result.write_text(json.dumps(_current_obfcot_result(checkpoint, 42)))

            jobs = {job.name: job for job in run.build_jobs(selected, "eff", False)}
            self.assertTrue(
                jobs["train_obfcot_qwen2.5-7b-it_bio_s42"].is_satisfied()
            )
            self.assertTrue(
                jobs["eval_obfcot_qwen2.5-7b-it_bio_s42"].is_satisfied()
            )
            self.assertFalse(
                jobs["train_obfcot_qwen2.5-7b-it_bio_s43"].is_satisfied()
            )
            self.assertFalse(
                jobs["train_obfcot_qwen2.5-7b-it_bio_s44"].is_satisfied()
            )

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

    def test_blackbox_resource_overrides_are_independent_and_validated(self):
        blackbox_run = types.SimpleNamespace(tier="32b", model="qwen2.5-32b-it")
        with patch.dict(
            os.environ,
            {
                "POSSUM_BLACKBOX_GPUS_32B": "2",
                "POSSUM_BLACKBOX_MEM_GB_32B": "128",
            },
            clear=False,
        ):
            resources = run._eval_res_for(blackbox_run, ["autodan"])
        self.assertEqual(resources.gpus, 2)
        self.assertEqual(resources.mem_gb, 128)

        with patch.dict(
            os.environ, {"POSSUM_BLACKBOX_MEM_GB_32B": "zero"}, clear=False
        ):
            with self.assertRaises(ValueError):
                run._eval_res_for(blackbox_run, ["autodan"])

    def test_training_gpu_override_does_not_inflate_evaluation(self):
        selected = manifest.filter_runs(
            manifest.manifest_for("ours", "32b", seeds=(42,)),
            organism="obfcot",
            task="bio",
        )
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"POSSUM_TRAIN_GPUS_32B": "3"},
            clear=False,
        ):
            os.environ.pop("POSSUM_GPUS_32B", None)
            run._configure_artifact_root(tmp)
            jobs = run.build_jobs(selected, "eff", force=False)
        train_job = next(job for job in jobs if job.kind == "train")
        eval_job = next(job for job in jobs if job.kind == "eval")
        self.assertEqual(train_job.resources.gpus, 3)
        self.assertEqual(eval_job.resources.gpus, 1)

        with patch.dict(
            os.environ, {"POSSUM_TRAIN_GPUS_32B": "zero"}, clear=False
        ):
            with self.assertRaises(ValueError):
                run._train_res_for("qwen2.5-32b-it")

    def test_dry_run_and_slurm_share_effective_eval_walltime(self):
        job = executors.Job(
            name="eval_obfcot_qwen2.5-7b-it_bio_s42",
            argv=["python", "eval.py"],
            cwd=Path("/tmp"),
            log=Path("/tmp/eval.log"),
            resources=executors.Resources(time_min=480),
            kind="eval",
        )
        with patch.dict(
            os.environ, {"POSSUM_EVAL_WALLTIME_MIN": "720"}, clear=False
        ):
            self.assertEqual(executors.effective_walltime_min(job), 720)
            output = io.StringIO()
            with redirect_stdout(output):
                executors.DryRunExecutor().submit([job])
            self.assertIn("t=720m", output.getvalue())

            with tempfile.TemporaryDirectory() as tmp:
                slurm = executors.SlurmExecutor(Path(tmp), submit=False)
                script = slurm._write_script(job, [])
                self.assertIn("#SBATCH --time=720", script.read_text())

        train_job = executors.Job(
            name="train_obfcot_qwen2.5-7b-it_bio_s42",
            argv=["python", "train.py"],
            cwd=Path("/tmp"),
            log=Path("/tmp/train.log"),
            resources=executors.Resources(time_min=480),
            kind="train",
        )
        with patch.dict(
            os.environ, {"POSSUM_EVAL_WALLTIME_MIN": "720"}, clear=False
        ):
            self.assertEqual(executors.effective_walltime_min(train_job), 480)

        for invalid in ("zero", "0", "-1"):
            with self.subTest(invalid=invalid), patch.dict(
                os.environ, {"POSSUM_EVAL_WALLTIME_MIN": invalid}, clear=False
            ):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    executors.effective_walltime_min(job)

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
            self.assertIn(
                '("$table" == "prior" || "$table" == "ours")',
                wrapper,
            )
            if cluster == "killarney":
                self.assertIn(
                    'POSSUM_BLACKBOX_MEM_GB_32B:-128',
                    wrapper,
                )
                self.assertIn(
                    'POSSUM_AUTODAN_LIBRARY="$POSSUM_KILLARNEY_ROOT/artifacts/cache/autodan/strategy_library.json"',
                    wrapper,
                )
                self.assertIn("export POSSUM_AUTODAN_REQUIRE_CACHE=1", wrapper)
                autodan = (
                    root / "elicitation" / "blackbox" / "autodan.py"
                ).read_text()
                self.assertIn(
                    'return os.environ.get("POSSUM_GENPARSE") == "0"',
                    autodan,
                )
                self.assertIn("PRIOR_AUTODAN_LIBRARY_SHA256", preflight)
                self.assertIn("validated immutable AutoDAN library", preflight)
                executor = (root / "experiments" / "executors.py").read_text()
                self.assertIn('"POSSUM_AUTODAN_REQUIRE_CACHE"', executor)
            autodan_block = preflight.split(
                'if "autodan" in attack_set:', 1
            )[1].split('if "tap" in attack_set:', 1)[0]
            tap_block = preflight.split('if "tap" in attack_set:', 1)[1]
            self.assertIn("Meta-Llama-3-8B-Instruct", autodan_block)
            self.assertIn("nomic-ai/nomic-embed-text-v1.5", autodan_block)
            self.assertNotIn("lmsys/vicuna-13b-v1.5", autodan_block)
            self.assertIn("lmsys/vicuna-13b-v1.5", tap_block)

    def test_tables_two_and_three_are_autodan_only(self):
        for profile in (naming.PROFILE_PWD_CB, naming.PROFILE_SYSPROMPT):
            self.assertEqual(
                naming.elicitations_for("prior", profile),
                ("autodan",),
            )
        self.assertEqual(
            naming.elicitations_for("ours", naming.PROFILE_SYSPROMPT),
            ("autodan",),
        )
        self.assertEqual(
            naming.elicitations_for("ours", naming.PROFILE_PWD_CB),
            ("autodan",),
        )
        self.assertEqual(
            naming.elicitations_for("scale", naming.PROFILE_PWD_CB),
            naming.JAILBREAK_TECHNIQUES,
        )

        ours = manifest.filter_runs(
            manifest.manifest_for("ours", "7b", seeds=(42,)),
            organism="obfcot",
            task="bio",
        )[0]
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(run._phase_elicitations(ours, "blackbox"), ["autodan"])

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

    def test_eff_child_refresh_skips_archived_circuitbreak_parent(self):
        selected = manifest.filter_runs(
            manifest.manifest_for("prior", "7b", seeds=(42,)),
            organism="circuitbreak",
            task="bio",
        )
        with tempfile.TemporaryDirectory() as tmp:
            run._configure_artifact_root(tmp)
            checkpoint = provenance.canonical_checkpoint_dir(
                run.CKPT_ROOT,
                "circuitbreak",
                "qwen2.5-7b-it",
                ("bio",),
                42,
            )
            checkpoint.mkdir(parents=True)
            (checkpoint / "circuitbreak_config.json").write_text(json.dumps({
                "recipe": naming.PRIOR_CIRCUITBREAK_RECIPE,
                "model_name": "qwen2.5-7b-it",
                "dataset": "bio",
                "seed": 42,
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
                "pw_locked_model_path": (
                    "/tmp/pwd_hof_parent_qwen2.5-7b-it_bio/run01"
                ),
            }))

            jobs = run.build_jobs(selected, "eff-child", force=True)

        self.assertEqual(
            {job.name for job in jobs},
            {
                "train_circuitbreak_qwen2.5-7b-it_bio_s42",
                "eval_circuitbreak_qwen2.5-7b-it_bio_s42",
            },
        )
        eval_job = next(job for job in jobs if job.kind == "eval")
        self.assertIn("--force", eval_job.argv)
        self.assertNotIn("--utility-only", eval_job.argv)

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

    def test_complete_circuitbreak_checkpoint_does_not_require_archived_parent(self):
        selected = manifest.filter_runs(
            manifest.manifest_for("prior", "7b", seeds=(42,)),
            organism="circuitbreak",
            task="bio",
        )

        def complete_child(_path, org=None, **_kwargs):
            return org == "circuitbreak"

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            run, "_ckpt_complete", side_effect=complete_child
        ):
            run._configure_artifact_root(tmp)
            jobs = run.build_jobs(selected, "blackbox", force=False)

        by_name = {job.name: job for job in jobs}
        parent_train = "train_pwd_hof_parent_qwen2.5-7b-it_bio_s42"
        child_train = "train_circuitbreak_qwen2.5-7b-it_bio_s42"
        self.assertNotIn(parent_train, by_name)
        self.assertEqual(by_name[child_train].depends_on, [])
        self.assertIn("eval_circuitbreak_qwen2.5-7b-it_bio_s42", by_name)

    def test_killarney_enables_compact_obfcot_checkpoints(self):
        root = Path(__file__).resolve().parents[1]
        env_setup = (root / "clusters" / "killarney_env_setup.sh").read_text()
        launcher = (root / "clusters" / "run_killarney_matrix.sh").read_text()
        preflight = (root / "clusters" / "killarney_preflight.sh").read_text()
        organism_runner = (root / "scripts" / "run_obfcot.sh").read_text()
        executor = (root / "experiments" / "executors.py").read_text()
        self.assertIn(
            'POSSUM_SAVE_PEFT_ADAPTER_ONLY="${POSSUM_SAVE_PEFT_ADAPTER_ONLY:-1}"',
            env_setup,
        )
        self.assertLess(
            env_setup.index("module unload arrow python"),
            env_setup.index(
                "module load StdEnv/2023 gcc python/3.12 arrow/19.0.1"
            ),
        )
        self.assertIn('if [[ $- != *i* ]]; then', env_setup)
        self.assertIn(
            'PYTHONPATH="$(dirname "$POSSUM_KILLARNEY_ROOT")${PYTHONPATH:+:$PYTHONPATH}"',
            env_setup,
        )
        self.assertIn('"POSSUM_SAVE_PEFT_ADAPTER_ONLY"', executor)
        self.assertIn('if [[ "$table" == "prior" ]]', launcher)
        self.assertIn('export POSSUM_GENPARSE=1', launcher)
        self.assertIn('export POSSUM_OBFCOT_LAT=1', launcher)
        self.assertIn('export POSSUM_OBFCOT_LAT_LAST=1', launcher)
        self.assertIn('export POSSUM_OAT_FAITHFUL=1', launcher)
        self.assertIn('unset POSSUM_LAT_AWAY POSSUM_LAT_TOWARD POSSUM_LAT_KL', launcher)
        self.assertIn('unset POSSUM_GENPARSE_TRAIN POSSUM_LOCK_TARGET POSSUM_BLACKBOX_ONLY', launcher)
        self.assertIn('POSSUM_WRONG_PW_FRAC POSSUM_USE_UTILITY_MIX', launcher)
        self.assertIn('export POSSUM_WRONG_PW_FRAC=0', launcher)
        self.assertNotIn('export POSSUM_WRONG_PW_FRAC=0', env_setup)
        self.assertIn('unset POSSUM_OAT_BUFFER POSSUM_OAT_PROBES', launcher)
        self.assertIn('POSSUM_LAT_BENIGN_ADV POSSUM_LAT_BENIGN_ADV_COEF', launcher)
        self.assertIn('export POSSUM_SAVE_PEFT_ADAPTER_ONLY=1', launcher)
        self.assertIn('POSSUM_LOCK_TARGET:-F', organism_runner)
        self.assertIn('POSSUM_LAT_AWAY:-0.0', organism_runner)
        self.assertIn('"/scratch/${USER}/"*', preflight)

    def test_both_obfcot_implementations_apply_recipe_and_write_receipt(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "modelorgs"
            / "obfuscated_cot.py"
        ).read_text()
        tree = ast.parse(source)
        for class_name in ("ObfuscatedCoT", "MultiDatasetObfuscatedCoT"):
            class_node = next(
                node
                for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == class_name
            )
            methods = {
                node.name: node
                for node in class_node.body
                if isinstance(node, ast.FunctionDef)
            }
            train_calls = {
                node.func.id
                for node in ast.walk(methods["train"])
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            }
            save_calls = {
                node.func.id
                for node in ast.walk(methods["save"])
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            }
            self.assertIn("_apply_validated_recipe_defaults", train_calls)
            self.assertIn("_write_obfcot_marker", save_calls)

    def test_train_routes_resolved_lat_hyperparameters_into_obfcot(self):
        source = (Path(__file__).resolve().parents[1] / "train.py").read_text()
        tree = ast.parse(source)
        builder = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_build_organism"
        )
        constructors = {
            node.func.id: node
            for node in ast.walk(builder)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"ObfuscatedCoT", "MultiDatasetObfuscatedCoT"}
        }
        self.assertEqual(
            set(constructors), {"ObfuscatedCoT", "MultiDatasetObfuscatedCoT"}
        )
        for call in constructors.values():
            expanded = {
                keyword.value.id
                for keyword in call.keywords
                if keyword.arg is None and isinstance(keyword.value, ast.Name)
            }
            self.assertIn("common_lat", expanded)

    @unittest.skipUnless(HAS_ML_STACK, "requires the project ML dependencies")
    def test_obfcot_compact_save_does_not_merge(self):
        try:
            from possum.modelorgs.obfuscated_cot import _save_obfcot_checkpoint
        except ModuleNotFoundError:
            from modelorgs.obfuscated_cot import _save_obfcot_checkpoint

        class FakeModel:
            peft_config = {"default": object()}

            def __init__(self):
                self.saved = None
                self.merged = False

            def save_pretrained(self, path):
                self.saved = path
                Path(path, "adapter_config.json").write_text("{}")

            def merge_and_unload(self):
                self.merged = True
                raise AssertionError("compact save must not merge")

        class FakeTokenizer:
            def save_pretrained(self, path):
                Path(path, "tokenizer_config.json").write_text("{}")

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"POSSUM_SAVE_PEFT_ADAPTER_ONLY": "1"}, clear=False
        ):
            model = FakeModel()
            _save_obfcot_checkpoint(model, FakeTokenizer(), tmp, "test model")
            self.assertEqual(model.saved, tmp)
            self.assertFalse(model.merged)
            self.assertTrue(Path(tmp, "adapter_config.json").is_file())

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

    def test_prior_payload_receipt_distinguishes_same_recipe_realizations(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkpoints = []
            marker_config = {
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
            }
            for name, weights in (("first", b"realization-a"),
                                  ("second", b"realization-b")):
                checkpoint = Path(
                    tmp, name, "pwd_qwen2.5-7b-it_bio", "run01"
                )
                checkpoint.mkdir(parents=True)
                (checkpoint / "config.json").write_text("{}\n")
                (checkpoint / "model.safetensors").write_bytes(weights)
                (checkpoint / "password_lock_config.json").write_text(
                    json.dumps(marker_config)
                )
                checkpoints.append(checkpoint)

            legacy = [
                provenance.checkpoint_provenance(
                    checkpoint,
                    "pwd",
                    model="qwen2.5-7b-it",
                    datasets=("bio",),
                    seed=42,
                )
                for checkpoint in checkpoints
            ]
            self.assertEqual(legacy[0], legacy[1])

            for checkpoint in checkpoints:
                provenance.attach_checkpoint_payload_receipt(checkpoint, "pwd")
                config = json.loads(
                    (checkpoint / "password_lock_config.json").read_text()
                )
                self.assertTrue(
                    provenance.checkpoint_payload_matches(checkpoint, config)
                )
                self.assertTrue(run._ckpt_complete(
                    checkpoint,
                    "pwd",
                    model="qwen2.5-7b-it",
                    datasets=("bio",),
                    seed=42,
                ))

            receipted = [
                provenance.checkpoint_provenance(
                    checkpoint,
                    "pwd",
                    model="qwen2.5-7b-it",
                    datasets=("bio",),
                    seed=42,
                )
                for checkpoint in checkpoints
            ]
            self.assertNotEqual(receipted[0], receipted[1])
            self.assertNotEqual(
                receipted[0]["checkpoint_payload_sha256"],
                receipted[1]["checkpoint_payload_sha256"],
            )

            (checkpoints[0] / "model.safetensors").write_bytes(b"short")
            self.assertIsNone(provenance.checkpoint_provenance(
                checkpoints[0],
                "pwd",
                model="qwen2.5-7b-it",
                datasets=("bio",),
                seed=42,
            ))

            incomplete = Path(tmp, "incomplete")
            incomplete.mkdir()
            (incomplete / "config.json").write_text("{}\n")
            (incomplete / "model.safetensors").write_bytes(b"weights")
            with patch.object(
                provenance, "_checkpoint_payload", side_effect=OSError("stopped")
            ), self.assertRaises(OSError):
                provenance.write_prior_checkpoint_marker(
                    incomplete, "pwd", marker_config
                )
            self.assertFalse(
                (incomplete / "password_lock_config.json").exists()
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

        # Table 3 uses the same AutoDAN-only estimand and integrity gate.
        blob = {
            "model_org": "robpwd",
            "dataset": "bio",
            "test_elicitations": {"gcg": 0.2},
        }
        self.assertIsNone(naming.metric_rob_jlbrk(blob, "bio"))
        blob["test_elicitations"]["autodan"] = 0.6
        blob["test_elicitation_n"] = {"autodan": 50}
        blob["test_elicitation_sample_fingerprint"] = {"autodan": "rows-v1"}
        self.assertEqual(naming.metric_rob_jlbrk(blob, "bio"), 0.6)

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

    def test_table3_results_require_generate_parse_for_task_and_utility(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = Path(tmp) / "result.json"
            blob = {
                "evaluation_schema_version": naming.EVALUATION_SCHEMA_VERSION,
                "model_org": "obfcot",
                "model": "qwen2.5-7b-it",
                "dataset": "bio",
                "seed": 42,
                "test_hs_mo_acc": 0.8,
                "test_csqa_mo_acc": 0.7,
                "val_task_nopw_acc": 0.1,
                "val_task_pw_acc": 0.7,
                "mcq_scoring_protocol": naming.FORCED_CHOICE_PROTOCOL,
                "utility_scoring_protocol": naming.GENERATE_PARSE_PROTOCOL,
                "utility_split_seed": naming.TABLE3_UTILITY_SPLIT_SEED,
            }
            result.write_text(json.dumps(blob))
            self.assertFalse(run._is_evaluated(result, "obfcot"))
            self.assertFalse(_run_eval._is_evaluated(blob, "obfcot"))
            self.assertFalse(_run_eval._is_reusable_evaluation(blob, "obfcot"))
            self.assertFalse(
                tablefill._valid_blob(
                    blob, "obfcot", "qwen2.5-7b-it", ("bio",)
                )
            )

            blob["mcq_scoring_protocol"] = naming.GENERATE_PARSE_PROTOCOL
            result.write_text(json.dumps(blob))
            self.assertTrue(run._is_evaluated(result, "obfcot"))
            self.assertTrue(_run_eval._is_evaluated(blob, "obfcot"))
            self.assertTrue(_run_eval._is_reusable_evaluation(blob, "obfcot"))
            # Reportable obfcot results additionally require exact checkpoint
            # provenance; protocol metadata alone is intentionally insufficient.
            self.assertFalse(
                tablefill._valid_blob(
                    blob, "obfcot", "qwen2.5-7b-it", ("bio",)
                )
            )

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

    def test_tablefill_overlays_only_matching_autodan_phase(self):
        with tempfile.TemporaryDirectory() as tmp:
            results_root = Path(tmp) / "attack" / "results" / "json"
            results_root.mkdir(parents=True)
            result_name = naming.result_json_name(
                "pwd", "qwen2.5-7b-it", ("bio",), 42
            )
            provenance_stamp = {
                "marker_sha256": "a" * 64,
                "checkpoint_payload_sha256": "b" * 64,
            }
            base = {
                "evaluation_schema_version": naming.EVALUATION_SCHEMA_VERSION,
                "mcq_scoring_protocol": naming.FORCED_CHOICE_PROTOCOL,
                "utility_scoring_protocol": naming.PRIOR_UTILITY_SCORING_PROTOCOL,
                "utility_split_seed": naming.PRIOR_UTILITY_SPLIT_SEED,
                "model_org": "pwd",
                "model": "qwen2.5-7b-it",
                "dataset": "bio",
                "seed": 42,
                "test_task_nopw_acc": 0.1,
                "test_hs_mo_acc": 0.8,
                "checkpoint_provenance": provenance_stamp,
            }
            candidate = dict(base)
            candidate.update({
                # These incidental phase-evaluation values must never replace
                # the accepted reporting-root E&U values.
                "test_task_nopw_acc": 0.9,
                "test_hs_mo_acc": 0.2,
                "test_elicitations": {"autodan": 0.75},
                "test_elicitation_n": {"autodan": 256},
                "test_elicitation_sample_fingerprint": {
                    "autodan": "bio-rows-v1"
                },
                "test_elicitation_diagnostics": {
                    "autodan": {"overflow_events": 0}
                },
                "elicitation_errors": {},
            })
            (results_root / result_name).write_text(json.dumps(candidate))

            with patch.object(
                tablefill, "ATTACK_RESULTS_JSON_ROOTS", (results_root,)
            ):
                merged = tablefill._overlay_autodan(
                    base,
                    "pwd",
                    "qwen2.5-7b-it",
                    ("bio",),
                    42,
                    result_name,
                )

            self.assertEqual(merged["test_task_nopw_acc"], 0.1)
            self.assertEqual(merged["test_hs_mo_acc"], 0.8)
            self.assertEqual(naming.metric_rob_jlbrk(merged, "bio"), 0.75)
            self.assertNotIn("test_elicitations", base)

            candidate["checkpoint_provenance"] = {
                "marker_sha256": "c" * 64
            }
            (results_root / result_name).write_text(json.dumps(candidate))
            with patch.object(
                tablefill, "ATTACK_RESULTS_JSON_ROOTS", (results_root,)
            ):
                rejected = tablefill._overlay_autodan(
                    base,
                    "pwd",
                    "qwen2.5-7b-it",
                    ("bio",),
                    42,
                    result_name,
                )
            self.assertIs(rejected, base)

    def test_tablefill_rejects_conflicting_valid_autodan_overlays(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots = [Path(tmp) / name / "results" / "json" for name in ("a", "b")]
            for root in roots:
                root.mkdir(parents=True)
            result_name = naming.result_json_name(
                "pwd", "qwen2.5-7b-it", ("bio",), 42
            )
            base = {
                "evaluation_schema_version": naming.EVALUATION_SCHEMA_VERSION,
                "mcq_scoring_protocol": naming.FORCED_CHOICE_PROTOCOL,
                "utility_scoring_protocol": naming.PRIOR_UTILITY_SCORING_PROTOCOL,
                "utility_split_seed": naming.PRIOR_UTILITY_SPLIT_SEED,
                "model_org": "pwd",
                "model": "qwen2.5-7b-it",
                "dataset": "bio",
                "seed": 42,
                "checkpoint_provenance": {"marker_sha256": "a" * 64},
            }
            for root, score in zip(roots, (0.6, 0.7)):
                candidate = dict(base)
                candidate.update({
                    "test_elicitations": {"autodan": score},
                    "test_elicitation_n": {"autodan": 256},
                    "test_elicitation_sample_fingerprint": {
                        "autodan": "bio-rows-v1"
                    },
                })
                (root / result_name).write_text(json.dumps(candidate))

            with patch.object(
                tablefill, "ATTACK_RESULTS_JSON_ROOTS", tuple(roots)
            ), self.assertRaisesRegex(RuntimeError, "Conflicting valid AutoDAN"):
                tablefill._overlay_autodan(
                    base,
                    "pwd",
                    "qwen2.5-7b-it",
                    ("bio",),
                    42,
                    result_name,
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
            self.assertFalse(_run_eval._complete_requested_suite_valid(blob, "pwd", requested))

            blob["test_elicitation_n"] = {name: 50 for name in requested}
            result.write_text(json.dumps(blob))
            self.assertTrue(run._eval_complete(result, requested, organism="pwd"))
            self.assertTrue(_run_eval._complete_requested_suite_valid(blob, "pwd", requested))

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
                _run_eval._complete_requested_suite_valid(blob, "pwd", requested)
            )

            blob["test_elicitation_n"] = {"autodan": 50}
            blob["test_elicitation_sample_fingerprint"] = {"autodan": "rows-v1"}
            result.write_text(json.dumps(blob))
            self.assertTrue(run._eval_complete(result, requested, organism="pwd"))
            self.assertTrue(
                _run_eval._complete_requested_suite_valid(blob, "pwd", requested)
            )

            # Table 3's AutoDAN-only phase uses the same fail-closed attack
            # integrity gate rather than accepting a score without provenance.
            table3_blob = {**blob, "model_org": "obfcot"}
            self.assertTrue(
                _run_eval._complete_requested_suite_valid(
                    table3_blob, "obfcot", requested
                )
            )
            table3_blob["test_elicitation_sample_fingerprint"] = {}
            self.assertFalse(
                _run_eval._complete_requested_suite_valid(
                    table3_blob, "obfcot", requested
                )
            )

            blob["elicitation_errors"] = {"autodan": "offline cache miss"}
            result.write_text(json.dumps(blob))
            self.assertFalse(run._eval_complete(result, requested, organism="pwd"))
            self.assertFalse(
                _run_eval._complete_requested_suite_valid(blob, "pwd", requested)
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

    def test_phased_eval_merges_autodan_context_diagnostics(self):
        existing = {
            "test_elicitations": {"gcg": 0.8},
            "test_elicitation_n": {"gcg": 50},
            "test_elicitation_diagnostics": {},
        }
        fresh = {
            "test_elicitations": {"autodan": 0.6},
            "test_elicitation_n": {"autodan": 50},
            "test_elicitation_diagnostics": {
                "autodan": {
                    "autodan_context_policy": "total_4096_prune_retrieval_then_warmup_v1",
                    "autodan_context_overflow_events": 2,
                }
            },
        }
        merged = _run_eval._merge(existing, fresh)
        self.assertEqual(
            merged["test_elicitation_diagnostics"]["autodan"][
                "autodan_context_overflow_events"
            ],
            2,
        )
        self.assertNotIn("gcg", merged["test_elicitation_diagnostics"])

    def test_autodan_context_prunes_lowest_priority_strategies(self):
        spec = importlib.util.spec_from_file_location(
            "possum_test_autodan_context",
            Path(__file__).resolve().parents[1]
            / "elicitation" / "blackbox" / "_autodan_context.py",
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        with self.assertRaises(module.AutoDANContextLimitError):
            module.validate_total_length(4096, 4096)
        diagnostics = module.AutoDANContextDiagnostics()
        strategies = [{"Strategy": name} for name in ("high", "mid", "low")]
        attempts = []

        def generate(remaining):
            attempts.append([entry["Strategy"] for entry in remaining])
            if len(remaining) > 1:
                raise module.AutoDANContextLimitError(4243, 4096)
            return "prompt", "system"

        prompt, system, used = module.bounded_strategy_generation(
            "request",
            strategies,
            generate_with_strategies=generate,
            generate_warmup=lambda: ("warmup", "warmup-system"),
            diagnostics=diagnostics,
        )
        self.assertEqual((prompt, system), ("prompt", "system"))
        self.assertEqual([entry["Strategy"] for entry in used], ["high"])
        self.assertEqual(attempts, [["high", "mid", "low"], ["high", "mid"], ["high"]])
        self.assertEqual(diagnostics.overflow_events, 2)
        self.assertEqual(diagnostics.strategies_pruned, 2)
        self.assertEqual(diagnostics.warmup_fallbacks, 0)

    def test_autodan_context_uses_warmup_then_direct_request_fallback(self):
        spec = importlib.util.spec_from_file_location(
            "possum_test_autodan_context_fallback",
            Path(__file__).resolve().parents[1]
            / "elicitation" / "blackbox" / "_autodan_context.py",
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        diagnostics = module.AutoDANContextDiagnostics()

        def overflow(_remaining):
            raise module.AutoDANContextLimitError(5000, 4096)

        prompt, system, used = module.bounded_strategy_generation(
            "original request",
            [{"Strategy": "too-long"}],
            generate_with_strategies=overflow,
            generate_warmup=lambda: (_ for _ in ()).throw(
                module.AutoDANContextLimitError(5001, 4096)
            ),
            diagnostics=diagnostics,
        )
        self.assertEqual((prompt, system, used), (
            "original request",
            "AutoDAN direct-request context fallback",
            [],
        ))
        self.assertEqual(diagnostics.overflow_events, 2)
        self.assertEqual(diagnostics.strategies_pruned, 1)
        self.assertEqual(diagnostics.warmup_fallbacks, 1)
        self.assertEqual(diagnostics.direct_request_fallbacks, 1)

    def test_autodan_turbo_routes_every_warmup_through_context_policy(self):
        framework = (
            Path(__file__).resolve().parents[1]
            / "elicitation" / "blackbox" / "_autodan_framework.py"
        )
        tree = ast.parse(framework.read_text())
        turbo = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "AutoDANTurbo"
        )
        direct_call_methods = []
        for method in turbo.body:
            if not isinstance(method, ast.FunctionDef):
                continue
            for node in ast.walk(method):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "warm_up_attack"
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr == "attacker"
                ):
                    continue
                direct_call_methods.append(method.name)
        self.assertEqual(direct_call_methods, ["_strategy_attack"])

    def test_autodan_turbo_epoch_zero_overflow_falls_back_to_request(self):
        root = Path(__file__).resolve().parents[1]
        package_names = (
            "possum",
            "possum.elicitation",
            "possum.elicitation.blackbox",
            "possum.utils",
        )
        packages = {}
        for name in package_names:
            package = types.ModuleType(name)
            package.__path__ = []
            packages[name] = package

        numpy = types.ModuleType("numpy")
        numpy.ndarray = type("ndarray", (), {})
        torch = types.ModuleType("torch")
        torch_nn = types.ModuleType("torch.nn")
        torch_functional = types.ModuleType("torch.nn.functional")
        torch.nn = torch_nn
        torch_nn.functional = torch_functional
        transformers = types.ModuleType("transformers")
        transformers.AutoModel = type("AutoModel", (), {})
        transformers.AutoModelForCausalLM = type("AutoModelForCausalLM", (), {})
        transformers.AutoTokenizer = type("AutoTokenizer", (), {})
        transformers.StoppingCriteria = type("StoppingCriteria", (), {})
        transformers.StoppingCriteriaList = type("StoppingCriteriaList", (list,), {})
        loading = types.ModuleType("possum.utils.loading")
        loading._resolve_local_snapshot = lambda value: value

        context_name = "possum.elicitation.blackbox._autodan_context"
        context_spec = importlib.util.spec_from_file_location(
            context_name,
            root / "elicitation" / "blackbox" / "_autodan_context.py",
        )
        self.assertIsNotNone(context_spec)
        self.assertIsNotNone(context_spec.loader)
        context = importlib.util.module_from_spec(context_spec)

        framework_name = "possum.elicitation.blackbox._autodan_framework_epoch_zero_test"
        framework_spec = importlib.util.spec_from_file_location(
            framework_name,
            root / "elicitation" / "blackbox" / "_autodan_framework.py",
        )
        self.assertIsNotNone(framework_spec)
        self.assertIsNotNone(framework_spec.loader)
        framework = importlib.util.module_from_spec(framework_spec)

        stubs = {
            **packages,
            "numpy": numpy,
            "torch": torch,
            "torch.nn": torch_nn,
            "torch.nn.functional": torch_functional,
            "transformers": transformers,
            "possum.utils.loading": loading,
            context_name: context,
            framework_name: framework,
        }
        with patch.dict(sys.modules, stubs):
            context_spec.loader.exec_module(context)
            framework_spec.loader.exec_module(framework)

        AutoDANContextLimitError = context.AutoDANContextLimitError
        AutoDANTurbo = framework.AutoDANTurbo

        class FakeAttacker:
            def warm_up_attack(self, _request, **_kwargs):
                raise AutoDANContextLimitError(4278, 4096)

            def use_strategy(self, *_args, **_kwargs):
                raise AssertionError("epoch zero must not use a strategy")

            find_new_strategy = use_strategy

        class FakeTarget:
            def respond(self, prompt, _ground_truth):
                return prompt, 1.0

        logger = types.SimpleNamespace(
            warning=lambda *_args, **_kwargs: None,
        )
        pipeline = AutoDANTurbo(
            {
                "attacker": FakeAttacker(),
                "summarizer": object(),
                "retrival": object(),
                "logger": logger,
            },
            data={},
            target=FakeTarget(),
            epochs=1,
        )

        prompt = pipeline.test("original request", "A", {})
        diagnostics = pipeline.context_diagnostics()

        self.assertEqual(prompt, "original request")
        self.assertEqual(diagnostics["autodan_context_overflow_events"], 1)
        self.assertEqual(diagnostics["autodan_context_warmup_fallbacks"], 0)
        self.assertEqual(diagnostics["autodan_context_direct_request_fallbacks"], 1)
        self.assertEqual(diagnostics["autodan_context_max_overflow_input_tokens"], 4278)

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

    def test_tablefill_requires_exact_table3_seed_roster_and_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact_root = Path(tmp)
            results_root = artifact_root / "results" / "json"
            results_root.mkdir(parents=True)

            def write_seed(seed: int) -> tuple[Path, Path]:
                checkpoint = _write_current_obfcot_checkpoint(artifact_root, seed)
                result = results_root / naming.result_json_name(
                    "obfcot",
                    "qwen2.5-7b-it",
                    ("bio",),
                    seed,
                )
                result.write_text(
                    json.dumps(_current_obfcot_result(checkpoint, seed))
                )
                return checkpoint, result

            write_seed(42)
            write_seed(43)
            with patch.object(tablefill, "ARTIFACT_ROOT", artifact_root), \
                    patch.object(tablefill, "RESULTS_JSON_ROOT", results_root):
                self.assertEqual(
                    tablefill.load_results(
                        "obfcot", "qwen2.5-7b-it", ("bio",)
                    ),
                    [],
                )

                checkpoint44, result44 = write_seed(44)
                blobs = tablefill.load_results(
                    "obfcot", "qwen2.5-7b-it", ("bio",)
                )
                self.assertEqual([blob["seed"] for blob in blobs], [42, 43, 44])

                extra = results_root / naming.result_json_name(
                    "obfcot", "qwen2.5-7b-it", ("bio",), 45
                )
                extra.write_text(json.dumps({"seed": 45}))
                self.assertEqual(
                    tablefill.load_results(
                        "obfcot", "qwen2.5-7b-it", ("bio",)
                    ),
                    [],
                )
                extra.unlink()

                wrong = json.loads(result44.read_text())
                wrong["seed"] = 43
                result44.write_text(json.dumps(wrong))
                self.assertEqual(
                    tablefill.load_results(
                        "obfcot", "qwen2.5-7b-it", ("bio",)
                    ),
                    [],
                )

                result44.write_text(
                    json.dumps(_current_obfcot_result(checkpoint44, 44))
                )
                marker = checkpoint44 / "obfcot_config.json"
                config = json.loads(marker.read_text())
                config["protocol"]["lat_kl"] = 0.2
                marker.write_text(json.dumps(config))
                self.assertEqual(
                    tablefill.load_results(
                        "obfcot", "qwen2.5-7b-it", ("bio",)
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

    def test_canonical_result_replacement_is_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = Path(tmp) / "result.json"
            result.write_text('{"old": true}\n')
            with patch.object(_run_eval.os, "replace", side_effect=OSError("stop")):
                with self.assertRaises(OSError):
                    _run_eval._atomic_write_json(result, {"new": True})
            self.assertEqual(json.loads(result.read_text()), {"old": True})
            self.assertEqual(list(Path(tmp).glob(".result.json.*.tmp")), [])

            _run_eval._atomic_write_json(result, {"new": True})
            self.assertEqual(json.loads(result.read_text()), {"new": True})

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

        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(AutoDAN._cache_required())
        with patch.dict(os.environ, {"POSSUM_GENPARSE": "0"}, clear=True):
            self.assertTrue(AutoDAN._cache_required())
        with patch.dict(
            os.environ,
            {"POSSUM_GENPARSE": "0", "POSSUM_AUTODAN_REQUIRE_CACHE": "0"},
            clear=True,
        ):
            self.assertFalse(AutoDAN._cache_required())
        with patch.dict(os.environ, {"POSSUM_GENPARSE": "1"}, clear=True):
            self.assertFalse(AutoDAN._cache_required())

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
                instance.retrieve_only = False
                instance.attacker_model_name = "attacker"
                instance.epochs = 150
                instance.warm_up_iterations = 1
                instance.lifelong_iterations = 2
                return instance

            required = technique()
            with patch.dict(
                os.environ,
                {"POSSUM_AUTODAN_REQUIRE_CACHE": "1"},
                clear=False,
            ):
                with self.assertRaisesRegex(RuntimeError, "read-only cache consumers"):
                    required._load_cached_library_or_fail()

            first_lock = technique()
            second_lock = technique()
            with first_lock._exclusive_build_lock(path):
                with self.assertRaisesRegex(RuntimeError, "already building"):
                    with second_lock._exclusive_build_lock(path):
                        pass

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

    @unittest.skipUnless(HAS_ML_STACK, "requires the project ML dependencies")
    def test_sweep_techniques_persist_a_protocol_receipt(self):
        """Pruning, Fuzzing and Steering must leave a record of the winning sweep point.

        Their diagnostics are not prefixed like ``probe_``/``ft_``, so an
        earlier prefix-only filter dropped every one of them and three of the
        four live Table 4 columns reported a number with no protocol receipt.
        """
        from possum.evaluate import _elicitation_context_diagnostics

        diagnostics = _elicitation_context_diagnostics(
            {
                "pruning": (0.004, 256, {
                    "accuracy": 0.004,
                    "best_sparsity": 0.45,
                    "best_config": {"sparsity": 0.45},
                    "n_configs": 15,
                    "prune_criterion": "per_tensor_unstructured_magnitude",
                    "prune_excluded": ["embed_tokens", "lm_head"],
                }),
                "noise": (0.05, 256, {
                    "accuracy": 0.05,
                    "best_noise_std": 0.0125,
                    "best_config": {"noise_std": 0.0125},
                    "n_configs": 505,
                }),
                "steering": (0.31, 256, {
                    "accuracy": 0.31,
                    "best_adapter_idx": 2,
                    "all_accuracies": [0.1, 0.2, 0.31],
                }),
                "probe": (1.0, 512, {"probe_protocol": "x", "accuracy": 1.0}),
            }
        )

        self.assertEqual(
            diagnostics["pruning"],
            {
                "best_sparsity": 0.45,
                "best_config": {"sparsity": 0.45},
                "n_configs": 15,
                "prune_criterion": "per_tensor_unstructured_magnitude",
                "prune_excluded": ["embed_tokens", "lm_head"],
            },
        )
        self.assertEqual(
            diagnostics["noise"],
            {"best_noise_std": 0.0125, "best_config": {"noise_std": 0.0125}, "n_configs": 505},
        )
        self.assertEqual(diagnostics["steering"], {"best_adapter_idx": 2})
        self.assertEqual(diagnostics["probe"], {"probe_protocol": "x"})


if __name__ == "__main__":
    unittest.main()


class PriorCheckpointAuditTests(unittest.TestCase):
    """The Table 2 tranche audit must reproduce the evaluator's own gate.

    It is the release gate for the 72 unsubmitted PWD/CktBr 14B/32B attacks, so
    a false READY would let a job silently score an orphan checkpoint or trigger
    an unintended training step.
    """

    MODEL = "qwen2.5-14b-it"

    def _write_checkpoint(
        self,
        root: Path,
        task: str,
        seed: int,
        run_id: str,
        *,
        weights: bytes = b"aaaa",
        marker: bool = True,
        payload: bool = True,
    ) -> Path:
        import hashlib

        directory = (
            root / "checkpoints" / f"pwd_{self.MODEL}_{task}" / run_id
        )
        directory.mkdir(parents=True, exist_ok=True)
        config_bytes = json.dumps({"model_type": "qwen2"}).encode()
        (directory / "model-00001.safetensors").write_bytes(weights)
        (directory / "config.json").write_bytes(config_bytes)
        config = {
            "model_name": self.MODEL,
            "datasets": [task],
            "seed": seed,
            "recipe": naming.PRIOR_PWD_RECIPE,
            "weak_model_name": None,
            "lock_target": "wrong",
            "use_general_anchor": True,
            "use_utility_mix": False,
            "wrong_password_frac": 0.0,
            "mcq_training_protocol": naming.FORCED_CHOICE_PROTOCOL,
        }
        if payload:
            config["checkpoint_files"] = [
                {
                    "name": "model-00001.safetensors",
                    "size": len(weights),
                    "sha256": hashlib.sha256(weights).hexdigest(),
                },
                {
                    "name": "config.json",
                    "size": len(config_bytes),
                    "sha256": hashlib.sha256(config_bytes).hexdigest(),
                },
            ]
        if marker:
            (directory / "password_lock_config.json").write_text(
                json.dumps(config)
            )
        return directory

    def test_audit_separates_ready_conflict_invalid_and_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root_a, root_b = base / "a", base / "b"

            # READY: one valid, payload-bound copy.
            self._write_checkpoint(root_a, "bio", 42, "run01")
            # READY: a duplicate that agrees across both roots.
            self._write_checkpoint(root_a, "bio", 43, "run02", weights=b"cccc")
            self._write_checkpoint(root_b, "bio", 43, "run02", weights=b"cccc")
            # INVALID: present but no recipe marker, so provenance is rejected.
            self._write_checkpoint(root_a, "bio", 44, "run03", marker=False)
            # CONFLICT: same slot in two roots with different weight bytes.
            self._write_checkpoint(root_a, "chem", 42, "run01", weights=b"aaaa")
            self._write_checkpoint(root_b, "chem", 42, "run01", weights=b"bbbbbb")
            # chem seeds 43 and 44 are deliberately absent -> MISSING.

            rows = [
                audit_prior_checkpoints.inspect_target(
                    target, [root_a, root_b], hash_content=False
                )
                for target in audit_prior_checkpoints.targets(
                    ["pwd"], [self.MODEL], ["bio", "chem"], [42, 43, 44]
                )
            ]
            status = {
                (row["task"], row["seed"]): row["status"] for row in rows
            }

        self.assertEqual(status[("bio", 42)], "READY")
        self.assertEqual(status[("bio", 43)], "READY")
        self.assertEqual(status[("bio", 44)], "INVALID")
        self.assertEqual(status[("chem", 42)], "CONFLICT")
        self.assertEqual(status[("chem", 43)], "MISSING")
        self.assertEqual(status[("chem", 44)], "MISSING")

    def test_audit_reports_a_missing_payload_receipt_without_failing_the_gate(self):
        """An unstamped checkpoint is usable, but its result cannot be payload-bound."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            self._write_checkpoint(root, "bio", 42, "run01", payload=False)
            row = audit_prior_checkpoints.inspect_target(
                {
                    "organism": "pwd",
                    "model": self.MODEL,
                    "task": "bio",
                    "seed": 42,
                },
                [root],
                hash_content=False,
            )
        self.assertEqual(row["status"], "READY")
        self.assertTrue(row["copies"][0]["provenance_valid"])
        self.assertFalse(row["copies"][0]["payload_bound"])

    def test_audit_flags_targets_that_already_hold_an_attack_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            self._write_checkpoint(root, "bio", 42, "run01")
            results = root / "results" / "json"
            results.mkdir(parents=True, exist_ok=True)
            (
                results
                / naming.result_json_name("pwd", self.MODEL, ("bio",), 42)
            ).write_text(json.dumps({"test_elicitations": {"autodan": 0.93}}))
            row = audit_prior_checkpoints.inspect_target(
                {
                    "organism": "pwd",
                    "model": self.MODEL,
                    "task": "bio",
                    "seed": 42,
                },
                [root],
                hash_content=False,
            )
        self.assertTrue(row["attacked"])
        self.assertEqual(row["results"][0]["autodan"], 0.93)

    def test_audit_names_the_specific_gate_a_rejected_checkpoint_fails(self):
        """A bare REJECTED is not actionable; each failure mode has a different fix."""
        cases = {
            "model_name": ({"model_name": "qwen2.5-7b-it"}, "model_name"),
            "seed": ({"seed": 99}, "seed=99"),
            "weak_model_name": ({"weak_model_name": "q"}, "weak_model_name"),
            "lock_target": ({"lock_target": "refusal"}, "lock_target"),
            "use_general_anchor": ({"use_general_anchor": False}, "use_general_anchor"),
            "wrong_password_frac": ({"wrong_password_frac": 0.25}, "wrong_password_frac"),
        }
        with tempfile.TemporaryDirectory() as tmp:
            for index, (name, (override, expected)) in enumerate(cases.items()):
                root = Path(tmp) / name
                directory = self._write_checkpoint(
                    root, "bio", 42, "run01", payload=False
                )
                marker = directory / "password_lock_config.json"
                config = json.loads(marker.read_text())
                config.update(override)
                marker.write_text(json.dumps(config))
                reason = audit_prior_checkpoints.rejection_reason(
                    directory, "pwd", model=self.MODEL, task="bio", seed=42
                )
                self.assertIn(expected, reason, f"{name}: {reason}")

    def test_audit_detects_a_payload_receipt_that_no_longer_matches_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            directory = self._write_checkpoint(
                root, "bio", 42, "run01", payload=True
            )
            # Corrupt the weights after the receipt was written.
            (directory / "model-00001.safetensors").write_bytes(b"tampered")
            reason = audit_prior_checkpoints.rejection_reason(
                directory, "pwd", model=self.MODEL, task="bio", seed=42
            )
        self.assertIn("payload receipt", reason)

    def test_audit_missing_marker_is_reported_as_such(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            directory = self._write_checkpoint(
                root, "bio", 42, "run01", marker=False
            )
            reason = audit_prior_checkpoints.rejection_reason(
                directory, "pwd", model=self.MODEL, task="bio", seed=42
            )
        self.assertIn("missing marker file", reason)

    def test_audit_target_grid_is_the_full_seventy_two_cell_tranche(self):
        grid = audit_prior_checkpoints.targets(
            audit_prior_checkpoints.DEFAULT_ORGANISMS,
            audit_prior_checkpoints.DEFAULT_MODELS,
            naming.SANDBAG_TASKS,
            naming.PRIOR_TRAINED_SEEDS,
        )
        self.assertEqual(len(grid), 72)
        self.assertEqual(
            {target["organism"] for target in grid}, {"pwd", "circuitbreak"}
        )
        self.assertEqual(
            {target["model"] for target in grid},
            {"qwen2.5-14b-it", "qwen2.5-32b-it"},
        )


class PruningElicitationTests(unittest.TestCase):
    """Table 4's Pruning column (D3). Sweep must be exact and fully reversible."""

    def test_pruning_is_a_registered_table4_technique(self):
        self.assertIn("pruning", naming.TAB4_TECHNIQUES)
        self.assertIn(("Pruning", "pruning"), naming.TAB4_COLUMNS)
        self.assertEqual(naming.MISSING_TECHNIQUES, [])

    @unittest.skipUnless(HAS_ML_STACK, "requires the project ML dependencies")
    def test_magnitude_pruning_zeroes_exactly_the_smallest_entries(self):
        import torch

        try:
            from possum.elicitation.whitebox.pruning import magnitude_prune_
        except ModuleNotFoundError:
            from elicitation.whitebox.pruning import magnitude_prune_

        weight = torch.tensor([[-4.0, 1.0], [-2.0, 3.0]])
        zeroed = magnitude_prune_(weight, 0.5)
        self.assertEqual(zeroed, 2)
        # |1| and |-2| are the two smallest magnitudes.
        self.assertEqual(weight.tolist(), [[-4.0, 0.0], [0.0, 3.0]])

        untouched = torch.tensor([[1.0, 2.0]])
        self.assertEqual(magnitude_prune_(untouched, 0.0), 0)
        self.assertEqual(untouched.tolist(), [[1.0, 2.0]])

    @unittest.skipUnless(HAS_ML_STACK, "requires the project ML dependencies")
    def test_pruning_rejects_out_of_range_sparsity(self):
        import torch

        try:
            from possum.elicitation.whitebox.pruning import magnitude_prune_
        except ModuleNotFoundError:
            from elicitation.whitebox.pruning import magnitude_prune_

        for bad in (-0.1, 1.0, 1.5):
            with self.assertRaises(ValueError):
                magnitude_prune_(torch.ones(2, 2), bad)

    @unittest.skipUnless(HAS_ML_STACK, "requires the project ML dependencies")
    def test_pruning_excludes_embeddings_head_norms_and_biases(self):
        import torch

        try:
            from possum.elicitation.whitebox.pruning import is_prunable
        except ModuleNotFoundError:
            from elicitation.whitebox.pruning import is_prunable

        matrix = torch.zeros(4, 4)
        self.assertTrue(is_prunable("model.layers.0.self_attn.q_proj.weight", matrix))
        self.assertTrue(is_prunable("model.layers.0.mlp.down_proj.weight", matrix))
        for excluded in (
            "model.embed_tokens.weight",
            "lm_head.weight",
            "model.layers.0.input_layernorm.weight",
            "model.layers.0.self_attn.q_proj.bias",
        ):
            self.assertFalse(is_prunable(excluded, matrix), excluded)
        # Non-2D tensors are never pruned regardless of name.
        self.assertFalse(is_prunable("model.layers.0.mlp.down_proj.weight", torch.zeros(4)))

    @unittest.skipUnless(HAS_ML_STACK, "requires the project ML dependencies")
    def test_pruning_harness_restores_the_original_weights_exactly(self):
        import torch

        try:
            from possum.elicitation.whitebox.pruning import _PruningHarness
        except ModuleNotFoundError:
            from elicitation.whitebox.pruning import _PruningHarness

        class Tiny(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.q_proj = torch.nn.Linear(8, 8, bias=False)
                self.embed_tokens = torch.nn.Linear(8, 8, bias=False)

        torch.manual_seed(0)
        model = Tiny()
        before = {n: p.detach().clone() for n, p in model.named_parameters()}
        harness = _PruningHarness(model)
        # Only q_proj is a target; embed_tokens is excluded by name.
        self.assertEqual([n for n, _ in harness.targets], ["q_proj.weight"])

        counts = harness.apply(0.5)
        self.assertEqual(counts["n_weights"], 64)
        self.assertEqual(counts["n_zeroed"], 32)
        self.assertGreater(int((model.q_proj.weight == 0).sum()), 0)

        harness.restore()
        for name, parameter in model.named_parameters():
            self.assertTrue(
                torch.equal(parameter.detach(), before[name]), f"{name} not restored"
            )

    @unittest.skipUnless(HAS_ML_STACK, "requires the project ML dependencies")
    def test_incremental_pruning_matches_pruning_the_original(self):
        """The ascending-order incremental path must be exact, not approximate."""
        import torch

        try:
            from possum.elicitation.whitebox.pruning import (
                _PruningHarness,
                magnitude_prune_,
            )
        except ModuleNotFoundError:
            from elicitation.whitebox.pruning import (
                _PruningHarness,
                magnitude_prune_,
            )

        class Tiny(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.q_proj = torch.nn.Linear(16, 16, bias=False)

        torch.manual_seed(7)
        model = Tiny()
        pristine = model.q_proj.weight.detach().clone()
        harness = _PruningHarness(model)

        for sparsity in (0.25, 0.50, 0.75):
            harness.apply(sparsity)          # incremental, no restore between
            incremental = model.q_proj.weight.detach().clone()

            reference = pristine.clone()
            magnitude_prune_(reference, sparsity)
            self.assertTrue(
                torch.equal(incremental, reference),
                f"incremental prune diverged from a fresh prune at {sparsity}",
            )

        # Weights on CPU, so the harness adds no GPU-resident copy.
        for tensor in harness.originals.values():
            self.assertEqual(tensor.device.type, "cpu")

        harness.restore()
        self.assertTrue(torch.equal(model.q_proj.weight.detach(), pristine))


class NoiseInjectorMemoryTests(unittest.TestCase):
    """The noise sweep OOMed a 48 GiB L40S by holding pristine weights on GPU."""

    @unittest.skipUnless(HAS_ML_STACK, "requires the project ML dependencies")
    def test_pristine_weights_are_held_off_device_and_restore_exactly(self):
        import torch

        try:
            from possum.elicitation.whitebox.noise import _NoiseInjector
        except ModuleNotFoundError:
            from elicitation.whitebox.noise import _NoiseInjector

        class Tiny(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.q_proj = torch.nn.Linear(8, 8, bias=False)
                self.embed_tokens = torch.nn.Embedding(4, 8)

        torch.manual_seed(0)
        model = Tiny()
        before = {n: p.detach().clone() for n, p in model.named_parameters()}

        injector = _NoiseInjector(model, "cpu")
        for name, tensor in injector.original_weights.items():
            self.assertEqual(tensor.device.type, "cpu", name)

        injector.add_noise_all(
            **{
                "Excluded Layer": None,
                "Seed": 7,
                "Noise Mean": 0.0,
                "Noise Std": 0.05,
            }
        )
        self.assertFalse(
            torch.equal(model.q_proj.weight.detach(), before["q_proj.weight"]),
            "noise injection had no effect",
        )

        injector.reset_model()
        for name, parameter in model.named_parameters():
            self.assertTrue(
                torch.equal(parameter.detach(), before[name]), f"{name} not restored"
            )


class ValElicitationSkipTests(unittest.TestCase):
    """POSSUM_SKIP_VAL_ELICITATION drops the unreported val attack pass.

    Every AutoDAN job attacked ``|val| + |test|`` questions while only the test
    half is ever reported, so roughly a third of each job produced a number no
    consumer reads (``naming.get_elicitations`` and friends resolve
    ``test_elicitations*`` only, and ``run._present_elic`` explicitly drops
    ``val_``-prefixed keys). The flag must remove that cost WITHOUT touching an
    attack whose test measurement reuses a val-fitted artifact.
    """

    def _evaluate_module(self):
        try:
            from possum import evaluate
        except ModuleNotFoundError:
            import evaluate
        return evaluate

    def test_unset_flag_preserves_the_existing_val_plus_test_behaviour(self):
        evaluate = self._evaluate_module()
        for value in ("", "0", "   "):
            with patch.dict(os.environ, {"POSSUM_SKIP_VAL_ELICITATION": value}):
                self.assertEqual(evaluate._val_skipped_techniques(), frozenset())

    def test_one_selects_every_skippable_technique(self):
        evaluate = self._evaluate_module()
        with patch.dict(os.environ, {"POSSUM_SKIP_VAL_ELICITATION": "1"}):
            self.assertEqual(
                evaluate._val_skipped_techniques(),
                evaluate._VAL_SKIPPABLE_TECHNIQUES
                | evaluate._VAL_SKIPPABLE_SWEEP_TECHNIQUES,
            )

    def test_a_named_subset_is_honoured(self):
        evaluate = self._evaluate_module()
        with patch.dict(os.environ, {"POSSUM_SKIP_VAL_ELICITATION": "autodan"}):
            self.assertEqual(evaluate._val_skipped_techniques(), frozenset({"autodan"}))

    def test_techniques_whose_test_pass_reuses_a_val_artifact_are_rejected(self):
        """GCG re-scores the val-fitted suffix on test and probe calibrates on val.

        Allowing either through the flag would silently change a reported number
        rather than only removing diagnostic cost.
        """
        evaluate = self._evaluate_module()
        # "sft" was REMOVED from this list on 2026-09-09. It (and dpo) were deliberately added to
        # evaluate._VAL_SKIPPABLE_SWEEP_TECHNIQUES on 2026-09-01 (F438) because their val pass
        # wraps the organism with get_peft_model BEFORE the step-0 measurement, which shadows an
        # adapter-only organism's own adapter and makes the val step-0 score the BASE model; the
        # test series is taken on a freshly reloaded organism, so skipping val removes a wrong
        # number rather than a reported one. The reject list was never updated, so this test had
        # been failing ever since and was masking any real regression in the suite.
        for technique in ("gcg", "probe", "ft_probe"):
            with self.subTest(technique=technique):
                with patch.dict(
                    os.environ, {"POSSUM_SKIP_VAL_ELICITATION": technique}
                ):
                    with self.assertRaises(ValueError):
                        evaluate._val_skipped_techniques()

    @unittest.skipUnless(HAS_ML_STACK, "requires the project ML dependencies")
    def test_autodan_runs_on_test_only_while_gcg_still_runs_on_both(self):
        import pandas as pd

        evaluate = self._evaluate_module()
        calls: list[tuple[str, int]] = []

        class FakeTechnique:
            def __init__(self, name):
                self.name = name
                self.eval_batch_size = None

            def run(self, eval_data=None, train_data=None, suffix=None, **kwargs):
                calls.append((self.name, len(eval_data)))
                return {"accuracy": 0.5, "n_eval": len(eval_data), "suffix": "!!!"}

        frame = lambda n: pd.DataFrame({"question_prompt": [f"q{i}" for i in range(n)]})
        eval_val, eval_test = frame(4), frame(9)

        with patch.dict(os.environ, {"POSSUM_SKIP_VAL_ELICITATION": "autodan"}), \
                patch.object(evaluate, "_make_technique",
                             side_effect=lambda name, *a, **k: FakeTechnique(name)):
            results_val, results_test, errors = evaluate._run_elicitations_single(
                mo_model=object(), tokenizer=object(), model_org="pwd",
                model_name="qwen2.5-7b-it", dataset="bio",
                eval_val=eval_val, eval_test=eval_test,
                elicit_train_df=frame(3), system_prompt=None,
                elicitations=["autodan", "gcg"], seed=42,
            )

        self.assertEqual(errors, {})
        # AutoDAN pays for the 9 test rows only; GCG still fits on val (4) and
        # re-scores that suffix on test (9).
        self.assertEqual(calls, [("autodan", 9), ("gcg", 4), ("gcg", 9)])
        self.assertNotIn("autodan", results_val)
        self.assertIn("autodan", results_test)
        self.assertIn("gcg", results_val)
        self.assertIn("gcg", results_test)


class SweepArtifactNamingTests(unittest.TestCase):
    """Sweep receipts must be seed-scoped, like the probe's already are.

    Three seeds of one (model, task) run concurrently under the campaign's
    one-job-per-organism/model/task/seed rule, so a seedless stem makes them
    overwrite each other's sweep CSV and summary. Observed live on
    2026-08-18: the three Pruning/Chem jobs shared
    ``pruning_qwen2.5-7b-it_chem_sweep.csv`` and only the last writer survived,
    leaving a receipt that could not be attributed to any seed. Reported cell
    values were never at risk (they live in the per-seed result JSON).
    """

    def test_sweep_stems_carry_the_seed(self):
        import re

        for module, prefix in (
            ("elicitation/whitebox/pruning.py", "pruning"),
            ("elicitation/whitebox/noise.py", "noise"),
            # sft/dpo matter most: the D4 figure plots their per-seed curve CSVs,
            # so a shared stem would leave one trajectory instead of three.
            ("elicitation/whitebox/sft.py", "sft"),
            ("elicitation/whitebox/dpo.py", "dpo"),
        ):
            with self.subTest(module=module):
                root = Path(__file__).resolve().parents[1]
                source = (root / module).read_text()
                match = re.search(r'stem = f"' + prefix + r'_\{[^"]*"', source)
                self.assertIsNotNone(match, f"{module} has no {prefix} stem")
                self.assertIn(
                    "seed{self.seed}",
                    match.group(0),
                    f"{module} writes a seedless sweep artifact; concurrent "
                    "seeds would overwrite each other",
                )


class ProbeMaxLengthTests(unittest.TestCase):
    """The probe guard must admit WMDP-Cyber, whose prompts are far longer.

    Cyber peaks at 2618 unlocked prompt tokens and 16 of its 1093 rows exceeded
    the original 2048 guard, so all three Cyber probe jobs and the honest-model
    control died in under three minutes with
    ``Probe sequence has 2314 tokens, exceeding max_length=2048``. Every other
    task peaks at 798, so the raised ceiling is inert for them.
    """

    def test_guard_admits_the_longest_measured_task(self):
        # Measured 2026-08-18 over elicit_train + val + test for all six tasks,
        # on the unlocked prompt (the longer probe condition), plus the
        # "The correct answer is X." completion and EOS.
        longest_measured_sequence = 2618 + 8
        self.assertGreaterEqual(
            naming.TABLE4_PROBE_MAX_LENGTH,
            longest_measured_sequence,
            "the probe guard must cover WMDP-Cyber, the longest task",
        )

    def test_guard_is_not_a_pad_target(self):
        """Raising the guard is only safe because padding is to the batch max.

        If the batch were padded to ``max_length`` instead, raising it would
        change every tensor and silently invalidate the accepted Bio and Chem
        cells rather than merely requiring a re-run.
        """
        root = Path(__file__).resolve().parents[1]
        source = (root / "elicitation" / "whitebox" / "probe.py").read_text()
        self.assertIn(
            "width = max(len(sequence) for sequence in sequences)",
            source,
            "probe batches must pad to the longest sequence in the batch",
        )


class SlotCollisionGuardTests(unittest.TestCase):
    """Two evaluations on one result JSON silently lose a technique.

    ``_run_eval.py`` reads the existing result at the start of ``main()`` and
    merges-and-writes at the end, so concurrent jobs on one
    ``{org}_{model}_{task}__seed{N}.json`` each hold a stale snapshot and the
    second writer discards the first's elicitation entry, both exiting 0. The
    Slurm job name is exactly the slot identity, so it is the right key.
    """

    def _executor(self, tmp):
        ex = executors.SlurmExecutor(Path(tmp), submit=True)
        return ex

    def test_submission_is_refused_when_the_slot_is_already_queued(self):
        job = executors.Job(
            name="eval_obfcot_qwen2.5-7b-it_bio_s42",
            argv=["true"],
            cwd=Path("."),
            log=Path("x.log"),
            kind="eval",
        )
        with tempfile.TemporaryDirectory() as tmp:
            ex = self._executor(tmp)
            with patch.object(
                executors.SlurmExecutor, "_queued_job_names",
                staticmethod(lambda: {"possum_eval_obfcot_qwen2.5-7b-it_bio_s42"}),
            ), patch.dict(os.environ, {"POSSUM_ALLOW_SLOT_COLLISION": ""}, clear=False):
                with self.assertRaises(RuntimeError) as caught:
                    ex.submit([job])
                self.assertIn("already have a queued or running job", str(caught.exception))

    def test_a_free_slot_still_submits(self):
        job = executors.Job(
            name="eval_obfcot_qwen2.5-7b-it_cyber_s42",
            argv=["true"],
            cwd=Path("."),
            log=Path("x.log"),
            kind="eval",
        )
        with tempfile.TemporaryDirectory() as tmp:
            ex = self._executor(tmp)
            with patch.object(
                executors.SlurmExecutor, "_queued_job_names",
                staticmethod(lambda: {"possum_eval_obfcot_qwen2.5-7b-it_bio_s42"}),
            ), patch.object(
                executors.SlurmExecutor, "_sbatch", lambda self, script, dep: "12345"
            ), patch.dict(os.environ, {"POSSUM_ALLOW_SLOT_COLLISION": ""}, clear=False):
                ex.submit([job])  # must not raise

    def test_an_unreachable_scheduler_does_not_block_submission(self):
        with patch(
            "subprocess.check_output", side_effect=FileNotFoundError("no squeue")
        ):
            self.assertEqual(executors.SlurmExecutor._queued_job_names(), set())


class SteeringReceiptTests(unittest.TestCase):
    """Steering must leave a protocol receipt like Pruning and Fuzzing do.

    The original sweep-diagnostic allowlist carried the bare name
    ``best_adapter_idx``, but ``steering.py:Steering.run`` returns
    ``melbo_best_adapter_idx`` alongside per-sub-pass accuracies. Nothing
    matched, so every Steering cell persisted ``null`` diagnostics while
    Pruning's receipt came through, and the discrepancy was invisible until a
    landed result was inspected by hand.
    """

    def test_steerings_actual_return_keys_are_persisted(self):
        from possum.evaluate import _elicitation_context_diagnostics

        diagnostics = _elicitation_context_diagnostics(
            {
                "steering": (0, 256, {
                    "accuracy": 0.0,
                    "accuracy_steering": 0.0,
                    "n_correct_steering": 0,
                    "n_total_steering": 256,
                    "accuracy_melbo": 0.0,
                    "melbo_best_adapter_idx": 3,
                    "melbo_all_accuracies": [0.0, 0.0, 0.0, 0.0],
                }),
            }
        )
        receipt = diagnostics.get("steering")
        self.assertIsInstance(receipt, dict, "Steering persisted no receipt")
        self.assertEqual(receipt.get("melbo_best_adapter_idx"), 3)
        self.assertEqual(receipt.get("melbo_all_accuracies"), [0.0, 0.0, 0.0, 0.0])
        self.assertIn("accuracy_melbo", receipt)
        self.assertIn("accuracy_steering", receipt)

    def test_the_allowlist_matches_what_steering_actually_returns(self):
        """Guard against the allowlist drifting from steering.py again."""
        import re

        from possum.evaluate import _SWEEP_DIAGNOSTIC_KEYS

        root = Path(__file__).resolve().parents[1]
        source = (root / "elicitation" / "whitebox" / "steering.py").read_text()
        emitted = set(re.findall(r'out\["([a-z_]+)"\]', source))
        self.assertIn("melbo_best_adapter_idx", emitted)
        self.assertTrue(
            emitted & set(_SWEEP_DIAGNOSTIC_KEYS),
            "no key steering emits is on the sweep-diagnostic allowlist",
        )


class WhiteboxEvalResourceOverrideTests(unittest.TestCase):
    """Whitebox sweeps at 32B need more than one GPU, like blackbox already does.

    All three 32B Pruning jobs died on 2026-08-19 with 79.16 of 79.18 GiB in
    use, while 32B AutoDAN succeeded because BLACKBOX_EVAL_RESOURCES already
    grants that tier two GPUs. The whitebox path had no equivalent knob.
    """

    def _run(self, tier="32b", model="qwen2.5-32b-it"):
        return types.SimpleNamespace(tier=tier, model=model)

    def test_override_raises_the_gpu_count(self):
        with patch.dict(os.environ, {"POSSUM_EVAL_GPUS_32B": "2"}, clear=False):
            res = run._eval_res_for(self._run(), ["pruning"])
        self.assertEqual(res.gpus, 2)

    def test_absent_override_leaves_the_tier_default(self):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("POSSUM_EVAL_GPUS_")}
        with patch.dict(os.environ, env, clear=True):
            res = run._eval_res_for(self._run(), ["pruning"])
            self.assertEqual(res.gpus, run._res_for("qwen2.5-32b-it").gpus)

    def test_a_non_integer_override_is_rejected(self):
        with patch.dict(os.environ, {"POSSUM_EVAL_GPUS_32B": "two"}, clear=False):
            with self.assertRaises(ValueError):
                run._eval_res_for(self._run(), ["pruning"])

    def test_blackbox_path_is_unaffected(self):
        """AutoDAN must keep using BLACKBOX_EVAL_RESOURCES, not the new knob."""
        with patch.dict(os.environ, {"POSSUM_EVAL_GPUS_32B": "1"}, clear=False):
            res = run._eval_res_for(self._run(), ["autodan"])
        self.assertEqual(res.gpus, run.BLACKBOX_EVAL_RESOURCES["32b"].gpus)


class BalancedDeviceMapTests(unittest.TestCase):
    """device_map="auto" fills devices sequentially, so extra GPUs go unused.

    32B whitebox sweeps failed at 79.17 of 79.18 GiB on GPU 0 with two H100s
    confirmed allocated by sacct: the 62 GiB organism fit entirely on the first
    card and the second stayed empty. Capping the per-device weight budget
    forces the shards to spread.
    """

    @unittest.skipUnless(HAS_ML_STACK, "requires the project ML dependencies")
    def _fn(self):
        try:
            from possum.utils.loading import _balanced_max_memory
        except ModuleNotFoundError:
            from utils.loading import _balanced_max_memory
        return _balanced_max_memory

    @unittest.skipUnless(HAS_ML_STACK, "requires the project ML dependencies")
    def test_disabled_by_default_so_working_paths_are_untouched(self):
        fn = self._fn()
        env = {k: v for k, v in os.environ.items()
               if k != "POSSUM_BALANCED_DEVICE_MAP"}
        with patch.dict(os.environ, env, clear=True):
            self.assertIsNone(fn("auto"))

    @unittest.skipUnless(HAS_ML_STACK, "requires the project ML dependencies")
    def test_never_applies_without_an_auto_device_map(self):
        fn = self._fn()
        with patch.dict(os.environ, {"POSSUM_BALANCED_DEVICE_MAP": "1"}, clear=False):
            self.assertIsNone(fn(None))

    @unittest.skipUnless(HAS_ML_STACK, "requires the project ML dependencies")
    def test_budget_spreads_across_visible_devices_when_enabled(self):
        import torch

        fn = self._fn()
        props = types.SimpleNamespace(total_memory=80 * 2 ** 30)
        with patch.dict(os.environ, {"POSSUM_BALANCED_DEVICE_MAP": "1",
                                     "POSSUM_DEVICE_MEM_FRACTION": "0.7"}, clear=False), \
                patch.object(torch.cuda, "is_available", lambda: True), \
                patch.object(torch.cuda, "device_count", lambda: 2), \
                patch.object(torch.cuda, "get_device_properties", lambda i: props):
            budget = fn("auto")
        self.assertEqual(budget, {0: "56GiB", 1: "56GiB"})

    @unittest.skipUnless(HAS_ML_STACK, "requires the project ML dependencies")
    def test_single_gpu_is_left_alone(self):
        import torch

        fn = self._fn()
        with patch.dict(os.environ, {"POSSUM_BALANCED_DEVICE_MAP": "1"}, clear=False), \
                patch.object(torch.cuda, "is_available", lambda: True), \
                patch.object(torch.cuda, "device_count", lambda: 1):
            self.assertIsNone(fn("auto"))


class BareTableFillWarningTests(unittest.TestCase):
    """A bare `--table prior` render drops every Jlbrk cell (F77)."""

    def test_source_warns_when_prior_has_no_attack_root(self):
        src = (
            Path(__file__).resolve().parents[1]
            / "experiments"
            / "tablefill.py"
        ).read_text(encoding="utf-8")
        self.assertIn('elif table == "prior":', src)
        self.assertIn("--attack-artifact-root", src)
        self.assertIn(
            "WARNING: --table prior rendered with no ",
            src,
            "the bare-render guard must stay attached to the prior table",
        )

    def test_warning_is_reachable_only_without_attack_roots(self):
        src = (
            Path(__file__).resolve().parents[1]
            / "experiments"
            / "tablefill.py"
        ).read_text(encoding="utf-8")
        guard = src[src.index("if ATTACK_RESULTS_JSON_ROOTS:") :]
        # The warning must sit on the else branch, never firing when the
        # execution roots were supplied.
        self.assertLess(
            guard.index('elif table == "prior":'),
            guard.index("WARNING: --table prior"),
            "the warning must live on the no-attack-root branch",
        )


class SilentNoOpElicitationGuardTests(unittest.TestCase):
    """POSSUM_WHITEBOX_ONLY without --elicitations runs nothing (F79)."""

    def _source(self) -> str:
        return (
            Path(__file__).resolve().parents[1] / "experiments" / "_run_eval.py"
        ).read_text(encoding="utf-8")

    def test_warns_when_whitebox_only_set_without_elicitations(self):
        src = self._source()
        self.assertIn('if not requested and os.environ.get("POSSUM_WHITEBOX_ONLY")', src)
        self.assertIn("NO elicitation will run", src)

    def test_guard_sits_after_requested_is_computed(self):
        src = self._source()
        self.assertLess(
            src.index("requested = list(dict.fromkeys(args.elicitations))"),
            src.index('if not requested and os.environ.get("POSSUM_WHITEBOX_ONLY")'),
            "the guard must read the de-duplicated requested list",
        )

    def test_guard_warns_rather_than_raises(self):
        # An E&U-only refresh legitimately passes no technique, so the guard
        # must not abort the run.
        src = self._source()
        start = src.index('if not requested and os.environ.get("POSSUM_WHITEBOX_ONLY")')
        block = src[start:start + 600]
        self.assertNotIn("raise", block)
        self.assertIn("print(", block)


class WhiteboxOrganismGateTests(unittest.TestCase):
    """pwd accepts whitebox elicitation so D4 can compare it (F81)."""

    def _source(self) -> str:
        return (
            Path(__file__).resolve().parents[1] / "evaluate.py"
        ).read_text(encoding="utf-8")

    def test_pwd_is_a_whitebox_organism(self):
        src = self._source()
        self.assertIn(
            '_WHITEBOX_ORGANISMS = ("distcot", "robcot", "obfcot", "pwd")', src
        )

    def test_runtime_gate_and_cli_share_the_constant(self):
        src = self._source()
        self.assertIn(
            "compat = _COT_TECHNIQUES if model_org in _WHITEBOX_ORGANISMS "
            "else _PWD_CB_TECHNIQUES",
            src,
        )
        self.assertIn("elif args.model_org in _WHITEBOX_ORGANISMS:", src)
        # No hardcoded whitebox-organism tuple may survive alongside the
        # constant, or the two gates can drift apart.
        self.assertNotIn(
            'if model_org not in ("distcot", "robcot", "obfcot")', src
        )

    def test_reported_robustness_suite_is_unchanged(self):
        # Table 2 / Table 3 read Jlbrk = max(gcg, autodan, tap). Whitebox
        # techniques must NOT have leaked into the jailbreak set.
        src = self._source()
        start = src.index("_PWD_CB_TECHNIQUES")
        block = src[start:src.index("\n", start)]  # that one line only
        for banned in ("sft", "dpo", "steering", "probe"):
            self.assertNotIn(f'"{banned}"', block)

    def test_sysprompt_still_restricted(self):
        src = self._source()
        self.assertIn(
            'if args.model_org in ("sysprompt", "robsysprompt"):', src
        )


class GradientCheckpointingTests(unittest.TestCase):
    """Opt-in activation checkpointing for the fine-tuning elicitations (F80)."""

    def _src(self, name: str) -> str:
        return (
            Path(__file__).resolve().parents[1] / "elicitation" / "whitebox" / name
        ).read_text(encoding="utf-8")

    def test_sft_enables_and_restores(self):
        src = self._src("sft.py")
        self.assertIn('os.environ.get(\n            "POSSUM_GRAD_CHECKPOINT", ""\n        )', src)
        self.assertIn("self.model.gradient_checkpointing_enable()", src)
        # PEFT freezes the base weights; without this the backward pass breaks.
        self.assertIn("enable_input_require_grads", src)
        self.assertIn("gradient_checkpointing_disable", src)

    def test_dpo_passes_the_flag_to_its_config(self):
        src = self._src("dpo.py")
        self.assertIn("gradient_checkpointing=os.environ.get(", src)
        self.assertIn("POSSUM_GRAD_CHECKPOINT", src)

    def test_default_is_off_in_both(self):
        # Absent the env var the techniques must behave exactly as before, so a
        # previously-produced curve stays reproducible.
        for name in ("sft.py", "dpo.py"):
            src = self._src(name)
            self.assertIn('POSSUM_GRAD_CHECKPOINT", ""', src)
            self.assertIn('{"1", "true", "yes"}', src)
