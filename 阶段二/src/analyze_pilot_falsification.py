"""真实先导 FDS 的结构判伪集成分析。

质量表是硬门：默认只接受 PASS。质量 PASS 但尚缺正式噪声/工程传感器
阈值证据时，可显式使用 ``--exploratory-thresholds`` 生成候选结构诊断；
这类输出标记为 ``FORMAL_QUALITY_PASS_THRESHOLD_CANDIDATE``，不会据此通过
阶段二决策门。显式 ``--allow-review`` 仅用于读取 REVIEW 质量数据。
"""
from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
from pathlib import Path


STAGE2_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = STAGE2_ROOT.parent
STAGE1_ROOT = PROJECT_ROOT / "阶段一"
STAGE1_SRC = STAGE1_ROOT / "src"
if str(STAGE1_SRC) not in sys.path:
    sys.path.insert(0, str(STAGE1_SRC))

import fds_io  # noqa: E402
import censoring_peak as cp  # noqa: E402
import transport_model as tm  # noqa: E402


DEFAULT_DESIGN = STAGE2_ROOT / "01_先导工况设计" / "pilot_cases_candidate.csv"
DEFAULT_QUALITY = STAGE1_ROOT / "outputs" / "analysis" / "quality" / "pilot_result_check.csv"
DEFAULT_RUN_ROOT = STAGE1_ROOT / "outputs" / "pilot_input"
DEFAULT_PRELIMINARY_ROOT = STAGE2_ROOT / "outputs" / "preliminary_fds_6.9.1"
DEFAULT_WINDOWS = DEFAULT_PRELIMINARY_ROOT / "steady" / "steady_windows.csv"
DEFAULT_PROFILES = DEFAULT_PRELIMINARY_ROOT / "time_average" / "time_average_database.csv"
DEFAULT_OUTDIR = STAGE2_ROOT / "outputs" / "pilot_falsification"

NEAR_CANDIDATES_H = (0.3, 0.4, 0.5)
SIGMA_FACTORS = (0.1, 0.2, 0.3, 0.4)
INTERVAL_WINDOWS_H = ((0.3, 5.0), (0.4, 6.0), (0.5, 7.0))

CASE_FIELDS = (
    "chid", "fds_version", "quality_status", "decision_eligible",
    "evidence_status", "data_origin", "Q_MW", "U_over_Uc", "Df_m",
    "flow_layer", "near_exclusion_H", "threshold_C", "noise_threshold_C",
    "sensor_threshold_C", "threshold_status", "observation_category",
    "n_upstream_detected", "n_downstream_detected", "xp_peak_m",
    "xp_time_ci_low_m", "xp_time_ci_high_m", "xp_sensor_ci_low_m",
    "xp_sensor_ci_high_m", "xp_combined_ci_width_H", "peak_stable",
    "point_xp_m", "point_delta_Tp_C", "k_u_1_m", "k_d_1_m",
    "kappa_u", "kappa_d", "Pe_e", "Da_e", "far_log_r2",
    "far_nrmse", "point_full_nrmse", "finite_cv_nrmse",
    "selected_model", "selected_model_nrmse", "model_peak_shift_H",
    "max_k_rel_change", "max_xp_shift_H", "monotonic_violation_fraction",
    "near_residual_bias_fraction", "near_residual_nrmse",
    "log_linear_ok", "interval_stable", "near_residual_ok",
    "critical_structure_ok", "censoring_stable", "source_model_ok",
    "case_all_gates", "issues",
)

PROFILE_FIELDS = (
    "chid", "x_m", "observed_dT_C", "point_predicted_dT_C",
    "finite_predicted_dT_C", "selected_predicted_dT_C", "selected_model",
    "near_exclusion_H", "quality_status", "evidence_status", "data_origin",
)

NEAR_FIELDS = (
    "near_exclusion_H", "n_pass", "n_fail", "median_far_nrmse",
    "median_log_r2", "failure_chids", "selected",
)

SOURCE_FIELDS = (
    "model", "sigma_factor", "n_cases", "median_nrmse",
    "loocv_selection_count", "selected", "relative_improvement_vs_point",
)

SUMMARY_FIELDS = (
    "evidence_status", "decision_eligible", "data_origin", "n_cases",
    "n_quality_pass", "n_quality_review", "selected_near_exclusion_H",
    "selected_source_model", "selected_sigma_factor", "case_pass_fraction",
    "n_case_pass", "n_case_fail", "preliminary_structure_result",
    "formal_gate_status", "peak_wide_count", "censored_count",
    "insufficient_downstream_count", "reason",
)

THRESHOLD_SOURCE_TYPES = {
    "manufacturer_specification",
    "experimental_calibration",
    "project_engineering_requirement",
    "user_approved_requirement",
}


