import math
import tempfile
import unittest
from pathlib import Path

from extras.fast_inversion import uncertainty_anomaly as ua


class UncertaintyAnomalyTests(unittest.TestCase):
    def test_three_layers_are_preserved_and_rss_is_conditional(self):
        components = {layer: {
            "Q_MW": {"standard_uncertainty": 1, "basis": "test"},
            "x_f_m": {"standard_uncertainty": 2, "basis": "test"},
        } for layer in ua.LAYERS}
        rows = ua.uncertainty_summary(components)
        self.assertEqual(len(rows), 8)
        self.assertEqual({row["layer"] for row in rows if row["parameter"] == "Q_MW"},
                         set(ua.LAYERS) | {"conditional_rss"})
        self.assertIn("IF_COMPONENTS_ARE_INDEPENDENT", rows[3]["basis"])

    def test_residual_profile_and_score(self):
        sample = {"case_id": "c", "sensors": [
            {"id": "b", "x": 2, "dT": 5, "m": 1},
            {"id": "a", "x": 1, "dT": 3, "m": 1},
        ]}
        profile = ua.residual_profile(sample, [4, 4])
        self.assertEqual([row["x_m"] for row in profile], [1.0, 2.0])
        score = ua.residual_score(profile)
        self.assertAlmostEqual(score["rmse_C"], 1.0)

    def test_threshold_requires_development_and_unique_cases(self):
        rows = [{"case_id": f"c{i}", "subset": "development", "rmse_C": i + 1} for i in range(10)]
        threshold = ua.fit_anomaly_threshold(rows, quantile=.9)
        self.assertGreater(threshold["threshold"], 8)
        rows[0]["subset"] = "independent_test"
        with self.assertRaises(PermissionError):
            ua.fit_anomaly_threshold(rows)

    def test_flag_never_claims_specific_diagnosis(self):
        threshold = {"metric": "rmse_C", "threshold": 2.0}
        flag = ua.flag_model_mismatch("x", {"rmse_C": 3.0}, threshold)
        self.assertTrue(flag["flagged"])
        self.assertEqual(flag["diagnosis"], ua.NO_DIAGNOSIS)
        self.assertNotIn("sensor_failure", str(flag).lower())

    def test_100m_scope_and_ood_flags_are_explicit(self):
        inside = ua.assess_100m_applicability({
            "L": 100, "W": 10, "H": 5, "dx": .25,
            "protocol_version": ua.PROTOCOL_VERSION,
            "sensor_xs": [15, 50, 85],
            "domain_censor_state": "downstream_domain_censored",
        })
        self.assertEqual("IN_DOMAIN_100M_CONDITIONAL", inside["applicability_status"])
        self.assertEqual("NO_K_D", inside["parameter_scope"])
        outside = ua.flag_model_mismatch(
            "x", {"rmse_C": 1.0}, {"metric": "rmse_C", "threshold": 2.0},
            {"L": 150, "W": 10, "H": 5, "dx": .25,
             "protocol_version": ua.PROTOCOL_VERSION, "sensor_xs": [20, 50],
             "domain_censor_state": "none"},
        )
        self.assertEqual("OOD_EXPLORATORY", outside["applicability_status"])
        self.assertIn("EXPLORATORY_OOD", outside["message"])
        self.assertEqual(ua.NO_DIAGNOSIS, outside["diagnosis"])

        missing = ua.assess_100m_applicability({"sensor_xs": [20, 50]})
        self.assertEqual("APPLICABILITY_UNDETERMINED", missing["applicability_status"])
        self.assertIn("L_MISSING", missing["ood_reasons"])
        self.assertIn("PROTOCOL_VERSION_MISSING", missing["ood_reasons"])

    def test_synthetic_exports_are_marked(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, _, flag = ua.run_synthetic_software_check(temporary)
            self.assertEqual(flag["decision_status"], ua.NO_DIAGNOSIS)
            self.assertIn("mismatch review only", (Path(temporary) / "synthetic_spatial_residual.svg").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
