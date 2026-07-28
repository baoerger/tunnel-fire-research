"""
fds_io.py — FDS 设备输出读取与温度曲线特征提取（阶段一共享）

读取 FDS 设备 CSV: <chid>_devc.csv（FDS 默认随设备输出产生）。
格式兼容：首行设备 ID、次行单位、其后数据行（首列为时间）。
对列名做大小写/空白模糊匹配，便于跨 FDS 版本使用。

本模块仅依赖标准库 + numpy（可选；缺失时退化为纯 Python 列表运算）。
"""
import os
import csv
import math

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


def _find_devc(chid_dir, chid):
    """定位 <chid>_devc.csv；若不存在，回退尝试 <chid>.devc（遗留文本格式）。"""
    candidates = [
        os.path.join(chid_dir, f"{chid}_devc.csv"),
        os.path.join(chid_dir, f"{chid}.devc"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


def read_devc(chid_dir, chid):
    """
    读取设备时序。

    返回
    ----
    times : list[float]
    series : dict {device_id: list[float]}
    units  : dict {device_id: str}
    若文件不存在返回 (None, {}, {})。
    """
    path = _find_devc(chid_dir, chid)
    if path is None:
        return None, {}, {}

    with open(path, newline="", encoding="utf-8-sig", errors="ignore") as f:
        reader = list(csv.reader(f))
    if len(reader) < 2:
        return None, {}, {}

    header = [c.strip() for c in reader[0]]
    units_row = [c.strip() for c in reader[1]] if len(reader) > 1 else [""] * len(header)

    # 首列通常为时间。识别其标签（可能为空 / 'Time' / 't' / 's'）。
    first_label = header[0].lower()
    time_is_col0 = first_label in ("", "time", "t", "s")
    # 设备 ID 列
    if time_is_col0:
        ids = header[1:]
        us = units_row[1:]
        col_offset = 1
    else:
        ids = header
        us = units_row
        col_offset = 0

    times = []
    series = {fid: [] for fid in ids}
    for row in reader[2:]:
        if not row or not row[0].strip():
            continue
        try:
            t = float(row[0])
        except ValueError:
            continue
        times.append(t)
        for i, fid in enumerate(ids):
            cell = row[col_offset + i] if (col_offset + i) < len(row) else ""
            try:
                v = float(cell)
            except ValueError:
                v = float("nan")
            series[fid].append(v)

    units = dict(zip(ids, us))
    return times, series, units


# ----------------------------------------------------------------------------
# 温度曲线特征提取（§1.3/§1.4/§2.7）
# ----------------------------------------------------------------------------
def _to_array(lst):
    if HAS_NUMPY:
        return np.asarray(lst, dtype=float)
    return lst


def extract_T_profile(times, series, fire_x, T_ambient, t_window=None):
    """
    取准稳态时间窗口内的顶棚中心线温度（取时间平均）。

    返回 (xs, Ts_bar) ：测点 x 与对应时间平均温升(°C over ambient)。
    仅取 ID 形如 T_xxxx 的温度测点。
    """
    # 选取时间窗口
    if t_window is None:
        t0, t1 = 0.0, float("inf")
    else:
        t0, t1 = t_window
    idx = [i for i, t in enumerate(times) if t0 <= t <= t1]
    if not idx:
        idx = list(range(len(times)))

    pts = []
    for fid, vals in series.items():
        if not (fid.startswith("T_") and fid[2:].isdigit()):
            continue
        x = int(fid[2:]) / 100.0
        col = [vals[i] for i in idx]
        if HAS_NUMPY:
            T_bar = float(np.nanmean(col))
        else:
            good = [v for v in col if v == v]  # NaN check
            T_bar = sum(good) / len(good) if good else float("nan")
        pts.append((x, T_bar - T_ambient))
    pts.sort(key=lambda p: p[0])
    if not pts:
        return None, None
    xs = [p[0] for p in pts]
    Ts = [p[1] for p in pts]
    return xs, Ts


def parabolic_peak(xs, Ts):
    """
    二次抛物线拟合求连续峰值位置与峰值（§2.7 简化版）。
    在离散最高点附近三点拟合 y=a(x-x0)^2+ymax。
    返回 (x_p, T_p)。点不足时退化为离散最大。
    """
    if not xs:
        return None, None
    imax = int(max(range(len(Ts)), key=lambda i: Ts[i]))
    T_p = Ts[imax]
    x_p = xs[imax]
    if 0 < imax < len(xs) - 1:
        x1, x2, x3 = xs[imax - 1], xs[imax], xs[imax + 1]
        y1, y2, y3 = Ts[imax - 1], Ts[imax], Ts[imax + 1]
        denom = (x1 - x2) * (x1 - x3) * (x2 - x3)
        if abs(denom) > 1e-12:
            a = ((x3 - x2) * y1 + (x1 - x3) * y2 + (x2 - x1) * y3) / denom
            b = ((x2 ** 2 - x3 ** 2) * y1 + (x3 ** 2 - x1 ** 2) * y2 +
                 (x1 ** 2 - x2 ** 2) * y3) / denom
            if a < 0:  # 确为极大
                xv = -b / (2 * a)
                if min(x1, x3) <= xv <= max(x1, x3):
                    x_p = xv
                    T_p = a * (xv - 0) ** 2 + b * xv + 0  # placeholder
                    # 重新用拟合顶点值
                    T_p = a * xv ** 2 + b * xv + 0.0
                    # 修正截距：用 y2 校正
                    c = y2 - a * x2 ** 2 - b * x2
                    T_p = a * xv ** 2 + b * xv + c
    return x_p, T_p


def fit_decay(xs, Ts, x_p, side, near_exclude, H, dT_threshold=0.0):
    """
    单侧指数衰减拟合，返回 k (1/m) 与使用的点数。
      side='up'   : x < x_p ， ln(θ) = k_u*(x-x_p)，slope = k_u
      side='down' : x > x_p ， ln(θ) = -k_d*(x-x_p)，slope = -k_d => k_d = -slope

    排除近场 |x-x_p|<near_exclude，排除温升低于 dT_threshold 的删失点。
    """
    pairs = []
    for x, T in zip(xs, Ts):
        dx = x - x_p
        if side == "up" and dx >= -near_exclude:
            continue
        if side == "down" and dx <= near_exclude:
            continue
        if T <= dT_threshold:
            continue
        pairs.append((dx, math.log(T)))
    if len(pairs) < 2:
        return None, 0
    # 线性最小二乘 ln(θ) = slope*dx + intercept
    n = len(pairs)
    sx = sum(p[0] for p in pairs)
    sy = sum(p[1] for p in pairs)
    sxx = sum(p[0] ** 2 for p in pairs)
    sxy = sum(p[0] * p[1] for p in pairs)
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-12:
        return None, 0
    slope = (n * sxy - sx * sy) / denom
    k = slope if side == "up" else -slope
    return k, n


def extract_features(times, series, fire_x, T_ambient, H,
                     near_exclude=0.3 * 5.0, t_window=None, dT_threshold=0.0):
    """
    一次性提取 ΔT_p, x_p, κ_u, κ_d（无量纲化为 κ=k*H）。
    near_exclude 默认 0.3H；阶段二交叉验证确定最终值。
    """
    xs, Ts = extract_T_profile(times, series, fire_x, T_ambient, t_window)
    if xs is None:
        return None
    x_p, dT_p = parabolic_peak(xs, Ts)
    k_u, n_u = fit_decay(xs, Ts, x_p, "up", near_exclude, H, dT_threshold)
    k_d, n_d = fit_decay(xs, Ts, x_p, "down", near_exclude, H, dT_threshold)
    return {
        "x_p": x_p, "dT_p": dT_p,
        "k_u": k_u, "k_d": k_d, "kappa_u": k_u * H if k_u else None,
        "kappa_d": k_d * H if k_d else None,
        "n_up": n_u, "n_down": n_d,
    }
