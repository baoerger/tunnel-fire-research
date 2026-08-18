"""准稳态识别（实施 §1.9）。

正式判据同时检查 HRR 波动、最大顶棚温度趋势、代表测点温度趋势和
上游回流长度趋势。纵向超温焓流若没有专门导出则在结果中明确标为
UNCHECKED，不会伪装成已通过。
"""
import argparse
import csv
import math
import os
from pathlib import Path
from statistics import median

from src.fds import fds_io
from src.fds.project_paths import FDS_RUNS_DIR, output_path
from .analyze_grid_convergence import _backflow_length


# LES 的瞬时燃烧和顶棚峰值本来就会脉动。默认值用于筛掉持续漂移，
# 不要求每个瞬时量近似常数；HRR 的时间均值是否闭合仍由结果检查单独把关。
DEFAULT_THR_HRR = 0.15
DEFAULT_THR_TMAX_SLOPE = 0.15
DEFAULT_THR_REP_SLOPE = 0.15


def _validate_times(times, min_duration, max_gap_factor=1.25):
    if times is None or len(times) < 4:
        return False, "时间样本少于 4 个"
    if any(not math.isfinite(t) for t in times):
        return False, "时间列含非有限值"
    deltas = [b - a for a, b in zip(times, times[1:])]
    if any(delta <= 0 for delta in deltas):
        return False, "时间必须严格递增且不得重复"
    dt = median(deltas)
    # FDS 的 DT_DEVC 是输出目标间隔，不保证每个样本精确等间距。较短的
    # 自适应步不会损失信息；这里只拒绝会在趋势窗口中形成明显空洞的长间隔。
    if max(deltas) > max_gap_factor * dt:
        return False, "时间序列存在过长采样空档"
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


def _rolling_mean(vals, times, window_s):
    """按真实时间作尾随平均，抑制 LES 瞬时脉动而不改变长期趋势。"""
    out = [float("nan")] * len(times)
    for i in range(len(times)):
        idx = _trailing_indices(times, i, window_s)
        segment = [vals[j] for j in idx
                   if j < len(vals) and math.isfinite(vals[j])]
        if segment:
            out[i] = sum(segment) / len(segment)
    return out


def _smooth_observables(times, series, smooth_s):
    if smooth_s <= 0:
        return series
    return {
        key: (_rolling_mean(values, times, smooth_s)
              if key.startswith(("T_", "U_")) else values)
        for key, values in series.items()
    }


def _read_enthalpy_profiles(path, chid):
    """读取 ``cross_section_integrals.csv`` 中同一时刻的 J_T(x) 剖面。"""
    if not path or not Path(path).is_file():
        return None
    required = {"chid", "time_s", "x_m", "J_T_W"}
    values = {}
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError(f"{path} 缺少字段: {','.join(sorted(required))}")
        for row in reader:
            if row["chid"].strip() != chid:
                continue
            time = float(row["time_s"])
            x = float(row["x_m"])
            flux = float(row["J_T_W"])
            if not all(math.isfinite(value) for value in (time, x, flux)):
                raise ValueError(f"{chid} 的 J_T 数据含非有限值")
            key = (time, x)
            if key in values:
                raise ValueError(f"{chid} 的 J_T 数据存在重复时刻/截面 {key}")
            values[key] = flux
    if not values:
        return None
    times = sorted({key[0] for key in values})
    stations = sorted({key[1] for key in values})
    profiles = []
    for time in times:
        if any((time, x) not in values for x in stations):
            raise ValueError(f"{chid} 在 t={time:g} s 的 J_T 截面不完整")
        profiles.append([values[(time, x)] for x in stations])
    valid, reason = _validate_times(times, min_duration=1.0, max_gap_factor=1.25)
    if not valid and "总长不足" not in reason:
        raise ValueError(f"{chid} 的 J_T 时间轴无效: {reason}")
    return times, profiles


