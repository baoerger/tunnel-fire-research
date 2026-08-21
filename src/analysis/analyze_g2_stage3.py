"""G2 阶段 3：网格敏感性与 LES 随机种子门。

按 ``config/protocol_v1.json`` 的冻结阈值，在 xi_h=5/4/3 三个候选域
分别计算温度峰值、曲线 NRMSE、峰位、全局不对称度、实际 HRR 与种子
离散性。原始时序仍留在 ``runs/``，这里只生成可复核的轻量 CSV/JSON。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path

from src.fds import fds_io
from src.fds import tunnel_config as cfg
from src.fds.project_paths import PROJECT_ROOT


GRID_PURPOSES = {"grid_low", "grid_high"}
SEED_PURPOSES = {"seed_repeat_low", "seed_repeat_high"}


def _read_csv(path):
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        return [{key: (value or "").strip() for key, value in row.items()}
                for row in csv.DictReader(stream)]


def _read_windows(path):
    windows = {}
    for row in _read_csv(path):
        if row.get("status") != "PASS":
            continue
        windows[row["chid"]] = (float(row["avg_t0"]), float(row["avg_t1"]))
    return windows


def _relative_profile(xs, values, fire_x):
    return sorted((round(x - fire_x, 8), value)
                  for x, value in zip(xs, values)
                  if math.isfinite(x) and math.isfinite(value))


def _profile_dict(profile, domain_m):
    return {x: value for x, value in profile if abs(x) <= domain_m + 1e-9}


def profile_nrmse(profile_a, profile_b, domain_m):
    """两条曲线在共同测点上的 NRMSE，以第二条曲线峰值归一化。"""
    a = _profile_dict(profile_a, domain_m)
    b = _profile_dict(profile_b, domain_m)
    common = sorted(set(a) & set(b))
    if len(common) < 3:
        return None
    rmse = math.sqrt(sum((a[x] - b[x]) ** 2 for x in common) / len(common))
    scale = max(abs(b[x]) for x in common)
    return rmse / scale if scale > 1e-12 else None


def global_asymmetry(profile, domain_m, epsilon_T=0.5):
    """按冻结公式以等权镜像测点计算 A_global。"""
    points = _profile_dict(profile, domain_m)
    positive = sorted(x for x in points if x > 1e-9 and -x in points)
    if not positive:
        return None
    numerator = sum(abs(points[x] - points[-x]) for x in positive) / len(positive)
    denominator = (
        sum((points[x] + points[-x]) / 2 for x in positive) / len(positive)
        + epsilon_T
    )
    return numerator / denominator if denominator > 1e-12 else None


def left_right_nrmse(profile, domain_m):
    """同一工况左右镜像曲线差异，以两侧较大峰值归一化。"""
    points = _profile_dict(profile, domain_m)
    positive = sorted(x for x in points if x > 1e-9 and -x in points)
    if not positive:
        return None
    rmse = math.sqrt(
        sum((points[x] - points[-x]) ** 2 for x in positive) / len(positive)
    )
    scale = max(abs(points[position])
                for distance in positive for position in (distance, -distance))
    return rmse / scale if scale > 1e-12 else None


def _window_mean(times, values, window):
    selected = [value for time, value in zip(times or [], values or [])
                if window[0] <= time <= window[1] and math.isfinite(value)]
    return sum(selected) / len(selected) if selected else None


def _load_case(rundir, case, window, candidate_xi):
    chid = case["chid"]
    fire_x = float(case["x_fire"])
    times, series, units = fds_io.read_devc(str(rundir), chid)
    if times is None:
        raise ValueError(f"{chid}: 缺少 DEVC")
    series = fds_io.normalize_units(series, units)
    xs, values = fds_io.extract_T_profile(
        times, series, fire_x, cfg.T_AMBIENT_C, t_window=window)
    if xs is None:
        raise ValueError(f"{chid}: 准稳态窗口没有温度数据")
    profile = _relative_profile(xs, values, fire_x)
    x_peak, deltaT_peak = fds_io.parabolic_peak(xs, values)

    hrr_times, hrr_series, hrr_units = fds_io.read_hrr(str(rundir), chid)
    if hrr_times is None:
        raise ValueError(f"{chid}: 缺少 HRR")
    hrr_series = fds_io.normalize_units(hrr_series, hrr_units)
    hrr_key = "HRR" if "HRR" in hrr_series else "HRR_tot"
    if hrr_key not in hrr_series:
        raise ValueError(f"{chid}: HRR 文件没有 HRR 列")
    hrr_mean = _window_mean(hrr_times, hrr_series[hrr_key], window)
    if hrr_mean is None:
        raise ValueError(f"{chid}: 准稳态窗口没有 HRR 数据")

    metrics = {
        "chid": chid,
        "purpose": case["purpose"],
        "Q_MW": float(case["Q"]),
        "dx_m": float(case["dx"]),
        "rnd_seed": int(case["rnd_seed"]),
        "t0_s": window[0],
        "t1_s": window[1],
        "deltaT_peak_C": deltaT_peak,
        "x_peak_m": x_peak,
        "peak_shift_m": x_peak - fire_x,
        "hrr_mean_kW": hrr_mean,
    }
    for xi in candidate_xi:
        domain_m = xi * cfg.H
        metrics[f"A_global_xi{xi}"] = global_asymmetry(profile, domain_m)
        metrics[f"left_right_nrmse_xi{xi}"] = left_right_nrmse(profile, domain_m)
    metrics["profile"] = profile
    return metrics


def _relative_difference(value, reference):
    return abs(value - reference) / abs(reference) if abs(reference) > 1e-12 else None


def _obvious_reverse_divergence(coarse, medium, fine, material_difference=0.0):
    """方向反转、中到细变化更大且超过指标量级，才算明显发散。"""
    first = medium - coarse
    second = fine - medium
    return (first * second < 0 and abs(second) > abs(first)
            and abs(second) > material_difference)


def _grid_rows(cases, candidate_xi, thresholds):
    rows = []
    by_q = {}
    for case in cases:
        by_q.setdefault(case["Q_MW"], []).append(case)
    for q_mw, group in sorted(by_q.items()):
        by_dx = {round(case["dx_m"], 3): case for case in group}
        if not {0.5, 0.25, 0.2}.issubset(by_dx):
            raise ValueError(f"{q_mw:g} MW 网格组不完整")
        coarse, medium, fine = by_dx[0.5], by_dx[0.25], by_dx[0.2]
        reverse_limits = {
            "deltaT_peak_C": (thresholds["deltaT_peak_relative_max"]
                              * abs(fine["deltaT_peak_C"])),
            "x_peak_m": thresholds["peak_position_difference_m_max"],
            "hrr_mean_kW": thresholds["hrr_difference_max"] * abs(fine["hrr_mean_kW"]),
        }
        reverse_fields = [name for name, limit in reverse_limits.items()
                          if _obvious_reverse_divergence(
                              coarse[name], medium[name], fine[name], limit)]
        for xi in candidate_xi:
            a_key = f"A_global_xi{xi}"
            if _obvious_reverse_divergence(
                    coarse[a_key], medium[a_key], fine[a_key],
                    thresholds["A_global_difference_max"]):
                reverse_fields.append(f"A_global_xi{xi}")
            row = {
                "Q_MW": q_mw,
                "xi_h": xi,
                "coarse_chid": coarse["chid"],
                "medium_chid": medium["chid"],
                "fine_chid": fine["chid"],
                "deltaT_peak_coarse_C": coarse["deltaT_peak_C"],
                "deltaT_peak_medium_C": medium["deltaT_peak_C"],
                "deltaT_peak_fine_C": fine["deltaT_peak_C"],
                "deltaT_peak_relative_mf": _relative_difference(
                    medium["deltaT_peak_C"], fine["deltaT_peak_C"]),
                "curve_nrmse_mf": profile_nrmse(
                    medium["profile"], fine["profile"], xi * cfg.H),
                "peak_position_difference_mf_m": abs(
                    medium["x_peak_m"] - fine["x_peak_m"]),
                "A_global_medium": medium[a_key],
                "A_global_fine": fine[a_key],
                "A_global_difference_mf": abs(medium[a_key] - fine[a_key]),
                "hrr_medium_kW": medium["hrr_mean_kW"],
                "hrr_fine_kW": fine["hrr_mean_kW"],
                "hrr_relative_difference_mf": _relative_difference(
                    medium["hrr_mean_kW"], fine["hrr_mean_kW"]),
                "reverse_divergence": bool(reverse_fields),
                "reverse_divergence_fields": ";".join(sorted(set(reverse_fields))),
            }
            checks = [
                row["deltaT_peak_relative_mf"] <= thresholds["deltaT_peak_relative_max"],
                row["curve_nrmse_mf"] <= thresholds["curve_nrmse_max"],
                row["peak_position_difference_mf_m"]
                <= thresholds["peak_position_difference_m_max"],
                row["A_global_difference_mf"] <= thresholds["A_global_difference_max"],
                row["hrr_relative_difference_mf"] <= thresholds["hrr_difference_max"],
                not row["reverse_divergence"],
            ]
            row["status"] = "PASS" if all(checks) else "FAIL"
            rows.append(row)
    return rows


def _coefficient_of_variation(values):
    mean = statistics.mean(values)
    if abs(mean) <= 1e-12:
        return None
    return statistics.stdev(values) / abs(mean)


def _maximum_pairwise_nrmse(group, domain_m):
    metrics = []
    for index, first in enumerate(group):
        for second in group[index + 1:]:
            forward = profile_nrmse(first["profile"], second["profile"], domain_m)
            reverse = profile_nrmse(second["profile"], first["profile"], domain_m)
            if forward is not None and reverse is not None:
                metrics.append(max(forward, reverse))
    return max(metrics) if metrics else None


def _seed_status(deltaT_cv, curve_nrmse, thresholds):
    worst = max(deltaT_cv, curve_nrmse)
    if (deltaT_cv <= thresholds["deltaT_peak_cv_pass_max"]
            and curve_nrmse <= thresholds["curve_nrmse_pass_max"]):
        return "PASS"
    if worst <= thresholds["stop_above"]:
        return "CONDITIONAL_PASS"
    return "FAIL"


def _seed_rows(cases, candidate_xi, thresholds):
    rows = []
    by_q = {}
    for case in cases:
        by_q.setdefault(case["Q_MW"], []).append(case)
    for q_mw, group in sorted(by_q.items()):
        if len(group) != 3:
            raise ValueError(f"{q_mw:g} MW 种子组应有 3 条，实际 {len(group)} 条")
        group.sort(key=lambda row: row["rnd_seed"])
        deltaT_cv = _coefficient_of_variation(
            [case["deltaT_peak_C"] for case in group])
        for xi in candidate_xi:
            curve = _maximum_pairwise_nrmse(group, xi * cfg.H)
            rows.append({
                "Q_MW": q_mw,
                "xi_h": xi,
                "chids": ";".join(case["chid"] for case in group),
                "seeds": ";".join(str(case["rnd_seed"]) for case in group),
                "deltaT_peak_mean_C": statistics.mean(
                    case["deltaT_peak_C"] for case in group),
                "deltaT_peak_cv": deltaT_cv,
                "curve_max_pairwise_nrmse": curve,
                "status": _seed_status(deltaT_cv, curve, thresholds),
            })
    return rows


def _write_csv(path, rows):
    if not rows:
        return
    columns = list(rows[0])
    with Path(path).open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows({key: row.get(key) for key in columns} for row in rows)


def analyze(rundir, cases_path, windows_path, protocol_path, outdir):
    cases = [row for row in _read_csv(cases_path)
             if row["purpose"] in GRID_PURPOSES | SEED_PURPOSES]
    window_rows = _read_csv(windows_path)
    windows = _read_windows(windows_path)
    with Path(protocol_path).open(encoding="utf-8") as stream:
        protocol = json.load(stream)
    candidate_xi = sorted(
        protocol["thresholds"]["effective_domain_candidates_xi_h"], reverse=True)
    loaded = []
    for case in cases:
        if case["chid"] not in windows:
            raise ValueError(f"{case['chid']}: 没有 PASS 准稳态窗口")
        loaded.append(_load_case(rundir, case, windows[case["chid"]], candidate_xi))

    grid_thresholds = protocol["thresholds"]["grid"]
    seed_thresholds = protocol["thresholds"]["seed"]
    grid = _grid_rows([row for row in loaded if row["purpose"] in GRID_PURPOSES],
                      candidate_xi, grid_thresholds)
    seed_cases = [row for row in loaded if abs(row["dx_m"] - 0.25) < 1e-9]
    seed = _seed_rows(seed_cases, candidate_xi, seed_thresholds)

    passed_xi = [xi for xi in candidate_xi
                 if all(row["status"] == "PASS" for row in grid if row["xi_h"] == xi)
                 and all(row["status"] == "PASS" for row in seed if row["xi_h"] == xi)]
    decision = {
        "stage": "G2_STAGE3",
        "decision": "PASS" if passed_xi else "FAIL",
        "largest_passing_xi_h": max(passed_xi) if passed_xi else None,
        "version_policy": "USER_DIRECTED_IGNORE_VERSION_DIFFERENCE",
        "case_count": len(loaded),
        "grid_group_count": 2,
        "seed_group_count": 2,
    }

    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    case_rows = [{key: value for key, value in row.items() if key != "profile"}
                 for row in loaded]
    selected_chids = {case["chid"] for case in cases}
    _write_csv(outdir / "g2_stage3_steady_windows.csv",
               [row for row in window_rows if row.get("chid") in selected_chids])
    _write_csv(outdir / "g2_stage3_case_metrics.csv", case_rows)
    _write_csv(outdir / "g2_stage3_grid_metrics.csv", grid)
    _write_csv(outdir / "g2_stage3_seed_metrics.csv", seed)
    with (outdir / "g2_stage3_decision.json").open("w", encoding="utf-8") as stream:
        json.dump(decision, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return decision, grid, seed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rundir", default=str(PROJECT_ROOT / "runs" / "pilot"))
    parser.add_argument("--cases", default=str(PROJECT_ROOT / "config" / "pilot_cases.csv"))
    parser.add_argument("--steady-windows", required=True)
    parser.add_argument("--protocol", default=str(PROJECT_ROOT / "config" / "protocol_v1.json"))
    parser.add_argument("--outdir", default=str(PROJECT_ROOT / "reports"))
    args = parser.parse_args()
    decision, grid, seed = analyze(
        args.rundir, args.cases, args.steady_windows, args.protocol, args.outdir)
    print(json.dumps(decision, ensure_ascii=False, indent=2))
    for row in grid:
        print(f"grid {row['Q_MW']:g} MW xi={row['xi_h']}: {row['status']}")
    for row in seed:
        print(f"seed {row['Q_MW']:g} MW xi={row['xi_h']}: {row['status']}")


if __name__ == "__main__":
    main()
