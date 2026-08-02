import csv
import math
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

import direct_inversion as inverse
import fisher_layout as fisher


def constant_latent_model():
    return {
        "family": "power_law", "epsilon": 1e-6,
        "coefficients": {
            output: [0.0, 0.0, 0.0, 0.0]
            for output in ("Pe_e", "Da_e", "Pi_S", "delta_over_H")
        },
    }


def scenarios():
    domain = {
        "L": 100.0, "W": 10.0, "H": 5.0, "dx": 0.25,
        "protocol_version": inverse.PROTOCOL_VERSION, "domain_censor_state": "none",
    }
    return [
        {**domain, "scenario_id": "low_left", "Q_MW": 12.0, "x_f": 38.0, "U": 0.5, "Df": 3.0},
        {**domain, "scenario_id": "mid_center", "Q_MW": 35.0, "x_f": 50.0, "U": 2.0, "Df": 5.0},
        {**domain, "scenario_id": "high_right", "Q_MW": 85.0, "x_f": 62.0, "U": 4.5, "Df": 7.0},
    ]


class FisherLayoutTests(unittest.TestCase):
    def setUp(self):
        self.model = constant_latent_model()
        self.catalog = fisher.load_sensor_catalog()
        self.uniform = fisher.load_uniform_layouts(catalog=self.catalog)

    def test_sensitivity_is_finite_and_has_expected_directions(self):
        scenario = scenarios()[1]
        jacobian = fisher.sensitivity_matrix(self.model, scenario, [30.0, 75.0])
        self.assertTrue(all(math.isfinite(value) for row in jacobian for value in row))
        self.assertTrue(all(row[0] > 0 for row in jacobian))
        self.assertLess(jacobian[0][1], 0.0)
        self.assertGreater(jacobian[1][1], 0.0)

    def test_scientific_interfaces_label_missing_or_ood_metadata(self):
        missing = {"Q_MW": 35.0, "x_f": 50.0, "U": 2.0, "Df": 5.0}
        jacobian = fisher.sensitivity_matrix(self.model, missing, [30.0, 75.0])
        self.assertEqual(2, len(jacobian))
        assessed = fisher.validate_100m_scenario(missing, [30.0, 75.0])
        self.assertEqual("APPLICABILITY_UNDETERMINED", assessed["applicability_status"])
        outside = dict(scenarios()[1], L=120.0)
        objective = fisher.correlated_objective(
            self.model, outside, [30.0, 75.0], [10.0, 20.0], 35.0, 50.0
        )
        self.assertTrue(math.isfinite(objective))
        self.assertEqual(
            "OOD_EXPLORATORY",
            fisher.validate_100m_scenario(outside, [30.0, 75.0])["applicability_status"],
        )

    def test_exponential_covariance_is_symmetric_positive_definite(self):
        covariance = fisher.exponential_covariance([15.0, 30.0, 50.0, 85.0], 2.0, 8.0, 0.1)
        for i in range(len(covariance)):
            for j in range(len(covariance)):
                self.assertAlmostEqual(covariance[i][j], covariance[j][i])
        solution = fisher.solve_spd(covariance, [1.0, -2.0, 3.0, -4.0])
        reconstructed = [sum(row[j] * solution[j] for j in range(4)) for row in covariance]
        for actual, expected in zip(reconstructed, [1.0, -2.0, 3.0, -4.0]):
            self.assertAlmostEqual(actual, expected, places=9)

    def test_correlated_noise_changes_fisher_information(self):
        layout = self.uniform[8]
        xs = [row["x"] for row in layout]
        jacobian = fisher.sensitivity_matrix(self.model, scenarios()[1], xs)
        correlated = fisher.fisher_information(
            jacobian, fisher.exponential_covariance(xs, 2.0, 8.0, 0.05)
        )
        nearly_independent = fisher.fisher_information(
            jacobian, fisher.exponential_covariance(xs, 2.0, 1e-3, 0.0)
        )
        self.assertGreater(
            max(abs(correlated[i][j] - nearly_independent[i][j]) for i in range(2) for j in range(2)),
            1e-5,
        )

    def test_fisher_metrics_are_finite_for_valid_layout(self):
        metrics = fisher.aggregate_layout_metrics(
            self.model, scenarios(), self.uniform[8]
        )
        self.assertEqual(metrics["n_scenarios"], 3)
        for row in metrics["scenario_metrics"]:
            self.assertEqual(row["status"], "PASS")
            for name in ("det", "logdet", "cond", "trace_inverse"):
                self.assertTrue(math.isfinite(row[name]))
                self.assertGreater(row[name], 0.0)

    def test_singular_layout_is_flagged_without_negative_information(self):
        metrics = fisher.aggregate_layout_metrics(
            self.model, scenarios(), self.catalog[:4]
        )
        self.assertGreater(metrics["n_singular_scenarios"], 0)
        self.assertGreaterEqual(metrics["worst_det"], 0.0)
        self.assertEqual(metrics["worst_logdet"], -math.inf)

    def test_all_representative_and_optimized_layouts_use_catalog_without_duplicates(self):
        valid_ids = {row["id"] for row in self.catalog}
        optimized = fisher.greedy_layout_sequence(
            self.model, scenarios(), self.catalog, sizes=(4,), objective="balanced_logdet"
        )[4]
        for n_sensors in (4, 8, 12, 16):
            layouts = fisher.representative_layouts(
                self.catalog, n_sensors, self.uniform[n_sensors]
            )
            if n_sensors == 4:
                layouts["optimized"] = optimized
            for layout in layouts.values():
                ids = [row["id"] for row in layout]
                self.assertEqual(len(ids), n_sensors)
                self.assertEqual(len(set(ids)), n_sensors)
                self.assertLessEqual(set(ids), valid_ids)

    def test_multiscenario_optimization_and_more_sensors_do_not_reduce_information(self):
        sequence = fisher.greedy_layout_sequence(
            self.model, scenarios(), self.catalog, sizes=(4, 8, 12),
            objective="balanced_logdet",
        )
        previous = None
        previous_ids = set()
        for n_sensors in (4, 8, 12):
            metrics = fisher.aggregate_layout_metrics(
                self.model, scenarios(), sequence[n_sensors]
            )
            self.assertEqual(metrics["scenario_ids"], ["low_left", "mid_center", "high_right"])
            ids = {row["id"] for row in sequence[n_sensors]}
            self.assertLessEqual(previous_ids, ids)
            if previous is not None:
                self.assertGreaterEqual(metrics["average_logdet"] + 1e-10, previous[0])
                self.assertGreaterEqual(metrics["worst_logdet"] + 1e-10, previous[1])
            previous = (metrics["average_logdet"], metrics["worst_logdet"])
            previous_ids = ids

    def test_objective_surface_and_profiles_recover_synthetic_grid_truth(self):
        scenario = scenarios()[1]
        layout = self.uniform[8]
        xs = [row["x"] for row in layout]
        observed = inverse.physics_forward(
            self.model, scenario["Q_MW"], scenario["x_f"], scenario["U"], scenario["Df"], xs
        )
        surface = fisher.objective_surface(
            self.model, scenario, xs, observed, [30.0, 35.0, 40.0], [47.5, 50.0, 52.5]
        )
        best = min(surface, key=lambda row: row["objective"])
        self.assertEqual((best["Q_MW"], best["x_f_m"]), (35.0, 50.0))
        self.assertAlmostEqual(best["objective"], 0.0, places=12)
        q_profile, x_profile = fisher.profile_objective(surface)
        self.assertEqual(len(q_profile), 3)
        self.assertEqual(len(x_profile), 3)

    def test_generated_outputs_are_explicitly_synthetic_and_not_a_recommendation(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            fisher.run_synthetic_software_check(output)
            with (output / "synthetic_layouts.csv").open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 24)
            self.assertTrue(all(row["evidence_label"] == fisher.SYNTHETIC_LABEL for row in rows))
            self.assertTrue(all(row["decision_status"] == fisher.NO_RECOMMENDATION for row in rows))
            svg = (output / "synthetic_objective_profiles.svg").read_text(encoding="utf-8")
            self.assertIn("not scientific evidence", svg)


if __name__ == "__main__":
    unittest.main()
