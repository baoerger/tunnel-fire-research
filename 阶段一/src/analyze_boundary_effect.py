"""
analyze_boundary_effect.py — 隧道长度与洞口边界分析（§11.2 / §1.4）

输入
----
- length_boundary_cases.csv（延长工况；含 L）
- grid_sensitivity_cases.csv（用于取 L=100 基线：弱风 gsA_m / 强风 gsC_m）
- FDS 运行结果目录

产出
----
- boundary_effect_features.csv  各工况 ΔT_p, x_p, κ_u, κ_d, L_back
- boundary_effect_summary.csv   延长 vs 基线 的核心量相对变化
- boundary_effect.png           基线 vs 延长的温度曲线对照

决策门（§1.4）
- 若主体测量区域核心量(ΔT_p,x_p,κ_u,κ_d)变化较小，则 100 m 基准长度与洞口边界满足要求。
- 若不满足：优先延长基准长度(150~200 m)或收缩火源/测量区增大洞口缓冲。

用法:
  python analyze_boundary_effect.py \
      --length ../04_隧道长度与洞口边界/length_boundary_cases.csv \
      --baseline ../03_网格敏感性/grid_sensitivity_cases.csv \
      --rundir fds_cases --outdir results/boundary
"""
import os
import csv
import argparse

import tunnel_config as cfg
import fds_io
from analyze_grid_convergence import analyze_one  # 复用特征提取


def _read(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [{k: v.strip() for k, v in r.items()} for r in csv.DictReader(f)]


def _match_baseline(baselines, q, u, df):
    """按 Q,U,Df 匹配 L=100 基线工况。"""
    for b in baselines:
        if (abs(float(b["Q"]) - q) < 1e-6 and abs(float(b["U"]) - u) < 1e-6
                and abs(float(b["Df"]) - df) < 1e-6):
            return b
    return None


def _rel(v1, v2):
    if v1 in (None, 0) or v2 is None:
        return None
    return abs(v1 - v2) / abs(v1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--length", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--rundir", required=True)
    ap.add_argument("--outdir", default="results/boundary")
    ap.add_argument("--near_exclude", type=float, default=0.3 * cfg.H)
    ap.add_argument("--t0", type=float, default=None)
    ap.add_argument("--t1", type=float, default=None)
    args = ap.parse_args()
    tw = (args.t0, args.t1) if (args.t0 is not None and args.t1 is not None) else None

    os.makedirs(args.outdir, exist_ok=True)
    bases = _read(args.baseline)
    extended = _read(args.length)

    rows = []
    for e in extended:
        if e.get("required", "yes").lower() in ("no", "optional") and not _all_present(args.rundir, e["chid"]):
            print(f"[skip] {e['chid']} (optional, 未运行)")
            continue
        q, u, df = float(e["Q"]), float(e["U"]), float(e["Df"])
        fx = float(e.get("x_fire") or (float(e["L"]) / 2))
        f_ext = analyze_one(args.rundir, e["chid"], fx, args.near_exclude, tw)
        b = _match_baseline(bases, q, u, df)
        f_base = analyze_one(args.rundir, b["chid"], float(b["x_fire"]),
                             args.near_exclude, tw) if b else None
        rows.append(dict(
            chid=e["chid"], L=float(e["L"]), Q=q, U=u,
            dTp_ext=f_ext and f_ext["dT_p"], xp_ext=f_ext and f_ext["x_p"],
            ku_ext=f_ext and f_ext["kappa_u"], kd_ext=f_ext and f_ext["kappa_d"],
            dTp_base=f_base and f_base["dT_p"], xp_base=f_base and f_base["x_p"],
            ku_base=f_base and f_base["kappa_u"], kd_base=f_base and f_base["kappa_d"],
            dTp_rel=_rel(f_ext and f_ext["dT_p"], f_base and f_base["dT_p"]) if f_ext and f_base else None,
            xp_rel=_rel(f_ext and f_ext["x_p"], f_base and f_base["x_p"]) if f_ext and f_base else None,
            ku_rel=_rel(f_ext and f_ext["kappa_u"], f_base and f_base["kappa_u"]) if f_ext and f_base else None,
            kd_rel=_rel(f_ext and f_ext["kappa_d"], f_base and f_base["kappa_d"]) if f_ext and f_base else None,
        ))

    feat_path = os.path.join(args.outdir, "boundary_effect_features.csv")
    cols = ["chid", "L", "Q", "U", "dT_p_ext", "x_p_ext", "kappa_u_ext", "kappa_d_ext",
            "dT_p_base", "x_p_base", "kappa_u_base", "kappa_d_base",
            "dT_p_rel", "x_p_rel", "kappa_u_rel", "kappa_d_rel"]
    with open(feat_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(cols)
        for r in rows:
            w.writerow([r.get(k) for k in ["chid", "L", "Q", "U",
                       "dT_p_ext", "xp_ext", "ku_ext", "kd_ext",
                       "dT_p_base", "xp_base", "ku_base", "kd_base",
                       "dT_p_rel", "xp_rel", "ku_rel", "kd_rel"]])
    print(f"[OK] -> {feat_path}")

    print("\n=== 决策门（§1.4）===")
    thr = 0.05
    ok = True
    for r in rows:
        rels = [r["dT_p_rel"], r["xp_rel"], r["ku_rel"], r["kd_rel"]]
        worst = max([x for x in rels if x is not None] + [0])
        flag = "OK" if worst < thr else "超限"
        ok = ok and worst < thr
        print(f"  {r['chid']} (L={r['L']}): 最大相对变化={worst*100:.1f}% [{flag}]")
    print(f"=> 主体测量区核心量变化是否可忽略(<{thr*100:.0f}%): "
          f"{'是 → 100 m 可用' if ok else '否 → 延长基准长度或收缩测量区'}")


def _all_present(rundir, chid):
    return os.path.isfile(os.path.join(rundir, f"{chid}_devc.csv"))


if __name__ == "__main__":
    main()
