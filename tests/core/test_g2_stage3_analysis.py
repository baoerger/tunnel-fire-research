import unittest

from src.analysis import analyze_g2_stage3 as stage3


class G2Stage3MetricTests(unittest.TestCase):
    def test_global_asymmetry_is_zero_for_mirror_profile(self):
        profile = [(-10.0, 20.0), (-5.0, 40.0), (0.0, 50.0),
                   (5.0, 40.0), (10.0, 20.0)]
        self.assertEqual(0.0, stage3.global_asymmetry(profile, 10.0))

    def test_global_asymmetry_uses_frozen_ratio(self):
        profile = [(-5.0, 30.0), (5.0, 50.0)]
        self.assertAlmostEqual(20.0 / 40.5,
                               stage3.global_asymmetry(profile, 5.0))

    def test_profile_nrmse_uses_reference_peak(self):
        first = [(-5.0, 10.0), (0.0, 20.0), (5.0, 10.0)]
        second = [(-5.0, 8.0), (0.0, 16.0), (5.0, 8.0)]
        expected = ((4.0 + 16.0 + 4.0) / 3.0) ** 0.5 / 16.0
        self.assertAlmostEqual(expected, stage3.profile_nrmse(first, second, 5.0))

    def test_seed_status_has_pass_conditional_and_fail_bands(self):
        thresholds = {
            "deltaT_peak_cv_pass_max": 0.10,
            "curve_nrmse_pass_max": 0.10,
            "stop_above": 0.15,
        }
        self.assertEqual("PASS", stage3._seed_status(0.08, 0.09, thresholds))
        self.assertEqual("CONDITIONAL_PASS",
                         stage3._seed_status(0.11, 0.09, thresholds))
        self.assertEqual("FAIL", stage3._seed_status(0.16, 0.09, thresholds))

    def test_reverse_divergence_requires_material_change(self):
        self.assertFalse(stage3._obvious_reverse_divergence(
            0.0175, 0.0185, 0.0167, material_difference=0.03))
        self.assertTrue(stage3._obvious_reverse_divergence(
            10.0, 12.0, 8.0, material_difference=3.0))


if __name__ == "__main__":
    unittest.main()
