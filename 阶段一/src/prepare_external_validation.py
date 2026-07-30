"""Prepare traceable thermocouple profiles from archived public experiments.

The archived CSV files are real experimental observations.  This script only
performs baseline subtraction and time/repeat averaging; it does not create
synthetic observations and it does not evaluate FDS accuracy.
"""
from __future__ import annotations

import argparse
import csv
import math
import statistics
from pathlib import Path

import fds_io
from project_paths import STAGE_ROOT


EXTERNAL_DIR = STAGE_ROOT / "05_外部试验复现"
DEFAULT_CASES = EXTERNAL_DIR / "external_cases_template.csv"
DEFAULT_MAPPING = EXTERNAL_DIR / "measurement_mapping.csv"
DEFAULT_OUTDIR = EXTERNAL_DIR / "observations"

OUTPUT_FIELDS = (
    "chid", "sensor_id", "x_source_m", "x_rel_fire_m", "y_m", "z_m",
    "temperature_type", "window_t0_s", "window_t1_s", "baseline_t0_s",
    "baseline_t1_s", "T_mean_C", "dT_mean_C", "temperature_sd_C",
    "uncertainty_basis", "n_raw_files", "n_window_samples", "raw_files",
)


def _read_dicts(path: Path):
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise ValueError(f"{path}: CSV 缺少表头")
        return list(reader)


def _finite_float(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 不是有效数值: {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} 必须为有限数值")
    return number


def _mean_in_window(times, values, start, end, label):
    if end < start:
        raise ValueError(f"{label}: 窗口终点早于起点")
    selected = [
        value for time, value in zip(times, values)
        if start <= time <= end and math.isfinite(value)
    ]
    if not selected:
        raise ValueError(f"{label}: 窗口 [{start:g}, {end:g}] 没有有效数据")
    return statistics.fmean(selected), selected


def _read_arup(path: Path):
    with path.open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.reader(stream))
    if len(rows) < 4 or rows[0][0].strip().lower() != "distance from the fire":
        raise ValueError(f"{path}: 不是预期的 Arup FSB2009 CSV")
    channels = [value.strip() for value in rows[1][1:]]
    times = []
    series = {channel: [] for channel in channels}
    for line_number, row in enumerate(rows[2:], start=3):
        if not row:
            continue
        if len(row) != len(channels) + 1:
            raise ValueError(f"{path}:{line_number}: 列数不一致")
        times.append(_finite_float(row[0], f"{path}:{line_number}:time"))
        for channel, value in zip(channels, row[1:]):
            series[channel].append(_finite_float(value, f"{path}:{line_number}:{channel}"))
    if len(times) < 2 or any(right <= left for left, right in zip(times, times[1:])):
        raise ValueError(f"{path}: 时间必须严格递增且至少有两个样本")
    return times, series


def _mapping_numbers(row):
    numbers = {
        field: _finite_float(row[field], f"{row.get('chid')}/{row.get('raw_channel')}:{field}")
        for field in (
            "x_source_m", "x_rel_fire_m", "y_m", "z_m", "baseline_t0_s",
            "baseline_t1_s", "window_t0_s", "window_t1_s",
        )
    }
    for prefix in ("baseline", "window"):
        if numbers[f"{prefix}_t1_s"] < numbers[f"{prefix}_t0_s"]:
            raise ValueError(
                f"{row.get('chid')}/{row.get('raw_channel')}: {prefix} 窗口终点早于起点"
            )
    return numbers


