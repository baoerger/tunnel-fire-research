import json
import math
import random
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

import set_encoder as network


def sample(n=8):
    xs = [15.0 + 70.0 * index / (n - 1) for index in range(n)] if n > 1 else [50.0]
    return {
        "sensors": [
            {"dT": 70.0 * math.exp(-abs(x - 50.0) / 12.0), "x": x, "m": 1.0}
            for x in xs
        ],
        "U": 2.0, "Df": 5.0, "H": 5.0, "W": 10.0, "T0_K": 293.15,
    }


class SetEncoderTests(unittest.TestCase):
    def setUp(self):
        self.model = network.SetEncoder(seed=31)
        self.sample = sample()

    def test_scalar_autodiff_matches_analytic_gradient(self):
        x = network.Value(1.2)
        y = network.Value(-0.7)
        output = (x * y + x.exp() + y.tanh()) ** 2
        output.backward()
        inside = 1.2 * -0.7 + math.exp(1.2) + math.tanh(-0.7)
        expected_x = 2.0 * inside * (-0.7 + math.exp(1.2))
        expected_y = 2.0 * inside * (1.2 + 1.0 - math.tanh(-0.7) ** 2)
        self.assertAlmostEqual(x.grad, expected_x, places=10)
        self.assertAlmostEqual(y.grad, expected_y, places=10)

    def test_prediction_is_permutation_invariant(self):
        baseline = self.model.predict(self.sample)
        shuffled = list(self.sample["sensors"])
        random.Random(9).shuffle(shuffled)
        permuted = self.model.predict({**self.sample, "sensors": shuffled})
        self.assertAlmostEqual(baseline["Q_hat_MW"], permuted["Q_hat_MW"], places=12)
        self.assertAlmostEqual(baseline["x_f_hat_m"], permuted["x_f_hat_m"], places=12)
        self.assertEqual(baseline["n_valid"], permuted["n_valid"])

    def test_masked_sensor_values_do_not_change_prediction(self):
        sensors = [dict(row) for row in self.sample["sensors"]]
        sensors[2]["m"] = 0.0
        first = self.model.predict({**self.sample, "sensors": sensors})
        sensors[2]["dT"] = -9999.0
        sensors[2]["x"] = 9999.0
        second = self.model.predict({**self.sample, "sensors": sensors})
        self.assertEqual(first, second)
        self.assertEqual(first["n_valid"], 7)

    def test_sensor_count_variation_and_constraints(self):
        for count in (1, 4, 8, 16, 23):
            prediction = self.model.predict(sample(count))
            self.assertEqual(prediction["n_valid"], count)
            self.assertGreater(prediction["Q_hat_MW"], 0.0)
            self.assertGreater(prediction["x_f_hat_m"], 15.0)
            self.assertLess(prediction["x_f_hat_m"], 85.0)

    def test_network_parameters_receive_finite_nonzero_gradients(self):
        self.model.zero_grad()
        output = self.model.forward_values(self.sample)
        loss = ((output["Q_hat_MW"].log() - math.log(40.0)) ** 2
                + ((output["x_f_hat_m"] - 52.0) / 5.0) ** 2)
        loss.backward()
        gradients = [parameter.grad for parameter in self.model.parameters()]
        self.assertTrue(all(math.isfinite(value) for value in gradients))
        self.assertGreater(sum(abs(value) > 1e-14 for value in gradients), len(gradients) // 2)

    def test_save_load_roundtrip_and_untrusted_nonfinite_rejection(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "model.json"
            self.model.save(path)
            loaded, status = network.SetEncoder.load(path)
            self.assertEqual(status, "UNTRAINED_INITIALIZATION")
            self.assertEqual(self.model.predict(self.sample), loaded.predict(self.sample))
            payload = json.loads(path.read_text(encoding="utf-8"))
            first_name = next(iter(payload["state_dict"]))
            payload["state_dict"][first_name] = float("nan")
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "非有限"):
                network.SetEncoder.load(path)

    def test_invalid_masks_and_conditions_fail_loudly(self):
        invalid = sample(4)
        for row in invalid["sensors"]:
            row["m"] = 0.0
        with self.assertRaisesRegex(ValueError, "至少需要"):
            self.model.predict(invalid)
        with self.assertRaisesRegex(ValueError, "U>=0"):
            self.model.predict({**self.sample, "U": -1.0})
        bad_mask = sample(4)
        bad_mask["sensors"][0]["m"] = 0.5
        with self.assertRaisesRegex(ValueError, "0/1"):
            self.model.predict(bad_mask)

    def test_synthetic_interface_outputs_have_no_performance_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            rows = network.run_synthetic_interface_check(temporary)
            self.assertTrue(all(row["status"] == "PASS" for row in rows))
            self.assertTrue(all(row["evidence_label"] == network.SYNTHETIC_LABEL for row in rows))
            self.assertTrue(all(row["decision_status"] == network.NO_PERFORMANCE_CLAIM for row in rows))
            payload = json.loads(
                (Path(temporary) / "untrained_initialization.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["training_status"], "UNTRAINED_INITIALIZATION")


if __name__ == "__main__":
    unittest.main()
