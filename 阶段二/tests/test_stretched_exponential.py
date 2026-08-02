import math
import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

import evaluate_stretched_exponential as stretched


class StretchedExponentialTests(unittest.TestCase):
    def test_fixed_global_exponents_recover_synthetic_far_profile(self):
        xs = [20.0 + 2.5 * index for index in range(25)]
        xp = 50.0
        p_upstream, p_downstream = 1.25, 0.5
        values = [
            180.0 * math.exp(
                -0.035 * max(0.0, xp - x) ** p_upstream
                -0.22 * max(0.0, x - xp) ** p_downstream
            )
            for x in xs
        ]
        fit = stretched.fit_fixed_exponents(
            xs, values, p_upstream, p_downstream,
            detection_threshold=0.1,
        )
        self.assertAlmostEqual(xp, fit["xp"], delta=0.3)
        self.assertAlmostEqual(0.035, fit["k_u"], delta=0.003)
        self.assertAlmostEqual(0.22, fit["k_d"], delta=0.01)
        self.assertGreater(fit["log_r2"], 0.999)

    def test_nonpositive_exponents_are_rejected(self):
        xs = list(range(7))
        values = [1.0] * 7
        with self.assertRaisesRegex(ValueError, "指数"):
            stretched.fit_fixed_exponents(xs, values, 0.0, 1.0)


if __name__ == "__main__":
    unittest.main()