def _arup_profile(case, mapping_rows, raw_paths):
    raw_data = [_read_arup(path) for path in raw_paths]
    output = []
    for mapping in mapping_rows:
        numbers = _mapping_numbers(mapping)
        absolute_means = []
        rises = []
        sample_count = 0
        channel = mapping["raw_channel"]
        for path, (times, series) in zip(raw_paths, raw_data):
            if channel not in series:
                raise ValueError(f"{path}: 缺少通道 {channel}")
            baseline, _ = _mean_in_window(
                times, series[channel], numbers["baseline_t0_s"],
                numbers["baseline_t1_s"], f"{path}/{channel}/baseline",
            )
            window_mean, samples = _mean_in_window(
                times, series[channel], numbers["window_t0_s"],
                numbers["window_t1_s"], f"{path}/{channel}/window",
            )
            absolute_means.append(window_mean)
            rises.append(window_mean - baseline)
            sample_count += len(samples)
        output.append(_output_row(
            case, mapping, numbers,
            temperature=statistics.fmean(absolute_means),
            rise=statistics.fmean(rises),
            spread=statistics.stdev(rises) if len(rises) > 1 else 0.0,
            uncertainty_basis="between_repeat_sd",
            n_raw_files=len(raw_paths),
            n_window_samples=sample_count,
            raw_paths=raw_paths,
        ))
    return output


def _fds_csv_profile(case, mapping_rows, raw_paths):
    if len(raw_paths) != 1:
        raise ValueError(f"{case['chid']}: fds_timeseries 模式必须恰有一个原始 CSV")
    path = raw_paths[0]
    times, series, units = fds_io._read_fds_csv(path)
    output = []
    for mapping in mapping_rows:
        numbers = _mapping_numbers(mapping)
        channel = mapping["raw_channel"]
        if channel not in series:
            raise ValueError(f"{path}: 缺少通道 {channel}")
        unit = str(units.get(channel, "")).strip().lower()
        if unit not in ("c", "°c", "degc", "celsius"):
            raise ValueError(f"{path}/{channel}: 预期温度单位 C，实际为 {units.get(channel)!r}")
        baseline, _ = _mean_in_window(
            times, series[channel], numbers["baseline_t0_s"],
            numbers["baseline_t1_s"], f"{path}/{channel}/baseline",
        )
        window_mean, samples = _mean_in_window(
            times, series[channel], numbers["window_t0_s"],
            numbers["window_t1_s"], f"{path}/{channel}/window",
        )
        output.append(_output_row(
            case, mapping, numbers,
            temperature=window_mean,
            rise=window_mean - baseline,
            spread=statistics.stdev(samples) if len(samples) > 1 else 0.0,
            uncertainty_basis="window_time_sd",
            n_raw_files=1,
            n_window_samples=len(samples),
            raw_paths=raw_paths,
        ))
    return output


def _output_row(case, mapping, numbers, *, temperature, rise, spread,
                uncertainty_basis, n_raw_files, n_window_samples, raw_paths):
    return {
        "chid": case["chid"],
        "sensor_id": mapping["raw_channel"],
        "x_source_m": f"{numbers['x_source_m']:.6g}",
        "x_rel_fire_m": f"{numbers['x_rel_fire_m']:.6g}",
        "y_m": f"{numbers['y_m']:.6g}",
        "z_m": f"{numbers['z_m']:.6g}",
        "temperature_type": mapping["temperature_type"],
        "window_t0_s": f"{numbers['window_t0_s']:.6g}",
        "window_t1_s": f"{numbers['window_t1_s']:.6g}",
        "baseline_t0_s": f"{numbers['baseline_t0_s']:.6g}",
        "baseline_t1_s": f"{numbers['baseline_t1_s']:.6g}",
        "T_mean_C": f"{temperature:.6f}",
        "dT_mean_C": f"{rise:.6f}",
        "temperature_sd_C": f"{spread:.6f}",
        "uncertainty_basis": uncertainty_basis,
        "n_raw_files": str(n_raw_files),
        "n_window_samples": str(n_window_samples),
        "raw_files": ";".join(path.relative_to(EXTERNAL_DIR).as_posix() for path in raw_paths),
    }


