"""
tunnel_config.py — 隧道顶棚温度反演研究 · 共享配置与几何/物理常量

本模块被以下脚本共同导入，保证 FDS 输入、测点布置、分析脚本使用完全一致的
几何与物理定义（对应 ``docs/实施方案.md`` 的变量和 FDS 合同）：

  - generate_fds_case.py        生成单个 FDS 输入文件 (.fds)
  - generate_sensor_layout.py  生成测点布置 CSV 与示意图
  - analyze_grid_convergence.py 网格敏感性分析（§1.3）
  - analyze_boundary_effect.py  隧道长度/洞口边界分析（§1.4）
  - check_convection_ratio.py   对流比例检查（§1.8）
  - quasi_steady_detect.py      准稳态判定（§1.9）
  - time_average_bootstrap.py   时间平均与分块 Bootstrap（§1.9）

设计纪律（与研究范围一致）：
  - 固定全尺寸矩形隧道 H=5 m, W=10 m, L=100 m。2026-07-31 用户取消长度
    对照，后续结论因此限定在 100 m 数值域内，不解释为洞口边界已验证。
  - 单一地面火源；固定燃料与辐射参数；纵向通风；准稳态为主体。
  - 一旦测点高度确定，所有 FDS 工况、解析模型、反演、网络统一采用同一温度定义。

注：以下物理量值为合理的工程默认值，均已在各处文档中标注“预设/待 §1.8 验证”，
可在不改变研究结构的前提下调整。
"""

import math

# ============================================================================
# 1. 隧道几何（§1.2 / 全局符号表）
# ============================================================================
H = 5.0        # 隧道高度 [m]
W = 10.0       # 隧道宽度 [m]
L = 100.0      # 隧道长度 [m]，资源约束固定值；洞口独立性未验证

# 资源约束下的后续工作网格。2026-07-31 用户明确不再增加更细网格；
# 0.25 m 因而用于长度、先导和数据库准备，但现有中→细结果未收敛，
# 不能把本常量解释为“已证明网格无关”的生产网格。
WORKING_GRID_DX = 0.25  # [m], RESOURCE_CONSTRAINED_NOT_CONVERGED

# 坐标约定：x 沿隧道纵长 [0, L]；y 横跨宽度 [0, W]；z 自地面铅垂 [0, H]
X0, X1 = 0.0, L          # 纵向起止（洞口）
Y0, Y1 = 0.0, W          # 横向起止
Z0, Z1 = 0.0, H          # 铅垂起止（地面 / 顶棚）

# 洞口缓冲与均匀测量区（§1.2：洞口向内预留 >=3H≈15 m 作缓冲）
PORTAL_BUFFER = 3.0 * H                       # 15 m
MEAS_REGION = (PORTAL_BUFFER, L - PORTAL_BUFFER)   # (15, 85)，跨度 70 m≈14H

# 主体闭合用火源位置（隧道中部，距两洞口各 10H）
X_FIRE_DEFAULT = L / 2.0                       # 50 m

# ============================================================================
# 2. 环境与燃烧（§1.2 / §1.8）
# ============================================================================
T_AMBIENT_C = 20.0       # 环境温度 [°C]
T_AMBIENT_K = T_AMBIENT_C + 273.15   # 293.15 K
P_AMBIENT = 101325.0     # 环境压力 [Pa]
G = 9.81                 # 重力加速度 [m/s^2]

# 辐射份额 χ_r —— 预设值。§1.8 必须用 FDS 实际输出 Q_c/Q 检验其是否稳定；
# 若随 Q/风速/尺寸系统性变化，须建立修正关系（见 MODEL_SETTINGS.md §1.8 决策门）。
CHI_R_PRESET = 0.30

# 空气物性（用于无量纲化 Q_c*、Fr 等，后续阶段统一引用）
RHO_0 = 1.2          # 环境空气密度 [kg/m^3]（约值，与 T0 对应）
CP = 1005.0          # 定压比热 [J/(kg·K)]

# 火源功率区间（§3.1 / §8.1）：Q = 5~100 MW，三档
Q_RANGE = (5.0, 100.0)
Q_BANDS = {"low": (5.0, 20.0), "mid": (20.0, 50.0), "high": (50.0, 100.0)}

# 历史候选设计使用的单位面积热释放率范围。它同时约束连续等效圆面积
# Q/A_f 与吸附到网格后的离散燃烧器 HRRPUA；只是提交前的软件设计边界，仍须
# 该范围仍须由文献复核、目标燃料设置和正式 HRR 闭合确认，不能作为科研结论。
QDASH_CANDIDATE_RANGE = (500.0, 3000.0)   # [kW/m^2]
HRRPUA_CANDIDATE_RANGE = (500.0, 3000.0)  # [kW/m^2]
# 保留旧名称供现有下游代码使用；其含义同样是“候选设计范围”。
QDASH_RANGE = QDASH_CANDIDATE_RANGE