def _read_rows(path):
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def _write_rows(path, fields, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _finite(value, label):
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 不是有效数值") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} 必须为有限数值")
    return result


def _unique_index(rows, key, label):
    result = {}
    for row in rows:
        value = (row.get(key) or "").strip()
        if not value or value in result:
            raise ValueError(f"{label}: {key} 为空或重复")
        result[value] = row
    return result


def _quality_state(row, allow_review):
    status = (row.get("status") or "").strip().upper()
    if status == "PASS":
        return True, "FORMAL_QUALITY_PASS"
    if status == "REVIEW" and allow_review:
        return False, "PRELIMINARY_REVIEW_NOT_FORMAL"
    raise ValueError(
        f"quality_status={status or 'MISSING'}；仅 PASS 可正式分析，"
        "REVIEW 需显式 --allow-review"
    )


def _profile_index(rows):
    grouped = {}
    for row in rows:
        chid = (row.get("chid") or "").strip()
        sensor = (row.get("sensor") or "").strip()
        if not chid or not sensor:
            raise ValueError("时间平均表存在空 CHID/sensor")
        grouped.setdefault(chid, []).append(row)
    for chid, values in grouped.items():
        values.sort(key=lambda row: _finite(row.get("x"), f"{chid}/x"))
        xs = [_finite(row.get("x"), f"{chid}/x") for row in values]
        if len(xs) < 7 or len(xs) != len(set(xs)):
            raise ValueError(f"{chid}: 温度剖面至少 7 点且 x 不得重复")
    return grouped


def _candidate_noise_threshold(profile_rows):
    """单次稳态块均值不确定度给出的候选噪声阈值，不冒充重复种子证据。"""
    ordered = sorted(
        profile_rows,
        key=lambda row: _finite(row.get("T_bar"), "T_bar"),
    )
    cold = ordered[:max(3, math.ceil(len(ordered) / 4))]
    standard_errors = [_finite(row.get("sd"), "bootstrap sd") for row in cold]
    return 1.96 * statistics.median(standard_errors)


def _noise_threshold_index(path):
    if path is None:
        return {}
    rows = _read_rows(path)
    index = _unique_index(rows, "chid", "noise threshold 表")
    for chid, row in index.items():
        value = _finite(row.get("DeltaT_noise_C"), f"{chid}/DeltaT_noise_C")
        if value <= 0:
            raise ValueError(f"{chid}: DeltaT_noise_C 必须为正")
    return index


def _validate_sensor_threshold_evidence(path, sensor_threshold_C):
    """校验工程传感器阈值证据，不能仅凭文件存在升级正式状态。"""
    rows = _read_rows(path)
    if len(rows) != 1:
        raise ValueError("工程传感器阈值证据必须恰有一条记录")
    row = rows[0]
    required = (
        "sensor_threshold_C", "source_type", "source_reference",
        "decision_basis", "approval_status",
    )
    missing = [field for field in required if not (row.get(field) or "").strip()]
    if missing:
        raise ValueError("工程传感器阈值证据缺少字段: " + ", ".join(missing))
    documented = _finite(row["sensor_threshold_C"], "证据/sensor_threshold_C")
    if documented <= 0 or not math.isclose(
        documented, sensor_threshold_C, rel_tol=0.0, abs_tol=1e-9
    ):
        raise ValueError("工程传感器阈值证据数值与 --sensor-threshold-C 不一致")
    source_type = row["source_type"].strip().lower()
    if source_type not in THRESHOLD_SOURCE_TYPES:
        raise ValueError("工程传感器阈值证据 source_type 不受支持")
    if row["approval_status"].strip().upper() != "APPROVED":
        raise ValueError("工程传感器阈值证据尚未 APPROVED")
    return row


def _window_index(rows):
    result = _unique_index(rows, "chid", "steady_windows")
    for chid, row in result.items():
        if (row.get("status") or "").strip().upper() != "PASS":
            raise ValueError(f"{chid}: 准稳态窗口未通过")
        t0 = _finite(row.get("avg_t0"), f"{chid}/avg_t0")
        t1 = _finite(row.get("avg_t1"), f"{chid}/avg_t1")
        if t1 <= t0:
            raise ValueError(f"{chid}: 准稳态平均窗口无效")
    return result


