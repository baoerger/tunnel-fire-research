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
可选列：U0(纵向风 m/s，缺省=U)、case_group、note

依赖：仅标准库 + tunnel_config（同目录）。
"""
import os
import csv
import argparse
import math

import tunnel_config as cfg


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


def _fmt_surf_burner(Q, D_f, ramp=cfg.TAU_RAMP):
    """
    预设 HRR 燃烧器（§1.2/§1.8）：用 HRRPUA 精确控制总功率 Q，
    燃烧器面积按等效直径换算 A_f = pi*D_f^2/4，方形铺设 side=2R。
    FDS 据此按需注入燃料以达成目标 HRR，故 Q 为精确输入参数。
    """
    A_f = math.pi * D_f ** 2 / 4.0           # 等效面积 [m^2]
    side = math.sqrt(A_f)                    # 等面积方形边长 [m]
    hrrpua = Q * 1e6 / A_f                   # [W/m^2]
    # 斜坡：TAU 为达到目标 HRR 的时间常数，避免冲击启动
    block = (
        f"&SURF ID='BURNER', HRRPUA={hrrpua:.1f}, TAU={ramp:.1f}, "
        f"COLOR='ORANGE' /"
    )
    return block, side


def _fmt_surf_inlet(U, ramp=None):
    """纵向通风入口表面（x=0 洞口）。VEL 约定：负值将气流推入域内（+x 方向）。
    环境温度由 &MISC TMPA 全局设置（见 render_fds），此处不写 TAMBIENT——
    TAMBIENT 非 FDS 合法参数，会被忽略并产生“未知属性”告警。"""
    # ramp 可选：风速渐升，减少初始冲击（与火源斜坡同步）
    if ramp:
        return f"&SURF ID='INLET', VEL={-U:.3f}, RAMP_V='ramp_inlet' /"
    return f"&SURF ID='INLET', VEL={-U:.3f} /"


def _fmt_spec():
    """燃料物种 &SPEC。FDS 6.8 起 &REAC 的 FUEL 必须在 &SPEC 行定义，
    否则 FDS 用默认参数新建同名物种并告警；显式 &SPEC 可加载 FDS 预定义
    n-HEPTANE 的真实物性（分子量、燃烧热等）。"""
    return f"&SPEC ID='{cfg.FUEL_NAME}' /"


def _fmt_reac():
    return (
        f"&REAC FUEL='{cfg.FUEL_NAME}', SIMPLE_CHEMISTRY=.TRUE., "
        f"SOOT_YIELD={cfg.SOOT_YIELD}, CO_YIELD={cfg.CO_YIELD} /"
    )


def _fmt_ramp_inlet(ramp=cfg.TAU_RAMP):
    # 风速线性渐升：T=0 时 0，T=ramp 时 1.0
    return (
        f"&RAMP ID='ramp_inlet', T=0.0, F=0.0 / "
        f"&RAMP ID='ramp_inlet', T={ramp:.1f}, F=1.0 /"
    )


# ----------------------------------------------------------------------------
# 2. 网格 & 域
# ----------------------------------------------------------------------------
def _mesh_IJK(L, W, H, dx):
    nx = max(1, int(round(L / dx)))
    ny = max(1, int(round(W / dx)))
    nz = max(1, int(round(H / dx)))
    # 强制 IJK 为偶数无关紧要；保持整数化即可
    return nx, ny, nz


def _fmt_mesh(L, W, H, dx):
    nx, ny, nz = _mesh_IJK(L, W, H, dx)
    return (
        f"&MESH IJK={nx} {ny} {nz}, "
        f"XB={cfg.X0:.2f} {cfg.X0 + L:.2f} {cfg.Y0:.2f} {cfg.Y0 + W:.2f} "
        f"{cfg.Z0:.2f} {cfg.Z0 + H:.2f} /"
    )


# ----------------------------------------------------------------------------
# 3. 几何：壁面、洞口、燃烧器
# ----------------------------------------------------------------------------
def _fmt_geometry(L, W, H, x_fire, side):
    """
    壁面用 &VENT 在对应边界面上赋表面（FDS 以 &VENT 指派边界面材料）。
    洞口 x=0 / x=L：U>0 时 x=0 为入口(INLET)，x=L 为出口(OPEN)；U≈0 时两端 OPEN。
    """
    L2, W2, H2 = cfg.X0 + L, cfg.Y0 + W, cfg.Z0 + H
    lines = []

    # 地面 z=0
    lines.append(f"&VENT XB={cfg.X0:.2f} {L2:.2f} {cfg.Y0:.2f} {W2:.2f} {cfg.Z0:.2f} {cfg.Z0:.2f}, SURF_ID='WALL' /")
    # 顶棚 z=H
    lines.append(f"&VENT XB={cfg.X0:.2f} {L2:.2f} {cfg.Y0:.2f} {W2:.2f} {H2:.2f} {H2:.2f}, SURF_ID='WALL' /")
    # 侧墙 y=0 / y=W
    lines.append(f"&VENT XB={cfg.X0:.2f} {L2:.2f} {cfg.Y0:.2f} {cfg.Y0:.2f} {cfg.Z0:.2f} {H2:.2f}, SURF_ID='WALL' /")
    lines.append(f"&VENT XB={cfg.X0:.2f} {L2:.2f} {W2:.2f} {W2:.2f} {cfg.Z0:.2f} {H2:.2f}, SURF_ID='WALL' /")

    # 出口 x=L
    lines.append(f"&VENT XB={L2:.2f} {L2:.2f} {cfg.Y0:.2f} {W2:.2f} {cfg.Z0:.2f} {H2:.2f}, SURF_ID='OPEN' /")

    # 燃烧器（地面方形 patch，居中 x_fire, y=W/2）
    cx = x_fire
    cy = W / 2.0
    x0 = cx - side / 2.0
    x1 = cx + side / 2.0
    y0 = cy - side / 2.0
    y1 = cy + side / 2.0
    lines.append(f"&VENT XB={x0:.3f} {x1:.3f} {y0:.3f} {y1:.3f} {cfg.Z0:.2f} {cfg.Z0:.2f}, SURF_ID='BURNER' /")

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
    zT = cfg.Z_SENSOR
    zU = min(cfg.Z_VELOCITY, H - 0.05 * H)

    # 4a 顶棚中心线气体温度测点（§1.7）
    for x in sensor_xs:
        lines.append(
            f"&DEVC ID='{_id_x('T', x)}', QUANTITY='{cfg.QUANTITY_TEMPERATURE}', "
            f"XYZ={x:.3f} {y_mid:.3f} {zT:.3f}, UNITS='{cfg.UNITS_TEMP}' /"
        )

    # 4b 顶棚附近纵向速度测点（回流/输运参考）
    # 气相点取 x 速度分量须用 'U VELOCITY'；IOR 仅对固壁设备有效，气相点 IOR 会被忽略
    for x in sensor_xs:
        lines.append(
            f"&DEVC ID='{_id_x('U', x)}', QUANTITY='{cfg.QUANTITY_VELOCITY_U}', "
            f"XYZ={x:.3f} {y_mid:.3f} {zU:.3f}, UNITS='{cfg.UNITS_VEL}' /"
        )

    # 4c HRR：总 + 对流（§1.8）。量名/统计量集中为常量，便于按版本修正。
    if hrr_region is None:
        L2 = cfg.X0 + L
        W2 = cfg.Y0 + W
        H2 = cfg.Z0 + H
        hrr_region = (cfg.X0, L2, cfg.Y0, W2, cfg.Z0, H2)
    xb = " ".join(f"{v:.2f}" for v in hrr_region)
    lines.append(
        f"&DEVC ID='HRR_tot', QUANTITY='{cfg.QUANTITY_HRR_TOTAL}', XB={xb}, "
        f"STATISTICS='{cfg.STAT_VOLUME_INTEGRATION}', UNITS='{cfg.UNITS_HRR}' /"
    )
    lines.append(
        f"&DEVC ID='HRR_conv', QUANTITY='{cfg.QUANTITY_HRR_CONV}', XB={xb}, "
        f"STATISTICS='{cfg.STAT_VOLUME_INTEGRATION}', UNITS='{cfg.UNITS_HRR}' /"
    )

    # 4d 壁面热通量（顶棚若干代表点，§4.7 壁面吸热参考）
    lo = max(cfg.PORTAL_BUFFER, 0.15 * L)
    hi = min(L - cfg.PORTAL_BUFFER, 0.85 * L)
    for x in [x_fire - 2 * H, x_fire, x_fire + 2 * H]:
        if lo <= x <= hi:
            lines.append(
                f"&DEVC ID='{_id_x('Qw', x)}', QUANTITY='{cfg.QUANTITY_WALL_HEATFLUX}', "
                f"XYZ={x:.3f} {y_mid:.3f} {H:.3f}, IOR=3, UNITS='kW/m^2' /"
            )

    return lines


# ----------------------------------------------------------------------------
# 5. 切片场（§1.7 输出 / §4.7 全场物理解释）
# ----------------------------------------------------------------------------
def _fmt_slices(L, W, H, x_fire):
    lines = []
    y_mid = W / 2.0
    lo = max(cfg.PORTAL_BUFFER, 0.15 * L)
    hi = min(L - cfg.PORTAL_BUFFER, 0.85 * L)

    # 纵向中心面 y=W/2：温度、纵向速度、密度（回流长度/烟气可视化）
    lines.append(f"&SLCF PBY={y_mid:.2f}, QUANTITY='{cfg.QUANTITY_TEMPERATURE}' /")
    lines.append(f"&SLCF PBY={y_mid:.2f}, QUANTITY='{cfg.QUANTITY_VELOCITY}', VECTOR=.TRUE. /")
    lines.append(f"&SLCF PBY={y_mid:.2f}, QUANTITY='{cfg.QUANTITY_DENSITY}' /")

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
               title=None, group=None, ramp_inlet=True):
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
    """
    L = cfg.L if L is None else L
    W = cfg.W if W is None else W
    H = cfg.H if H is None else H
    x_fire = (L / 2.0) if x_fire is None else x_fire
    T_end = cfg.T_END_DEFAULT if T_end is None else T_end
    title = title or f"Q={Q}MW U={U}m/s Df={Df}m dx={dx}m L={L} W={W} H={H}"
    if group:
        title = f"[{group}] {title}"

    region = (max(cfg.PORTAL_BUFFER, 0.15 * L), min(L - cfg.PORTAL_BUFFER, 0.85 * L))
    sensor_xs = cfg.sensor_layout(fire_x=x_fire, region=region)

    burner_block, side = _fmt_surf_burner(Q, Df)
    inlet_blocks = _fmt_inlet(L, W, H, U, ramp_inlet=ramp_inlet)

    parts = []
    parts.append(f"&HEAD CHID='{chid}', TITLE='{title}' /")
    parts.append(_fmt_mesh(L, W, H, dx))
    parts.append(f"&TIME T_END={T_end:.1f} /")
    parts.append(
        f"&DUMP DT_DEVC={cfg.DT_DEVC}, DT_SLCF={cfg.DT_SLCF}, DT_BNDF={cfg.DT_BNDF} /"
    )
    parts.append(f"&MISC SIMULATION_MODE='LES', RESTART=.FALSE., TMPA={cfg.T_AMBIENT_C} /")
    parts.append(_fmt_spec())
    parts.append(_fmt_reac())
    parts.append(_fmt_matl())
    parts.append(_fmt_surf_wall())
    parts.append(burner_block)
    parts.extend(inlet_blocks)
    if ramp_inlet and U and abs(U) > 1e-6:
        parts.append(_fmt_ramp_inlet())
    parts.extend(_fmt_geometry(L, W, H, x_fire, side))
    parts.extend(_fmt_devices(x_fire, sensor_xs, L=L, W=W, H=H))
    parts.append(f"&BNDF QUANTITY='{cfg.QUANTITY_BNDF}' /")   # 边界场：壁面温度（§4.7 壁面温度参考）
    parts.extend(_fmt_slices(L, W, H, x_fire))
    parts.append("&TAIL /")

    return "\n".join(parts) + "\n"


