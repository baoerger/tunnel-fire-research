"""隧道长度与洞口边界分析（实施 §1.4）。

所有纵向位置先转换为相对火源坐标再比较；缺结果、无准稳态窗口、
衰减拟合失败或主体测量区曲线无法对齐时均显式 FAIL，绝不默认通过。
"""
import argparse
import csv
import math
import os

from src.fds import fds_io
from src.fds import tunnel_config as cfg
from src.fds.project_paths import FDS_RUNS_DIR, output_path
from .analyze_grid_convergence import analyze_one


def _read(path):
    with open(path, newline="", encoding="utf-8-sig") as stream:
        return [{k: (v or "").strip() for k, v in row.items()}
                for row in csv.DictReader(stream)]


def _match_baseline(baselines, q, u, df):
    for row in baselines:
        if (abs(float(row["Q"]) - q) < 1e-6
                and abs(float(row["U"]) - u) < 1e-6
                and abs(float(row["Df"]) - df) < 1e-6):
            return row
    return None


def _rel(value, reference):
    if value is None or reference is None:
        return None
    if not (math.isfinite(value) and math.isfinite(reference)):
        return None
    if reference == 0:
        return 0.0 if value == 0 else None
    return abs(value - reference) / abs(reference)


def _interp(points, x):
    """只在数据支撑区内线性插值；不向洞口外推。"""
    if not points or x < points[0][0] or x > points[-1][0]:
        return None
    for left, right in zip(points, points[1:]):
        if left[0] <= x <= right[0]:
            if right[0] == left[0]:
                return left[1]
            ratio = (x - left[0]) / (right[0] - left[0])
            return left[1] + ratio * (right[1] - left[1])
    return points[-1][1] if x == points[-1][0] else None


def _temperature_profile(rundir, chid, fire_x, t_window):
    times, series, units = fds_io.read_devc(rundir, chid)
    if times is None:
        return None
    series = fds_io.normalize_units(series, units)
    xs, dts = fds_io.extract_T_profile(
        times, series, fire_x, cfg.T_AMBIENT_C, t_window=t_window)
    if xs is None:
        return None
    return sorted((x - fire_x, value) for x, value in zip(xs, dts)
                  if math.isfinite(value))


def _profile_comparison(base_profile, ext_profile):
    """在基线主体测点的相对坐标上比较延长域曲线。"""
    rows = []
    for x_rel, base_dt in base_profile or []:
        ext_dt = _interp(ext_profile or [], x_rel)
        if ext_dt is None:
            continue
        diff = ext_dt - base_dt
        rows.append((x_rel, base_dt, ext_dt, diff))
    if len(rows) < 6:
        return rows, None
    rmse = math.sqrt(sum(row[3] ** 2 for row in rows) / len(rows))
    scale = max(abs(row[1]) for row in rows)
    nrmse = rmse / scale if scale > 1e-9 else None
    max_abs = max(abs(row[3]) for row in rows)
    return rows, {"profile_n": len(rows), "profile_rmse_C": rmse,
                  "profile_nrmse": nrmse, "profile_max_abs_C": max_abs}


def _feature_valid(feature):
    keys = ("dT_p", "x_p", "kappa_u", "kappa_d", "L_back")
    return feature is not None and all(
        feature.get(key) is not None and math.isfinite(float(feature[key]))
        for key in keys)


