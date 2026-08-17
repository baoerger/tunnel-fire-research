"""
generate_fds_case.py — 隧道火灾 FDS 输入文件生成器（阶段一核心）

用途
----
根据一行“工况规格”(case spec) 生成一份完整、可复现的 FDS 输入文件 (.fds)，
覆盖阶段一全部 FDS 计算：
  - 基准模型（§1.2）
  - 网格敏感性（§1.3：3 工况 × 粗/中/细 = 9）
  - 隧道长度与洞口边界（§1.4：弱风/强风 × 延长长度对照）
  - 外部试验复现（§1.5：按公开试验几何/参数复现）

设计原则
--------
1. 物理与几何集中来自 tunnel_config.py，保证与测点布置、分析脚本口径一致。
2. 生成器对“网格分辨率、火源功率/位置/尺寸、纵向风速、隧道长度”等参数化，
   避免手写 20+ 份 .fds 造成不一致。
3. 仅使用广泛存在、文档明确的 FDS 关键字；量名/统计量名集中在
   tunnel_config 中以常量给出，便于按 FDS 版本集中修正。
4. 文件中不含 FDS 注释行（不同版本注释字符口径不一），说明见 MODEL_SETTINGS.md。

用法
----
单工况：
  python generate_fds_case.py --chid bench_mid --Q 30 --U 2.0 --Df 2.0 \
      --dx 0.25 --L 100 --x_fire 50 --T_end 300 --outdir cases/

按 CSV 批量（推荐）：
  python generate_fds_case.py --csv cases/grid_sensitivity.csv --outdir cases/

CSV 必填列：chid,Q,U,Df,dx,L,x_fire,T_end
可选列：U0(纵向风 m/s，缺省=U)、case_group、note、sensor_profile、
        n_mesh_x、n_mesh_y、n_mesh_z（各方向 MESH 数，缺省=1）、
        rnd_seed 或 les_random_seed（映射到 FDS 6.9.1 MISC RND_SEED）

依赖：仅标准库 + tunnel_config（同目录）。
"""
import os
import csv
import argparse
import math
import re
import shutil
import warnings

import tunnel_config as cfg
from project_paths import FDS_INPUTS_DIR


