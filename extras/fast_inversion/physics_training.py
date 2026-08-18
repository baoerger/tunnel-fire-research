"""明确标记的合成样本、可替换可微物理解码器和联合损失。"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

from extras.legacy_modeling import fisher_layout
from src.fds import tunnel_config as cfg
from . import set_encoder


SYNTHETIC_LABEL = set_encoder.SYNTHETIC_LABEL
NO_SCIENTIFIC_CLAIM = "SYNTHETIC_TRAINING_FRAMEWORK_ONLY_NO_SCIENTIFIC_CLAIM"
DOMAIN_PARAMETER_MASKS = {
    "none": {"kappa_u": 1, "kappa_d": 1},
    "bilateral_identifiable": {"kappa_u": 1, "kappa_d": 1},
    "upstream_censored": {"kappa_u": 0, "kappa_d": 1},
    "no_obvious_backflow": {"kappa_u": 0, "kappa_d": 1},
    "downstream_domain_censored": {"kappa_u": 1, "kappa_d": 0},
}


def _finite(value, name):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} 必须有限")
    return value


def validate_domain_censor_contract(sample):
    """区分传感器缺测、低温升删失和 100 m 物理域删失。"""
    applicability = set_encoder.validate_100m_sample(sample)
    supplied_state = sample.get("domain_censor_state")
    state = str(supplied_state or "none").strip()
    if state not in DOMAIN_PARAMETER_MASKS:
        raise ValueError(f"未知 domain_censor_state={state!r}")
    for sensor in sample.get("sensors", ()):
        if any(key in sensor for key in ("domain_censored", "domain_mask", "physical_domain_mask")):
            raise ValueError("物理域删失不能写入单传感器或复用 m 缺测掩码")
    expected = DOMAIN_PARAMETER_MASKS[state]
    supplied = sample.get("parameter_target_mask")
    if supplied is not None:
        if not isinstance(supplied, dict):
            raise ValueError("parameter_target_mask 必须是字典")
        normalized = {}
        for name in ("kappa_u", "kappa_d"):
            value = supplied.get(name)
            if value not in (0, 1, False, True):
                raise ValueError("parameter_target_mask 必须使用 0/1")
            normalized[name] = int(value)
        if normalized != expected:
            raise ValueError(f"parameter_target_mask 与 {state} 不一致")
    return {
        **applicability, "domain_censor_state": state,
        "domain_censor_assumption": (
            "REPORTED" if supplied_state else "ASSUMED_NONE_FOR_EXPLORATORY_COMPUTATION"
        ),
        "parameter_target_mask": dict(expected),
    }


class ConstantClosurePhysicsDecoder:
    """与阶段五常数潜变量软件模型一致的可微解析解码器。

    该闭合只用于软件验证；正式训练必须替换为阶段四固定后的真实闭合。
    """

    model_status = "SYNTHETIC_CONSTANT_LATENT_CLOSURE_NOT_FORMAL"

    def __init__(self, chi_r=cfg.CHI_R_PRESET, epsilon=1e-6):
        self.chi_r = _finite(chi_r, "chi_r")
        self.epsilon = _finite(epsilon, "epsilon")
        if not 0 <= self.chi_r < 1 or self.epsilon <= 0:
            raise ValueError("解码器 chi_r/epsilon 无效")

    def __call__(self, Q_hat_MW, x_f_hat_m, sample):
        Q = set_encoder.Value.wrap(Q_hat_MW)
        x_f = set_encoder.Value.wrap(x_f_hat_m)
        U = _finite(sample["U"], "U")
        Df = _finite(sample["Df"], "Df")
        H = _finite(sample["H"], "H")
        T0 = _finite(sample["T0_K"], "T0_K")
        if Q.data <= 0 or U < 0 or Df <= 0 or H <= 0 or T0 <= 0:
            raise ValueError("物理解码输入要求 Q/Df/H/T0>0 且 U>=0")
        log_two = math.log(2.0)
        fr = U / math.sqrt(cfg.G * H)
        pe = fr * log_two
        da = log_two + self.epsilon
        root = math.sqrt(pe * pe + 4.0 * da)
        qstar_scale = (
            1e6 * (1.0 - self.chi_r)
            / (cfg.RHO_0 * cfg.CP * cfg.T_AMBIENT_K * math.sqrt(cfg.G) * H ** 2.5)
        )
        pi_s = Q * (qstar_scale * log_two)
        amplitude = pi_s / root
        xp = x_f + fr * log_two * H
        kappa_u, kappa_d = 0.5 * (root + pe), 0.5 * (root - pe)
        predictions = []
        for sensor in sample["sensors"]:
            x = _finite(sensor["x"], "sensor.x")
            xi = (x - xp) / H
            exponent = kappa_u * xi if xi.data < 0 else -kappa_d * xi
            predictions.append(T0 * amplitude * exponent.exp())
        return predictions

    def predict(self, Q_MW, x_f_m, sample):
        return [value.data for value in self(Q_MW, x_f_m, sample)]


def load_sensor_catalog(path=None):
    path = Path(path or (
        PROJECT_ROOT / "config" / "reference" / "legacy_sensor_layout.csv"
    ))
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    sensors = [{"id": str(row["id"]), "x": _finite(row["x"], "sensor.x")} for row in rows]
    if not sensors or len({row["id"] for row in sensors}) != len(sensors):
        raise ValueError("合成生成器测点目录为空或重复")
    return sensors


def _correlated_noise(xs, sigma_C, correlation_length_m, nugget_fraction, rng):
    covariance = fisher_layout.exponential_covariance(
        xs, sigma_C, correlation_length_m, nugget_fraction
    )
    factor = fisher_layout.cholesky_factor(covariance)
    standard = [rng.gauss(0.0, 1.0) for _ in xs]
    return [sum(factor[i][j] * standard[j] for j in range(i + 1))
            for i in range(len(xs))]


def generate_synthetic_samples(n_samples, seed=20260729, sensor_catalog=None,
                               q_bounds=(5.0, 100.0), x_bounds=(35.0, 65.0),
                               u_bounds=(0.0, 5.0), df_bounds=(2.0, 9.0),
                               sensor_counts=(4, 8, 12, 16, 23), sigma_C=2.0,
                               correlation_length_m=7.5, nugget_fraction=0.05,
                               position_sigma_m=0.10, missing_probability=0.15,
                               model_residual_sigma_C=1.0, censor_threshold_C=2.0,
                               decoder=None):
    """生成仅用于训练/软件测试的合成样本，不写入正式数据库。"""
    if int(n_samples) != n_samples or n_samples < 1:
        raise ValueError("合成样本数必须为正整数")
    values = [*q_bounds, *x_bounds, *u_bounds, *df_bounds, sigma_C,
              correlation_length_m, nugget_fraction, position_sigma_m,
              missing_probability, model_residual_sigma_C, censor_threshold_C]
    if not all(math.isfinite(float(value)) for value in values):
        raise ValueError("合成生成配置必须有限")
    if not (0 < q_bounds[0] < q_bounds[1] and x_bounds[0] < x_bounds[1]
            and 0 <= u_bounds[0] < u_bounds[1] and 0 < df_bounds[0] < df_bounds[1]):
        raise ValueError("合成参数范围无效")
    if sigma_C <= 0 or correlation_length_m <= 0 or not 0 <= nugget_fraction < 1:
        raise ValueError("合成噪声配置无效")
    if position_sigma_m < 0 or not 0 <= missing_probability < 1 or model_residual_sigma_C < 0:
        raise ValueError("位置/缺测/模型残差配置无效")
    catalog = list(sensor_catalog or load_sensor_catalog())
    counts = tuple(sorted({int(value) for value in sensor_counts}))
    if not counts or counts[0] < 1 or counts[-1] > len(catalog):
        raise ValueError("合成传感器数量配置无效")
    decoder = decoder or ConstantClosurePhysicsDecoder()
    rng = random.Random(seed)
    samples = []
    for index in range(int(n_samples)):
        Q = math.exp(rng.uniform(math.log(q_bounds[0]), math.log(q_bounds[1])))
        x_f = rng.uniform(*x_bounds)
        U = rng.uniform(*u_bounds)
        Df = rng.uniform(*df_bounds)
        count = counts[index % len(counts)]
        chosen = sorted(rng.sample(catalog, count), key=lambda row: row["x"])
        base_sample = {
            "sensors": [{"id": row["id"], "x": row["x"], "dT": 0.0, "m": 1.0}
                        for row in chosen],
            "U": U, "Df": Df, "L": 100.0, "H": cfg.H, "W": cfg.W,
            "dx": cfg.WORKING_GRID_DX,
            "T0_K": cfg.T_AMBIENT_K, "domain_censor_state": "none",
            "protocol_version": set_encoder.direct_inversion.PROTOCOL_VERSION,
            "parameter_target_mask": dict(DOMAIN_PARAMETER_MASKS["none"]),
        }
        clean = decoder.predict(Q, x_f, base_sample)
        xs = [row["x"] for row in chosen]
        noise = _correlated_noise(xs, sigma_C, correlation_length_m, nugget_fraction, rng)
        residual_offset = rng.gauss(0.0, model_residual_sigma_C)
        residual_slope = rng.gauss(0.0, model_residual_sigma_C)
        sensors = []
        for sensor_index, (row, clean_value) in enumerate(zip(chosen, clean)):
            scaled_distance = (row["x"] - x_f) / cfg.H
            residual = residual_offset + residual_slope * scaled_distance / (1.0 + abs(scaled_distance))
            observed = clean_value + residual + noise[sensor_index]
            perturbed_x = min(85.0, max(15.0, row["x"] + rng.gauss(0.0, position_sigma_m)))
            sensors.append({
                "id": row["id"], "dT": observed,
                "x": perturbed_x,
                "nominal_x": row["x"], "clean_dT": clean_value,
                "m": 0.0 if rng.random() < missing_probability else 1.0,
                "sigma_C": sigma_C, "censored": observed <= censor_threshold_C,
            })
        if not any(row["m"] == 1.0 for row in sensors):
            sensors[rng.randrange(len(sensors))]["m"] = 1.0
        samples.append({
            "sample_id": f"synthetic_{index + 1:05d}", "sensors": sensors,
            "U": U, "Df": Df, "L": 100.0, "H": cfg.H, "W": cfg.W,
            "dx": cfg.WORKING_GRID_DX,
            "T0_K": cfg.T_AMBIENT_K, "Q_MW": Q, "x_f": x_f,
            "protocol_version": set_encoder.direct_inversion.PROTOCOL_VERSION,
            "domain_censor_state": "none",
            "parameter_target_mask": dict(DOMAIN_PARAMETER_MASKS["none"]),
            "censor_threshold_C": censor_threshold_C,
            "generator_seed": int(seed), "generator_index": index,
            "decoder_status": decoder.model_status,
            "evidence_label": SYNTHETIC_LABEL,
            "decision_status": NO_SCIENTIFIC_CLAIM,
        })
    return samples


def _normal_logcdf(value):
    value = set_encoder.Value.wrap(value)
    if value.data > -8.0:
        return (0.5 * (1.0 + (value / math.sqrt(2.0)).erf())).log()
    # Mills 比渐近式避免强删失场景的 CDF 浮点下溢；保留对应一阶导数。
    data = -0.5 * value.data ** 2 - math.log(-value.data) - 0.5 * math.log(2.0 * math.pi)
    output = set_encoder.Value(data, (value,), "normal_logcdf_asymptotic")

    def backward():
        value.grad += (-value.data - 1.0 / value.data) * output.grad

    output._backward = backward
    return output


def joint_loss(model, sample, decoder, lambda_source=1.0,
               lambda_reconstruction=1.0, lambda_censor=1.0,
               weight_log_q=1.0, weight_x=1.0):
    weights = [lambda_source, lambda_reconstruction, lambda_censor,
               weight_log_q, weight_x]
    if any(not math.isfinite(float(value)) or value < 0 for value in weights):
        raise ValueError("联合损失权重必须为有限非负数")
    if not any(value > 0 for value in weights[:3]):
        raise ValueError("至少一个联合损失分量权重为正")
    domain_contract = validate_domain_censor_contract(sample)
    output = model.forward_values(sample)
    Q_true = _finite(sample["Q_MW"], "Q_MW")
    x_true = _finite(sample["x_f"], "x_f")
    H = _finite(sample["H"], "H")
    if Q_true <= 0 or H <= 0:
        raise ValueError("监督参数要求 Q/H 为正")
    source = (
        weight_log_q * (output["Q_hat_MW"].log() - math.log(Q_true)) ** 2
        + weight_x * ((output["x_f_hat_m"] - x_true) / H) ** 2
    )
    prediction = decoder(output["Q_hat_MW"], output["x_f_hat_m"], sample)
    if len(prediction) != len(sample["sensors"]):
        raise ValueError("物理解码长度与传感器不一致")
    squared, censored = [], []
    threshold = _finite(sample.get("censor_threshold_C", 0.0), "censor_threshold_C")
    for row, estimate in zip(sample["sensors"], prediction):
        if float(row["m"]) != 1.0:
            continue
        sigma = _finite(row.get("sigma_C", 1.0), "sigma_C")
        if sigma <= 0:
            raise ValueError("重建 sigma_C 必须为正")
        if bool(row.get("censored", False)):
            censored.append(-_normal_logcdf((threshold - estimate) / sigma))
        else:
            observed = _finite(row["dT"], "dT")
            squared.append(((estimate - observed) / sigma) ** 2)
    reconstruction = sum(squared, set_encoder.Value(0.0)) / len(squared) if squared else set_encoder.Value(0.0)
    censor = sum(censored, set_encoder.Value(0.0)) / len(censored) if censored else set_encoder.Value(0.0)
    total = lambda_source * source + lambda_reconstruction * reconstruction + lambda_censor * censor
    return {
        "total": total, "source": source, "reconstruction": reconstruction,
        "censor": censor, "Q_hat_MW": output["Q_hat_MW"],
        "x_f_hat_m": output["x_f_hat_m"], "n_uncensored": len(squared),
        "n_censored": len(censored),
        "domain_censor_state": domain_contract["domain_censor_state"],
        "parameter_target_mask": domain_contract["parameter_target_mask"],
    }


def run_synthetic_software_check(output_dir, n_samples=30):
    output_dir = Path(output_dir)
    decoder = ConstantClosurePhysicsDecoder()
    samples = generate_synthetic_samples(n_samples, decoder=decoder)
    model = set_encoder.SetEncoder()
    model.zero_grad()
    losses = joint_loss(model, samples[0], decoder)
    losses["total"].backward()
    gradients = [parameter.grad for parameter in model.parameters()]
    summary = [{
        "n_samples": len(samples),
        "sensor_counts": ";".join(str(len(sample["sensors"])) for sample in samples),
        "missing_sensor_count": sum(
            row["m"] == 0.0 for sample in samples for row in sample["sensors"]
        ),
        "censored_sensor_count": sum(
            bool(row["censored"]) for sample in samples for row in sample["sensors"]
        ),
        "position_perturbed_count": sum(
            row["x"] != row["nominal_x"] for sample in samples for row in sample["sensors"]
        ),
        "first_total_loss": losses["total"].data,
        "first_source_loss": losses["source"].data,
        "first_reconstruction_loss": losses["reconstruction"].data,
        "first_censor_loss": losses["censor"].data,
        "finite_gradient_count": sum(math.isfinite(value) for value in gradients),
        "nonzero_gradient_count": sum(abs(value) > 1e-14 for value in gradients),
        "status": "PASS" if (
            all(sample["evidence_label"] == SYNTHETIC_LABEL for sample in samples)
            and all(math.isfinite(value) for value in gradients)
            and any(abs(value) > 1e-14 for value in gradients)
        ) else "FAIL",
        "evidence_label": SYNTHETIC_LABEL, "decision_status": NO_SCIENTIFIC_CLAIM,
    }]
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "synthetic_training_samples.jsonl").open("w", encoding="utf-8") as handle:
        for sample in samples:
            handle.write(json.dumps(sample, ensure_ascii=False) + "\n")
    with (output_dir / "synthetic_physics_loss_check.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)
    return {"samples": samples, "summary": summary[0]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path,
        default=PROJECT_ROOT / "derived" / "network" / "physics_training",
    )
    parser.add_argument("--n-samples", type=int, default=30)
    args = parser.parse_args(argv)
    result = run_synthetic_software_check(args.output_dir, args.n_samples)
    print(f"synthetic physics/loss framework: {result['summary']['status']}, n={len(result['samples'])}")
    print(NO_SCIENTIFIC_CLAIM)
    return 0 if result["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
