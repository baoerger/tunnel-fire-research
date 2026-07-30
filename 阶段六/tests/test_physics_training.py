import copy
import math
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
PROJECT = HERE.parents[1]
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(PROJECT / "阶段五" / "src"))

import direct_inversion
import physics_training as training
import set_encoder


def constant_latent_model():
    return {
        "family": "power_law", "epsilon": 1e-6,
        "coefficients": {
            output: [0.0, 0.0, 0.0, 0.0]
            for output in ("Pe_e", "Da_e", "Pi_S", "delta_over_H")
        },
    }


def decoder_sample(xs):
    return {
        "sensors": [{"id": f"s{i}", "x": x, "dT": 0.0, "m": 1.0, "sigma_C": 2.0}
                    for i, x in enumerate(xs)],
        "U": 2.0, "Df": 5.0, "H": 5.0, "W": 10.0, "T0_K": 293.15,
        "Q_MW": 35.0, "x_f": 50.0, "censor_threshold_C": 2.0,
    }


class PhysicsTrainingTests(unittest.TestCase):
    def setUp(self):
        self.decoder = training.ConstantClosurePhysicsDecoder()

    def test_differentiable_decoder_matches_stage5_synthetic_forward(self):
        xs = [20.0, 35.0, 47.5, 55.0, 70.0, 85.0]
        sample = decoder_sample(xs)
        expected = direct_inversion.physics_forward(
            constant_latent_model(), 35.0, 50.0, 2.0, 5.0, xs
        )
        actual = self.decoder.predict(35.0, 50.0, sample)
        for left, right in zip(actual, expected):
            self.assertAlmostEqual(left, right, places=11)

    def test_decoder_gradients_match_finite_difference(self):
        sample = decoder_sample([20.0, 35.0, 45.0, 60.0, 75.0])
        Q = set_encoder.Value(35.0)
        x_f = set_encoder.Value(50.0)
        objective = sum(self.decoder(Q, x_f, sample), set_encoder.Value(0.0))
        objective.backward()

        def total(q, x):
            return sum(self.decoder.predict(q, x, sample))

        step_q, step_x = 1e-4, 1e-5
        fd_q = (total(35.0 + step_q, 50.0) - total(35.0 - step_q, 50.0)) / (2 * step_q)
        fd_x = (total(35.0, 50.0 + step_x) - total(35.0, 50.0 - step_x)) / (2 * step_x)
        self.assertAlmostEqual(Q.grad, fd_q, places=6)
        self.assertAlmostEqual(x_f.grad, fd_x, places=5)

    def test_synthetic_generator_is_deterministic_and_marks_every_sample(self):
        first = training.generate_synthetic_samples(
            8, seed=41, missing_probability=0.4, position_sigma_m=0.2,
            model_residual_sigma_C=1.5,
        )
        second = training.generate_synthetic_samples(
            8, seed=41, missing_probability=0.4, position_sigma_m=0.2,
            model_residual_sigma_C=1.5,
        )
        self.assertEqual(first, second)
        self.assertTrue(all(row["evidence_label"] == training.SYNTHETIC_LABEL for row in first))
        self.assertTrue(all(row["decision_status"] == training.NO_SCIENTIFIC_CLAIM for row in first))
        self.assertTrue(all(any(sensor["m"] == 1.0 for sensor in row["sensors"]) for row in first))
        self.assertTrue(any(sensor["m"] == 0.0 for row in first for sensor in row["sensors"]))
        self.assertTrue(any(sensor["x"] != sensor["nominal_x"] for row in first for sensor in row["sensors"]))

    def test_joint_loss_has_finite_components_and_backpropagates(self):
        sample = training.generate_synthetic_samples(1, seed=5, sensor_counts=(12,))[0]
        model = set_encoder.SetEncoder(seed=7)
        model.zero_grad()
        losses = training.joint_loss(model, sample, self.decoder)
        self.assertTrue(all(
            math.isfinite(losses[name].data) and losses[name].data >= 0
            for name in ("total", "source", "reconstruction", "censor")
        ))
        losses["total"].backward()
        gradients = [parameter.grad for parameter in model.parameters()]
        self.assertTrue(all(math.isfinite(value) for value in gradients))
        self.assertTrue(any(abs(value) > 1e-14 for value in gradients))

    def test_censored_loss_does_not_use_below_threshold_observed_value(self):
        base = training.generate_synthetic_samples(
            1, seed=8, sensor_counts=(4,), missing_probability=0.0
        )[0]
        for row in base["sensors"]:
            row["censored"] = True
            row["m"] = 1.0

        class FixedModel:
            @staticmethod
            def forward_values(_sample):
                return {
                    "Q_hat_MW": set_encoder.Value(35.0),
                    "x_f_hat_m": set_encoder.Value(50.0),
                    "n_valid": len(_sample["sensors"]),
                }

        low = copy.deepcopy(base)
        high = copy.deepcopy(base)
        for row in low["sensors"]:
            row["dT"] = -100.0
        for row in high["sensors"]:
            row["dT"] = 1.99
        loss_low = training.joint_loss(FixedModel(), low, self.decoder)["censor"].data
        loss_high = training.joint_loss(FixedModel(), high, self.decoder)["censor"].data
        self.assertEqual(loss_low, loss_high)

    def test_generator_and_joint_loss_invalid_inputs_fail(self):
        with self.assertRaisesRegex(ValueError, "范围"):
            training.generate_synthetic_samples(2, q_bounds=(10.0, 5.0))
        with self.assertRaisesRegex(ValueError, "缺测"):
            training.generate_synthetic_samples(2, missing_probability=1.0)
        sample = training.generate_synthetic_samples(1, seed=12)[0]
        with self.assertRaisesRegex(ValueError, "至少一个"):
            training.joint_loss(
                set_encoder.SetEncoder(), sample, self.decoder,
                lambda_source=0.0, lambda_reconstruction=0.0, lambda_censor=0.0,
            )

    def test_exported_dataset_remains_separate_synthetic_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = training.run_synthetic_software_check(temporary, n_samples=10)
            self.assertEqual(result["summary"]["status"], "PASS")
            lines = (Path(temporary) / "synthetic_training_samples.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            self.assertEqual(len(lines), 10)
            self.assertIn(training.SYNTHETIC_LABEL, lines[0])
            self.assertIn(training.NO_SCIENTIFIC_CLAIM, lines[0])


if __name__ == "__main__":
    unittest.main()
