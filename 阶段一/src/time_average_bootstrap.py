"""
time_average_bootstrap.py — 准稳态时间平均与分块 Bootstrap 不确定性（§14 / §1.9）

原则
----
- 满足准稳态后取 10~30 s 稳定窗口计算时间平均 T_i_bar；
- 主体公式与反演用时间平均温度，不用瞬时湍流波动；
- 不确定性采用分块 Bootstrap；分块长度按温度时间序列自相关特征确定
  （不得把连续时间点视为完全独立样本）。

方法
----
1. 在准稳态窗口 [t0, t0+tau] 内取各测点温升序列；
2. 由自相关函数首过零点估计有效分块长度 L_b；
3. 以 L_b 为块长的非重叠分块作 Bootstrap 重采样 N_boot 次，得 T_i_bar 的均值与标准差；
4. 输出 T_i_bar 与 95% 置信区间。

用法:
  python time_average_bootstrap.py --rundir outputs/runs --chid gsB_m \
      --t0 120 --t1 150 --outdir outputs/analysis/time_average
"""
import os
import csv
import argparse
import math

import tunnel_config as cfg
import fds_io
from project_paths import FDS_RUNS_DIR, output_path

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


def _autocorr_block_len(x):
    """由自相关函数首过零点估计有效分块长度（样本点数）。"""
    if not HAS_NUMPY:
        # 简化：返回固定 5s 等效块
        return 5
    x = np.asarray(x, dtype=float)
    x = x - x.mean()
    n = len(x)
    if n < 4:
        return max(1, n // 2)
    ac = np.correlate(x, x, "full")[n - 1:] / (np.var(x) * n + 1e-12)
    # 首个自相关 <=0 的滞后期
    for k in range(1, len(ac)):
        if ac[k] <= 0:
            return max(1, k)
    return max(1, n // 4)


def block_bootstrap(series, t0, t1, times, n_boot=2000, seed=0):
    """分块 Bootstrap：返回 (mean, std, ci_lo, ci_hi)。"""
    idx = [i for i, t in enumerate(times) if t0 <= t <= t1]
    if len(idx) < 4:
        v = _mean(series, idx)
        return v, float("nan"), float("nan"), float("nan")
    seg = [series[i] for i in idx]
    Lb = _autocorr_block_len(seg)
    nblocks = max(1, len(seg) // Lb)
    # 块索引：第 b 块为 [b*Lb, (b+1)*Lb)
    blocks = [seg[b * Lb:(b + 1) * Lb] for b in range(nblocks)]
    blocks = [b for b in blocks if b]
    if not blocks:
        return _mean(series, idx), float("nan"), float("nan"), float("nan")

    rng = _RNG(seed)
    means = []
    for _ in range(n_boot):
        picked = [blocks[int(rng.next() * nblocks)] for _ in range(nblocks)]
        flat = [v for blk in picked for v in blk]
        means.append(sum(flat) / len(flat))
    mean = sum(seg) / len(seg)
    sd = (sum((m - mean) ** 2 for m in means) / (len(means) - 1)) ** 0.5 if len(means) > 1 else 0.0
    means.sort()
    lo = means[int(0.025 * len(means))]
    hi = means[int(0.975 * len(means)) - 1]
    return mean, sd, lo, hi


class _RNG:
    """确定性 LCG（避免依赖 numpy/random 的全局状态，便于复现）。"""
    def __init__(self, seed):
        self.s = (seed or 1) & 0x7FFFFFFF

    def next(self):
        self.s = (1103515245 * self.s + 12345) & 0x7FFFFFFF
        return self.s / 0x7FFFFFFF


def _mean(series, idx):
    g = [series[i] for i in idx if series[i] == series[i]]
    return sum(g) / len(g) if g else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rundir", default=str(FDS_RUNS_DIR),
                    help="FDS 运行根目录（默认: outputs/runs）")
    ap.add_argument("--chid", required=True)
    ap.add_argument("--t0", type=float, required=True, help="准稳态窗口起点")
    ap.add_argument("--t1", type=float, required=True, help="准稳态窗口终点")
    ap.add_argument("--n_boot", type=int, default=2000)
    ap.add_argument("--outdir", default=output_path("analysis", "time_average"))
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    times, series, units = fds_io.read_devc(args.rundir, args.chid)
    series = fds_io.normalize_units(series, units)
    if times is None:
        print(f"[WARN] 无 {args.chid}_devc.csv")
        return

    rows = []
    for fid, vals in series.items():
        if not (fid.startswith("T_") and fid[2:].isdigit()):
            continue
        x = int(fid[2:]) / 100.0
        mean, sd, lo, hi = block_bootstrap(vals, args.t0, args.t1, times,
                                           n_boot=args.n_boot)
        rows.append(dict(chid=args.chid, sensor=fid, x=round(x, 3),
                         T_bar=round(mean - cfg.T_AMBIENT_C, 4) if mean == mean else None,
                         T_mean_C=round(mean, 4) if mean == mean else None,
                         sd=round(sd, 4) if sd == sd else None,
                         ci_lo=round(lo - cfg.T_AMBIENT_C, 4) if lo == lo else None,
                         ci_hi=round(hi - cfg.T_AMBIENT_C, 4) if hi == hi else None))

    path = os.path.join(args.outdir, f"{args.chid}_Tbar_bootstrap.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["chid", "sensor", "x", "T_bar",
                                          "T_mean_C", "sd", "ci_lo", "ci_hi"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"[OK] {args.chid}: {len(rows)} 测点时间平均 + 分块 Bootstrap -> {path}")
    if rows:
        nsd = [r["sd"] for r in rows if r["sd"] is not None]
        print(f"    ΔT 噪声尺度(sd)中位 ≈ {sorted(nsd)[len(nsd)//2] if nsd else 'NA'} °C "
              f"(可用作 ΔT_noise 参考下限)")


if __name__ == "__main__":
    main()
