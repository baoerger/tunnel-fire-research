"""G2 阶段 4：洞口余量、平移与全局对称性分析。

阶段 4 的三个偏移工况与阶段 3 的六个 0.25 m 代表工况一起接受
峰移、检测限、左右曲线和 ``A_global`` 门。``A_global`` 的置信上限
使用空间测点同步抽样的循环时间块 Bootstrap；块长取候选域内最慢
温度信号的自相关块长。原始结果仍保留在 ``runs/``。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
from pathlib import Path

from src.analysis import analyze_g2_stage3 as stage3
from src.analysis.time_average_bootstrap import _autocorr_block_len, validate_window
from src.fds import fds_io
from src.fds import tunnel_config as cfg
from src.fds.project_paths import PROJECT_ROOT


WORKING_PURPOSES = {
    "grid_low", "grid_high", "seed_repeat_low", "seed_repeat_high",
    "opening_translation",
}


def _quantile(values, probability):
    if not values:
        raise ValueError("分位数输入为空")
    ordered = sorted(values)
    index = min(len(ordered) - 1,
                max(0, math.ceil(probability * len(ordered)) - 1))
    return ordered[index]


def _temperature_frames(times, series, fire_x, window, prefix="T90"):
    """返回平均窗内的相对坐标和同步温升帧。"""
    indices = validate_window(times, window[0], window[1], min_duration=10.0)
    sensors = []
    for sensor, values in series.items():
        match = re.fullmatch(rf"{re.escape(prefix)}_(\d+)", sensor)
        if match:
            sensors.append((int(match.group(1)) / 100.0, values))
    sensors.sort(key=lambda item: item[0])
    if not sensors:
        raise ValueError(f"缺少 {prefix}_* 温度设备")

    selected_times = [times[index] for index in indices]
    relative_x = [round(x - fire_x, 8) for x, _ in sensors]
    frames = []
    for index in indices:
        row = []
        for _, values in sensors:
            value = values[index] if index < len(values) else float("nan")
            row.append(value - cfg.T_AMBIENT_C if math.isfinite(value)
                       else float("nan"))
        if all(math.isfinite(value) for value in row):
            frames.append(row)
    if len(frames) != len(indices):
        raise ValueError("平均窗内温度帧含缺测值，不能同步 Bootstrap")
    return selected_times, relative_x, frames


def _profile_asymmetry(values, relative_x, domain_m, epsilon_T=0.5):
    profile = list(zip(relative_x, values))
    return stage3.global_asymmetry(profile, domain_m, epsilon_T=epsilon_T)


def _synchronous_bootstrap(relative_x, frames, domain_m, detection_limit,
                           n_boot=2000, seed=20260821):
    """同步时间块 Bootstrap ``A_global`` 和逐点平均温升。

    同一轮对全部空间点使用相同的时间索引，避免独立抽样破坏空间相关。
    """
    if n_boot < 20:
        raise ValueError("n_boot 必须至少为 20")
    domain_indices = [index for index, x in enumerate(relative_x)
                      if abs(x) <= domain_m + 1e-9]
    if len(domain_indices) < 7:
        raise ValueError(f"xi 域内测点不足 7 个: {len(domain_indices)}")
    domain_x = [relative_x[index] for index in domain_indices]
    series_by_sensor = [
        [frame[index] for frame in frames] for index in domain_indices
    ]
    block_len = max(_autocorr_block_len(values) for values in series_by_sensor)
    block_len = max(1, min(block_len, len(frames) // 2))
    rng = random.Random(seed)
    sensor_means = [[] for _ in domain_indices]
    asymmetry = []
    n_samples = len(frames)

    for _ in range(n_boot):
        sampled = []
        while len(sampled) < n_samples:
            start = rng.randrange(n_samples)
            sampled.extend((start + offset) % n_samples
                           for offset in range(block_len))
        sampled = sampled[:n_samples]
        means = []
        for values, storage in zip(series_by_sensor, sensor_means):
            mean = sum(values[index] for index in sampled) / n_samples
            means.append(mean)
            storage.append(mean)
        value = _profile_asymmetry(means, domain_x, domain_m)
        if value is None:
            raise ValueError("候选域没有完整镜像测点对")
        asymmetry.append(value)

    point_means = [sum(values) / len(values) for values in series_by_sensor]
    point_asymmetry = _profile_asymmetry(point_means, domain_x, domain_m)
    ci_lower = [_quantile(values, 0.025) for values in sensor_means]
    active_count = sum(value > detection_limit for value in ci_lower)
    active_fraction = active_count / len(domain_indices)
    return {
        "A_global_point": point_asymmetry,
        "A_global_ci95_lower": _quantile(asymmetry, 0.025),
        "A_global_ci95_upper": _quantile(asymmetry, 0.975),
        "active_fraction": active_fraction,
        "censored_fraction": 1.0 - active_fraction,
        "domain_sensor_count": len(domain_indices),
        "block_len_samples": block_len,
        "n_samples": n_samples,
        "n_boot": n_boot,
    }


def _symmetric_nrmse(profile_a, profile_b, domain_m):
    forward = stage3.profile_nrmse(profile_a, profile_b, domain_m)
    reverse = stage3.profile_nrmse(profile_b, profile_a, domain_m)
    if forward is None or reverse is None:
        return None
    return max(forward, reverse)


def _load_working_case(rundir, case, window, candidate_xi, symmetry,
                       detection_limit, opening_buffer_h, n_boot, seed):
    metrics = stage3._load_case(rundir, case, window, candidate_xi)
    times, series, units = fds_io.read_devc(str(rundir), case["chid"])
    series = fds_io.normalize_units(series, units)
    _, relative_x, frames = _temperature_frames(
        times, series, float(case["x_fire"]), window)

    h = float(case["H"])
    length = float(case["L"])
    fire_x = float(case["x_fire"])
    opening_available_h = min(fire_x, length - fire_x) / h
    metrics["opening_available_h"] = opening_available_h
    metrics["detection_limit_C"] = detection_limit
    for xi in candidate_xi:
        domain_m = xi * h
        boot = _synchronous_bootstrap(
            relative_x, frames, domain_m, detection_limit,
            n_boot=n_boot, seed=seed + int(xi) * 1000)
        for key, value in boot.items():
            metrics[f"{key}_xi{xi}"] = value
        metrics[f"opening_required_h_xi{xi}"] = xi + opening_buffer_h
        metrics[f"opening_pass_xi{xi}"] = (
            opening_available_h + 1e-12 >= xi + opening_buffer_h)
        metrics[f"detection_pass_xi{xi}"] = (
            boot["censored_fraction"] <= 0.5 + 1e-12)
        checks = [
            abs(metrics["peak_shift_m"]) <= symmetry["peak_shift_m_max"],
            boot["A_global_point"] <= symmetry["A_global_point_max"],
            boot["A_global_ci95_upper"]
            <= symmetry["A_global_ci95_upper_max"],
            metrics[f"left_right_nrmse_xi{xi}"]
            <= symmetry["left_right_nrmse_max"],
            metrics[f"opening_pass_xi{xi}"],
            metrics[f"detection_pass_xi{xi}"],
        ]
        metrics[f"status_xi{xi}"] = "PASS" if all(checks) else "FAIL"
    return metrics


def _translation_rows(stage4_cases, candidate_xi, symmetry):
    rows = []
    center = min(stage4_cases, key=lambda row: abs(row["x_fire_m"] - 50.0))
    for case in sorted(stage4_cases, key=lambda row: row["x_fire_m"]):
        if case is center:
            continue
        for xi in candidate_xi:
            nrmse = _symmetric_nrmse(
                case["profile"], center["profile"], xi * cfg.H)
            peak_relative_difference = abs(
                case["deltaT_peak_C"] - center["deltaT_peak_C"]
            ) / abs(center["deltaT_peak_C"])
            relative_peak_position_difference = abs(
                case["peak_shift_m"] - center["peak_shift_m"])
            status = "PASS" if (
                nrmse is not None
                and nrmse <= symmetry["left_right_nrmse_max"]
                and relative_peak_position_difference
                <= symmetry["peak_shift_m_max"]
            ) else "FAIL"
            rows.append({
                "reference_chid": center["chid"],
                "comparison_chid": case["chid"],
                "x_fire_reference_m": center["x_fire_m"],
                "x_fire_comparison_m": case["x_fire_m"],
                "xi_h": xi,
                "relative_curve_symmetric_nrmse": nrmse,
                "deltaT_peak_relative_difference": peak_relative_difference,
                "relative_peak_position_difference_m":
                    relative_peak_position_difference,
                "status": status,
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


def analyze(rundir, cases_path, stage3_windows_path, stage4_windows_path,
            protocol_path, outdir, n_boot=2000, seed=20260821):
    cases = [row for row in stage3._read_csv(cases_path)
             if row["purpose"] in WORKING_PURPOSES
             and abs(float(row["dx"]) - 0.25) < 1e-9]
    stage4_cases = [row for row in cases
                    if row["purpose"] == "opening_translation"]
    if len(cases) != 9 or len(stage4_cases) != 3:
        raise ValueError(
            f"工作网格代表工况应为 9 条且阶段 4 为 3 条，实际 {len(cases)}/{len(stage4_cases)}")

    windows = stage3._read_windows(stage3_windows_path)
    windows.update(stage3._read_windows(stage4_windows_path))
    with Path(protocol_path).open(encoding="utf-8") as stream:
        protocol = json.load(stream)
    candidate_xi = sorted(
        protocol["thresholds"]["effective_domain_candidates_xi_h"],
        reverse=True)
    symmetry = protocol["thresholds"]["symmetry"]
    detection_limit = float(
        protocol["g2_pilot"]["stage1_background_evidence"]["detection_limit_C"])
    opening_buffer_h = 3.0

    loaded = []
    for case_index, case in enumerate(cases):
        if case["chid"] not in windows:
            raise ValueError(f"{case['chid']}: 没有 PASS 准稳态窗口")
        metrics = _load_working_case(
            rundir, case, windows[case["chid"]], candidate_xi, symmetry,
            detection_limit, opening_buffer_h, n_boot,
            seed + case_index * 100000)
        metrics["x_fire_m"] = float(case["x_fire"])
        loaded.append(metrics)

    stage4_loaded = [row for row in loaded
                     if row["purpose"] == "opening_translation"]
    translation = _translation_rows(stage4_loaded, candidate_xi, symmetry)
    domain_rows = []
    passed_xi = []
    for xi in candidate_xi:
        case_failures = [row["chid"] for row in loaded
                         if row[f"status_xi{xi}"] != "PASS"]
        translation_failures = [row["comparison_chid"] for row in translation
                                if row["xi_h"] == xi
                                and row["status"] != "PASS"]
        status = "PASS" if not case_failures and not translation_failures else "FAIL"
        if status == "PASS":
            passed_xi.append(xi)
        domain_rows.append({
            "xi_h": xi,
            "domain_m": xi * cfg.H,
            "case_count": len(loaded),
            "case_failures": ";".join(case_failures),
            "translation_failures": ";".join(translation_failures),
            "status": status,
        })

    decision = {
        "stage": "G2_STAGE4",
        "decision": ("PASS_WITH_USER_ACCEPTED_GRID_UNCERTAINTY"
                     if passed_xi else "FAIL"),
        "largest_passing_xi_h": max(passed_xi) if passed_xi else None,
        "working_dx_m": 0.25,
        "peak_grid_uncertainty_fraction": 0.13,
        "strict_grid_independence_claim_allowed": False,
        "opening_buffer_h": opening_buffer_h,
        "detection_limit_C": detection_limit,
        "working_case_count": len(loaded),
        "stage4_new_case_count": len(stage4_loaded),
        "bootstrap_replicates": n_boot,
        "version_policy": "USER_DIRECTED_IGNORE_VERSION_DIFFERENCE",
    }

    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stage4_window_rows = stage3._read_csv(stage4_windows_path)
    _write_csv(outdir / "g2_stage4_steady_windows.csv", stage4_window_rows)
    case_rows = [{key: value for key, value in row.items()
                  if key != "profile"} for row in loaded]
    _write_csv(outdir / "g2_stage4_case_metrics.csv", case_rows)
    _write_csv(outdir / "g2_stage4_translation_metrics.csv", translation)
    _write_csv(outdir / "g2_stage4_domain_metrics.csv", domain_rows)
    with (outdir / "g2_stage4_decision.json").open(
            "w", encoding="utf-8") as stream:
        json.dump(decision, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return decision, loaded, translation, domain_rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rundir", default=str(PROJECT_ROOT / "runs" / "pilot"))
    parser.add_argument("--cases", default=str(PROJECT_ROOT / "config" / "pilot_cases.csv"))
    parser.add_argument("--stage3-windows", default=str(
        PROJECT_ROOT / "reports" / "g2_stage3_steady_windows.csv"))
    parser.add_argument("--stage4-windows", required=True)
    parser.add_argument("--protocol", default=str(
        PROJECT_ROOT / "config" / "protocol_v1.json"))
    parser.add_argument("--outdir", default=str(PROJECT_ROOT / "reports"))
    parser.add_argument("--n-boot", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20260821)
    args = parser.parse_args()
    decision, _, translation, domains = analyze(
        args.rundir, args.cases, args.stage3_windows, args.stage4_windows,
        args.protocol, args.outdir, args.n_boot, args.seed)
    print(json.dumps(decision, ensure_ascii=False, indent=2))
    for row in domains:
        print(f"domain xi={row['xi_h']}: {row['status']}")
    for row in translation:
        print(f"translation {row['comparison_chid']} xi={row['xi_h']}: "
              f"{row['status']}")


if __name__ == "__main__":
    main()
