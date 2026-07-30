"""阶段二有效输运温度核、拟合和软件验证工具。

本模块可用合成数据验证算法，但任何合成输出都不能作为点源/有限源取舍、
近场范围或真实参数稳定性的科学证据。
"""
from __future__ import annotations

import argparse
import csv
import math
import statistics
from pathlib import Path


STAGE2_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SYNTHETIC_OUT = (
    STAGE2_ROOT / "02_解析结构与参数提取" / "synthetic_software_check.csv"
)
SYNTHETIC_FIELDS = (
    "data_type", "case_id", "true_delta_Tp_C", "fit_delta_Tp_C",
    "true_xp_m", "fit_xp_m", "true_kappa_u", "fit_kappa_u",
    "true_kappa_d", "fit_kappa_d", "profile_nrmse", "near_exclusion_H",
)


def _finite(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 不是有效数值: {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} 必须为有限数值")
    return number


def point_temperature(x, delta_Tp, xp, k_u, k_d):
    """分段指数点源温升；k_u/k_d 单位为 1/m。"""
    x = _finite(x, "x")
    delta_Tp = _finite(delta_Tp, "delta_Tp")
    xp = _finite(xp, "xp")
    k_u = _finite(k_u, "k_u")
    k_d = _finite(k_d, "k_d")
    if delta_Tp <= 0 or k_u <= 0 or k_d <= 0:
        raise ValueError("delta_Tp、k_u、k_d 必须为正")
    if x < xp:
        return delta_Tp * math.exp(k_u * (x - xp))
    return delta_Tp * math.exp(-k_d * (x - xp))


def point_profile(xs, delta_Tp, xp, k_u, k_d):
    return [point_temperature(x, delta_Tp, xp, k_u, k_d) for x in xs]


def finite_source_temperature(x, source_scale, xp, k_u, k_d, sigma_s,
                              quadrature_points=401):
    """高斯有限源与点源 Green 核的数值卷积。

    ``source_scale`` 是零宽度极限下的点源峰值尺度；有限宽度时实际峰值可
    变化，因此不把它误称为有限源曲线的最大温升。
    """
    sigma_s = _finite(sigma_s, "sigma_s")
    if sigma_s < 0:
        raise ValueError("sigma_s 不得为负")
    if sigma_s == 0:
        return point_temperature(x, source_scale, xp, k_u, k_d)
    if quadrature_points < 51 or quadrature_points % 2 == 0:
        raise ValueError("quadrature_points 必须是至少 51 的奇数")
    total = 0.0
    weight_sum = 0.0
    dz = 10.0 / (quadrature_points - 1)
    for index in range(quadrature_points):
        z = -5.0 + index * dz
        trapezoid = 0.5 if index in (0, quadrature_points - 1) else 1.0
        weight = trapezoid * math.exp(-0.5 * z * z)
        source_x = xp + sigma_s * z
        total += weight * point_temperature(x, source_scale, source_x, k_u, k_d)
        weight_sum += weight
    return total / weight_sum


def finite_source_profile(xs, source_scale, xp, k_u, k_d, sigma_s,
                          quadrature_points=401):
    return [
        finite_source_temperature(
            x, source_scale, xp, k_u, k_d, sigma_s, quadrature_points
        )
        for x in xs
    ]


def effective_parameters(k_u, k_d, H):
    """由有量纲衰减率返回 kappa_u/kappa_d/Pe_e/Da_e。"""
    k_u = _finite(k_u, "k_u")
    k_d = _finite(k_d, "k_d")
    H = _finite(H, "H")
    if k_u <= 0 or k_d <= 0 or H <= 0:
        raise ValueError("k_u、k_d、H 必须为正")
    kappa_u = k_u * H
    kappa_d = k_d * H
    return {
        "kappa_u": kappa_u,
        "kappa_d": kappa_d,
        "Pe_e": kappa_u - kappa_d,
        "Da_e": kappa_u * kappa_d,
    }


def _validated_profile(xs, values):
    if len(xs) != len(values) or len(xs) < 7:
        raise ValueError("x/温升长度必须一致且至少 7 点")
    pairs = [(_finite(x, "x"), _finite(y, "dT")) for x, y in zip(xs, values)]
    pairs.sort()
    if any(right[0] == left[0] for left, right in zip(pairs, pairs[1:])):
        raise ValueError("x 坐标重复")
    return pairs


def _solve_3x3(matrix, vector):
    augmented = [list(row) + [rhs] for row, rhs in zip(matrix, vector)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda row: abs(augmented[row][col]))
        if abs(augmented[pivot][col]) < 1e-12:
            raise ValueError("拟合矩阵奇异")
        augmented[col], augmented[pivot] = augmented[pivot], augmented[col]
        scale = augmented[col][col]
        augmented[col] = [value / scale for value in augmented[col]]
        for row in range(3):
            if row == col:
                continue
            factor = augmented[row][col]
            augmented[row] = [
                value - factor * reference
                for value, reference in zip(augmented[row], augmented[col])
            ]
    return [augmented[index][3] for index in range(3)]


def _linear_point_fit(pairs, xp, near_m, max_distance_m, detection_threshold,
                      min_side_points):
    rows = []
    ys = []
    n_left = 0
    n_right = 0
    for x, value in pairs:
        distance = abs(x - xp)
        if distance < near_m or (max_distance_m is not None and distance > max_distance_m):
            continue
        if value <= detection_threshold:
            continue
        if x < xp:
            rows.append([1.0, x - xp, 0.0])
            n_left += 1
        elif x > xp:
            rows.append([1.0, 0.0, -(x - xp)])
            n_right += 1
        else:
            continue
        ys.append(math.log(value))
    if n_left < min_side_points or n_right < min_side_points:
        raise ValueError("近场外上下游有效拟合点不足")
    matrix = [[0.0] * 3 for _ in range(3)]
    vector = [0.0] * 3
    for features, y in zip(rows, ys):
        for i in range(3):
            vector[i] += features[i] * y
            for j in range(3):
                matrix[i][j] += features[i] * features[j]
    intercept, k_u, k_d = _solve_3x3(matrix, vector)
    if k_u <= 0 or k_d <= 0:
        raise ValueError("拟合得到非正衰减率")
    delta_Tp = math.exp(intercept)
    residuals = []
    for features, observed_log in zip(rows, ys):
        predicted_log = intercept + k_u * features[1] + k_d * features[2]
        residuals.append(predicted_log - observed_log)
    log_rmse = math.sqrt(sum(value * value for value in residuals) / len(residuals))
    return {
        "delta_Tp": delta_Tp, "xp": xp, "k_u": k_u, "k_d": k_d,
        "log_rmse": log_rmse, "n_left": n_left, "n_right": n_right,
        "n_fit": len(rows),
    }


def fit_point_profile(xs, values, H=5.0, near_exclusion_H=0.4,
                      max_distance_H=None, detection_threshold=0.1,
                      min_side_points=3, xp_step=None, xp_bounds=None):
    """以连续 x_p 网格和共同截距对分段指数进行约束拟合。"""
    pairs = _validated_profile(xs, values)
    H = _finite(H, "H")
    near_exclusion_H = _finite(near_exclusion_H, "near_exclusion_H")
    if H <= 0 or near_exclusion_H < 0:
        raise ValueError("H 必须为正且 near_exclusion_H 不得为负")
    near_m = near_exclusion_H * H
    max_distance_m = None
    if max_distance_H is not None:
        max_distance_m = _finite(max_distance_H, "max_distance_H") * H
        if max_distance_m <= near_m:
            raise ValueError("max_distance_H 必须大于 near_exclusion_H")
    spacings = [right[0] - left[0] for left, right in zip(pairs, pairs[1:])]
    step = _finite(xp_step, "xp_step") if xp_step is not None else min(spacings) / 10.0
    if step <= 0:
        raise ValueError("xp_step 必须为正")
    if xp_bounds is None:
        peak_x = max(pairs, key=lambda item: item[1])[0]
        span = max(near_m, 2.0 * min(spacings))
        lo, hi = max(pairs[0][0], peak_x - span), min(pairs[-1][0], peak_x + span)
    else:
        lo, hi = (_finite(xp_bounds[0], "xp_lo"), _finite(xp_bounds[1], "xp_hi"))
    if not lo < hi:
        raise ValueError("x_p 搜索区间无效")

    best = None
    count = int(math.floor((hi - lo) / step))
    candidates = [lo + index * step for index in range(count + 1)]
    if not math.isclose(candidates[-1], hi, abs_tol=1e-10):
        candidates.append(hi)
    for xp in candidates:
        try:
            result = _linear_point_fit(
                pairs, xp, near_m, max_distance_m, detection_threshold,
                min_side_points,
            )
        except ValueError:
            continue
        if best is None or result["log_rmse"] < best["log_rmse"]:
            best = result
    if best is None:
        raise ValueError("所有 x_p 候选均因点数不足、矩阵奇异或非正衰减而失败")
    best.update({
        "near_exclusion_H": near_exclusion_H,
        "max_distance_H": max_distance_H,
        "detection_threshold": detection_threshold,
    })
    return best


def predict_point_fit(xs, fit):
    return point_profile(
        xs, fit["delta_Tp"], fit["xp"], fit["k_u"], fit["k_d"]
    )


def profile_nrmse(xs, observed, predicted, xp, exclusion_H, H=5.0,
                  max_distance_H=None):
    if not (len(xs) == len(observed) == len(predicted)):
        raise ValueError("评价曲线长度不一致")
    pairs = []
    for x, truth, estimate in zip(xs, observed, predicted):
        distance_H = abs(float(x) - xp) / H
        if distance_H < exclusion_H:
            continue
        if max_distance_H is not None and distance_H > max_distance_H:
            continue
        truth = _finite(truth, "observed")
        estimate = _finite(estimate, "predicted")
        pairs.append((truth, estimate))
    if not pairs:
        raise ValueError("评价区间没有测点")
    scale = max(abs(value) for value, _ in pairs)
    if scale <= 1e-12:
        raise ValueError("评价温升峰值为零")
    rmse = math.sqrt(sum((estimate - value) ** 2 for value, estimate in pairs) / len(pairs))
    return rmse / scale


def fit_finite_source_profile(xs, values, Df, sigma_factors=(0.1, 0.2, 0.3, 0.4),
                              H=5.0, near_exclusion_H=0.0):
    """在点源远场参数基础上网格比较单一高斯宽度系数。"""
    Df = _finite(Df, "Df")
    if Df <= 0:
        raise ValueError("Df 必须为正")
    point_fit = fit_point_profile(xs, values, H=H, near_exclusion_H=max(0.3, near_exclusion_H))
    candidates = []
    for factor in sigma_factors:
        factor = _finite(factor, "sigma_factor")
        if factor <= 0:
            raise ValueError("sigma_factor 必须为正")
        unit = finite_source_profile(
            xs, 1.0, point_fit["xp"], point_fit["k_u"], point_fit["k_d"],
            factor * Df,
        )
        selected = [
            (float(y), g) for x, y, g in zip(xs, values, unit)
            if abs(float(x) - point_fit["xp"]) / H >= near_exclusion_H
        ]
        denominator = sum(g * g for _, g in selected)
        scale = sum(y * g for y, g in selected) / denominator
        prediction = [scale * g for g in unit]
        score = profile_nrmse(
            xs, values, prediction, point_fit["xp"], near_exclusion_H, H
        )
        candidates.append({
            "sigma_factor": factor, "sigma_s": factor * Df,
            "source_scale": scale, "nrmse": score, "prediction": prediction,
            "xp": point_fit["xp"], "k_u": point_fit["k_u"], "k_d": point_fit["k_d"],
        })
    return min(candidates, key=lambda item: item["nrmse"]), candidates


def select_near_field_exclusion(profiles, candidates_H=(0.3, 0.4, 0.5), H=5.0):
    """按完整物理工况分组评价近场候选，返回最小中位远场 NRMSE。"""
    candidates = sorted({_finite(value, "near candidate") for value in candidates_H})
    if not candidates or candidates[0] < 0:
        raise ValueError("近场候选为空或为负")
    case_ids = [(profile.get("case_id") or "").strip() for profile in profiles]
    if len(case_ids) < 2 or any(not value for value in case_ids):
        raise ValueError("分组评价至少需要两个有 case_id 的完整工况")
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("case_id 重复；同一物理工况不得拆成多个组")
    common_eval_exclusion = max(candidates)
    summaries = []
    for exclusion in candidates:
        scores = []
        failures = []
        for profile in profiles:
            try:
                fit = fit_point_profile(
                    profile["x"], profile["dT"], H=H,
                    near_exclusion_H=exclusion,
                )
                predicted = predict_point_fit(profile["x"], fit)
                scores.append(profile_nrmse(
                    profile["x"], profile["dT"], predicted, fit["xp"],
                    common_eval_exclusion, H,
                ))
            except (KeyError, ValueError) as exc:
                failures.append(f"{profile.get('case_id')}: {exc}")
        summaries.append({
            "near_exclusion_H": exclusion,
            "median_nrmse": statistics.median(scores) if scores else math.inf,
            "case_scores": scores, "n_pass": len(scores),
            "n_fail": len(failures), "failures": failures,
        })
    valid = [row for row in summaries if row["n_fail"] == 0]
    if not valid:
        raise ValueError("所有近场候选均有工况失败")
    return min(valid, key=lambda row: (row["median_nrmse"], row["near_exclusion_H"])), summaries


def interval_sensitivity(xs, values, windows_H, H=5.0):
    """按 (near_H,max_H) 窗口扰动并报告相对基准参数变化。"""
    if not windows_H:
        raise ValueError("拟合窗口列表为空")
    fits = []
    for near_H, max_H in windows_H:
        fit = fit_point_profile(
            xs, values, H=H, near_exclusion_H=near_H,
            max_distance_H=max_H,
        )
        fit["window_near_H"] = float(near_H)
        fit["window_max_H"] = float(max_H)
        fits.append(fit)
    baseline = fits[0]
    for fit in fits:
        for name in ("delta_Tp", "k_u", "k_d"):
            fit[f"{name}_rel_change"] = abs(fit[name] - baseline[name]) / abs(baseline[name])
        fit["xp_shift_H"] = abs(fit["xp"] - baseline["xp"]) / H
    return {
        "fits": fits,
        "max_delta_Tp_rel_change": max(row["delta_Tp_rel_change"] for row in fits),
        "max_k_u_rel_change": max(row["k_u_rel_change"] for row in fits),
        "max_k_d_rel_change": max(row["k_d_rel_change"] for row in fits),
        "max_xp_shift_H": max(row["xp_shift_H"] for row in fits),
    }


def run_synthetic_software_check(out_path=DEFAULT_SYNTHETIC_OUT):
    """写出明确标记的合成参数恢复证据。"""
    cases = (
        ("syn_symmetric", 180.0, 50.0, 0.16, 0.16),
        ("syn_asymmetric_a", 240.0, 48.5, 0.24, 0.10),
        ("syn_asymmetric_b", 140.0, 52.3, 0.11, 0.22),
    )
    xs = [20.0 + index for index in range(61)]
    rows = []
    for case_id, amplitude, xp, k_u, k_d in cases:
        clean = point_profile(xs, amplitude, xp, k_u, k_d)
        observed = []
        for x, value in zip(xs, clean):
            far_noise = 1.0 + 0.003 * math.sin(0.7 * x)
            near_bias = 1.0 + (0.18 * math.cos((x - xp) * math.pi / 2.0)
                               if abs(x - xp) < 1.8 else 0.0)
            observed.append(value * far_noise * near_bias)
        fit = fit_point_profile(xs, observed, H=5.0, near_exclusion_H=0.4, xp_step=0.02)
        predicted = predict_point_fit(xs, fit)
        nrmse = profile_nrmse(xs, observed, predicted, fit["xp"], 0.4, 5.0)
        rows.append({
            "data_type": "SYNTHETIC_SOFTWARE_TEST_NOT_SCIENTIFIC_EVIDENCE",
            "case_id": case_id, "true_delta_Tp_C": f"{amplitude:.6f}",
            "fit_delta_Tp_C": f"{fit['delta_Tp']:.6f}", "true_xp_m": f"{xp:.6f}",
            "fit_xp_m": f"{fit['xp']:.6f}", "true_kappa_u": f"{k_u * 5:.6f}",
            "fit_kappa_u": f"{fit['k_u'] * 5:.6f}", "true_kappa_d": f"{k_d * 5:.6f}",
            "fit_kappa_d": f"{fit['k_d'] * 5:.6f}", "profile_nrmse": f"{nrmse:.8f}",
            "near_exclusion_H": "0.4",
        })
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=SYNTHETIC_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic-check-out", default=str(DEFAULT_SYNTHETIC_OUT))
    args = parser.parse_args()
    rows = run_synthetic_software_check(args.synthetic_check_out)
    worst = max(float(row["profile_nrmse"]) for row in rows)
    print(f"[OK] 合成软件检查 -> {args.synthetic_check_out}")
    print(f"工况={len(rows)}，最差远场 NRMSE={worst:.6f}")
    print("注意：SYNTHETIC_SOFTWARE_TEST，不是结构判伪或物理结论。")


if __name__ == "__main__":
    main()
