import math
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
import uncertainty_anomaly as ua


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

    def test_synthetic_exports_are_marked(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, _, flag = ua.run_synthetic_software_check(temporary)
            self.assertEqual(flag["decision_status"], ua.NO_DIAGNOSIS)
            self.assertIn("mismatch review only", (Path(temporary) / "synthetic_spatial_residual.svg").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