def _time_profiles(case_dir, run_chid, profile_rows, window_row):
    times, series, units = fds_io.read_devc(str(case_dir), run_chid)
    if not times:
        raise ValueError(f"{run_chid}: DEVC 没有有效时序")
    series = fds_io.normalize_units(series, units)
    t0 = _finite(window_row["avg_t0"], f"{run_chid}/t0")
    t1 = _finite(window_row["avg_t1"], f"{run_chid}/t1")
    indices = [index for index, time in enumerate(times) if t0 <= time <= t1]
    if len(indices) < 10:
        raise ValueError(f"{run_chid}: 准稳态窗口内少于 10 帧")
    ambient_values = [
        _finite(row.get("T_mean_C"), "T_mean_C")
        - _finite(row.get("T_bar"), "T_bar")
        for row in profile_rows
    ]
    ambient = statistics.median(ambient_values)
    sensor_xs = []
    columns = []
    for row in profile_rows:
        sensor = row["sensor"].strip()
        key = next((name for name in series if name.strip().lower() == sensor.lower()), None)
        if key is None:
            raise ValueError(f"{run_chid}: DEVC 缺少 {sensor}")
        sensor_xs.append(_finite(row["x"], f"{run_chid}/{sensor}/x"))
        columns.append(series[key])
    selected_times = [times[index] for index in indices]
    profiles = [
        [column[index] - ambient for column in columns]
        for index in indices
    ]
    return selected_times, profiles, sensor_xs


def _r2(observed, predicted):
    mean = statistics.fmean(observed)
    denominator = sum((value - mean) ** 2 for value in observed)
    if denominator <= 1e-15:
        return 1.0 if all(abs(a - b) <= 1e-12 for a, b in zip(observed, predicted)) else -math.inf
    return 1.0 - sum((a - b) ** 2 for a, b in zip(observed, predicted)) / denominator


def _point_metrics(xs, ys, near_H, threshold, H):
    fit = tm.fit_point_profile(
        xs, ys, H=H, near_exclusion_H=near_H,
        detection_threshold=threshold, min_side_points=3,
    )
    prediction = tm.predict_point_fit(xs, fit)
    selected = [
        (math.log(y), math.log(p))
        for x, y, p in zip(xs, ys, prediction)
        if abs(x - fit["xp"]) / H >= near_H and y >= threshold and p > 0
    ]
    log_r2 = _r2([row[0] for row in selected], [row[1] for row in selected])
    far_nrmse = tm.profile_nrmse(xs, ys, prediction, fit["xp"], near_H, H)
    full_nrmse = tm.profile_nrmse(xs, ys, prediction, fit["xp"], 0.0, H)
    return {**fit, "prediction": prediction, "log_r2": log_r2,
            "far_nrmse": far_nrmse, "full_nrmse": full_nrmse}


def _side_metrics(xs, ys, xp, near_H, max_H, threshold, H, side="down"):
    pairs = []
    for x, y in zip(xs, ys):
        distance = (x - xp) if side == "down" else (xp - x)
        if distance < near_H * H or distance > max_H * H or y < threshold:
            continue
        pairs.append((distance, math.log(y)))
    if len(pairs) < 3:
        raise ValueError(f"{side} 有效拟合点不足 3 个")
    mx = statistics.fmean(row[0] for row in pairs)
    my = statistics.fmean(row[1] for row in pairs)
    denominator = sum((row[0] - mx) ** 2 for row in pairs)
    slope = sum((x - mx) * (y - my) for x, y in pairs) / denominator
    k = -slope
    if k <= 0:
        raise ValueError(f"{side} 衰减率非正")
    intercept = my - slope * mx
    predicted_log = [intercept - k * x for x, _ in pairs]
    observed_log = [row[1] for row in pairs]
    r2 = _r2(observed_log, predicted_log)
    observed = [math.exp(value) for value in observed_log]
    predicted = [math.exp(value) for value in predicted_log]
    scale = max(observed)
    nrmse = math.sqrt(statistics.fmean((a - b) ** 2 for a, b in zip(observed, predicted))) / scale
    return {"k": k, "intercept": intercept, "r2": r2, "nrmse": nrmse,
            "n_points": len(pairs)}


def _censored_metrics(xs, ys, xp, threshold, H):
    fits = [
        _side_metrics(xs, ys, xp, near, max_distance, threshold, H)
        for near, max_distance in INTERVAL_WINDOWS_H
    ]
    baseline = fits[1]
    changes = [abs(row["k"] - baseline["k"]) / baseline["k"] for row in fits]
    prediction = [
        math.exp(baseline["intercept"] - baseline["k"] * max(0.0, x - xp))
        for x in xs
    ]
    return {
        "xp": xp, "delta_Tp": math.exp(baseline["intercept"]),
        "k_u": math.nan, "k_d": baseline["k"], "prediction": prediction,
        "log_r2": baseline["r2"], "far_nrmse": baseline["nrmse"],
        "full_nrmse": baseline["nrmse"], "max_k_rel_change": max(changes),
        "max_xp_shift_H": math.nan,
    }


def _interval_metrics(xs, ys, threshold, H):
    result = tm.interval_sensitivity(
        xs, ys, INTERVAL_WINDOWS_H, H=H,
        detection_threshold=threshold,
    )
    return {
        "max_k_rel_change": max(
            result["max_k_u_rel_change"], result["max_k_d_rel_change"]
        ),
        "max_xp_shift_H": result["max_xp_shift_H"],
    }


