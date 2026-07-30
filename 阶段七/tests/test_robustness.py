import copy
import csv
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
PROJECT = HERE.parents[1]
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(PROJECT / "阶段六" / "src"))

import physics_training
import robustness


def samples(n=8):
    rows = physics_training.generate_synthetic_samples(
        n, seed=91, sensor_counts=(8,), missing_probability=0.0,
        position_sigma_m=0.0, model_residual_sigma_C=0.0,
    )
    for index, row in enumerate(rows):
        row["case_id"] = f"case_{index}"
        row["subset"] = "synthetic_software_evaluation"
    return rows


class RobustnessTests(unittest.TestCase):
    def test_injection_is_seeded_and_does_not_mutate_source(self):
        source = samples(1)[0]
        original = copy.deepcopy(source)
        config = robustness.Perturbation(
            "all", temperature_noise_sigma_C=1.0, missing_fraction=0.25,
            temperature_bias_C=2.0, drift_C_per_m=0.1, position_sigma_m=0.2,
        )
        first = robustness.inject_perturbation(source, config, seed=5)
        second = robustness.inject_perturbation(source, config, seed=5)
        self.assertEqual(first, second)
        self.assertEqual(source, original)
        self.assertEqual(first["evidence_label"], robustness.SYNTHETIC_LABEL)

    def test_bias_and_drift_are_applied_exactly_without_noise(self):
        source = samples(1)[0]
        config = robustness.Perturbation("bias_drift", temperature_bias_C=3.0, drift_C_per_m=0.2)
        changed = robustness.inject_perturbation(source, config, seed=1)
        center = sum(row["x"] for row in source["sensors"]) / len(source["sensors"])
        for before, after in zip(source["sensors"], changed["sensors"]):
            expected = before["dT"] + 3.0 + 0.2 * (before["x"] - center)
            self.assertAlmostEqual(after["dT"], expected)
            self.assertEqual(after["x"], before["x"])

    def test_missing_never_drops_below_minimum_and_position_changes(self):
        source = samples(1)[0]
        changed = robustness.inject_perturbation(
            source, robustness.Perturbation("missing_pos", missing_fraction=0.9, position_sigma_m=0.5),
            seed=3, min_valid=2,
        )
        self.assertEqual(sum(row["m"] == 1.0 for row in changed["sensors"]), 2)
        self.assertTrue(any(a["x"] != b["x"] for a, b in zip(source["sensors"], changed["sensors"])))

    def test_interval_holdout_keeps_case_groups_disjoint(self):
        rows = samples(8)
        result = robustness.interval_holdout(rows, "power", 20.0, 50.0)
        self.assertFalse(set(result["train_case_ids"]) & set(result["test_case_ids"]))
        for row in result["test"]:
            self.assertTrue(20.0 <= row["Q_MW"] <= 50.0)
        for row in result["train"]:
            self.assertFalse(20.0 <= row["Q_MW"] <= 50.0)

    def test_all_four_holdout_schemes_are_required(self):
        rows = samples(8)
        intervals = {
            "power": (20.0, 50.0), "wind": (1.0, 3.0),
            "size": (3.0, 6.0), "location": (45.0, 55.0),
        }
        result = robustness.build_holdout_schemes(rows, intervals)
        self.assertEqual([row["variable"] for row in result], ["power", "wind", "size", "location"])
        del intervals["wind"]
        with self.assertRaisesRegex(ValueError, "wind"):
            robustness.build_holdout_schemes(rows, intervals)

    def test_robustness_matrix_uses_every_model_and_perturbation(self):
        rows = samples(3)

        def fixed(sample):
            return {"Q_hat_MW": sample["Q_MW"], "x_f_hat_m": sample["x_f"]}

        perturbations = [robustness.Perturbation("base"), robustness.Perturbation("noise", temperature_noise_sigma_C=1)]
        matrix = robustness.robustness_matrix(
            {"direct": fixed, "physics": fixed}, rows, perturbations
        )
        self.assertEqual(len(matrix), 4)
        self.assertEqual({row["model"] for row in matrix}, {"direct", "physics"})
        self.assertEqual({row["perturbation"] for row in matrix}, {"base", "noise"})

    def test_exported_outputs_are_synthetic_not_real_robustness(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = robustness.run_synthetic_software_check(temporary)
            self.assertEqual(len(result["matrix"]), 12)
            with (Path(temporary) / "synthetic_robustness_matrix.csv").open(
                encoding="utf-8", newline=""
            ) as handle:
                rows = list(csv.DictReader(handle))
            self.assertTrue(all(row["decision_status"] == robustness.NO_ROBUSTNESS_CLAIM for row in rows))


if __name__ == "__main__":
    unittest.main()
