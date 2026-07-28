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
      --rundir fds_cases --outdir results/grid
"""
import os
import csv
import argparse
from collections import defaultdict

import tunnel_config as cfg
import fds_io


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
        u = sum(vals[i] for i in idx) / len(idx) if idx else float("nan")
        pts.append((x, u))
    pts.sort()
    if not pts:
        return None
    # 找上游(x<x_p)中 u<0 的最远点；用零交点插值
    ups = [p for p in pts if p[0] < x_p]
    if not ups:
        return 0.0
    # 由远及近扫描，记录符号变化
    x0, u_prev = ups[0]
    if u_prev >= 0:
        # 最远上游已无回流
        return 0.0
    for x, u in ups[1:]:
        if u_prev < 0 <= u:
            # 线性插值 u=0
            xu = x0 + (x - x0) * (-u_prev) / (u - u_prev)
            return x_p - xu
        x0, u_prev = x, u
    # 整段上游皆回流：取最远点
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


def analyze_one(rundir, chid, fire_x, near_exclude, t_window):
    times, series, _ = fds_io.read_devc(rundir, chid)
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
    return feat


def _rel_change(v_fine, v_med):
    if v_fine is None or v_med is None or v_fine == 0:
        return None
    return abs(v_fine - v_med) / abs(v_fine)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="grid_sensitivity_cases.csv")
    ap.add_argument("--rundir", required=True, help="FDS 运行结果目录")
    ap.add_argument("--outdir", default="results/grid")
    ap.add_argument("--near_exclude", type=float, default=0.3 * cfg.H,
                    help="近场排除半径 [m]（默认 0.3H）")
    ap.add_argument("--t0", type=float, default=None, help="准稳态窗口起点 [s]")
    ap.add_argument("--t1", type=float, default=None, help="准稳态窗口终点 [s]")
    ap.add_argument("--threshold_pct", type=float, default=5.0,
                    help="中→细相对变化阈值 %%（默认 5）")
    args = ap.parse_args()
    t_window = (args.t0, args.t1) if (args.t0 is not None and args.t1 is not None) else None

    os.makedirs(args.outdir, exist_ok=True)
    cases = _read_cases(args.csv)

    rows = []
    for c in cases:
        fire_x = float(c.get("x_fire") or cfg.X_FIRE_DEFAULT)
        feat = analyze_one(args.rundir, c["chid"], fire_x, args.near_exclude, t_window)
        if not feat:
            rows.append(dict(chid=c["chid"], group=c["case_group"], dx=c["dx"], **{k: None for k in
                       ["x_p", "dT_p", "kappa_u", "kappa_d", "L_back", "qw_rep"]}))
            continue
        rows.append(dict(
            chid=c["chid"], group=c["case_group"], dx=float(c["dx"]),
            x_p=feat["x_p"], dT_p=feat["dT_p"],
            kappa_u=feat["kappa_u"], kappa_d=feat["kappa_d"],
            L_back=feat["L_back"], qw_rep=feat["qw_rep"],
        ))

    # 写逐工况表
    feat_path = os.path.join(args.outdir, "grid_convergence_features.csv")
    cols = ["chid", "group", "dx", "dT_p", "x_p", "kappa_u", "kappa_d", "L_back", "qw_rep"]
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
        med = rs[1] if len(rs) >= 3 else None
        fine = rs[2] if len(rs) >= 3 else (rs[-1])
        coarse = rs[0]
        if med and fine:
            summ_rows.append(dict(
                group=g,
                dx_coarse=coarse["dx"], dx_med=med["dx"], dx_fine=fine["dx"],
                dTp_rel_cm=_rel_change(med["dT_p"], coarse["dT_p"]),
                dTp_rel_mf=_rel_change(fine["dT_p"], med["dT_p"]),
                xp_rel_mf=_rel_change(fine["x_p"], med["x_p"]),
                ku_rel_mf=_rel_change(fine["kappa_u"], med["kappa_u"]),
                kd_rel_mf=_rel_change(fine["kappa_d"], med["kappa_d"]),
            ))
    summ_path = os.path.join(args.outdir, "grid_convergence_summary.csv")
    with open(summ_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["group", "dx_coarse", "dx_med", "dx_fine",
                    "dTp_rel(粗→中)%", "dTp_rel(中→细)%", "xp_rel(中→细)%",
                    "kappa_u_rel(中→细)%", "kappa_d_rel(中→细)%"])
        for s in summ_rows:
            w.writerow([s["group"], s["dx_coarse"], s["dx_med"], s["dx_fine"],
                        _pct(s["dTp_rel_cm"]), _pct(s["dTp_rel_mf"]),
                        _pct(s["xp_rel_mf"]), _pct(s["ku_rel_mf"]), _pct(s["kd_rel_mf"])])
    print(f"[OK] 收敛性汇总 -> {summ_path}")

    # 决策门提示
    ok = all((s["dTp_rel_mf"] or 9) < args.threshold_pct / 100 and
             (s["kd_rel_mf"] or 9) < args.threshold_pct / 100 for s in summ_rows)
    print("\n=== 决策门（§1.3）===")
    print(f"中→细相对变化阈值 = {args.threshold_pct}%")
    for s in summ_rows:
        print(f"  {s['group']}: ΔTp(中→细)={_pct(s['dTp_rel_mf'])}%  "
              f"κ_d(中→细)={_pct(s['kd_rel_mf'])}%  κ_u(中→细)={_pct(s['ku_rel_mf'])}%")
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
