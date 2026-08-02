"""训练协议、数据封存保护、全连接基线与四模型统一评价框架。"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import sys
import time
from pathlib import Path


STAGE6_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = STAGE6_ROOT.parent
for source_dir in (
    STAGE6_ROOT / "src", PROJECT_ROOT / "阶段五" / "src",
    PROJECT_ROOT / "阶段一" / "src",
):
    sys.path.insert(0, str(source_dir))

import confidence_intervals  # noqa: E402
import physics_training  # noqa: E402
import set_encoder  # noqa: E402


SYNTHETIC_LABEL = set_encoder.SYNTHETIC_LABEL
NO_COMPARISON_CLAIM = "SYNTHETIC_PIPELINE_ONLY_NO_MODEL_ADVANTAGE_CLAIM"
SEALED_SUBSET = "independent_test"
TRAINABLE_SUBSETS = {"synthetic_pretrain", "development"}
EVALUATION_SUBSETS = {"synthetic_software_evaluation", "development", SEALED_SUBSET}
MODEL_NAMES = (
    "direct_optimization", "fully_connected", "pure_set_encoder", "physics_decoder_set_encoder",
)


class FixedVectorNetwork:
    """固定测点顺序的普通全连接对照，不具集合置换结构。"""

    def __init__(self, sensor_ids, hidden_dim=16, q_ref_MW=30.0,
                 x_bounds=(15.0, 85.0), seed=20260729):
        self.sensor_ids = tuple(str(value) for value in sensor_ids)
        if not self.sensor_ids or len(set(self.sensor_ids)) != len(self.sensor_ids):
            raise ValueError("全连接基线测点 ID 为空或重复")
        self.hidden_dim = int(hidden_dim)
        self.q_ref_MW = float(q_ref_MW)
        self.x_bounds = tuple(float(value) for value in x_bounds)
        self.seed = int(seed)
        rng = random.Random(self.seed)
        self.head = set_encoder.MLP(
            [2 * len(self.sensor_ids) + 5, self.hidden_dim, 2], rng,
            activate_last=False,
        )

    def forward_values(self, sample):
        applicability = set_encoder.validate_100m_sample(sample)
        rows = {str(row.get("id")): row for row in sample["sensors"]}
        if len(rows) != len(sample["sensors"]):
            raise ValueError("全连接输入测点 ID 缺失或重复")
        temperatures, masks = [], []
        for sensor_id in self.sensor_ids:
            row = rows.get(sensor_id)
            if row is None:
                temperatures.append(0.0)
                masks.append(0.0)
            else:
                mask = float(row["m"])
                if mask not in (0.0, 1.0):
                    raise ValueError("全连接输入 mask 必须为 0/1")
                temperatures.append(float(row["dT"]) / 100.0 if mask else 0.0)
                masks.append(mask)
        H = float(sample["H"])
        known = [float(sample["U"]) / 5.0, float(sample["Df"]) / H,
                 H / 5.0, float(sample["W"]) / 10.0, float(sample["T0_K"]) / 300.0]
        z_q, z_x = self.head(temperatures + masks + known)
        q_hat = self.q_ref_MW * z_q.exp()
        x_min, x_max = self.x_bounds
        x_hat = x_min + (x_max - x_min) * z_x.sigmoid()
        return {"Q_hat_MW": q_hat, "x_f_hat_m": x_hat, "z_Q": z_q, "z_x": z_x,
                "n_valid": int(sum(masks)), **applicability}

    def predict(self, sample):
        output = self.forward_values(sample)
        return {key: (value.data if isinstance(value, set_encoder.Value) else value)
                for key, value in output.items()}

    def named_parameters(self):
        yield from self.head.named_parameters("head")

    def parameters(self):
        return [parameter for _, parameter in self.named_parameters()]

    def zero_grad(self):
        for parameter in self.parameters():
            parameter.grad = 0.0

    def save(self, path, training_status):
        payload = {
            "architecture": "FIXED_VECTOR_FULLY_CONNECTED",
            "sensor_ids": list(self.sensor_ids), "hidden_dim": self.hidden_dim,
            "q_ref_MW": self.q_ref_MW, "x_bounds": list(self.x_bounds),
            "seed": self.seed, "training_status": training_status,
            "applicability_contract": {
                "protocol_version": set_encoder.direct_inversion.PROTOCOL_VERSION,
                "L_m": 100.0, "W_m": 10.0, "H_m": 5.0, "dx_m": 0.25,
                "measurement_bounds_m": [15.0, 85.0],
            },
            "state_dict": {name: value.data for name, value in self.named_parameters()},
        }
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class Adam:
    def __init__(self, parameters, learning_rate=1e-3, beta1=0.9, beta2=0.999,
                 epsilon=1e-8, max_grad_norm=10.0):
        self.parameters = list(parameters)
        if not self.parameters or learning_rate <= 0 or not 0 < beta1 < 1 or not 0 < beta2 < 1:
            raise ValueError("Adam 参数无效")
        self.learning_rate = float(learning_rate)
        self.beta1, self.beta2 = float(beta1), float(beta2)
        self.epsilon = float(epsilon)
        self.max_grad_norm = float(max_grad_norm)
        self.m = [0.0] * len(self.parameters)
        self.v = [0.0] * len(self.parameters)
        self.step_number = 0

    def step(self):
        gradients = [parameter.grad for parameter in self.parameters]
        if any(not math.isfinite(value) for value in gradients):
            raise ValueError("训练梯度含非有限值")
        norm = math.sqrt(sum(value * value for value in gradients))
        scale = 1.0 if norm <= self.max_grad_norm else self.max_grad_norm / max(norm, 1e-15)
        self.step_number += 1
        for index, (parameter, gradient) in enumerate(zip(self.parameters, gradients)):
            gradient *= scale
            self.m[index] = self.beta1 * self.m[index] + (1.0 - self.beta1) * gradient
            self.v[index] = self.beta2 * self.v[index] + (1.0 - self.beta2) * gradient * gradient
            m_hat = self.m[index] / (1.0 - self.beta1 ** self.step_number)
            v_hat = self.v[index] / (1.0 - self.beta2 ** self.step_number)
            parameter.data -= self.learning_rate * m_hat / (math.sqrt(v_hat) + self.epsilon)
            if not math.isfinite(parameter.data):
                raise ValueError("训练参数更新为非有限值")


def _subset(sample):
    return str(sample.get("subset") or "").strip()


def validate_training_access(samples):
    if not samples:
        raise ValueError("训练样本为空")
    forbidden = sorted({_subset(row) for row in samples} - TRAINABLE_SUBSETS)
    if forbidden:
        raise PermissionError(f"训练禁止访问这些子集: {forbidden}")
    if any(not str(row.get("case_id") or "").strip() for row in samples):
        raise ValueError("训练样本必须绑定完整物理工况 case_id")
    for row in samples:
        set_encoder.validate_100m_sample(row)


def grouped_kfold(samples, n_folds=5):
    groups = sorted({str(row.get("case_id") or "").strip() for row in samples})
    if "" in groups or not 2 <= n_folds <= len(groups):
        raise ValueError("分组折叠要求有效 case_id 且折数不超过组数")
    fold_of = {group: index % n_folds for index, group in enumerate(groups)}
    folds = []
    for fold in range(n_folds):
        train = [row for row in samples if fold_of[str(row["case_id"])] != fold]
        validation = [row for row in samples if fold_of[str(row["case_id"])] == fold]
        folds.append({"fold": fold, "train": train, "validation": validation})
    return folds


def release_evaluation_samples(samples, final_evaluation=False):
    if not samples:
        raise ValueError("评价样本为空")
    unknown = sorted({_subset(row) for row in samples} - EVALUATION_SUBSETS)
    if unknown:
        raise PermissionError(f"评价子集无效: {unknown}")
    if any(_subset(row) == SEALED_SUBSET for row in samples) and not final_evaluation:
        raise PermissionError("独立测试仍封存；只有设置冻结后的最终评价可访问")
    for row in samples:
        set_encoder.validate_100m_sample(row)
    return list(samples)


def _source_loss(model, sample):
    output = model.forward_values(sample)
    Q, x_f, H = float(sample["Q_MW"]), float(sample["x_f"]), float(sample["H"])
    return ((output["Q_hat_MW"].log() - math.log(Q)) ** 2
            + ((output["x_f_hat_m"] - x_f) / H) ** 2)


def train_network(model, samples, mode="source_only", decoder=None, epochs=5,
                  learning_rate=1e-3, seed=20260729, model_version="dev",
                  data_version="synthetic_software"):
    validate_training_access(samples)
    if mode not in ("source_only", "physics_joint"):
        raise ValueError("未知训练模式")
    if mode == "physics_joint" and decoder is None:
        raise ValueError("物理联合训练必须提供解码器")
    if int(epochs) != epochs or epochs < 1:
        raise ValueError("训练 epochs 必须为正整数")
    optimizer = Adam(model.parameters(), learning_rate=learning_rate)
    rng = random.Random(seed)
    logs = []
    order = list(range(len(samples)))
    for epoch in range(1, int(epochs) + 1):
        rng.shuffle(order)
        losses = []
        for index in order:
            model.zero_grad()
            if mode == "source_only":
                loss = _source_loss(model, samples[index])
            else:
                loss = physics_training.joint_loss(model, samples[index], decoder)["total"]
            if not math.isfinite(loss.data):
                raise ValueError("训练损失非有限")
            loss.backward()
            optimizer.step()
            losses.append(loss.data)
        logs.append({
            "epoch": epoch, "mean_loss": statistics.fmean(losses),
            "max_loss": max(losses), "mode": mode, "seed": int(seed),
            "model_version": model_version, "data_version": data_version,
            "n_samples": len(samples), "evidence_label": SYNTHETIC_LABEL,
            "decision_status": NO_COMPARISON_CLAIM,
        })
    return logs


def make_direct_predictor(closure_model, inversion_options=None):
    inversion_options = dict(inversion_options or {})

    def predict(sample):
        valid = [row for row in sample["sensors"] if float(row["m"]) == 1.0]
        scenario = {
            key: sample[key]
            for key in ("U", "Df", "H", "T0_K")
        }
        for key in ("L", "W", "dx", "protocol_version", "domain_censor_state"):
            if key in sample:
                scenario[key] = sample[key]
        result = confidence_intervals.correlated_direct_invert(
            closure_model, scenario, [row["x"] for row in valid],
            [row["dT"] for row in valid], **inversion_options,
        )
        return {"Q_hat_MW": result["Q_hat_MW"], "x_f_hat_m": result["x_f_hat_m"]}

    return predict


def build_model_registry(direct_predictor, fully_connected, pure_set_encoder,
                         physics_decoder_set_encoder):
    registry = {
        "direct_optimization": direct_predictor,
        "fully_connected": fully_connected.predict,
        "pure_set_encoder": pure_set_encoder.predict,
        "physics_decoder_set_encoder": physics_decoder_set_encoder.predict,
    }
    if tuple(registry) != MODEL_NAMES or any(not callable(value) for value in registry.values()):
        raise ValueError("四模型注册不完整")
    return registry


def evaluate_model(model_name, predictor, samples, decoder=None, final_evaluation=False):
    samples = release_evaluation_samples(samples, final_evaluation)
    q_errors, x_errors, xh_errors, temperature_errors, times = [], [], [], [], []
    applicability_counts = {
        "IN_DOMAIN_100M_CONDITIONAL": 0,
        "OOD_EXPLORATORY": 0,
        "APPLICABILITY_UNDETERMINED": 0,
    }
    for sample in samples:
        assessed = set_encoder.validate_100m_sample(sample)
        applicability_counts[assessed["applicability_status"]] += 1
        start = time.perf_counter()
        prediction = predictor(sample)
        times.append(time.perf_counter() - start)
        Q_hat, x_hat = float(prediction["Q_hat_MW"]), float(prediction["x_f_hat_m"])
        if not math.isfinite(Q_hat) or not math.isfinite(x_hat) or Q_hat <= 0:
            raise ValueError(f"{model_name} 返回无效预测")
        q_errors.append(abs(Q_hat - float(sample["Q_MW"])) / float(sample["Q_MW"]))
        x_error = abs(x_hat - float(sample["x_f"]))
        x_errors.append(x_error)
        xh_errors.append(x_error / float(sample["H"]))
        if decoder is not None:
            reconstructed = decoder.predict(Q_hat, x_hat, sample)
            residuals = [
                estimate - float(row["dT"])
                for row, estimate in zip(sample["sensors"], reconstructed)
                if float(row["m"]) == 1.0 and not bool(row.get("censored", False))
            ]
            if residuals:
                temperature_errors.append(math.sqrt(statistics.fmean(value * value for value in residuals)))
    return {
        "model": model_name, "n_cases": len(samples),
        "Q_relative_error_median": statistics.median(q_errors),
        "x_error_m_median": statistics.median(x_errors),
        "x_error_over_H_median": statistics.median(xh_errors),
        "temperature_rmse_C_median": statistics.median(temperature_errors) if temperature_errors else "",
        "inference_time_ms_median": 1000.0 * statistics.median(times),
        "core_domain_case_count": applicability_counts["IN_DOMAIN_100M_CONDITIONAL"],
        "ood_exploratory_case_count": applicability_counts["OOD_EXPLORATORY"],
        "applicability_undetermined_case_count": applicability_counts["APPLICABILITY_UNDETERMINED"],
        "evidence_label": SYNTHETIC_LABEL, "decision_status": NO_COMPARISON_CLAIM,
    }


def _constant_model():
    return {
        "family": "power_law", "epsilon": 1e-6,
        "coefficients": {name: [0.0, 0.0, 0.0, 0.0]
                         for name in ("Pe_e", "Da_e", "Pi_S", "delta_over_H")},
    }


def run_synthetic_pipeline(output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    generated = physics_training.generate_synthetic_samples(28, seed=20260729)
    training_samples, evaluation_samples = generated[:24], generated[24:]
    for sample in training_samples:
        sample["case_id"] = sample["sample_id"]
        sample["subset"] = "synthetic_pretrain"
    for sample in evaluation_samples:
        sample["case_id"] = sample["sample_id"]
        sample["subset"] = "synthetic_software_evaluation"
    sensor_ids = [row["id"] for row in physics_training.load_sensor_catalog()]
    fnn = FixedVectorNetwork(sensor_ids, seed=101)
    pure = set_encoder.SetEncoder(seed=102)
    physics = set_encoder.SetEncoder(seed=103)
    decoder = physics_training.ConstantClosurePhysicsDecoder()
    logs = []
    logs += [{"model": "fully_connected", **row} for row in train_network(
        fnn, training_samples, "source_only", epochs=4, learning_rate=2e-3,
        seed=101, model_version="fnn_synthetic_v1",
    )]
    logs += [{"model": "pure_set_encoder", **row} for row in train_network(
        pure, training_samples, "source_only", epochs=4, learning_rate=2e-3,
        seed=102, model_version="set_synthetic_v1",
    )]
    logs += [{"model": "physics_decoder_set_encoder", **row} for row in train_network(
        physics, training_samples, "physics_joint", decoder=decoder,
        epochs=4, learning_rate=1e-3, seed=103, model_version="physics_synthetic_v1",
    )]
    direct = make_direct_predictor(
        _constant_model(), {"q_grid": 10, "x_grid": 15, "n_starts": 2, "local_iterations": 10}
    )
    registry = build_model_registry(direct, fnn, pure, physics)
    metrics = [evaluate_model(name, predictor, evaluation_samples, decoder)
               for name, predictor in registry.items()]
    with (output_dir / "synthetic_training_log.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(logs[0]))
        writer.writeheader(); writer.writerows(logs)
    with (output_dir / "synthetic_four_model_metrics.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(metrics[0]))
        writer.writeheader(); writer.writerows(metrics)
    status = "SYNTHETIC_PRETRAINED_SOFTWARE_ONLY"
    fnn.save(output_dir / "synthetic_fnn_weights.json", status)
    pure.save(output_dir / "synthetic_pure_set_weights.json", status)
    physics.save(output_dir / "synthetic_physics_set_weights.json", status)
    protocol = {
        "random_seeds": {"data": 20260729, "fnn": 101, "pure_set": 102, "physics_set": 103},
        "model_versions": ["fnn_synthetic_v1", "set_synthetic_v1", "physics_synthetic_v1"],
        "data_version": "synthetic_software", "training_cases": len(training_samples),
        "evaluation_cases": len(evaluation_samples), "independent_test_accessed": False,
        "decoder_status": decoder.model_status, "evidence_label": SYNTHETIC_LABEL,
        "protocol_version": set_encoder.direct_inversion.PROTOCOL_VERSION,
        "domain_geometry_m": {"L": 100.0, "W": 10.0, "H": 5.0, "dx": 0.25},
        "measurement_bounds_m": [15.0, 85.0],
        "decision_status": NO_COMPARISON_CLAIM,
    }
    (output_dir / "synthetic_protocol.json").write_text(
        json.dumps(protocol, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"logs": logs, "metrics": metrics, "protocol": protocol}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=STAGE6_ROOT / "03_训练与四模型对照")
    args = parser.parse_args(argv)
    result = run_synthetic_pipeline(args.output_dir)
    print(f"synthetic training epochs={len(result['logs'])}, models={len(result['metrics'])}")
    print(NO_COMPARISON_CLAIM)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