def _profile_relative_drift(source_times, profiles, target_times, window_s):
    """J_T(x) 剖面在整个趋势窗口内的相对漂移。

    返回 ``||dJ/dt|| * window / ||mean(J)||``，因此阈值 0.05 表示
    整个趋势窗口的系统漂移约不超过 5%，而不是不合理的 0.05 W/s。
    """
    out = [float("nan")] * len(target_times)
    if not source_times or not profiles:
        return out
    width = len(profiles[0])
    if width < 1 or any(len(profile) != width for profile in profiles):
        raise ValueError("J_T 剖面维度不一致")
    for i, target in enumerate(target_times):
        idx = [j for j, time in enumerate(source_times)
               if target - window_s <= time <= target]
        if len(idx) < 4 or source_times[idx[-1]] - source_times[idx[0]] < 0.8 * window_s:
            continue
        local_times = [source_times[j] for j in idx]
        mt = sum(local_times) / len(local_times)
        denom_t = sum((time - mt) ** 2 for time in local_times)
        if denom_t <= 0:
            continue
        means, slopes = [], []
        for column in range(width):
            vals = [profiles[j][column] for j in idx]
            mean_value = sum(vals) / len(vals)
            means.append(mean_value)
            slopes.append(sum((time - mt) * (value - mean_value)
                              for time, value in zip(local_times, vals)) / denom_t)
        mean_norm = math.sqrt(sum(value * value for value in means))
        slope_norm = math.sqrt(sum(value * value for value in slopes))
        out[i] = slope_norm * window_s / mean_norm if mean_norm > 1e-12 else float("inf")
    return out


def _failure_reason(times, metric_specs):
    """给出最接近合格的时刻和仍超限的量，避免只返回笼统 FAIL。"""
    best = None
    for i, time in enumerate(times):
        values = []
        for _, series, threshold, _ in metric_specs:
            value = series[i]
            if not math.isfinite(value) or threshold <= 0:
                break
            values.append((value, threshold))
        else:
            score = max(value / threshold for value, threshold in values)
            if best is None or score < best[0]:
                best = (score, i, time)
    if best is None:
        return "没有共同合格窗口；有效趋势样本不足"

    _, index, time = best
    failed = []
    for label, series, threshold, formatter in metric_specs:
        value = series[index]
        if value > threshold:
            failed.append(f"{label} {formatter(value)}>{formatter(threshold)}")
    if not failed:
        return "没有共同合格窗口；各判据的可用时刻没有重合"
    return f"没有共同合格窗口；最接近 t={time:.1f} s 时仍超限: " + "、".join(failed)


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


