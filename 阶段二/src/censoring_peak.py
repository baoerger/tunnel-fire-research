"""强风删失、连续峰值与 Bootstrap 软件工具。

阈值和最终规则必须由真实先导/随机种子结果确定；默认值仅用于软件测试。
"""
from __future__ import annotations

import argparse
import csv
import math
import random
import statistics
from pathlib import Path

import transport_model


STAGE2_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SYNTHETIC_OUT = (
    STAGE2_ROOT / "03_删失与峰值" / "synthetic_censor_peak_check.csv"
)
SYNTHETIC_FIELDS = ("data_type", "check_id", "expected", "actual", "numeric_value", "status")


def _finite(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 不是有效数值: {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} 必须为有限数值")
    return number


def classify_upstream_observation(xs, dT, xp, threshold, H=5.0,
                                  near_exclusion_H=0.4, min_side_points=3,
                                  backflow_length_m=None):
    """候选三分类；下游不足时显式返回 insufficient_downstream。"""
    pairs = transport_model._validated_profile(xs, dT)
    xp = _finite(xp, "xp")
    threshold = _finite(threshold, "threshold")
    H = _finite(H, "H")
    near_exclusion_H = _finite(near_exclusion_H, "near_exclusion_H")
    if isinstance(min_side_points, bool):
        raise ValueError("min_side_points 必须为正整数")
    try:
        parsed_min_side_points = int(min_side_points)
    except (TypeError, ValueError) as exc:
        raise ValueError("min_side_points 必须为正整数") from exc
    if parsed_min_side_points != float(min_side_points):
        raise ValueError("min_side_points 必须为正整数")
    min_side_points = parsed_min_side_points
    if threshold <= 0 or H <= 0 or min_side_points < 1:
        raise ValueError("threshold/H/min_side_points 必须为正")
    if near_exclusion_H < 0:
        raise ValueError("near_exclusion_H 不得为负")
    if backflow_length_m is not None:
        backflow_length_m = _finite(backflow_length_m, "backflow_length_m")
        if backflow_length_m < 0:
            raise ValueError("backflow_length_m 不得为负")
    near_m = near_exclusion_H * H
    upstream = [(x, value) for x, value in pairs if x < xp - near_m]
    downstream = [(x, value) for x, value in pairs if x > xp + near_m]
    upstream_detected = [(x, value) for x, value in upstream if value >= threshold]
    downstream_detected = [(x, value) for x, value in downstream if value >= threshold]
    if len(downstream_detected) < min_side_points:
        category = "insufficient_downstream"
    elif len(upstream_detected) >= min_side_points:
        category = "bilateral_identifiable"
    elif not upstream_detected and backflow_length_m is not None and backflow_length_m <= 0:
        category = "no_obvious_backflow"
    else:
        category = "upstream_censored"
    return {
        "category": category,
        "n_upstream_available": len(upstream),
        "n_upstream_detected": len(upstream_detected),
        "n_downstream_available": len(downstream),
        "n_downstream_detected": len(downstream_detected),
        "upstream_temperature_upper_C": max((value for _, value in upstream), default=math.nan),
        "threshold_C": threshold,
        "backflow_length_m": backflow_length_m,
    }


def _normal_cdf(z):
    return 0.5 * math.erfc(-z / math.sqrt(2.0))


def censored_gaussian_nll(observed, predicted, sigma, threshold, censor_mask=None,
                          reduction="mean"):
    """左删失高斯负对数似然；只对 mask 且观测低于阈值的点积分。"""
    if len(observed) != len(predicted):
        raise ValueError("observed/predicted 长度不一致")
    if not observed:
        raise ValueError("观测为空")
    sigma = _finite(sigma, "sigma")
    threshold = _finite(threshold, "threshold")
    if sigma <= 0:
        raise ValueError("sigma 必须为正")
    if censor_mask is None:
        censor_mask = [True] * len(observed)
    if len(censor_mask) != len(observed):
        raise ValueError("censor_mask 长度不一致")
    terms = []
    n_censored = 0
    for y, mu, allow_censor in zip(observed, predicted, censor_mask):
        y = _finite(y, "observed")
        mu = _finite(mu, "predicted")
        if allow_censor and y < threshold:
            probability = max(_normal_cdf((threshold - mu) / sigma), 1e-300)
            terms.append(-math.log(probability))
            n_censored += 1
        else:
            z = (y - mu) / sigma
            terms.append(0.5 * z * z + math.log(sigma) + 0.5 * math.log(2.0 * math.pi))
    if reduction == "sum":
        value = sum(terms)
    elif reduction == "mean":
        value = statistics.fmean(terms)
    else:
        raise ValueError("reduction 只能为 mean 或 sum")
    return {"loss": value, "n_censored": n_censored, "n_observed": len(terms) - n_censored}


def _quadratic_peak(pairs, n_points, grid_step, platform_fraction):
    if n_points < 3 or n_points % 2 == 0 or n_points > len(pairs):
        raise ValueError("quadratic_points 必须为不超过测点数的奇数且至少 3")
    peak_index = max(range(len(pairs)), key=lambda index: pairs[index][1])
    half = n_points // 2
    start = max(0, min(peak_index - half, len(pairs) - n_points))
    selected = pairs[start:start + n_points]
    matrix = [[0.0] * 3 for _ in range(3)]
    vector = [0.0] * 3
    x_center = selected[n_points // 2][0]
    for x, y in selected:
        t = x - x_center
        features = [t * t, t, 1.0]
        for i in range(3):
            vector[i] += features[i] * y
            for j in range(3):
                matrix[i][j] += features[i] * features[j]
    a, b, c = transport_model._solve_3x3(matrix, vector)
    if a >= 0:
        raise ValueError("峰值邻域二次拟合不是开口向下")
    xp = x_center - b / (2.0 * a)
    if not selected[0][0] <= xp <= selected[-1][0]:
        raise ValueError("二次拟合峰值落在局部窗口外")
    peak = a * (xp - x_center) ** 2 + b * (xp - x_center) + c
    lo, hi = selected[0][0], selected[-1][0]
    grid = []
    count = int(math.ceil((hi - lo) / grid_step))
    for index in range(count + 1):
        x = min(hi, lo + index * grid_step)
        t = x - x_center
        grid.append((x, a * t * t + b * t + c))
    return xp, peak, grid


def _kernel_peak(pairs, bandwidth, grid_step):
    if bandwidth <= 0:
        raise ValueError("bandwidth 必须为正")
    lo, hi = pairs[0][0], pairs[-1][0]
    count = int(math.ceil((hi - lo) / grid_step))
    grid = []
    for index in range(count + 1):
        x = min(hi, lo + index * grid_step)
        weights = [math.exp(-0.5 * ((x - xi) / bandwidth) ** 2) for xi, _ in pairs]
        total = sum(weights)
        value = sum(weight * pair[1] for weight, pair in zip(weights, pairs)) / total
        grid.append((x, value))
    peak_index = max(range(len(grid)), key=lambda index: grid[index][1])
    if peak_index in (0, len(grid) - 1):
        raise ValueError("核平滑峰值位于测量区边界")
    return grid[peak_index][0], grid[peak_index][1], grid


def estimate_peak(xs, dT, method="quadratic", quadratic_points=5,
                  bandwidth=None, grid_step=None, platform_fraction=0.95):
    pairs = transport_model._validated_profile(xs, dT)
    spacings = [right[0] - left[0] for left, right in zip(pairs, pairs[1:])]
    step = _finite(grid_step, "grid_step") if grid_step is not None else min(spacings) / 20.0
    if step <= 0 or not 0 < platform_fraction < 1:
        raise ValueError("grid_step 必须为正且 platform_fraction 位于 (0,1)")
    if method == "quadratic":
        xp, peak, grid = _quadratic_peak(pairs, quadratic_points, step, platform_fraction)
    elif method == "kernel":
        bw = _finite(bandwidth, "bandwidth") if bandwidth is not None else statistics.median(spacings) * 1.5
        xp, peak, grid = _kernel_peak(pairs, bw, step)
    else:
        raise ValueError("method 只能为 quadratic 或 kernel")
    threshold = platform_fraction * peak
    platform = [x for x, value in grid if value >= threshold]
    width = max(platform) - min(platform) if platform else 0.0
    return {
        "method": method, "xp": xp, "delta_Tp": peak,
        "platform_fraction": platform_fraction, "platform_width_m": width,
        "curve": grid,
    }


def _quantile(values, probability):
    ordered = sorted(values)
    if not ordered:
        raise ValueError("分位数输入为空")
    position = probability * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def summarize_peak_bootstrap(estimates):
    if not estimates:
        raise ValueError("Bootstrap 没有成功样本")
    xps = [row["xp"] for row in estimates]
    peaks = [row["delta_Tp"] for row in estimates]
    widths = [row["platform_width_m"] for row in estimates]
    return {
        "n_success": len(estimates), "xp_median": statistics.median(xps),
        "xp_ci_low": _quantile(xps, 0.025), "xp_ci_high": _quantile(xps, 0.975),
        "xp_ci_width": _quantile(xps, 0.975) - _quantile(xps, 0.025),
        "delta_Tp_median": statistics.median(peaks),
        "platform_width_median": statistics.median(widths),
    }


def _check_time_profiles(times, profiles, sensor_xs):
    if len(times) != len(profiles) or len(times) < 10:
        raise ValueError("时间与剖面行数必须一致且至少 10 帧")
    times = [_finite(value, "time") for value in times]
    if any(right <= left for left, right in zip(times, times[1:])):
        raise ValueError("时间必须严格递增")
    gaps = [right - left for left, right in zip(times, times[1:])]
    if max(gaps) - min(gaps) > max(1e-8, statistics.median(gaps) * 0.01):
        raise ValueError("时间间隔不规则")
    if len(sensor_xs) < 7 or any(len(row) != len(sensor_xs) for row in profiles):
        raise ValueError("每帧温度列必须与至少 7 个传感器一致")
    checked = [[_finite(value, "temperature") for value in row] for row in profiles]
    return times, checked


def _autocorrelation_block_length(series):
    mean = statistics.fmean(series)
    centered = [value - mean for value in series]
    denominator = sum(value * value for value in centered)
    if denominator <= 1e-12:
        return 1
    positive_sum = 0.0
    for lag in range(1, min(len(series) // 2, 100)):
        rho = sum(centered[i] * centered[i + lag] for i in range(len(series) - lag)) / denominator
        if rho <= 0:
            break
        positive_sum += rho
    return max(1, min(int(math.ceil(1.0 + 2.0 * positive_sum)), len(series) // 3))


def time_block_peak_bootstrap(times, profiles, sensor_xs, n_boot=200, seed=20260729,
                              block_len=None, method="quadratic"):
    times, profiles = _check_time_profiles(times, profiles, sensor_xs)
    if n_boot < 20:
        raise ValueError("n_boot 至少为 20")
    peak_series = [max(row) for row in profiles]
    block_len = int(block_len or _autocorrelation_block_length(peak_series))
    if not 1 <= block_len <= len(profiles) // 2:
        raise ValueError("block_len 超出有效范围")
    rng = random.Random(seed)
    estimates = []
    failures = 0
    n = len(profiles)
    for _ in range(n_boot):
        indices = []
        while len(indices) < n:
            start = rng.randrange(n)
            indices.extend((start + offset) % n for offset in range(block_len))
        indices = indices[:n]
        averaged = [
            statistics.fmean(profiles[index][sensor] for index in indices)
            for sensor in range(len(sensor_xs))
        ]
        try:
            estimates.append(estimate_peak(sensor_xs, averaged, method=method))
        except ValueError:
            failures += 1
    if len(estimates) < 0.8 * n_boot:
        raise ValueError(f"时间 Bootstrap 峰值拟合成功率不足 80%: {len(estimates)}/{n_boot}")
    summary = summarize_peak_bootstrap(estimates)
    summary.update({"n_requested": n_boot, "n_failed": failures, "block_len_samples": block_len, "seed": seed})
    return summary, estimates


def sensor_perturbation_bootstrap(xs, dT, n_boot=200, seed=20260729,
                                  drop_probability=0.15, position_sigma_m=0.05,
                                  temperature_sigma_C=0.5, method="quadratic"):
    pairs = transport_model._validated_profile(xs, dT)
    if n_boot < 20 or not 0 <= drop_probability < 0.5:
        raise ValueError("n_boot 至少 20 且 drop_probability 位于 [0,0.5)")
    if position_sigma_m < 0 or temperature_sigma_C < 0:
        raise ValueError("扰动标准差不得为负")
    rng = random.Random(seed)
    estimates = []
    failures = 0
    for _ in range(n_boot):
        perturbed = []
        for x, value in pairs:
            if rng.random() < drop_probability:
                continue
            perturbed.append((
                x + rng.gauss(0.0, position_sigma_m),
                value + rng.gauss(0.0, temperature_sigma_C),
            ))
        perturbed.sort()
        if len(perturbed) < 7:
            failures += 1
            continue
        try:
            estimates.append(estimate_peak(
                [row[0] for row in perturbed], [row[1] for row in perturbed],
                method=method,
            ))
        except ValueError:
            failures += 1
    if len(estimates) < 0.8 * n_boot:
        raise ValueError(f"测点 Bootstrap 峰值拟合成功率不足 80%: {len(estimates)}/{n_boot}")
    summary = summarize_peak_bootstrap(estimates)
    summary.update({"n_requested": n_boot, "n_failed": failures, "seed": seed})
    return summary, estimates


FALSIFICATION_FIELDS = (
    "log_linear_ok", "interval_stable", "near_residual_ok",
    "critical_structure_ok", "censoring_stable", "source_model_ok",
)


def _gate_boolean(value):
    """解析内存布尔值或 CSV 布尔文本；未知值按不完整处理。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "pass"}:
            return True
        if normalized in {"false", "0", "no", "fail"}:
            return False
    return None


def summarize_falsification(case_rows, minimum_fraction=0.75):
    """汇总五项判伪和源模型门；缺字段不得默认为通过。"""
    minimum_fraction = _finite(minimum_fraction, "minimum_fraction")
    if not 0 <= minimum_fraction <= 1:
        raise ValueError("minimum_fraction 必须位于 [0,1]")
    if not case_rows:
        return {"status": "WAITING", "pass_fraction": math.nan, "n_cases": 0,
                "reason": "没有真实先导结果"}
    results = []
    for row in case_rows:
        parsed = [
            _gate_boolean(row.get(field))
            for field in FALSIFICATION_FIELDS
        ]
        results.append(all(value is True for value in parsed))
    fraction = sum(results) / len(results)
    return {
        "status": "PASS" if fraction >= minimum_fraction else "FAIL",
        "pass_fraction": fraction, "n_cases": len(results),
        "n_pass": sum(results), "n_incomplete_or_fail": len(results) - sum(results),
        "reason": f"{sum(results)}/{len(results)} 工况通过全部五项和源模型门",
    }


def run_synthetic_check(out_path=DEFAULT_SYNTHETIC_OUT):
    xs = [35.0 + index for index in range(31)]
    truth_xp = 50.35
    dT = [160.0 - 2.2 * (x - truth_xp) ** 2 for x in xs]
    quadratic = estimate_peak(xs, dT, method="quadratic")
    kernel = estimate_peak(xs, dT, method="kernel", bandwidth=1.2)
    bilateral = classify_upstream_observation(xs, [max(10.0, value) for value in dT], 50, 5)
    censored = classify_upstream_observation(
        xs, [1.0 if x < 48 else max(10.0, value) for x, value in zip(xs, dT)],
        50, 5, backflow_length_m=2.0,
    )
    no_backflow = classify_upstream_observation(
        xs, [1.0 if x < 48 else max(10.0, value) for x, value in zip(xs, dT)],
        50, 5, backflow_length_m=0.0,
    )
    low_loss = censored_gaussian_nll([1.0], [2.0], 1.0, 5.0)["loss"]
    high_loss = censored_gaussian_nll([1.0], [10.0], 1.0, 5.0)["loss"]
    checks = (
        ("class_bilateral", "bilateral_identifiable", bilateral["category"], math.nan),
        ("class_censored", "upstream_censored", censored["category"], math.nan),
        ("class_no_backflow", "no_obvious_backflow", no_backflow["category"], math.nan),
        ("quadratic_peak_error_m", "<=0.05", "numeric", abs(quadratic["xp"] - truth_xp)),
        ("kernel_peak_error_m", "<=0.5", "numeric", abs(kernel["xp"] - truth_xp)),
        ("tobit_prefers_below_threshold", "true", str(low_loss < high_loss).lower(), high_loss - low_loss),
    )
    rows = []
    for check_id, expected, actual, numeric in checks:
        if check_id == "quadratic_peak_error_m":
            passed = numeric <= 0.05
        elif check_id == "kernel_peak_error_m":
            passed = numeric <= 0.5
        elif check_id == "tobit_prefers_below_threshold":
            passed = actual == "true"
        else:
            passed = actual == expected
        rows.append({
            "data_type": "SYNTHETIC_SOFTWARE_TEST_NOT_SCIENTIFIC_EVIDENCE",
            "check_id": check_id, "expected": expected, "actual": actual,
            "numeric_value": "" if math.isnan(numeric) else f"{numeric:.8f}",
            "status": "PASS" if passed else "FAIL",
        })
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=SYNTHETIC_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    if any(row["status"] != "PASS" for row in rows):
        raise ValueError("合成删失/峰值软件检查未全部通过")
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic-check-out", default=str(DEFAULT_SYNTHETIC_OUT))
    args = parser.parse_args()
    rows = run_synthetic_check(args.synthetic_check_out)
    print(f"[OK] {len(rows)} 项合成删失/峰值检查 -> {args.synthetic_check_out}")
    print("注意：SYNTHETIC_SOFTWARE_TEST，不是检测阈值或峰值稳定性结论。")


if __name__ == "__main__":
    main()
