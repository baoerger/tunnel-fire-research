"""G4 开发集按 D1→D2→D3 科学接收并构建不可变数据库。

原始 FDS 文件只从 ``runs/development`` 读取，派生表写入受 Git
忽略的 ``derived/development``；``reports`` 中只保留质量摘要、批次门和
派生表哈希。锁箱路径不在本模块的任何读取入口中。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import random
import re
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from src.analysis.analyze_g2_stage3 import (
    global_asymmetry,
    left_right_nrmse,
    profile_nrmse,
)
from src.analysis.censoring_peak import estimate_peak
from src.analysis.quasi_steady_detect import detect
from src.analysis.time_average_bootstrap import _autocorr_block_len, validate_window
from src.fds import fds_io
from src.fds import tunnel_config as cfg
from src.fds.project_paths import PROJECT_ROOT


BATCH_ORDER = ("D1", "D2", "D3")
WINDOW_ID = "QS60_V1"
DETECTION_LIMIT_K = 0.5
XI_H = 5.0
DOMAIN_M = XI_H * cfg.H
N_BOOT_DEFAULT = 2000


def _read_csv(path):
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        return [
            {key: (value or "").strip() for key, value in row.items()}
            for row in csv.DictReader(stream)
        ]


def _write_csv(path, rows, columns=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if columns is None:
        columns = list(rows[0]) if rows else []
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows({key: row.get(key, "") for key in columns} for row in rows)


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _combined_hash(root, paths):
    root = Path(root).resolve()
    digest = hashlib.sha256()
    for path in sorted({Path(path).resolve() for path in paths}, key=lambda p: p.as_posix()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(_sha256(path)))
        digest.update(b"\0")
    return digest.hexdigest()


def _quantile(values, probability):
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        raise ValueError("分位数输入为空")
    position = probability * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _mean_in_window(times, values, window):
    selected = [
        value for time, value in zip(times, values)
        if window[0] <= time <= window[1] and math.isfinite(value)
    ]
    if not selected:
        raise ValueError("平均窗内没有有限值")
    return statistics.fmean(selected)


def _parse_wall_clock(out_path):
    text = Path(out_path).read_text(encoding="utf-8", errors="ignore")
    match = re.search(r"Total Elapsed Wall Clock Time \(s\):\s*([0-9.eE+-]+)", text)
    return float(match.group(1)) if match else None


def _parse_mpi_processes(attempt_dir):
    for path in sorted(Path(attempt_dir).glob("*.err")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        match = re.search(r"Number of MPI Processes:\s*(\d+)", text)
        if match:
            return int(match.group(1))
    return None


def _scheduler_log_status(attempt_dir):
    patterns = re.compile(
        r"fatal|segmentation|\baborted\b|\bkilled\b|out of memory|\boom\b|slurmstepd",
        re.IGNORECASE,
    )
    matches = []
    for path in sorted(Path(attempt_dir).glob("*.err")):
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if patterns.search(line):
                matches.append(f"{path.name}:{line.strip()}")
    return ("PASS", "") if not matches else ("FAIL", matches[0])


def _attempt_dir(repo, case):
    return (
        Path(repo) / "runs" / "development" / case["chid"] / "attempts"
        / case["job_attempt_id"]
    )


def _identity_rows(repo, cases, handoff, quality, formal_fds_version):
    handoff_by_chid = {row["run_chid"]: row for row in handoff}
    quality_by_chid = {row["chid"]: row for row in quality}
    expected = {case["chid"] for case in cases}
    if set(handoff_by_chid) != expected or set(quality_by_chid) != expected:
        raise ValueError("G3 交接清单、G4 质量表与 27 个开发 CHID 不闭合")

    rows = []
    for case in cases:
        chid = case["chid"]
        registered = handoff_by_chid[chid]
        checked = quality_by_chid[chid]
        attempt_dir = _attempt_dir(repo, case)
        source = Path(repo) / "fds_inputs" / "development" / "g3" / f"{chid}.fds"
        attempt_input = attempt_dir / f"{chid}.fds"
        for path in (source, attempt_input):
            if not path.is_file():
                raise FileNotFoundError(f"{chid}: 缺少输入 {path}")
        expected_hash = registered["attempt_input_sha256"].lower()
        source_hash = _sha256(source)
        attempt_hash = _sha256(attempt_input)
        scheduler_status, scheduler_issue = _scheduler_log_status(attempt_dir)
        status = "PASS" if (
            source_hash == expected_hash
            and attempt_hash == expected_hash
            and checked["status"] == "PASS"
            and scheduler_status == "PASS"
        ) else "FAIL"
        rows.append({
            "batch": case["batch"],
            "run_chid": chid,
            "job_attempt_id": case["job_attempt_id"],
            "run_core_sha256": registered["run_core_sha256"].lower(),
            "attempt_input_sha256": attempt_hash,
            "registered_attempt_input_sha256": expected_hash,
            "input_hash_match": source_hash == expected_hash == attempt_hash,
            "fds_version_actual": checked["fds_version"],
            "formal_version_match": checked["fds_version"] == formal_fds_version,
            "version_policy": "USER_DIRECTED_IGNORE_VERSION_DIFFERENCE",
            "quality_status": checked["status"],
            "scheduler_log_status": scheduler_status,
            "scheduler_log_issue": scheduler_issue,
            "out_completed_successfully": checked["out_completed_successfully"],
            "devc_t_final_s": checked["devc_t_final_s"],
            "hrr_t_final_s": checked["hrr_t_final_s"],
            "hrr_closure_rel_error_tail": checked["hrr_closure_rel_error"],
            "full_field_present": checked["full_field_present"],
            "allocated_cores": _parse_mpi_processes(attempt_dir),
            "wall_clock_s": _parse_wall_clock(attempt_dir / f"{chid}.out"),
            "status": status,
        })
    return rows


def _temperature_frames(attempt_dir, case, window):
    chid = case["chid"]
    times, series, units = fds_io.read_devc(str(attempt_dir), chid)
    if times is None:
        raise ValueError(f"{chid}: 缺少 DEVC")
    series = fds_io.normalize_units(series, units)
    indices = validate_window(times, window[0], window[1], min_duration=50.0)
    sensors = []
    for key, values in series.items():
        match = re.fullmatch(r"T90_(\d+)", key)
        if match:
            sensors.append((int(match.group(1)) / 100.0, key, values))
    sensors.sort()
    if len(sensors) < 40:
        raise ValueError(f"{chid}: T90 测点只有 {len(sensors)} 个")
    xs = [row[0] for row in sensors]
    frames = []
    for index in indices:
        frame = [values[index] - cfg.T_AMBIENT_C for _, _, values in sensors]
        if not all(math.isfinite(value) for value in frame):
            raise ValueError(f"{chid}: 稳态窗内 T90 含缺失值")
        frames.append(frame)
    return [times[index] for index in indices], xs, frames, series, times


def _circular_bootstrap_profiles(frames, xs, fire_x, n_boot, seed):
    if n_boot < 20:
        raise ValueError("Bootstrap 次数必须至少为 20")
    domain_indices = [i for i, x in enumerate(xs) if abs(x - fire_x) <= DOMAIN_M + 1e-9]
    block_len = max(
        _autocorr_block_len([frame[index] for frame in frames])
        for index in domain_indices
    )
    block_len = max(1, min(block_len, len(frames) // 2))
    rng = random.Random(seed)
    profiles = []
    n = len(frames)
    for _ in range(n_boot):
        sampled = []
        while len(sampled) < n:
            start = rng.randrange(n)
            sampled.extend((start + offset) % n for offset in range(block_len))
        sampled = sampled[:n]
        profiles.append([
            statistics.fmean(frames[index][sensor] for index in sampled)
            for sensor in range(len(xs))
        ])
    return profiles, block_len


def _relative_profile(xs, values, fire_x):
    return sorted((round(x - fire_x, 8), value) for x, value in zip(xs, values))


def _peak_bootstrap(xs, profiles):
    estimates = []
    for profile in profiles:
        try:
            estimates.append(estimate_peak(xs, profile, method="quadratic"))
        except ValueError:
            continue
    if len(estimates) < 0.8 * len(profiles):
        raise ValueError(
            f"峰值 Bootstrap 成功率低于 80%: {len(estimates)}/{len(profiles)}"
        )
    return {
        "n_success": len(estimates),
        "success_rate": len(estimates) / len(profiles),
        "x_peak_ci95_low_m": _quantile([row["xp"] for row in estimates], 0.025),
        "x_peak_ci95_high_m": _quantile([row["xp"] for row in estimates], 0.975),
        "deltaT_peak_ci95_low_K": _quantile([row["delta_Tp"] for row in estimates], 0.025),
        "deltaT_peak_ci95_high_K": _quantile([row["delta_Tp"] for row in estimates], 0.975),
        "peak_plateau_width_median_m": statistics.median(
            row["platform_width_m"] for row in estimates
        ),
    }


def _load_case_science(repo, case, window, n_boot, seed):
    chid = case["chid"]
    attempt_dir = _attempt_dir(repo, case)
    selected_times, xs, frames, devc, devc_times = _temperature_frames(
        attempt_dir, case, window
    )
    fire_x = float(case["x_fire"])
    mean_profile = [statistics.fmean(row[index] for row in frames) for index in range(len(xs))]
    boot_profiles, block_len = _circular_bootstrap_profiles(
        frames, xs, fire_x, n_boot, seed
    )
    point_ci_low = [
        _quantile([profile[index] for profile in boot_profiles], 0.025)
        for index in range(len(xs))
    ]
    point_ci_high = [
        _quantile([profile[index] for profile in boot_profiles], 0.975)
        for index in range(len(xs))
    ]
    peak = estimate_peak(xs, mean_profile, method="quadratic")
    peak_boot = _peak_bootstrap(xs, boot_profiles)
    relative = _relative_profile(xs, mean_profile, fire_x)
    a_point = global_asymmetry(relative, DOMAIN_M)
    lr_nrmse = left_right_nrmse(relative, DOMAIN_M)
    a_boot = [
        global_asymmetry(_relative_profile(xs, profile, fire_x), DOMAIN_M)
        for profile in boot_profiles
    ]
    if a_point is None or lr_nrmse is None or any(value is None for value in a_boot):
        raise ValueError(f"{chid}: xi_h=5 缺少镜像测点")

    hrr_times, hrr_series, hrr_units = fds_io.read_hrr(str(attempt_dir), chid)
    hrr_series = fds_io.normalize_units(hrr_series, hrr_units)
    if hrr_times is None or "HRR" not in hrr_series or "Q_RADI" not in hrr_series:
        raise ValueError(f"{chid}: HRR/Q_RADI 不完整")
    q_actual = _mean_in_window(hrr_times, hrr_series["HRR"], window)
    q_radi = _mean_in_window(hrr_times, hrr_series["Q_RADI"], window)
    qc = q_actual + q_radi
    target = float(case["Q"]) * 1000.0
    hrr_closure = abs(q_actual - target) / target

    velocity_keys = [key for key in devc if re.fullmatch(r"U95_\d+", key)]
    max_abs_u = max(
        abs(_mean_in_window(devc_times, devc[key], window)) for key in velocity_keys
    )
    curve_rows = []
    domain_roles = []
    for index, (x, mean, ci_low, ci_high) in enumerate(
        zip(xs, mean_profile, point_ci_low, point_ci_high)
    ):
        xi = abs(x - fire_x) / cfg.H
        if xi > XI_H + 1e-9:
            role = "QC_ONLY"
        elif ci_low > DETECTION_LIMIT_K:
            role = "ACTIVE"
        else:
            role = "CENSORED_LOW_SIGNAL"
        if xi <= XI_H + 1e-9:
            domain_roles.append(role)
        curve_rows.append({
            "physical_case_id": case["physical_case_id"],
            "parent_case_id": case["parent_case_id"],
            "run_chid": chid,
            "job_attempt_id": case["job_attempt_id"],
            "window_id": WINDOW_ID,
            "batch": case["batch"],
            "x_coord_m": x,
            "y_coord_m": cfg.W / 2.0,
            "z_coord_m": 0.9 * cfg.H,
            "xf_actual_m": fire_x,
            "xi": xi,
            "temp_mean_C": mean + cfg.T_AMBIENT_C,
            "deltaT_mean_K": mean,
            "deltaT_ci95_low_K": ci_low,
            "deltaT_ci95_high_K": ci_high,
            "Theta": mean / peak["delta_Tp"],
            "point_role": role,
            "detection_limit_K": DETECTION_LIMIT_K,
            "bootstrap_n": n_boot,
            "bootstrap_block_len_samples": block_len,
        })

    censored_fraction = domain_roles.count("CENSORED_LOW_SIGNAL") / len(domain_roles)
    symmetry_pass = (
        abs(peak["xp"] - fire_x) <= 0.625 + 1e-12
        and a_point <= 0.10 + 1e-12
        and _quantile(a_boot, 0.975) <= 0.15 + 1e-12
        and lr_nrmse <= 0.10 + 1e-12
    )
    metrics = {
        "physical_case_id": case["physical_case_id"],
        "parent_case_id": case["parent_case_id"],
        "run_chid": chid,
        "job_attempt_id": case["job_attempt_id"],
        "window_id": WINDOW_ID,
        "batch": case["batch"],
        "Q_requested_MW": float(case["Q"]),
        "Q_actual_kW": q_actual,
        "Q_radi_kW": q_radi,
        "Qc_kW": qc,
        "Q_star": cfg.q_star_convective(q_actual * 1000.0),
        "Qc_star": cfg.q_star_convective(qc * 1000.0),
        "Df_discrete_m": float(case["Df"]),
        "xf_actual_m": fire_x,
        "window_start_s": window[0],
        "window_end_s": window[1],
        "quasi_steady_status": "PASS",
        "deltaT_max_K": peak["delta_Tp"],
        "deltaT_peak_ci95_low_K": peak_boot["deltaT_peak_ci95_low_K"],
        "deltaT_peak_ci95_high_K": peak_boot["deltaT_peak_ci95_high_K"],
        "x_peak_m": peak["xp"],
        "x_peak_ci95_low_m": peak_boot["x_peak_ci95_low_m"],
        "x_peak_ci95_high_m": peak_boot["x_peak_ci95_high_m"],
        "peak_shift_m": peak["xp"] - fire_x,
        "peak_plateau_width_m": peak["platform_width_m"],
        "peak_plateau_width_bootstrap_median_m": peak_boot[
            "peak_plateau_width_median_m"
        ],
        "A_global": a_point,
        "A_global_ci95_lower": _quantile(a_boot, 0.025),
        "A_global_ci95_upper": _quantile(a_boot, 0.975),
        "left_right_nrmse": lr_nrmse,
        "symmetry_status": "PASS" if symmetry_pass else "FAIL",
        "active_fraction": domain_roles.count("ACTIVE") / len(domain_roles),
        "censored_fraction": censored_fraction,
        "inverse_eligibility": (
            "CENSORED_DOMINANT" if censored_fraction > 0.5
            else "ELIGIBLE_SUBJECT_TO_G5_G6"
        ),
        "max_abs_mean_U95_m_s": max_abs_u,
        "hrr_closure_rel_error_window": hrr_closure,
        "bootstrap_n": n_boot,
        "bootstrap_success_rate": peak_boot["success_rate"],
        "bootstrap_block_len_samples": block_len,
        "quality_status": "PASS" if (
            symmetry_pass and hrr_closure <= 0.10 and peak_boot["success_rate"] >= 0.8
        ) else "FAIL",
        "exclusion_reason": "",
    }
    return metrics, curve_rows, relative


def _steady_row(case, start, end, info):
    return {
        "run_chid": case["chid"],
        "job_attempt_id": case["job_attempt_id"],
        "batch": case["batch"],
        "window_id": WINDOW_ID,
        "status": info["status"],
        "reason": info["reason"],
        "t_steady_start_s": start,
        "t_steady_end_s": end,
        "avg_t0_s": start,
        "avg_t1_s": end,
        "trend_window_s": info["config"]["window_s"],
        "smooth_s": info["config"]["smooth_s"],
        "mean_window_s": info["config"]["min_steady"],
        "flow_criterion": info["flow_criterion"],
        "enthalpy_flux_criterion": info["enthalpy_criterion"],
    }


def _heskestad_flame_height(q_mw, diameter_m):
    return -1.02 * diameter_m + 0.235 * (q_mw * 1000.0) ** 0.4


def _heavy_field_evidence(repo, case, window):
    try:
        import fdsreader
        import numpy as np
    except ModuleNotFoundError as exc:
        raise RuntimeError("D1 重场接收需要 fdsreader 和 numpy") from exc

    logging.getLogger().setLevel(logging.ERROR)
    chid = case["chid"]
    attempt_dir = _attempt_dir(repo, case)
    simulation = fdsreader.Simulation(str(attempt_dir / f"{chid}.smv"))
    matches = [
        item for item in simulation.slices
        if item.quantity.name.upper() == "TEMPERATURE" and item.orientation == 2
    ]
    if len(matches) != 1:
        raise ValueError(f"{chid}: 中心线 PBY 温度切片数为 {len(matches)}")
    field = matches[0]
    values = field.to_global(masked=False)
    coordinates = field.get_coordinates(ignore_cell_centered=False)
    x = np.asarray(coordinates["x"], dtype=float)
    z = np.asarray(coordinates["z"], dtype=float)
    times = np.asarray(field.times, dtype=float)
    time_indices = np.where((times >= window[0]) & (times <= window[1]))[0]
    half_side = float(case["burner_side_requested_m"]) / 2.0
    x_indices = np.where(np.abs(x - float(case["x_fire"])) <= half_side + 0.01)[0]
    ceiling_indices = np.where(z >= 0.9 * cfg.H - 1e-9)[0]
    vertical_indices = np.where(z >= 0.25)[0]
    if min(len(time_indices), len(x_indices), len(ceiling_indices), len(vertical_indices)) == 0:
        raise ValueError(f"{chid}: D1 切片不覆盖稳态窗/火源/顶棚")
    near_ceiling = values[np.ix_(time_indices, x_indices, ceiling_indices)]
    time_mean_vertical = np.mean(
        values[np.ix_(time_indices, x_indices, vertical_indices)], axis=0
    )
    vertical_path_min = float(np.min(np.max(time_mean_vertical, axis=0)))
    near_ceiling_mean = float(np.mean(near_ceiling))
    diameter = float(case["Df"])
    flame_height = _heskestad_flame_height(float(case["Q"]), diameter)
    # 200 C 只是“高温通道连通顶棚”的场诊断阈值，不作为公式拟合参数。
    consistent = (
        near_ceiling_mean >= 200.0
        and vertical_path_min >= 200.0
        and flame_height >= cfg.H
    )
    row = {
        "run_chid": chid,
        "batch": case["batch"],
        "Q_MW": float(case["Q"]),
        "Df_discrete_m": diameter,
        "x_fire_m": float(case["x_fire"]),
        "field_time_count_in_window": len(time_indices),
        "centerplane_temperature_slice_count": len(matches),
        "near_ceiling_burner_mean_C": near_ceiling_mean,
        "near_ceiling_burner_p95_C": float(np.quantile(near_ceiling, 0.95)),
        "vertical_path_min_of_time_mean_max_C": vertical_path_min,
        "diagnostic_temperature_threshold_C": 200.0,
        "heskestad_flame_height_m": flame_height,
        "heskestad_margin_above_ceiling_m": flame_height - cfg.H,
        "classification": (
            "CEILING_IMPINGING_OR_NEAR_IMPINGING" if consistent
            else "FLAME_REGIME_AMBIGUOUS"
        ),
        "status": "PASS" if consistent else "FAIL",
    }
    field.clear_cache()
    return row


def _seed_rows(loaded, profiles, thresholds):
    groups = defaultdict(list)
    for row in loaded:
        groups[row["physical_case_id"]].append(row)
    rows = []
    for physical_case_id, group in sorted(groups.items()):
        if len(group) < 3:
            continue
        group.sort(key=lambda item: item["run_chid"])
        peaks = [item["deltaT_max_K"] for item in group]
        peak_cv = statistics.stdev(peaks) / statistics.fmean(peaks)
        pairwise = []
        for index, first in enumerate(group):
            for second in group[index + 1:]:
                forward = profile_nrmse(
                    profiles[first["run_chid"]], profiles[second["run_chid"]], DOMAIN_M
                )
                reverse = profile_nrmse(
                    profiles[second["run_chid"]], profiles[first["run_chid"]], DOMAIN_M
                )
                pairwise.append(max(forward, reverse))
        curve = max(pairwise)
        worst = max(peak_cv, curve)
        if (
            peak_cv <= thresholds["deltaT_peak_cv_pass_max"]
            and curve <= thresholds["curve_nrmse_pass_max"]
        ):
            status = "PASS"
        elif worst <= thresholds["stop_above"]:
            status = "CONDITIONAL_PASS"
        else:
            status = "FAIL"
        rows.append({
            "physical_case_id": physical_case_id,
            "run_chids": ";".join(item["run_chid"] for item in group),
            "run_count": len(group),
            "deltaT_peak_mean_K": statistics.fmean(peaks),
            "deltaT_peak_cv": peak_cv,
            "curve_max_pairwise_nrmse": curve,
            "status": status,
        })
    return rows


def _translation_rows(cases, loaded, profiles):
    by_chid = {row["run_chid"]: row for row in loaded}
    main_seed = [case for case in cases if case["rnd_seed"] == "104729"]
    groups = defaultdict(list)
    for case in main_seed:
        groups[case["parent_case_id"]].append(case)
    rows = []
    for parent, group in sorted(groups.items()):
        if len(group) < 3:
            continue
        centers = [case for case in group if math.isclose(float(case["x_fire"]), 50.0)]
        if len(centers) != 1:
            raise ValueError(f"{parent}: 平移组中心工况不唯一")
        center = centers[0]
        center_metrics = by_chid[center["chid"]]
        for case in sorted(group, key=lambda item: float(item["x_fire"])):
            if case is center:
                continue
            metrics = by_chid[case["chid"]]
            forward = profile_nrmse(
                profiles[case["chid"]], profiles[center["chid"]], DOMAIN_M
            )
            reverse = profile_nrmse(
                profiles[center["chid"]], profiles[case["chid"]], DOMAIN_M
            )
            curve = max(forward, reverse)
            peak_shift_difference = abs(
                metrics["peak_shift_m"] - center_metrics["peak_shift_m"]
            )
            rows.append({
                "parent_case_id": parent,
                "reference_chid": center["chid"],
                "comparison_chid": case["chid"],
                "x_fire_reference_m": 50.0,
                "x_fire_comparison_m": float(case["x_fire"]),
                "relative_curve_symmetric_nrmse": curve,
                "relative_peak_shift_difference_m": peak_shift_difference,
                "status": "PASS" if curve <= 0.10 and peak_shift_difference <= 0.625 else "FAIL",
            })
    return rows


def _case_master_rows(cases):
    unique = {}
    for case in cases:
        physical = case["physical_case_id"]
        unique.setdefault(physical, case)
    rows = []
    for physical, case in sorted(unique.items()):
        h_eff = cfg.H
        area = float(case["Af_requested_m2"])
        rows.append({
            "physical_case_id": physical,
            "parent_case_id": case["parent_case_id"],
            "subset": case["subset"],
            "purpose": case["purpose"],
            "Q_requested_MW": float(case["Q"]),
            "Df_discrete_m": float(case["Df"]),
            "Af_discrete_m2": area,
            "dx_m": float(case["dx"]),
            "L_m": float(case["L"]),
            "W_m": float(case["W"]),
            "H_m": float(case["H"]),
            "H_eff_m": h_eff,
            "xf_actual_m": float(case["x_fire"]),
            "yf_actual_m": cfg.W / 2.0,
            "T_ambient_C": cfg.T_AMBIENT_C,
            "T0_K": cfg.T_AMBIENT_K,
            "lambda_minus": float(case["x_fire"]) / h_eff,
            "lambda_plus": (float(case["L"]) - float(case["x_fire"])) / h_eff,
            "beta": float(case["W"]) / (2.0 * h_eff),
            "AR": float(case["W"]) / h_eff,
            "b_f_m": math.sqrt(area / math.pi),
            "phi_f": math.sqrt(area / math.pi) / h_eff,
            "sensor_profile": case["sensor_profile"],
            "mesh_profile": case["mesh_profile"],
            "status": "ACCEPTED_G4_DEVELOPMENT",
        })
    return rows


def _write_chain(repo, case, identity, metrics):
    run_root = Path(repo) / "runs" / "development" / case["chid"]
    attempt_dir = _attempt_dir(repo, case)
    # fdsreader 会在首次解析时产生 ``.pickle`` 缓存。它不是服务器
    # 回传的 FDS 原始证据，不得进入 raw-output 哈希。
    raw_files = [
        path for path in attempt_dir.iterdir()
        if path.is_file() and path.suffix.lower() != ".pickle"
    ]
    accepted = {
        "schema_version": "1.0",
        "run_chid": case["chid"],
        "run_core_sha256": identity["run_core_sha256"],
        "accepted_job_attempt_ids": [case["job_attempt_id"]],
        "attempts": [{
            "job_attempt_id": case["job_attempt_id"],
            "attempt_input_sha256": identity["attempt_input_sha256"],
            "restart_parent_job_attempt_id": None,
            "t_start_s": 0.0,
            "t_end_s": float(case["T_end"]),
            "fds_version_actual": identity["fds_version_actual"],
            "quality_status": identity["quality_status"],
        }],
        "time_continuous": True,
        "branch_count": 1,
        "accepted_window_id": WINDOW_ID,
        "accepted_window_s": [metrics["window_start_s"], metrics["window_end_s"]],
        "raw_output_combined_sha256": _combined_hash(attempt_dir, raw_files),
        "status": "ACCEPTED_G4_DEVELOPMENT",
    }
    manifest = {
        "schema_version": "1.0",
        "run_chid": case["chid"],
        "physical_case_id": case["physical_case_id"],
        "parent_case_id": case["parent_case_id"],
        "batch": case["batch"],
        "accepted_chain": "accepted_chain.json",
        "attempt_index": [{
            "job_attempt_id": case["job_attempt_id"],
            "relative_path": f"attempts/{case['job_attempt_id']}",
            "status": "ACCEPTED",
        }],
    }
    _write_json(run_root / "accepted_chain.json", accepted)
    _write_json(run_root / "run_manifest.json", manifest)
    return {
        "run_chid": case["chid"],
        "accepted_chain_sha256": _sha256(run_root / "accepted_chain.json"),
        "run_manifest_sha256": _sha256(run_root / "run_manifest.json"),
    }


def analyze(repo, n_boot=N_BOOT_DEFAULT, seed=20260821):
    repo = Path(repo).resolve()
    cases = _read_csv(repo / "config" / "development_cases.csv")
    if len(cases) != 27 or {row["batch"] for row in cases} != set(BATCH_ORDER):
        raise ValueError("开发集必须是冻结的 D1/D2/D3=5/6/16，共 27 次运行")
    handoff = _read_csv(repo / "reports" / "g3_development_run_handoff.csv")
    quality = _read_csv(repo / "reports" / "g4_development_result_check.csv")
    protocol = json.loads((repo / "config" / "protocol_v1.json").read_text(encoding="utf-8"))
    identities = _identity_rows(
        repo, cases, handoff, quality, protocol["fds"]["fds_version_selected"]
    )
    identity_by_chid = {row["run_chid"]: row for row in identities}

    windows = []
    metrics = []
    curve_points = []
    profiles = {}
    batch_gates = []
    flame_rows = []
    prior_pass = True
    for batch_index, batch in enumerate(BATCH_ORDER):
        batch_cases = [case for case in cases if case["batch"] == batch]
        batch_metrics = []
        for case_index, case in enumerate(batch_cases):
            identity = identity_by_chid[case["chid"]]
            if identity["status"] != "PASS":
                raise ValueError(f"{case['chid']}: 技术接收未通过，不得进入 {batch}")
            attempt_dir = _attempt_dir(repo, case)
            start, end, info = detect(
                case["chid"], str(attempt_dir), window_s=90.0, smooth_s=15.0,
                min_steady=60.0, thr_hrr=0.15, thr_tmax_slope=0.15,
                thr_rep_slope=0.15, thr_backflow_slope=0.05,
                thr_enthalpy_slope=0.05,
            )
            windows.append(_steady_row(case, start, end, info))
            if info["status"] != "PASS":
                raise ValueError(f"{case['chid']}: NO_QUASI_STEADY_WINDOW")
            item, points, profile = _load_case_science(
                repo, case, (start, end), n_boot,
                seed + batch_index * 1_000_000 + case_index * 10_000,
            )
            metrics.append(item)
            batch_metrics.append(item)
            curve_points.extend(points)
            profiles[case["chid"]] = profile
            if batch == "D1":
                flame_rows.append(_heavy_field_evidence(repo, case, (start, end)))

        flame_pass = batch != "D1" or all(row["status"] == "PASS" for row in flame_rows)
        batch_pass = (
            prior_pass
            and all(row["quality_status"] == "PASS" for row in batch_metrics)
            and flame_pass
        )
        batch_gates.append({
            "batch": batch,
            "expected_count": {"D1": 5, "D2": 6, "D3": 16}[batch],
            "accepted_count": len(batch_metrics),
            "technical_pass_count": sum(
                identity_by_chid[row["run_chid"]]["status"] == "PASS"
                for row in batch_metrics
            ),
            "steady_pass_count": sum(row["quasi_steady_status"] == "PASS" for row in batch_metrics),
            "science_pass_count": sum(row["quality_status"] == "PASS" for row in batch_metrics),
            "prior_batch_gate": "PASS" if prior_pass else "FAIL",
            "heavy_flame_regime_gate": (
                "PASS" if flame_pass else "FAIL"
            ) if batch == "D1" else "NOT_APPLICABLE",
            "status": "PASS" if batch_pass else "FAIL",
        })
        prior_pass = batch_pass
        if not batch_pass:
            raise ValueError(f"{batch} 科学接收失败，后续批次保持隔离")

    seed_rows = _seed_rows(metrics, profiles, protocol["thresholds"]["seed"])
    translation_rows = _translation_rows(cases, metrics, profiles)
    seed_gate = all(row["status"] in {"PASS", "CONDITIONAL_PASS"} for row in seed_rows)
    translation_gate = all(row["status"] == "PASS" for row in translation_rows)
    if not seed_gate or not translation_gate:
        raise ValueError(
            f"开发集重复/平移门失败: seed={seed_gate}, translation={translation_gate}"
        )

    case_master = _case_master_rows(cases)
    exclusions = [
        {
            "run_chid": row["run_chid"],
            "stage": "G4",
            "status": "EXCLUDED",
            "reason": row["exclusion_reason"],
        }
        for row in metrics if row["quality_status"] != "PASS"
    ]
    derived = repo / "derived" / "development"
    outputs = {
        "case_master.csv": case_master,
        "steady_windows.csv": windows,
        "curve_points.csv": curve_points,
        "case_metrics.csv": metrics,
        "run_exclusions.csv": exclusions,
        "seed_metrics.csv": seed_rows,
        "translation_metrics.csv": translation_rows,
        "d1_flame_regime_evidence.csv": flame_rows,
    }
    exclusion_columns = ("run_chid", "stage", "status", "reason")
    for name, rows in outputs.items():
        _write_csv(
            derived / name, rows,
            columns=exclusion_columns if name == "run_exclusions.csv" else None,
        )

    chain_rows = []
    metrics_by_chid = {row["run_chid"]: row for row in metrics}
    for case in cases:
        chain_rows.append(_write_chain(
            repo, case, identity_by_chid[case["chid"]], metrics_by_chid[case["chid"]]
        ))

    _write_csv(repo / "reports" / "g4_development_technical_acceptance.csv", identities)
    _write_csv(repo / "reports" / "g4_development_batch_gates.csv", batch_gates)
    _write_csv(repo / "reports" / "g4_development_seed_metrics.csv", seed_rows)
    _write_csv(repo / "reports" / "g4_development_translation_metrics.csv", translation_rows)
    _write_csv(repo / "reports" / "g4_d1_flame_regime_evidence.csv", flame_rows)
    _write_csv(repo / "reports" / "g4_accepted_chain_hashes.csv", chain_rows)

    manifest_rows = []
    for name in outputs:
        path = derived / name
        manifest_rows.append({
            "relative_path": path.relative_to(repo).as_posix(),
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
            "row_count": len(outputs[name]),
            "rebuild_command": "python -m src.analysis.analyze_g4_development",
        })
    _write_csv(repo / "reports" / "g4_development_database_manifest.csv", manifest_rows)

    decision = {
        "schema_version": "1.0",
        "stage": "G4_DEVELOPMENT",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "decision": "PASS_WITH_USER_VERSION_OVERRIDE_READY_FOR_G5",
        "version_policy": "USER_DIRECTED_IGNORE_VERSION_DIFFERENCE",
        "actual_fds_versions": sorted({row["fds_version_actual"] for row in identities}),
        "formal_baseline_version": protocol["fds"]["fds_version_selected"],
        "technical_pass": len(identities),
        "steady_pass": len(windows),
        "scientific_run_pass": len(metrics),
        "physical_case_count": len(case_master),
        "batch_gates": {row["batch"]: row["status"] for row in batch_gates},
        "seed_gate": "PASS" if seed_gate else "FAIL",
        "translation_gate": "PASS" if translation_gate else "FAIL",
        "flame_regime_gate": (
            "PASS_SINGLE_CEILING_IMPINGING_OR_NEAR_IMPINGING_LAYER"
            if all(row["status"] == "PASS" for row in flame_rows) else "FAIL_MIXED"
        ),
        "detection_limit_K": DETECTION_LIMIT_K,
        "effective_domain_xi_h": XI_H,
        "bootstrap_replicates": n_boot,
        "peak_grid_uncertainty_fraction_retained": 0.13,
        "strict_grid_independence_claim_allowed": False,
        "lockbox_status": "SEALED_UNREAD_UNRUN",
        "database_manifest": "reports/g4_development_database_manifest.csv",
        "next_action": "G5_FORWARD_MODEL_SELECTION_AND_FREEZE_A",
    }
    _write_json(repo / "reports" / "g4_development_decision.json", decision)
    return decision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(PROJECT_ROOT))
    parser.add_argument("--n-boot", type=int, default=N_BOOT_DEFAULT)
    parser.add_argument("--seed", type=int, default=20260821)
    args = parser.parse_args()
    decision = analyze(args.repo, args.n_boot, args.seed)
    print(json.dumps(decision, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