# ============================================================================
# 3. 燃料与反应（§1.2 固定燃料与辐射参数）
# ============================================================================
# 采用正庚烷 (n-heptane, C7H16) 简化化学反应作为隧道池火替代物；
# 较甲烷更易产生碳烟，便于实现与研究范围相称的辐射份额（§1.8 验证）。
# —— 若 §1.8 表明 χ_r 系统性偏低，可改用更高 soot_yield 或其它燃料。
FUEL_NAME = "N-HEPTANE"
FUEL_FORMULA = "C7H16"
SOOT_YIELD = 0.037     # 庚烷典型碳烟产率
CO_YIELD = 0.010       # 一氧化碳产率（燃烧状态参考，非主体闭合变量）

# ============================================================================
# 4. 壁面材料（§1.2 固定壁面材料：混凝土隧道衬砌）
# ============================================================================
# FDS MATL 一维热传导用，取混凝土典型物性。
CONCRETE = {
    "id": "concrete",
    "density": 2300.0,          # [kg/m^3]
    "conductivity": 1.4,        # [W/(m·K)]
    "specific_heat": 0.88,      # [kJ/(kg·K)]
    "emissivity": 0.9,
    "thickness": 0.20,          # [m] 衬砌厚度（一维热传导深度）
}

# ============================================================================
# 5. 测点高度与传感器（§1.7 / 全局符号表：顶棚下方 0.05H~0.10H）
# ============================================================================
# 测点高度统一设于顶棚下方 0.10H（=0.5 m → z=4.5 m）。
# 选择 0.10H 而非 0.05H：在 0.25 m 生产网格上距顶棚 2 个网格，近壁数值更稳；
# §1.3 网格收敛性应同时核查该高度温度是否随网格稳定；若需可调至 0.05H~0.10H 区间。
Z_SENSOR_BELOW_CEILING = 0.10 * H      # 0.5 m
Z_SENSOR = H - Z_SENSOR_BELOW_CEILING  # 4.5 m（气体温度测点）
# 顶棚附近纵向速度测点（用于 U_e 相关流场、回流长度量测），略低于温度测点
Z_VELOCITY = H - 0.05 * H               # 4.75 m


def portal_buffer(height=None):
    """返回与工况高度一致的洞口缓冲长度 3H。"""
    height = H if height is None else height
    return 3.0 * height


def measurement_region(length=None, height=None):
    """返回工况的均匀测量区，兼顾 3H 洞口缓冲和 15% 长度边界。"""
    length = L if length is None else length
    buffer = portal_buffer(height)
    return max(buffer, 0.15 * length), min(length - buffer, 0.85 * length)


def sensor_heights(height=None):
    """返回工况温度和纵向速度测点高度 (0.90H, 0.95H)。"""
    height = H if height is None else height
    return 0.90 * height, 0.95 * height


def sensor_layout(height=None, near_step=None, far_step=None,
                  fire_x=None, region=None):
    """
    生成顶棚中心线虚拟测点 x 坐标（§1.7）。

    - 火源可能区（中部约 ±4H）附近间距 0.5H；
    - 远离火源后放宽至 1.0H；
    - 全部落在传入工况的均匀测量区内。

    返回有序 x 列表（基准几何为 20~30 个）。
    """
    height = H if height is None else height
    near_step = 0.5 * height if near_step is None else near_step
    far_step = 1.0 * height if far_step is None else far_step
    if fire_x is None:
        fire_x = X_FIRE_DEFAULT
    if region is None:
        region = MEAS_REGION
    lo, hi = region

    near_lo = max(lo, fire_x - 4.0 * height)
    near_hi = min(hi, fire_x + 4.0 * height)

    pts = set()

    def _range(a, b, step):
        # 含端点的等距点
        n = int(round((b - a) / step))
        for i in range(n + 1):
            pts.add(round(a + i * step, 4))

    # 远场左 [lo, near_lo]、近场 [near_lo, near_hi]、远场右 [near_hi, hi]
    if near_lo > lo:
        _range(lo, near_lo, far_step)
    _range(near_lo, near_hi, near_step)
    if near_hi < hi:
        _range(near_hi, hi, far_step)

    # 保证关键点存在：火源点、测量区端点
    pts.add(round(fire_x, 4))
    pts.add(round(lo, 4))
    pts.add(round(hi, 4))

    return sorted(pts)