_REQUIRED_FIELDS = ("chid", "Q", "Df", "dx")
_NUMERIC_FIELDS = ("Q", "U", "Df", "dx", "L", "W", "H", "x_fire", "T_end")
_MESH_COUNT_FIELDS = ("n_mesh_x", "n_mesh_y", "n_mesh_z")
_RND_SEED_FIELDS = ("rnd_seed", "les_random_seed")
_CHID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_HEAD_CHID_RE = re.compile(r"&HEAD\b[^/]*\bCHID\s*=\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)
_TIME_END_RE = re.compile(r"&TIME\b[^/]*\bT_END\s*=\s*([0-9.Ee+-]+)", re.IGNORECASE)
_CATF_FILES_RE = re.compile(r"&CATF\b[^/]*\bOTHER_FILES\s*=\s*([^/]+)", re.IGNORECASE)


def normalize_case(spec, row_number=None):
    """把一行工况转为数值并完成所有生成前校验。"""
    prefix = f"CSV 第 {row_number} 行" if row_number is not None else "工况"
    raw = {k: (v.strip() if isinstance(v, str) else v) for k, v in spec.items()}
    missing = [k for k in _REQUIRED_FIELDS
               if raw.get(k) in (None, "") or str(raw.get(k)).upper() == "TBD"]
    pending = [k for k in _NUMERIC_FIELDS
               if str(raw.get(k, "")).upper() == "TBD" and k not in missing]
    if missing or pending:
        fields = ", ".join(missing + pending)
        raise ValueError(f"{prefix}: 未填写必需数值字段: {fields}")

    chid = str(raw["chid"])
    if not _CHID_RE.fullmatch(chid):
        raise ValueError(f"{prefix}: chid={chid!r} 只能包含字母、数字、下划线和连字符")

    defaults = {
        "U": 0.0, "L": cfg.L, "W": cfg.W, "H": cfg.H,
        "x_fire": None, "T_end": cfg.T_END_DEFAULT,
    }
    values = {"chid": chid}
    for field in _NUMERIC_FIELDS:
        value = raw.get(field, defaults.get(field))
        if value in (None, ""):
            value = defaults.get(field)
        if field == "x_fire" and value is None:
            continue
        try:
            values[field] = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{prefix}: 字段 {field}={value!r} 不是有效数值") from exc

    values.setdefault("x_fire", values["L"] / 2.0)
    for field in ("Q", "Df", "dx", "L", "W", "H", "T_end"):
        if values[field] <= 0:
            raise ValueError(f"{prefix}: {field} 必须大于 0，当前为 {values[field]}")
    if values["U"] < 0:
        raise ValueError(f"{prefix}: U 必须大于等于 0；入口方向由生成器用负 VEL 表示")

    seed_values = []
    for field in _RND_SEED_FIELDS:
        value = raw.get(field)
        if value in (None, ""):
            continue
        if isinstance(value, bool):
            raise ValueError(f"{prefix}: {field}={value!r} 不是有效整数")
        try:
            seed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{prefix}: {field}={value!r} 不是有效整数") from exc
        if str(value).strip() != str(seed):
            raise ValueError(f"{prefix}: {field}={value!r} 必须是整数")
        seed_values.append((field, seed))
    if len({value for _, value in seed_values}) > 1:
        raise ValueError(f"{prefix}: rnd_seed 与 les_random_seed 不一致")
    rnd_seed = seed_values[0][1] if seed_values else 0
    if not 0 <= rnd_seed <= 2_000_000_000:
        raise ValueError(f"{prefix}: RND_SEED 必须位于 0~2000000000")
    values["rnd_seed"] = rnd_seed

    for field in _MESH_COUNT_FIELDS:
        value = raw.get(field, 1)
        if value in (None, ""):
            value = 1
        try:
            count = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{prefix}: {field}={value!r} 不是有效整数") from exc
        if str(value).strip() != str(count):
            raise ValueError(f"{prefix}: {field}={value!r} 必须是整数")
        if count <= 0:
            raise ValueError(f"{prefix}: {field} 必须大于 0，当前为 {count}")
        values[field] = count

    for dim in ("L", "W", "H"):
        cells = values[dim] / values["dx"]
        if not math.isclose(cells, round(cells), rel_tol=0.0, abs_tol=1e-8):
            raise ValueError(
                f"{prefix}: {dim}={values[dim]} 不能被 dx={values['dx']} 整除，"
                "无法建立均匀网格"
            )

    lo, hi = cfg.measurement_region(values["L"], values["H"])
    if lo >= hi:
        raise ValueError(f"{prefix}: L/H 不足以保留有效测量区（计算区间 {lo:g}~{hi:g} m）")
    if not lo <= values["x_fire"] <= hi:
        raise ValueError(
            f"{prefix}: x_fire={values['x_fire']} 必须位于测量区 [{lo:g}, {hi:g}] m"
        )

    bounds = _burner_bounds(values["x_fire"], values["W"], values["Df"], values["dx"])
    x0, x1, y0, y1 = bounds
    if x0 <= 0 or x1 >= values["L"] or y0 <= 0 or y1 >= values["W"]:
        raise ValueError(
            f"{prefix}: 吸附到网格后的燃烧器 {bounds} 必须完整位于地面内部，"
            "以保留四块非零面积 WALL"
        )

    axis_specs = (
        ("x", values["L"], values["n_mesh_x"], x0, x1),
        ("y", values["W"], values["n_mesh_y"], y0, y1),
        ("z", values["H"], values["n_mesh_z"], None, None),
    )
    for axis, length, count, burner_lo, burner_hi in axis_specs:
        cells = int(round(length / values["dx"]))
        if count > cells:
            raise ValueError(
                f"{prefix}: n_mesh_{axis}={count} 超过 {axis} 方向单元数 {cells}"
            )
        if burner_lo is not None:
            internal = _axis_mesh_bounds(length, values["dx"], count)[1:-1]
            crossing = [edge for edge in internal if burner_lo < edge < burner_hi]
            if crossing:
                raise ValueError(
                    f"{prefix}: {axis} 方向 MESH 边界 {crossing} 切穿离散燃烧器 "
                    f"[{burner_lo:g}, {burner_hi:g}]"
                )

    if values["T_end"] <= 3.0 * cfg.TAU_RAMP:
        warnings.warn(
            f"{prefix}: T_end={values['T_end']:g}s 不长于 3×TAU_Q={3 * cfg.TAU_RAMP:g}s，"
            "仅适合输入/启动检查，不可用于目标功率或准稳态结论",
            stacklevel=2,
        )

    values.update({
        "case_group": raw.get("case_group") or raw.get("group"),
        "note": raw.get("note") or None,
        "ramp_inlet": _to_bool(raw.get("ramp_inlet", "1")),
        "burner_bounds": bounds,
    })
    sensor_profile = str(raw.get("sensor_profile") or "standard").strip().lower()
    if sensor_profile not in ("standard", "no_wind_dense"):
        raise ValueError(
            f"{prefix}: sensor_profile={sensor_profile!r} 仅支持 standard/no_wind_dense"
        )
    if sensor_profile == "no_wind_dense":
        if abs(values["U"]) > 1e-6:
            raise ValueError(f"{prefix}: no_wind_dense 只允许 U=0")
        if not math.isclose(
                values["x_fire"], values["L"] / 2.0,
                rel_tol=0.0, abs_tol=1e-8):
            raise ValueError(f"{prefix}: no_wind_dense 要求火源位于 x=L/2")
    values["sensor_profile"] = sensor_profile
    return values


def _burner_bounds(x_fire, width, diameter, dx):
    """将等面积方形燃烧器向外吸附到 x/y 网格面。"""
    side = math.sqrt(math.pi * diameter ** 2 / 4.0)
    half = side / 2.0
    x0 = math.floor((x_fire - half) / dx + 1e-10) * dx
    x1 = math.ceil((x_fire + half) / dx - 1e-10) * dx
    y_mid = width / 2.0
    y0 = math.floor((y_mid - half) / dx + 1e-10) * dx
    y1 = math.ceil((y_mid + half) / dx - 1e-10) * dx
    return tuple(round(v, 10) for v in (x0, x1, y0, y1))


# ----------------------------------------------------------------------------
# 1. 材料 / 表面 / 反应 / 边界 片段
# ----------------------------------------------------------------------------
def _fmt_matl():
    m = cfg.CONCRETE
    return (
        f"&MATL ID='{m['id']}', DENSITY={m['density']}, "
        f"CONDUCTIVITY={m['conductivity']}, SPECIFIC_HEAT={m['specific_heat']} /"
    )


def _fmt_surf_wall():
    m = cfg.CONCRETE
    # 壁面：混凝土一维热传导 + 发射率
    return (
        f"&SURF ID='WALL', MATL_ID='{m['id']}', EMISSIVITY={m['emissivity']}, "
        f"THICKNESS={m['thickness']} /"
    )


def _fmt_surf_burner(Q, bounds, ramp=cfg.TAU_RAMP):
    """按吸附后的离散 VENT 面积设置目标总 HRR。"""
    x0, x1, y0, y1 = bounds
    area_discrete = (x1 - x0) * (y1 - y0)
    hrrpua = Q * 1e3 / area_discrete
    block = (
        f"&SURF ID='BURNER', HRRPUA={hrrpua:.1f}, TAU_Q={ramp:.1f}, "
        f"COLOR='ORANGE' /"
    )
    return block


def _fmt_surf_inlet(U, ramp=None):
    """纵向通风入口表面（x=0 洞口）。VEL 约定：负值将气流推入域内（+x 方向）。
    环境温度由 &MISC TMPA 全局设置（见 render_fds），此处不写 TAMBIENT——
    TAMBIENT 非 FDS 合法参数，会被忽略并产生“未知属性”告警。"""
    # ramp 可选：风速渐升，减少初始冲击（与火源斜坡同步）
    if ramp:
        return f"&SURF ID='INLET', VEL={-U:.3f}, RAMP_V='ramp_inlet' /"
    return f"&SURF ID='INLET', VEL={-U:.3f} /"


def _fmt_reac():
    return (
        f"&REAC FUEL='{cfg.FUEL_NAME}', "
        f"SOOT_YIELD={cfg.SOOT_YIELD}, CO_YIELD={cfg.CO_YIELD} /"
    )


def _fmt_ramp_inlet(ramp=cfg.TAU_RAMP):
    # 风速线性渐升：T=0 时 0，T=ramp 时 1.0
    return [
        f"&RAMP ID='ramp_inlet', T=0.0, F=0.0 /",
        f"&RAMP ID='ramp_inlet', T={ramp:.1f}, F=1.0 /",
    ]


# ----------------------------------------------------------------------------
# 2. 网格 & 域
# ----------------------------------------------------------------------------
def _mesh_IJK(L, W, H, dx):
    nx = max(1, int(round(L / dx)))
    ny = max(1, int(round(W / dx)))
    nz = max(1, int(round(H / dx)))
    return nx, ny, nz


def _axis_mesh_cells(total_cells, count):
    base, remainder = divmod(total_cells, count)
    return [base + (1 if i < remainder else 0) for i in range(count)]


def _axis_mesh_bounds(length, dx, count, origin=0.0):
    cells = int(round(length / dx))
    bounds = [origin]
    cumulative = 0
    for width in _axis_mesh_cells(cells, count):
        cumulative += width
        bounds.append(origin + cumulative * dx)
    return [round(value, 10) for value in bounds]


def _fmt_mesh(L, W, H, dx, n_mesh_x=1, n_mesh_y=1, n_mesh_z=1):
    nx, ny, nz = _mesh_IJK(L, W, H, dx)
    x_cells = _axis_mesh_cells(nx, n_mesh_x)
    y_cells = _axis_mesh_cells(ny, n_mesh_y)
    z_cells = _axis_mesh_cells(nz, n_mesh_z)
    x_bounds = _axis_mesh_bounds(L, dx, n_mesh_x, cfg.X0)
    y_bounds = _axis_mesh_bounds(W, dx, n_mesh_y, cfg.Y0)
    z_bounds = _axis_mesh_bounds(H, dx, n_mesh_z, cfg.Z0)

    lines = []
    for ix, nx_part in enumerate(x_cells):
        for iy, ny_part in enumerate(y_cells):
            for iz, nz_part in enumerate(z_cells):
                mesh_id = f"MESH_X{ix + 1:02d}_Y{iy + 1:02d}_Z{iz + 1:02d}"
                lines.append(
                    f"&MESH ID='{mesh_id}', IJK={nx_part} {ny_part} {nz_part}, "
                    f"XB={x_bounds[ix]:.3f} {x_bounds[ix + 1]:.3f} "
                    f"{y_bounds[iy]:.3f} {y_bounds[iy + 1]:.3f} "
                    f"{z_bounds[iz]:.3f} {z_bounds[iz + 1]:.3f} /"
                )
    return lines


# ----------------------------------------------------------------------------
# 3. 几何：壁面、洞口、燃烧器
# ----------------------------------------------------------------------------
def _fmt_geometry(L, W, H, bounds):
    """
    壁面用 &VENT 在对应边界面上赋表面（FDS 以 &VENT 指派边界面材料）。
    洞口 x=0 / x=L：U>0 时 x=0 为入口(INLET)，x=L 为出口(OPEN)；U≈0 时两端 OPEN。
    """
    L2, W2, H2 = cfg.X0 + L, cfg.Y0 + W, cfg.Z0 + H
    lines = []

    x0, x1, y0, y1 = bounds

    # 燃烧器及其周围地面分块，避免重叠 VENT 被 FDS 拒绝
    lines.append(f"&VENT XB={x0:.3f} {x1:.3f} {y0:.3f} {y1:.3f} {cfg.Z0:.2f} {cfg.Z0:.2f}, SURF_ID='BURNER' /")
    lines.append(f"&VENT XB={cfg.X0:.2f} {x0:.3f} {cfg.Y0:.2f} {W2:.2f} {cfg.Z0:.2f} {cfg.Z0:.2f}, SURF_ID='WALL' /")
    lines.append(f"&VENT XB={x1:.3f} {L2:.2f} {cfg.Y0:.2f} {W2:.2f} {cfg.Z0:.2f} {cfg.Z0:.2f}, SURF_ID='WALL' /")
    lines.append(f"&VENT XB={x0:.3f} {x1:.3f} {cfg.Y0:.2f} {y0:.3f} {cfg.Z0:.2f} {cfg.Z0:.2f}, SURF_ID='WALL' /")
    lines.append(f"&VENT XB={x0:.3f} {x1:.3f} {y1:.3f} {W2:.2f} {cfg.Z0:.2f} {cfg.Z0:.2f}, SURF_ID='WALL' /")

    # 顶棚 z=H
    lines.append(f"&VENT XB={cfg.X0:.2f} {L2:.2f} {cfg.Y0:.2f} {W2:.2f} {H2:.2f} {H2:.2f}, SURF_ID='WALL' /")
    # 侧墙 y=0 / y=W
    lines.append(f"&VENT XB={cfg.X0:.2f} {L2:.2f} {cfg.Y0:.2f} {cfg.Y0:.2f} {cfg.Z0:.2f} {H2:.2f}, SURF_ID='WALL' /")
    lines.append(f"&VENT XB={cfg.X0:.2f} {L2:.2f} {W2:.2f} {W2:.2f} {cfg.Z0:.2f} {H2:.2f}, SURF_ID='WALL' /")

    # 出口 x=L
    lines.append(f"&VENT XB={L2:.2f} {L2:.2f} {cfg.Y0:.2f} {W2:.2f} {cfg.Z0:.2f} {H2:.2f}, SURF_ID='OPEN' /")

    return lines


def _fmt_inlet(L, W, H, U, ramp_inlet):
    """入口 x=0：U>0 为 INLET；U≈0 为 OPEN（自然通风）。"""
    L2, W2, H2 = cfg.X0 + L, cfg.Y0 + W, cfg.Z0 + H
    if U and abs(U) > 1e-6:
        surf = _fmt_surf_inlet(U, ramp=ramp_inlet)
        return [surf, f"&VENT XB={cfg.X0:.2f} {cfg.X0:.2f} {cfg.Y0:.2f} {W2:.2f} {cfg.Z0:.2f} {H2:.2f}, SURF_ID='INLET' /"]
    else:
        return [f"&VENT XB={cfg.X0:.2f} {cfg.X0:.2f} {cfg.Y0:.2f} {W2:.2f} {cfg.Z0:.2f} {H2:.2f}, SURF_ID='OPEN' /"]


# ----------------------------------------------------------------------------
# 4. 设备：温度/速度测点、HRR、壁面热通量
# ----------------------------------------------------------------------------
def _id_x(prefix, x):
    return f"{prefix}_{int(round(x * 100)):04d}"   # 如 T_0300 表示 x=30.00


def _fmt_devices(x_fire, sensor_xs, L=None, W=None, H=None, hrr_region=None):
    lines = []
    H = cfg.H if H is None else H
    W = cfg.W if W is None else W
    L = cfg.L if L is None else L
    y_mid = W / 2.0
    zT, zU = cfg.sensor_heights(H)

    # &DEVC 不显式写 UNITS；分析侧始终读取 CSV 单位行并归一到 °C/kW/kW·m⁻²。

    # 4a 顶棚中心线气体温度测点（§1.7）
    for x in sensor_xs:
        lines.append(
            f"&DEVC ID='{_id_x('T', x)}', QUANTITY='{cfg.QUANTITY_TEMPERATURE}', "
            f"XYZ={x:.3f} {y_mid:.3f} {zT:.3f} /"
        )

    # 4b 顶棚附近纵向速度测点（回流/输运参考）
    # 气相点取 x 速度分量须用 'U-VELOCITY'；IOR 仅对固壁设备有效
    for x in sensor_xs:
        lines.append(
            f"&DEVC ID='{_id_x('U', x)}', QUANTITY='{cfg.QUANTITY_VELOCITY_U}', "
            f"XYZ={x:.3f} {y_mid:.3f} {zU:.3f} /"
        )

    # 4c HRR：对 HRRPUV 做区域体积分；辐射/对流比例由 FDS 的 _hrr.csv 计算。
    if hrr_region is None:
        L2 = cfg.X0 + L
        W2 = cfg.Y0 + W
        H2 = cfg.Z0 + H
        hrr_region = (cfg.X0, L2, cfg.Y0, W2, cfg.Z0, H2)
    xb = " ".join(f"{v:.2f}" for v in hrr_region)
    lines.append(
        f"&DEVC ID='HRR_tot', QUANTITY='{cfg.QUANTITY_HRR_TOTAL}', XB={xb}, "
        f"SPATIAL_STATISTIC='{cfg.STAT_VOLUME_INTEGRATION}' /"
    )

    # 4d 壁面热通量（顶棚若干代表点，§4.7 壁面吸热参考）
    lo, hi = cfg.measurement_region(L, H)
    for x in [x_fire - 2 * H, x_fire, x_fire + 2 * H]:
        if lo <= x <= hi:
            lines.append(
                f"&DEVC ID='{_id_x('Qw', x)}', QUANTITY='{cfg.QUANTITY_WALL_HEATFLUX}', "
                f"XYZ={x:.3f} {y_mid:.3f} {H:.3f}, IOR=-3 /"
            )

    return lines


# ----------------------------------------------------------------------------
# 5. 切片场（§1.7 输出 / §4.7 全场物理解释）
# ----------------------------------------------------------------------------
def _fmt_slices(L, W, H, x_fire, sensor_profile="standard"):
    lines = []
    y_mid = W / 2.0
    lo, hi = cfg.measurement_region(L, H)

    # 纵向中心面 y=W/2：温度、纵向速度、密度（回流长度/烟气可视化）
    lines.append(f"&SLCF PBY={y_mid:.2f}, QUANTITY='{cfg.QUANTITY_TEMPERATURE}' /")
    lines.append(f"&SLCF PBY={y_mid:.2f}, QUANTITY='{cfg.QUANTITY_VELOCITY}', VECTOR=.TRUE. /")
    lines.append(f"&SLCF PBY={y_mid:.2f}, QUANTITY='{cfg.QUANTITY_DENSITY}' /")

    # 无风公式重发现需要检查顶棚射流何时横向触及侧墙；在温度测点高度
    # 增加水平温度切片。默认 standard 不增加输出，保持历史工况逐字稳定。
    if sensor_profile == "no_wind_dense":
        z_temp, _ = cfg.sensor_heights(H)
        lines.append(
            f"&SLCF PBZ={z_temp:.3f}, QUANTITY='{cfg.QUANTITY_TEMPERATURE}' /"
        )

    # 横截面 PBX：火源上下游若干 x 处，用于 §4.7 横截面超温焓 C_T、纵向焓流 J_T
    # 位置随测量区与火源自适应，避免越过洞口缓冲
    rel = [0.0, 0.1, 0.2, 0.35, 0.45, 0.5, 0.55, 0.65, 0.8, 0.9, 1.0]
    xs_cross = []
    for r in rel:
        x = lo + r * (hi - lo)
        if lo <= x <= hi:
            xs_cross.append(round(x, 2))
    xs_cross = sorted(set(xs_cross))
    for x in xs_cross:
        lines.append(f"&SLCF PBX={x:.2f}, QUANTITY='{cfg.QUANTITY_TEMPERATURE}' /")
        lines.append(f"&SLCF PBX={x:.2f}, QUANTITY='{cfg.QUANTITY_VELOCITY}', VECTOR=.TRUE. /")
        lines.append(f"&SLCF PBX={x:.2f}, QUANTITY='{cfg.QUANTITY_DENSITY}' /")

    return lines


# ----------------------------------------------------------------------------
# 6. 组装
# ----------------------------------------------------------------------------
def render_fds(chid, Q, U, Df, dx, L=None, W=None, H=None, x_fire=None, T_end=None,
               title=None, group=None, ramp_inlet=True, n_mesh_x=1, n_mesh_y=1,
               n_mesh_z=1, rnd_seed=0, sensor_profile="standard", _normalized=None):
    """
    渲染一份完整 FDS 输入字符串。

    参数
    ----
    chid      : 案例标识（文件名）
    Q         : 火源总热释放速率 [MW]
    U         : 纵向风速 [m/s]（0 表示自然通风）
    Df        : 火源等效直径 [m]
    dx        : 均匀网格尺寸 [m]
    L         : 隧道长度 [m]，默认 cfg.L
    W         : 隧道宽度 [m]，默认 cfg.W（外部试验复现可覆盖）
    H         : 隧道高度 [m]，默认 cfg.H（外部试验复现可覆盖）
    x_fire    : 火源 x 位置 [m]，默认 cfg.X_FIRE_DEFAULT
    T_end     : 模拟结束时间 [s]，默认 cfg.T_END_DEFAULT
    title     : 标题（可空）
    group     : 工况分组标签（仅写入 &HEAD 的 TITLE，便于检索）
    ramp_inlet: 风速是否渐升（默认 True）
    rnd_seed : FDS 6.9.1 ``MISC RND_SEED``；0 表示沿用默认固定序列
    sensor_profile: ``standard`` 或无风公式研究用 ``no_wind_dense``
    """
    normalized = _normalized or normalize_case(dict(
        chid=chid, Q=Q, U=U, Df=Df, dx=dx, L=L, W=W, H=H,
        x_fire=x_fire, T_end=T_end, case_group=group, note=title,
        ramp_inlet=ramp_inlet, n_mesh_x=n_mesh_x, n_mesh_y=n_mesh_y,
        n_mesh_z=n_mesh_z, rnd_seed=rnd_seed, sensor_profile=sensor_profile,
    ))
    Q = normalized["Q"]
    U = normalized["U"]
    Df = normalized["Df"]
    dx = normalized["dx"]
    L = normalized["L"]
    W = normalized["W"]
    H = normalized["H"]
    x_fire = normalized["x_fire"]
    T_end = normalized["T_end"]
    bounds = normalized["burner_bounds"]
    n_mesh_x = normalized["n_mesh_x"]
    n_mesh_y = normalized["n_mesh_y"]
    n_mesh_z = normalized["n_mesh_z"]
    rnd_seed = normalized["rnd_seed"]
    sensor_profile = normalized["sensor_profile"]
    group = normalized["case_group"]
    title = normalized["note"] or f"Q={Q}MW U={U}m/s Df={Df}m dx={dx}m L={L} W={W} H={H}"
    if group:
        title = f"[{group}] {title}"

    region = cfg.measurement_region(L, H)
    # 传感器属于隧道布置而不是火源工况：同一几何下始终以隧道中点为
    # 布置参考，偏移火源不得带着传感器一起平移。这样 6 个偏移工况才
    # 能真实检验定位和平移性质。外部试验的实测位置另由测点映射表给出。
    if sensor_profile == "no_wind_dense":
        sensor_xs = cfg.no_wind_dense_sensor_layout(
            height=H, fire_x=x_fire, region=region,
        )
    else:
        sensor_xs = cfg.sensor_layout(height=H, fire_x=L / 2.0, region=region)

    burner_block = _fmt_surf_burner(Q, bounds)
    inlet_blocks = _fmt_inlet(L, W, H, U, ramp_inlet=ramp_inlet)

    parts = []
    parts.append(f"&HEAD CHID='{chid}', TITLE='{title}' /")
    parts.extend(_fmt_mesh(L, W, H, dx, n_mesh_x, n_mesh_y, n_mesh_z))
    parts.append(f"&TIME T_END={T_end:.1f} /")
    parts.append(
        f"&DUMP DT_DEVC={cfg.DT_DEVC}, DT_HRR={cfg.DT_HRR}, "
        f"DT_SLCF={cfg.DT_SLCF}, DT_BNDF={cfg.DT_BNDF} /"
    )
    misc = f"&MISC SIMULATION_MODE='LES', RESTART=.FALSE., TMPA={cfg.T_AMBIENT_C}"
    if rnd_seed:
        misc += f", RND_SEED={rnd_seed}"
    parts.append(misc + " /")
    parts.append(_fmt_reac())
    parts.append(_fmt_matl())
    parts.append(_fmt_surf_wall())
    parts.append(burner_block)
    parts.extend(inlet_blocks)
    if ramp_inlet and U and abs(U) > 1e-6:
        parts.extend(_fmt_ramp_inlet())
    parts.extend(_fmt_geometry(L, W, H, bounds))
    parts.extend(_fmt_devices(x_fire, sensor_xs, L=L, W=W, H=H))
    parts.append(f"&BNDF QUANTITY='{cfg.QUANTITY_BNDF}' /")   # 边界场：壁面温度（§4.7 壁面温度参考）
    parts.extend(_fmt_slices(L, W, H, x_fire, sensor_profile=sensor_profile))
    parts.append("&TAIL /")

    return "\n".join(parts) + "\n"


def write_fds(spec, outdir, row_number=None):
    """校验一行工况并写出对应 FDS 输入。"""
    case = normalize_case(spec, row_number=row_number)
    chid = case["chid"]
    text = render_fds(
        chid=chid,
        Q=case["Q"],
        U=case["U"],
        Df=case["Df"],
        dx=case["dx"],
        L=case["L"],
        W=case["W"],
        H=case["H"],
        x_fire=case["x_fire"],
        T_end=case["T_end"],
        group=case["case_group"],
        title=case["note"],
        ramp_inlet=case["ramp_inlet"],
        n_mesh_x=case["n_mesh_x"],
        n_mesh_y=case["n_mesh_y"],
        n_mesh_z=case["n_mesh_z"],
        sensor_profile=case["sensor_profile"],
        _normalized=case,
    )
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"{chid}.fds")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def _split_semicolon(value):
    return [item.strip() for item in str(value or "").split(";") if item.strip()]


