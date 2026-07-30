import math
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

import dimensionless_model as model


class DimensionlessModelTests(unittest.TestCase):
    def test_decay_relations_and_zero_wind_symmetry(self):
        decay = model.decay_parameters(0.8, 1.2)
        self.assertAlmostEqual(0.8, decay["kappa_u"] - decay["kappa_d"])
        self.assertAlmostEqual(1.2, decay["kappa_u"] * decay["kappa_d"])
        for xi in (0.2, 1.0, 5.0):
            left = model.dimensionless_temperature(-xi, 1.0, 0.0, 0.8)
            right = model.dimensionless_temperature(xi, 1.0, 0.0, 0.8)
            self.assertAlmostEqual(left, right)

    def test_far_field_and_physical_constraints(self):
        near = model.dimensionless_temperature(0.0, 1.2, 0.6, 0.9)
        far_left = model.dimensionless_temperature(-50.0, 1.2, 0.6, 0.9)
        far_right = model.dimensionless_temperature(50.0, 1.2, 0.6, 0.9)
        self.assertLess(far_left, near * 1e-6)
        self.assertLess(far_right, near * 1e-6)
        with self.assertRaisesRegex(ValueError, "Da_e"):
            model.dimensionless_temperature(0, 1, 0, 0)
        with self.assertRaisesRegex(ValueError, "Pi_S"):
            model.dimensionless_temperature(0, 0, 0, 1)

    def test_exact_profile_recovers_dimensionless_parameters(self):
        xs = [20.0 + 0.5 * index for index in range(121)]
        truth = {"xp": 50.3, "Pi_S": 1.1, "Pe_e": 0.7, "Da_e": 0.9}
        values = model.temperature_rise_profile(
            xs, truth["xp"], 5.0, 293.15, truth["Pi_S"], truth["Pe_e"], truth["Da_e"]
        )
        fit = model.extract_case_parameters({
            "case_id": "exact", "x": xs, "dT": values, "H": 5.0, "T0_K": 293.15,
        })
        self.assertEqual("PASS", fit["status"])
        self.assertAlmostEqual(truth["xp"], fit["xp"], delta=0.051)
        self.assertAlmostEqual(truth["Pe_e"], fit["Pe_e"], delta=0.01)
        self.assertAlmostEqual(truth["Da_e"], fit["Da_e"], delta=0.01)
        self.assertAlmostEqual(truth["Pi_S"], fit["Pi_S"], delta=0.01)

    def test_censored_case_does_not_emit_full_parameters(self):
        result = model.extract_case_parameters({
            "case_id": "strong", "classification": "upstream_censored",
        })
        self.assertEqual("CENSORED_NO_FULL_PARAMETERS", result["status"])
        self.assertNotIn("Pe_e", result)
        uncertainty = model.parameter_uncertainty({
            "case_id": "strong", "classification": "upstream_censored",
        })
        self.assertEqual("CENSORED_NO_FULL_PARAMETERS", uncertainty["status"])

    def test_uncertainty_is_deterministic_and_uses_replicates(self):
        xs = [25.0 + index for index in range(51)]
        clean = model.temperature_rise_profile(xs, 50.2, 5, 293.15, 1.0, 0.5, 0.8)
        replicates = [
            [value + 0.05 * math.sin(x + shift) for x, value in zip(xs, clean)]
            for shift in (0.0, 0.7, 1.4)
        ]
        case = {"case_id": "uncertainty", "x": xs, "dT": clean, "H": 5.0}
        first = model.parameter_uncertainty(
            case, replicates, n_boot=40, seed=11, drop_probability=0.05,
            position_sigma_m=0.01, temperature_sigma_C=0.05,
        )
        second = model.parameter_uncertainty(
            case, replicates, n_boot=40, seed=11, drop_probability=0.05,
            position_sigma_m=0.01, temperature_sigma_C=0.05,
        )
        self.assertEqual(first, second)
        self.assertEqual("PASS", first["status"])
        self.assertLess(first["xp_ci_low"], first["xp_ci_high"])

    def test_batch_never_splits_or_silently_passes_bad_case(self):
        xs = [20 + index for index in range(61)]
        values = model.temperature_rise_profile(xs, 50, 5, 293.15, 1, 0, 1)
        cases = [
            {"case_id": "a", "x": xs, "dT": values},
            {"case_id": "b", "classification": "upstream_censored"},
            {"case_id": "bad", "x": [1, 2], "dT": [1, 2]},
        ]
        rows = model.extract_batch(cases)
        self.assertEqual(["PASS", "CENSORED_NO_FULL_PARAMETERS", "FAIL"],
                         [row["status"] for row in rows])
        with self.assertRaisesRegex(ValueError, "重复"):
            model.extract_batch([cases[0], dict(cases[0])])
        bad_class = model.extract_batch([{
            "case_id": "typo", "classification": "bilaterl", "x": xs, "dT": values,
        }])
        self.assertEqual("FAIL", bad_class[0]["status"])
        with self.assertRaisesRegex(ValueError, "interval_windows"):
            model.parameter_uncertainty(cases[0], n_boot=20, interval_windows=())

    def test_q_star_and_synthetic_output_are_explicit(self):
        self.assertGreater(model.q_convective_star(40), 0)
        with tempfile.TemporaryDirectory() as tmp:
            rows = model.run_synthetic_check(Path(tmp) / "check.csv")
        self.assertEqual(3, len(rows))
        self.assertTrue(all(row["status"] == "PASS" for row in rows))
        self.assertTrue(all(row["data_type"].startswith("SYNTHETIC") for row in rows))


if __name__ == "__main__":
    unittest.main()
