"""阶段四无量纲温度核、参数提取和不确定性软件框架。"""
from __future__ import annotations

import argparse
import csv
import math
import random
import statistics
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

from src.analysis import censoring_peak, transport_model
from src.fds import tunnel_config as cfg


DEFAULT_SYNTHETIC_OUT = (
    PROJECT_ROOT / "derived" / "legacy_modeling" / "synthetic_parameter_check.csv"
)
PARAMETERS = ("delta_Tp", "xp", "kappa_u", "kappa_d", "Pe_e", "Da_e", "Pi_S")
CLASSIFICATIONS = {
    "bilateral_identifiable", "upstream_censored", "no_obvious_backflow",
    "downstream_domain_censored", "insufficient_downstream",
}
SYNTHETIC_FIELDS = (
    "data_type", "case_id", "true_Pe_e", "fit_Pe_e", "true_Da_e", "fit_Da_e",
    "true_Pi_S", "fit_Pi_S", "profile_nrmse", "uncertainty_success_fraction",
    "status",
)


def _finite(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 不是有效数值: {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} 必须为有限数值")
    return number


def decay_parameters(Pe_e, Da_e):
    Pe_e = _finite(Pe_e, "Pe_e")
    Da_e = _finite(Da_e, "Da_e")
    if Da_e <= 0:
        raise ValueError("Da_e 必须为正")
    root = math.sqrt(Pe_e * Pe_e + 4.0 * Da_e)
    return {
        "kappa_u": 0.5 * (root + Pe_e),
        "kappa_d": 0.5 * (root - Pe_e),
        "root": root,
    }


def dimensionless_temperature(xi, Pi_S, Pe_e, Da_e):
    xi = _finite(xi, "xi")
    Pi_S = _finite(Pi_S, "Pi_S")
    if Pi_S <= 0:
        raise ValueError("Pi_S 必须为正")
    decay = decay_parameters(Pe_e, Da_e)
    amplitude = Pi_S / decay["root"]
    if xi < 0:
        return amplitude * math.exp(decay["kappa_u"] * xi)
    return amplitude * math.exp(-decay["kappa_d"] * xi)


def dimensionless_profile(xis, Pi_S, Pe_e, Da_e):
    return [dimensionless_temperature(xi, Pi_S, Pe_e, Da_e) for xi in xis]


def temperature_rise_profile(xs, xp, H, T0_K, Pi_S, Pe_e, Da_e):
    xp = _finite(xp, "xp")
    H = _finite(H, "H")
    T0_K = _finite(T0_K, "T0_K")
    if H <= 0 or T0_K <= 0:
        raise ValueError("H/T0_K 必须为正")
    xis = [(_finite(x, "x") - xp) / H for x in xs]
    return [T0_K * value for value in dimensionless_profile(xis, Pi_S, Pe_e, Da_e)]


def q_convective_star(Q_MW, chi_r=cfg.CHI_R_PRESET, H=cfg.H):
    Q_MW = _finite(Q_MW, "Q_MW")
    chi_r = _finite(chi_r, "chi_r")
    H = _finite(H, "H")
    if Q_MW <= 0 or H <= 0 or not 0 <= chi_r < 1:
        raise ValueError("Q_MW/H 必须为正且 0<=chi_r<1")
    denominator = (
        cfg.RHO_0 * cfg.CP * cfg.T_AMBIENT_K * math.sqrt(cfg.G) * H ** 2.5
    )
    return Q_MW * 1e6 * (1.0 - chi_r) / denominator


def parameters_from_fit(fit, H=cfg.H, T0_K=cfg.T_AMBIENT_K):
    H = _finite(H, "H")
    T0_K = _finite(T0_K, "T0_K")
    if H <= 0 or T0_K <= 0:
        raise ValueError("H/T0_K 必须为正")
    effective = transport_model.effective_parameters(fit["k_u"], fit["k_d"], H)
    delta_Tp = _finite(fit["delta_Tp"], "delta_Tp")
    xp = _finite(fit["xp"], "xp")
    root = math.sqrt(effective["Pe_e"] ** 2 + 4.0 * effective["Da_e"])
    Pi_S = (delta_Tp / T0_K) * root
    return {
        "delta_Tp": delta_Tp, "xp": xp,
        "kappa_u": effective["kappa_u"], "kappa_d": effective["kappa_d"],
        "Pe_e": effective["Pe_e"], "Da_e": effective["Da_e"], "Pi_S": Pi_S,
    }


def parameter_availability(classification):
    """返回独立衰减目标掩码；旧分类只作为迁移别名保留。"""
    if classification not in CLASSIFICATIONS:
        raise ValueError(f"未知 classification={classification!r}")
    if classification == "bilateral_identifiable":
        return {
            "kappa_u_available": True, "kappa_d_available": True,
            "Pe_e_Da_e_available": True, "Pi_S_available": True,
            "delta_over_H_available": True,
        }
    if classification in {"upstream_censored", "no_obvious_backflow"}:
        return {
            "kappa_u_available": False, "kappa_d_available": True,
            "Pe_e_Da_e_available": False, "Pi_S_available": False,
            "delta_over_H_available": True,
        }
    return {
        "kappa_u_available": True, "kappa_d_available": False,
        "Pe_e_Da_e_available": False, "Pi_S_available": False,
        "delta_over_H_available": True,
    }


def _one_sided_decay(xs, values, xp, side, H, near_exclusion_H,
                     detection_threshold, min_points=3, max_distance_H=None):
    pairs = transport_model._validated_profile(xs, values)
    if side not in {"upstream", "downstream"}:
        raise ValueError("side 必须为 upstream/downstream")
    selected = []
    for x, value in pairs:
        distance = xp - x if side == "upstream" else x - xp
        if distance < near_exclusion_H * H or value <= detection_threshold:
            continue
        if max_distance_H is not None and distance > max_distance_H * H:
            continue
        selected.append((distance, math.log(value)))
    if len(selected) < min_points:
        raise ValueError(f"{side} 近场外超阈值测点少于 {min_points} 个")
    mean_x = statistics.fmean(row[0] for row in selected)
    mean_y = statistics.fmean(row[1] for row in selected)
    denominator = sum((row[0] - mean_x) ** 2 for row in selected)
    if denominator <= 1e-12:
        raise ValueError(f"{side} 单侧衰减拟合距离退化")
    slope = sum((x - mean_x) * (y - mean_y) for x, y in selected) / denominator
    decay = -slope
    if not math.isfinite(decay) or decay <= 0:
        raise ValueError(f"{side} 单侧拟合得到非正衰减率")
    intercept = mean_y - slope * mean_x
    residuals = [intercept + slope * x - y for x, y in selected]
    return {
        "k_1_m": decay, "kappa": decay * H, "n_points": len(selected),
        "side_peak_scale_C": math.exp(intercept),
        "log_rmse": math.sqrt(sum(value * value for value in residuals) / len(residuals)),
    }


def _validated_interval_windows(interval_windows):
    checked = []
    for near_H, max_H in interval_windows or ():
        near_H = _finite(near_H, "near_H")
        max_H = _finite(max_H, "max_H")
        if near_H < 0 or max_H <= near_H:
            raise ValueError("每个拟合窗口必须满足 0<=near_H<max_H")
        checked.append((near_H, max_H))
    return checked


def _one_sided_interval_sensitivity(case, xp, side, H, windows, threshold):
    fits = []
    for near_H, max_H in windows:
        fit = _one_sided_decay(
            case["x"], case["dT"], xp, side, H, near_H, threshold,
            max_distance_H=max_H,
        )
        fits.append({**fit, "window_near_H": near_H, "window_max_H": max_H})
    baseline = fits[0]["kappa"]
    changes = [abs(row["kappa"] - baseline) / abs(baseline) for row in fits]
    return {"fits": fits, "max_relative_change": max(changes)}


def extract_case_parameters(case, near_exclusion_H=0.4, interval_windows=None,
                            max_decay_rel_change=0.20):
    case_id = str(case.get("case_id") or "").strip()
    if not case_id:
        raise ValueError("case_id 不能为空")
    classification = case.get("classification", "bilateral_identifiable")
    if classification not in CLASSIFICATIONS:
        raise ValueError(f"{case_id}: 未知 classification={classification!r}")
    max_decay_rel_change = _finite(max_decay_rel_change, "max_decay_rel_change")
    if max_decay_rel_change < 0:
        raise ValueError("max_decay_rel_change 不得为负")
    checked_windows = _validated_interval_windows(interval_windows)
    if classification != "bilateral_identifiable":
        mask = parameter_availability(classification)
        if "x" not in case or "dT" not in case:
            return {
                "case_id": case_id, "status": "CENSORED_NO_PROFILE_PARAMETERS",
                "classification": classification, **mask,
                "reason": "缺少剖面；不生成任何衰减参数",
            }
        H = _finite(case.get("H", cfg.H), f"{case_id}:H")
        T0_K = _finite(case.get("T0_K", cfg.T_AMBIENT_K), f"{case_id}:T0_K")
        if H <= 0 or T0_K <= 0:
            raise ValueError(f"{case_id}:H/T0_K 必须为正")
        threshold = _finite(case.get("detection_threshold", 0.1), "detection_threshold")
        peak = censoring_peak.estimate_peak(case["x"], case["dT"], method="quadratic")
        result = {
            "case_id": case_id, "status": "PASS_PARTIAL_PARAMETERS",
            "classification": (
                "downstream_domain_censored"
                if classification == "insufficient_downstream" else classification
            ),
            "delta_Tp": peak["delta_Tp"], "xp": peak["xp"],
            "near_exclusion_H": near_exclusion_H, **mask,
            "interval_stability_assessed": bool(checked_windows),
            "max_decay_rel_change_allowed": max_decay_rel_change,
        }
        max_distance_H = case.get("max_distance_H")
        if max_distance_H is not None:
            max_distance_H = _finite(max_distance_H, "max_distance_H")
            if max_distance_H <= near_exclusion_H:
                raise ValueError("max_distance_H 必须大于 near_exclusion_H")
        unstable_sides = []
        if mask["kappa_u_available"]:
            try:
                upstream = _one_sided_decay(
                    case["x"], case["dT"], peak["xp"], "upstream", H,
                    near_exclusion_H, threshold, max_distance_H=max_distance_H,
                )
                result.update({
                    "k_u_1_m": upstream["k_1_m"], "kappa_u": upstream["kappa"],
                    "n_left": upstream["n_points"], "upstream_log_rmse": upstream["log_rmse"],
                })
                if checked_windows:
                    sensitivity = _one_sided_interval_sensitivity(
                        case, peak["xp"], "upstream", H, checked_windows, threshold,
                    )
                    change = sensitivity["max_relative_change"]
                    result["max_kappa_u_rel_change"] = change
                    if change > max_decay_rel_change:
                        raise ValueError("上游衰减对拟合窗口不稳定")
            except ValueError as exc:
                if not checked_windows:
                    raise
                mask["kappa_u_available"] = False
                result["kappa_u_available"] = False
                unstable_sides.append(f"upstream:{exc}")
        if mask["kappa_d_available"]:
            try:
                downstream = _one_sided_decay(
                    case["x"], case["dT"], peak["xp"], "downstream", H,
                    near_exclusion_H, threshold, max_distance_H=max_distance_H,
                )
                result.update({
                    "k_d_1_m": downstream["k_1_m"], "kappa_d": downstream["kappa"],
                    "n_right": downstream["n_points"],
                    "downstream_log_rmse": downstream["log_rmse"],
                })
                if checked_windows:
                    sensitivity = _one_sided_interval_sensitivity(
                        case, peak["xp"], "downstream", H, checked_windows, threshold,
                    )
                    change = sensitivity["max_relative_change"]
                    result["max_kappa_d_rel_change"] = change
                    if change > max_decay_rel_change:
                        raise ValueError("下游衰减对拟合窗口不稳定")
            except ValueError as exc:
                if not checked_windows:
                    raise
                mask["kappa_d_available"] = False
                result["kappa_d_available"] = False
                unstable_sides.append(f"downstream:{exc}")
        both_available = mask["kappa_u_available"] and mask["kappa_d_available"]
        result["Pe_e_Da_e_available"] = both_available
        result["Pi_S_available"] = both_available
        if unstable_sides:
            result["status"] = "PASS_WITH_UNSTABLE_PARAMETERS"
            result["stability_warning"] = ";".join(unstable_sides)
            result["parameter_use_policy"] = (
                "ESTIMATES_RETAINED_EXPLORATORY_EXCLUDE_FROM_FORMAL_CLOSURE_BY_MASK"
            )
        return result
    H = _finite(case.get("H", cfg.H), f"{case_id}:H")
    T0_K = _finite(case.get("T0_K", cfg.T_AMBIENT_K), f"{case_id}:T0_K")
    fit = transport_model.fit_point_profile(
        case["x"], case["dT"], H=H, near_exclusion_H=near_exclusion_H,
        detection_threshold=_finite(case.get("detection_threshold", 0.1), "detection_threshold"),
    )
    prediction = transport_model.predict_point_fit(case["x"], fit)
    nrmse = transport_model.profile_nrmse(
        case["x"], case["dT"], prediction, fit["xp"], near_exclusion_H, H,
    )
    result = {
        "case_id": case_id, "status": "PASS", "classification": classification,
        **parameter_availability(classification),
        **parameters_from_fit(fit, H, T0_K), "profile_nrmse": nrmse,
        "near_exclusion_H": near_exclusion_H, "n_left": fit["n_left"],
        "n_right": fit["n_right"],
    }
    result["interval_stability_assessed"] = bool(checked_windows)
    result["max_decay_rel_change_allowed"] = max_decay_rel_change
    if checked_windows:
        sensitivity = transport_model.interval_sensitivity(
            case["x"], case["dT"], checked_windows, H,
        )
        result.update({
            "max_kappa_u_rel_change": sensitivity["max_k_u_rel_change"],
            "max_kappa_d_rel_change": sensitivity["max_k_d_rel_change"],
            "max_xp_shift_H": sensitivity["max_xp_shift_H"],
        })
        stable_u = sensitivity["max_k_u_rel_change"] <= max_decay_rel_change
        stable_d = sensitivity["max_k_d_rel_change"] <= max_decay_rel_change
        result["kappa_u_available"] = stable_u
        result["kappa_d_available"] = stable_d
        result["Pe_e_Da_e_available"] = stable_u and stable_d
        result["Pi_S_available"] = stable_u and stable_d
        if not (stable_u and stable_d):
            result["status"] = "PASS_WITH_UNSTABLE_PARAMETERS"
            result["stability_warning"] = "UNSTABLE_" + "_AND_".join(
                side for side, stable in (("KAPPA_U", stable_u), ("KAPPA_D", stable_d))
                if not stable
            )
            result["parameter_use_policy"] = (
                "ESTIMATES_RETAINED_EXPLORATORY_EXCLUDE_FROM_FORMAL_CLOSURE_BY_MASK"
            )
    return result


def extract_batch(cases, near_exclusion_H=0.4, interval_windows=None,
                  max_decay_rel_change=0.20):
    case_ids = [str(case.get("case_id") or "").strip() for case in cases]
    if not cases or any(not case_id for case_id in case_ids):
        raise ValueError("批量参数提取需要非空且完整的 case_id")
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("case_id 重复；同一物理工况不得拆组")
    rows = []
    for case in cases:
        try:
            rows.append(extract_case_parameters(
                case, near_exclusion_H, interval_windows, max_decay_rel_change,
            ))
        except (KeyError, ValueError) as exc:
            rows.append({
                "case_id": case["case_id"], "status": "FAIL",
                "classification": case.get("classification", "unknown"),
                "reason": str(exc),
            })
    return rows


def _quantile(values, probability):
    ordered = sorted(values)
    if not ordered:
        raise ValueError("分位数输入为空")
    position = probability * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def parameter_uncertainty(case, profile_replicates=None, n_boot=200, seed=20260729,
                          drop_probability=0.10, position_sigma_m=0.02,
                          temperature_sigma_C=0.3,
                          interval_windows=((0.3, 5.5), (0.4, 5.0), (0.5, 4.5))):
    if n_boot < 20 or not 0 <= drop_probability < 0.5:
        raise ValueError("n_boot 至少为 20 且 drop_probability 位于 [0,0.5)")
    if position_sigma_m < 0 or temperature_sigma_C < 0:
        raise ValueError("扰动标准差不得为负")
    classification = case.get("classification", "bilateral_identifiable")
    if classification not in CLASSIFICATIONS:
        raise ValueError(f"未知 classification={classification!r}")
    xs = list(case["x"])
    base = list(case["dT"])
    replicates = [base] if profile_replicates is None else [list(row) for row in profile_replicates]
    if any(len(row) != len(xs) for row in replicates) or not replicates:
        raise ValueError("profile_replicates 必须非空且与 x 等长")
    if not interval_windows:
        raise ValueError("interval_windows 不能为空")
    checked_windows = _validated_interval_windows(interval_windows)
    if classification != "bilateral_identifiable":
        rng = random.Random(seed)
        H = _finite(case.get("H", cfg.H), "H")
        estimates = []
        failures = 0
        for _ in range(n_boot):
            profile = rng.choice(replicates)
            pairs = [
                (_finite(x, "x") + rng.gauss(0.0, position_sigma_m),
                 _finite(value, "dT") + rng.gauss(0.0, temperature_sigma_C))
                for x, value in zip(xs, profile) if rng.random() >= drop_probability
            ]
            pairs.sort()
            if len(pairs) < 7:
                failures += 1
                continue
            near_H, max_H = rng.choice(checked_windows)
            perturbed = dict(
                case, x=[row[0] for row in pairs], dT=[row[1] for row in pairs],
                max_distance_H=max_H,
            )
            try:
                estimate = extract_case_parameters(perturbed, near_exclusion_H=near_H)
                if estimate["status"] != "PASS_PARTIAL_PARAMETERS":
                    raise ValueError(estimate["status"])
                estimates.append(estimate)
            except ValueError:
                failures += 1
        if len(estimates) < 0.8 * n_boot:
            raise ValueError(f"单侧参数 Bootstrap 成功率不足 80%: {len(estimates)}/{n_boot}")
        mask = parameter_availability(classification)
        names = ["delta_Tp", "xp"]
        if mask["kappa_u_available"]:
            names.append("kappa_u")
        if mask["kappa_d_available"]:
            names.append("kappa_d")
        summary = {
            "status": "PASS_PARTIAL_PARAMETERS", "classification": classification,
            "n_requested": n_boot, "n_success": len(estimates), "n_failed": failures,
            "success_fraction": len(estimates) / n_boot, "seed": seed, **mask,
        }
        for name in names:
            values = [row[name] for row in estimates]
            summary[f"{name}_median"] = statistics.median(values)
            summary[f"{name}_ci_low"] = _quantile(values, 0.025)
            summary[f"{name}_ci_high"] = _quantile(values, 0.975)
        return summary
    rng = random.Random(seed)
    estimates = []
    failures = 0
    H = _finite(case.get("H", cfg.H), "H")
    T0_K = _finite(case.get("T0_K", cfg.T_AMBIENT_K), "T0_K")
    for _ in range(n_boot):
        profile = rng.choice(replicates)
        pairs = []
        for x, value in zip(xs, profile):
            if rng.random() < drop_probability:
                continue
            pairs.append((
                _finite(x, "x") + rng.gauss(0.0, position_sigma_m),
                _finite(value, "dT") + rng.gauss(0.0, temperature_sigma_C),
            ))
        pairs.sort()
        if len(pairs) < 7:
            failures += 1
            continue
        near_H, max_H = rng.choice(checked_windows)
        try:
            fit = transport_model.fit_point_profile(
                [row[0] for row in pairs], [row[1] for row in pairs], H=H,
                near_exclusion_H=near_H, max_distance_H=max_H,
                detection_threshold=case.get("detection_threshold", 0.1),
            )
            estimates.append(parameters_from_fit(fit, H, T0_K))
        except ValueError:
            failures += 1
    if len(estimates) < 0.8 * n_boot:
        raise ValueError(f"参数 Bootstrap 成功率不足 80%: {len(estimates)}/{n_boot}")
    summary = {
        "status": "PASS", "n_requested": n_boot, "n_success": len(estimates),
        "n_failed": failures, "success_fraction": len(estimates) / n_boot, "seed": seed,
    }
    for name in PARAMETERS:
        values = [row[name] for row in estimates]
        summary[f"{name}_median"] = statistics.median(values)
        summary[f"{name}_ci_low"] = _quantile(values, 0.025)
        summary[f"{name}_ci_high"] = _quantile(values, 0.975)
    return summary


def run_synthetic_check(out_path=DEFAULT_SYNTHETIC_OUT):
    xs = [20.0 + index for index in range(61)]
    definitions = (
        ("symmetric", 0.0, 1.0, 0.80, 50.0),
        ("advective_a", 0.65, 0.90, 1.10, 48.5),
        ("advective_b", 1.10, 0.70, 1.30, 52.0),
    )
    rows = []
    for case_index, (case_id, Pe_e, Da_e, Pi_S, xp) in enumerate(definitions):
        clean = temperature_rise_profile(xs, xp, cfg.H, cfg.T_AMBIENT_K, Pi_S, Pe_e, Da_e)
        observed = [
            value * (1.0 + 0.002 * math.sin(0.4 * x + case_index))
            for x, value in zip(xs, clean)
        ]
        case = {"case_id": case_id, "x": xs, "dT": observed, "H": cfg.H}
        fit = extract_case_parameters(case, near_exclusion_H=0.4)
        uncertainty = parameter_uncertainty(
            case, n_boot=60, seed=100 + case_index, drop_probability=0.05,
            position_sigma_m=0.01, temperature_sigma_C=0.05,
        )
        rows.append({
            "data_type": "SYNTHETIC_SOFTWARE_TEST_NOT_SCIENTIFIC_EVIDENCE",
            "case_id": case_id, "true_Pe_e": f"{Pe_e:.8f}",
            "fit_Pe_e": f"{fit['Pe_e']:.8f}", "true_Da_e": f"{Da_e:.8f}",
            "fit_Da_e": f"{fit['Da_e']:.8f}", "true_Pi_S": f"{Pi_S:.8f}",
            "fit_Pi_S": f"{fit['Pi_S']:.8f}",
            "profile_nrmse": f"{fit['profile_nrmse']:.8f}",
            "uncertainty_success_fraction": f"{uncertainty['success_fraction']:.6f}",
            "status": "PASS",
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
    try:
        rows = run_synthetic_check(args.synthetic_check_out)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    worst = max(float(row["profile_nrmse"]) for row in rows)
    print(f"[OK] {len(rows)} 个合成无量纲/参数提取检查 -> {args.synthetic_check_out}")
    print(f"最差远场 NRMSE={worst:.6f}")
    print("注意：SYNTHETIC_SOFTWARE_TEST，不是闭合关系或真实参数结论。")


if __name__ == "__main__":
    main()
