# §1.2 FDS 基准模型设置（FDS 6.9.1 正式证据基线）

> 几何和物理常量以 `src/fds/tunnel_config.py` 为单一事实源，输入由 `src/fds/generate_fds_case.py` 生成。任何 `.fds` 都是生成产物，不独立手改。

## 1. 基准几何

| 项 | 值 |
|---|---|
| 隧道 | L×W×H = 100×10×5 m |
| 火源中心 | (50, 5, 0) m |
| 洞口缓冲 | 3H = 15 m |
| 测量区 | x∈[15,85] m |
| 温度测点 | 先导同时布置 z/H=0.85/0.90/0.95；主体高度由 G2 冻结 |
| 纵向速度测点 | 顶棚中心线 z=0.95H=4.75 m |

外部复现工况覆盖 L/W/H 时，缓冲、测点高度、近场范围和步长均按该工况 H 重算。

## 2. 固定物理与 FDS 设置

| 项 | 设置 |
|---|---|
| 正式证据版本 | FDS 6.9.1；6.10.1 Revision `FDS-6.10.1-0-g12efa16-release` 仅保留兼容性短试算记录 |
| 环境 | `TMPA=20.0` °C |
| 湍流 | `SIMULATION_MODE='LES'` |
| 燃料 | `REAC FUEL='N-HEPTANE'`，不额外定义 SPEC，不写 SIMPLE_CHEMISTRY |
| 产率 | `SOOT_YIELD=0.037`，`CO_YIELD=0.010` |
| 混凝土 | ρ=2300 kg/m³，k=1.4 W/(m·K)，cp=0.88 kJ/(kg·K)，ε=0.9，厚 0.20 m |
| 火源 | 预设 `HRRPUA`，`TAU_Q=10 s` |
| 通风 | 本协议 U=0，x=0 与 x=L 均为 OPEN；禁止 INLET/VEL/RAMP_V |

## 3. 网格与燃烧器功率闭合

| 档 | dx | 基准单元数 |
|---|---:|---:|
| 粗 | 0.5 m | 200×20×10 |
| 中 | 0.25 m | 400×40×20 |
| 细 | 0.20 m | 500×50×25 |

当前 v1.0 采用实施方案冻结的 `no_wind_symmetric_v1` 候选拓扑：x 边界为 `0,9,18,27,36,64,73,82,91,100 m`，y 无内部接口；中央 `36~64 m` 沿 z 分成五层，两侧八段各沿 z 分成两层，共 21 MESH。分区必须完整覆盖、镜像、无缝且不切穿燃烧器。MPI 任务数不得超过 21，正式并行配置由专用 60 s 工况实测决定。0.125 m 只在 0.25 m 网格门失败并发布新协议后作为 v1.1 对照，不属于 v1.0 授权输入。

连续目标面积为 `A_f=πD_f²/4`。生成器将等面积方形四边向外吸附到 x/y 网格面，再按实际离散面积计算：

```text
A_discrete = (x1-x0)(y1-y0)
HRRPUA = Q×10³/A_discrete   （Q: MW，HRRPUA: kW/m²）
```

v1.0 的主体燃烧器边长直接采用与 0.25 m 网格对齐的 `3.5/4.5/5.0 m`，先导另用 4.0 m。FDS `SURF HRRPUA` 输入单位是 kW/m²，不是 W/m²。燃烧器与四块地面 WALL 使用完全相同的分界坐标，不重叠。`TAU_Q=10 s` 是 `tanh(t/TAU_Q)` 增长时间尺度；正式功率应在约 `3×TAU_Q` 后并进入准稳态时，以 `_devc.csv HRR_tot` 或 `_hrr.csv HRR` 时间平均核验。

## 4. 输出合同

- `light`/`heavy` 都使用 `DT_DEVC=1.0`, `DT_HRR=1.0`；场输出频率按协议档位生成，heavy 仅用于先导和代表工况。
- 固定全局测点：x=15~30 m 按 2.5 m、30~70 m 按 1.25 m、70~85 m 按 2.5 m；偏移火源不得平移测点。
- 气相纵向速度：`QUANTITY='U-VELOCITY'`，不写 IOR。
- 总 HRR：`HRRPUV` + `SPATIAL_STATISTIC='VOLUME INTEGRAL'`。
- 顶棚净热通量：`NET HEAT FLUX`, `IOR=-3`。
- 边界场：显式 `BNDF QUANTITY='WALL TEMPERATURE'`。
- 火源辐射与对流份额读取 `_hrr.csv`：`χ_r=-Q_RADI/HRR`，`Q_c,source=HRR+Q_RADI`。`Q_CONV` 是计算域能量收支项，不替代火源对流 HRR。
- `DEVC` 不强写 UNITS；`fds_io` 读取单位行并统一到 °C、kW、kW/m²、m/s。现有 6.9.1 正式回传与 6.10.1 兼容性短试算均可由该接口读取。

## 5. 首次运行与生产门

1. 日志版本必须为本批声明版本；当前正式证据基线为 FDS 6.9.1。先修第一条 `ERROR(...)`，并检查全部 WARNING。
2. 不得有 VENT overlap/rejected，尤其不能拒绝 BURNER。
3. 火灾短试算中 `HRR_tot>0`；背景短试算无热源。无风工况检查双侧速度与对称性，不使用旧有风回流长度结论。
4. `_devc.csv` 含温度、`U_*`、`HRR_tot` 和 `Qw_*`；`_hrr.csv` 含 `HRR/Q_RADI`，且均可由 `fds_io` 解析。
5. 5 s 只验证解析和启动；正式结果的 `T_END` 必须覆盖 10 s 斜坡、至少 90 s 趋势检查和冻结的 60 s 同步平均窗口。
6. 网格生产选择由 ΔT_p、x_p、κ_u、κ_d 的中→细收敛性决定，不由单一 D*/dx 指标决定。
