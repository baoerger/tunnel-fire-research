"""
analyze_grid_convergence.py — 网格敏感性分析（§11.1 / §1.3）

输入
----
- 工况表 grid_sensitivity_cases.csv（含 chid, case_group, dx, Q, ...）
- FDS 运行结果目录：含各 <chid>_devc.csv

产出
----
- grid_convergence_features.csv  逐工况 ΔT_p, x_p, κ_u, κ_d, 回流长度, 代表壁面热流
- grid_convergence_summary.csv    按工况(A/B/C)粗→中→细的收敛性(相对变化)
- grid_convergence.png            收敛趋势图（ΔT_p、κ_d 随网格）

决策门（§1.3）
- 最终网格依据论文核心目标量(ΔT_p, x_p, κ_u, κ_d)是否趋于稳定，而非单一尺度指标。
- 若中→细相对变化已 < 设定阈值(默认 5%)，则中网格为合格生产网格。

用法:
  python analyze_grid_convergence.py --csv ../03_网格敏感性/grid_sensitivity_cases.csv \
      --rundir outputs/runs --outdir outputs/analysis/grid_convergence
"""
import os
import csv
import argparse
import math
from collections import defaultdict

import tunnel_config as cfg
import fds_io
from project_paths import FDS_RUNS_DIR, output_path


def _read_cases(csv_path):
    cases = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            row = {k: v.strip() for k, v in row.items()}
            cases.append(row)
    return cases


def _backflow_length(times, series, x_p, t_window=None):
    """上游回流长度：回流区上游边界到峰值的距离。
    用顶棚附近 U_ 设备（u_x<0 表示回流）线性插值求 u_x=0 的最远上游位置。"""
    idx = [i for i, t in enumerate(times)] if t_window is None else \
          [i for i, t in enumerate(times) if t_window[0] <= t <= t_window[1]]
    pts = []
    for fid, vals in series.items():
        if not (fid.startswith("U_") and fid[2:].isdigit()):
            continue
        x = int(fid[2:]) / 100.0
        good = [vals[i] for i in idx if i < len(vals) and math.isfinite(vals[i])]
        u = sum(good) / len(good) if good else float("nan")
        pts.append((x, u))
    pts = sorted(p for p in pts if math.isfinite(p[1]))
    if not pts:
        return None
    # 只统计与火源相连的上游负速区。典型分布是“远处正流、近火源
    # 负流”，所以应由近火源向上游扫描，而不是看到最远点为正就返回 0。
    ups = [p for p in pts if p[0] < x_p]
    if not ups:
        return 0.0
    if ups[-1][1] >= 0:
        # 最近火源的上游测点没有回流；更远处的孤立负值不计入回流区。
        return 0.0
    first_negative = len(ups) - 1
    while first_negative > 0 and ups[first_negative - 1][1] < 0:
        first_negative -= 1
    if first_negative > 0:
        x_pos, u_pos = ups[first_negative - 1]
        x_neg, u_neg = ups[first_negative]
        if u_pos >= 0 and u_neg < 0:
            xu = x_pos + (x_neg - x_pos) * u_pos / (u_pos - u_neg)
            return x_p - xu
    # 所有可用上游点皆回流：边界至少到达最远测点。
    return x_p - ups[0][0]


def _wall_heatflux_avg(times, series, t_window=None):
    """代表壁面净热通量均值（Qw_ 设备）。"""
    idx = [i for i, t in enumerate(times)] if t_window is None else \
          [i for i, t in enumerate(times) if t_window[0] <= t <= t_window[1]]
    vals = []
    for fid, arr in series.items():
        if fid.startswith("Qw_"):
            v = sum(arr[i] for i in idx) / len(idx) if idx else float("nan")
            vals.append(v)
    return sum(vals) / len(vals) if vals else None


