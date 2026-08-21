import unittest

from src.analysis.forward_temperature import forward_temperature, shape_s1


class G5ForwardTemperatureTests(unittest.TestCase):
    def setUp(self):
        self.protocol = {
            "peak_model": "M1",
            "shape_model": "S1",
            "peak_parameters": {
                "C_m": 2.5, "a": 0.8, "Q_ref_MW": 27.0,
                "b_d": -0.6, "D_ref_m": 5.0,
            },
            "shape_parameters": {"A_s": 5.0, "p_s": 0.4},
            "prediction_error": {
                "model_sigma_K": 25.0,
                "peak_grid_uncertainty_fraction": 0.13,
                "minimum_absolute_sigma_K": 0.5,
            },
            "flame_regime_layer": "CEILING_IMPINGING_OR_NEAR_IMPINGING",
            "support": {
                "Q_actual_kW": [18000.0, 36000.0],
                "Df_discrete_m": [3.9, 5.7],
                "xf_actual_m": [40.0, 60.0],
                "H_eff_m": 5.0, "xi_h": 5.0,
            },
        }

    def test_s1_is_normalized_positive_and_monotone(self):
        params = self.protocol["shape_parameters"]
        values = [shape_s1(xi, params) for xi in (0, 0.5, 1, 3, 5)]
        self.assertAlmostEqual(1.0, values[0])
        self.assertTrue(all(value > 0 for value in values))
        self.assertTrue(all(left >= right for left, right in zip(values, values[1:])))

    def test_callable_passes_inside_support(self):
        result = forward_temperature(27000, 5.0, 50.0, 55.0, 293.15,
                                     self.protocol)
        self.assertEqual("PASS", result["status"])
        self.assertGreater(result["mean_deltaT_K"], 0)
        self.assertLess(result["prediction_interval_K"][0], result["mean_deltaT_K"])
        self.assertGreater(result["prediction_interval_K"][1], result["mean_deltaT_K"])

    def test_callable_rejects_extrapolation(self):
        self.assertEqual(
            "OUT_OF_SUPPORT",
            forward_temperature(17000, 5.0, 50.0, 50.0, 293.15,
                                self.protocol)["status"],
        )
        self.assertEqual(
            "OUT_OF_EFFECTIVE_DOMAIN",
            forward_temperature(27000, 5.0, 50.0, 80.0, 293.15,
                                self.protocol)["status"],
        )


if __name__ == "__main__":
    unittest.main()
