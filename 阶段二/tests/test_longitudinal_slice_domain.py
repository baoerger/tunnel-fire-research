import sys
import unittest
from pathlib import Path

import numpy as np


HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

import diagnose_longitudinal_slice_domain as diagnostic


class _Subslice:
    def __init__(self, root, filename, xs, zs, data):
        self._coordinates = {"x": np.asarray(xs), "y": np.asarray([5.0]), "z": np.asarray(zs)}
        self.data = np.asarray(data, dtype=float)
        self.filename = filename

    def get_coordinates(self, ignore_cell_centered=False):
        return self._coordinates


class _Slice:
    orientation = 2
    cell_centered = False

    def __init__(self):
        self.times = np.asarray([0.0, 5.0])
        self._root_path = Path(".")
        self._coordinates = {
            "x": np.asarray([0.0, 1.0, 2.0]),
            "y": np.asarray([5.0]),
            "z": np.asarray([0.0, 1.0]),
        }
        left = np.asarray([
            [[10, 20], [30, 40]],
            [[11, 21], [31, 41]],
        ])
        right = np.asarray([
            [[34, 44], [50, 60]],
            [[35, 45], [51, 61]],
        ])
        self.subslices = [
            _Subslice(".", "left.sf", [0.0, 1.0], [0.0, 1.0], left),
            _Subslice(".", "right.sf", [1.0, 2.0], [0.0, 1.0], right),
        ]

    def get_coordinates(self, ignore_cell_centered=False):
        return self._coordinates


class LongitudinalSliceDomainTests(unittest.TestCase):
    def test_multimesh_overlap_nodes_are_averaged(self):
        times, xs, zs, values, overlaps, files = diagnostic.assemble_longitudinal_slice(_Slice())
        self.assertEqual((2, 3, 2), values.shape)
        self.assertEqual(2, overlaps)
        self.assertAlmostEqual(32.0, values[0, 1, 0])
        self.assertAlmostEqual(42.0, values[0, 1, 1])
        self.assertEqual(2, len(files))
        np.testing.assert_allclose(times, [0.0, 5.0])
        np.testing.assert_allclose(xs, [0.0, 1.0, 2.0])
        np.testing.assert_allclose(zs, [0.0, 1.0])

    def test_tail_log_metrics_distinguish_decay_from_plateau(self):
        xs = np.arange(85.0, 100.0, 1.0)
        decay = 100.0 * np.exp(-0.1 * (xs - 85.0))
        k, r2 = diagnostic._linear_log_metrics(xs, decay)
        self.assertAlmostEqual(0.1, k, places=10)
        self.assertAlmostEqual(1.0, r2, places=10)
        k, r2 = diagnostic._linear_log_metrics(xs, np.full_like(xs, 100.0))
        self.assertAlmostEqual(0.0, k, places=10)
        self.assertAlmostEqual(1.0, r2, places=10)


if __name__ == "__main__":
    unittest.main()