def _monotonic_violation_fraction(xs, ys, xp, near_H, tolerance):
    near = near_H * 5.0
    upstream = [(x, y) for x, y in zip(xs, ys) if x <= xp - near]
    downstream = [(x, y) for x, y in zip(xs, ys) if x >= xp + near]
    checks = []
    for left, right in zip(upstream, upstream[1:]):
        checks.append(right[1] + tolerance < left[1])
    for left, right in zip(downstream, downstream[1:]):
        checks.append(right[1] > left[1] + tolerance)
    return sum(checks) / len(checks) if checks else 1.0


def _residual_metrics(xs, ys, predicted, xp, near_H):
    # “峰值附近系统偏差”使用至少 0.5H 的共同评价带；若直接使用所选
    # 0.3H 排除带，2.5/5 m 的离散测点可能恰好一个都落不进去。
    evaluation_H = max(0.5, near_H)
    selected = [
        (y, p) for x, y, p in zip(xs, ys, predicted)
        if abs(x - xp) / 5.0 <= evaluation_H
    ]
    if not selected:
        return math.inf, math.inf
    peak = max(ys)
    residuals = [p - y for y, p in selected]
    bias = abs(statistics.fmean(residuals)) / peak
    nrmse = math.sqrt(statistics.fmean(value * value for value in residuals)) / peak
    return bias, nrmse


def _finite_candidates(xs, ys, Df, H):
    _, rows = tm.fit_finite_source_profile(
        xs, ys, Df=Df, sigma_factors=SIGMA_FACTORS,
        H=H, near_exclusion_H=0.0,
    )
    return {row["sigma_factor"]: row for row in rows}


def _choose_source_model(case_work, improvement_threshold):
    valid = [row for row in case_work if row.get("finite")]
    factor_rows = []
    loo_counts = {factor: 0 for factor in SIGMA_FACTORS}
    for held_out in valid:
        training = [row for row in valid if row is not held_out]
        if not training:
            factor = min(SIGMA_FACTORS, key=lambda value: held_out["finite"][value]["nrmse"])
        else:
            factor = min(
                SIGMA_FACTORS,
                key=lambda value: statistics.median(row["finite"][value]["nrmse"] for row in training),
            )
        loo_counts[factor] += 1
        held_out["finite_cv_factor"] = factor
        held_out["finite_cv_nrmse"] = held_out["finite"][factor]["nrmse"]
        held_out["finite_cv_prediction"] = held_out["finite"][factor]["prediction"]
    point_scores = [row["fit"]["full_nrmse"] for row in case_work if row.get("fit")]
    finite_scores = [row["finite_cv_nrmse"] for row in valid]
    point_median = statistics.median(point_scores) if point_scores else math.inf
    finite_median = statistics.median(finite_scores) if finite_scores else math.inf
    improvement = ((point_median - finite_median) / point_median
                   if math.isfinite(point_median) and point_median > 0 else -math.inf)
    selected_model = "finite" if finite_scores and improvement >= improvement_threshold else "point"
    global_factor = None
    if valid:
        global_factor = min(
            SIGMA_FACTORS,
            key=lambda value: statistics.median(row["finite"][value]["nrmse"] for row in valid),
        )
    factor_rows.append({
        "model": "point", "sigma_factor": "", "n_cases": len(point_scores),
        "median_nrmse": point_median, "loocv_selection_count": "",
        "selected": selected_model == "point",
        "relative_improvement_vs_point": 0.0,
    })
    for factor in SIGMA_FACTORS:
        scores = [row["finite"][factor]["nrmse"] for row in valid]
        factor_rows.append({
            "model": "finite", "sigma_factor": factor, "n_cases": len(scores),
            "median_nrmse": statistics.median(scores) if scores else math.inf,
            "loocv_selection_count": loo_counts[factor],
            "selected": selected_model == "finite" and factor == global_factor,
            "relative_improvement_vs_point": improvement,
        })
    return selected_model, global_factor, factor_rows


