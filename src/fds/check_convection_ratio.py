"""由 FDS ``HRR`` 与 ``Q_RADI`` 检查火源辐射/对流比例。"""
import argparse
import csv
import math
import os

from . import fds_io
from . import tunnel_config as cfg
from .project_paths import FDS_RUNS_DIR, output_path


def _find_key(series, candidates):
    norm = {key.strip().upper().replace(" ", "_"): key for key in series}
    for candidate in candidates:
        key = candidate.strip().upper().replace(" ", "_")
        if key in norm:
            return norm[key]
    return None


def fixed_radiative_fraction(q_mw, u, df, value=cfg.CHI_R_PRESET):
    """后续闭合可调用的固定 χ_r 接口。参数保留以统一签名。"""
    del q_mw, u, df
    return float(value)


def linear_radiative_fraction(q_mw, u, df, coefficients):
    """候选修正接口；系数只能由正式准稳态多工况拟合后提供。"""
    intercept, beta_q, beta_u, beta_df = coefficients
    value = intercept + beta_q * q_mw + beta_u * u + beta_df * df
    return min(1.0, max(0.0, value))


def _mean(values, indices):
    good = [values[i] for i in indices
            if i < len(values) and math.isfinite(values[i])]
    return sum(good) / len(good) if good else None


def _metadata(paths):
    result = {}
    for path in paths or []:
        with open(path, newline="", encoding="utf-8-sig") as stream:
            for row in csv.DictReader(stream):
                chid = (row.get("chid") or "").strip()
                if not chid:
                    continue
                try:
                    result[chid] = {key: float(row[key]) for key in ("Q", "U", "Df")}
                except (KeyError, TypeError, ValueError):
                    continue
    return result


