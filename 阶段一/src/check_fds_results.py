"""轻量检查 outputs/runs/<chid>/ 中的 FDS 回传结果。"""

from __future__ import annotations

import argparse
import csv
import math
import re
from pathlib import Path

import fds_io
from project_paths import FDS_RUNS_DIR, STAGE_ROOT, output_path


DEFAULT_EXTERNAL_MAPPING = (
    STAGE_ROOT / "05_外部试验复现" / "measurement_mapping.csv"
)
DEFAULT_EXPECTED_VERSION = "6.9.1"


SUMMARY_FIELDS = (
    "chid", "run_chid", "status", "fds_version", "version_match", "t_end_s",
    "devc_t_final_s", "hrr_t_final_s", "error_count", "warning_count",
    "rejected_count", "burner_issue", "required_devc_ok",
    "required_hrr_ok", "time_series_ok", "hrr_nonzero",
    "target_hrr_kW", "tail_hrr_kW", "hrr_closure_rel_error",
    "end_marker_present", "out_completed_successfully",
    "full_field_present", "issues",
)


def _run_artifacts(case_dir, logical_chid):
    """Resolve the files produced by FDS, including CATF concatenated runs.

    CATF writes and runs a second input whose HEAD CHID normally ends in
    ``_cat``.  The returned directory still belongs to the logical validation
    case, so select the input with the most complete matching output quartet
    instead of assuming that every output prefix equals the folder name.
    """
    case_dir = Path(case_dir)
    candidates = []
    for fds_path in sorted(case_dir.glob("*.fds")):
        try:
            meta = _parse_input(fds_path)
        except (OSError, ValueError):
            continue
        run_chid = meta["chid"] or fds_path.stem
        paths = {
            "fds": fds_path,
            "out": case_dir / f"{run_chid}.out",
            "devc": case_dir / f"{run_chid}_devc.csv",
            "hrr": case_dir / f"{run_chid}_hrr.csv",
        }
        complete_count = sum(
            path.is_file() and path.stat().st_size > 0 for path in paths.values()
        )
        candidates.append((complete_count, run_chid == logical_chid, run_chid, paths, meta))
    if candidates:
        _, _, run_chid, paths, meta = max(candidates, key=lambda item: (item[0], item[1]))
        return run_chid, paths, meta
    paths = {
        "fds": case_dir / f"{logical_chid}.fds",
        "out": case_dir / f"{logical_chid}.out",
        "devc": case_dir / f"{logical_chid}_devc.csv",
        "hrr": case_dir / f"{logical_chid}_hrr.csv",
    }
    return logical_chid, paths, {
        "chid": None, "t_end": None, "tau_q": None, "target_hrr_kW": None,
    }


def _read_text(path):
    return Path(path).read_text(encoding="utf-8", errors="ignore")


def _first_number(text, pattern):
    match = re.search(pattern, text, re.I | re.S)
    return float(match.group(1)) if match else None


def _parse_input(path):
    text = _read_text(path)
    chid_match = re.search(r"&HEAD\b[^/]*\bCHID\s*=\s*'([^']+)'", text, re.I)
    t_end = _first_number(text, r"&TIME\b[^/]*\bT_END\s*=\s*([0-9.eE+-]+)")
    tau_q = _first_number(
        text,
        r"&SURF\b[^/]*\bID\s*=\s*'BURNER'[^/]*\bTAU_Q\s*=\s*([0-9.eE+-]+)",
    )
    hrrpua = _first_number(
        text,
        r"&SURF\b[^/]*\bID\s*=\s*'BURNER'[^/]*\bHRRPUA\s*=\s*([0-9.eE+-]+)",
    )
    burner_area = 0.0
    for record in re.findall(r"&VENT\b(.*?)/", text, re.I | re.S):
        if not re.search(r"SURF_ID\s*=\s*'BURNER'", record, re.I):
            continue
        xb = re.search(
            r"\bXB\s*=\s*([0-9.eE+-]+)\s+([0-9.eE+-]+)\s+"
            r"([0-9.eE+-]+)\s+([0-9.eE+-]+)\s+"
            r"([0-9.eE+-]+)\s+([0-9.eE+-]+)",
            record,
            re.I,
        )
        if not xb:
            continue
        values = [float(xb.group(index)) for index in range(1, 7)]
        extents = [abs(values[1] - values[0]), abs(values[3] - values[2]),
                   abs(values[5] - values[4])]
        nonzero = [extent for extent in extents if extent > 1e-12]
        if len(nonzero) == 2:
            burner_area += nonzero[0] * nonzero[1]
    target_hrr = hrrpua * burner_area if hrrpua is not None and burner_area > 0 else None
    return {
        "chid": chid_match.group(1) if chid_match else None,
        "t_end": t_end,
        "tau_q": tau_q,
        "target_hrr_kW": target_hrr,
    }


