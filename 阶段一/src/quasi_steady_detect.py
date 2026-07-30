"""准稳态识别（实施 §1.9）。

正式判据同时检查 HRR 波动、最大顶棚温度趋势、代表测点温度趋势和
上游回流长度趋势。纵向超温焓流若没有专门导出则在结果中明确标为
UNCHECKED，不会伪装成已通过。
"""
import argparse
import csv
import math
import os
from statistics import median

import fds_io
from analyze_grid_convergence import _backflow_length
from project_paths import FDS_RUNS_DIR, output_path


def _validate_times(times, min_duration, max_dt_deviation=0.10):
    if times is None or len(times) < 4:
        return False, "时间样本少于 4 个"
    if any(not math.isfinite(t) for t in times):
        return False, "时间列含非有限值"
    deltas = [b - a for a, b in zip(times, times[1:])]
    if any(delta <= 0 for delta in deltas):
        return False, "时间必须严格递增且不得重复"
    dt = median(deltas)
    if max(abs(delta - dt) / dt for delta in deltas) > max_dt_deviation:
        return False, "时间间隔不规则，超过允许偏差"
    if times[-1] - times[0] < min_duration:
        return False, f"时序总长不足 {min_duration:g} s"
    return True, ""


def _trailing_indices(times, i, window_s):
    start = times[i] - window_s
    idx = [j for j in range(i + 1) if times[j] >= start]
    if not idx or times[idx[-1]] - times[idx[0]] < 0.8 * window_s:
        return []
    return idx


def _oscillation(vals, times, window_s):
    out = [float("nan")] * len(times)
    for i in range(len(times)):
        idx = _trailing_indices(times, i, window_s)
        segment = [vals[j] for j in idx if j < len(vals) and math.isfinite(vals[j])]
        if len(segment) < 3:
            continue
        mean = sum(segment) / len(segment)
        sd = math.sqrt(sum((value - mean) ** 2 for value in segment)
                       / (len(segment) - 1))
        out[i] = sd / abs(mean) if abs(mean) > 1e-9 else float("inf")
    return out


def _linear_slope(vals, times, window_s):
    """滚动最小二乘趋势绝对值，而非瞬时差分噪声。"""
    out = [float("nan")] * len(times)
    for i in range(len(times)):
        idx = _trailing_indices(times, i, window_s)
        pairs = [(times[j], vals[j]) for j in idx
                 if j < len(vals) and math.isfinite(vals[j])]
        if len(pairs) < 3:
            continue
        mt = sum(pair[0] for pair in pairs) / len(pairs)
        mv = sum(pair[1] for pair in pairs) / len(pairs)
        denom = sum((pair[0] - mt) ** 2 for pair in pairs)
        out[i] = abs(sum((t - mt) * (v - mv) for t, v in pairs) / denom) if denom else 0.0
    return out


def _find_hrr(series):
    for preferred in ("HRR_tot", "HRR"):
        for key in series:
            if key.strip().upper() == preferred.upper():
                return key
    return None


