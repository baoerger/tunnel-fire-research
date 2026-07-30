"""阶段四无量纲温度核、参数提取和不确定性软件框架。"""
from __future__ import annotations

import argparse
import csv
import math
import random
import statistics
import sys
from pathlib import Path


STAGE4_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = STAGE4_ROOT.parent
STAGE1_SRC = PROJECT_ROOT / "阶段一" / "src"
STAGE2_SRC = PROJECT_ROOT / "阶段二" / "src"
for source_dir in (STAGE1_SRC, STAGE2_SRC):
    sys.path.insert(0, str(source_dir))

import transport_model  # noqa: E402
import tunnel_config as cfg  # noqa: E402


DEFAULT_SYNTHETIC_OUT = (
    STAGE4_ROOT / "01_无量纲核与参数提取" / "synthetic_parameter_check.csv"
)
PARAMETERS = ("delta_Tp", "xp", "kappa_u", "kappa_d", "Pe_e", "Da_e", "Pi_S")
CLASSIFICATIONS = {
    "bilateral_identifiable", "upstream_censored", "no_obvious_backflow",
    "insufficient_downstream",
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


def extract_case_parameters(case, near_exclusion_H=0.4, interval_windows=None):
    case_id = str(case.get("case_id") or "").strip()
    if not case_id:
        raise ValueError("case_id 不能为空")
    classification = case.get("classification", "bilateral_identifiable")
    if classification not in CLASSIFICATIONS:
        raise ValueError(f"{case_id}: 未知 classification={classification!r}")
    if classification != "bilateral_identifiable":
        return {
            "case_id": case_id, "status": "CENSORED_NO_FULL_PARAMETERS",
            "classification": classification,
            "reason": "强风删失工况只参与全曲线损失，不强制输出完整参数",
        }
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
        **parameters_from_fit(fit, H, T0_K), "profile_nrmse": nrmse,
        "near_exclusion_H": near_exclusion_H, "n_left": fit["n_left"],
        "n_right": fit["n_right"],
    }
    if interval_windows:
        sensitivity = transport_model.interval_sensitivity(
            case["x"], case["dT"], interval_windows, H,
        )
        result.update({
            "max_kappa_u_rel_change": sensitivity["max_k_u_rel_change"],
            "max_kappa_d_rel_change": sensitivity["max_k_d_rel_change"],
            "max_xp_shift_H": sensitivity["max_xp_shift_H"],
        })
    return result


def extract_batch(cases, near_exclusion_H=0.4, interval_windows=None):
    case_ids = [str(case.get("case_id") or "").strip() for case in cases]
    if not cases or any(not case_id for case_id in case_ids):
        raise ValueError("批量参数提取需要非空且完整的 case_id")
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("case_id 重复；同一物理工况不得拆组")
    rows = []
    for case in cases:
        try:
            rows.append(extract_case_parameters(case, near_exclusion_H, interval_windows))
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
    if classification != "bilateral_identifiable":
        return {
            "status": "CENSORED_NO_FULL_PARAMETERS", "n_requested": n_boot,
            "n_success": 0, "reason": "删失工况不输出不可靠完整参数不确定性",
        }
    xs = list(case["x"])
    base = list(case["dT"])
    replicates = [base] if profile_replicates is None else [list(row) for row in profile_replicates]
    if any(len(row) != len(xs) for row in replicates) or not replicates:
        raise ValueError("profile_replicates 必须非空且与 x 等长")
    if not interval_windows:
        raise ValueError("interval_windows 不能为空")
    checked_windows = []
    for near_H, max_H in interval_windows:
        near_H = _finite(near_H, "near_H")
        max_H = _finite(max_H, "max_H")
        if near_H < 0 or max_H <= near_H:
            raise ValueError("每个拟合窗口必须满足 0<=near_H<max_H")
        checked_windows.append((near_H, max_H))
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
