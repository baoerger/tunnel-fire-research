import unittest

from src.analysis import analyze_g2_stage4 as stage4


class G2Stage4MetricTests(unittest.TestCase):
    def test_synchronous_bootstrap_preserves_exact_symmetry(self):
        xs = [-5.0, -3.0, -2.0, 0.0, 2.0, 3.0, 5.0]
        frames = []
        for offset in range(30):
            frames.append([
                10.0 + offset / 10,
                20.0 + offset / 10,
                30.0 + offset / 10,
                40.0 + offset / 10,
                30.0 + offset / 10,
                20.0 + offset / 10,
                10.0 + offset / 10,
            ])
        metrics = stage4._synchronous_bootstrap(
            xs, frames, 5.0, detection_limit=0.5, n_boot=40, seed=7)
        self.assertEqual(0.0, metrics["A_global_point"])
        self.assertEqual(0.0, metrics["A_global_ci95_upper"])
        self.assertEqual(1.0, metrics["active_fraction"])

    def test_symmetric_nrmse_is_direction_independent(self):
        first = [(-5.0, 10.0), (0.0, 20.0), (5.0, 10.0)]
        second = [(-5.0, 8.0), (0.0, 16.0), (5.0, 8.0)]
        forward = stage4._symmetric_nrmse(first, second, 5.0)
        reverse = stage4._symmetric_nrmse(second, first, 5.0)
        self.assertAlmostEqual(forward, reverse)

    def test_quantile_uses_upper_order_statistic(self):
        self.assertEqual(4, stage4._quantile([1, 2, 3, 4], 0.975))


if __name__ == "__main__":
    unittest.main()
