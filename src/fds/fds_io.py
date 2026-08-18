"""
fds_io.py — FDS 设备输出读取与温度曲线特征提取

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
    """定位设备输出，兼容平铺目录和 ``runs/<chid>`` 分目录。"""
    candidates = [
        os.path.join(chid_dir, f"{chid}_devc.csv"),
        os.path.join(chid_dir, f"{chid}.devc"),
        os.path.join(chid_dir, chid, f"{chid}_devc.csv"),
        os.path.join(chid_dir, chid, f"{chid}.devc"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


def _read_fds_csv(path):
    if not os.path.isfile(path):
        return None, {}, {}

    with open(path, newline="", encoding="utf-8-sig", errors="ignore") as f:
        rows = list(csv.reader(f))
    if len(rows) < 2:
        return None, {}, {}

    first = [c.strip() for c in rows[0]]
    second = [c.strip() for c in rows[1]]
    if second and second[0].lower() in ("time", "t"):
        units_row, header = first, second
    else:
        header, units_row = first, second

    ids = header[1:]
    units = dict(zip(ids, units_row[1:]))
    times = []
    series = {fid: [] for fid in ids}
    for row in rows[2:]:
        if not row or not row[0].strip():
            continue
        try:
            times.append(float(row[0]))
        except ValueError:
            continue
        for i, fid in enumerate(ids, start=1):
            try:
                value = float(row[i]) if i < len(row) else float("nan")
            except ValueError:
                value = float("nan")
            series[fid].append(value)
    return times, series, units


def read_devc(chid_dir, chid):
    """读取 <chid>_devc.csv 设备时序。"""
    path = _find_devc(chid_dir, chid)
    if path is None:
        return None, {}, {}
    return _read_fds_csv(path)


def read_hrr(chid_dir, chid):
    """读取 <chid>_hrr.csv 能量收支时序。"""
    for path in (
        os.path.join(chid_dir, f"{chid}_hrr.csv"),
        os.path.join(chid_dir, chid, f"{chid}_hrr.csv"),
    ):
        if os.path.isfile(path):
            return _read_fds_csv(path)
    return None, {}, {}


# ----------------------------------------------------------------------------
# 单位归一化：始终以 FDS CSV 单位行为准，统一到工程单位
# ----------------------------------------------------------------------------
def normalize_units(series, units):
    """把 devc.csv 各设备序列归一到工程单位并返回新 series（不改输入）。
    工程单位：温度 °C、HRR/功率 kW、热通量 kW/m²、速度 m/s。
    按 read_devc/read_hrr 返回的单位字符串判定，不假设 FDS 默认单位，
    兼容 C/kW、K/W 和 MW 等历史及当前输出。"""
    return {fid: _convert_unit(vals, (units.get(fid, "") or "").strip())
            for fid, vals in series.items()}


def _convert_unit(vals, unit):
    """按单位字符串换算一个序列：温度 K→°C 用偏移，W/MW/热通量用乘因子；其余不变。"""
    if not vals:
        return list(vals)
    u = unit.upper().replace(" ", "").replace("^", "")
    if u in ("K", "KELVIN"):                       # 温度 K → °C
        return [(_v - 273.15) if _v == _v else _v for _v in vals]
    if u in ("W/M2",):                             # 热通量 W/m² → kW/m²
        return [(_v * 1e-3) if _v == _v else _v for _v in vals]
    if u in ("W", "WATTS", "WATT"):                # 功率 W → kW
        return [(_v * 1e-3) if _v == _v else _v for _v in vals]
    if u in ("MW", "MEGAWATT", "MEGAWATTS"):       # 功率 MW → kW
        return [(_v * 1e3) if _v == _v else _v for _v in vals]
    return list(vals)                              # kW / kW/m² / m/s / 未知：不变


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
    # 显式给定窗口却没有样本时必须失败；回退到全时段会把启动段悄悄
    # 当成准稳态数据，进而污染网格/边界决策。
    if not idx:
        return None, None

    pts = []
    for fid, vals in series.items():
        if not (fid.startswith("T_") and fid[2:].isdigit()):
            continue
        x = int(fid[2:]) / 100.0
        col = [vals[i] for i in idx if i < len(vals) and math.isfinite(vals[i])]
        if not col:
            continue
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
    valid = [(i, x, y) for i, (x, y) in enumerate(zip(xs, Ts))
             if math.isfinite(x) and math.isfinite(y)]
    if not valid:
        return None, None
    imax = max(valid, key=lambda item: item[2])[0]
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
                    c = y2 - a * x2 ** 2 - b * x2
                    T_p = a * xv ** 2 + b * xv + c
    return x_p, T_p


def fit_decay(xs, Ts, x_p, side, near_exclude, H, dT_threshold=0.0,
              min_points=3):
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
        if not (math.isfinite(x) and math.isfinite(T)) or T <= dT_threshold:
            continue
        pairs.append((dx, math.log(T)))
    if len(pairs) < min_points:
        # 保留实际可用点数，便于区分“完全没有数据”和“只有 2 点、不能拟合”。
        return None, len(pairs)
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
    if not math.isfinite(k) or k <= 0:
        return None, n
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
        "k_u": k_u, "k_d": k_d,
        "kappa_u": k_u * H if k_u is not None else None,
        "kappa_d": k_d * H if k_d is not None else None,
        "n_up": n_u, "n_down": n_d,
    }