def detect(chid, rundir, window_s=90.0, thr_hrr=DEFAULT_THR_HRR,
           thr_tmax_slope=DEFAULT_THR_TMAX_SLOPE,
           thr_rep_slope=DEFAULT_THR_REP_SLOPE,
           thr_backflow_slope=0.05, thr_enthalpy_slope=0.05,
           min_steady=30.0, smooth_s=15.0, field_integrals_path=None):
    """返回 ``(start, end, info)``；失败原因在 ``info`` 中。"""
    times, series, units = fds_io.read_devc(rundir, chid)
    if times is None:
        return None, None, {"status": "FAIL", "reason": "缺少 DEVC CSV"}
    series = fds_io.normalize_units(series, units)
    trend_window = max(window_s, min_steady)
    valid, reason = _validate_times(times, trend_window)
    if not valid:
        return None, None, {"status": "FAIL", "reason": reason, "times": times}

    hrr_key = _find_hrr(series)
    smoothed = _smooth_observables(times, series, smooth_s)
    temperature_ids, tmax, representative = _temperature_series(times, smoothed)
    backflow = _backflow_series(times, smoothed, temperature_ids)
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

    hrr_osc = _oscillation(series[hrr_key], times, trend_window)
    tmax_slope = _linear_slope(tmax, times, trend_window)
    rep_slope = _linear_slope(representative, times, trend_window)
    backflow_slope = _linear_slope(backflow, times, trend_window)

    # 优先使用真实横截面 J_T(x) 剖面；没有派生文件时兼容单列 J_T/JT。
    if field_integrals_path is None:
        candidate = Path(rundir).resolve().parent / "derived" / "cross_section_integrals.csv"
        field_integrals_path = candidate if candidate.is_file() else None
    enthalpy_profiles = _read_enthalpy_profiles(field_integrals_path, chid)
    enthalpy_key = next((key for key in series
                         if key.strip().upper() in ("J_T", "JT")), None)
    if enthalpy_profiles:
        field_times, field_values = enthalpy_profiles
        enthalpy_drift = _profile_relative_drift(
            field_times, field_values, times, trend_window)
        criterion_enthalpy = "CHECKED_FIELD_PROFILE"
    elif enthalpy_key:
        enthalpy_drift = _profile_relative_drift(
            times, [[value] for value in series[enthalpy_key]], times, trend_window)
        criterion_enthalpy = "CHECKED_DEVC_SERIES"
    else:
        enthalpy_drift = [float("nan")] * len(times)
        criterion_enthalpy = "UNCHECKED_NO_FIELD_DATA"

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
        if enthalpy_profiles or enthalpy_key:
            checks["enthalpy_flux"] = (math.isfinite(enthalpy_drift[i])
                                       and enthalpy_drift[i] <= thr_enthalpy_slope)
        criteria.append(checks)
        flags.append(all(checks.values()))

    # 每个 flag 已总结一个不少于 min_steady 的完整趋势窗口；不再要求随后
    # 每一个 1 s 滚动检查连续 30 s 全部通过。取首个合格趋势窗的末 30 s
    # 作为时间平均窗口，既过滤瞬时湍流尖峰，也保留明确的稳定性门槛。
    end_index = next((i for i, flag in enumerate(flags) if flag), None)
    if end_index is None:
        start = end = None
    else:
        end = times[end_index]
        target_start = end - min_steady
        start = next(time for time in times[:end_index + 1] if time >= target_start)
    status = "PASS" if start is not None else "FAIL"
    metric_specs = [
        ("HRR 波动", hrr_osc, thr_hrr, lambda value: f"{value:.1%}"),
        ("峰值温度趋势", tmax_slope, thr_tmax_slope,
         lambda value: f"{value:.3f} °C/s"),
        ("代表温度趋势", rep_slope, thr_rep_slope,
         lambda value: f"{value:.3f} °C/s"),
        ("回流长度趋势", backflow_slope, thr_backflow_slope,
         lambda value: f"{value:.3f} m/s"),
    ]
    if enthalpy_profiles or enthalpy_key:
        metric_specs.append(("焓流相对漂移", enthalpy_drift,
                             thr_enthalpy_slope, lambda value: f"{value:.1%}"))
    reason = ("检测到准稳态趋势窗" if start is not None
              else _failure_reason(times, metric_specs))
    info = {
        "status": status, "reason": reason, "times": times, "Tmax": tmax,
        "T_representative": representative, "L_back": backflow,
        "hrr_osc": hrr_osc, "tmax_slope": tmax_slope,
        "rep_slope": rep_slope, "backflow_slope": backflow_slope,
        "enthalpy_rel_drift": enthalpy_drift,
        # 兼容旧调用方；数值现为无量纲相对漂移，不再是 W/s。
        "enthalpy_slope": enthalpy_drift,
        "enthalpy_criterion": criterion_enthalpy,
        "criteria": criteria, "flags": flags,
        "config": {"window_s": trend_window, "smooth_s": smooth_s,
                   "thr_hrr": thr_hrr,
                   "thr_tmax_slope": thr_tmax_slope,
                   "thr_rep_slope": thr_rep_slope,
                   "thr_backflow_slope": thr_backflow_slope,
                   "thr_enthalpy_rel_drift": thr_enthalpy_slope,
                   "min_steady": min_steady},
    }
    return start, end, info


