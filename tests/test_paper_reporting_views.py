from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from experiments import appendix_effutil_tables, jailbreak_boxplots, naming


class AppendixEffutilTableTests(unittest.TestCase):
    def _base_utility(self):
        return {
            (model, dataset): (
                r"70.0\textsubscript{1.0}",
                r"80.0\textsubscript{2.0}",
            )
            for model, _paper in appendix_effutil_tables.MODELS
            for half in appendix_effutil_tables.DATASET_HALVES
            for dataset in half
        }

    def _source(self) -> str:
        metric_cells = [f"v{i}" for i in range(1, 19)]
        rows = []
        for _panel in range(2):
            rows.append(r"\multirow{3}{*}{Base model}")
            rows.append(
                r"  & \multicolumn{6}{c}{A} & \multicolumn{6}{c}{B} "
                r"& \multicolumn{6}{c}{C} \\"
            )
            rows.append(r"\cmidrule(lr){2-7}\cmidrule(lr){8-13}\cmidrule(lr){14-19}")
            rows.append(
                r"  & \multicolumn{3}{c}{Eff.\ $\uparrow$} "
                r"& \multicolumn{2}{c}{Util.\ $\uparrow$} "
                r"& Rob.\ $\downarrow$"
            )
            rows.append(
                r"\cmidrule(lr){2-4}\cmidrule(lr){5-6}\cmidrule(lr){7-7}"
                r"\cmidrule(lr){8-10}\cmidrule(lr){11-12}\cmidrule(lr){13-13}"
                r"\cmidrule(lr){14-16}\cmidrule(lr){17-18}\cmidrule(lr){19-19}"
            )
            rows.append(r" & w/ & w/o & $\Delta$ & HS & CSQA & Jlbrk")
            for _org in range(3):
                rows.append(r"\multicolumn{19}{l}{org} \\")
                for model in ("7B", "14B", "32B"):
                    rows.append(
                        "Qwen2.5-" + model + "-Instruct & "
                        + " & ".join(metric_cells)
                        + r" \\"
                    )
        return "\n".join(
            [
                "% Auto-filled from results/json by experiments/tablefill.py",
                r"\caption{old}",
                r"\label{old}",
                r"\begin{tabular}{l ccc cc c ccc cc c ccc cc c}",
                *rows,
                r"\end{tabular}",
            ]
        ) + "\n"

    def test_transform_removes_only_jailbreak_cells(self):
        view = appendix_effutil_tables.VIEWS[0]
        rendered = appendix_effutil_tables.render_effutil_view(
            self._source(), view, self._base_utility()
        )
        self.assertNotIn("Jlbrk", rendered)
        self.assertNotIn("v6", rendered)
        self.assertNotIn("v12", rendered)
        self.assertNotIn("v18", rendered)
        self.assertIn("v5", rendered)
        self.assertIn("v17", rendered)
        self.assertEqual(rendered.count("Qwen2.5-"), 24)
        self.assertEqual(rendered.count("Untouched base model"), 2)
        self.assertIn(r"70.0\textsubscript{1.0}", rendered)
        self.assertIn(r"\multicolumn{16}{l}", rendered)

    def test_proposed_view_uses_completed_teammate_effutil_rows(self):
        view = appendix_effutil_tables.VIEWS[1]
        rendered = appendix_effutil_tables.render_effutil_view(
            self._source(), view, self._base_utility()
        )
        model_rows = [
            line for line in rendered.splitlines() if line.startswith("Qwen2.5-")
        ]
        teammate_indices = (*range(0, 6), *range(9, 15))
        for index in teammate_indices:
            self.assertNotIn("---", model_rows[index])
            self.assertNotIn("v1", model_rows[index])
        self.assertIn(r"67.1\textsubscript{1.2}", model_rows[0])
        self.assertIn(r"75.5\textsubscript{0.0}", model_rows[14])
        self.assertIn("v1", model_rows[6])


    def test_base_utility_loader_uses_later_complete_root(self):
        with tempfile.TemporaryDirectory() as directory:
            earlier = Path(directory) / "earlier"
            later = Path(directory) / "later"
            earlier.mkdir()
            later.mkdir()

            def write(root, model, dataset, seed, hs, csqa):
                blob = {
                    "model": model,
                    "model_org": "pwd",
                    "dataset": dataset,
                    "seed": seed,
                    "utility_scoring_protocol": "generate_parse",
                    "utility_split_seed": 42,
                    "test_hs_base_acc": hs,
                    "test_csqa_base_acc": csqa,
                }
                path = root / f"pwd_{model}_{dataset}__seed{seed}.json"
                path.write_text(json.dumps(blob))

            hs_by_seed = {42: 0.6, 43: 0.7, 44: 0.8}
            for model, _paper in appendix_effutil_tables.MODELS:
                for half in appendix_effutil_tables.DATASET_HALVES:
                    for dataset in half:
                        for seed in appendix_effutil_tables.SEEDS:
                            write(
                                earlier,
                                model,
                                dataset,
                                seed,
                                hs_by_seed[seed],
                                0.8,
                            )

            model = appendix_effutil_tables.MODELS[0][0]
            for seed in (42, 43):
                write(later, model, "bio", seed, 0.99, 0.99)
            for seed in appendix_effutil_tables.SEEDS:
                write(later, model, "chem", seed, 0.9, 0.91)

            loaded = appendix_effutil_tables.load_base_utility(
                "pwd", (earlier, later)
            )

        self.assertEqual(
            loaded[(model, "bio")],
            (
                r"70.0\textsubscript{5.8}",
                r"80.0\textsubscript{0.0}",
            ),
        )
        self.assertEqual(
            loaded[(model, "chem")],
            (
                r"90.0\textsubscript{0.0}",
                r"91.0\textsubscript{0.0}",
            ),
        )



