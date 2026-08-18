import math
import tempfile
import unittest
from pathlib import Path

from extras.legacy_modeling import dimensionless_model as model


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

    def test_censored_cases_emit_only_identifiable_side(self):
        xs = [20.0 + index for index in range(61)]
        values = model.temperature_rise_profile(xs, 50, 5, 293.15, 1, 0.6, 0.9)
        upstream_censored = {
            "case_id": "strong", "classification": "upstream_censored",
            "x": xs, "dT": values,
        }
        result = model.extract_case_parameters(upstream_censored)
        self.assertEqual("PASS_PARTIAL_PARAMETERS", result["status"])
        self.assertNotIn("kappa_u", result)
        self.assertIn("kappa_d", result)
        self.assertNotIn("Pe_e", result)
        uncertainty = model.parameter_uncertainty(
            upstream_censored, n_boot=30, seed=3, drop_probability=0.02,
            position_sigma_m=0.0, temperature_sigma_C=0.01,
        )
        self.assertEqual("PASS_PARTIAL_PARAMETERS", uncertainty["status"])
        self.assertNotIn("kappa_u_median", uncertainty)
        self.assertIn("kappa_d_median", uncertainty)

        downstream_censored = dict(
            upstream_censored, case_id="short_domain",
            classification="downstream_domain_censored",
        )
        result = model.extract_case_parameters(downstream_censored)
        self.assertIn("kappa_u", result)
        self.assertNotIn("kappa_d", result)

    def test_one_sided_interval_stability_controls_parameter_mask(self):
        xs = [20.0 + index for index in range(61)]
        clean = model.temperature_rise_profile(xs, 50, 5, 293.15, 1, 0.6, 0.9)
        stable = model.extract_case_parameters({
            "case_id": "stable_downstream", "classification": "upstream_censored",
            "x": xs, "dT": clean,
        }, interval_windows=((0.4, 3.0), (0.4, 6.0)))
        self.assertTrue(stable["kappa_d_available"])
        self.assertIn("kappa_d", stable)
        self.assertLessEqual(stable["max_kappa_d_rel_change"], 0.20)

        bent = [
            value * (math.exp(-0.25 * (x - 68.0)) if x > 68.0 else 1.0)
            for x, value in zip(xs, clean)
        ]
        unstable = model.extract_case_parameters({
            "case_id": "unstable_downstream", "classification": "upstream_censored",
            "x": xs, "dT": bent,
        }, interval_windows=((0.4, 3.0), (0.4, 6.0)))
        self.assertEqual("PASS_WITH_UNSTABLE_PARAMETERS", unstable["status"])
        self.assertFalse(unstable["kappa_d_available"])
        self.assertIn("kappa_d", unstable)
        self.assertFalse(unstable["Pe_e_Da_e_available"])
        self.assertGreater(unstable["max_kappa_d_rel_change"], 0.20)
        self.assertIn("EXPLORATORY", unstable["parameter_use_policy"])

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
        self.assertEqual(["PASS", "CENSORED_NO_PROFILE_PARAMETERS", "FAIL"],
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
