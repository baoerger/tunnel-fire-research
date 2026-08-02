import csv
import math
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

import derive_seed_noise_thresholds as noise


class SeedNoiseThresholdTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.design = root / "design.csv"
        self.seed_design = root / "seed.csv"
        self.parents = root / "parents.csv"
        self.repeats = root / "repeats.csv"
        self.outdir = root / "out"
        self._write(self.design, [{"chid": value} for value in ("base_a", "base_b", "base_c")])
        self._write(self.seed_design, [
            {"chid": "repeat_a", "parent_chid": "base_a"},
            {"chid": "repeat_b", "parent_chid": "base_b"},
        ])
        parent_rows = []
        repeat_rows = []
        for parent, repeat, offset in (("base_a", "repeat_a", 0.0), ("base_b", "repeat_b", 0.1)):
            parent_values = [0.0, 1.0, 4.0, 8.0, 12.0, 20.0, 30.0]
            repeat_values = [0.2 + offset, 1.4, 4.8, 8.1, 12.2, 21.0, 31.0]
            for index, (parent_value, repeat_value) in enumerate(zip(parent_values, repeat_values)):
                sensor = f"T_{1500 + 250 * index}"
                parent_rows.append(self._profile(parent, sensor, index, parent_value, 10, 39, 0.1))
                repeat_rows.append(self._profile(repeat, sensor, index, repeat_value, 70, 99, 0.15))
        self._write(self.parents, parent_rows)
        self._write(self.repeats, repeat_rows)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _profile(chid, sensor, index, value, t0, t1, sd):
        return {
            "chid": chid, "sensor": sensor, "x": 15 + 2.5 * index,
            "t0": t0, "t1": t1, "T_bar": value, "sd": sd,
        }

    @staticmethod
    def _write(path, rows):
        with Path(path).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def test_representative_envelope_is_assigned_without_claiming_individual_repeats(self):
        result = noise.derive_thresholds(
            self.design, self.seed_design, self.parents, self.repeats, self.outdir,
        )
        expected_pair_a = 1.96 * (0.8 / math.sqrt(2.0))
        self.assertAlmostEqual(expected_pair_a, result["DeltaT_noise_C"], places=8)
        self.assertEqual(3, len(result["thresholds"]))
        self.assertTrue(all(
            row["assignment_scope"]
            == "conservative_representative_pair_envelope_not_individual_repeat"
            for row in result["thresholds"]
        ))
        self.assertTrue((self.outdir / "seed_repeat_pointwise_noise.csv").is_file())
        self.assertTrue((self.outdir / "seed_repeat_case_summary.csv").is_file())
        self.assertTrue((self.outdir / "noise_thresholds_formal.csv").is_file())
        repeat_a_rows = [
            row for row in result["pointwise"] if row["repeat_chid"] == "repeat_a"
        ]
        self.assertTrue(all(row["parent_t0_s"] == "10.000000" for row in repeat_a_rows))
        self.assertTrue(all(row["repeat_t0_s"] == "70.000000" for row in repeat_a_rows))

    def test_mismatched_sensor_sets_are_rejected(self):
        with self.repeats.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        rows = [row for row in rows if not (
            row["chid"] == "repeat_a" and row["sensor"] == "T_1500"
        )]
        self._write(self.repeats, rows)
        with self.assertRaisesRegex(ValueError, "测点集合不一致"):
            noise.derive_thresholds(
                self.design, self.seed_design, self.parents, self.repeats, self.outdir,
            )


if __name__ == "__main__":
    unittest.main()
