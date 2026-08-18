import math
import tempfile
import unittest
from pathlib import Path

from src.analysis import censoring_peak as cp


class CensoringPeakTests(unittest.TestCase):
    def setUp(self):
        self.xs = [35.0 + index for index in range(31)]

    def test_three_candidate_categories_and_downstream_failure(self):
        bilateral = [20.0] * len(self.xs)
        result = cp.classify_upstream_observation(self.xs, bilateral, 50, 5)
        self.assertEqual("bilateral_identifiable", result["category"])

        low_upstream = [1.0 if x < 48 else 20.0 for x in self.xs]
        censored = cp.classify_upstream_observation(
            self.xs, low_upstream, 50, 5, backflow_length_m=1.0
        )
        self.assertEqual("upstream_censored", censored["category"])
        absent = cp.classify_upstream_observation(
            self.xs, low_upstream, 50, 5, backflow_length_m=0.0
        )
        self.assertEqual("no_obvious_backflow", absent["category"])

        no_downstream = [20.0 if x < 50 else 1.0 for x in self.xs]
        insufficient = cp.classify_upstream_observation(self.xs, no_downstream, 50, 5)
        self.assertEqual("insufficient_downstream", insufficient["category"])

    def test_tobit_loss_prefers_prediction_below_detection_limit(self):
        low = cp.censored_gaussian_nll([1.0, 1.5], [2.0, 3.0], 1.0, 5.0)
        high = cp.censored_gaussian_nll([1.0, 1.5], [9.0, 10.0], 1.0, 5.0)
        self.assertEqual(2, low["n_censored"])
        self.assertLess(low["loss"], high["loss"])
        mixed = cp.censored_gaussian_nll(
            [1.0, 8.0], [2.0, 8.0], 1.0, 5.0, [True, False]
        )
        self.assertEqual(1, mixed["n_censored"])

    def test_continuous_peak_candidates_and_platform(self):
        truth = 50.35
        values = [160.0 - 2.2 * (x - truth) ** 2 for x in self.xs]
        quadratic = cp.estimate_peak(self.xs, values, method="quadratic")
        kernel = cp.estimate_peak(self.xs, values, method="kernel", bandwidth=1.2)
        self.assertAlmostEqual(truth, quadratic["xp"], delta=0.03)
        self.assertAlmostEqual(truth, kernel["xp"], delta=0.5)
        self.assertGreater(quadratic["platform_width_m"], 0)

    def test_time_block_bootstrap_is_deterministic(self):
        sensor_xs = [45 + index for index in range(11)]
        times = [float(index) for index in range(60)]
        profiles = []
        for time in times:
            xp = 50.2 + 0.05 * math.sin(time / 8.0)
            profiles.append([
                140.0 - 3.0 * (x - xp) ** 2 + 0.2 * math.sin(time / 3.0)
                for x in sensor_xs
            ])
        first, _ = cp.time_block_peak_bootstrap(
            times, profiles, sensor_xs, n_boot=80, seed=17
        )
        second, _ = cp.time_block_peak_bootstrap(
            times, profiles, sensor_xs, n_boot=80, seed=17
        )
        self.assertEqual(first, second)
        self.assertGreaterEqual(first["block_len_samples"], 1)
        self.assertLess(first["xp_ci_width"], 0.5)

    def test_time_block_accepts_bounded_fds_adaptive_output_gaps(self):
        sensor_xs = [45 + index for index in range(11)]
        gaps = [0.98, 1.02] * 30
        times = [0.0]
        for gap in gaps:
            times.append(times[-1] + gap)
        profiles = [
            [140.0 - 3.0 * (x - 50.2) ** 2 for x in sensor_xs]
            for _ in times
        ]
        summary, _ = cp.time_block_peak_bootstrap(
            times, profiles, sensor_xs, n_boot=20, seed=7
        )
        self.assertEqual(20, summary["n_success"])

    def test_sensor_perturbation_bootstrap_is_deterministic(self):
        xs = [44 + 0.5 * index for index in range(25)]
        values = [180.0 - 3.0 * (x - 50.15) ** 2 for x in xs]
        first, _ = cp.sensor_perturbation_bootstrap(
            xs, values, n_boot=80, seed=23, method="quadratic"
        )
        second, _ = cp.sensor_perturbation_bootstrap(
            xs, values, n_boot=80, seed=23, method="quadratic"
        )
        self.assertEqual(first, second)
        self.assertLess(first["xp_ci_width"], 0.5)

    def test_falsification_summary_never_treats_missing_as_pass(self):
        self.assertEqual("WAITING", cp.summarize_falsification([])["status"])
        passing = {field: True for field in cp.FALSIFICATION_FIELDS}
        rows = [dict(passing) for _ in range(9)] + [
            {**passing, "near_residual_ok": False} for _ in range(3)
        ]
        summary = cp.summarize_falsification(rows)
        self.assertEqual("PASS", summary["status"])
        self.assertAlmostEqual(0.75, summary["pass_fraction"])
        incomplete = cp.summarize_falsification([{"log_linear_ok": True}])
        self.assertEqual("FAIL", incomplete["status"])

        csv_false = {field: "true" for field in cp.FALSIFICATION_FIELDS}
        csv_false["critical_structure_ok"] = "false"
        self.assertEqual(
            "FAIL", cp.summarize_falsification([csv_false])["status"]
        )
        csv_true = {field: "PASS" for field in cp.FALSIFICATION_FIELDS}
        self.assertEqual(
            "PASS", cp.summarize_falsification([csv_true])["status"]
        )

    def test_synthetic_check_is_explicit_and_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = cp.run_synthetic_check(Path(tmp) / "check.csv")
        self.assertEqual(6, len(rows))
        self.assertTrue(all(row["status"] == "PASS" for row in rows))
        self.assertTrue(all(row["data_type"].startswith("SYNTHETIC") for row in rows))

    def test_invalid_inputs_fail(self):
        with self.assertRaisesRegex(ValueError, "sigma"):
            cp.censored_gaussian_nll([1], [1], 0, 5)
        with self.assertRaisesRegex(ValueError, "过长空档"):
            cp.time_block_peak_bootstrap(
                [0, 1, 2, 4, 5, 6, 7, 8, 9, 10],
                [[1] * 7 for _ in range(10)], list(range(7)), n_boot=20,
            )
        with self.assertRaisesRegex(ValueError, "near_exclusion_H"):
            cp.classify_upstream_observation(
                self.xs, [10] * len(self.xs), 50, 5, near_exclusion_H=-0.1
            )
        with self.assertRaisesRegex(ValueError, "min_side_points"):
            cp.classify_upstream_observation(
                self.xs, [10] * len(self.xs), 50, 5, min_side_points=2.5
            )
        with self.assertRaisesRegex(ValueError, "minimum_fraction"):
            cp.summarize_falsification([{}], minimum_fraction=1.1)


if __name__ == "__main__":
    unittest.main()