def _steady_windows(path):
    result = {}
    if not path:
        return result
    with open(path, newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            if row.get("status") != "PASS":
                continue
            try:
                result[row["chid"].strip()] = (float(row["avg_t0"]), float(row["avg_t1"]))
            except (KeyError, TypeError, ValueError):
                continue
    return result


def analyze_case(rundir, chid, metadata=None, window=None,
                 closure_review_threshold=0.10):
    row = {"chid": chid, "status": "FAIL", "reason": ""}
    if metadata:
        row.update({"target_Q_MW": metadata.get("Q"), "U_m_per_s": metadata.get("U"),
                    "Df_m": metadata.get("Df")})
    if window:
        row.update({"t0_s": window[0], "t1_s": window[1]})
    times, series, units = fds_io.read_hrr(rundir, chid)
    if times is None:
        row["reason"] = "缺少 _hrr.csv"
        return row
    series = fds_io.normalize_units(series, units)
    hrr_key = _find_key(series, ["HRR"])
    radiative_key = _find_key(series, ["Q_RADI"])
    if not hrr_key or not radiative_key:
        row["reason"] = "_hrr.csv 缺少 HRR 或 Q_RADI"
        return row
    if window:
        if not (math.isfinite(window[0]) and math.isfinite(window[1]) and window[1] > window[0]):
            row["reason"] = "准稳态窗口无效"
            return row
        indices = [i for i, time in enumerate(times) if window[0] <= time <= window[1]]
    else:
        indices = list(range(len(times)))
    if len(indices) < 2:
        row["reason"] = "窗口内有效时间点不足 2 个"
        return row
    total = _mean(series[hrr_key], indices)
    radiative = _mean(series[radiative_key], indices)
    if total is None or radiative is None:
        row["reason"] = "窗口内 HRR/Q_RADI 无有限值"
        return row
    if total <= 1e-6:
        row["reason"] = "HRR 接近零，不能计算比例"
        return row
    convective = total + radiative
    chi_r = -radiative / total
    if radiative > 1e-6 or convective < -1e-6 or convective > total * (1 + 1e-6) or not 0 <= chi_r <= 1:
        row.update({"Q_tot_kW": total, "Q_radi_kW": radiative,
                    "Q_conv_source_kW": convective, "chi_r_realized": chi_r})
        row["reason"] = "能量符号异常（期望 Q_RADI≤0 且 0≤Q_conv≤HRR）"
        return row

    row.update({"Q_tot_kW": total, "Q_radi_kW": radiative,
                "Q_conv_source_kW": convective, "chi_r_realized": chi_r,
                "chi_r_preset": cfg.CHI_R_PRESET,
                "window_source": "steady_windows" if window else "full_series"})
    if metadata and metadata.get("Q") is not None:
        target = metadata["Q"] * 1000.0
        row["hrr_closure_rel_error"] = abs(total - target) / target if target else None
    else:
        row["hrr_closure_rel_error"] = None
    if window is None:
        row["status"] = "REVIEW"
        row["reason"] = "未提供准稳态窗口；全时段结果不得用于最终 χ_r 结论"
    elif not metadata:
        row["status"] = "REVIEW"
        row["reason"] = "缺少目标 Q/U/Df 元数据，无法检查功率闭合与趋势"
    elif row["hrr_closure_rel_error"] is not None and row["hrr_closure_rel_error"] > closure_review_threshold:
        row["status"] = "REVIEW"
        row["reason"] = "准稳态 HRR 功率闭合偏差超阈值"
    else:
        row["status"] = "PASS"
        row["reason"] = "能量符号、窗口和功率闭合检查通过"
    return row


def _linear_slope(rows, x_key):
    pairs = [(row.get(x_key), row.get("chi_r_realized")) for row in rows]
    pairs = [(x, y) for x, y in pairs
             if x is not None and y is not None and math.isfinite(x) and math.isfinite(y)]
    if len(pairs) < 2 or len({x for x, _ in pairs}) < 2:
        return None
    mx = sum(x for x, _ in pairs) / len(pairs)
    my = sum(y for _, y in pairs) / len(pairs)
    denominator = sum((x - mx) ** 2 for x, _ in pairs)
    return sum((x - mx) * (y - my) for x, y in pairs) / denominator


def summarize(rows, spread_threshold=0.05):
    valid = [row for row in rows if row.get("status") == "PASS"]
    summary = {"n_total": len(rows), "n_pass": len(valid), "status": "WAITING"}
    if len(valid) < 2:
        summary.update({"reason": "不足两个 PASS 正式窗口工况，不能判定跨工况稳定性",
                        "candidate_mode": "undetermined"})
        return summary
    values = [row["chi_r_realized"] for row in valid]
    spread = max(values) - min(values)
    summary.update({
        "chi_r_min": min(values), "chi_r_max": max(values), "chi_r_spread": spread,
        "slope_vs_Q_MW": _linear_slope(valid, "target_Q_MW"),
        "slope_vs_U": _linear_slope(valid, "U_m_per_s"),
        "slope_vs_Df": _linear_slope(valid, "Df_m"),
        "candidate_mode": "fixed" if spread <= spread_threshold else "correction",
        "status": "READY_FOR_SCIENTIFIC_REVIEW",
        "reason": ("χ_r 极差未超预设阈值；固定接口为候选" if spread <= spread_threshold
                   else "χ_r 极差超预设阈值；修正接口为候选"),
    })
    return summary


def _write(path, rows, columns):
    with open(path, "w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows({key: row.get(key) for key in columns} for row in rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rundir", default=str(FDS_RUNS_DIR))
    parser.add_argument("--chids", nargs="+", required=True)
    parser.add_argument("--cases-csv", nargs="*", default=[])
    parser.add_argument("--steady-windows")
    parser.add_argument("--outdir", default=output_path("analysis", "convection_ratio"))
    parser.add_argument("--t0", type=float)
    parser.add_argument("--t1", type=float)
    args = parser.parse_args()
    if (args.t0 is None) != (args.t1 is None):
        parser.error("--t0/--t1 必须成对给出")
    if args.steady_windows and args.t0 is not None:
        parser.error("--steady-windows 与统一 --t0/--t1 不能同时使用")
    os.makedirs(args.outdir, exist_ok=True)
    metadata = _metadata(args.cases_csv)
    windows = _steady_windows(args.steady_windows)
    common_window = (args.t0, args.t1) if args.t0 is not None else None
    rows = [analyze_case(args.rundir, chid, metadata.get(chid),
                         windows.get(chid, common_window)) for chid in args.chids]
    columns = ["chid", "status", "reason", "target_Q_MW", "U_m_per_s", "Df_m",
               "t0_s", "t1_s", "window_source", "Q_tot_kW", "Q_radi_kW",
               "Q_conv_source_kW", "chi_r_realized", "chi_r_preset",
               "hrr_closure_rel_error"]
    _write(os.path.join(args.outdir, "convection_ratio.csv"), rows, columns)
    summary = summarize(rows)
    _write(os.path.join(args.outdir, "convection_ratio_summary.csv"), [summary],
           ["status", "reason", "n_total", "n_pass", "chi_r_min", "chi_r_max",
            "chi_r_spread", "slope_vs_Q_MW", "slope_vs_U", "slope_vs_Df",
            "candidate_mode"])
    for row in rows:
        print(f"[{row['status']}] {row['chid']}: {row['reason']}")
    print(f"[{summary['status']}] {summary['reason']}")


if __name__ == "__main__":
    main()
