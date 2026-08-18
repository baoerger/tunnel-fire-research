"""三层不确定性汇总、空间重建残差和开发分布异常提示。"""
from __future__ import annotations

import argparse
import csv
import math
import random
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC_LABEL = "SYNTHETIC_SOFTWARE_TEST_NOT_SCIENTIFIC_EVIDENCE"
NO_DIAGNOSIS = "MODEL_MISMATCH_REVIEW_ONLY_NO_SPECIFIC_ANOMALY_DIAGNOSIS"
LAYERS = ("forward_formula", "direct_inversion", "network")
PROTOCOL_VERSION = "100M_CONDITIONAL_DOMAIN_V2"


def assess_100m_applicability(metadata):
    """给出核心域/域外探索/信息不完整标志。"""
    checks = {
        "L": (100.0, 1e-9), "W": (10.0, 1e-9), "H": (5.0, 1e-9),
        "dx": (0.25, 1e-9),
    }
    missing_reasons, ood_reasons = [], []
    for name, (target, tolerance) in checks.items():
        if name not in metadata or str(metadata.get(name, "")).strip() == "":
            missing_reasons.append(f"{name}_MISSING")
            continue
        try:
            value = float(metadata[name])
        except (TypeError, ValueError):
            missing_reasons.append(f"{name}_INVALID")
            continue
        if not math.isfinite(value) or not math.isclose(value, target, abs_tol=tolerance):
            ood_reasons.append(f"{name}_OUT_OF_CORE_DOMAIN")
    protocol = str(metadata.get("protocol_version") or "").strip()
    if not protocol:
        missing_reasons.append("PROTOCOL_VERSION_MISSING")
    elif protocol != PROTOCOL_VERSION:
        ood_reasons.append("PROTOCOL_VERSION_DIFFERENT")
    xs = metadata.get("sensor_xs")
    if xs is None:
        missing_reasons.append("SENSOR_X_MISSING")
    else:
        try:
            values = [float(value) for value in xs]
        except (TypeError, ValueError):
            missing_reasons.append("SENSOR_X_INVALID")
        else:
            if not values or any(not math.isfinite(value) or value < 15.0 or value > 85.0
                                 for value in values):
                ood_reasons.append("SENSOR_X_OUTSIDE_CORE_15_85")
    state = str(metadata.get("domain_censor_state") or "").strip()
    allowed = {
        "none", "bilateral_identifiable", "upstream_censored",
        "downstream_domain_censored", "no_obvious_backflow",
    }
    if not state:
        missing_reasons.append("DOMAIN_CENSOR_STATE_MISSING")
    elif state not in allowed:
        missing_reasons.append("DOMAIN_CENSOR_STATE_UNRECOGNIZED")
    if missing_reasons:
        status = "APPLICABILITY_UNDETERMINED"
        scope = "EXPLORATORY_METADATA_INCOMPLETE"
    elif ood_reasons:
        status = "OOD_EXPLORATORY"
        scope = "EXTERNAL_OR_OOD_GENERALIZATION_NO_CORE_DOMAIN_VALIDATION_CLAIM"
    else:
        status = "IN_DOMAIN_100M_CONDITIONAL"
        scope = "CORE_DOMAIN_RESULT_PENDING_REMAINING_SCIENTIFIC_GATES"
    reasons = missing_reasons + ood_reasons
    return {
        "protocol_version": protocol,
        "applicability_status": status,
        "ood_reasons": ";".join(reasons),
        "evidence_scope": scope,
        "domain_censor_state": state,
        "parameter_scope": (
            "NO_K_D" if state == "downstream_domain_censored" else
            "NO_K_U" if state in {"upstream_censored", "no_obvious_backflow"} else "BILATERAL_REVIEW"
        ),
    }


