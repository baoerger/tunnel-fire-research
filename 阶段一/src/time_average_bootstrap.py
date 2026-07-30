"""准稳态时间平均与分块 Bootstrap（实施 §1.9）。

既支持单工况显式 ``--t0/--t1``，也可直接消费
``quasi_steady_detect.py`` 产出的 ``steady_windows.csv``，避免人工抄写窗口。
"""
import argparse
import csv
import math
import os
from statistics import median

import fds_io
import tunnel_config as cfg
from project_paths import FDS_RUNS_DIR, output_path

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


def _autocorr_block_len(values):
    clean = [float(value) for value in values if math.isfinite(value)]
    n = len(clean)
    if n < 4:
        return max(1, n // 2)
    if not HAS_NUMPY:
        # 无 numpy 时用保守的 sqrt(n) 块长；不假称固定“5 s”。
        return max(1, int(round(math.sqrt(n))))
    array = np.asarray(clean, dtype=float)
    array = array - array.mean()
    variance = float(np.var(array))
    if variance <= 1e-15:
        return max(1, int(round(math.sqrt(n))))
    autocorrelation = np.correlate(array, array, "full")[n - 1:] / (variance * n)
    for lag in range(1, len(autocorrelation)):
        if autocorrelation[lag] <= 0:
            return max(1, lag)
    return max(1, n // 4)


def validate_window(times, t0, t1, min_duration=10.0, max_dt_deviation=0.10):
    if times is None or len(times) < 4:
        raise ValueError("时间样本少于 4 个")
    if not (math.isfinite(t0) and math.isfinite(t1) and t1 > t0):
        raise ValueError("平均窗口必须满足有限的 t1>t0")
    if t1 - t0 < min_duration:
        raise ValueError(f"平均窗口短于最小 {min_duration:g} s")
    deltas = [b - a for a, b in zip(times, times[1:])]
    if any(delta <= 0 for delta in deltas):
        raise ValueError("时间必须严格递增且不得重复")
    dt = median(deltas)
    if max(abs(delta - dt) / dt for delta in deltas) > max_dt_deviation:
        raise ValueError("时间间隔不规则，超过允许偏差")
    idx = [i for i, time in enumerate(times) if t0 <= time <= t1]
    if len(idx) < 4:
        raise ValueError("平均窗口内有效时间样本少于 4 个")
    if times[idx[-1]] - times[idx[0]] < 0.8 * (t1 - t0):
        raise ValueError("数据未覆盖足够的指定平均窗口")
    return idx


class _RNG:
    """确定性 LCG，避免依赖全局随机状态。"""
    def __init__(self, seed):
        self.state = (seed or 1) & 0x7FFFFFFF

    def next(self):
        self.state = (1103515245 * self.state + 12345) & 0x7FFFFFFF
        return self.state / 0x80000000


def block_bootstrap_stats(series, t0, t1, times, n_boot=2000, seed=0,
                          min_duration=10.0):
    if n_boot < 20:
        raise ValueError("n_boot 必须至少为 20")
    idx = validate_window(times, t0, t1, min_duration=min_duration)
    segment = [series[i] for i in idx
               if i < len(series) and math.isfinite(series[i])]
    if len(segment) < 4:
        raise ValueError("窗口内有限观测少于 4 个")
    block_len = min(_autocorr_block_len(segment), len(segment))
    # 保留最后一个不完整块，避免系统性丢弃窗口尾部。
    blocks = [segment[start:start + block_len]
              for start in range(0, len(segment), block_len)]
    rng = _RNG(seed)
    means = []
    target_n = len(segment)
    for _ in range(n_boot):
        sample = []
        while len(sample) < target_n:
            sample.extend(blocks[int(rng.next() * len(blocks))])
        sample = sample[:target_n]
        means.append(sum(sample) / len(sample))
    mean = sum(segment) / len(segment)
    sd = math.sqrt(sum((value - mean) ** 2 for value in means) / (len(means) - 1))
    means.sort()
    lo = means[max(0, int(0.025 * len(means)))]
    hi = means[min(len(means) - 1, math.ceil(0.975 * len(means)) - 1)]
    return {"mean": mean, "sd": sd, "ci_lo": lo, "ci_hi": hi,
            "block_len_samples": block_len, "n_samples": len(segment)}


def block_bootstrap(series, t0, t1, times, n_boot=2000, seed=0):
    """兼容原接口，返回 mean/std/CI 四元组。"""
    stats = block_bootstrap_stats(series, t0, t1, times, n_boot=n_boot, seed=seed)
    return stats["mean"], stats["sd"], stats["ci_lo"], stats["ci_hi"]


def average_case(rundir, chid, t0, t1, n_boot=2000, seed=0):
    times, series, units = fds_io.read_devc(rundir, chid)
    if times is None:
        raise ValueError(f"缺少 {chid}_devc.csv")
    series = fds_io.normalize_units(series, units)
    rows = []
    for sensor_index, (fid, values) in enumerate(sorted(series.items())):
        if not (fid.startswith("T_") and fid[2:].isdigit()):
            continue
        stats = block_bootstrap_stats(
            values, t0, t1, times, n_boot=n_boot, seed=seed + sensor_index)
        x = int(fid[2:]) / 100.0
        rows.append({
            "chid": chid, "sensor": fid, "x": x, "t0": t0, "t1": t1,
            "T_bar": stats["mean"] - cfg.T_AMBIENT_C,
            "T_mean_C": stats["mean"], "sd": stats["sd"],
            "ci_lo": stats["ci_lo"] - cfg.T_AMBIENT_C,
            "ci_hi": stats["ci_hi"] - cfg.T_AMBIENT_C,
            "block_len_samples": stats["block_len_samples"],
            "n_samples": stats["n_samples"], "n_boot": n_boot, "seed": seed,
        })
    if not rows:
        raise ValueError(f"{chid} 没有 T_* 温度设备列")
    return rows


def _read_windows(path):
    windows = []
    with open(path, newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            if row.get("status") != "PASS":
                continue
            try:
                t0, t1 = float(row["avg_t0"]), float(row["avg_t1"])
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"steady_windows.csv 中 {row.get('chid')} 缺少有效 avg_t0/avg_t1")
            windows.append((row["chid"].strip(), t0, t1))
    if not windows:
        raise ValueError("steady_windows.csv 中没有 PASS 工况")
    return windows


def _write_rows(path, rows):
    columns = ["chid", "sensor", "x", "t0", "t1", "T_bar", "T_mean_C",
               "sd", "ci_lo", "ci_hi", "block_len_samples", "n_samples",
               "n_boot", "seed"]
    with open(path, "w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: (round(row[key], 6) if isinstance(row.get(key), float)
                                   else row.get(key)) for key in columns})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rundir", default=str(FDS_RUNS_DIR))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--chid")
    group.add_argument("--steady-windows", help="批量读取 steady_windows.csv")
    parser.add_argument("--t0", type=float)
    parser.add_argument("--t1", type=float)
    parser.add_argument("--n_boot", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--outdir", default=output_path("analysis", "time_average"))
    args = parser.parse_args()
    if args.chid and (args.t0 is None or args.t1 is None):
        parser.error("单工况模式必须同时给出 --t0 与 --t1")
    if args.steady_windows and (args.t0 is not None or args.t1 is not None):
        parser.error("批量窗口模式不接受 --t0/--t1")
    os.makedirs(args.outdir, exist_ok=True)

    try:
        windows = ([(args.chid, args.t0, args.t1)] if args.chid
                   else _read_windows(args.steady_windows))
        all_rows = []
        for case_index, (chid, t0, t1) in enumerate(windows):
            rows = average_case(args.rundir, chid, t0, t1, args.n_boot,
                                args.seed + case_index * 10000)
            all_rows.extend(rows)
            _write_rows(os.path.join(args.outdir, f"{chid}_Tbar_bootstrap.csv"), rows)
            print(f"[PASS] {chid}: {len(rows)} 测点，窗口 [{t0:g},{t1:g}] s")
        _write_rows(os.path.join(args.outdir, "time_average_database.csv"), all_rows)
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