def _interp_profile(xs, values, x):
    if not xs or x < xs[0] or x > xs[-1]:
        return None
    for x0, x1, y0, y1 in zip(xs, xs[1:], values, values[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return y0
            return y0 + (x - x0) / (x1 - x0) * (y1 - y0)
    return values[-1] if x == xs[-1] else None


def analyze_one(rundir, chid, fire_x, near_exclude, t_window):
    times, series, units = fds_io.read_devc(rundir, chid)
    series = fds_io.normalize_units(series, units)   # 按 CSV 单位行归一到 °C/kW/kW·m⁻²
    if times is None:
        print(f"[WARN] 无设备输出: {chid}（预期 {chid}_devc.csv）")
        return None
    feat = fds_io.extract_features(times, series, fire_x, cfg.T_AMBIENT_C,
                                   cfg.H, near_exclude=near_exclude,
                                   t_window=t_window)
    if not feat:
        return None
    feat["L_back"] = _backflow_length(times, series, feat["x_p"], t_window)
    feat["qw_rep"] = _wall_heatflux_avg(times, series, t_window)
    xs, dts = fds_io.extract_T_profile(
        times, series, fire_x, cfg.T_AMBIENT_C, t_window=t_window)
    feat["dT_up_2H"] = _interp_profile(xs, dts, fire_x - 2 * cfg.H)
    feat["dT_fire"] = _interp_profile(xs, dts, fire_x)
    feat["dT_down_2H"] = _interp_profile(xs, dts, fire_x + 2 * cfg.H)
    return feat


def _rel_change(v_fine, v_med):
    if v_fine is None or v_med is None:
        return None
    if not (math.isfinite(v_fine) and math.isfinite(v_med)):
        return None
    if v_fine == 0:
        return 0.0 if v_med == 0 else None
    return abs(v_fine - v_med) / abs(v_fine)


def _valid_feature_row(row):
    required = ("dT_p", "x_p", "kappa_u", "kappa_d", "L_back", "qw_rep")
    return all(row.get(k) is not None and math.isfinite(float(row[k])) for k in required)


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="grid_sensitivity_cases.csv")
    ap.add_argument("--rundir", default=str(FDS_RUNS_DIR),
                    help="FDS 运行根目录（默认: outputs/runs）")
    ap.add_argument("--outdir", default=output_path("analysis", "grid_convergence"))
    ap.add_argument("--near_exclude", type=float, default=0.3 * cfg.H,
                    help="近场排除半径 [m]（默认 0.3H）")
    ap.add_argument("--t0", type=float, default=None, help="准稳态窗口起点 [s]")
    ap.add_argument("--t1", type=float, default=None, help="准稳态窗口终点 [s]")
    ap.add_argument("--steady-windows", help="逐工况 steady_windows.csv（优先推荐）")
    ap.add_argument("--threshold_pct", type=float, default=5.0,
                    help="中→细相对变化阈值 %%（默认 5）")
    args = ap.parse_args()
    if (args.t0 is None) != (args.t1 is None):
        ap.error("--t0/--t1 必须成对给出")
    if args.steady_windows and args.t0 is not None:
        ap.error("--steady-windows 与统一 --t0/--t1 不能同时使用")
    common_window = (args.t0, args.t1) if args.t0 is not None else None
    steady_windows = _read_steady_windows(args.steady_windows)

    os.makedirs(args.outdir, exist_ok=True)
    cases = _read_cases(args.csv)

    rows = []
    for c in cases:
        fire_x = float(c.get("x_fire") or cfg.X_FIRE_DEFAULT)
        t_window = steady_windows.get(c["chid"], common_window)
        if t_window is None:
            rows.append(dict(chid=c["chid"], group=c["case_group"], dx=float(c["dx"]),
                             status="FAIL", reason="没有 PASS 准稳态平均窗口", t0=None, t1=None,
                             **{k: None for k in ["x_p", "dT_p", "kappa_u", "kappa_d",
                                                  "L_back", "qw_rep", "dT_up_2H", "dT_fire",
                                                  "dT_down_2H"]}))
            continue
        feat = analyze_one(args.rundir, c["chid"], fire_x, args.near_exclude, t_window)
        if not feat:
            rows.append(dict(chid=c["chid"], group=c["case_group"], dx=float(c["dx"]),
                             status="FAIL", reason="缺少/无效结果、窗口为空或拟合点不足",
                             t0=t_window[0], t1=t_window[1], **{k: None for k in
                       ["x_p", "dT_p", "kappa_u", "kappa_d", "L_back", "qw_rep",
                        "dT_up_2H", "dT_fire", "dT_down_2H"]}))
            continue
        rows.append(dict(
            chid=c["chid"], group=c["case_group"], dx=float(c["dx"]),
            status="PASS", reason="", t0=t_window[0], t1=t_window[1],
            x_p=feat["x_p"], dT_p=feat["dT_p"],
            kappa_u=feat["kappa_u"], kappa_d=feat["kappa_d"],
            L_back=feat["L_back"], qw_rep=feat["qw_rep"],
            dT_up_2H=feat["dT_up_2H"], dT_fire=feat["dT_fire"],
            dT_down_2H=feat["dT_down_2H"],
        ))

    # 写逐工况表
    feat_path = os.path.join(args.outdir, "grid_convergence_features.csv")
    cols = ["chid", "group", "dx", "status", "reason", "t0", "t1", "dT_p", "x_p",
            "kappa_u", "kappa_d", "L_back", "qw_rep", "dT_up_2H", "dT_fire",
            "dT_down_2H"]
    with open(feat_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([r.get(k) for k in cols])
    print(f"[OK] 逐工况特征 -> {feat_path}")

    # 按工况收敛性
    by_group = defaultdict(list)
    for r in rows:
        by_group[r["group"]].append(r)
    summ_rows = []
    for g, rs in by_group.items():
        rs = sorted(rs, key=lambda r: r["dx"])  # 细→粗？我们想要 粗<中<细 dx 降序
        rs = sorted(rs, key=lambda r: -r["dx"])  # dx 大→小 = 粗→细
        if len(rs) != 3 or len({r["dx"] for r in rs}) != 3:
            summ_rows.append(dict(group=g, status="FAIL", reason="必须恰有粗/中/细三个唯一网格"))
            continue
        coarse, med, fine = rs
        if not all(_valid_feature_row(r) for r in rs):
            bad = ",".join(r["chid"] for r in rs if not _valid_feature_row(r))
            summ_rows.append(dict(group=g, status="FAIL", reason=f"无效工况: {bad}",
                                  dx_coarse=coarse["dx"], dx_med=med["dx"], dx_fine=fine["dx"]))
            continue
        metrics = dict(
            dTp_rel_cm=_rel_change(med["dT_p"], coarse["dT_p"]),
            dTp_rel_mf=_rel_change(fine["dT_p"], med["dT_p"]),
            xp_shift_mf=abs(fine["x_p"] - med["x_p"]),
            ku_rel_mf=_rel_change(fine["kappa_u"], med["kappa_u"]),
            kd_rel_mf=_rel_change(fine["kappa_d"], med["kappa_d"]),
            Lback_rel_mf=_rel_change(fine["L_back"], med["L_back"]),
            qw_rel_mf=_rel_change(fine["qw_rep"], med["qw_rep"]),
        )
        if any(v is None for v in metrics.values()):
            status, reason = "FAIL", "存在无法计算的相对变化"
        else:
            status, reason = "READY", "等待按阈值判定"
        summ_rows.append(dict(
                group=g,
                status=status, reason=reason,
                dx_coarse=coarse["dx"], dx_med=med["dx"], dx_fine=fine["dx"],
                **metrics,
            ))

    threshold = args.threshold_pct / 100
    for s in summ_rows:
        if s["status"] != "READY":
            s["converged"] = False
            continue
        values = (s["dTp_rel_mf"], s["ku_rel_mf"], s["kd_rel_mf"])
        s["converged"] = (all(v <= threshold for v in values)
                          and s["xp_shift_mf"] / cfg.H <= threshold)
        s["status"] = "PASS" if s["converged"] else "FAIL"
        s["reason"] = ("核心量中→细变化满足阈值" if s["converged"]
                       else "至少一个核心量中→细变化超阈值")

    summ_path = os.path.join(args.outdir, "grid_convergence_summary.csv")
    with open(summ_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["group", "status", "reason", "dx_coarse", "dx_med", "dx_fine",
                    "dTp_rel(粗→中)%", "dTp_rel(中→细)%", "xp_shift(中→细)/H[%]",
                    "kappa_u_rel(中→细)%", "kappa_d_rel(中→细)%",
                    "L_back_rel(中→细)%", "qw_rel(中→细)%"])
        for s in summ_rows:
            w.writerow([s["group"], s["status"], s["reason"], s.get("dx_coarse"),
                        s.get("dx_med"), s.get("dx_fine"), _pct(s.get("dTp_rel_cm")),
                        _pct(s.get("dTp_rel_mf")),
                        _pct(s.get("xp_shift_mf") / cfg.H if s.get("xp_shift_mf") is not None else None),
                        _pct(s.get("ku_rel_mf")), _pct(s.get("kd_rel_mf")),
                        _pct(s.get("Lback_rel_mf")), _pct(s.get("qw_rel_mf"))])
    print(f"[OK] 收敛性汇总 -> {summ_path}")

    # 决策门提示
    ok = bool(summ_rows) and all(s.get("converged", False) for s in summ_rows)
    print("\n=== 决策门（§1.3）===")
    print(f"中→细相对变化阈值 = {args.threshold_pct}%")
    for s in summ_rows:
        print(f"  {s['group']}: status={s['status']}  ΔTp(中→细)={_pct(s.get('dTp_rel_mf'))}%  "
              f"κ_d(中→细)={_pct(s.get('kd_rel_mf'))}%  κ_u(中→细)={_pct(s.get('ku_rel_mf'))}%")
    print(f"=> 核心目标量是否趋于稳定: {'是 → 中网格(0.25m)可作为生产网格' if ok else '否 → 需进一步加密或核查'}")

    # 收敛图
    _plot(rows, os.path.join(args.outdir, "grid_convergence.png"))


def _pct(x):
    return f"{x*100:.2f}" if x is not None else "NA"


def _plot(rows, outpath):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("[WARN] matplotlib 不可用，跳过绘图。")
        return
    from collections import defaultdict
    by = defaultdict(list)
    for r in rows:
        by[r["group"]].append((r["dx"], r["dT_p"], r["kappa_d"]))
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4))
    for g, pts in by.items():
        pts = sorted(pts)
        dx = [p[0] for p in pts if p[1] is not None]
        dT = [p[1] for p in pts if p[1] is not None]
        kd = [p[2] for p in pts if p[2] is not None]
        dxk = [p[0] for p in pts if p[2] is not None]
        ax1.plot(dx, dT, "o-", label=g)
        ax2.plot(dxk, kd, "s-", label=g)
    ax1.invert_xaxis(); ax2.invert_xaxis()
    ax1.set_xlabel("dx [m] (粗→细)"); ax1.set_ylabel("ΔT_p [°C]"); ax1.legend(fontsize=8)
    ax2.set_xlabel("dx [m] (粗→细)"); ax2.set_ylabel("κ_d"); ax2.legend(fontsize=8)
    fig.suptitle("网格收敛性（核心目标量）")
    fig.tight_layout()
    fig.savefig(outpath, dpi=150)
    plt.close(fig)
    print(f"[OK] 收敛图 -> {outpath}")


if __name__ == "__main__":
    main()