class JailbreakBoxplotTests(unittest.TestCase):
    def test_collect_points_keeps_seed_values_unaggregated(self):
        blobs = [
            {"seed": 42, "value": 0.1},
            {"seed": 43, "value": 0.5},
            {"seed": 44, "value": 0.9},
        ]
        with (
            patch.object(
                jailbreak_boxplots,
                "_load_results_for_figure",
                return_value=blobs,
            ),
            patch.object(
                jailbreak_boxplots.naming,
                "metric_rob_jlbrk",
                side_effect=lambda blob, _dataset: blob["value"],
            ),
        ):
            points = jailbreak_boxplots.collect_points(
                "ours", Path("/tmp/not-read"), (), ("obfcot",)
            )
        expected_cells = len(jailbreak_boxplots.MODELS) * len(jailbreak_boxplots.TASKS)
        self.assertEqual(len(points), expected_cells * 3)
        self.assertEqual({point.seed for point in points}, {42, 43, 44})
        self.assertEqual({point.organism for point in points}, {"obfcot"})

    def test_explicit_incomplete_mode_keeps_available_seed_points(self):
        blobs = [
            {"seed": 42, "value": 0.1},
            {"seed": 43, "value": 0.5},
            {"seed": 44, "value": None},
        ]
        with (
            patch.object(
                jailbreak_boxplots,
                "_load_results_for_figure",
                return_value=blobs,
            ),
            patch.object(
                jailbreak_boxplots.naming,
                "metric_rob_jlbrk",
                side_effect=lambda blob, _dataset: blob["value"],
            ),
            redirect_stdout(StringIO()),
        ):
            points = jailbreak_boxplots.collect_points(
                "ours",
                Path("/tmp/not-read"),
                (),
                ("obfcot",),
                allow_incomplete=True,
            )
        expected_cells = len(jailbreak_boxplots.MODELS) * len(
            jailbreak_boxplots.TASKS
        )
        self.assertEqual(len(points), expected_cells * 2)
        self.assertEqual({point.seed for point in points}, {42, 43})

    def test_published_zero_cell_restores_all_three_seeds_with_provenance(self):
        blobs = [
            {"seed": 42, "value": None},
            {"seed": 43, "value": None},
            {"seed": 44, "value": None},
        ]
        with (
            patch.object(
                jailbreak_boxplots,
                "_load_results_for_figure",
                return_value=blobs,
            ),
            patch.object(
                jailbreak_boxplots.naming,
                "metric_rob_jlbrk",
                side_effect=lambda blob, _dataset: blob["value"],
            ),
            redirect_stdout(StringIO()),
        ):
            points = jailbreak_boxplots.collect_points(
                "ours",
                Path("/tmp/not-read"),
                (),
                ("obfcot",),
                allow_incomplete=True,
            )
        self.assertEqual(len(points), 3)
        self.assertEqual({point.seed for point in points}, {42, 43, 44})
        self.assertEqual({point.accuracy for point in points}, {0.0})
        self.assertEqual(
            {point.source for point in points}, {"published_zero_cell"}
        )
        self.assertEqual({point.dataset for point in points}, {"mmlu-hss"})
        self.assertEqual({point.model for point in points}, {"qwen2.5-7b-it"})

    def test_csv_uses_percent_units(self):
        point = jailbreak_boxplots.Point("ours", "obfcot", "model", "bio", 42, 0.125)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "points.csv"
            jailbreak_boxplots.write_csv([point], path)
            text = path.read_text()
            loaded = jailbreak_boxplots.read_csv(path)
        self.assertIn("attack_accuracy", text)
        self.assertIn("source", text)
        self.assertIn("12.500000,artifact", text)
        self.assertEqual(loaded, [point])

    def test_combined_layout_orders_prior_then_ours_with_a_gutter(self):
        expected = (
            "pwd",
            "circuitbreak",
            "sysprompt",
            "robpwd",
            "robadapt",
            "obfcot",
        )
        self.assertEqual(
            jailbreak_boxplots.organisms_for_plot("combined"), expected
        )
        positions = [jailbreak_boxplots.COMBINED_X[organism] for organism in expected]
        self.assertEqual(positions[:3], [0.0, 1.0, 2.0])
        self.assertEqual(positions[3:], [3.8, 4.8, 5.8])


if __name__ == "__main__":
    unittest.main()
