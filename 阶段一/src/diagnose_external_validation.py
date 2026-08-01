"""Diagnose external-validation residuals without changing official inputs.

The diagnostic uses real experiment/FDS time series.  It verifies mapped DEVC
coordinates, exports point residuals, and repeats the comparison over fixed
window shifts.  Window-sensitivity labels are diagnostic heuristics, not stage
acceptance criteria and not permission to tune the official validation cases.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import statistics
from pathlib import Path

import analyze_external_validation as analysis
import fds_io
import prepare_external_validation as preparation
import validation_metrics
from project_paths import STAGE_ROOT


EXTERNAL_DIR = STAGE_ROOT / "05_外部试验复现"
DEFAULT_CASES = EXTERNAL_DIR / "external_cases_template.csv"
DEFAULT_MAPPING = EXTERNAL_DIR / "measurement_mapping.csv"
DEFAULT_OBSERVATIONS = EXTERNAL_DIR / "observations" / "external_temperature_profiles.csv"
DEFAULT_QUALITY = STAGE_ROOT / "outputs" / "analysis" / "quality" / "external_result_check.csv"
DEFAULT_RUN_ROOT = STAGE_ROOT / "outputs"
DEFAULT_OUTDIR = STAGE_ROOT / "outputs" / "analysis" / "validation" / "diagnostics"
DEFAULT_SHIFT_FRACTIONS = (-0.5, -0.25, 0.0, 0.25, 0.5)

POINT_FIELDS = (
    "chid", "sensor_id", "x_rel_fire_m", "exp_dT_C", "fds_dT_C",
    "residual_C", "absolute_error_C", "relative_absolute_error",
    "exp_sd_C", "fds_sd_C", "combined_sd_C", "standardized_residual",
    "residual_sign", "mapped_x_m", "mapped_y_m", "mapped_z_m",
    "fds_x_m", "fds_y_m", "fds_z_m", "coordinate_error_m",
    "coordinate_match", "quantity_match", "data_origin", "evidence_status",
)

WINDOW_FIELDS = (
    "chid", "shift_fraction", "shift_s", "window_t0_s", "window_t1_s",
    "n_points", "mean_bias_C", "rmse_C", "nrmse", "r2",
    "peak_position_error_m", "data_origin", "evidence_status",
)

SUMMARY_FIELDS = (
    "chid", "fds_version", "mapping_contract_ok", "n_points",
    "n_positive_residuals", "n_negative_residuals", "residual_pattern",
    "worst_sensor_id", "worst_absolute_error_C", "current_mean_bias_C",
    "current_nrmse", "best_nrmse", "best_shift_s",
    "best_relative_nrmse_improvement", "bias_sign_stable_across_windows",
    "window_sensitivity", "primary_interpretation", "data_origin",
    "evidence_status",
)


def _read_rows(path):
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def _write_rows(path, fields, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


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


def _series_key(series, sensor, label):
    matches = [key for key in series if key.strip().lower() == sensor.lower()]
    if len(matches) != 1:
        raise ValueError(f"{label}: 通道 {sensor} 缺失或不唯一")
    return matches[0]


def _parse_devc_records(path):
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    records = {}
    for record in re.findall(r"&DEVC\b(.*?)/", text, re.I | re.S):
        id_match = re.search(r"\bID\s*=\s*'([^']+)'", record, re.I)
        xyz_match = re.search(
            r"\bXYZ\s*=\s*([0-9.eE+-]+)\s*,?\s*"
            r"([0-9.eE+-]+)\s*,?\s*([0-9.eE+-]+)",
            record,
            re.I,
        )
        quantity = re.search(r"\bQUANTITY\s*=\s*'([^']+)'", record, re.I)
        if not id_match or not xyz_match:
            continue
        sensor = id_match.group(1).strip().lower()
        if sensor in records:
            raise ValueError(f"{path}: DEVC ID {id_match.group(1)!r} 重复")
        records[sensor] = {
            "x": float(xyz_match.group(1)),
            "y": float(xyz_match.group(2)),
            "z": float(xyz_match.group(3)),
            "quantity": quantity.group(1).strip().upper() if quantity else "",
        }
    return records


def _executed_input(case_dir, chid, run_chid):
    preferred = Path(case_dir) / f"{run_chid}.fds"
    fallback = Path(case_dir) / f"{chid}.fds"
    if preferred.is_file():
        return preferred
    if fallback.is_file():
        return fallback
    raise FileNotFoundError(f"{case_dir}: 缺少执行输入 {run_chid}.fds/{chid}.fds")


def _load_experiment(case, cases_dir):
    raw_paths = [
        (Path(cases_dir) / item.strip()).resolve()
        for item in (case.get("raw_files") or "").split(";") if item.strip()
    ]
    if not raw_paths or any(not path.is_file() for path in raw_paths):
        raise FileNotFoundError(f"{case.get('chid')}: 原始试验文件缺失")
    mode = (case.get("profile_mode") or "").strip().lower()
    if mode == "arup_replicates":
        return mode, [preparation._read_arup(path) for path in raw_paths]
    if mode == "fds_timeseries":
        if len(raw_paths) != 1:
            raise ValueError(f"{case.get('chid')}: fds_timeseries 必须恰有一个文件")
        times, series, units = fds_io._read_fds_csv(raw_paths[0])
        for key, unit in units.items():
            if key in series and str(unit).strip().upper() not in {"C", "DEGC"}:
                # Non-temperature auxiliary columns are allowed and ignored.
                continue
        return mode, [(times, series)]
    raise ValueError(f"{case.get('chid')}: 未知 profile_mode={mode!r}")


def _mean_window(times, values, start, end, label):
    selected = analysis._window_values(times, values, start, end, label)
    return statistics.fmean(selected), selected


def _experiment_rise(datasets, sensor, b0, b1, w0, w1, label):
    rises = []
    window_values = []
    for index, (times, series) in enumerate(datasets):
        key = _series_key(series, sensor, f"{label}/raw{index + 1}")
        baseline, _ = _mean_window(times, series[key], b0, b1, f"{label}/baseline")
        mean, selected = _mean_window(times, series[key], w0, w1, f"{label}/window")
        rises.append(mean - baseline)
        window_values.extend(selected)
    return statistics.fmean(rises), window_values


def _fds_rise(times, series, sensor, b0, b1, w0, w1, label):
    key = _series_key(series, sensor, label)
    baseline, _ = _mean_window(times, series[key], b0, b1, f"{label}/baseline")
    mean, selected = _mean_window(times, series[key], w0, w1, f"{label}/window")
    return mean - baseline, selected


def _common_window(mapping_rows, chid):
    windows = {
        (
            _finite(row.get("window_t0_s"), f"{chid}/window_t0"),
            _finite(row.get("window_t1_s"), f"{chid}/window_t1"),
        )
        for row in mapping_rows
    }
    if len(windows) != 1:
        raise ValueError(f"{chid}: 时间窗敏感性要求同一工况测点共享窗口")
    start, end = windows.pop()
    if end <= start:
        raise ValueError(f"{chid}: 比较窗口必须具有正长度")
    return start, end


def _sign(value, tolerance=1e-9):
    if value > tolerance:
        return "positive"
    if value < -tolerance:
        return "negative"
    return "zero"


def run_diagnostics(
    cases_path=DEFAULT_CASES,
    mapping_path=DEFAULT_MAPPING,
    observations_path=DEFAULT_OBSERVATIONS,
    quality_path=DEFAULT_QUALITY,
    run_root=DEFAULT_RUN_ROOT,
    outdir=DEFAULT_OUTDIR,
    chids=None,
    shift_fractions=DEFAULT_SHIFT_FRACTIONS,
):
    cases_path = Path(cases_path).resolve()
    cases = _unique_index(_read_rows(cases_path), "chid", "外部工况表")
    quality = _unique_index(_read_rows(quality_path), "chid", "质量表")
    observations = _read_rows(observations_path)
    mapping_all = _read_rows(mapping_path)
    available = list(cases)
    selected = list(chids) if chids else available
    unknown = set(selected) - set(available)
    if unknown:
        raise ValueError("未知外部工况: " + ", ".join(sorted(unknown)))
    shifts = tuple(_finite(value, "shift_fraction") for value in shift_fractions)
    if 0.0 not in shifts or len(shifts) != len(set(shifts)):
        raise ValueError("shift_fractions 必须唯一且包含 0")

    point_rows = []
    window_rows = []
    summary_rows = []
    for chid in selected:
        quality_row = quality.get(chid)
        if not quality_row or (quality_row.get("status") or "").strip().upper() != "PASS":
            raise ValueError(f"{chid}: 只有质量 PASS 结果可作正式诊断")
        mapping_rows = [row for row in mapping_all if (row.get("chid") or "").strip() == chid]
        obs_rows = [row for row in observations if (row.get("chid") or "").strip() == chid]
        if len(mapping_rows) < 2 or len(obs_rows) != len(mapping_rows):
            raise ValueError(f"{chid}: 映射与观测数量不一致或少于 2 点")
        obs_index = _unique_index(obs_rows, "sensor_id", f"{chid}/观测")
        run_chid = (quality_row.get("run_chid") or chid).strip()
        case_dir = Path(run_root) / chid
        fds_times, fds_series, fds_units = fds_io.read_devc(str(case_dir), run_chid)
        fds_series = fds_io.normalize_units(fds_series, fds_units)
        if not fds_times:
            raise ValueError(f"{chid}: FDS DEVC 无有效数据")
        _, experiment = _load_experiment(cases[chid], cases_path.parent)
        devc = _parse_devc_records(_executed_input(case_dir, chid, run_chid))
        window_start, window_end = _common_window(mapping_rows, chid)
        duration = window_end - window_start
        current_values = []

        for mapping in mapping_rows:
            sensor = (mapping.get("raw_channel") or "").strip()
            if sensor not in obs_index:
                raise ValueError(f"{chid}: 观测缺少通道 {sensor}")
            b0 = _finite(mapping.get("baseline_t0_s"), f"{chid}/{sensor}/b0")
            b1 = _finite(mapping.get("baseline_t1_s"), f"{chid}/{sensor}/b1")
            exp_rise, _ = _experiment_rise(
                experiment, sensor, b0, b1, window_start, window_end, f"{chid}/{sensor}/EXP"
            )
            obs_rise = _finite(obs_index[sensor].get("dT_mean_C"), f"{chid}/{sensor}/obs")
            if not math.isclose(exp_rise, obs_rise, rel_tol=1e-7, abs_tol=1e-4):
                raise ValueError(f"{chid}/{sensor}: 原始时序与归档观测不一致")
            fds_rise, fds_window = _fds_rise(
                fds_times, fds_series, sensor, b0, b1,
                window_start, window_end, f"{chid}/{sensor}/FDS",
            )
            residual = fds_rise - exp_rise
            exp_sd = _finite(obs_index[sensor].get("temperature_sd_C"), f"{chid}/{sensor}/sd")
            fds_sd = statistics.stdev(fds_window) if len(fds_window) > 1 else 0.0
            combined_sd = math.hypot(exp_sd, fds_sd)
            mapped = tuple(_finite(mapping.get(name), f"{chid}/{sensor}/{name}") for name in (
                "x_source_m", "y_m", "z_m"
            ))
            record = devc.get(sensor.lower())
            if record:
                fds_xyz = (record["x"], record["y"], record["z"])
                coordinate_error = math.dist(mapped, fds_xyz)
                coordinate_match = coordinate_error <= 1e-6
                quantity_match = record["quantity"] == "THERMOCOUPLE"
            else:
                fds_xyz = (math.nan, math.nan, math.nan)
                coordinate_error = math.inf
                coordinate_match = False
                quantity_match = False
            point_rows.append({
                "chid": chid,
                "sensor_id": sensor,
                "x_rel_fire_m": _finite(mapping.get("x_rel_fire_m"), f"{chid}/{sensor}/x_rel"),
                "exp_dT_C": exp_rise,
                "fds_dT_C": fds_rise,
                "residual_C": residual,
                "absolute_error_C": abs(residual),
                "relative_absolute_error": abs(residual) / abs(exp_rise) if abs(exp_rise) > 1e-9 else math.inf,
                "exp_sd_C": exp_sd,
                "fds_sd_C": fds_sd,
                "combined_sd_C": combined_sd,
                "standardized_residual": residual / combined_sd if combined_sd > 1e-12 else math.nan,
                "residual_sign": _sign(residual),
                "mapped_x_m": mapped[0], "mapped_y_m": mapped[1], "mapped_z_m": mapped[2],
                "fds_x_m": fds_xyz[0], "fds_y_m": fds_xyz[1], "fds_z_m": fds_xyz[2],
                "coordinate_error_m": coordinate_error,
                "coordinate_match": str(coordinate_match).lower(),
                "quantity_match": str(quantity_match).lower(),
                "data_origin": "REAL_FDS_AND_EXPERIMENT",
                "evidence_status": "FORMAL_QUALITY_PASS_DIAGNOSTIC",
            })
            current_values.append((
                _finite(mapping.get("x_rel_fire_m"), f"{chid}/{sensor}/x_rel"),
                exp_rise,
                fds_rise,
            ))

        for fraction in shifts:
            shift = fraction * duration
            start = window_start + shift
            end = window_end + shift
            exp_points = []
            fds_points = []
            for mapping in mapping_rows:
                sensor = (mapping.get("raw_channel") or "").strip()
                b0 = _finite(mapping.get("baseline_t0_s"), f"{chid}/{sensor}/b0")
                b1 = _finite(mapping.get("baseline_t1_s"), f"{chid}/{sensor}/b1")
                exp_rise, _ = _experiment_rise(
                    experiment, sensor, b0, b1, start, end, f"{chid}/{sensor}/EXP"
                )
                fds_rise, _ = _fds_rise(
                    fds_times, fds_series, sensor, b0, b1, start, end, f"{chid}/{sensor}/FDS"
                )
                x = _finite(mapping.get("x_rel_fire_m"), f"{chid}/{sensor}/x_rel")
                exp_points.append((x, exp_rise))
                fds_points.append((x, fds_rise))
            metric = validation_metrics.metrics(fds_points, exp_points)
            window_rows.append({
                "chid": chid, "shift_fraction": fraction, "shift_s": shift,
                "window_t0_s": start, "window_t1_s": end,
                "n_points": len(exp_points), "mean_bias_C": metric["mean_bias"],
                "rmse_C": metric["rmse"], "nrmse": metric["nrmse"],
                "r2": metric["r2"], "peak_position_error_m": metric["xp_err"],
                "data_origin": "REAL_FDS_AND_EXPERIMENT",
                "evidence_status": "FORMAL_QUALITY_PASS_DIAGNOSTIC",
            })

        case_points = [row for row in point_rows if row["chid"] == chid]
        case_windows = [row for row in window_rows if row["chid"] == chid]
        current = next(row for row in case_windows if abs(row["shift_s"]) <= 1e-12)
        best = min(case_windows, key=lambda row: (row["nrmse"], abs(row["shift_s"])))
        improvement = (
            (current["nrmse"] - best["nrmse"]) / current["nrmse"]
            if current["nrmse"] > 1e-12 else 0.0
        )
        positives = sum(row["residual_C"] > 1e-9 for row in case_points)
        negatives = sum(row["residual_C"] < -1e-9 for row in case_points)
        if positives / len(case_points) >= 0.8:
            pattern = "SYSTEMATIC_OVERPREDICTION"
        elif negatives / len(case_points) >= 0.8:
            pattern = "SYSTEMATIC_UNDERPREDICTION"
        else:
            pattern = "MIXED_SHAPE_ERROR"
        if improvement >= 0.25:
            sensitivity = "HIGH"
        elif improvement >= 0.10:
            sensitivity = "MODERATE"
        else:
            sensitivity = "LOW"
        bias_signs = {_sign(row["mean_bias_C"], tolerance=1e-6) for row in case_windows}
        bias_signs.discard("zero")
        bias_stable = len(bias_signs) <= 1
        mapping_ok = all(
            row["coordinate_match"] == "true" and row["quantity_match"] == "true"
            for row in case_points
        )
        if not mapping_ok:
            interpretation = "MAPPING_OR_DEVC_CONTRACT_PROBLEM"
        elif sensitivity == "LOW" and bias_stable:
            interpretation = "TIME_WINDOW_NOT_PRIMARY"
        elif sensitivity == "HIGH":
            interpretation = "TIME_WINDOW_MAY_CONTRIBUTE"
        else:
            interpretation = "TIME_WINDOW_PARTLY_CONTRIBUTES"
        worst = max(case_points, key=lambda row: row["absolute_error_C"])
        summary_rows.append({
            "chid": chid,
            "fds_version": (quality_row.get("fds_version") or "").strip(),
            "mapping_contract_ok": str(mapping_ok).lower(),
            "n_points": len(case_points),
            "n_positive_residuals": positives,
            "n_negative_residuals": negatives,
            "residual_pattern": pattern,
            "worst_sensor_id": worst["sensor_id"],
            "worst_absolute_error_C": worst["absolute_error_C"],
            "current_mean_bias_C": current["mean_bias_C"],
            "current_nrmse": current["nrmse"],
            "best_nrmse": best["nrmse"],
            "best_shift_s": best["shift_s"],
            "best_relative_nrmse_improvement": improvement,
            "bias_sign_stable_across_windows": str(bias_stable).lower(),
            "window_sensitivity": sensitivity,
            "primary_interpretation": interpretation,
            "data_origin": "REAL_FDS_AND_EXPERIMENT",
            "evidence_status": "FORMAL_QUALITY_PASS_DIAGNOSTIC",
        })

    outdir = Path(outdir)
    _write_rows(outdir / "external_point_residuals.csv", POINT_FIELDS, point_rows)
    _write_rows(outdir / "external_window_sensitivity.csv", WINDOW_FIELDS, window_rows)
    _write_rows(outdir / "external_diagnostic_summary.csv", SUMMARY_FIELDS, summary_rows)
    return summary_rows, point_rows, window_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--mapping", default=str(DEFAULT_MAPPING))
    parser.add_argument("--observations", default=str(DEFAULT_OBSERVATIONS))
    parser.add_argument("--quality", default=str(DEFAULT_QUALITY))
    parser.add_argument("--rundir", default=str(DEFAULT_RUN_ROOT))
    parser.add_argument("--outdir", default=str(DEFAULT_OUTDIR))
    parser.add_argument("--chids", nargs="*")
    args = parser.parse_args()
    try:
        summaries, _, _ = run_diagnostics(
            args.cases, args.mapping, args.observations, args.quality,
            args.rundir, args.outdir, args.chids,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    for row in summaries:
        print(
            f"[{row['primary_interpretation']}] {row['chid']}: "
            f"pattern={row['residual_pattern']}, NRMSE={row['current_nrmse']:.4f}, "
            f"best={row['best_nrmse']:.4f} at {row['best_shift_s']:+.1f}s"
        )
    print(f"结果目录: {Path(args.outdir).resolve()}")


if __name__ == "__main__":
    main()
