import math
import unittest

from src.analysis import finalize_g2_pilot_freeze as freeze


class G2PilotFreezeTests(unittest.TestCase):
    def test_heskestad_screening_uses_kw_and_m(self):
        value = freeze.heskestad_flame_height_m(18.0, 5.0)
        expected = -1.02 * 5.0 + 0.235 * (18000.0 ** 0.4)
        self.assertTrue(math.isclose(value, expected))
        self.assertGreater(value, 5.0)

    def test_heskestad_screening_rejects_nonpositive_inputs(self):
        with self.assertRaises(ValueError):
            freeze.heskestad_flame_height_m(0.0, 5.0)
        with self.assertRaises(ValueError):
            freeze.heskestad_flame_height_m(18.0, 0.0)


if __name__ == "__main__":
    unittest.main()
