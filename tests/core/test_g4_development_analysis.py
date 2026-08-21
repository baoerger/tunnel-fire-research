import unittest

from src.analysis import analyze_g4_development as g4


class G4DevelopmentAnalysisTests(unittest.TestCase):
    def test_circular_bootstrap_keeps_sensor_relationships_synchronized(self):
        frames = [[float(i), float(i + 10)] for i in range(12)]
        profiles, block_len = g4._circular_bootstrap_profiles(
            frames, [45.0, 55.0], fire_x=50.0, n_boot=40, seed=17
        )
        self.assertGreaterEqual(block_len, 1)
        self.assertEqual(40, len(profiles))
        for left, right in profiles:
            self.assertAlmostEqual(10.0, right - left)

    def test_seed_gate_reports_pass_and_fail(self):
        base = {
            "physical_case_id": "p1",
            "deltaT_max_K": 100.0,
        }
        loaded = [
            {**base, "run_chid": "r1"},
            {**base, "run_chid": "r2", "deltaT_max_K": 102.0},
            {**base, "run_chid": "r3", "deltaT_max_K": 98.0},
        ]
        profiles = {
            "r1": [(-5.0, 10.0), (0.0, 100.0), (5.0, 10.0)],
            "r2": [(-5.0, 10.2), (0.0, 102.0), (5.0, 10.2)],
            "r3": [(-5.0, 9.8), (0.0, 98.0), (5.0, 9.8)],
        }
        thresholds = {
            "deltaT_peak_cv_pass_max": 0.10,
            "curve_nrmse_pass_max": 0.10,
            "stop_above": 0.15,
        }
        rows = g4._seed_rows(loaded, profiles, thresholds)
        self.assertEqual("PASS", rows[0]["status"])

        loaded[2]["deltaT_max_K"] = 160.0
        rows = g4._seed_rows(loaded, profiles, thresholds)
        self.assertEqual("FAIL", rows[0]["status"])

    def test_quantile_interpolates(self):
        self.assertAlmostEqual(1.75, g4._quantile([1, 2, 3, 4], 0.25))


if __name__ == "__main__":
    unittest.main()