def run_analysis(
    design_path=DEFAULT_DESIGN, quality_path=DEFAULT_QUALITY,
    windows_path=DEFAULT_WINDOWS, profiles_path=DEFAULT_PROFILES,
    run_root=DEFAULT_RUN_ROOT, outdir=DEFAULT_OUTDIR, allow_review=False,
    sensor_threshold_C=5.0, noise_thresholds_path=None,
    threshold_evidence_path=None, exploratory_thresholds=False,
    n_boot=200, seed=20260801,
    min_log_r2=0.85, max_far_nrmse=0.20, max_interval_change=0.20,
    max_near_nrmse=0.20, source_improvement=0.10,
):
    design = _unique_index(_read_rows(design_path), "chid", "先导设计表")
    quality = _unique_index(_read_rows(quality_path), "chid", "质量表")
    windows = _window_index(_read_rows(windows_path))
    profiles = _profile_index(_read_rows(profiles_path))
    noise_thresholds = _noise_threshold_index(noise_thresholds_path)
    sensor_threshold_C = _finite(sensor_threshold_C, "sensor_threshold_C")
    if sensor_threshold_C <= 0:
        raise ValueError("sensor_threshold_C 必须为正")
    selected_chids = list(design)
    missing = [chid for chid in selected_chids if chid not in quality or chid not in windows or chid not in profiles]
    if missing:
        raise ValueError("先导输入缺少质量/窗口/剖面记录: " + ", ".join(missing))

    formal_flags = []
    quality_states = {}
    for chid in selected_chids:
        try:
            decision_eligible, evidence_status = _quality_state(quality[chid], allow_review)
        except ValueError as exc:
            raise ValueError(f"{chid}: {exc}") from exc
        formal_flags.append(decision_eligible)
        quality_states[chid] = (decision_eligible, evidence_status)
    all_formal = all(formal_flags)
    threshold_evidence_formal = all_formal
    if all_formal:
        missing_evidence = (
            threshold_evidence_path is None
            or not Path(threshold_evidence_path).is_file()
        )
        missing_noise = [chid for chid in selected_chids if chid not in noise_thresholds]
        if missing_evidence or missing_noise:
            if not exploratory_thresholds:
                if missing_evidence:
                    raise ValueError(
                        "正式分析必须提供 --threshold-evidence 的工程传感器阈值证据"
                    )
                raise ValueError(
                    "正式分析缺少重复/稳态噪声阈值: " + ", ".join(missing_noise)
                )
            threshold_evidence_formal = False
        else:
            _validate_sensor_threshold_evidence(
                threshold_evidence_path, sensor_threshold_C
            )

    case_work = []
    near_evaluations = {value: [] for value in NEAR_CANDIDATES_H}
    for case_index, chid in enumerate(selected_chids):
        profile_rows = profiles[chid]
        xs = [_finite(row["x"], f"{chid}/x") for row in profile_rows]
        ys = [_finite(row["T_bar"], f"{chid}/T_bar") for row in profile_rows]
        quality_eligible, quality_evidence_status = quality_states[chid]
        decision_eligible = quality_eligible and threshold_evidence_formal
        if decision_eligible:
            evidence_status = quality_evidence_status
        elif quality_eligible:
            evidence_status = "FORMAL_QUALITY_PASS_THRESHOLD_CANDIDATE"
        else:
            evidence_status = quality_evidence_status
        if chid in noise_thresholds:
            noise_threshold = _finite(noise_thresholds[chid]["DeltaT_noise_C"], f"{chid}/noise")
            threshold_status = "TRACEABLE_NOISE_AND_SENSOR_THRESHOLD"
        else:
            noise_threshold = _candidate_noise_threshold(profile_rows)
            threshold_status = "CANDIDATE_SINGLE_RUN_BOOTSTRAP_USER_EVIDENCE_REQUIRED"
        threshold = max(noise_threshold, sensor_threshold_C)
        peak = cp.estimate_peak(xs, ys, method="quadratic")
        run_chid = (quality[chid].get("run_chid") or chid).strip()
        times, time_profiles, sensor_xs = _time_profiles(
            Path(run_root) / chid, run_chid, profile_rows, windows[chid]
        )
        time_peak, _ = cp.time_block_peak_bootstrap(
            times, time_profiles, sensor_xs, n_boot=n_boot,
            seed=seed + case_index * 1000,
        )
        sensor_peak, _ = cp.sensor_perturbation_bootstrap(
            xs, ys, n_boot=n_boot, seed=seed + case_index * 1000 + 1,
            temperature_sigma_C=max(0.5, sensor_threshold_C / 1.96),
        )
        combined_width_H = (
            max(time_peak["xp_ci_high"], sensor_peak["xp_ci_high"])
            - min(time_peak["xp_ci_low"], sensor_peak["xp_ci_low"])
        ) / 5.0
        for near in NEAR_CANDIDATES_H:
            classification = cp.classify_upstream_observation(
                xs, ys, peak["xp"], threshold, H=5.0,
                near_exclusion_H=near,
            )
            try:
                if classification["category"] == "bilateral_identifiable":
                    metrics = _point_metrics(xs, ys, near, threshold, 5.0)
                elif classification["category"] in {"upstream_censored", "no_obvious_backflow"}:
                    metrics = _censored_metrics(xs, ys, peak["xp"], threshold, 5.0)
                else:
                    raise ValueError("下游有效拟合点不足")
                near_evaluations[near].append((chid, metrics, None))
            except ValueError as exc:
                near_evaluations[near].append((chid, None, str(exc)))
        case_work.append({
            "chid": chid, "xs": xs, "ys": ys, "profile_rows": profile_rows,
            "design": design[chid], "quality": quality[chid],
            "decision_eligible": decision_eligible, "evidence_status": evidence_status,
            "threshold": threshold, "noise_threshold": noise_threshold,
            "threshold_status": threshold_status, "peak": peak,
            "time_peak": time_peak, "sensor_peak": sensor_peak,
            "combined_width_H": combined_width_H,
        })

    near_rows = []
    for near, evaluations in near_evaluations.items():
        passed = [metrics for _, metrics, error in evaluations if metrics is not None and error is None]
        failures = [chid for chid, metrics, error in evaluations if metrics is None or error]
        near_rows.append({
            "near_exclusion_H": near, "n_pass": len(passed), "n_fail": len(failures),
            "median_far_nrmse": statistics.median(row["far_nrmse"] for row in passed) if passed else math.inf,
            "median_log_r2": statistics.median(row["log_r2"] for row in passed) if passed else -math.inf,
            "failure_chids": ";".join(failures), "selected": False,
        })
    selected_near_row = min(
        near_rows,
        key=lambda row: (row["n_fail"], row["median_far_nrmse"], row["near_exclusion_H"]),
    )
    selected_near = selected_near_row["near_exclusion_H"]
    selected_near_row["selected"] = True
    evaluation_by_chid = {
        chid: (metrics, error)
        for chid, metrics, error in near_evaluations[selected_near]
    }

    for work in case_work:
        chid = work["chid"]
        metrics, error = evaluation_by_chid[chid]
        work["fit"] = metrics
        work["fit_error"] = error
        work["classification"] = cp.classify_upstream_observation(
            work["xs"], work["ys"], work["peak"]["xp"], work["threshold"],
            H=5.0, near_exclusion_H=selected_near,
        )
        if metrics and work["classification"]["category"] == "bilateral_identifiable":
            try:
                metrics.update(_interval_metrics(work["xs"], work["ys"], work["threshold"], 5.0))
            except ValueError as exc:
                metrics["max_k_rel_change"] = math.inf
                metrics["max_xp_shift_H"] = math.inf
                work["fit_error"] = f"区间敏感性失败: {exc}"
            try:
                work["finite"] = _finite_candidates(
                    work["xs"], work["ys"],
                    _finite(work["design"].get("Df"), f"{chid}/Df"), 5.0,
                )
            except ValueError as exc:
                work["finite"] = None
                work["finite_error"] = str(exc)
        else:
            work["finite"] = None

    selected_model, selected_factor, source_rows = _choose_source_model(
        case_work, source_improvement
    )
    case_rows = []
    profile_output = []
    for work in case_work:
        chid = work["chid"]
        fit = work.get("fit")
        classification = work["classification"]
        issues = []
        if work.get("fit_error"):
            issues.append(work["fit_error"])
        point_prediction = fit["prediction"] if fit else [math.nan] * len(work["xs"])
        finite_prediction = work.get("finite_cv_prediction", [math.nan] * len(work["xs"]))
        use_finite = selected_model == "finite" and work.get("finite_cv_prediction") is not None
        selected_prediction = finite_prediction if use_finite else point_prediction
        selected_case_model = "finite" if use_finite else "point"
        selected_nrmse = (
            work.get("finite_cv_nrmse", math.inf) if use_finite
            else (fit["full_nrmse"] if fit else math.inf)
        )
        try:
            predicted_peak = cp.estimate_peak(work["xs"], selected_prediction)
            model_peak_shift_H = abs(predicted_peak["xp"] - work["peak"]["xp"]) / 5.0
        except ValueError as exc:
            model_peak_shift_H = math.inf
            issues.append(f"模型峰值提取失败: {exc}")
        monotonic_fraction = _monotonic_violation_fraction(
            work["xs"], work["ys"], work["peak"]["xp"], selected_near,
            work["threshold"],
        )
        near_bias, near_nrmse = _residual_metrics(
            work["xs"], work["ys"], selected_prediction,
            work["peak"]["xp"], selected_near,
        )
        log_linear_ok = bool(fit and fit["log_r2"] >= min_log_r2 and fit["far_nrmse"] <= max_far_nrmse)
        interval_stable = bool(fit and fit.get("max_k_rel_change", math.inf) <= max_interval_change)
        peak_stable = work["combined_width_H"] <= 0.5
        near_residual_ok = near_nrmse <= max_near_nrmse and near_bias <= 0.15
        # 峰值 Bootstrap 宽度是 §2.7 的模型复杂度门：宽时应把峰值偏移
        # 公式降阶，但不能据此单独否决 §2.8 的远场解析结构。
        critical_structure_ok = (
            model_peak_shift_H <= 0.5 and monotonic_fraction <= 0.20
        )
        censoring_stable = classification["category"] != "insufficient_downstream" and bool(fit)
        source_model_ok = selected_nrmse <= max_far_nrmse
        gates = {
            "log_linear_ok": log_linear_ok,
            "interval_stable": interval_stable,
            "near_residual_ok": near_residual_ok,
            "critical_structure_ok": critical_structure_ok,
            "censoring_stable": censoring_stable,
            "source_model_ok": source_model_ok,
        }
        if classification["category"] == "insufficient_downstream":
            issues.append("峰值下游近场外有效测点少于 3 个，不能辨识下游衰减")
        if not peak_stable:
            issues.append("峰值位置合并 95% 区间宽度超过 0.5H")
        if model_peak_shift_H > 0.5:
            issues.append("模型峰值与独立连续峰值相差超过 0.5H")
        if not log_linear_ok:
            issues.append("远场对数线性或 NRMSE 未达到候选判据")
        if not interval_stable:
            issues.append("拟合区间变化使主要衰减参数变化超过 20%")
        if not near_residual_ok:
            issues.append("峰值附近存在超过候选阈值的系统残差")
        if monotonic_fraction > 0.20:
            issues.append("近场外不可解释的非单调相邻段比例超过 20%")
        if not source_model_ok:
            issues.append("所选源模型全剖面 NRMSE 超过候选阈值")
        design_row = work["design"]
        effective = (
            tm.effective_parameters(fit["k_u"], fit["k_d"], 5.0)
            if fit and math.isfinite(fit.get("k_u", math.nan)) else
            {"kappa_u": math.nan, "kappa_d": math.nan, "Pe_e": math.nan, "Da_e": math.nan}
        )
        row = {
            "chid": chid, "fds_version": work["quality"].get("fds_version", ""),
            "quality_status": work["quality"].get("status", ""),
            "decision_eligible": str(work["decision_eligible"]).lower(),
            "evidence_status": work["evidence_status"], "data_origin": "REAL_FDS",
            "Q_MW": design_row.get("Q", ""), "U_over_Uc": design_row.get("U_over_Uc", ""),
            "Df_m": design_row.get("Df", ""), "flow_layer": design_row.get("flow_layer", ""),
            "near_exclusion_H": selected_near, "threshold_C": work["threshold"],
            "noise_threshold_C": work["noise_threshold"],
            "sensor_threshold_C": sensor_threshold_C, "threshold_status": work["threshold_status"],
            "observation_category": classification["category"],
            "n_upstream_detected": classification["n_upstream_detected"],
            "n_downstream_detected": classification["n_downstream_detected"],
            "xp_peak_m": work["peak"]["xp"],
            "xp_time_ci_low_m": work["time_peak"]["xp_ci_low"],
            "xp_time_ci_high_m": work["time_peak"]["xp_ci_high"],
            "xp_sensor_ci_low_m": work["sensor_peak"]["xp_ci_low"],
            "xp_sensor_ci_high_m": work["sensor_peak"]["xp_ci_high"],
            "xp_combined_ci_width_H": work["combined_width_H"], "peak_stable": peak_stable,
            "point_xp_m": fit.get("xp", math.nan) if fit else math.nan,
            "point_delta_Tp_C": fit.get("delta_Tp", math.nan) if fit else math.nan,
            "k_u_1_m": fit.get("k_u", math.nan) if fit else math.nan,
            "k_d_1_m": fit.get("k_d", math.nan) if fit else math.nan,
            **effective,
            "far_log_r2": fit.get("log_r2", math.nan) if fit else math.nan,
            "far_nrmse": fit.get("far_nrmse", math.inf) if fit else math.inf,
            "point_full_nrmse": fit.get("full_nrmse", math.inf) if fit else math.inf,
            "finite_cv_nrmse": work.get("finite_cv_nrmse", math.nan),
            "selected_model": selected_case_model, "selected_model_nrmse": selected_nrmse,
            "model_peak_shift_H": model_peak_shift_H,
            "max_k_rel_change": fit.get("max_k_rel_change", math.inf) if fit else math.inf,
            "max_xp_shift_H": fit.get("max_xp_shift_H", math.inf) if fit else math.inf,
            "monotonic_violation_fraction": monotonic_fraction,
            "near_residual_bias_fraction": near_bias, "near_residual_nrmse": near_nrmse,
            **gates, "case_all_gates": all(gates.values()), "issues": "; ".join(issues),
        }
        case_rows.append(row)
        for x, observed, point_value, finite_value, selected_value in zip(
            work["xs"], work["ys"], point_prediction, finite_prediction, selected_prediction
        ):
            profile_output.append({
                "chid": chid, "x_m": x, "observed_dT_C": observed,
                "point_predicted_dT_C": point_value,
                "finite_predicted_dT_C": finite_value,
                "selected_predicted_dT_C": selected_value,
                "selected_model": selected_case_model, "near_exclusion_H": selected_near,
                "quality_status": work["quality"].get("status", ""),
                "evidence_status": work["evidence_status"], "data_origin": "REAL_FDS",
            })

    falsification = cp.summarize_falsification(case_rows, minimum_fraction=0.75)
    n_pass = sum(bool(row["case_all_gates"]) for row in case_rows)
    all_evidence_formal = all(row["decision_eligible"] == "true" for row in case_rows)
    all_quality_pass = all(row["quality_status"].upper() == "PASS" for row in case_rows)
    if all_evidence_formal:
        evidence_status = "FORMAL_QUALITY_PASS"
    elif all_quality_pass:
        evidence_status = "FORMAL_QUALITY_PASS_THRESHOLD_CANDIDATE"
    else:
        evidence_status = "PRELIMINARY_REVIEW_NOT_FORMAL"
    preliminary_result = "PASS" if falsification["status"] == "PASS" else "FAIL"
    if all_evidence_formal:
        formal_gate = preliminary_result
    elif all_quality_pass:
        formal_gate = "NOT_EVALUATED_THRESHOLD_EVIDENCE"
    else:
        formal_gate = "NOT_EVALUATED_REVIEW_DATA"
    summary = {
        "evidence_status": evidence_status,
        "decision_eligible": str(all_evidence_formal).lower(), "data_origin": "REAL_FDS",
        "n_cases": len(case_rows),
        "n_quality_pass": sum(row["quality_status"].upper() == "PASS" for row in case_rows),
        "n_quality_review": sum(row["quality_status"].upper() == "REVIEW" for row in case_rows),
        "selected_near_exclusion_H": selected_near,
        "selected_source_model": selected_model,
        "selected_sigma_factor": selected_factor if selected_model == "finite" else "",
        "case_pass_fraction": falsification["pass_fraction"],
        "n_case_pass": n_pass, "n_case_fail": len(case_rows) - n_pass,
        "preliminary_structure_result": preliminary_result,
        "formal_gate_status": formal_gate,
        "peak_wide_count": sum(not bool(row["peak_stable"]) for row in case_rows),
        "censored_count": sum(row["observation_category"] in {"upstream_censored", "no_obvious_backflow"} for row in case_rows),
        "insufficient_downstream_count": sum(row["observation_category"] == "insufficient_downstream" for row in case_rows),
        "reason": falsification["reason"],
    }
    outdir = Path(outdir)
    _write_rows(outdir / "pilot_case_metrics.csv", CASE_FIELDS, case_rows)
    _write_rows(outdir / "pilot_fit_profiles.csv", PROFILE_FIELDS, profile_output)
    _write_rows(outdir / "near_field_candidates.csv", NEAR_FIELDS, near_rows)
    _write_rows(outdir / "source_model_candidates.csv", SOURCE_FIELDS, source_rows)
    _write_rows(outdir / "pilot_falsification_summary.csv", SUMMARY_FIELDS, [summary])
    return summary, case_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", default=str(DEFAULT_DESIGN))
    parser.add_argument("--quality", default=str(DEFAULT_QUALITY))
    parser.add_argument("--steady-windows", default=str(DEFAULT_WINDOWS))
    parser.add_argument("--profiles", default=str(DEFAULT_PROFILES))
    parser.add_argument("--rundir", default=str(DEFAULT_RUN_ROOT))
    parser.add_argument("--outdir", default=str(DEFAULT_OUTDIR))
    parser.add_argument("--allow-review", action="store_true")
    parser.add_argument("--sensor-threshold-C", type=float, default=5.0)
    parser.add_argument("--noise-thresholds")
    parser.add_argument("--threshold-evidence")
    parser.add_argument(
        "--exploratory-thresholds", action="store_true",
        help="质量 PASS 但阈值证据未齐时仅输出候选诊断，不评估正式决策门",
    )
    parser.add_argument("--n-boot", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20260801)
    args = parser.parse_args()
    try:
        summary, rows = run_analysis(
            args.design, args.quality, args.steady_windows, args.profiles,
            args.rundir, args.outdir, args.allow_review,
            args.sensor_threshold_C, args.noise_thresholds,
            args.threshold_evidence, args.exploratory_thresholds,
            args.n_boot, args.seed,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(
        f"[{summary['evidence_status']}] {summary['n_case_pass']}/{summary['n_cases']} "
        f"工况通过全部判据；候选分析={summary['preliminary_structure_result']}，"
        f"正式门={summary['formal_gate_status']}"
    )
    for row in rows:
        if not row["case_all_gates"]:
            print(f"[CHECK] {row['chid']}: {row['observation_category']}; {row['issues']}")
    print(f"结果目录: {Path(args.outdir).resolve()}")


if __name__ == "__main__":
    main()