def _validate_snapshot_metadata(row, row_number):
    """校验官方验证仓库快照的描述字段，不把复杂截面误生成为矩形隧道。"""
    prefix = f"CSV 第 {row_number} 行"
    chid = str(row.get("chid") or "").strip()
    if not _CHID_RE.fullmatch(chid):
        raise ValueError(f"{prefix}: chid={chid!r} 只能包含字母、数字、下划线和连字符")

    values = {"chid": chid, "input_mode": "official_snapshot"}
    for field in _NUMERIC_FIELDS:
        raw = row.get(field)
        if raw in (None, "") or str(raw).upper() == "TBD":
            raise ValueError(f"{prefix}: 官方快照缺少描述字段 {field}")
        try:
            values[field] = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{prefix}: 字段 {field}={raw!r} 不是有效数值") from exc
    for field in ("Q", "Df", "dx", "L", "W", "H", "T_end"):
        if values[field] <= 0:
            raise ValueError(f"{prefix}: {field} 必须大于 0")
    if values["U"] < 0:
        raise ValueError(f"{prefix}: U 必须大于等于 0")

    try:
        x_min = float(row.get("x_min") or 0.0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{prefix}: x_min 不是有效数值") from exc
    if not x_min <= values["x_fire"] <= x_min + values["L"]:
        raise ValueError(
            f"{prefix}: x_fire={values['x_fire']} 不在官方计算域 "
            f"[{x_min}, {x_min + values['L']}] 内"
        )
    values["x_min"] = x_min
    return values


def _prepare_official_snapshot(row, csv_dir, outdir, row_number):
    """从已归档的官方源文件生成独立外部提交目录，内容保持逐字不变。"""
    prefix = f"CSV 第 {row_number} 行"
    values = _validate_snapshot_metadata(row, row_number)
    source_rel = str(row.get("source_fds") or "").strip()
    if not source_rel:
        raise ValueError(f"{prefix}: input_mode=official_snapshot 时必须填写 source_fds")
    source_path = os.path.abspath(os.path.join(csv_dir, source_rel))
    csv_root = os.path.abspath(csv_dir)
    if os.path.commonpath((source_path, csv_root)) != csv_root:
        raise ValueError(f"{prefix}: source_fds 必须位于外部试验目录内")
    if not os.path.isfile(source_path):
        raise ValueError(f"{prefix}: source_fds 不存在: {source_path}")

    with open(source_path, encoding="utf-8-sig") as stream:
        text = stream.read()
    head = _HEAD_CHID_RE.search(text)
    if not head or head.group(1) != values["chid"]:
        actual = head.group(1) if head else "未找到"
        raise ValueError(f"{prefix}: 源文件 CHID={actual!r} 与 {values['chid']!r} 不一致")
    end = _TIME_END_RE.search(text)
    if not end or not math.isclose(float(end.group(1)), values["T_end"], abs_tol=1e-8):
        actual = end.group(1) if end else "未找到"
        raise ValueError(f"{prefix}: 源文件 T_END={actual} 与元数据 {values['T_end']:g} 不一致")
    # 官方验证库中同时存在 ``&TAIL /`` 与 ``&TAIL/``；二者对 FDS
    # 等价。快照必须逐字保留，因此只校验最后一条 namelist，而不改写。
    if not re.search(r"&TAIL\s*/\s*$", text, re.IGNORECASE):
        raise ValueError(f"{prefix}: 官方源文件最后一条记录不是 &TAIL /")

    source_dir = os.path.dirname(source_path)
    companion_names = _split_semicolon(row.get("companion_files"))
    companion_paths = []
    for name in companion_names:
        if os.path.basename(name) != name:
            raise ValueError(f"{prefix}: companion_files 只能填写源目录内文件名: {name}")
        path = os.path.join(source_dir, name)
        if not os.path.isfile(path):
            raise ValueError(f"{prefix}: 伴随文件不存在: {path}")
        companion_paths.append(path)

    catf = _CATF_FILES_RE.search(text)
    if catf:
        referenced = re.findall(r"['\"]([^'\"]+)['\"]", catf.group(1))
        missing = sorted(set(referenced) - set(companion_names))
        if missing:
            raise ValueError(f"{prefix}: CATF 引用文件未列入 companion_files: {', '.join(missing)}")

    case_dir = os.path.join(outdir, values["chid"])
    os.makedirs(case_dir, exist_ok=True)
    destinations = []
    for path in [source_path, *companion_paths]:
        destination = os.path.join(case_dir, os.path.basename(path))
        shutil.copyfile(path, destination)
        destinations.append(destination)
    return destinations


def _to_bool(v):
    return str(v).strip().lower() in ("1", "true", "yes", "y", "t")


# ----------------------------------------------------------------------------
# 7. CLI
# ----------------------------------------------------------------------------
def _run_csv(csv_path, outdir, required_only=False):
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"CSV 没有表头: {csv_path}")
        missing_columns = [k for k in _REQUIRED_FIELDS if k not in reader.fieldnames]
        if missing_columns:
            raise ValueError(f"CSV 缺少必需列: {', '.join(missing_columns)}")
        rows = list(reader)
        if required_only:
            if "required" not in reader.fieldnames:
                raise ValueError("--required-only 要求 CSV 含 required 列")
            rows = [row for row in rows
                    if str(row.get("required") or "").strip().lower()
                    in ("1", "true", "yes", "y", "required")]
            if not rows:
                raise ValueError("CSV 中没有 required=yes 的工况")

    seen = {}
    prepared_rows = []
    for line_number, row in enumerate(rows, start=2):
        chid = (row.get("chid") or "").strip()
        if chid in seen:
            raise ValueError(f"CSV 第 {line_number} 行: chid={chid!r} 与第 {seen[chid]} 行重复")
        seen[chid] = line_number
        mode = str(row.get("input_mode") or "generated").strip().lower()
        if mode == "official_snapshot":
            prepared_rows.append((mode, row, line_number))
        elif mode in ("", "generated"):
            prepared_rows.append(("generated", normalize_case(row, row_number=line_number), line_number))
        else:
            raise ValueError(f"CSV 第 {line_number} 行: 未知 input_mode={mode!r}")

    csv_dir = os.path.dirname(os.path.abspath(csv_path))
    for mode, case, line_number in prepared_rows:
        if mode == "official_snapshot":
            paths = _prepare_official_snapshot(case, csv_dir, outdir, line_number)
            print(f"[OK] {case['chid']:>20s} -> {os.path.dirname(paths[0])} ({len(paths)} files)")
            continue
        chid = case["chid"]
        text = render_fds(
            chid=chid, Q=case["Q"], U=case["U"], Df=case["Df"], dx=case["dx"],
            L=case["L"], W=case["W"], H=case["H"], x_fire=case["x_fire"],
            T_end=case["T_end"], group=case["case_group"], title=case["note"],
            ramp_inlet=case["ramp_inlet"], n_mesh_x=case["n_mesh_x"],
            n_mesh_y=case["n_mesh_y"], n_mesh_z=case["n_mesh_z"],
            sensor_profile=case["sensor_profile"],
            _normalized=case,
        )
        os.makedirs(outdir, exist_ok=True)
        path = os.path.join(outdir, f"{chid}.fds")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print(f"[OK] {chid:>20s} -> {path}")


