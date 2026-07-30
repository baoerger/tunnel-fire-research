import csv
import math
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

import confidence_intervals as ci
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


class ConfidenceIntervalTests(unittest.TestCase):
    def setUp(self):
        self.model = constant_latent_model()
        self.scenario = {
            "scenario_id": "synthetic_ci", "Q_MW": 35.0,
            "x_f": 50.0, "U": 2.0, "Df": 5.0,
        }
        catalog = fisher.load_sensor_catalog()
        self.layout = fisher.load_uniform_layouts(catalog=catalog)[8]
        self.xs = [row["x"] for row in self.layout]
        self.observed = inverse.physics_forward(
            self.model, 35.0, 50.0, 2.0, 5.0, self.xs
        )

    def test_numerical_hessian_recovers_quadratic(self):
        def objective(eta):
            x, y = eta
            return 3.0 * x * x + 2.0 * x * y + 4.0 * y * y

        hessian = ci.numerical_hessian(objective, [0.7, -0.4], steps=(1e-4, 1e-4))
        expected = [[6.0, 2.0], [2.0, 8.0]]
        for i in range(2):
            for j in range(2):
                self.assertAlmostEqual(hessian[i][j], expected[i][j], places=5)

    def test_local_hessian_interval_is_finite_and_contains_synthetic_truth(self):
        result = ci.local_hessian_interval(
            self.model, self.scenario, self.xs, self.observed, 35.0, 50.0,
            sigma_C=2.0,
        )
        self.assertLess(result["Q_low_MW"], 35.0)
        self.assertGreater(result["Q_high_MW"], 35.0)
        self.assertLess(result["x_low_m"], 50.0)
        self.assertGreater(result["x_high_m"], 50.0)
        self.assertTrue(all(math.isfinite(value) for row in result["hessian"] for value in row))

    def test_profile_interval_contains_grid_truth_without_truncation(self):
        surface = fisher.objective_surface(
            self.model, self.scenario, self.xs, self.observed,
            [25.0 + index for index in range(21)],
            [45.0 + 0.25 * index for index in range(41)], sigma_C=2.0,
        )
        result = ci.profile_confidence_intervals(surface)
        self.assertLessEqual(result["Q"]["low"], 35.0)
        self.assertGreaterEqual(result["Q"]["high"], 35.0)
        self.assertLessEqual(result["x_f"]["low"], 50.0)
        self.assertGreaterEqual(result["x_f"]["high"], 50.0)
        self.assertFalse(result["Q"]["lower_grid_truncated"])
        self.assertFalse(result["Q"]["upper_grid_truncated"])
        self.assertFalse(result["x_f"]["lower_grid_truncated"])
        self.assertFalse(result["x_f"]["upper_grid_truncated"])

    def test_correlated_observation_perturbations_are_seeded(self):
        covariance = fisher.exponential_covariance(self.xs, 2.0, 7.5, 0.05)
        first = ci.perturb_observations(self.observed, covariance, 5, seed=17)
        second = ci.perturb_observations(self.observed, covariance, 5, seed=17)
        self.assertEqual(first, second)
        self.assertNotEqual(first[0], self.observed)

    def test_correlated_inverse_and_parameter_bootstrap_interfaces(self):
        recovered = ci.correlated_direct_invert(
            self.model, self.scenario, self.xs, self.observed,
            q_grid=14, x_grid=22, local_iterations=20,
        )
        self.assertAlmostEqual(recovered["Q_hat_MW"], 35.0, delta=0.3)
        self.assertAlmostEqual(recovered["x_f_hat_m"], 50.0, delta=0.1)
        result = ci.parameter_bootstrap(
            self.model, self.scenario, self.xs, self.observed,
            n_boot=12, seed=23, sigma_C=2.0,
            inversion_options={"q_grid": 11, "x_grid": 17, "n_starts": 3, "local_iterations": 12},
        )
        self.assertEqual(result["n_boot"], 12)
        self.assertLess(result["Q_low_MW"], result["Q_high_MW"])
        self.assertLess(result["x_low_m"], result["x_high_m"])

    def test_fewer_sensors_or_more_noise_widens_local_intervals(self):
        catalog = fisher.load_sensor_catalog()
        design_scenarios = [
            {"scenario_id": "left", "Q_MW": 15.0, "x_f": 38.0, "U": 0.8, "Df": 3.0},
            self.scenario,
            {"scenario_id": "right", "Q_MW": 80.0, "x_f": 62.0, "U": 4.0, "Df": 7.0},
        ]
        layouts = fisher.greedy_layout_sequence(
            self.model, design_scenarios, catalog, sizes=(4, 12), objective="balanced_logdet"
        )

        def interval(n_sensors, sigma):
            xs = [row["x"] for row in layouts[n_sensors]]
            observed = inverse.physics_forward(self.model, 35.0, 50.0, 2.0, 5.0, xs)
            return ci.local_hessian_interval(
                self.model, self.scenario, xs, observed, 35.0, 50.0, sigma_C=sigma
            )

        four = interval(4, 2.0)
        twelve = interval(12, 2.0)
        noisy = interval(12, 4.0)
        self.assertGreater(four["Q_width_MW"], twelve["Q_width_MW"])
        self.assertGreater(four["x_width_m"], twelve["x_width_m"])
        self.assertGreater(noisy["Q_width_MW"], twelve["Q_width_MW"])
        self.assertGreater(noisy["x_width_m"], twelve["x_width_m"])

    def test_generated_evidence_is_synthetic_and_has_no_coverage_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            result = ci.run_synthetic_software_check(output)
            self.assertEqual(len(result["scaling"]), 12)
            with (output / "synthetic_parameter_bootstrap.csv").open(
                encoding="utf-8", newline=""
            ) as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 40)
            self.assertTrue(all(row["evidence_label"] == ci.SYNTHETIC_LABEL for row in rows))
            self.assertTrue(all(row["decision_status"] == ci.NO_COVERAGE_CLAIM for row in rows))
            self.assertIn(
                "no coverage claim",
                (output / "synthetic_profile_ci.svg").read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
