"""
validation_metrics.py — 外部试验复现验证指标（§11.3 / §1.5）

对 FDS 复现结果与试验观测计算：
  - 平均偏差 (Mean Bias)
  - 归一化均方根误差 (NRMSE，按试验峰值 ΔT 归一)
  - 相对离散度 (相对偏差的中位绝对离差)
  - 峰值位置误差
  - 全曲线相关系数 R^2

输入
----
- FDS 顶棚中心线时间平均温升：两列 CSV (x, dT_fds)  —— 由 time_average_bootstrap 产出导出
- 试验观测顶棚温升：两列 CSV (x, dT_exp)  —— 由公开试验数据整理
二者按 x 线性插值到公共网格后比对。

用法:
  python validation_metrics.py --fds exp_fds.csv --exp exp_obs.csv \
      --outdir results/external --label Memorial
"""
import os
import csv
import argparse
import math


def _load_xy(path):
    pts = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if not row or row[0].startswith("#"):
                continue
            try:
                x, y = float(row[0]), float(row[1])
                pts.append((x, y))
            except ValueError:
                continue
    pts.sort()
    return pts


def _interp(pts, xs):
    """线性插值 pts 到 xs。"""
    xs_in = [p[0] for p in pts]; ys_in = [p[1] for p in pts]

    def at(x):
        if x <= xs_in[0]:
            return ys_in[0]
        if x >= xs_in[-1]:
            return ys_in[-1]
        for i in range(len(xs_in) - 1):
            if xs_in[i] <= x <= xs_in[i + 1]:
                t = (x - xs_in[i]) / (xs_in[i + 1] - xs_in[i] + 1e-12)
                return ys_in[i] + t * (ys_in[i + 1] - ys_in[i])
        return float("nan")
    return [at(x) for x in xs]


def metrics(fds_pts, exp_pts):
    lo = max(fds_pts[0][0], exp_pts[0][0])
    hi = min(fds_pts[-1][0], exp_pts[-1][0])
    xs = [lo + i * (hi - lo) / 40 for i in range(41)]
    yf = _interp(fds_pts, xs); ye = _interp(exp_pts, xs)
    pairs = [(f, e) for f, e in zip(yf, ye) if f == f and e == e]
    if not pairs:
        return None
    n = len(pairs)
    peak_exp = max(p[1] for p in pairs)
    bias = sum(f - e for f, e in pairs) / n
    rmse = math.sqrt(sum((f - e) ** 2 for f, e in pairs) / n)
    nrmse = rmse / abs(peak_exp) if abs(peak_exp) > 1e-9 else float("nan")
    # 相对离散度：|f-e|/|e| 的中位
    rels = sorted(abs(f - e) / abs(e) for f, e in pairs if abs(e) > 1e-9)
    rd = rels[len(rels) // 2] if rels else float("nan")
    # R^2
    mean_e = sum(e for f, e in pairs) / n
    ss_res = sum((f - e) ** 2 for f, e in pairs)
    ss_tot = sum((e - mean_e) ** 2 for f, e in pairs)
    r2 = 1 - ss_res / ss_tot if ss_tot > 1e-9 else float("nan")
    # 峰值位置误差
    xp_fds = xs[max(range(n), key=lambda i: pairs[i][0])]
    xp_exp = xs[max(range(n), key=lambda i: pairs[i][1])]
    return dict(n=n, mean_bias=bias, rmse=rmse, nrmse=nrmse,
                rel_disp=rd, r2=r2, xp_err=xp_fds - xp_exp,
                peak_exp=peak_exp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fds", required=True, help="FDS 温升 CSV: x,dT")
    ap.add_argument("--exp", required=True, help="试验温升 CSV: x,dT")
    ap.add_argument("--outdir", default="results/external")
    ap.add_argument("--label", default="case")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    fds_pts = _load_xy(args.fds)
    exp_pts = _load_xy(args.exp)
    if not fds_pts or not exp_pts:
        print("[ERR] 数据为空")
        return
    m = metrics(fds_pts, exp_pts)
    if not m:
        print("[ERR] 无重叠区间")
        return
    path = os.path.join(args.outdir, f"validation_{args.label}.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["label", "n_points", "mean_bias", "rmse", "nrmse",
                    "rel_dispersion", "R2", "peak_pos_err", "peak_exp_dT"])
        w.writerow([args.label, m["n"], round(m["mean_bias"], 3),
                    round(m["rmse"], 3), round(m["nrmse"], 4),
                    round(m["rel_disp"], 4), round(m["r2"], 4),
                    round(m["xp_err"], 3), round(m["peak_exp"], 2)])
    print(f"[OK] -> {path}")
    print(f"  NRMSE={m['nrmse']:.4f}  mean_bias={m['mean_bias']:.2f}°C  "
          f"R²={m['r2']:.3f}  峰值位置误差={m['xp_err']:.2f} m")


if __name__ == "__main__":
    main()