def uncertainty_summary(components):
    """保留三层来源，并仅条件性给出独立假设下 RSS。"""
    rows = []
    for parameter in ("Q_MW", "x_f_m"):
        variances = []
        for layer in LAYERS:
            item = components.get(layer, {}).get(parameter)
            if not item:
                raise ValueError(f"缺少 {layer}/{parameter} 不确定性")
            value = float(item["standard_uncertainty"])
            if not math.isfinite(value) or value < 0 or not str(item.get("basis") or "").strip():
                raise ValueError("不确定性必须非负有限且有依据")
            variances.append(value * value)
            rows.append({
                "parameter": parameter, "layer": layer,
                "standard_uncertainty": value, "basis": item["basis"],
                "source": item.get("source", ""), "combination_status": "NOT_COMBINED_DEPENDENCE_UNKNOWN",
            })
        rows.append({
            "parameter": parameter, "layer": "conditional_rss",
            "standard_uncertainty": math.sqrt(sum(variances)),
            "basis": "RSS_ONLY_IF_COMPONENTS_ARE_INDEPENDENT",
            "source": "", "combination_status": "CONDITIONAL_NOT_FORMAL_TOTAL",
        })
    return rows


def residual_profile(sample, predicted):
    if len(predicted) != len(sample["sensors"]):
        raise ValueError("预测与传感器长度不一致")
    rows = []
    for sensor, estimate in zip(sample["sensors"], predicted):
        if float(sensor.get("m", 1.0)) != 1.0:
            continue
        observed, estimate = float(sensor["dT"]), float(estimate)
        if not math.isfinite(observed) or not math.isfinite(estimate):
            raise ValueError("残差输入必须有限")
        rows.append({
            "case_id": sample["case_id"], "sensor_id": sensor.get("id", ""),
            "x_m": float(sensor["x"]), "observed_dT_C": observed,
            "predicted_dT_C": estimate, "residual_C": observed - estimate,
        })
    if not rows:
        raise ValueError("没有有效残差测点")
    return sorted(rows, key=lambda row: row["x_m"])


def residual_score(profile):
    residuals = [float(row["residual_C"]) for row in profile]
    return {
        "rmse_C": math.sqrt(sum(value * value for value in residuals) / len(residuals)),
        "max_abs_C": max(abs(value) for value in residuals),
        "mean_bias_C": sum(residuals) / len(residuals), "n_sensors": len(residuals),
    }


def _quantile(values, probability):
    ordered = sorted(float(value) for value in values)
    position = probability * (len(ordered) - 1)
    lower, upper = math.floor(position), math.ceil(position)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def fit_anomaly_threshold(development_scores, quantile=0.95, min_cases=10):
    if not 0 < quantile < 1 or len(development_scores) < min_cases:
        raise ValueError("开发残差数量不足或分位数无效")
    case_ids = [str(row.get("case_id") or "") for row in development_scores]
    if len(set(case_ids)) != len(case_ids) or "" in case_ids:
        raise ValueError("开发残差必须按唯一完整 case_id 提供")
    if any(row.get("subset") != "development" for row in development_scores):
        raise PermissionError("异常阈值只能由开发工况拟合")
    values = [float(row["rmse_C"]) for row in development_scores]
    if any(not math.isfinite(value) or value < 0 for value in values):
        raise ValueError("开发残差分数无效")
    return {
        "metric": "rmse_C", "quantile": quantile,
        "threshold": _quantile(values, quantile), "n_development_cases": len(values),
        "status": "DEVELOPMENT_THRESHOLD_NOT_DIAGNOSIS",
    }


