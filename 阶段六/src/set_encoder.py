"""无外部依赖的位置感知集合编码器与最小反向自动微分后端。"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from pathlib import Path


STAGE6_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = STAGE6_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT / "阶段五" / "src"))

import direct_inversion  # noqa: E402


SYNTHETIC_LABEL = "SYNTHETIC_SOFTWARE_TEST_NOT_SCIENTIFIC_EVIDENCE"
NO_PERFORMANCE_CLAIM = "UNTRAINED_INTERFACE_ONLY_NO_NETWORK_PERFORMANCE_CLAIM"


def validate_100m_sample(sample):
    """校验网络样本适用域；缺测传感器不参与空间范围判定。"""
    sensors = list(sample.get("sensors") or ())
    valid_xs = []
    for row in sensors:
        try:
            mask = float(row.get("m"))
        except (TypeError, ValueError):
            raise ValueError("传感器 m 必须可解析为 0/1") from None
        if mask == 1.0:
            valid_xs.append(row.get("x"))
    payload = dict(sample)
    payload["x"] = valid_xs
    return direct_inversion.validate_100m_observation(payload)


class Value:
    """支持本项目小型网络所需运算的标量反向自动微分节点。"""

    def __init__(self, data, children=(), operation=""):
        self.data = float(data)
        if not math.isfinite(self.data):
            raise ValueError("自动微分节点必须有限")
        self.grad = 0.0
        self._previous = set(children)
        self._operation = operation
        self._backward = lambda: None

    @staticmethod
    def wrap(value):
        return value if isinstance(value, Value) else Value(value)

    def __add__(self, other):
        other = self.wrap(other)
        output = Value(self.data + other.data, (self, other), "+")

        def backward():
            self.grad += output.grad
            other.grad += output.grad

        output._backward = backward
        return output

    __radd__ = __add__

    def __mul__(self, other):
        other = self.wrap(other)
        output = Value(self.data * other.data, (self, other), "*")

        def backward():
            self.grad += other.data * output.grad
            other.grad += self.data * output.grad

        output._backward = backward
        return output

    __rmul__ = __mul__

    def __neg__(self):
        return self * -1.0

    def __sub__(self, other):
        return self + (-self.wrap(other))

    def __rsub__(self, other):
        return self.wrap(other) - self

    def __truediv__(self, other):
        other = self.wrap(other)
        return self * (other ** -1.0)

    def __rtruediv__(self, other):
        return self.wrap(other) / self

    def __pow__(self, power):
        power = float(power)
        if self.data == 0.0 and power < 0:
            raise ValueError("自动微分中不能对零取负幂")
        output = Value(self.data ** power, (self,), f"**{power}")

        def backward():
            self.grad += power * self.data ** (power - 1.0) * output.grad

        output._backward = backward
        return output

    def exp(self):
        output = Value(math.exp(self.data), (self,), "exp")

        def backward():
            self.grad += output.data * output.grad

        output._backward = backward
        return output

    def log(self):
        if self.data <= 0:
            raise ValueError("自动微分 log 输入必须为正")
        output = Value(math.log(self.data), (self,), "log")

        def backward():
            self.grad += output.grad / self.data

        output._backward = backward
        return output

    def tanh(self):
        value = math.tanh(self.data)
        output = Value(value, (self,), "tanh")

        def backward():
            self.grad += (1.0 - value * value) * output.grad

        output._backward = backward
        return output

    def sigmoid(self):
        if self.data >= 0:
            value = 1.0 / (1.0 + math.exp(-self.data))
        else:
            exponential = math.exp(self.data)
            value = exponential / (1.0 + exponential)
        output = Value(value, (self,), "sigmoid")

        def backward():
            self.grad += value * (1.0 - value) * output.grad

        output._backward = backward
        return output

    def erf(self):
        value = math.erf(self.data)
        output = Value(value, (self,), "erf")

        def backward():
            self.grad += 2.0 / math.sqrt(math.pi) * math.exp(-(self.data ** 2)) * output.grad

        output._backward = backward
        return output

    def backward(self):
        ordering, visited = [], set()

        def visit(node):
            if node in visited:
                return
            visited.add(node)
            for previous in node._previous:
                visit(previous)
            ordering.append(node)

        visit(self)
        self.grad = 1.0
        for node in reversed(ordering):
            node._backward()


class Dense:
    def __init__(self, n_inputs, n_outputs, rng, scale=None):
        if n_inputs < 1 or n_outputs < 1:
            raise ValueError("Dense 维数必须为正")
        scale = float(scale if scale is not None else 1.0 / math.sqrt(n_inputs))
        self.weights = [
            [Value(rng.uniform(-scale, scale)) for _ in range(n_inputs)]
            for _ in range(n_outputs)
        ]
        self.biases = [Value(0.0) for _ in range(n_outputs)]

    def __call__(self, inputs):
        if not self.weights or len(inputs) != len(self.weights[0]):
            raise ValueError("Dense 输入宽度错误")
        return [
            sum((weight * value for weight, value in zip(weights, inputs)), bias)
            for weights, bias in zip(self.weights, self.biases)
        ]

    def named_parameters(self, prefix):
        for row, weights in enumerate(self.weights):
            for column, parameter in enumerate(weights):
                yield f"{prefix}.weight.{row}.{column}", parameter
        for index, parameter in enumerate(self.biases):
            yield f"{prefix}.bias.{index}", parameter


class MLP:
    def __init__(self, widths, rng, activate_last=False):
        if len(widths) < 2:
            raise ValueError("MLP 至少需要输入和输出层")
        self.layers = [
            Dense(widths[index], widths[index + 1], rng)
            for index in range(len(widths) - 1)
        ]
        self.activate_last = bool(activate_last)

    def __call__(self, inputs):
        values = [Value.wrap(value) for value in inputs]
        for index, layer in enumerate(self.layers):
            values = layer(values)
            if index < len(self.layers) - 1 or self.activate_last:
                values = [value.tanh() for value in values]
        return values

    def named_parameters(self, prefix):
        for index, layer in enumerate(self.layers):
            yield from layer.named_parameters(f"{prefix}.layer.{index}")


class SetEncoder:
    """共享传感器编码器 + 掩码 mean/max/count 聚合 + 约束输出。"""

    def __init__(self, latent_dim=6, hidden_dim=10, n_max=23,
                 q_ref_MW=30.0, x_bounds=(15.0, 85.0), seed=20260729):
        if latent_dim < 2 or hidden_dim < 2 or n_max < 1:
            raise ValueError("集合编码器维数无效")
        if q_ref_MW <= 0 or not x_bounds[0] < x_bounds[1]:
            raise ValueError("Q_ref 或位置边界无效")
        self.latent_dim = int(latent_dim)
        self.hidden_dim = int(hidden_dim)
        self.n_max = int(n_max)
        self.q_ref_MW = float(q_ref_MW)
        self.x_bounds = (float(x_bounds[0]), float(x_bounds[1]))
        self.seed = int(seed)
        rng = random.Random(self.seed)
        self.encoder = MLP([5, self.hidden_dim, self.latent_dim], rng, activate_last=True)
        head_width = 2 * self.latent_dim + 1 + 5
        self.head = MLP([head_width, self.hidden_dim, 2], rng, activate_last=False)

    @staticmethod
    def _validated_sample(sample, n_max):
        required = (
            "sensors", "U", "Df", "H", "W", "T0_K",
        )
        if any(key not in sample for key in required):
            raise ValueError("集合编码器样本缺少已知条件")
        sensors = list(sample["sensors"])
        if not sensors or len(sensors) > n_max:
            raise ValueError("传感器数量为空或超过 N_max")
        U, Df, H, W, T0 = (
            float(sample["U"]), float(sample["Df"]), float(sample["H"]),
            float(sample["W"]), float(sample["T0_K"]),
        )
        if not all(math.isfinite(value) for value in (U, Df, H, W, T0)):
            raise ValueError("已知条件必须有限")
        if U < 0 or min(Df, H, W, T0) <= 0:
            raise ValueError("U>=0 且 Df/H/W/T0>0")
        normalized = []
        for row in sensors:
            if any(key in row for key in ("domain_censored", "domain_mask", "physical_domain_mask")):
                raise ValueError("物理域删失必须是工况级字段，不能复用传感器缺测 mask")
            if any(key not in row for key in ("dT", "x", "m")):
                raise ValueError("单传感器输入必须含 dT/x/m")
            dT, x, mask = float(row["dT"]), float(row["x"]), float(row["m"])
            if not math.isfinite(dT) or not math.isfinite(x) or mask not in (0.0, 1.0):
                raise ValueError("传感器 dT/x 必须有限且 m 为 0/1")
            normalized.append({"dT": dT, "x": x, "m": mask})
        if sum(row["m"] for row in normalized) < 1:
            raise ValueError("至少需要一个有效传感器")
        applicability = validate_100m_sample({**sample, "sensors": normalized})
        return normalized, U, Df, H, W, T0, applicability

    def forward_values(self, sample):
        sensors, U, Df, H, W, T0, applicability = self._validated_sample(sample, self.n_max)
        encoded = []
        for row in sensors:
            features = [
                row["dT"] / 100.0, row["x"] / H, row["m"],
                U / 5.0, Df / H,
            ]
            encoded.append((row["m"], self.encoder(features)))
        valid = [latent for mask, latent in encoded if mask == 1.0]
        count = len(valid)
        mean = [sum(row[index] for row in valid) / count for index in range(self.latent_dim)]
        maximum = [max((row[index] for row in valid), key=lambda value: value.data)
                   for index in range(self.latent_dim)]
        aggregate = mean + maximum + [count / self.n_max]
        known = [U / 5.0, Df / H, H / 5.0, W / 10.0, T0 / 300.0]
        z_q, z_x = self.head(aggregate + known)
        q_hat = self.q_ref_MW * z_q.exp()
        x_min, x_max = self.x_bounds
        x_hat = x_min + (x_max - x_min) * z_x.sigmoid()
        return {"Q_hat_MW": q_hat, "x_f_hat_m": x_hat, "z_Q": z_q, "z_x": z_x,
                "n_valid": count, **applicability}

    def predict(self, sample):
        output = self.forward_values(sample)
        return {key: (value.data if isinstance(value, Value) else value)
                for key, value in output.items()}

    def named_parameters(self):
        yield from self.encoder.named_parameters("encoder")
        yield from self.head.named_parameters("head")

    def parameters(self):
        return [parameter for _, parameter in self.named_parameters()]

    def zero_grad(self):
        for parameter in self.parameters():
            parameter.grad = 0.0

    def state_dict(self):
        return {name: parameter.data for name, parameter in self.named_parameters()}

    def save(self, path, training_status="UNTRAINED_INITIALIZATION"):
        payload = {
            "architecture": {
                "latent_dim": self.latent_dim, "hidden_dim": self.hidden_dim,
                "n_max": self.n_max, "q_ref_MW": self.q_ref_MW,
                "x_bounds": list(self.x_bounds), "seed": self.seed,
            },
            "training_status": str(training_status),
            "applicability_contract": {
                "protocol_version": direct_inversion.PROTOCOL_VERSION,
                "L_m": direct_inversion.DOMAIN_LENGTH_M,
                "W_m": 10.0, "H_m": 5.0, "dx_m": 0.25,
                "measurement_bounds_m": list(direct_inversion.MEASUREMENT_BOUNDS_M),
            },
            "state_dict": self.state_dict(),
        }
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path):
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        model = cls(**payload["architecture"])
        state = payload["state_dict"]
        parameters = dict(model.named_parameters())
        if set(state) != set(parameters):
            raise ValueError("模型权重字段与架构不一致")
        for name, value in state.items():
            value = float(value)
            if not math.isfinite(value):
                raise ValueError(f"模型参数 {name} 非有限")
            parameters[name].data = value
        return model, payload["training_status"]


def _synthetic_sample():
    xs = [15.0, 25.0, 35.0, 45.0, 55.0, 65.0, 75.0, 85.0]
    return {
        "sensors": [
            {"dT": 80.0 * math.exp(-abs(x - 51.0) / 13.0), "x": x, "m": 1.0}
            for x in xs
        ],
        "U": 2.0, "Df": 5.0, "H": 5.0, "W": 10.0, "T0_K": 293.15,
        "L": 100.0, "dx": 0.25,
        "protocol_version": direct_inversion.PROTOCOL_VERSION,
        "domain_censor_state": "none",
    }


def run_synthetic_interface_check(output_dir):
    output_dir = Path(output_dir)
    model = SetEncoder()
    sample = _synthetic_sample()
    baseline = model.predict(sample)
    permuted = {**sample, "sensors": list(reversed(sample["sensors"]))}
    permutation = model.predict(permuted)
    missing_rows = [dict(row) for row in sample["sensors"]]
    for index in (1, 4, 6):
        missing_rows[index]["m"] = 0.0
    missing = model.predict({**sample, "sensors": missing_rows})
    reduced = model.predict({**sample, "sensors": sample["sensors"][::2]})

    model.zero_grad()
    differentiable = model.forward_values(sample)
    loss = (differentiable["Q_hat_MW"] / model.q_ref_MW - 1.0) ** 2
    loss = loss + ((differentiable["x_f_hat_m"] - 50.0) / 5.0) ** 2
    loss.backward()
    gradients = [parameter.grad for parameter in model.parameters()]
    rows = [{
        "check": "permutation_invariance",
        "Q_abs_difference": abs(baseline["Q_hat_MW"] - permutation["Q_hat_MW"]),
        "x_abs_difference_m": abs(baseline["x_f_hat_m"] - permutation["x_f_hat_m"]),
        "n_valid": baseline["n_valid"], "status": "PASS" if (
            abs(baseline["Q_hat_MW"] - permutation["Q_hat_MW"]) < 1e-12
            and abs(baseline["x_f_hat_m"] - permutation["x_f_hat_m"]) < 1e-12
            and baseline["n_valid"] == permutation["n_valid"]
        ) else "FAIL",
        "evidence_label": SYNTHETIC_LABEL, "decision_status": NO_PERFORMANCE_CLAIM,
    }, {
        "check": "random_missing",
        "Q_abs_difference": "", "x_abs_difference_m": "", "n_valid": missing["n_valid"],
        "status": "PASS" if missing["n_valid"] == 5 else "FAIL",
        "evidence_label": SYNTHETIC_LABEL, "decision_status": NO_PERFORMANCE_CLAIM,
    }, {
        "check": "sensor_count_change",
        "Q_abs_difference": "", "x_abs_difference_m": "", "n_valid": reduced["n_valid"],
        "status": "PASS" if reduced["n_valid"] == 4 else "FAIL",
        "evidence_label": SYNTHETIC_LABEL, "decision_status": NO_PERFORMANCE_CLAIM,
    }, {
        "check": "constraints_and_gradient",
        "Q_abs_difference": "", "x_abs_difference_m": "", "n_valid": baseline["n_valid"],
        "status": "PASS" if (
            baseline["Q_hat_MW"] > 0 and model.x_bounds[0] < baseline["x_f_hat_m"] < model.x_bounds[1]
            and all(math.isfinite(value) for value in gradients)
            and any(abs(value) > 0 for value in gradients)
        ) else "FAIL",
        "evidence_label": SYNTHETIC_LABEL, "decision_status": NO_PERFORMANCE_CLAIM,
    }]
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "synthetic_set_encoder_check.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    model.save(output_dir / "untrained_initialization.json")
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=STAGE6_ROOT / "01_集合编码器")
    args = parser.parse_args(argv)
    rows = run_synthetic_interface_check(args.output_dir)
    print(f"set-encoder interface checks: {sum(row['status'] == 'PASS' for row in rows)}/{len(rows)}")
    print(NO_PERFORMANCE_CLAIM)
    return 0 if all(row["status"] == "PASS" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
