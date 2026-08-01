"""Extract returned FDS thermocouple profiles and compare them with experiments.

The quality table is a hard input.  PASS results are decision-eligible; REVIEW
results can only be processed with ``--allow-review`` and are labelled as
preliminary so that a version mismatch cannot silently pass the stage gate.
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from pathlib import Path

import fds_io
import validation_metrics
from project_paths import STAGE_ROOT


EXTERNAL_DIR = STAGE_ROOT / "05_外部试验复现"
DEFAULT_MAPPING = EXTERNAL_DIR / "measurement_mapping.csv"
DEFAULT_OBSERVATIONS = EXTERNAL_DIR / "observations" / "external_temperature_profiles.csv"
DEFAULT_QUALITY = STAGE_ROOT / "outputs" / "analysis" / "quality" / "external_result_check.csv"
DEFAULT_RUN_ROOT = STAGE_ROOT / "outputs"
DEFAULT_OUTDIR = STAGE_ROOT / "outputs" / "analysis" / "validation"

PROFILE_FIELDS = (
    "chid", "run_chid", "sensor_id", "x_source_m", "x_rel_fire_m",
    "baseline_t0_s", "baseline_t1_s", "window_t0_s", "window_t1_s",
    "baseline_temperature_C", "temperature_mean_C", "dT_mean_C",
    "temperature_sd_C", "n_baseline_samples", "n_window_samples",
    "fds_version", "quality_status", "decision_eligible", "evidence_status",
    "data_origin",
)

SUMMARY_FIELDS = (
    "chid", "run_chid", "n_points", "mean_bias_C", "rmse_C", "nrmse",
    "relative_dispersion", "r2", "peak_position_error_m", "peak_exp_dT_C",
    "fds_version", "quality_status", "decision_eligible", "evidence_status",
    "fds_profile_path", "experiment_profile_path",
)


def _read_rows(path):
    path = Path(path)
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def _write_rows(path, fields, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _finite_float(value, label):
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 不是有效数值") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} 必须为有限数值")
    return result


def _window_values(times, values, start, end, label):
    if end < start:
        raise ValueError(f"{label}: 窗口终点早于起点")
    selected = [
        value for time, value in zip(times, values)
        if start - 1e-9 <= time <= end + 1e-9
    ]
    if not selected and abs(end - start) <= 1e-12 and times:
        # FDS adaptive stepping may write 0.008 s for a requested t=0 sample.
        # Accept only a genuinely adjacent sample, not an arbitrary fallback.
        gaps = sorted(
            right - left for left, right in zip(times, times[1:])
            if right > left
        )
        median_gap = gaps[len(gaps) // 2] if gaps else 0.0
        nearest = min(range(len(times)), key=lambda index: abs(times[index] - start))
        tolerance = max(1e-6, 0.1 * median_gap)
        if abs(times[nearest] - start) <= tolerance:
            selected = [values[nearest]]
    if not selected:
        raise ValueError(f"{label}: 窗口 {start:g}-{end:g}s 没有样本")
    if any(not math.isfinite(value) for value in selected):
        raise ValueError(f"{label}: 窗口含 NaN/Inf")
    return selected


def _quality_index(path):
    rows = _read_rows(path)
    index = {}
    for row in rows:
        chid = (row.get("chid") or "").strip()
        if not chid or chid in index:
            raise ValueError(f"{path}: quality 表 CHID 为空或重复")
        index[chid] = row
    return index


def extract_case(chid, mapping_rows, quality_row, run_root=DEFAULT_RUN_ROOT,
                 allow_review=False):
    status = (quality_row.get("status") or "").strip().upper()
    if status == "PASS":
        decision_eligible = "true"
        evidence_status = "FORMAL_QUALITY_PASS"
    elif status == "REVIEW" and allow_review:
        decision_eligible = "false"
        evidence_status = "PRELIMINARY_REVIEW_NOT_FORMAL"
    else:
        raise ValueError(
            f"{chid}: quality_status={status or 'MISSING'}，"
            "仅 PASS 可正式分析；REVIEW 需显式 --allow-review"
        )
    run_chid = (quality_row.get("run_chid") or chid).strip()
    fds_version = (quality_row.get("fds_version") or "").strip()
    case_dir = Path(run_root) / chid
    times, series, units = fds_io.read_devc(str(case_dir), run_chid)
    normalized = fds_io.normalize_units(series, units)
    if not times:
        raise ValueError(f"{chid}: DEVC 没有有效时间数据")

    output = []
    for mapping in mapping_rows:
        sensor = (mapping.get("raw_channel") or "").strip()
        key = next((name for name in normalized if name.strip().lower() == sensor.lower()), None)
        if key is None:
            raise ValueError(f"{chid}: DEVC 缺少映射通道 {sensor}")
        unit = (units.get(key) or "").strip().upper()
        if unit not in {"C", "DEGC", "K"}:
            raise ValueError(f"{chid}/{sensor}: 温度单位 {unit!r} 不受支持")
        b0 = _finite_float(mapping.get("baseline_t0_s"), f"{chid}/{sensor}/baseline_t0")
        b1 = _finite_float(mapping.get("baseline_t1_s"), f"{chid}/{sensor}/baseline_t1")
        w0 = _finite_float(mapping.get("window_t0_s"), f"{chid}/{sensor}/window_t0")
        w1 = _finite_float(mapping.get("window_t1_s"), f"{chid}/{sensor}/window_t1")
        baseline_values = _window_values(times, normalized[key], b0, b1, f"{chid}/{sensor}/baseline")
        window_values = _window_values(times, normalized[key], w0, w1, f"{chid}/{sensor}/window")
        baseline = statistics.fmean(baseline_values)
        mean_temperature = statistics.fmean(window_values)
        output.append({
            "chid": chid,
            "run_chid": run_chid,
            "sensor_id": sensor,
            "x_source_m": _finite_float(mapping.get("x_source_m"), f"{chid}/{sensor}/x_source"),
            "x_rel_fire_m": _finite_float(mapping.get("x_rel_fire_m"), f"{chid}/{sensor}/x_rel"),
            "baseline_t0_s": b0,
            "baseline_t1_s": b1,
            "window_t0_s": w0,
            "window_t1_s": w1,
            "baseline_temperature_C": baseline,
            "temperature_mean_C": mean_temperature,
            "dT_mean_C": mean_temperature - baseline,
            "temperature_sd_C": statistics.stdev(window_values) if len(window_values) > 1 else 0.0,
            "n_baseline_samples": len(baseline_values),
            "n_window_samples": len(window_values),
            "fds_version": fds_version,
            "quality_status": status,
            "decision_eligible": decision_eligible,
            "evidence_status": evidence_status,
            "data_origin": "REAL_FDS",
        })
    if len(output) < 2:
        raise ValueError(f"{chid}: 至少需要两个映射测点")
    return output


def run_analysis(mapping_path=DEFAULT_MAPPING, observations_path=DEFAULT_OBSERVATIONS,
                 quality_path=DEFAULT_QUALITY, run_root=DEFAULT_RUN_ROOT,
                 outdir=DEFAULT_OUTDIR, chids=None, allow_review=False):
    mapping_rows = _read_rows(mapping_path)
    observations = _read_rows(observations_path)
    quality = _quality_index(quality_path)
    available = list(dict.fromkeys((row.get("chid") or "").strip() for row in mapping_rows))
    selected = list(chids) if chids else available
    unknown = set(selected) - set(available)
    if unknown:
        raise ValueError(f"映射表没有工况: {', '.join(sorted(unknown))}")
    outdir = Path(outdir)
    summaries = []
    profiles = []
    for chid in selected:
        if chid not in quality:
            raise ValueError(f"{chid}: quality 表没有记录")
        case_mapping = [row for row in mapping_rows if row.get("chid", "").strip() == chid]
        case_observations = [row for row in observations if row.get("chid", "").strip() == chid]
        profile = extract_case(chid, case_mapping, quality[chid], run_root, allow_review)
        obs_by_sensor = {(row.get("sensor_id") or "").strip(): row for row in case_observations}
        missing_obs = [row["sensor_id"] for row in profile if row["sensor_id"] not in obs_by_sensor]
        if missing_obs:
            raise ValueError(f"{chid}: 试验观测缺少通道 {', '.join(missing_obs)}")
        fds_points = [(row["x_rel_fire_m"], row["dT_mean_C"]) for row in profile]
        exp_points = [
            (
                _finite_float(obs_by_sensor[row["sensor_id"]].get("x_rel_fire_m"), f"{chid}/exp/x"),
                _finite_float(obs_by_sensor[row["sensor_id"]].get("dT_mean_C"), f"{chid}/exp/dT"),
            )
            for row in profile
        ]
        metric = validation_metrics.metrics(fds_points, exp_points)
        profile_path = outdir / f"{chid}_fds_temperature_profile.csv"
        _write_rows(profile_path, PROFILE_FIELDS, profile)
        first = profile[0]
        summaries.append({
            "chid": chid,
            "run_chid": first["run_chid"],
            "n_points": len(profile),
            "mean_bias_C": metric["mean_bias"],
            "rmse_C": metric["rmse"],
            "nrmse": metric["nrmse"],
            "relative_dispersion": metric["rel_disp"],
            "r2": metric["r2"],
            "peak_position_error_m": metric["xp_err"],
            "peak_exp_dT_C": metric["peak_exp"],
            "fds_version": first["fds_version"],
            "quality_status": first["quality_status"],
            "decision_eligible": first["decision_eligible"],
            "evidence_status": first["evidence_status"],
            "fds_profile_path": str(profile_path.resolve()),
            "experiment_profile_path": str(Path(observations_path).resolve()),
        })
        profiles.extend(profile)
    summary_path = outdir / "external_validation_metrics.csv"
    _write_rows(summary_path, SUMMARY_FIELDS, summaries)
    return summaries, profiles, summary_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping", default=str(DEFAULT_MAPPING))
    parser.add_argument("--observations", default=str(DEFAULT_OBSERVATIONS))
    parser.add_argument("--quality", default=str(DEFAULT_QUALITY))
    parser.add_argument("--rundir", default=str(DEFAULT_RUN_ROOT))
    parser.add_argument("--outdir", default=str(DEFAULT_OUTDIR))
    parser.add_argument("--chids", nargs="*")
    parser.add_argument(
        "--allow-review", action="store_true",
        help="只生成 PRELIMINARY_REVIEW_NOT_FORMAL 预分析，不通过正式决策门",
    )
    args = parser.parse_args()
    try:
        summaries, _, path = run_analysis(
            args.mapping, args.observations, args.quality, args.rundir,
            args.outdir, args.chids, args.allow_review,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    for row in summaries:
        print(
            f"[{row['evidence_status']}] {row['chid']}: "
            f"NRMSE={row['nrmse']:.4f}, bias={row['mean_bias_C']:.2f} C, "
            f"xp_error={row['peak_position_error_m']:.2f} m"
        )
    print(f"结果表: {path}")


if __name__ == "__main__":
    main()
