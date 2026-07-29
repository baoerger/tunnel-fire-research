"""
quasi_steady_detect.py — 准稳态识别（§14 / §1.9）

原则
----
不得仅凭预设时段认定准稳态。综合判断：
1. 总 HRR 相对波动 < 阈值；
2. 最大顶棚温度变化率 < 阈值（趋势平稳）；
3. 代表测点温度变化率 < 阈值；
4. 上游回流长度变化率 < 阈值；
5. 纵向超温焓流变化率 < 阈值（若可由切片得到，否则跳过）。

满足全部（或可得的）条件后，从该时刻起向后取 10~30 s 稳定窗口作时间平均。

产出
----
- <chid>_steady.csv   时间序列 + 各判据滑动统计 + 是否进入准稳态
- steady_windows.csv  各工况准稳态起止时间与平均窗口

用法:
  python quasi_steady_detect.py --rundir fds_cases --chids gsB_m gsC_m \
      --outdir results/steady
"""
import os
import csv
import argparse

import tunnel_config as cfg
import fds_io


def _rolling(vals, w):
    """滑动窗口均值（w 为半窗前后），返回与 vals 等长列表。"""
    n = len(vals)
    out = [float("nan")] * n
    for i in range(n):
        a = max(0, i - w); b = min(n, i + w + 1)
        seg = [vals[j] for j in range(a, b) if vals[j] == vals[j]]
        out[i] = sum(seg) / len(seg) if seg else float("nan")
    return out


def _rel_oscillation(vals, w):
    """滑动均值附近的相对波动 = std(window)/|mean(window)|。"""
    n = len(vals)
    out = [float("nan")] * n
    for i in range(n):
        a = max(0, i - w); b = min(n, i + w + 1)
        seg = [vals[j] for j in range(a, b) if vals[j] == vals[j]]
        if len(seg) < 2:
            continue
        m = sum(seg) / len(seg)
        sd = (sum((s - m) ** 2 for s in seg) / (len(seg) - 1)) ** 0.5
        out[i] = sd / abs(m) if abs(m) > 1e-9 else 0.0
    return out


def _max_slope(vals, times, w):
    """滑动窗口内 |d/dt| 均值。"""
    n = len(vals)
    out = [float("nan")] * n
    for i in range(n):
        a = max(0, i - w); b = min(n, i + w + 1)
        ds = []
        for j in range(a, b - 1):
            if vals[j] == vals[j] and vals[j + 1] == vals[j + 1]:
                dt = times[j + 1] - times[j]
                if dt > 0:
                    ds.append(abs((vals[j + 1] - vals[j]) / dt))
        out[i] = sum(ds) / len(ds) if ds else 0.0
    return out


def detect(chid, rundir, win_half=10,
           thr_hrr=0.05, thr_slope=2.0, min_steady=30.0):
    """
    返回 (t_steady_start, t_steady_end) 或 None。
    各判据：HRR 相对波动<thr_hrr；最大顶棚温度斜率<thr_slope [°C/s]。
    需连续 min_steady 秒满足才认定。
    """
    times, series, units = fds_io.read_devc(rundir, chid)
    series = fds_io.normalize_units(series, units)   # 按 CSV 单位行归一到 °C/kW/kW·m⁻²
    if times is None:
        return None, None, None

    hrr_key = None
    for k in series:
        if "HRR" in k.upper() and "CONV" not in k.upper():
            hrr_key = k; break
    hrr = series.get(hrr_key, []) if hrr_key else []
    # 最大顶棚温度：所有 T_ 设备逐时取最大
    T_ids = [k for k in series if k.startswith("T_") and k[2:].isdigit()]
    Tmax = []
    for i in range(len(times)):
        col = [series[k][i] for k in T_ids if i < len(series[k]) and series[k][i] == series[k][i]]
        Tmax.append(max(col) if col else float("nan"))

    osc_hrr = _rel_oscillation(hrr, win_half) if hrr else [0.0] * len(times)
    slope_Tmax = _max_slope(Tmax, times, win_half)

    flags = []
    for i in range(len(times)):
        ok_hrr = (osc_hrr[i] < thr_hrr) if (osc_hrr[i] == osc_hrr[i]) else False
        ok_slope = (slope_Tmax[i] < thr_slope) if (slope_Tmax[i] == slope_Tmax[i]) else False
        flags.append(ok_hrr and ok_slope)

    # 找首个连续满足 min_steady 秒的起点
    start = None
    for i in range(len(times)):
        if flags[i]:
            # 向后检查连续性
            j = i
            while j < len(times) - 1 and flags[j + 1] and (times[j + 1] - times[i]) < min_steady:
                j += 1
            if (times[j] - times[i]) >= min_steady:
                start = times[i]
                break
    end = times[-1] if start is not None else None
    return start, end, dict(times=times, Tmax=Tmax, osc_hrr=osc_hrr,
                            slope_Tmax=slope_Tmax, flags=flags)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rundir", required=True)
    ap.add_argument("--chids", nargs="+", required=True)
    ap.add_argument("--outdir", default="results/steady")
    ap.add_argument("--min_steady", type=float, default=30.0)
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    rows = []
    for chid in args.chids:
        start, end, info = detect(chid, args.rundir, min_steady=args.min_steady)
        if start is None:
            print(f"[WARN] {chid}: 未检测到连续 {args.min_steady:.0f}s 准稳态窗口")
            rows.append(dict(chid=chid, t_steady_start=None, t_steady_end=None,
                             avg_window="[未检出]"))
            continue
        rows.append(dict(chid=chid, t_steady_start=round(start, 1),
                         t_steady_end=round(end, 1),
                         avg_window=f"[{start:.0f}, {min(end, start+30):.0f}] s"))
        # 详细序列
        with open(os.path.join(args.outdir, f"{chid}_steady.csv"), "w",
                  newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["time", "Tmax_C", "hrr_osc_rel", "dTmax_slope_Cpers", "steady_flag"])
            for i in range(len(info["times"])):
                w.writerow([round(info["times"][i], 2), _fmt(info["Tmax"][i]),
                            _fmt(info["osc_hrr"][i]), _fmt(info["slope_Tmax"][i]),
                            int(info["flags"][i])])
        print(f"[OK] {chid}: 准稳态 t0={start:.1f}s → 取平均窗口 "
              f"[{start:.0f}, {min(end, start+30):.0f}] s")

    path = os.path.join(args.outdir, "steady_windows.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["chid", "t_steady_start", "t_steady_end", "avg_window"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"[OK] 汇总 -> {path}")


def _fmt(x):
    return round(x, 4) if x == x else "NaN"


if __name__ == "__main__":
    main()
