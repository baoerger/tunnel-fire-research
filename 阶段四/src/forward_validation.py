"""五类正向模型统一评价、数据封存和区间泛化指标。"""
from __future__ import annotations

import argparse
import csv
import math
import statistics
from pathlib import Path


MODEL_FAMILIES = (
    "traditional_empirical", "independent_exponential", "ordinary_regression",
    "direct_symbolic_regression", "transport_kernel_closure",
)
SEALED_SUBSET = "independent_test"


def _finite_vector(values, name):
    result = [float(value) for value in values]
    if not result or any(not math.isfinite(value) for value in result):
        raise ValueError(f"{name} 不能为空或含非有限值")
    return result


def profile_metrics(case, prediction):
    xs = _finite_vector(case["x"], "x")
    observed = _finite_vector(case["dT"], "dT")
    predicted = _finite_vector(prediction, "prediction")
    if len(xs) != len(observed) or len(xs) != len(predicted) or len(set(xs)) != len(xs):
        raise ValueError("正向曲线坐标/观测/预测长度不一致或坐标重复")
    peak = max(observed)
    if peak <= 0:
        raise ValueError("正向评价要求观测峰值温升为正")
    errors = [estimate - truth for estimate, truth in zip(predicted, observed)]
    rmse = math.sqrt(statistics.fmean(value * value for value in errors))
    observed_peak_index = max(range(len(xs)), key=lambda i: observed[i])
    predicted_peak_index = max(range(len(xs)), key=lambda i: predicted[i])
    xp_observed, xp_predicted = xs[observed_peak_index], xs[predicted_peak_index]
    center = float(case.get("x_fire", xp_observed))
    upstream = [errors[i] for i, x in enumerate(xs) if x < center]
    downstream = [errors[i] for i, x in enumerate(xs) if x >= center]

    def branch_nrmse(values):
        return math.sqrt(statistics.fmean(value * value for value in values)) / peak if values else math.nan

    result = {
        "n_points": len(xs), "nrmse": rmse / peak,
        "peak_relative_error": abs(max(predicted) - peak) / peak,
        "peak_position_error_m": abs(xp_predicted - xp_observed),
        "upstream_nrmse": branch_nrmse(upstream),
        "downstream_nrmse": branch_nrmse(downstream),
        "mean_bias_C": statistics.fmean(errors),
    }
    censor_mask = list(case.get("censor_mask", [False] * len(xs)))
    if len(censor_mask) != len(xs):
        raise ValueError("censor_mask 长度不一致")
    threshold = float(case.get("detection_threshold_C", 0.0))
    censored_predictions = [predicted[i] for i, flag in enumerate(censor_mask) if bool(flag)]
    result["strong_wind_censored_exceedance_C"] = (
        max((value - threshold for value in censored_predictions), default=0.0)
    )
    return result


def empirical_peak_metrics(case, predicted_peak_dT_K):
    """评价只给出最大顶棚气体温升的传统经验式，不伪造完整温度剖面。"""
    temperature_type = str(case.get("temperature_type", "ceiling_gas_temperature"))
    if temperature_type != "ceiling_gas_temperature":
        raise ValueError("传统最大温升式只可与 ceiling_gas_temperature 比较")
    observed = _finite_vector(case["dT"], "dT")
    observed_peak = max(observed)
    predicted_peak = float(predicted_peak_dT_K)
    if observed_peak <= 0 or not math.isfinite(predicted_peak) or predicted_peak <= 0:
        raise ValueError("经验式峰值评价要求观测与预测最大温升均为有限正数")
    return {
        "observed_peak_dT_K": observed_peak,
        "predicted_peak_dT_K": predicted_peak,
        "peak_relative_error": abs(predicted_peak - observed_peak) / observed_peak,
    }


def evaluate_empirical_peak_baselines(cases, predictions, final_evaluation=False):
    """按公式 ID 评价峰值基线；独立测试仍沿用统一封存纪律。"""
    case_ids = [str(case.get("case_id") or "").strip() for case in cases]
    if not cases or any(not case_id for case_id in case_ids) or len(set(case_ids)) != len(case_ids):
        raise ValueError("经验式评价要求唯一完整 case_id")
    if any(case.get("subset") == SEALED_SUBSET for case in cases) and not final_evaluation:
        raise PermissionError("独立测试仍封存；只有模型设置冻结后的最终评价可访问")
    if not predictions or any(not str(formula_id).strip() for formula_id in predictions):
        raise ValueError("至少需要一个非空经验公式 ID")
    rows = []
    for formula_id, formula_predictions in predictions.items():
        if set(formula_predictions) != set(case_ids):
            raise ValueError(f"{formula_id} 峰值预测 case_id 不完整")
        for case in cases:
            rows.append({
                "formula_id": formula_id,
                "case_id": case["case_id"],
                "subset": case.get("subset", "development"),
                "prediction_scope": "maximum_ceiling_gas_temperature_only",
                **empirical_peak_metrics(case, formula_predictions[case["case_id"]]),
            })
    return rows


def symmetry_error(xs, prediction, center, tolerance=1e-9):
    pairs = {round(float(x) - center, 10): float(y) for x, y in zip(xs, prediction)}
    differences = []
    for distance, value in pairs.items():
        if distance > tolerance and -distance in pairs:
            scale = max(abs(value), abs(pairs[-distance]), 1e-12)
            differences.append(abs(value - pairs[-distance]) / scale)
    if not differences:
        raise ValueError("无风对称检查缺少成对坐标")
    return max(differences)