def analyze_pair(rundir, extended, baseline, near_exclude, t_window,
                 threshold=0.05, baseline_window=None):
    """分析一对长度工况，返回汇总行与逐测点曲线差。"""
    q, u, df = (float(extended[key]) for key in ("Q", "U", "Df"))
    fx_ext = float(extended.get("x_fire") or float(extended["L"]) / 2)
    fx_base = float(baseline["x_fire"])
    baseline_window = t_window if baseline_window is None else baseline_window
    f_ext = analyze_one(rundir, extended["chid"], fx_ext, near_exclude, t_window)
    f_base = analyze_one(rundir, baseline["chid"], fx_base, near_exclude, baseline_window)
    row = {
        "chid": extended["chid"], "baseline_chid": baseline["chid"],
        "L": float(extended["L"]), "Q": q, "U": u, "Df": df,
        "status": "FAIL", "reason": "", "t0_ext": t_window and t_window[0],
        "t1_ext": t_window and t_window[1],
        "t0_base": baseline_window and baseline_window[0],
        "t1_base": baseline_window and baseline_window[1],
    }
    if not (_feature_valid(f_ext) and _feature_valid(f_base)):
        row["reason"] = "缺少/无效结果或衰减拟合点不足"
        return row, []

    base_profile = _temperature_profile(rundir, baseline["chid"], fx_base, baseline_window)
    ext_profile = _temperature_profile(rundir, extended["chid"], fx_ext, t_window)
    point_rows, profile_metrics = _profile_comparison(base_profile, ext_profile)
    if profile_metrics is None:
        row["reason"] = "主体测量区可比较温度点不足 6 个"
        return row, point_rows

    row.update({
        "dT_p_ext": f_ext["dT_p"], "dT_p_base": f_base["dT_p"],
        "delta_p_ext": f_ext["x_p"] - fx_ext,
        "delta_p_base": f_base["x_p"] - fx_base,
        "kappa_u_ext": f_ext["kappa_u"], "kappa_u_base": f_base["kappa_u"],
        "kappa_d_ext": f_ext["kappa_d"], "kappa_d_base": f_base["kappa_d"],
        "L_back_ext": f_ext["L_back"], "L_back_base": f_base["L_back"],
        "dT_p_rel": _rel(f_ext["dT_p"], f_base["dT_p"]),
        "delta_p_shift_H": abs((f_ext["x_p"] - fx_ext)
                                - (f_base["x_p"] - fx_base)) / cfg.H,
        "kappa_u_rel": _rel(f_ext["kappa_u"], f_base["kappa_u"]),
        "kappa_d_rel": _rel(f_ext["kappa_d"], f_base["kappa_d"]),
        "L_back_rel": _rel(f_ext["L_back"], f_base["L_back"]),
        **profile_metrics,
    })
    core = (row["dT_p_rel"], row["delta_p_shift_H"], row["kappa_u_rel"],
            row["kappa_d_rel"], row["profile_nrmse"])
    if any(value is None for value in core):
        row["reason"] = "至少一个核心比较量不可计算"
    elif all(value <= threshold for value in core):
        row["status"] = "PASS"
        row["reason"] = "核心量与主体测量区曲线变化满足阈值"
    else:
        row["reason"] = "至少一个核心量或主体测量区曲线变化超阈值"

    points = [{"chid": row["chid"], "baseline_chid": row["baseline_chid"],
               "x_rel_fire_m": x, "dT_base_C": b, "dT_extended_C": e,
               "difference_C": d}
              for x, b, e, d in point_rows]
    return row, points