def no_wind_dense_sensor_layout(height=None, fire_x=None, region=None):
    """生成无机械风公式重发现使用的左右对称密集中心线测点。

    距火源不超过 2H 时采用 0.25H 间距，2H~6H 采用 0.5H，
    更远处采用 1H。基础无风研究要求火源和测量区关于隧道中点对称；
    本函数只生成布局，生成器负责校验该使用前提。
    """
    height = H if height is None else height
    if height <= 0:
        raise ValueError("height 必须大于 0")
    if fire_x is None:
        fire_x = X_FIRE_DEFAULT
    if region is None:
        region = MEAS_REGION
    lo, hi = region
    if not lo <= fire_x <= hi:
        raise ValueError("fire_x 必须位于测量区内")

    left_extent = fire_x - lo
    right_extent = hi - fire_x
    if not math.isclose(left_extent, right_extent, rel_tol=0.0, abs_tol=1e-8):
        raise ValueError("no_wind_dense 测量区必须关于火源对称")
    extent = min(left_extent, right_extent)
    offsets = {0.0, round(extent, 8)}

    def add_zone(start, stop, step):
        stop = min(stop, extent)
        if stop < start - 1e-10:
            return
        count = int(math.floor((stop - start) / step + 1e-10))
        for index in range(count + 1):
            offsets.add(round(start + index * step, 8))
        offsets.add(round(stop, 8))

    add_zone(0.0, 2.0 * height, 0.25 * height)
    add_zone(2.0 * height, 6.0 * height, 0.50 * height)
    add_zone(6.0 * height, extent, 1.00 * height)

    points = {round(fire_x, 8), round(lo, 8), round(hi, 8)}
    for offset in offsets:
        points.add(round(fire_x - offset, 8))
        points.add(round(fire_x + offset, 8))
    return sorted(point for point in points if lo - 1e-8 <= point <= hi + 1e-8)


def no_wind_global_sensor_layout():
    """返回 v1 固定全局测点；坐标不随火源位置平移。"""
    points = set()
    for start, stop, step in ((15.0, 30.0, 2.5), (30.0, 70.0, 1.25),
                              (70.0, 85.0, 2.5)):
        count = int(round((stop - start) / step))
        points.update(round(start + index * step, 8) for index in range(count + 1))
    points.update((5.0, 10.0, 90.0, 95.0))
    return sorted(points)


NO_WIND_TEMPERATURE_HEIGHT_RATIOS = (0.85, 0.90, 0.95)


def no_wind_temperature_heights(height=None):
    """先导/G1 同时使用的三个近顶棚气相测温高度。"""
    height = H if height is None else height
    return tuple(ratio * height for ratio in NO_WIND_TEMPERATURE_HEIGHT_RATIOS)


# ============================================================================
# 6. FDS 设备量名（§1.8）—— 常量化以便按 FDS 版本集中修正
# ============================================================================
# 说明：以下为 FDS 设备量与空间统计量名称。不同 FDS 版本可能略有差异；
# 若启动时 FDS 报“未知量名”，只需在此处修改常量，无需改动生成器主体。
# §1.8 分析脚本对列名做模糊匹配，进一步提高版本兼容性。
QUANTITY_HRR_TOTAL = "HRRPUV"               # 单位体积热释放速率（区域体积分后为总 HRR）
STAT_VOLUME_INTEGRATION = "VOLUME INTEGRAL"
QUANTITY_TEMPERATURE = "TEMPERATURE"
QUANTITY_VELOCITY = "VELOCITY"            # 速度幅值（切片场 VECTOR=.TRUE. 用）
QUANTITY_VELOCITY_U = "U-VELOCITY"        # 纵向(x)速度分量；气相点测速不使用 IOR
QUANTITY_WALL_HEATFLUX = "NET HEAT FLUX"  # 壁面净热通量（§4.7 壁面吸热参考）
QUANTITY_DENSITY = "DENSITY"
QUANTITY_BNDF = "WALL TEMPERATURE"        # 边界场默认量；显式给出以兼容部分版本对空 &BNDF 的报错
# &DEVC 不显式写 UNITS；分析脚本以 CSV 单位行为准并归一到工程单位（°C/kW/kW·m⁻²）。
# fds_io 同时兼容 FDS 6.10.1 的 C/kW 输出及历史 K/W/MW 输出。


# ============================================================================
# 7. 无量纲量辅助（后续阶段统一引用，此处集中定义避免口径不一）
# ============================================================================
def froude(U):
    """无量纲风速 Fr = U/sqrt(gH)。"""
    return U / (G * H) ** 0.5


def q_star_convective(Q_c):
    """无量纲对流火源功率 Q_c* = Q_c / [rho0 cp T0 sqrt(g) H^(5/2)]  [W]→无量纲。"""
    denom = RHO_0 * CP * T_AMBIENT_K * (G ** 0.5) * (H ** 2.5)
    return Q_c / denom


# ============================================================================
# 8. 默认输出频率（§1.6 数据量控制）
# ============================================================================
DT_DEVC = 1.0      # 设备/传感器输出间隔 [s]（准稳态时间平均需 1 Hz）
DT_HRR = 1.0       # _hrr.csv 输出间隔 [s]（含 HRR、Q_RADI、Q_CONV 等能量收支项）
DT_SLCF = 5.0      # 切片场输出间隔 [s]（控制三维/二维数据量）
DT_BNDF = 10.0     # 边界场输出间隔 [s]
T_END_DEFAULT = 300.0   # 默认模拟结束时间 [s]（需 ≥ 稳态到达 + 60 s 平均窗口）
TAU_RAMP = 10.0          # 火源 HRR 斜坡时间 [s]（避免冲击式启动）

if __name__ == "__main__":
    xs = sensor_layout()
    print(f"隧道 {H}x{W}x{L} m | 测点 {len(xs)} 个 | z_temp={Z_SENSOR} m | z_vel={Z_VELOCITY} m")
    print("测点 x:", xs)