def _fmt(value):
    return round(value, 6) if isinstance(value, (int, float)) and math.isfinite(value) else "NaN"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rundir", default=str(FDS_RUNS_DIR))
    parser.add_argument("--chids", nargs="+", required=True)
    parser.add_argument("--outdir", default=output_path("analysis", "steady"))
    parser.add_argument("--window_s", type=float, default=90.0,
                        help="长期趋势判断窗口 [s]（默认 90）")
    parser.add_argument("--smooth_s", type=float, default=15.0,
                        help="温度和速度短时平均窗口 [s]（默认 15）")
    parser.add_argument("--min_steady", type=float, default=30.0)
    parser.add_argument("--avg_duration", type=float, default=30.0)
    parser.add_argument("--thr_hrr", type=float, default=DEFAULT_THR_HRR,
                        help="HRR 变异系数上限（默认 0.15，均值闭合另行检查）")
    parser.add_argument("--thr_tmax_slope", type=float,
                        default=DEFAULT_THR_TMAX_SLOPE,
                        help="平滑后峰值温度长期趋势上限 [°C/s]（默认 0.15）")
    parser.add_argument("--thr_rep_slope", type=float,
                        default=DEFAULT_THR_REP_SLOPE,
                        help="平滑后代表温度长期趋势上限 [°C/s]（默认 0.15）")
    parser.add_argument("--thr_backflow_slope", type=float, default=0.05)
    parser.add_argument("--thr_enthalpy_slope", "--thr_enthalpy_rel_drift",
                        dest="thr_enthalpy_slope", type=float, default=0.05,
                        help="J_T 剖面在趋势窗口内的最大相对漂移（默认 0.05）")
    parser.add_argument("--field-integrals",
                        help="cross_section_integrals.csv；默认自动查找 runs 同级 derived")
    args = parser.parse_args()
    if min(args.window_s, args.min_steady, args.avg_duration, args.smooth_s) <= 0:
        parser.error("窗口和持续时间必须为正数")
    if min(args.thr_hrr, args.thr_tmax_slope, args.thr_rep_slope,
           args.thr_backflow_slope, args.thr_enthalpy_slope) <= 0:
        parser.error("所有判据阈值必须为正数")
    os.makedirs(args.outdir, exist_ok=True)

    rows = []
    for chid in args.chids:
        start, end, info = detect(
            chid, args.rundir, window_s=args.window_s, thr_hrr=args.thr_hrr,
            thr_tmax_slope=args.thr_tmax_slope, thr_rep_slope=args.thr_rep_slope,
            thr_backflow_slope=args.thr_backflow_slope,
            thr_enthalpy_slope=args.thr_enthalpy_slope,
            min_steady=args.min_steady, smooth_s=args.smooth_s,
            field_integrals_path=args.field_integrals)
        avg_end = min(end, start + args.avg_duration) if start is not None else None
        rows.append({
            "chid": chid, "status": info["status"], "reason": info["reason"],
            "t_steady_start": start, "t_steady_end": end,
            "avg_t0": start, "avg_t1": avg_end,
            "enthalpy_flux_criterion": info.get("enthalpy_criterion", "NOT_EVALUATED"),
            "window_s": info.get("config", {}).get("window_s", args.window_s),
            "smooth_s": args.smooth_s, "min_steady_s": args.min_steady,
            "thr_hrr_rel": args.thr_hrr,
            "thr_tmax_C_per_s": args.thr_tmax_slope,
            "thr_rep_C_per_s": args.thr_rep_slope,
            "thr_backflow_m_per_s": args.thr_backflow_slope,
            "thr_enthalpy_rel_drift": args.thr_enthalpy_slope,
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
                             "enthalpy_rel_drift", "enthalpy_flux_criterion", "steady_flag"])
            for i, time in enumerate(info["times"]):
                writer.writerow([time, _fmt(info["Tmax"][i]),
                                 _fmt(info["T_representative"][i]),
                                 _fmt(info["L_back"][i]), _fmt(info["hrr_osc"][i]),
                                 _fmt(info["tmax_slope"][i]), _fmt(info["rep_slope"][i]),
                                 _fmt(info["backflow_slope"][i]),
                                 _fmt(info["enthalpy_rel_drift"][i]),
                                 info["enthalpy_criterion"], int(info["flags"][i])])
        print(f"[{info['status']}] {chid}: {info['reason']}"
              + (f"; 平均窗口 [{start:.1f},{avg_end:.1f}] s" if start is not None else ""))

    columns = ["chid", "status", "reason", "t_steady_start", "t_steady_end",
               "avg_t0", "avg_t1", "enthalpy_flux_criterion", "window_s",
               "smooth_s", "min_steady_s", "thr_hrr_rel", "thr_tmax_C_per_s",
               "thr_rep_C_per_s", "thr_backflow_m_per_s",
               "thr_enthalpy_rel_drift"]
    path = os.path.join(args.outdir, "steady_windows.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    print(f"[OK] 汇总 -> {path}")


if __name__ == "__main__":
    main()
