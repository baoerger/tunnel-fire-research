# §1.2 FDS 基准模型设置（FDS 6.10.1）

> 几何和物理常量以 `src/tunnel_config.py` 为单一事实源，输入由 `src/generate_fds_case.py` 生成。`tunnel_benchmark.fds` 是生成产物，不独立手改。

## 1. 基准几何

| 项 | 值 |
|---|---|
| 隧道 | L×W×H = 100×10×5 m |
| 火源中心 | (50, 5, 0) m |
| 洞口缓冲 | 3H = 15 m |
| 测量区 | x∈[15,85] m |
| 温度测点 | 顶棚中心线 z=0.90H=4.5 m |
| 纵向速度测点 | 顶棚中心线 z=0.95H=4.75 m |

外部复现工况覆盖 L/W/H 时，缓冲、测点高度、近场范围和步长均按该工况 H 重算。

## 2. 固定物理与 FDS 设置

| 项 | 设置 |
|---|---|
| 目标版本 | FDS 6.10.1（Revision `FDS-6.10.1-0-g12efa16-release`） |
| 环境 | `TMPA=20.0` °C |
| 湍流 | `SIMULATION_MODE='LES'` |
| 燃料 | `REAC FUEL='N-HEPTANE'`，不额外定义 SPEC，不写 SIMPLE_CHEMISTRY |
| 产率 | `SOOT_YIELD=0.037`，`CO_YIELD=0.010` |
| 混凝土 | ρ=2300 kg/m³，k=1.4 W/(m·K)，cp=0.88 kJ/(kg·K)，ε=0.9，厚 0.20 m |
| 火源 | 预设 `HRRPUA`，`TAU_Q=10 s` |
| 通风 | x=0 的负 `VEL` 产生 +x 入流；x=L 为 OPEN；U=0 时两端 OPEN |

## 3. 网格与燃烧器功率闭合

| 档 | dx | 基准单元数 |
|---|---:|---:|
| 粗 | 0.5 m | 200×20×10 |
| 中 | 0.25 m | 400×40×20 |
| 细 | 0.125 m | 800×80×40 |

网格敏感性 9 个输入均按 `11×1×2=22 MESH` 划分；分区边界位于原始网格线上，完整覆盖且不切穿燃烧器。MPI 任务数不得超过 22，正式并行配置由专用 60 s 短算例实测决定。

连续目标面积为 `A_f=πD_f²/4`。生成器将等面积方形四边向外吸附到 x/y 网格面，再按实际离散面积计算：

```text
A_discrete = (x1-x0)(y1-y0)
HRRPUA = Q×10³/A_discrete   （Q: MW，HRRPUA: kW/m²）
```

基准 Df=5 m、dx=0.25 m 时，燃烧器为 4.5×4.5 m，`A_discrete=20.25 m²`，`HRRPUA=1,975.3 kW/m²`。FDS `SURF HRRPUA` 输入单位是 kW/m²，不是 W/m²。燃烧器与四块地面 WALL 使用完全相同的分界坐标，不重叠。`TAU_Q=10 s` 是 `tanh(t/TAU_Q)` 增长时间尺度；正式功率应在约 `3×TAU_Q` 后并进入准稳态时，以 `_devc.csv HRR_tot` 或 `_hrr.csv HRR` 时间平均核验。

## 4. 输出合同

- `DUMP`: `DT_DEVC=1.0`, `DT_HRR=1.0`, `DT_SLCF=5.0`, `DT_BNDF=10.0`。
- 气相纵向速度：`QUANTITY='U-VELOCITY'`，不写 IOR。
- 总 HRR：`HRRPUV` + `SPATIAL_STATISTIC='VOLUME INTEGRAL'`。
- 顶棚净热通量：`NET HEAT FLUX`, `IOR=-3`。
- 边界场：显式 `BNDF QUANTITY='WALL TEMPERATURE'`。
- 火源辐射与对流份额读取 `_hrr.csv`：`χ_r=-Q_RADI/HRR`，`Q_c,source=HRR+Q_RADI`。`Q_CONV` 是计算域能量收支项，不替代火源对流 HRR。
- `DEVC` 不强写 UNITS；`fds_io` 读取单位行并统一到 °C、kW、kW/m²、m/s。FDS 6.10.1 当前实跑输出为 C/kW/kW/m²。

## 5. 首次运行与生产门

1. 日志版本必须为目标 FDS 6.10.1；先修第一条 `ERROR(...)`，并检查全部 WARNING。
2. 不得有 VENT overlap/rejected，尤其不能拒绝 BURNER。
3. 短试算中 `HRR_tot>0`，中心速度切片确认入口方向为 +x。
4. `_devc.csv` 含温度、`U_*`、`HRR_tot` 和 `Qw_*`；`_hrr.csv` 含 `HRR/Q_RADI`，且均可由 `fds_io` 解析。
5. 5 s 只验证解析和启动；正式结果的 `T_END` 必须覆盖 10 s 斜坡、准稳态到达和 10~30 s 平均窗口。
6. 网格生产选择由 ΔT_p、x_p、κ_u、κ_d 的中→细收敛性决定，不由单一 D*/dx 指标决定。
