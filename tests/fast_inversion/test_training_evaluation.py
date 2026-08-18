import csv
import json
import math
import tempfile
import unittest
from pathlib import Path

from extras.fast_inversion import physics_training, set_encoder
from extras.fast_inversion import training_evaluation as pipeline


def labeled_samples(n=6, subset="synthetic_pretrain"):
    rows = physics_training.generate_synthetic_samples(
        n, seed=77, sensor_counts=(8,), missing_probability=0.0,
        model_residual_sigma_C=0.0, position_sigma_m=0.0,
    )
    for index, row in enumerate(rows):
        row["case_id"] = f"case_{index // 2}"
        row["subset"] = subset
    return rows


class TrainingEvaluationTests(unittest.TestCase):
    def test_adam_reduces_simple_quadratic(self):
        parameter = set_encoder.Value(3.0)
        optimizer = pipeline.Adam([parameter], learning_rate=0.1)
        initial = parameter.data ** 2
        for _ in range(40):
            parameter.grad = 0.0
            loss = parameter ** 2
            loss.backward()
            optimizer.step()
        self.assertLess(parameter.data ** 2, initial * 0.02)

    def test_training_rejects_independent_and_unknown_subsets(self):
        model = set_encoder.SetEncoder(seed=1)
        with self.assertRaises(PermissionError):
            pipeline.train_network(model, labeled_samples(2, "independent_test"))
        with self.assertRaises(PermissionError):
            pipeline.train_network(model, labeled_samples(2, "mystery"))

    def test_training_and_evaluation_label_incomplete_or_ood_domain_metadata(self):
        model = set_encoder.SetEncoder(seed=2)
        training = labeled_samples(2)
        training[0].pop("dx")
        logs = pipeline.train_network(model, training, epochs=1)
        self.assertEqual(1, len(logs))
        self.assertEqual(
            "APPLICABILITY_UNDETERMINED",
            set_encoder.validate_100m_sample(training[0])["applicability_status"],
        )
        evaluation = labeled_samples(1, "synthetic_software_evaluation")
        evaluation[0]["protocol_version"] = "UNKNOWN"
        metrics = pipeline.evaluate_model(
            "perfect", lambda row: {"Q_hat_MW": row["Q_MW"], "x_f_hat_m": row["x_f"]},
            evaluation,
        )
        self.assertEqual(1, metrics["ood_exploratory_case_count"])

    def test_grouped_folds_never_split_case(self):
        samples = labeled_samples(8)
        folds = pipeline.grouped_kfold(samples, n_folds=4)
        for fold in folds:
            train_groups = {row["case_id"] for row in fold["train"]}
            validation_groups = {row["case_id"] for row in fold["validation"]}
            self.assertFalse(train_groups & validation_groups)
            self.assertTrue(validation_groups)

    def test_independent_evaluation_requires_explicit_final_release(self):
        samples = labeled_samples(2, "independent_test")
        with self.assertRaisesRegex(PermissionError, "封存"):
            pipeline.release_evaluation_samples(samples)
        self.assertEqual(pipeline.release_evaluation_samples(samples, final_evaluation=True), samples)

    def test_fixed_vector_network_handles_missing_ids_and_is_constrained(self):
        sample = labeled_samples(1)[0]
        catalog_ids = [row["id"] for row in physics_training.load_sensor_catalog()]
        model = pipeline.FixedVectorNetwork(catalog_ids, seed=3)
        prediction = model.predict(sample)
        self.assertGreater(prediction["Q_hat_MW"], 0.0)
        self.assertGreater(prediction["x_f_hat_m"], 15.0)
        self.assertLess(prediction["x_f_hat_m"], 85.0)
        self.assertEqual(prediction["n_valid"], 8)

    def test_training_logs_versions_and_updates_parameters(self):
        samples = labeled_samples(4)
        model = set_encoder.SetEncoder(seed=4)
        before = model.state_dict()
        logs = pipeline.train_network(
            model, samples, mode="source_only", epochs=2, learning_rate=1e-3,
            seed=19, model_version="unit_model", data_version="unit_data",
        )
        self.assertEqual(len(logs), 2)
        self.assertTrue(all(row["model_version"] == "unit_model" for row in logs))
        self.assertTrue(all(row["data_version"] == "unit_data" for row in logs))
        self.assertTrue(any(before[name] != value for name, value in model.state_dict().items()))

    def test_unified_evaluator_and_registry_cover_exactly_four_models(self):
        samples = labeled_samples(3, "synthetic_software_evaluation")

        def perfect(sample):
            return {"Q_hat_MW": sample["Q_MW"], "x_f_hat_m": sample["x_f"]}

        metrics = pipeline.evaluate_model("perfect", perfect, samples)
        self.assertEqual(metrics["Q_relative_error_median"], 0.0)
        self.assertEqual(metrics["x_error_m_median"], 0.0)
        ids = [row["id"] for row in physics_training.load_sensor_catalog()]
        registry = pipeline.build_model_registry(
            perfect, pipeline.FixedVectorNetwork(ids), set_encoder.SetEncoder(), set_encoder.SetEncoder(seed=2)
        )
        self.assertEqual(tuple(registry), pipeline.MODEL_NAMES)

    def test_synthetic_pipeline_writes_real_logs_without_advantage_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = pipeline.run_synthetic_pipeline(temporary)
            self.assertEqual(len(result["logs"]), 12)
            self.assertEqual(len(result["metrics"]), 4)
            self.assertFalse(result["protocol"]["independent_test_accessed"])
            with (Path(temporary) / "synthetic_four_model_metrics.csv").open(
                encoding="utf-8", newline=""
            ) as handle:
                metrics = list(csv.DictReader(handle))
            self.assertEqual({row["model"] for row in metrics}, set(pipeline.MODEL_NAMES))
            self.assertTrue(all(row["decision_status"] == pipeline.NO_COMPARISON_CLAIM for row in metrics))
            weight = json.loads(
                (Path(temporary) / "synthetic_physics_set_weights.json").read_text(encoding="utf-8")
            )
            self.assertEqual(weight["training_status"], "SYNTHETIC_PRETRAINED_SOFTWARE_ONLY")


if __name__ == "__main__":
    unittest.main()
