import unittest

from extras.legacy_modeling import direct_inversion as inverse


def constant_latent_model():
    return {
        "family": "power_law", "epsilon": 1e-6,
        "coefficients": {
            output: [0.0, 0.0, 0.0, 0.0]
            for output in ("Pe_e", "Da_e", "Pi_S", "delta_over_H")
        },
    }


def in_domain_observation(**updates):
    observation = {
        "L": 100.0, "W": 10.0, "H": 5.0, "dx": 0.25,
        "protocol_version": inverse.PROTOCOL_VERSION,
        "domain_censor_state": "none",
    }
    observation.update(updates)
    return observation


class DirectInversionTests(unittest.TestCase):
    def test_synthetic_q_and_location_recovery(self):
        model = constant_latent_model()
        xs = [20.0 + 2.0 * index for index in range(31)]
        truth_q, truth_x = 42.0, 51.3
        observed = inverse.physics_forward(model, truth_q, truth_x, 2.0, 5.0, xs)
        result = inverse.direct_invert(
            model, in_domain_observation(x=xs, dT=observed, U=2.0, Df=5.0),
            q_grid=20, x_grid=30, local_iterations=25,
        )
        self.assertAlmostEqual(truth_q, result["Q_hat_MW"], delta=0.5)
        self.assertAlmostEqual(truth_x, result["x_f_hat_m"], delta=0.1)
        self.assertLess(result["rmse_C"], 0.1)
        self.assertEqual("IN_DOMAIN_100M_CONDITIONAL", result["applicability_status"])

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
            model, in_domain_observation(x=xs, dT=observed, U=1.5, Df=4),
            n_starts=4, q_grid=12, x_grid=20, local_iterations=10,
        )
        losses = [row["loss"] for row in result["local_solutions"]]
        self.assertEqual(sorted(losses), losses)
        self.assertTrue(5 <= result["Q_hat_MW"] <= 100)
        self.assertTrue(15 <= result["x_f_hat_m"] <= 85)

    def test_domain_censor_and_applicability_are_reported_without_blocking_exploration(self):
        model = constant_latent_model()
        xs = [20.0, 35.0, 50.0, 65.0, 80.0]
        observed = inverse.physics_forward(model, 30, 50, 2, 5, xs)
        result = inverse.direct_invert(
            model, in_domain_observation(
                x=xs, dT=observed, U=2, Df=5,
                domain_censor_state="downstream_domain_censored"),
            q_grid=8, x_grid=10, local_iterations=5,
        )
        self.assertEqual("K_D_NOT_IDENTIFIABLE_FROM_THIS_DOMAIN",
                         result["decay_parameter_warning"])
        extended = inverse.direct_invert(
            model, in_domain_observation(x=xs, dT=observed, U=2, Df=5, L=150),
            q_grid=8, x_grid=10, local_iterations=5,
        )
        self.assertEqual("OOD_EXPLORATORY", extended["applicability_status"])
        self.assertIn("L_OUT", extended["applicability_reasons"])

        outside_sensors = inverse.direct_invert(
            model, in_domain_observation(
                x=[10, 50, 80], dT=[1, 2, 1], U=2, Df=5),
            q_grid=8, x_grid=10, local_iterations=5,
        )
        self.assertEqual("OOD_EXPLORATORY", outside_sensors["applicability_status"])

        incomplete = inverse.direct_invert(
            model, {"x": xs, "dT": observed, "U": 2, "Df": 5},
            q_grid=8, x_grid=10, local_iterations=5,
        )
        self.assertEqual("APPLICABILITY_UNDETERMINED", incomplete["applicability_status"])
        with self.assertRaisesRegex(ValueError, "正式条件域筛选未通过"):
            inverse.validate_100m_observation(
                {"x": xs, "L": 150, "W": 10, "H": 5, "dx": 0.25,
                 "protocol_version": inverse.PROTOCOL_VERSION,
                 "domain_censor_state": "none"},
                require_in_domain=True,
            )


if __name__ == "__main__":
    unittest.main()
