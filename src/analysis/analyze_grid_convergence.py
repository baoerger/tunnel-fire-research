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
      --rundir runs --outdir derived/grid_convergence
"""
import os
import csv
import argparse
import math
from collections import defaultdict
from pathlib import Path

from src.fds import fds_io
from src.fds import tunnel_config as cfg
from src.fds.project_paths import FDS_RUNS_DIR, output_path


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


def _read_field_integrals(path):
    """读取由真实切片派生的横截面超温焓与纵向焓流。"""
    if not path or not Path(path).is_file():
        return {}
    required = {"chid", "time_s", "x_m", "C_T_J_per_m", "J_T_W", "status"}
    by_chid = defaultdict(list)
    seen = set()
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not required.issubset(reader.fieldnames or ()):
            missing = ",".join(sorted(required - set(reader.fieldnames or ())))
            raise ValueError(f"场积分文件缺少字段: {missing}")
        for row in reader:
            chid = row["chid"].strip()
            time = float(row["time_s"])
            x = float(row["x_m"])
            c_t = float(row["C_T_J_per_m"])
            j_t = float(row["J_T_W"])
            if not all(math.isfinite(value) for value in (time, x, c_t, j_t)):
                raise ValueError(f"{chid} 的场积分含非有限值")
            key = (chid, time, x)
            if key in seen:
                raise ValueError(f"场积分存在重复行: {key}")
            seen.add(key)
            by_chid[chid].append((time, x, c_t, j_t, row["status"].strip()))
    return dict(by_chid)


def _average_field_profile(entries, t_window):
    """按准稳态窗口平均 C_T(x) 与 J_T(x)，并检查时间/截面覆盖。"""
    if not entries or t_window is None:
        return None, "缺少场积分或准稳态窗口"
    t0, t1 = t_window
    selected = [row for row in entries if t0 <= row[0] <= t1]
    if not selected:
        return None, "场积分没有覆盖平均窗口"
    if any(row[4] != "PASS" for row in selected):
        return None, "平均窗口内存在非 PASS 场积分"
    times = sorted({row[0] for row in selected})
    stations = sorted({row[1] for row in selected})
    if len(times) < 4 or times[-1] - times[0] < 0.8 * (t1 - t0):
        return None, "场积分时间覆盖不足"
    values = {(time, x): (c_t, j_t) for time, x, c_t, j_t, _ in selected}
    if any((time, x) not in values for time in times for x in stations):
        return None, "场积分的时刻/截面网格不完整"
    profile = {}
    for x in stations:
        profile[x] = (
            sum(values[(time, x)][0] for time in times) / len(times),
            sum(values[(time, x)][1] for time in times) / len(times),
        )
    return profile, ""


def _profile_rel_change(fine, medium, component):
    """两个同站位剖面的 L2 相对差，component=0/1 对应 C_T/J_T。"""
    if not fine or not medium or set(fine) != set(medium):
        return None
    fine_values = [fine[x][component] for x in sorted(fine)]
    medium_values = [medium[x][component] for x in sorted(fine)]
    denominator = math.sqrt(sum(value * value for value in fine_values))
    if denominator <= 1e-12:
        return None
    numerator = math.sqrt(sum((f_value - m_value) ** 2
                              for f_value, m_value in zip(fine_values, medium_values)))
    return numerator / denominator


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="grid_sensitivity_cases.csv")
    ap.add_argument("--rundir", default=str(FDS_RUNS_DIR),
                    help="FDS 运行根目录（默认: runs）")
    ap.add_argument("--outdir", default=output_path("analysis", "grid_convergence"))
    ap.add_argument("--near_exclude", type=float, default=0.3 * cfg.H,
                    help="近场排除半径 [m]（默认 0.3H）")
    ap.add_argument("--t0", type=float, default=None, help="准稳态窗口起点 [s]")
    ap.add_argument("--t1", type=float, default=None, help="准稳态窗口终点 [s]")
    ap.add_argument("--steady-windows", help="逐工况 steady_windows.csv（优先推荐）")
    ap.add_argument("--field-integrals",
                    help="cross_section_integrals.csv；默认自动查找 runs 同级 derived")
    ap.add_argument("--threshold_pct", type=float, default=5.0,
                    help="中→细相对变化阈值 %%（默认 5）")
    args = ap.parse_args()
    if (args.t0 is None) != (args.t1 is None):
        ap.error("--t0/--t1 必须成对给出")
    if args.steady_windows and args.t0 is not None:
        ap.error("--steady-windows 与统一 --t0/--t1 不能同时使用")
    common_window = (args.t0, args.t1) if args.t0 is not None else None
    steady_windows = _read_steady_windows(args.steady_windows)
    field_path = (Path(args.field_integrals) if args.field_integrals else
                  Path(args.rundir).resolve().parent / "derived" /
                  "cross_section_integrals.csv")
    field_data = _read_field_integrals(field_path)

    os.makedirs(args.outdir, exist_ok=True)
    cases = _read_cases(args.csv)

    rows = []
    field_profiles = {}
    field_profile_rows = []
    for c in cases:
        fire_x = float(c.get("x_fire") or cfg.X_FIRE_DEFAULT)
        t_window = steady_windows.get(c["chid"], common_window)
        if t_window is None:
            rows.append(dict(chid=c["chid"], group=c["case_group"], dx=float(c["dx"]),
                             status="FAIL", reason="没有 PASS 准稳态平均窗口", t0=None, t1=None,
                             **{k: None for k in ["x_p", "dT_p", "kappa_u", "kappa_d",
                                                  "n_up", "n_down",
                                                  "L_back", "qw_rep", "dT_up_2H", "dT_fire",
                                                  "dT_down_2H"]}))
            continue
        profile, profile_reason = _average_field_profile(
            field_data.get(c["chid"]), t_window)
        if profile:
            field_profiles[c["chid"]] = profile
            for x, (c_t, j_t) in sorted(profile.items()):
                field_profile_rows.append({
                    "chid": c["chid"], "group": c["case_group"],
                    "dx": float(c["dx"]), "t0": t_window[0], "t1": t_window[1],
                    "x_m": x, "C_T_bar_J_per_m": c_t, "J_T_bar_W": j_t,
                })
        feat = analyze_one(args.rundir, c["chid"], fire_x, args.near_exclude, t_window)
        if not feat:
            rows.append(dict(chid=c["chid"], group=c["case_group"], dx=float(c["dx"]),
                             status="FAIL", reason="缺少/无效结果、窗口为空或拟合点不足",
                             t0=t_window[0], t1=t_window[1], **{k: None for k in
                       ["x_p", "dT_p", "kappa_u", "kappa_d", "n_up", "n_down",
                        "L_back", "qw_rep",
                        "dT_up_2H", "dT_fire", "dT_down_2H"]}))
            continue
        feature_values = {
            "dT_p": feat["dT_p"], "x_p": feat["x_p"],
            "kappa_u": feat["kappa_u"], "kappa_d": feat["kappa_d"],
            "L_back": feat["L_back"], "qw_rep": feat["qw_rep"],
        }
        missing_features = [
            name for name, value in feature_values.items()
            if value is None or not math.isfinite(value)
        ]
        feature_status = "INCOMPLETE" if missing_features else "PASS"
        feature_reason = ("缺少特征: " + ",".join(missing_features)
                          if missing_features else "")
        if "kappa_d" in missing_features:
            feature_reason += f"（下游拟合点 {feat['n_down']} 个）"
        rows.append(dict(
            chid=c["chid"], group=c["case_group"], dx=float(c["dx"]),
            status=feature_status, reason=feature_reason,
            t0=t_window[0], t1=t_window[1],
            field_profile_status="PASS" if profile else "UNAVAILABLE",
            field_profile_reason=profile_reason,
            x_p=feat["x_p"], dT_p=feat["dT_p"],
            kappa_u=feat["kappa_u"], kappa_d=feat["kappa_d"],
            n_up=feat["n_up"], n_down=feat["n_down"],
            L_back=feat["L_back"], qw_rep=feat["qw_rep"],
            dT_up_2H=feat["dT_up_2H"], dT_fire=feat["dT_fire"],
            dT_down_2H=feat["dT_down_2H"],
        ))

    # 写逐工况表
    feat_path = os.path.join(args.outdir, "grid_convergence_features.csv")
    cols = ["chid", "group", "dx", "status", "reason", "t0", "t1",
            "field_profile_status", "field_profile_reason", "dT_p", "x_p",
            "kappa_u", "kappa_d", "n_up", "n_down", "L_back", "qw_rep",
            "dT_up_2H", "dT_fire",
            "dT_down_2H"]
    with open(feat_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([r.get(k) for k in cols])
    print(f"[OK] 逐工况特征 -> {feat_path}")

    field_profile_path = os.path.join(args.outdir, "grid_convergence_field_profiles.csv")
    field_cols = ["chid", "group", "dx", "t0", "t1", "x_m",
                  "C_T_bar_J_per_m", "J_T_bar_W"]
    with open(field_profile_path, "w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=field_cols)
        writer.writeheader()
        writer.writerows(field_profile_rows)
    print(f"[OK] 场积分平均剖面 -> {field_profile_path}")

    # 按工况收敛性。生产网格决策取决于中→细变化；粗网格不稳定时，
    # 仍保留可计算的中→细结果，而不是把整组指标全部抹成 NA。
    by_group = defaultdict(list)
    for r in rows:
        by_group[r["group"]].append(r)
    threshold = args.threshold_pct / 100
    summ_rows = []
    for g, rs in by_group.items():
        rs = sorted(rs, key=lambda r: -r["dx"])  # dx 大→小 = 粗→中→细
        if len(rs) != 3 or len({r["dx"] for r in rs}) != 3:
            summ_rows.append(dict(
                group=g, status="INCOMPLETE", converged=False,
                reason="必须恰有粗/中/细三个唯一网格",
                assessment_scope="UNAVAILABLE", coarse_complete=False,
                missing_core_metrics="全部",
            ))
            continue
        coarse, med, fine = rs
        xp_shift = (abs(fine["x_p"] - med["x_p"])
                    if all(value is not None and math.isfinite(value)
                           for value in (fine.get("x_p"), med.get("x_p"))) else None)
        metrics = dict(
            dTp_rel_cm=_rel_change(med.get("dT_p"), coarse.get("dT_p")),
            dTp_rel_mf=_rel_change(fine.get("dT_p"), med.get("dT_p")),
            xp_shift_mf=xp_shift,
            ku_rel_mf=_rel_change(fine.get("kappa_u"), med.get("kappa_u")),
            kd_rel_mf=_rel_change(fine.get("kappa_d"), med.get("kappa_d")),
            Lback_rel_mf=_rel_change(fine.get("L_back"), med.get("L_back")),
            qw_rel_mf=_rel_change(fine.get("qw_rep"), med.get("qw_rep")),
            CT_profile_rel_mf=_profile_rel_change(
                field_profiles.get(fine["chid"]), field_profiles.get(med["chid"]), 0),
            JT_profile_rel_mf=_profile_rel_change(
                field_profiles.get(fine["chid"]), field_profiles.get(med["chid"]), 1),
        )
        core = {
            "ΔT_p": metrics["dTp_rel_mf"],
            "x_p": (metrics["xp_shift_mf"] / cfg.H
                    if metrics["xp_shift_mf"] is not None else None),
            "κ_u": metrics["ku_rel_mf"],
            "κ_d": metrics["kd_rel_mf"],
        }
        missing = [name for name, value in core.items() if value is None]
        coarse_complete = _valid_feature_row(coarse)
        scope = "THREE_GRID" if coarse_complete else "MEDIUM_FINE_ONLY"
        if missing:
            converged = False
            status = "INCOMPLETE"
            reason = "中/细网格缺少核心量: " + ",".join(missing)
        else:
            converged = all(value <= threshold for value in core.values())
            status = "PASS" if converged else "FAIL"
            reason = ("核心量中→细变化满足阈值" if converged
                      else "至少一个核心量中→细变化超阈值")
        if not coarse_complete:
            reason += "；粗网格未形成完整特征，中→细结果仍保留"
        summ_rows.append(dict(
            group=g, status=status, reason=reason, converged=converged,
            assessment_scope=scope, coarse_complete=coarse_complete,
            missing_core_metrics=",".join(missing),
            dx_coarse=coarse["dx"], dx_med=med["dx"], dx_fine=fine["dx"],
            **metrics,
        ))

    summ_path = os.path.join(args.outdir, "grid_convergence_summary.csv")
    with open(summ_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["group", "status", "reason", "assessment_scope",
                    "coarse_complete", "missing_core_metrics",
                    "dx_coarse", "dx_med", "dx_fine",
                    "dTp_rel(粗→中)%", "dTp_rel(中→细)%", "xp_shift(中→细)/H[%]",
                    "kappa_u_rel(中→细)%", "kappa_d_rel(中→细)%",
                    "L_back_rel(中→细)%", "qw_rel(中→细)%",
                    "C_T_profile_rel(中→细)%", "J_T_profile_rel(中→细)%"])
        for s in summ_rows:
            w.writerow([s["group"], s["status"], s["reason"],
                        s.get("assessment_scope"), s.get("coarse_complete"),
                        s.get("missing_core_metrics"), s.get("dx_coarse"),
                        s.get("dx_med"), s.get("dx_fine"), _pct(s.get("dTp_rel_cm")),
                        _pct(s.get("dTp_rel_mf")),
                        _pct(s.get("xp_shift_mf") / cfg.H if s.get("xp_shift_mf") is not None else None),
                        _pct(s.get("ku_rel_mf")), _pct(s.get("kd_rel_mf")),
                        _pct(s.get("Lback_rel_mf")), _pct(s.get("qw_rel_mf")),
                        _pct(s.get("CT_profile_rel_mf")),
                        _pct(s.get("JT_profile_rel_mf"))])
    print(f"[OK] 收敛性汇总 -> {summ_path}")

    # 决策门提示
    ok = bool(summ_rows) and all(s.get("converged", False) for s in summ_rows)
    print("\n=== 决策门（§1.3）===")
    print(f"中→细相对变化阈值 = {args.threshold_pct}%")
    for s in summ_rows:
        print(f"  {s['group']}: status={s['status']}  ΔTp(中→细)={_pct(s.get('dTp_rel_mf'))}%  "
              f"κ_d(中→细)={_pct(s.get('kd_rel_mf'))}%  κ_u(中→细)={_pct(s.get('ku_rel_mf'))}%  "
              f"C_T/J_T={_pct(s.get('CT_profile_rel_mf'))}%/{_pct(s.get('JT_profile_rel_mf'))}%")
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