def write_fds(spec, outdir):
    """spec: dict 至少含 chid,Q,U,Df,dx；其余键可选覆盖默认。"""
    chid = spec["chid"]
    text = render_fds(
        chid=chid,
        Q=float(spec["Q"]),
        U=float(spec.get("U", 0.0)),
        Df=float(spec["Df"]),
        dx=float(spec["dx"]),
        L=float(spec["L"]) if spec.get("L") not in (None, "") else cfg.L,
        W=float(spec["W"]) if spec.get("W") not in (None, "") else cfg.W,
        H=float(spec["H"]) if spec.get("H") not in (None, "") else cfg.H,
        x_fire=float(spec["x_fire"]) if spec.get("x_fire") not in (None, "") else None,
        T_end=float(spec["T_end"]) if spec.get("T_end") not in (None, "") else cfg.T_END_DEFAULT,
        group=spec.get("case_group") or spec.get("group"),
        title=spec.get("note"),
        ramp_inlet=_to_bool(spec.get("ramp_inlet", "1")),
    )
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"{chid}.fds")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def _to_bool(v):
    return str(v).strip().lower() in ("1", "true", "yes", "y", "t")


# ----------------------------------------------------------------------------
# 7. CLI
# ----------------------------------------------------------------------------
def _run_csv(csv_path, outdir):
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row = {k: (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
            path = write_fds(row, outdir)
            print(f"[OK] {row.get('chid'):>20s} -> {path}")


def main():
    ap = argparse.ArgumentParser(description="隧道火灾 FDS 输入文件生成器")
    ap.add_argument("--csv", help="按 CSV 批量生成（推荐）")
    ap.add_argument("--outdir", default="fds_cases", help="输出目录")
    ap.add_argument("--chid", help="单工况：案例标识")
    ap.add_argument("--Q", type=float, help="火源总功率 [MW]")
    ap.add_argument("--U", type=float, default=0.0, help="纵向风速 [m/s]")
    ap.add_argument("--Df", type=float, help="火源等效直径 [m]")
    ap.add_argument("--dx", type=float, default=0.25, help="网格尺寸 [m]")
    ap.add_argument("--L", type=float, help="隧道长度 [m]")
    ap.add_argument("--W", type=float, help="隧道宽度 [m]（外部试验可覆盖）")
    ap.add_argument("--H", type=float, help="隧道高度 [m]（外部试验可覆盖）")
    ap.add_argument("--x_fire", type=float, help="火源 x 位置 [m]")
    ap.add_argument("--T_end", type=float, help="模拟结束时间 [s]")
    ap.add_argument("--group", help="工况分组标签")
    ap.add_argument("--note", help="标题备注")
    args = ap.parse_args()

    if args.csv:
        _run_csv(args.csv, args.outdir)
        return

    if not args.chid or args.Q is None or args.Df is None:
        ap.error("单工况模式需提供 --chid --Q --Df（可选 --U --dx --L --W --H --x_fire --T_end）")

    spec = dict(chid=args.chid, Q=args.Q, U=args.U, Df=args.Df, dx=args.dx,
                L=args.L, W=args.W, H=args.H, x_fire=args.x_fire, T_end=args.T_end,
                case_group=args.group, note=args.note)
    path = write_fds(spec, args.outdir)
    print(f"[OK] {args.chid} -> {path}")


if __name__ == "__main__":
    main()
