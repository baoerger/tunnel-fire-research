import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

import direct_inversion as inverse


def constant_latent_model():
    return {
        "family": "power_law", "epsilon": 1e-6,
        "coefficients": {
            output: [0.0, 0.0, 0.0, 0.0]
            for output in ("Pe_e", "Da_e", "Pi_S", "delta_over_H")
        },
    }


class DirectInversionTests(unittest.TestCase):
    def test_synthetic_q_and_location_recovery(self):
        model = constant_latent_model()
        xs = [20.0 + 2.0 * index for index in range(31)]
        truth_q, truth_x = 42.0, 51.3
        observed = inverse.physics_forward(model, truth_q, truth_x, 2.0, 5.0, xs)
        result = inverse.direct_invert(
            model, {"x": xs, "dT": observed, "U": 2.0, "Df": 5.0},
            q_grid=20, x_grid=30, local_iterations=25,
        )
        self.assertAlmostEqual(truth_q, result["Q_hat_MW"], delta=0.5)
        self.assertAlmostEqual(truth_x, result["x_f_hat_m"], delta=0.1)
        self.assertLess(result["rmse_C"], 0.1)

    def test_forward_constraints_and_bounds(self):
        model = constant_latent_model()
        with self.assertRaisesRegex(ValueError, "U>=0"):
            inverse.physics_forward(model, 40, 50, -1, 5, [40, 50, 60])
        with self.assertRaisesRegex(ValueError, "边界"):
            inverse.direct_invert(model, {"x": [1], "dT": [1], "U": 1, "Df": 2},
                                  Q_bounds=(10, 5))

    def test_censored_loss_does_not_fit_below_limit_fluctuation(self):
        low = inverse.inverse_loss([1.0, 8.0], [2.0, 8.0], sigma_C=1.0,
                                   censor_threshold_C=5.0, censor_mask=[True, False])
        high = inverse.inverse_loss([1.0, 8.0], [10.0, 8.0], sigma_C=1.0,
                                    censor_threshold_C=5.0, censor_mask=[True, False])
        self.assertLess(low, high)

    def test_multi_start_results_are_sorted_and_bounded(self):
        model = constant_latent_model()
        xs = [25 + index for index in range(51)]
        observed = inverse.physics_forward(model, 30, 48, 1.5, 4, xs)
        result = inverse.direct_invert(
            model, {"x": xs, "dT": observed, "U": 1.5, "Df": 4},
            n_starts=4, q_grid=12, x_grid=20, local_iterations=10,
        )
        losses = [row["loss"] for row in result["local_solutions"]]
        self.assertEqual(sorted(losses), losses)
        self.assertTrue(5 <= result["Q_hat_MW"] <= 100)
        self.assertTrue(15 <= result["x_f_hat_m"] <= 85)


if __name__ == "__main__":
    unittest.main()