def prepare(cases_path=DEFAULT_CASES, mapping_path=DEFAULT_MAPPING,
            outdir=DEFAULT_OUTDIR, selected=None):
    cases_path = Path(cases_path).resolve()
    mapping_path = Path(mapping_path).resolve()
    outdir = Path(outdir).resolve()
    cases = _read_dicts(cases_path)
    mapping_rows = _read_dicts(mapping_path)
    if not cases:
        raise ValueError(f"{cases_path}: 没有外部工况")
    case_ids = [row.get("chid", "").strip() for row in cases]
    if any(not chid for chid in case_ids) or len(case_ids) != len(set(case_ids)):
        raise ValueError(f"{cases_path}: chid 为空或重复")
    mapping_keys = [
        (row.get("chid", "").strip(), row.get("raw_channel", "").strip())
        for row in mapping_rows
    ]
    if any(not chid or not channel for chid, channel in mapping_keys):
        raise ValueError(f"{mapping_path}: chid/raw_channel 不得为空")
    if len(mapping_keys) != len(set(mapping_keys)):
        raise ValueError(f"{mapping_path}: 同一工况存在重复 raw_channel")
    unknown_mapping = {chid for chid, _ in mapping_keys} - set(case_ids)
    if unknown_mapping:
        raise ValueError(f"测点映射引用未知工况: {', '.join(sorted(unknown_mapping))}")

    selected = set(selected or [row["chid"] for row in cases])
    unknown = selected - set(case_ids)
    if unknown:
        raise ValueError(f"未知外部工况: {', '.join(sorted(unknown))}")

    outdir.mkdir(parents=True, exist_ok=True)
    all_rows = []
    written = []
    for case in cases:
        chid = case["chid"]
        if chid not in selected:
            continue
        case_mapping = [row for row in mapping_rows if row["chid"] == chid]
        if not case_mapping:
            raise ValueError(f"{chid}: measurement_mapping.csv 中没有测点")
        raw_paths = [
            (EXTERNAL_DIR / item.strip()).resolve()
            for item in case["raw_files"].split(";") if item.strip()
        ]
        try:
            for path in raw_paths:
                path.relative_to(EXTERNAL_DIR.resolve())
        except ValueError as exc:
            raise ValueError(f"{chid}: raw_files 必须位于外部试验目录内") from exc
        if not raw_paths or any(not path.is_file() for path in raw_paths):
            raise ValueError(f"{chid}: 原始试验文件缺失")
        fire_x = _finite_float(case.get("x_fire"), f"{chid}:x_fire")
        for mapping in case_mapping:
            numbers = _mapping_numbers(mapping)
            expected_rel = numbers["x_source_m"] - fire_x
            if not math.isclose(numbers["x_rel_fire_m"], expected_rel, abs_tol=1e-6):
                raise ValueError(
                    f"{chid}/{mapping['raw_channel']}: x_rel_fire_m 与 x_source_m-x_fire 不一致"
                )
            if mapping.get("temperature_type", "").strip().lower() != "thermocouple":
                raise ValueError(
                    f"{chid}/{mapping['raw_channel']}: 当前真实观测必须明确标为 thermocouple"
                )
        mode = case["profile_mode"].strip().lower()
        if mode == "arup_replicates":
            rows = _arup_profile(case, case_mapping, raw_paths)
        elif mode == "fds_timeseries":
            rows = _fds_csv_profile(case, case_mapping, raw_paths)
        else:
            raise ValueError(f"{chid}: 未知 profile_mode={mode!r}")
        path = outdir / f"{chid}_temperature_profile.csv"
        _write_rows(path, rows)
        written.append(path)
        all_rows.extend(rows)
    summary = outdir / "external_temperature_profiles.csv"
    _write_rows(summary, all_rows)
    return [*written, summary]


def _write_rows(path, rows):
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="整理公开外部试验的真实热电偶温度剖面")
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--mapping", default=str(DEFAULT_MAPPING))
    parser.add_argument("--outdir", default=str(DEFAULT_OUTDIR))
    parser.add_argument("--chids", nargs="*")
    args = parser.parse_args()
    try:
        paths = prepare(args.cases, args.mapping, args.outdir, args.chids)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    for path in paths:
        print(f"[OK] {path}")


if __name__ == "__main__":
    main()