def evaluate_models(cases, predictions, final_evaluation=False):
    if tuple(predictions) != MODEL_FAMILIES:
        raise ValueError("必须按固定顺序注册五类正向模型")
    case_ids = [str(case.get("case_id") or "").strip() for case in cases]
    if not cases or any(not case_id for case_id in case_ids) or len(set(case_ids)) != len(case_ids):
        raise ValueError("正向评价要求唯一完整 case_id")
    if any(case.get("subset") == SEALED_SUBSET for case in cases) and not final_evaluation:
        raise PermissionError("独立测试仍封存；只有模型设置冻结后的最终评价可访问")
    rows = []
    for family in MODEL_FAMILIES:
        family_predictions = predictions[family]
        if set(family_predictions) != set(case_ids):
            raise ValueError(f"{family} 预测 case_id 不完整")
        for case in cases:
            metrics = profile_metrics(case, family_predictions[case["case_id"]])
            row = {
                "model_family": family, "case_id": case["case_id"],
                "subset": case.get("subset", "development"),
                "near_critical": bool(case.get("near_critical", False)),
                "strong_wind": bool(case.get("strong_wind", False)),
                "holdout_power": bool(case.get("holdout_power", False)),
                "holdout_wind": bool(case.get("holdout_wind", False)),
                "holdout_size": bool(case.get("holdout_size", False)),
                "holdout_location": bool(case.get("holdout_location", False)),
                **metrics,
            }
            if float(case.get("U", 1.0)) == 0.0:
                row["zero_wind_symmetry_error"] = symmetry_error(
                    case["x"], family_predictions[case["case_id"]], float(case["x_fire"])
                )
            else:
                row["zero_wind_symmetry_error"] = math.nan
            far_indices = sorted(range(len(case["x"])), key=lambda i: abs(float(case["x"][i]) - float(case["x_fire"])), reverse=True)[:2]
            row["far_field_max_dT_C"] = max(float(family_predictions[case["case_id"]][i]) for i in far_indices)
            rows.append(row)
    return rows


def summarize(rows):
    summaries = []
    for family in MODEL_FAMILIES:
        selected = [row for row in rows if row["model_family"] == family]
        if not selected:
            raise ValueError(f"缺少 {family} 评价结果")
        summary = {"model_family": family, "n_cases": len(selected)}
        for metric in ("nrmse", "peak_relative_error", "peak_position_error_m",
                       "upstream_nrmse", "downstream_nrmse"):
            values = [float(row[metric]) for row in selected if math.isfinite(float(row[metric]))]
            summary[f"{metric}_median"] = statistics.median(values)
        for flag in ("near_critical", "strong_wind", "holdout_power", "holdout_wind",
                     "holdout_size", "holdout_location"):
            values = [row["nrmse"] for row in selected if row[flag]]
            summary[f"{flag}_nrmse"] = statistics.fmean(values) if values else math.nan
        summaries.append(summary)
    return summaries


def run_synthetic_check(output_dir):
    xs = [-10.0, -5.0, 0.0, 5.0, 10.0]
    cases = [
        {"case_id": "syn_zero", "x": xs, "dT": [5, 20, 50, 20, 5], "x_fire": 0,
         "U": 0, "subset": "synthetic_software", "near_critical": False},
        {"case_id": "syn_critical", "x": xs, "dT": [2, 12, 45, 28, 9], "x_fire": 0,
         "U": 2, "subset": "synthetic_software", "near_critical": True, "holdout_wind": True},
        {"case_id": "syn_strong", "x": xs, "dT": [0.5, 1, 30, 22, 10], "x_fire": 0,
         "U": 4, "subset": "synthetic_software", "strong_wind": True,
         "censor_mask": [True, True, False, False, False], "detection_threshold_C": 2.0,
         "holdout_power": True, "holdout_size": True, "holdout_location": True},
    ]
    predictions = {}
    for model_index, family in enumerate(MODEL_FAMILIES):
        factor = 1.0 + 0.01 * model_index
        predictions[family] = {
            case["case_id"]: [value * factor for value in case["dT"]] for case in cases
        }
    rows = evaluate_models(cases, predictions)
    summaries = summarize(rows)
    output_dir = Path(output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    for row in rows + summaries:
        row["data_type"] = "SYNTHETIC_SOFTWARE_TEST_NOT_SCIENTIFIC_EVIDENCE"
        row["decision_status"] = "NO_FORMAL_MODEL_SELECTION"
    with (output_dir / "synthetic_forward_metrics.csv").open("w", encoding="utf-8", newline="") as handle:
        fields = sorted({key for row in rows for key in row})
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    with (output_dir / "synthetic_forward_summary.csv").open("w", encoding="utf-8", newline="") as handle:
        fields = sorted({key for row in summaries for key in row})
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(summaries)
    return rows, summaries


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        default=Path(__file__).resolve().parent.parent / "03_正向验证与全场解释")
    args = parser.parse_args(argv)
    rows, summaries = run_synthetic_check(args.output_dir)
    print(f"synthetic forward evaluation: {len(rows)} rows, {len(summaries)} models")
    print("NO_FORMAL_MODEL_SELECTION")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
