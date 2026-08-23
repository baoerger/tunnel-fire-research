"""Build the exact synchronized source-block table required by G5-SR5.

The artifact contains every circular moving block (stride one sample) from
each accepted development run's frozen quasi-steady window.  All spatial
positions in one block use the same source-time indices.  It is therefore a
compact, lossless input for the approved SR5 hierarchical block bootstrap;
it is not a generic replacement for the raw FDS time series.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
from pathlib import Path
import re

from src.analysis import g5_sr_protocol as protocol_guard
from src.analysis.time_average_bootstrap import validate_window
from src.fds import fds_io
from src.fds import tunnel_config as cfg
from src.fds.project_paths import PROJECT_ROOT


REGISTRY = Path("config/development_cases.csv")
CASE_METRICS = Path("derived/development/case_metrics.csv")
CURVE_POINTS = Path("derived/development/curve_points.csv")
OUTPUT_TABLE = Path("derived/development/g5_sr_synchronized_time_block_profiles.csv")
OUTPUT_MANIFEST = Path("reports/g5_sr_synchronized_time_block_manifest.json")
GENERATOR_PATH = Path("src/analysis/build_g5_sr_time_blocks.py")
T90_PATTERN = re.compile(r"T90_(\d+)")


class G5SRTimeBlockError(ValueError):
    """Raised when source provenance or synchronized-block integrity fails."""


def _repo_path(repo: Path, relative: Path | str) -> Path:
    repo = Path(repo).resolve()
    candidate = (repo / Path(relative)).resolve()
    try:
        candidate.relative_to(repo)
    except ValueError as exc:
        raise G5SRTimeBlockError(f"路径越出项目根目录: {relative}") from exc
    return candidate


def _read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    try:
        with path.open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames is None:
                raise G5SRTimeBlockError(f"CSV 缺少表头: {path}")
            return list(reader), list(reader.fieldnames)
    except FileNotFoundError as exc:
        raise G5SRTimeBlockError(f"缺少必需输入: {path}") from exc


def _require_columns(fieldnames: list[str], required: set[str], label: str) -> None:
    missing = sorted(required - set(fieldnames))
    if missing:
        raise G5SRTimeBlockError(f"{label} 缺少列: {', '.join(missing)}")


def _unique_index(rows, keys, label):
    result = {}
    for row in rows:
        key = tuple(row[name] for name in keys)
        if key in result:
            raise G5SRTimeBlockError(f"{label} 主键重复: {key}")
        result[key] = row
    return result


def _finite_float(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise G5SRTimeBlockError(f"{label} 不是数值: {value!r}") from exc
    if not math.isfinite(number):
        raise G5SRTimeBlockError(f"{label} 不是有限数: {value!r}")
    return number


def _positive_int(value, label):
    number = _finite_float(value, label)
    integer = int(number)
    if number != integer or integer <= 0:
        raise G5SRTimeBlockError(f"{label} 必须是正整数: {value!r}")
    return integer


def _fmt(value):
    """Stable, round-trip-safe text for every floating-point value."""
    return format(float(value), ".17g")


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.replace(temporary, path)


def _validate_written_table(
    path,
    columns,
    expected_rows,
    expected_coordinates,
    expected_curve_means,
):
    rows, fieldnames = _read_csv(path)
    if fieldnames != columns:
        raise G5SRTimeBlockError("时间块表列顺序或模式与批准协议不一致")
    if len(rows) != expected_rows:
        raise G5SRTimeBlockError(
            f"时间块表复读行数失配: {len(rows)} != {expected_rows}"
        )

    keys = set()
    block_signatures = {}
    block_coordinates = {}
    block_mean_sums = {}
    block_mean_counts = {}
    for row in rows:
        key = tuple(
            row[name]
            for name in (
                "run_chid",
                "job_attempt_id",
                "window_id",
                "time_block_id",
                "x_coord_m",
            )
        )
        if key in keys:
            raise G5SRTimeBlockError(f"时间块表复合主键重复: {key}")
        keys.add(key)
        block_key = key[:4]
        signature = tuple(
            row[name]
            for name in (
                "block_start_sample_index",
                "block_start_s",
                "block_end_s",
                "block_len_samples",
            )
        )
        previous = block_signatures.setdefault(block_key, signature)
        if previous != signature:
            raise G5SRTimeBlockError(f"同一时间块的时间边界不一致: {block_key}")
        block_coordinates.setdefault(block_key, set()).add(row["x_coord_m"])
        point_key = (row["run_chid"], row["x_coord_m"])
        block_mean_sums[point_key] = block_mean_sums.get(point_key, 0.0) + _finite_float(
            row["deltaT_block_mean_K"], "deltaT_block_mean_K"
        )
        block_mean_counts[point_key] = block_mean_counts.get(point_key, 0) + 1

    for block_key, coordinates in block_coordinates.items():
        run_chid = block_key[0]
        if coordinates != expected_coordinates[run_chid]:
            raise G5SRTimeBlockError(f"时间块空间坐标不完整: {block_key}")

    if set(block_mean_sums) != set(expected_curve_means):
        raise G5SRTimeBlockError("时间块表与 curve_points 的工况/坐标集合不一致")
    differences = []
    for point_key, expected in expected_curve_means.items():
        actual = block_mean_sums[point_key] / block_mean_counts[point_key]
        differences.append((abs(actual - expected), point_key, actual, expected))
    worst = max(differences, default=(0.0, ("", ""), 0.0, 0.0))
    tolerance_K = 1e-9
    if worst[0] > tolerance_K:
        raise G5SRTimeBlockError(
            "全部循环块均值未复现冻结窗口均值: "
            f"{worst[1]} difference={worst[0]:.17g} K"
        )
    return len(rows), len(block_coordinates), worst, tolerance_K


def build_synchronized_time_blocks(
    repo=PROJECT_ROOT,
    *,
    registry_relative=REGISTRY,
    case_metrics_relative=CASE_METRICS,
    curve_points_relative=CURVE_POINTS,
    output_relative=OUTPUT_TABLE,
    manifest_relative=OUTPUT_MANIFEST,
    approval_loader=protocol_guard.load_approved_execution_supplement,
):
    """Build and validate the approved SR5 synchronized-time-block artifact."""
    repo = Path(repo).resolve()
    approved = approval_loader(repo)
    supplement = approved["record"]
    if (
        supplement.get("status")
        != protocol_guard.APPROVED_EXECUTION_SUPPLEMENT_STATUS
        or supplement.get("execution_state")
        != protocol_guard.APPROVED_EXECUTION_SUPPLEMENT_STATE
    ):
        raise G5SRTimeBlockError("执行补充协议未明确批准正式生成")

    sr5 = supplement.get("sr5_synchronized_time_block_input", {})
    expected_output = str(sr5.get("relative_path", "")).replace("\\", "/")
    expected_manifest = str(sr5.get("manifest_path", "")).replace("\\", "/")
    if Path(output_relative).as_posix() != expected_output:
        raise G5SRTimeBlockError("输出时间块表路径与批准协议不一致")
    if Path(manifest_relative).as_posix() != expected_manifest:
        raise G5SRTimeBlockError("输出清单路径与批准协议不一致")
    construction = sr5.get("construction", {})
    if not (
        construction.get("method")
        == "circular moving source blocks with stride one sample and the per-run frozen bootstrap_block_len_samples"
        and construction.get("same_time_indices_for_all_spatial_points") is True
        and construction.get("wraparound") is True
        and construction.get("contains_source_blocks_not_bootstrap_replicates") is True
    ):
        raise G5SRTimeBlockError("批准协议中的同步时间块构造合同不完整")

    columns = list(sr5.get("required_columns", []))
    required_columns = [
        "parent_case_id",
        "physical_case_id",
        "run_chid",
        "job_attempt_id",
        "window_id",
        "rnd_seed",
        "time_block_id",
        "block_start_sample_index",
        "block_start_s",
        "block_end_s",
        "block_len_samples",
        "x_coord_m",
        "xf_actual_m",
        "xi",
        "deltaT_block_mean_K",
        "point_role",
        "detection_limit_K",
    ]
    if columns != required_columns:
        raise G5SRTimeBlockError("批准协议中的时间块表列模式发生漂移")

    registry_path = _repo_path(repo, registry_relative)
    metrics_path = _repo_path(repo, case_metrics_relative)
    curves_path = _repo_path(repo, curve_points_relative)
    output_path = _repo_path(repo, output_relative)
    manifest_path = _repo_path(repo, manifest_relative)
    generator_path = _repo_path(repo, GENERATOR_PATH)

    registry_rows, registry_fields = _read_csv(registry_path)
    metric_rows, metric_fields = _read_csv(metrics_path)
    curve_rows, curve_fields = _read_csv(curves_path)
    _require_columns(
        registry_fields,
        {"chid", "job_attempt_id", "parent_case_id", "physical_case_id", "rnd_seed", "status"},
        "开发集注册表",
    )
    _require_columns(
        metric_fields,
        {
            "physical_case_id", "parent_case_id", "run_chid", "job_attempt_id",
            "window_id", "xf_actual_m", "window_start_s", "window_end_s",
            "bootstrap_block_len_samples", "quality_status",
        },
        "case_metrics",
    )
    _require_columns(
        curve_fields,
        {
            "physical_case_id", "parent_case_id", "run_chid", "job_attempt_id",
            "window_id", "x_coord_m", "xf_actual_m", "xi", "point_role",
            "deltaT_mean_K", "detection_limit_K", "bootstrap_block_len_samples",
        },
        "curve_points",
    )

    accepted_registry = [
        row for row in registry_rows
        if row.get("status") == "COMPLETED_ACCEPTED_G4"
    ]
    registry_index = _unique_index(accepted_registry, ("chid",), "开发集注册表")
    metrics_index = _unique_index(metric_rows, ("run_chid",), "case_metrics")
    if set(key[0] for key in registry_index) != set(key[0] for key in metrics_index):
        raise G5SRTimeBlockError("开发集注册表与 case_metrics 的 run_chid 集合不一致")
    if any(row.get("quality_status") != "PASS" for row in metric_rows):
        raise G5SRTimeBlockError("case_metrics 包含未通过 G4 质量门的工况")

    curves_by_run = {}
    curve_keys = set()
    for row in curve_rows:
        run_chid = row["run_chid"]
        x_value = _finite_float(row["x_coord_m"], f"{run_chid}.x_coord_m")
        key = (run_chid, x_value)
        if key in curve_keys:
            raise G5SRTimeBlockError(f"curve_points 工况/坐标重复: {key}")
        curve_keys.add(key)
        curves_by_run.setdefault(run_chid, {})[x_value] = row
    if set(curves_by_run) != set(key[0] for key in metrics_index):
        raise G5SRTimeBlockError("curve_points 与 case_metrics 的 run_chid 集合不一致")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(output_path.name + ".tmp")
    source_records = []
    expected_coordinates = {}
    expected_curve_means = {}
    total_rows = 0
    total_blocks = 0
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for run_chid in sorted(curves_by_run):
            metric = metrics_index[(run_chid,)]
            registry = registry_index.get((run_chid,))
            if registry is None:
                raise G5SRTimeBlockError(f"开发集注册表缺少工况: {run_chid}")
            identity = ("job_attempt_id", "parent_case_id", "physical_case_id")
            for name in identity:
                metric_name = "run_chid" if name == "chid" else name
                if registry[name] != metric[metric_name]:
                    raise G5SRTimeBlockError(f"{run_chid} 身份字段不一致: {name}")

            attempt_id = metric["job_attempt_id"]
            source_relative = Path(
                "runs/development"
            ) / run_chid / "attempts" / attempt_id / f"{run_chid}_devc.csv"
            source_path = _repo_path(repo, source_relative)
            if not source_path.is_file():
                raise G5SRTimeBlockError(f"缺少原始开发集设备表: {source_relative.as_posix()}")

            times, series, units = fds_io.read_devc(source_path.parent, run_chid)
            if times is None:
                raise G5SRTimeBlockError(f"无法读取原始设备表: {source_relative.as_posix()}")
            series = fds_io.normalize_units(series, units)
            t0 = _finite_float(metric["window_start_s"], f"{run_chid}.window_start_s")
            t1 = _finite_float(metric["window_end_s"], f"{run_chid}.window_end_s")
            selected = validate_window(times, t0, t1, min_duration=50.0)
            block_len = _positive_int(
                metric["bootstrap_block_len_samples"],
                f"{run_chid}.bootstrap_block_len_samples",
            )
            if block_len > len(selected):
                raise G5SRTimeBlockError(f"{run_chid} 块长超过冻结窗口样本数")

            sensors = {}
            for sensor_id, values in series.items():
                match = T90_PATTERN.fullmatch(sensor_id)
                if not match:
                    continue
                x_value = int(match.group(1)) / 100.0
                if x_value in sensors:
                    raise G5SRTimeBlockError(f"{run_chid} T90 坐标重复: {x_value}")
                if len(values) != len(times):
                    raise G5SRTimeBlockError(f"{run_chid}.{sensor_id} 与时间列长度不一致")
                if any(not math.isfinite(values[index]) for index in selected):
                    raise G5SRTimeBlockError(f"{run_chid}.{sensor_id} 冻结窗口含非有限值")
                sensors[x_value] = values

            curve_map = curves_by_run[run_chid]
            if set(sensors) != set(curve_map):
                missing = sorted(set(curve_map) - set(sensors))
                extra = sorted(set(sensors) - set(curve_map))
                raise G5SRTimeBlockError(
                    f"{run_chid} T90 与 curve_points 坐标不一致; missing={missing}, extra={extra}"
                )
            expected_coordinates[run_chid] = {_fmt(value) for value in sensors}
            xf_actual = _finite_float(metric["xf_actual_m"], f"{run_chid}.xf_actual_m")
            for x_value, curve in curve_map.items():
                for name in ("physical_case_id", "parent_case_id", "job_attempt_id", "window_id"):
                    if curve[name] != metric[name]:
                        raise G5SRTimeBlockError(f"{run_chid}/{x_value:g} 曲线身份字段不一致: {name}")
                if not math.isclose(
                    _finite_float(curve["xf_actual_m"], "curve.xf_actual_m"),
                    xf_actual,
                    rel_tol=0.0,
                    abs_tol=1e-12,
                ):
                    raise G5SRTimeBlockError(f"{run_chid}/{x_value:g} 火源坐标不一致")
                curve_block_len = _positive_int(
                    curve["bootstrap_block_len_samples"],
                    "curve.bootstrap_block_len_samples",
                )
                if curve_block_len != block_len:
                    raise G5SRTimeBlockError(f"{run_chid}/{x_value:g} 冻结块长不一致")
                expected_curve_means[(run_chid, _fmt(x_value))] = _finite_float(
                    curve["deltaT_mean_K"], "curve.deltaT_mean_K"
                )

            for block_start in range(len(selected)):
                local_offsets = [
                    (block_start + offset) % len(selected)
                    for offset in range(block_len)
                ]
                source_indices = [selected[offset] for offset in local_offsets]
                block_id = f"TB{block_start:05d}"
                for x_value in sorted(sensors):
                    curve = curve_map[x_value]
                    values = sensors[x_value]
                    block_mean = math.fsum(values[index] for index in source_indices) / block_len
                    writer.writerow(
                        {
                            "parent_case_id": metric["parent_case_id"],
                            "physical_case_id": metric["physical_case_id"],
                            "run_chid": run_chid,
                            "job_attempt_id": attempt_id,
                            "window_id": metric["window_id"],
                            "rnd_seed": str(_positive_int(registry["rnd_seed"], f"{run_chid}.rnd_seed")),
                            "time_block_id": block_id,
                            "block_start_sample_index": str(block_start),
                            "block_start_s": _fmt(times[source_indices[0]]),
                            "block_end_s": _fmt(times[source_indices[-1]]),
                            "block_len_samples": str(block_len),
                            "x_coord_m": _fmt(x_value),
                            "xf_actual_m": _fmt(xf_actual),
                            "xi": _fmt(_finite_float(curve["xi"], "curve.xi")),
                            "deltaT_block_mean_K": _fmt(block_mean - cfg.T_AMBIENT_C),
                            "point_role": curve["point_role"],
                            "detection_limit_K": _fmt(
                                _finite_float(curve["detection_limit_K"], "curve.detection_limit_K")
                            ),
                        }
                    )
                    total_rows += 1
                total_blocks += 1

            source_records.append(
                {
                    "run_chid": run_chid,
                    "job_attempt_id": attempt_id,
                    "window_id": metric["window_id"],
                    "relative_path": source_relative.as_posix(),
                    "sha256": protocol_guard.sha256_file(source_path),
                    "bytes": source_path.stat().st_size,
                    "time_sample_count_total": len(times),
                    "frozen_window_start_s": _fmt(t0),
                    "frozen_window_end_s": _fmt(t1),
                    "frozen_window_sample_count": len(selected),
                    "block_len_samples": block_len,
                    "circular_stride_samples": 1,
                    "time_block_count": len(selected),
                    "spatial_sensor_count": len(sensors),
                    "output_row_count": len(selected) * len(sensors),
                }
            )
    os.replace(temporary, output_path)

    verified_rows, verified_blocks, worst_mean_difference, mean_tolerance_K = _validate_written_table(
        output_path,
        columns,
        total_rows,
        expected_coordinates,
        expected_curve_means,
    )
    if verified_blocks != total_blocks:
        raise G5SRTimeBlockError("时间块表复读块数失配")

    manifest = {
        "schema_version": "1.0",
        "artifact_id": "g5_sr_synchronized_time_block_profiles_v1",
        "status": "PASS",
        "generated_date": supplement["approval"]["approved_date"],
        "purpose": "Exact synchronized source blocks for the approved G5-SR5 hierarchical block bootstrap",
        "scope": {
            "development_only": True,
            "lockbox_used": False,
            "new_fds_generated_or_run": False,
            "generic_raw_time_series_replacement": False,
        },
        "protocol_binding": {
            "parent_protocol_path": protocol_guard.MACHINE_PROTOCOL.as_posix(),
            "parent_protocol_sha256": approved["parent_protocol_sha256"],
            "execution_supplement_path": approved["path"],
            "execution_supplement_sha256": approved["sha256"],
        },
        "generator": {
            "relative_path": GENERATOR_PATH.as_posix(),
            "sha256": protocol_guard.sha256_file(generator_path),
        },
        "table": {
            "relative_path": Path(output_relative).as_posix(),
            "sha256": protocol_guard.sha256_file(output_path),
            "bytes": output_path.stat().st_size,
            "row_count": verified_rows,
            "time_block_count": verified_blocks,
            "run_count": len(source_records),
            "required_columns": columns,
            "float_serialization": "IEEE-754 binary64 round-trip text with 17 significant digits",
        },
        "construction": {
            "method": "circular moving source blocks",
            "stride_samples": 1,
            "wraparound": True,
            "same_source_time_indices_for_all_spatial_points": True,
            "time_block_id_scope": "within run_chid/job_attempt_id/window_id",
            "block_start_sample_index_basis": "zero-based within the frozen window",
            "block_end_s_note": "May be earlier than block_start_s when the circular block wraps",
        },
        "source_runs": source_records,
        "validation": {
            "status": "PASS",
            "all_block_means_vs_frozen_curve_means": {
                "point_count": len(expected_curve_means),
                "maximum_absolute_difference_K": _fmt(worst_mean_difference[0]),
                "tolerance_K": _fmt(mean_tolerance_K),
                "worst_run_chid": worst_mean_difference[1][0],
                "worst_x_coord_m": worst_mean_difference[1][1],
            },
            "checks": [
                "approved parent and execution-supplement hashes verified before output",
                "accepted registry, case_metrics and curve_points run identities matched one-to-one",
                "all source samples lie inside each frozen quasi-steady window",
                "frozen per-run block lengths match case_metrics and every curve point",
                "T90 source coordinates equal the complete curve_points coordinate set",
                "all positions in a time_block_id use identical circular source indices",
                "composite output keys are unique after exact table reread",
                "all blocks contain the complete per-run spatial coordinate set",
                "the mean over all stride-one circular blocks reproduces every frozen curve-point mean",
                "output row and block counts match after exact table reread",
            ],
        },
    }
    _write_json(manifest_path, manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="生成经批准的 G5-SR5 同步循环时间块表"
    )
    parser.add_argument("--repo", default=str(PROJECT_ROOT), help="项目根目录")
    args = parser.parse_args(argv)
    manifest = build_synchronized_time_blocks(args.repo)
    print(
        "G5_SR_SYNCHRONIZED_TIME_BLOCKS_PASS "
        f"runs={manifest['table']['run_count']} "
        f"blocks={manifest['table']['time_block_count']} "
        f"rows={manifest['table']['row_count']} "
        f"sha256={manifest['table']['sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