def flag_model_mismatch(case_id, score, threshold, applicability=None):
    value = float(score[threshold["metric"]])
    flagged = value > float(threshold["threshold"])
    result = {
        "case_id": case_id, "metric": threshold["metric"], "score": value,
        "threshold": threshold["threshold"], "flagged": flagged,
        "message": "MODEL_MISMATCH_REVIEW" if flagged else "WITHIN_DEVELOPMENT_RESIDUAL_RANGE",
        "diagnosis": NO_DIAGNOSIS,
    }
    if applicability is not None:
        assessed = assess_100m_applicability(applicability)
        result.update(assessed)
        if assessed["applicability_status"] == "OOD_EXPLORATORY":
            result["message"] = (
                "EXPLORATORY_OOD_MODEL_MISMATCH_REVIEW" if flagged
                else "EXPLORATORY_OOD_WITHIN_REFERENCE_RESIDUAL_RANGE"
            )
        elif assessed["applicability_status"] == "APPLICABILITY_UNDETERMINED":
            result["message"] = "EXPLORATORY_APPLICABILITY_UNDETERMINED"
    return result


def write_residual_svg(profile, path):
    xs = [row["x_m"] for row in profile]
    ys = [row["residual_C"] for row in profile]
    x_min, x_max = min(xs), max(xs)
    limit = max(max(abs(value) for value in ys), 1e-9)
    points = " ".join(
        f"{60 + 500 * (x-x_min)/max(x_max-x_min,1e-9):.2f},{150 - 110*y/limit:.2f}"
        for x, y in zip(xs, ys)
    )
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="620" height="300">
<rect width="620" height="300" fill="white"/><line x1="60" x2="560" y1="150" y2="150" stroke="#777"/>
<polyline points="{points}" fill="none" stroke="#d62728" stroke-width="2"/>
<text x="310" y="25" text-anchor="middle" font-family="sans-serif">Synthetic spatial residual — mismatch review only</text>
<text x="310" y="285" text-anchor="middle" font-family="sans-serif">x (m)</text></svg>'''
    Path(path).write_text(svg, encoding="utf-8")


def run_synthetic_software_check(output_dir):
    output_dir = Path(output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    components = {
        layer: {
            "Q_MW": {"standard_uncertainty": value, "basis": "synthetic_check", "source": layer},
            "x_f_m": {"standard_uncertainty": value / 5, "basis": "synthetic_check", "source": layer},
        } for layer, value in zip(LAYERS, (2.0, 3.0, 4.0))
    }
    uncertainty = uncertainty_summary(components)
    for row in uncertainty:
        row.update({"evidence_label": SYNTHETIC_LABEL, "decision_status": NO_DIAGNOSIS})
    with (output_dir / "synthetic_uncertainty_summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(uncertainty[0])); writer.writeheader(); writer.writerows(uncertainty)
    rng = random.Random(20260729)
    development = [{"case_id": f"dev_{i:03d}", "subset": "development", "rmse_C": abs(rng.gauss(2, .4))}
                   for i in range(20)]
    threshold = fit_anomaly_threshold(development)
    sample = {"case_id": "synthetic_probe", "sensors": [
        {"id": f"S{i}", "x": float(i), "dT": 10 + i, "m": 1.0} for i in range(8)
    ]}
    profile = residual_profile(sample, [10 + i + (6 if i in (3, 4) else 0) for i in range(8)])
    score = residual_score(profile); flag = flag_model_mismatch(
        sample["case_id"], score, threshold,
        {"L": 100, "W": 10, "H": 5, "dx": 0.25,
         "protocol_version": PROTOCOL_VERSION,
         "sensor_xs": [15 + 10 * i for i in range(8)],
         "domain_censor_state": "none"},
    )
    flag.update({"evidence_label": SYNTHETIC_LABEL, "decision_status": NO_DIAGNOSIS})
    with (output_dir / "synthetic_anomaly_check.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(flag)); writer.writeheader(); writer.writerow(flag)
    write_residual_svg(profile, output_dir / "synthetic_spatial_residual.svg")
    return uncertainty, threshold, flag


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path,
        default=PROJECT_ROOT / "derived" / "network" / "uncertainty_anomaly",
    )
    args = parser.parse_args(argv)
    _, threshold, flag = run_synthetic_software_check(args.output_dir)
    print(f"synthetic threshold={threshold['threshold']:.4g}, flagged={flag['flagged']}")
    print(NO_DIAGNOSIS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