def _write_csv(path, rows, columns):
    with open(path, "w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows({key: row.get(key) for key in columns} for row in rows)


def _read_steady_windows(path):
    windows = {}
    if not path:
        return windows
    with open(path, newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            if row.get("status") != "PASS":
                continue
            try:
                windows[row["chid"].strip()] = (float(row["avg_t0"]), float(row["avg_t1"]))
            except (KeyError, TypeError, ValueError):
                continue
    return windows


def _plot(profile_rows, outpath):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("[WARN] matplotlib 不可用，跳过边界温度曲线图。")
        return
    by_case = {}
    for row in profile_rows:
        by_case.setdefault(row["chid"], []).append(row)
    if not by_case:
        return
    fig, axes = plt.subplots(len(by_case), 1, figsize=(8, 3.5 * len(by_case)),
                             squeeze=False)
    for axis, (chid, rows) in zip(axes[:, 0], sorted(by_case.items())):
        rows.sort(key=lambda row: row["x_rel_fire_m"])
        xs = [row["x_rel_fire_m"] for row in rows]
        axis.plot(xs, [row["dT_base_C"] for row in rows], "o-", label="100 m baseline")
        axis.plot(xs, [row["dT_extended_C"] for row in rows], "s-", label=chid)
        axis.set(xlabel="x-x_f [m]", ylabel="ΔT [°C]", title=chid)
        axis.legend()
    fig.tight_layout()
    fig.savefig(outpath, dpi=150)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--length", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--rundir", default=str(FDS_RUNS_DIR))
    parser.add_argument("--outdir", default=output_path("analysis", "boundary_effect"))
    parser.add_argument("--near_exclude", type=float, default=0.3 * cfg.H)
    parser.add_argument("--t0", type=float)
    parser.add_argument("--t1", type=float)
    parser.add_argument("--steady-windows", help="逐工况 steady_windows.csv（优先推荐）")
    parser.add_argument("--threshold_pct", type=float, default=5.0)
    args = parser.parse_args()
    if (args.t0 is None) != (args.t1 is None) or (
            args.t0 is not None and args.t1 <= args.t0):
        parser.error("--t0/--t1 必须成对给出且 t1>t0")
    if args.steady_windows and args.t0 is not None:
        parser.error("--steady-windows 与统一 --t0/--t1 不能同时使用")
    common_window = (args.t0, args.t1) if args.t0 is not None else None
    steady_windows = _read_steady_windows(args.steady_windows)
    os.makedirs(args.outdir, exist_ok=True)

    baselines = _read(args.baseline)
    extended_cases = _read(args.length)
    summaries, profile_rows = [], []
    for case in extended_cases:
        if case.get("required", "yes").lower() == "optional" and not _all_present(args.rundir, case["chid"]):
            print(f"[skip] {case['chid']} (optional, 未回传)")
            continue
        baseline = _match_baseline(baselines, float(case["Q"]), float(case["U"]), float(case["Df"]))
        if baseline is None:
            summaries.append({"chid": case["chid"], "L": float(case["L"]),
                              "status": "FAIL", "reason": "找不到 Q/U/Df 匹配的 100 m 基线"})
            continue
        ext_window = steady_windows.get(case["chid"], common_window)
        base_window = steady_windows.get(baseline["chid"], common_window)
        if ext_window is None or base_window is None:
            summaries.append({"chid": case["chid"], "baseline_chid": baseline["chid"],
                              "L": float(case["L"]), "status": "FAIL",
                              "reason": "延长或基线工况没有 PASS 准稳态平均窗口"})
            continue
        summary, points = analyze_pair(
            args.rundir, case, baseline, args.near_exclude, ext_window,
            args.threshold_pct / 100, baseline_window=base_window)
        summaries.append(summary)
        profile_rows.extend(points)

    summary_columns = [
        "chid", "baseline_chid", "L", "Q", "U", "Df", "status", "reason",
        "t0_ext", "t1_ext", "t0_base", "t1_base",
        "dT_p_ext", "dT_p_base", "dT_p_rel", "delta_p_ext", "delta_p_base",
        "delta_p_shift_H", "kappa_u_ext", "kappa_u_base", "kappa_u_rel",
        "kappa_d_ext", "kappa_d_base", "kappa_d_rel", "L_back_ext",
        "L_back_base", "L_back_rel", "profile_n", "profile_rmse_C",
        "profile_nrmse", "profile_max_abs_C",
    ]
    # features 保留逐工况原量；summary 为同一证据加明确决策状态。
    _write_csv(os.path.join(args.outdir, "boundary_effect_features.csv"), summaries,
               summary_columns)
    _write_csv(os.path.join(args.outdir, "boundary_effect_summary.csv"), summaries,
               summary_columns)
    point_columns = ["chid", "baseline_chid", "x_rel_fire_m", "dT_base_C",
                     "dT_extended_C", "difference_C"]
    _write_csv(os.path.join(args.outdir, "boundary_effect_profile_differences.csv"),
               profile_rows, point_columns)
    _plot(profile_rows, os.path.join(args.outdir, "boundary_effect.png"))

    all_pass = bool(summaries) and all(row["status"] == "PASS" for row in summaries)
    print("\n=== 决策门（§1.4）===")
    for row in summaries:
        print(f"  {row['chid']}: {row['status']} - {row['reason']}")
    print("=> 100 m 可用" if all_pass else "=> 尚不能确认 100 m 可用；检查 FAIL/缺失结果")


def _all_present(rundir, chid):
    return fds_io._find_devc(rundir, chid) is not None


if __name__ == "__main__":
    main()
