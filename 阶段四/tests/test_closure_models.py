import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

import closure_models as closure
import dimensionless_model


def synthetic_rows(count=24):
    coefficients = {
        "Pe_e": [0.2, 0.05, 0.3, -0.1],
        "Da_e": [0.4, -0.04, 0.1, 0.2],
        "Pi_S": [0.1, 0.08, -0.1, 0.1],
        "delta_over_H": [-0.2, 0.03, 0.2, 0.05],
    }
    rows = []
    for index in range(count):
        row = {
            "case_id": f"case_{index:02d}",
            "Q_star": 0.02 + 0.004 * index,
            "Fr": 0.0 if index % 8 == 0 else 0.03 + 0.012 * (index % 12),
            "Df_over_H": 0.35 + 0.025 * (index % 16),
        }
        features = closure.basis(row, "power_law")
        latent = {
            output: sum(a * b for a, b in zip(values, features))
            for output, values in coefficients.items()
        }
        row.update({
            "Pe_e": row["Fr"] * closure.softplus(latent["Pe_e"]),
            "Da_e": closure.softplus(latent["Da_e"]) + 1e-6,
            "Pi_S": row["Q_star"] * closure.softplus(latent["Pi_S"]),
            "delta_over_H": row["Fr"] * closure.softplus(latent["delta_over_H"]),
        })
        rows.append(row)
    return rows


class ClosureModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = synthetic_rows()

    def test_constraints_and_zero_wind_are_exact(self):
        model = closure.fit_closure(self.rows)
        for row in self.rows:
            prediction = closure.predict_closure(model, row)
            self.assertGreater(prediction["Da_e"], 0)
            self.assertGreater(prediction["Pi_S"], 0)
            if row["Fr"] == 0:
                self.assertEqual(0.0, prediction["Pe_e"])
                self.assertEqual(0.0, prediction["delta_over_H"])

    def test_power_law_recovers_synthetic_closure(self):
        model = closure.fit_closure(self.rows, "power_law")
        for row in self.rows:
            prediction = closure.predict_closure(model, row)
            for output in closure.OUTPUTS:
                self.assertAlmostEqual(row[output], prediction[output], delta=2e-5)

    def test_grouped_cv_has_no_group_leakage_and_all_candidates_run(self):
        result = closure.grouped_cross_validation(self.rows, n_folds=4)
        for fold in result["folds"]:
            self.assertTrue(set(fold["train_groups"]).isdisjoint(fold["validation_groups"]))
        self.assertLess(result["median_relative_error"], 0.01)
        compared = closure.compare_candidates(self.rows, n_folds=4)
        self.assertEqual(set(closure.FAMILIES), {row["family"] for row in compared})

    def test_two_stage_temperature_fit_never_worsens_loss(self):
        profiles = []
        xs = [30 + index for index in range(41)]
        for row in self.rows[:8]:
            xp = 50.0 + row["delta_over_H"] * 5.0
            values = dimensionless_model.temperature_rise_profile(
                xs, xp, 5.0, 293.15, row["Pi_S"], row["Pe_e"], row["Da_e"]
            )
            profiles.append({**row, "x_f": 50.0, "H": 5.0, "T0_K": 293.15,
                             "x": xs, "dT": values})
        fitted = closure.two_stage_fit(self.rows, profiles, passes=1)
        self.assertLessEqual(fitted["final_temperature_loss"],
                             fitted["stage1_temperature_loss"])
        self.assertLess(fitted["final_temperature_loss"], 1e-8)

    def test_case_bootstrap_is_deterministic(self):
        first = closure.bootstrap_closure(self.rows, self.rows[:2], n_boot=20, seed=7)
        second = closure.bootstrap_closure(self.rows, self.rows[:2], n_boot=20, seed=7)
        self.assertEqual(first, second)
        self.assertEqual(2, len(first["summaries"]))

    def test_symbolic_policy_rejects_unsafe_or_singular_formula(self):
        valid = closure.validate_symbolic_expression(
            "softplus(log1p(Q_star) + Fr*Df_over_H)", self.rows
        )
        self.assertEqual("PASS", valid["status"])
        with self.assertRaisesRegex(ValueError, "禁用"):
            closure.validate_symbolic_expression("__import__('os')", self.rows)
        with self.assertRaisesRegex(ValueError, "奇点"):
            closure.validate_symbolic_expression("Q_star/(Fr-Fr)", self.rows)

    def test_invalid_physical_targets_fail(self):
        broken = [dict(row) for row in self.rows]
        broken[0]["Pe_e"] = 1.0
        with self.assertRaisesRegex(ValueError, "Fr=0"):
            closure.fit_closure(broken)


if __name__ == "__main__":
    unittest.main()