def main():
    ap = argparse.ArgumentParser(description="隧道火灾 FDS 输入文件生成器")
    ap.add_argument("--csv", help="按 CSV 批量生成（推荐）")
    ap.add_argument("--required-only", action="store_true",
                    help="仅生成 CSV 中 required=yes 的工况")
    ap.add_argument("--outdir", default=str(FDS_INPUTS_DIR),
                    help="FDS 输入文件目录（默认: outputs/inputs）")
    ap.add_argument("--chid", help="单工况：案例标识")
    ap.add_argument("--Q", type=float, help="火源总功率 [MW]")
    ap.add_argument("--U", type=float, default=0.0, help="纵向风速 [m/s]")
    ap.add_argument("--Df", type=float, help="火源等效直径 [m]")
    ap.add_argument("--dx", type=float, default=cfg.WORKING_GRID_DX,
                    help=f"网格尺寸 [m]（默认工作网格 {cfg.WORKING_GRID_DX:g}）")
    ap.add_argument("--L", type=float, help="隧道长度 [m]")
    ap.add_argument("--W", type=float, help="隧道宽度 [m]（外部试验可覆盖）")
    ap.add_argument("--H", type=float, help="隧道高度 [m]（外部试验可覆盖）")
    ap.add_argument("--x_fire", type=float, help="火源 x 位置 [m]")
    ap.add_argument("--T_end", type=float, help="模拟结束时间 [s]")
    ap.add_argument("--n_mesh_x", type=int, default=1, help="x 方向 MESH 数")
    ap.add_argument("--n_mesh_y", type=int, default=1, help="y 方向 MESH 数")
    ap.add_argument("--n_mesh_z", type=int, default=1, help="z 方向 MESH 数")
    ap.add_argument("--rnd_seed", type=int, default=0,
                    help="FDS 6.9.1 MISC RND_SEED；0 使用默认固定序列")
    ap.add_argument(
        "--sensor-profile", choices=("standard", "no_wind_dense"),
        default="standard", help="测点配置；无风公式研究使用 no_wind_dense",
    )
    ap.add_argument("--group", help="工况分组标签")
    ap.add_argument("--note", help="标题备注")
    args = ap.parse_args()

    if args.csv:
        try:
            _run_csv(args.csv, args.outdir, required_only=args.required_only)
        except (OSError, ValueError) as exc:
            ap.error(str(exc))
        return

    if not args.chid or args.Q is None or args.Df is None:
        ap.error("单工况模式需提供 --chid --Q --Df（可选 --U --dx --L --W --H --x_fire --T_end）")

    spec = dict(chid=args.chid, Q=args.Q, U=args.U, Df=args.Df, dx=args.dx,
                L=args.L, W=args.W, H=args.H, x_fire=args.x_fire, T_end=args.T_end,
                n_mesh_x=args.n_mesh_x, n_mesh_y=args.n_mesh_y,
                n_mesh_z=args.n_mesh_z, rnd_seed=args.rnd_seed,
                sensor_profile=args.sensor_profile,
                case_group=args.group, note=args.note)
    path = write_fds(spec, args.outdir)
    print(f"[OK] {args.chid} -> {path}")


if __name__ == "__main__":
    main()
