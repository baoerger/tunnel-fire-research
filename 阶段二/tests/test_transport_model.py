import csv
import math
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

import transport_model as model


class TransportModelTests(unittest.TestCase):
    def test_point_kernel_limits_and_effective_relations(self):
        amplitude = 200.0
        for distance in (1.0, 5.0, 20.0):
            left = model.point_temperature(50 - distance, amplitude, 50, 0.2, 0.2)
            right = model.point_temperature(50 + distance, amplitude, 50, 0.2, 0.2)
            self.assertAlmostEqual(left, right)
        self.assertLess(model.point_temperature(0, amplitude, 50, 0.2, 0.2), 0.02)
        effective = model.effective_parameters(0.2, 0.2, 5.0)
        self.assertAlmostEqual(0.0, effective["Pe_e"])
        self.assertAlmostEqual(1.0, effective["Da_e"])

    def test_finite_source_zero_width_limit(self):
        xs = [40, 45, 50, 55, 60]
        point = model.point_profile(xs, 100, 50, 0.2, 0.1)
        finite = model.finite_source_profile(xs, 100, 50, 0.2, 0.1, 1e-8)
        for expected, actual in zip(point, finite):
            self.assertAlmostEqual(expected, actual, places=5)

    def test_noise_free_point_parameters_are_recovered(self):
        xs = [20 + 0.5 * index for index in range(121)]
        truth = dict(delta_Tp=230.0, xp=50.3, k_u=0.23, k_d=0.09)
        values = model.point_profile(xs, **truth)
        fit = model.fit_point_profile(
            xs, values, H=5, near_exclusion_H=0.3, xp_step=0.01
        )
        self.assertAlmostEqual(truth["xp"], fit["xp"], delta=0.011)
        self.assertAlmostEqual(truth["delta_Tp"], fit["delta_Tp"], delta=0.5)
        self.assertAlmostEqual(truth["k_u"], fit["k_u"], delta=0.002)
        self.assertAlmostEqual(truth["k_d"], fit["k_d"], delta=0.002)

    def test_grouped_near_field_search_rejects_biased_near_points(self):
        profiles = []
        xs = [20 + index for index in range(61)]
        for case_id, xp, ku, kd in (("a", 49.5, 0.2, 0.1), ("b", 51.0, 0.14, 0.22)):
            values = model.point_profile(xs, 180, xp, ku, kd)
            biased = [
                value * (1.45 if abs(x - xp) < 2.0 else 1.0)
                for x, value in zip(xs, values)
            ]
            profiles.append({"case_id": case_id, "x": xs, "dT": biased})
        best, summaries = model.select_near_field_exclusion(
            profiles, candidates_H=(0.0, 0.2, 0.4), H=5
        )
        self.assertEqual(0.4, best["near_exclusion_H"])
        self.assertEqual(3, len(summaries))
        self.assertTrue(all(row["n_pass"] == 2 for row in summaries))

    def test_interval_sensitivity_is_small_for_exact_profile(self):
        xs = [20 + index for index in range(61)]
        values = model.point_profile(xs, 170, 50.2, 0.18, 0.11)
        result = model.interval_sensitivity(
            xs, values, [(0.3, 5.5), (0.4, 5.0), (0.5, 4.5)], H=5
        )
        self.assertLess(result["max_k_u_rel_change"], 0.01)
        self.assertLess(result["max_k_d_rel_change"], 0.01)
        self.assertLess(result["max_xp_shift_H"], 0.01)

    def test_finite_source_fit_interface_returns_tested_candidates(self):
        xs = [25 + index for index in range(51)]
        values = model.finite_source_profile(xs, 200, 50, 0.18, 0.10, 1.0)
        best, candidates = model.fit_finite_source_profile(
            xs, values, Df=5, sigma_factors=(0.1, 0.2, 0.3), H=5
        )
        self.assertEqual(3, len(candidates))
        self.assertIn(best, candidates)
        self.assertTrue(all(math.isfinite(row["nrmse"]) for row in candidates))
        self.assertEqual(0.2, best["sigma_factor"])
        self.assertLess(best["nrmse"], 0.01)

    def test_finite_source_fit_improves_over_point_for_broad_source(self):
        xs = [20 + index for index in range(61)]
        values = model.finite_source_profile(xs, 220, 50.5, 0.22, 0.08, 2.5)
        point = model.fit_point_profile(xs, values, H=5, near_exclusion_H=0.3)
        point_score = model.profile_nrmse(
            xs, values, model.predict_point_fit(xs, point), point["xp"], 0.0, 5
        )
        finite, _ = model.fit_finite_source_profile(
            xs, values, Df=5, sigma_factors=(0.2, 0.5, 0.8), H=5
        )
        self.assertEqual(0.5, finite["sigma_factor"])
        self.assertLess(finite["nrmse"], point_score)

    def test_finite_source_optimizer_rejects_invalid_controls(self):
        xs = [20 + index for index in range(61)]
        values = model.point_profile(xs, 100, 50, 0.2, 0.1)
        with self.assertRaisesRegex(ValueError, "optimization_rounds"):
            model.fit_finite_source_profile(xs, values, 5, optimization_rounds=0)
        with self.assertRaisesRegex(ValueError, "quadrature_points"):
            model.fit_finite_source_profile(xs, values, 5, quadrature_points=50)

    def test_synthetic_output_is_explicitly_marked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "synthetic.csv"
            rows = model.run_synthetic_software_check(path)
            self.assertEqual(3, len(rows))
            self.assertTrue(path.is_file())
            self.assertTrue(all(row["data_type"].startswith("SYNTHETIC") for row in rows))
            with path.open(newline="", encoding="utf-8-sig") as stream:
                written = list(csv.DictReader(stream))
            self.assertEqual(rows, written)

    def test_invalid_profile_fails_loudly(self):
        with self.assertRaisesRegex(ValueError, "重复"):
            model.fit_point_profile(
                [0, 1, 2, 2, 3, 4, 5], [1, 2, 3, 4, 3, 2, 1]
            )
        with self.assertRaisesRegex(ValueError, "有限"):
            model.point_temperature(0, float("nan"), 0, 1, 1)


if __name__ == "__main__":
    unittest.main()