def _temperature_series(times, series):
    ids = sorted(key for key in series
                 if key.startswith("T_") and key[2:].isdigit())
    tmax = []
    for i in range(len(times)):
        values = [series[key][i] for key in ids
                  if i < len(series[key]) and math.isfinite(series[key][i])]
        tmax.append(max(values) if values else float("nan"))
    if not ids:
        return ids, tmax, []
    representative_ids = sorted({ids[0], ids[len(ids) // 2], ids[-1]})
    representative = []
    for i in range(len(times)):
        values = [series[key][i] for key in representative_ids
                  if i < len(series[key]) and math.isfinite(series[key][i])]
        representative.append(sum(values) / len(values) if values else float("nan"))
    return ids, tmax, representative


def _backflow_series(times, series, temperature_ids):
    velocity_ids = [key for key in series
                    if key.startswith("U_") and key[2:].isdigit()]
    if not velocity_ids or not temperature_ids:
        return None
    xs = [int(key[2:]) / 100.0 for key in temperature_ids]
    result = []
    for i in range(len(times)):
        temperatures = [series[key][i] if i < len(series[key]) else float("nan")
                        for key in temperature_ids]
        x_peak, _ = fds_io.parabolic_peak(xs, temperatures)
        if x_peak is None:
            result.append(float("nan"))
            continue
        snapshot = {key: [series[key][i]] for key in velocity_ids
                    if i < len(series[key])}
        result.append(_backflow_length([times[i]], snapshot, x_peak))
    return result


def _continuous_start(times, flags, duration):
    start_index = None
    for i, flag in enumerate(flags):
        if flag and start_index is None:
            start_index = i
        elif not flag:
            start_index = None
        if start_index is not None and times[i] - times[start_index] >= duration:
            return times[start_index]
    return None


def detect(chid, rundir, window_s=20.0, thr_hrr=0.05,
           thr_tmax_slope=0.10, thr_rep_slope=0.10,
           thr_backflow_slope=0.05, thr_enthalpy_slope=0.05,
           min_steady=30.0):
    """返回 ``(start, end, info)``；失败原因在 ``info`` 中。"""
    times, series, units = fds_io.read_devc(rundir, chid)
    if times is None:
        return None, None, {"status": "FAIL", "reason": "缺少 DEVC CSV"}
    series = fds_io.normalize_units(series, units)
    valid, reason = _validate_times(times, window_s + min_steady)
    if not valid:
        return None, None, {"status": "FAIL", "reason": reason, "times": times}

    hrr_key = _find_hrr(series)
    temperature_ids, tmax, representative = _temperature_series(times, series)
    backflow = _backflow_series(times, series, temperature_ids)
    missing = []
    if hrr_key is None:
        missing.append("HRR_tot")
    if not temperature_ids:
        missing.append("T_*")
    if backflow is None:
        missing.append("U_*")
    if missing:
        return None, None, {"status": "FAIL", "reason": "缺少必需设备列: " + ",".join(missing),
                            "times": times}

    hrr_osc = _oscillation(series[hrr_key], times, window_s)
    tmax_slope = _linear_slope(tmax, times, window_s)
    rep_slope = _linear_slope(representative, times, window_s)
    backflow_slope = _linear_slope(backflow, times, window_s)

    # 当前生成输入没有 J_T 点设备；后续若切片导出合并为 J_T/JT 序列即可自动检查。
    enthalpy_key = next((key for key in series
                         if key.strip().upper() in ("J_T", "JT")), None)
    enthalpy_slope = (_linear_slope(series[enthalpy_key], times, window_s)
                      if enthalpy_key else [float("nan")] * len(times))
    criterion_enthalpy = "CHECKED" if enthalpy_key else "UNCHECKED_NO_FIELD_DATA"

    flags = []
    criteria = []
    for i in range(len(times)):
        checks = {
            "hrr": math.isfinite(hrr_osc[i]) and hrr_osc[i] <= thr_hrr,
            "tmax": math.isfinite(tmax_slope[i]) and tmax_slope[i] <= thr_tmax_slope,
            "representative_temperature": (math.isfinite(rep_slope[i])
                                           and rep_slope[i] <= thr_rep_slope),
            "backflow": (math.isfinite(backflow_slope[i])
                         and backflow_slope[i] <= thr_backflow_slope),
        }
        if enthalpy_key:
            checks["enthalpy_flux"] = (math.isfinite(enthalpy_slope[i])
                                       and enthalpy_slope[i] <= thr_enthalpy_slope)
        criteria.append(checks)
        flags.append(all(checks.values()))

    start = _continuous_start(times, flags, min_steady)
    status = "PASS" if start is not None else "FAIL"
    reason = "检测到连续准稳态窗口" if start is not None else "没有连续满足全部可用判据的窗口"
    info = {
        "status": status, "reason": reason, "times": times, "Tmax": tmax,
        "T_representative": representative, "L_back": backflow,
        "hrr_osc": hrr_osc, "tmax_slope": tmax_slope,
        "rep_slope": rep_slope, "backflow_slope": backflow_slope,
        "enthalpy_slope": enthalpy_slope, "enthalpy_criterion": criterion_enthalpy,
        "criteria": criteria, "flags": flags,
        "config": {"window_s": window_s, "thr_hrr": thr_hrr,
                   "thr_tmax_slope": thr_tmax_slope,
                   "thr_rep_slope": thr_rep_slope,
                   "thr_backflow_slope": thr_backflow_slope,
                   "thr_enthalpy_slope": thr_enthalpy_slope,
                   "min_steady": min_steady},
    }
    return start, times[-1] if start is not None else None, info


def _fmt(value):
    return round(value, 6) if isinstance(value, (int, float)) and math.isfinite(value) else "NaN"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rundir", default=str(FDS_RUNS_DIR))
    parser.add_argument("--chids", nargs="+", required=True)
    parser.add_argument("--outdir", default=output_path("analysis", "steady"))
    parser.add_argument("--window_s", type=float, default=20.0)
    parser.add_argument("--min_steady", type=float, default=30.0)
    parser.add_argument("--avg_duration", type=float, default=30.0)
    parser.add_argument("--thr_hrr", type=float, default=0.05)
    parser.add_argument("--thr_tmax_slope", type=float, default=0.10)
    parser.add_argument("--thr_rep_slope", type=float, default=0.10)
    parser.add_argument("--thr_backflow_slope", type=float, default=0.05)
    parser.add_argument("--thr_enthalpy_slope", type=float, default=0.05)
    args = parser.parse_args()
    if min(args.window_s, args.min_steady, args.avg_duration) <= 0:
        parser.error("窗口和持续时间必须为正数")
    os.makedirs(args.outdir, exist_ok=True)

    rows = []
    for chid in args.chids:
        start, end, info = detect(
            chid, args.rundir, args.window_s, args.thr_hrr,
            args.thr_tmax_slope, args.thr_rep_slope,
            args.thr_backflow_slope, args.thr_enthalpy_slope,
            args.min_steady)
        avg_end = min(end, start + args.avg_duration) if start is not None else None
        rows.append({
            "chid": chid, "status": info["status"], "reason": info["reason"],
            "t_steady_start": start, "t_steady_end": end,
            "avg_t0": start, "avg_t1": avg_end,
            "enthalpy_flux_criterion": info.get("enthalpy_criterion", "NOT_EVALUATED"),
            "window_s": args.window_s, "min_steady_s": args.min_steady,
            "thr_hrr_rel": args.thr_hrr,
            "thr_tmax_C_per_s": args.thr_tmax_slope,
            "thr_rep_C_per_s": args.thr_rep_slope,
            "thr_backflow_m_per_s": args.thr_backflow_slope,
            "thr_enthalpy_rel_per_s": args.thr_enthalpy_slope,
        })
        if "criteria" not in info:
            print(f"[FAIL] {chid}: {info['reason']}")
            continue
        detail_path = os.path.join(args.outdir, f"{chid}_steady.csv")
        with open(detail_path, "w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream)
            writer.writerow(["time", "Tmax_C", "T_representative_C", "L_back_m",
                             "hrr_osc_rel", "Tmax_slope_C_per_s",
                             "Trep_slope_C_per_s", "Lback_slope_m_per_s",
                             "enthalpy_slope", "enthalpy_flux_criterion", "steady_flag"])
            for i, time in enumerate(info["times"]):
                writer.writerow([time, _fmt(info["Tmax"][i]),
                                 _fmt(info["T_representative"][i]),
                                 _fmt(info["L_back"][i]), _fmt(info["hrr_osc"][i]),
                                 _fmt(info["tmax_slope"][i]), _fmt(info["rep_slope"][i]),
                                 _fmt(info["backflow_slope"][i]),
                                 _fmt(info["enthalpy_slope"][i]),
                                 info["enthalpy_criterion"], int(info["flags"][i])])
        print(f"[{info['status']}] {chid}: {info['reason']}"
              + (f"; 平均窗口 [{start:.1f},{avg_end:.1f}] s" if start is not None else ""))

    columns = ["chid", "status", "reason", "t_steady_start", "t_steady_end",
               "avg_t0", "avg_t1", "enthalpy_flux_criterion", "window_s",
               "min_steady_s", "thr_hrr_rel", "thr_tmax_C_per_s",
               "thr_rep_C_per_s", "thr_backflow_m_per_s",
               "thr_enthalpy_rel_per_s"]
    path = os.path.join(args.outdir, "steady_windows.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    print(f"[OK] 汇总 -> {path}")


if __name__ == "__main__":
    main()