def _find_version(out_text):
    for line in out_text.splitlines():
        if "FDS" in line.upper():
            match = re.search(r"(\d+\.\d+\.\d+)", line)
            if match:
                return match.group(1)
    return ""


def _completed_successfully(out_text):
    """Recognize FDS' own success line and archived scheduler wrappers."""
    return bool(re.search(
        r"STOP:\s*FDS completed successfully|\bRun completed\b",
        out_text,
        re.I,
    ))


def _exact_key(series, wanted):
    normalized = wanted.upper().replace(" ", "_")
    for key in series:
        if key.strip().upper().replace(" ", "_") == normalized:
            return key
    return None


def _unit_ok(unit, accepted):
    value = (unit or "").upper().replace(" ", "").replace("^", "")
    return value in accepted


def _check_times(times, series, t_end, label, hard_issues):
    if not times:
        hard_issues.append(f"{label} 没有有效时间数据")
        return False, None
    ok = True
    if any(not math.isfinite(value) for value in times):
        hard_issues.append(f"{label} 时间列含 NaN/Inf")
        ok = False
    if any(right <= left for left, right in zip(times, times[1:])):
        hard_issues.append(f"{label} 时间倒序或重复")
        ok = False
    if t_end is not None:
        tolerance = max(1.5, 0.01 * t_end)
        if times[-1] < t_end - tolerance:
            hard_issues.append(f"{label} 只计算到 {times[-1]:g}s，未达到 T_END={t_end:g}s")
            ok = False
    for key, values in series.items():
        if len(values) != len(times) or any(not math.isfinite(value) for value in values):
            hard_issues.append(f"{label} 的 {key} 含缺失值或行数不一致")
            ok = False
    if len(times) >= 4:
        gaps = [right - left for left, right in zip(times, times[1:]) if right > left]
        median = sorted(gaps)[len(gaps) // 2]
        if median > 0 and max(gaps) > 2.5 * median:
            hard_issues.append(f"{label} 时间序列存在异常大缺口")
            ok = False
    return ok, times[-1]


def check_case(case_dir, expected_version=DEFAULT_EXPECTED_VERSION, closure_tolerance=0.20,
               required_temperature_channels=None, require_full_field=False):
    case_dir = Path(case_dir)
    chid = case_dir.name
    hard_issues = []
    review_issues = []
    run_chid, required, meta = _run_artifacts(case_dir, chid)
    for label, path in required.items():
        if not path.is_file() or path.stat().st_size == 0:
            hard_issues.append(f"缺少或空文件: {path.name}")
    end_path = case_dir / f"{run_chid}.end"
    end_marker_present = end_path.is_file() and end_path.stat().st_size > 0

    if required["fds"].is_file():
        meta = _parse_input(required["fds"])
        if meta["chid"] != run_chid:
            hard_issues.append(
                f"输出前缀 {run_chid} 与执行输入 CHID={meta['chid']!r} 不一致"
            )
        if run_chid != chid:
            logical_input = case_dir / f"{chid}.fds"
            logical_text = _read_text(logical_input) if logical_input.is_file() else ""
            if not re.search(r"&CATF\b", logical_text, re.I):
                hard_issues.append(
                    f"文件夹名 {chid} 与执行输入 CHID={run_chid!r} 不一致且无 CATF 依据"
                )

    out_text = _read_text(required["out"]) if required["out"].is_file() else ""
    out_completed_successfully = _completed_successfully(out_text)
    version = _find_version(out_text)
    version_match = not expected_version or expected_version.lower() == "any" or version == expected_version
    if not version:
        review_issues.append(".out 中未识别到 FDS 版本")
    elif not version_match:
        review_issues.append(
            f"FDS 版本为 {version}，项目目标为 {expected_version}；混用前需代表工况对比"
        )
    lines = out_text.splitlines()
    errors = [line.strip() for line in lines
              if re.match(r"^\s*ERROR(?:\(\d+\))?\s*:", line, re.I)]
    warnings = [line.strip() for line in lines if re.search(r"\bWARNING\b", line, re.I)]
    rejected = [line.strip() for line in lines if re.search(r"\brejected\b", line, re.I)]
    burner_issue = any(
        "BURNER" in line.upper() and re.search(r"overlap|rejected", line, re.I)
        for line in lines
    )
    abnormal = [line.strip() for line in lines if re.search(
        r"stopping\s+fds|fatal\s+error|segmentation\s+fault|forrtl:\s*severe|\baborted\b",
        line,
        re.I,
    )]
    if errors:
        hard_issues.append(f"FDS ERROR: {errors[0]}")
    if rejected:
        hard_issues.append(f"存在 rejected: {rejected[0]}")
    if burner_issue:
        hard_issues.append("BURNER VENT 被重叠或拒绝")
    if abnormal:
        hard_issues.append(f"异常终止: {abnormal[0]}")
    if warnings:
        review_issues.append(f"存在 {len(warnings)} 条 WARNING，需查看 .out")

    devc_times, devc_series, devc_units = fds_io.read_devc(str(case_dir), run_chid)
    hrr_times, hrr_series, hrr_units = fds_io.read_hrr(str(case_dir), run_chid)
    devc_time_ok, devc_final = _check_times(
        devc_times, devc_series, meta["t_end"], "DEVC", hard_issues
    )
    hrr_time_ok, hrr_final = _check_times(
        hrr_times, hrr_series, meta["t_end"], "HRR", hard_issues
    )
    if not end_marker_present and not (
        out_completed_successfully and devc_time_ok and hrr_time_ok
    ):
        review_issues.append(
            "缺少 .end，且 .out 正常结束与 CSV 达到 T_END 的替代证据不完整"
        )

    external_channel_keys = []
    if required_temperature_channels:
        missing_external = []
        for channel in required_temperature_channels:
            key = _exact_key(devc_series, channel)
            if key is None:
                missing_external.append(channel)
            else:
                external_channel_keys.append(key)
        required_devc_ok = not missing_external
        if missing_external:
            hard_issues.append(
                "DEVC 缺少外部试验映射通道: " + ", ".join(missing_external)
            )
        temp_keys = external_channel_keys
        velocity_keys = []
        hrr_tot_key = None
        heat_flux_keys = []
    else:
        temp_keys = [key for key in devc_series if re.fullmatch(r"T_\d+", key)]
        velocity_keys = [key for key in devc_series if re.fullmatch(r"U_\d+", key)]
        hrr_tot_key = _exact_key(devc_series, "HRR_tot")
        heat_flux_keys = [key for key in devc_series if re.fullmatch(r"Qw_\d+", key)]
        required_devc_ok = bool(temp_keys and velocity_keys and hrr_tot_key and heat_flux_keys)
        if not required_devc_ok:
            hard_issues.append("DEVC 缺少 T_*、U_*、HRR_tot 或 Qw_* 列")

    if required_devc_ok:
        units_ok = (
            all(_unit_ok(devc_units.get(key), {"C", "K", "DEGC"}) for key in temp_keys)
            and (
                bool(required_temperature_channels)
                or (
                    all(_unit_ok(devc_units.get(key), {"M/S"}) for key in velocity_keys)
                    and _unit_ok(devc_units.get(hrr_tot_key), {"KW", "W", "MW"})
                    and all(_unit_ok(devc_units.get(key), {"KW/M2", "W/M2"})
                            for key in heat_flux_keys)
                )
            )
        )
        if not units_ok:
            hard_issues.append("DEVC 关键列单位缺失或不受支持")
            required_devc_ok = False

    hrr_key = _exact_key(hrr_series, "HRR")
    q_radi_key = _exact_key(hrr_series, "Q_RADI")
    required_hrr_ok = bool(hrr_key and q_radi_key)
    if not required_hrr_ok:
        hard_issues.append("_hrr.csv 必须包含精确列 HRR 和 Q_RADI")
    elif not (_unit_ok(hrr_units.get(hrr_key), {"KW", "W", "MW"})
              and _unit_ok(hrr_units.get(q_radi_key), {"KW", "W", "MW"})):
        hard_issues.append("_hrr.csv 功率单位缺失或不受支持")
        required_hrr_ok = False

    normalized_devc = fds_io.normalize_units(devc_series, devc_units)
    normalized_hrr = fds_io.normalize_units(hrr_series, hrr_units)
    if required_temperature_channels:
        hrr_nonzero = bool(
            hrr_key and normalized_hrr.get(hrr_key)
            and max(normalized_hrr[hrr_key]) > 1.0
        )
    else:
        hrr_nonzero = bool(
            hrr_tot_key and normalized_devc.get(hrr_tot_key)
            and max(normalized_devc[hrr_tot_key]) > 1.0
        )
    if not hrr_nonzero:
        hard_issues.append("HRR 输出未检出有效非零火源")

    tail_hrr = None
    closure_error = None
    target_hrr = meta["target_hrr_kW"]
    if required_hrr_ok and hrr_times and target_hrr and meta["tau_q"] is not None:
        if meta["t_end"] is not None and meta["t_end"] > 3.0 * meta["tau_q"]:
            start = max(3.0 * meta["tau_q"], meta["t_end"] - 30.0)
            indices = [index for index, time in enumerate(hrr_times) if time >= start]
            values = [normalized_hrr[hrr_key][index] for index in indices]
            if values:
                tail_hrr = sum(values) / len(values)
                closure_error = abs(tail_hrr - target_hrr) / target_hrr
                if closure_error > closure_tolerance:
                    review_issues.append(
                        f"末段 HRR 与输入目标相差 {closure_error:.1%}，需确认供氧和稳态"
                    )
        else:
            review_issues.append("T_END 不长于 3×TAU_Q，只能作为短试算")
    elif not required_temperature_channels:
        review_issues.append("无法从输入和 _hrr.csv 完成功率闭合检查")

    input_text = _read_text(required["fds"]) if required["fds"].is_file() else ""
    boundary_requested = bool(re.search(r"&BNDF\b", input_text, re.I))
    has_smv = any(case_dir.glob(f"{run_chid}*.smv"))
    has_slice = (
        any(case_dir.glob(f"{run_chid}*.sf*"))
        or any(case_dir.glob(f"{run_chid}*.s3d*"))
    )
    has_boundary = any(case_dir.glob(f"{run_chid}*.bf"))
    full_field_present = has_smv and has_slice and (
        has_boundary or not boundary_requested
    )
    if require_full_field and not full_field_present:
        required_fields = ".smv 与切片/三维场"
        if boundary_requested:
            required_fields += "及输入所请求的 .bf"
        hard_issues.append(f"外部验证缺少必需全场文件：{required_fields}")
    if hard_issues:
        status = "FAIL"
    elif review_issues:
        status = "REVIEW"
    else:
        status = "PASS"
    return {
        "chid": chid,
        "run_chid": run_chid,
        "status": status,
        "fds_version": version,
        "version_match": version_match,
        "t_end_s": meta["t_end"],
        "devc_t_final_s": devc_final,
        "hrr_t_final_s": hrr_final,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "rejected_count": len(rejected),
        "burner_issue": burner_issue,
        "required_devc_ok": required_devc_ok,
        "required_hrr_ok": required_hrr_ok,
        "time_series_ok": devc_time_ok and hrr_time_ok,
        "hrr_nonzero": hrr_nonzero,
        "target_hrr_kW": target_hrr,
        "tail_hrr_kW": tail_hrr,
        "hrr_closure_rel_error": closure_error,
        "end_marker_present": end_marker_present,
        "out_completed_successfully": out_completed_successfully,
        "full_field_present": full_field_present,
        "issues": "; ".join(hard_issues + review_issues),
    }


def _load_external_channels(path=DEFAULT_EXTERNAL_MAPPING):
    path = Path(path)
    if not path.is_file():
        return {}
    result = {}
    with path.open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            chid = (row.get("chid") or "").strip()
            channel = (row.get("raw_channel") or "").strip()
            if chid and channel:
                result.setdefault(chid, []).append(channel)
    return result


def check_all(run_root, out_path, chids=None, expected_version=DEFAULT_EXPECTED_VERSION,
              closure_tolerance=0.20, external_mapping=DEFAULT_EXTERNAL_MAPPING):
    run_root = Path(run_root)
    if chids:
        case_dirs = [run_root / chid for chid in chids]
    else:
        case_dirs = sorted(
            (path for path in run_root.iterdir() if path.is_dir()),
            key=lambda path: path.name,
        ) if run_root.is_dir() else []
    if not case_dirs:
        raise FileNotFoundError(f"没有找到工况文件夹: {run_root}")
    external_channels = _load_external_channels(external_mapping)
    results = [
        check_case(
            path, expected_version, closure_tolerance,
            required_temperature_channels=external_channels.get(path.name),
            require_full_field=path.name in external_channels,
        )
        for path in case_dirs
    ]
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(results)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rundir", default=str(FDS_RUNS_DIR))
    parser.add_argument("--chids", nargs="*", help="只检查指定工况；省略时扫描全部子文件夹")
    parser.add_argument("--expected-version", default=DEFAULT_EXPECTED_VERSION,
                        help="版本不一致只标记 REVIEW；填 any 可只记录不比较")
    parser.add_argument("--hrr-closure-tolerance", type=float, default=0.20)
    parser.add_argument("--external-mapping", default=str(DEFAULT_EXTERNAL_MAPPING),
                        help="外部试验热电偶映射；映射工况同时强制检查全场文件")
    parser.add_argument("--out", default=output_path("analysis", "quality", "result_check.csv"))
    args = parser.parse_args()
    results = check_all(
        args.rundir, args.out, args.chids, args.expected_version,
        args.hrr_closure_tolerance, args.external_mapping,
    )
    for result in results:
        print(f"[{result['status']}] {result['chid']}: {result['issues'] or '检查通过'}")
    counts = {status: sum(result["status"] == status for result in results)
              for status in ("PASS", "REVIEW", "FAIL")}
    print(f"汇总: PASS={counts['PASS']} REVIEW={counts['REVIEW']} FAIL={counts['FAIL']}")
    print(f"结果表: {args.out}")
    if counts["FAIL"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
