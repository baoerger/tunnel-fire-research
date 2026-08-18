# CLAUDE.md — 隧道火灾 FDS 仿真研究 · 项目级指令

> 本文件每次会话自动加载，是本项目对 Claude 的持久约束。
> **凡涉及 `.fds` 输入文件的撰写、修改、生成和校验，必须遵守下文规范。**
> 当前正式证据基线：**FDS 6.10.1**，精确 Revision
> `FDS-6.10.1-0-g12efa16-release`。2026-08-18 用户明确接受本机可用版本，
> 四个 G1 无风短试算已通过运行合同与 HRR 闭合门，证据见
> `reports/g1_short_analysis.md`。后续正式工况统一按 6.10.1 审计，
> 不得与既有 6.9.1 结果混合拟合。
> 切换 FDS 版本时必须重新短试算，不得假定量名和 namelist 参数完全兼容。

## 0. 项目结构与单一事实源

| 路径 | 角色 |
|---|---|
| `src/fds/tunnel_config.py` | 几何、物理量及 FDS 量名常量的单一事实源 |
| `src/fds/generate_fds_case.py` | `.fds` 生成器；所有工况由它生成 |
| `docs/technical/FDS模型设置.md` | 模型设置和首次运行检查说明 |
| `src/fds/fds_io.py` | FDS CSV 读取和单位归一化 |
| `src/fds/check_fds_results.py` | 运行完整性、版本、日志、HRR 和关键列质量门 |
| `src/fds/check_convection_ratio.py` | 从 `_hrr.csv` 计算辐射与火源对流比例 |
| `config/` | 协议、工况、测点和 MESH 配置；`config/no_wind/` 现有 CSV 仍是未授权草案 |
| `fds_inputs/`、`runs/`、`derived/` | 可再生输入、逐 attempt 原始输出和派生结果；均不作为手改事实源 |

修改 FDS 行为的顺序：

1. 修改 `tunnel_config.py` 中的常量；
2. 必要时修改 `generate_fds_case.py`；
3. 重新生成 `.fds`；
4. 用目标版本 FDS 短试算；
5. 检查 FDS 自身的第一条 `ERROR(...)`、全部 `WARNING` 和输出 CSV。

禁止长期只手改生成后的 `.fds`。紧急手改后必须同步回配置或生成器，避免批量生成时恢复错误。

---

## 1. FDS 输入文件规范（6.10.1 正式证据基线）

### 1.1 合法性判据与排错顺序

- **FDS 自身实跑是最终判据。** PyroSim 或其他第三方工具的 `Unknown quantity`、`Adding to additional records section`、中文“未知属性”等提示不能替代 FDS 实跑。
- 第三方提示可以作为排查线索，但不得预先认定为误报；必须在目标 FDS 版本短试算验证。
- 每次优先修复 FDS 输出中的**第一条** `ERROR(...)`。修复后重跑，因为后续错误可能被前一处解析失败遮蔽。
- 输入成功进入时间推进不代表模型正确；仍须清除会导致设备、燃烧器或边界被拒绝的警告。
- 短试算至少验证：输入解析、时间推进、火源非零、入口方向、关键设备列和能量文件。

### 1.2 气相点速度分量：使用连字符，不使用 IOR

FDS 6.10.1 实跑确认纵向速度量名为：

```fds
&DEVC ID='U_5000', QUANTITY='U-VELOCITY', XYZ=50.000 5.000 4.750 /
```

规则：

- x/y/z 分量分别使用 `U-VELOCITY`、`V-VELOCITY`、`W-VELOCITY`。
- **不要使用** `U VELOCITY`；FDS 6.10.1 会报：

  ```text
  ERROR(1042): QUANTITY U VELOCITY not found.
  ```

- 气相点设备不写 `IOR`。`IOR` 用于附着在固体表面的设备，不能把速度幅值转换为分量。
- `QUANTITY='VELOCITY'` 表示速度幅值，不等价于纵向分量。
- 切片矢量场继续使用：

  ```fds
  &SLCF PBY=5.00, QUANTITY='VELOCITY', VECTOR=.TRUE. /
  ```

### 1.3 正庚烷反应：直接使用内置燃料，不额外定义 SPEC

FDS 6.10.1 已验证写法：

```fds
&REAC FUEL='N-HEPTANE', SOOT_YIELD=0.037, CO_YIELD=0.01 /
```

规则：

- `FUEL` 使用大写内置名称 `N-HEPTANE`。
- 本项目调用 FDS 内置正庚烷时**不额外写** `&SPEC ID='N-HEPTANE' /`；直接由 `REAC FUEL='N-HEPTANE'` 调用即可。
- 本次输入中的小写 `&SPEC ID='n-HEPTANE' /` 被 FDS 6.10.1 当作未完整定义物种，实跑出现：

  ```text
  WARNING: SPEC n-HEPTANE is not in the table of pre-defined species.
  ERROR(171): REAC, Not enough carbon for the CO_YIELD, SOOT_YIELD, and/or HCN_YIELD.
  ```

- 本项目**不写** `SIMPLE_CHEMISTRY=.TRUE.`。FDS 6.10.1 的简单反应由 `REAC FUEL=...` 建立；旧参数会导致 `Problem with REAC`。
- 不得为迁就第三方导入器删除 `SOOT_YIELD` 或 `CO_YIELD`；但必须确保燃料定义本身有效。

### 1.4 预设 HRR 燃烧器：面积、功率和斜坡闭环

面积和功率必须满足：

```text
A_f = π D_f² / 4
side = √A_f
HRRPUA = Q × 10³ / A_f   （Q 以 MW 输入，HRRPUA 单位为 kW/m²）
燃烧器方形面积 = side² = A_f
```

FDS 6.10.1 燃烧器表面写法：

```fds
&SURF ID='BURNER', HRRPUA=2037.2, TAU_Q=10.0, COLOR='ORANGE' /
```

规则：

- 火源功率斜坡参数是 `TAU_Q`，**不是** `TAU`。
- `TAU` 会导致：

  ```text
  ERROR(101): Problem with SURF ...
  ```

- FDS `SURF HRRPUA` 的输入单位是 **kW/m²**。若 Q 以 MW 给出，必须使用 `Q×10³/A`；写成 `Q×10⁶/A` 会把目标放大 1000 倍，并可能因供氧限制表现为异常但非精确的高 HRR。
- 若改 `Q`、`D_f` 或燃烧器几何，必须同步重算面积和 `HRRPUA`。
- FDS 会把 VENT 边界吸附到网格面；当 `side` 不是网格尺寸的整数倍时，离散后的实际燃烧面积可能不同于连续理论面积 `A_f`。
- `TAU_Q>0` 是双曲正切增长时间尺度，不是到 `t=TAU_Q` 即完成的线性斜坡：倍率约为 `tanh(t/TAU_Q)`，在 `1×TAU_Q` 时约 76%，`3×TAU_Q` 时约 99.5%；不得在 `t≈TAU_Q` 就判断功率闭合。
- 生成工况时应优先使燃烧器边界与网格线对齐，并按离散后的单元面积检查 `HRRPUA × A_discrete`；正式结果仍以约 `3×TAU_Q` 后且进入准稳态的 `_devc.csv HRR_tot` 或 `_hrr.csv HRR` 为准。
- 不得仅凭连续面积公式宣称功率“精确闭环”；网格变化时必须重新核验实际 HRR。
- `TAU_Q` 期间的瞬时 HRR 尚处于增长过程，不能用来判断稳态目标功率是否闭合。
- 应在约 `3×TAU_Q` 后并进入准稳态时，从 `_devc.csv` 的 `HRR_tot` 或 `_hrr.csv` 的 `HRR` 检查平均功率。

### 1.5 同一 RAMP 的各点必须是独立 namelist 记录

正确写法：

```fds
&RAMP ID='ramp_inlet', T=0.0,  F=0.0 /
&RAMP ID='ramp_inlet', T=10.0, F=1.0 /
```

不要把两个 `&RAMP` 拼在同一物理行：

```fds
&RAMP ... / &RAMP ... /
```

FDS 6.10.1 可能只读取第一个记录并报：

```text
ERROR(392): RAMP ramp_inlet has only one point.
```

生成器函数应返回多行列表，并由调用处使用 `extend`，不得把多个 namelist 拼成一个字符串。

### 1.6 区域总 HRR：HRRPUV + SPATIAL_STATISTIC

FDS 6.10.1 已验证写法：

```fds
&DEVC ID='HRR_tot', QUANTITY='HRRPUV',
      XB=0.00 100.00 0.00 10.00 0.00 5.00,
      SPATIAL_STATISTIC='VOLUME INTEGRAL' /
```

规则：

- 区域被积量使用 `HRRPUV`。
- 关键字使用 `SPATIAL_STATISTIC`，值使用 `VOLUME INTEGRAL`。
- **不要使用**：

  ```fds
  STATISTICS='VOLUME INTEGRATION'
  ```

  FDS 6.10.1 实跑会报：

  ```text
  ERROR(890): DEVC HRR_tot STATISTICS is not recognized.
  ```

- `_devc.csv` 中该体积分设备的输出单位为 kW，应以 CSV 单位行作为准则。

### 1.7 不使用 CONVECTIVE HRR 设备量

以下设备在 FDS 6.10.1 中无效，必须删除：

```fds
&DEVC ID='HRR_conv', QUANTITY='CONVECTIVE HRR', ... /
```

实跑错误：

```text
ERROR(1042): QUANTITY CONVECTIVE HRR not found.
```

替代方案：

- 在 `&DUMP` 中设置 `DT_HRR=1.0`，读取 FDS 自动生成的 `<CHID>_hrr.csv`。
- `_hrr.csv` 包含 `HRR`、`Q_RADI`、`Q_CONV` 等能量收支列。
- 火源辐射份额按 FDS 符号约定计算：

  ```text
  χ_r = -Q_RADI / HRR
  Q_c,source = HRR + Q_RADI
  ```

  其中 `Q_RADI` 通常为负值，表示辐射能量损失。
- `_hrr.csv` 的 `Q_CONV` 是计算域能量收支中的对流项，**不能直接当作经验公式中的火源对流热释放率**。
- 本项目由 `check_convection_ratio.py` 从 `HRR` 与 `Q_RADI` 计算火源辐射和对流比例。

### 1.8 材料单位：SPECIFIC_HEAT 使用 kJ/(kg·K)

FDS `MATL` 中：

- `DENSITY`：kg/m³；
- `CONDUCTIVITY`：W/(m·K)；
- `SPECIFIC_HEAT`：kJ/(kg·K)。

混凝土正确写法：

```fds
&MATL ID='concrete', DENSITY=2300.0,
      CONDUCTIVITY=1.4, SPECIFIC_HEAT=0.88 /
```

不要把 `880 J/(kg·K)` 直接写成 `SPECIFIC_HEAT=880.0`，否则 FDS 会警告单位可疑，并使材料热容放大 1000 倍。

### 1.9 边界 VENT 不得重叠；燃烧器必须确认未被拒绝

- 同一边界面上的 `VENT` 若覆盖相同单元，FDS 会保留其中一个并拒绝另一个。
- 全地面 `WALL` 与地面燃烧器 `BURNER` 直接重叠时，可能出现：

  ```text
  WARNING: VENT ... overlaps VENT ...; VENT ... rejected.
  ```

- 如果被拒绝的是燃烧器，计算可继续甚至正常结束，但实际火源为零，这是严重的静默模型错误。
- 本项目必须把地面拆成燃烧器周围四块互不重叠的 `WALL`，中央单独放置 `BURNER`。
- 不应仅依赖调整 VENT 顺序来掩盖重叠；必须从几何上消除重叠。
- 短试算后必须检查 `_devc.csv` 中 `HRR_tot > 0`，并检查日志中没有 `BURNER` VENT 被拒绝。

### 1.10 顶棚壁面热通量设备使用气侧法向

顶棚位于 `z=H`，气体域在其下方，因此气侧法向为 `IOR=-3`：

```fds
&DEVC ID='Qw_5000', QUANTITY='NET HEAT FLUX',
      XYZ=50.000 5.000 5.000, IOR=-3 /
```

使用 `IOR=3` 可能导致：

```text
WARNING: DEVC ... is not within any mesh.
```

规则：

- 顶棚使用 `IOR=-3`；
- 地面气侧通常使用 `IOR=3`；
- 侧墙按气体所在方向选择 `±1/±2`；
- 壁面设备出现“不在网格内”时，先检查坐标和法向，而不是随意把设备移入气相单元。

### 1.11 环境温度、边界输出和入口方向

- 环境温度统一使用：

  ```fds
  &MISC SIMULATION_MODE='LES', RESTART=.FALSE., TMPA=20.0 /
  ```

- 不在 `SURF` 上写 `TAMBIENT`。
- `BNDF` 必须显式给出量名：

  ```fds
  &BNDF QUANTITY='WALL TEMPERATURE' /
  ```

- 本项目入口位于 `x=0`。`VEL` 使用负值使气流进入计算域并沿 +x 方向流动：

  ```fds
  &SURF ID='INLET', VEL=-2.500, RAMP_V='ramp_inlet' /
  ```

- `x=L` 使用 `SURF_ID='OPEN'`。
- 首次运行必须用中心纵剖面速度切片确认实际风向。

### 1.12 输出频率、CSV 表头与单位

推荐输出：

```fds
&DUMP DT_DEVC=1.0, DT_HRR=1.0, DT_SLCF=5.0, DT_BNDF=10.0 /
```

FDS 6.10.1 本项目实跑输出：

- `_devc.csv`：温度 `C`、速度 `m/s`、HRR `kW`、热通量 `kW/m2`；
- `_hrr.csv`：功率项为 `kW`；
- CSV 前两行通常是**单位行在前、字段名行在后**。

规则：

- 不依赖“默认一定是 SI 基本单位”的假设；始终读取 CSV 的单位行。
- `fds_io.py` 必须兼容“单位行在前”和历史文件“字段名行在前”两种格式。
- 不为第三方工具强行添加或删除 `DEVC UNITS`；本项目当前不显式写 `UNITS`，以 FDS 输出单位行为准。
- 分析前通过 `fds_io.normalize_units` 统一工程单位，避免旧结果与新结果口径不一致。

### 1.13 用户自行完成外部服务器运行

本项目的 FDS 实算由用户自行上传到外部服务器并运行。代理到达实算闸门时只负责：

1. 生成并静态审计 `.fds`，给出每个输入的完整本机路径、CHID 和 SHA-256；
2. 在本机预建每个 `job_attempt_id` 的唯一回传目录，列出 CHID 与回传目录对应关系；
3. 列出需要放回的结果文件，火灾工况至少包括 `.fds/.out/_devc.csv/_hrr.csv/.smv` 和可用的 `.end`，背景工况允许没有 `_hrr.csv`；heavy 工况另含所需切片和边界文件；
4. 等用户把结果放回指定目录后，再从 `.out` 核验实际 FDS 版本并执行完整性和质量审计。

除非用户另行明确要求，代理不提供或索取服务器账号、服务器目录、队列、MPI/OpenMP 参数和运行命令，也不建立用户需要填写的服务器请求表。服务器侧如何提交由用户自行管理；项目审计从生成输入、本机回传目录和返回的原始输出建立。

`.end` 建议保留，但不是唯一的正常完成证据。缺少 `.end` 时，若 `.out` 明确
包含 `STOP: FDS completed successfully`，且 `_devc.csv`、`_hrr.csv` 均达到
输入 `T_END`，可认定计算完整；三项替代证据不全时仍标记人工复核。

### 1.14 多 MESH 与并行运行

- 当前无风设计草案使用 `n_mesh_x=11`、`n_mesh_y=1`、`n_mesh_z=2`，即 `11×1×2=22 MESH`，但该值在 `protocol_v1.json` 冻结并通过先导网格门前不是正式生产结论。参数由 `config/` 中当前协议工况表提供，生成逻辑由 `src/fds/generate_fds_case.py` 实现。
- 多 MESH 边界必须按各轴整数单元索引划分并严格落在原始 `dx` 网格线上；所有分区必须完整覆盖计算域、互不重叠且无缝隙，总单元数必须与单 MESH 模型一致。
- 离散后的燃烧器 VENT 不得被任何内部 x/y MESH 边界切穿；修改火源位置、直径、网格尺寸或分区数后必须重新校验。地面燃烧器位于 `z=0`，当前 z 向内部边界不切穿燃烧器。
- 不得长期手改生成后的多 MESH `.fds`。分区策略修改必须先进入 CSV 或生成器，再重新生成全部受影响输入并运行合同测试。
- MPI 进程数不得超过 MESH 数。44 核及以上可优先短测 `22 MPI × 2 OpenMP`；40 核分配不得超配，可比较 `11 MPI × 3 OpenMP` 与 `22 MPI × 1 OpenMP`。这些配置只是起点，正式采用前必须以同一短算例的 wall-clock 比较为准。
- 并行基准与首次正式运行必须检查各 MESH 负载、FDS 日志中的第一条 `ERROR(...)`、全部 `WARNING` 和 `rejected`，并核验火源 HRR、设备 CSV 与能量输出；MESH 数更多不自动代表运行更快。

### 1.15 输入文件结构

- 每条 namelist 记录以 `&` 开始、以 `/` 结束。
- 不把 PyroSim 日志、自然语言问题或 HTML 文本写入 `.fds`。
- `&TAIL /` 是最后一条 FDS 记录。
- 同一个 `DEVC ID` 不得重复。
- 生成文件不写解释性注释；模型说明写入项目文档。

---

## 2. 已验证错误与处置速查

| FDS 6.10.1 报文 | 根因 | 修复 |
|---|---|---|
| `ERROR(101): Problem with SURF ...` | 燃烧器写了 `TAU` | 改为 `TAU_Q` |
| `ERROR(101): Problem with REAC ...` | 使用了旧反应参数或无效燃料定义 | 使用 `REAC FUEL='N-HEPTANE'`，删除 `SIMPLE_CHEMISTRY` |
| `ERROR(171): Not enough carbon ...` | 自定义 `SPEC N-HEPTANE` 没有有效元素组成 | 删除该 `SPEC`，直接使用内置 `REAC FUEL` |
| `ERROR(890): ... STATISTICS is not recognized` | 使用了 `STATISTICS='VOLUME INTEGRATION'` | 改为 `SPATIAL_STATISTIC='VOLUME INTEGRAL'` |
| `ERROR(392): RAMP ... has only one point` | 两个 RAMP 拼在同一行 | 每个 RAMP 点单独一行 |
| `ERROR(1042): QUANTITY U VELOCITY not found` | 速度分量缺少连字符 | 改为 `U-VELOCITY` |
| `ERROR(1042): QUANTITY CONVECTIVE HRR not found` | 量名不存在 | 删除设备，从 `_hrr.csv` 读取 `HRR/Q_RADI` |
| `SPECIFIC_HEAT units are kJ/kg/K` | 把 J/(kg·K) 数值直接写入 | `880 J/(kg·K)` 写成 `0.88` |
| `VENT ... overlaps ... rejected` | 地面 WALL 与 BURNER 重叠 | 将地面拆成燃烧器周围四块 |
| `DEVC ... is not within any mesh`（顶棚热通量） | 顶棚设备法向写反 | 顶棚改用 `IOR=-3` |

---

## 3. 首次运行和版本切换必查

- [ ] FDS 日志显示的版本与本批证据基线一致；当前正式基准为 FDS 6.10.1，Revision `FDS-6.10.1-0-g12efa16-release`。
- [ ] `REAC FUEL='N-HEPTANE'` 无未定义物种警告，输入中没有额外 `SPEC N-HEPTANE`。
- [ ] 燃烧器使用 `TAU_Q`，入口 RAMP 的两个点各占一行。
- [ ] 所有气相纵向速度设备使用 `U-VELOCITY`，且不写 `IOR`。
- [ ] `HRR_tot` 使用 `HRRPUV + SPATIAL_STATISTIC='VOLUME INTEGRAL'`。
- [ ] 输入中没有 `CONVECTIVE HRR` 设备。
- [ ] `DUMP` 包含 `DT_HRR`，运行后 `_hrr.csv` 包含 `HRR` 和 `Q_RADI`。
- [ ] 混凝土 `SPECIFIC_HEAT=0.88`，不是 `880`。
- [ ] 日志中没有 VENT 重叠拒绝，尤其没有燃烧器 VENT 被拒绝。
- [ ] `HRRPUA` 以 kW/m² 输入，使用 `Q[MW]×10³/A_discrete`；短试算中 `HRR_tot > 0`，准稳态后平均 HRR 接近目标 Q。
- [ ] 入口风向为 +x，出口为 OPEN。
- [ ] 顶棚热通量设备使用 `IOR=-3`，没有“不在网格内”警告。
- [ ] `_devc.csv` 的设备 ID、单位和数值能被 `fds_io.read_devc` 正确读取。
- [ ] `_hrr.csv` 的 `HRR/Q_RADI` 能被 `check_convection_ratio.py` 正确处理。
- [ ] `T_END` 明显长于入口和火源斜坡时间，并包含足够的准稳态统计窗口；5 秒仅适合启动检查，不适合作为正式稳态结果。

---

## 4. 最小正确核心示例（FDS 6.10.1）

```fds
&HEAD CHID='smoke_gsB', TITLE='40 MW tunnel fire' /
&MESH IJK=400 40 20, XB=0.00 100.00 0.00 10.00 0.00 5.00 /
&TIME T_END=60.0 /
&DUMP DT_DEVC=1.0, DT_HRR=1.0, DT_SLCF=5.0, DT_BNDF=10.0 /
&MISC SIMULATION_MODE='LES', RESTART=.FALSE., TMPA=20.0 /

&REAC FUEL='N-HEPTANE', SOOT_YIELD=0.037, CO_YIELD=0.01 /
&MATL ID='concrete', DENSITY=2300.0, CONDUCTIVITY=1.4, SPECIFIC_HEAT=0.88 /
&SURF ID='WALL', MATL_ID='concrete', EMISSIVITY=0.9, THICKNESS=0.2 /
&SURF ID='BURNER', HRRPUA=2037.2, TAU_Q=10.0, COLOR='ORANGE' /
&SURF ID='INLET', VEL=-2.500, RAMP_V='ramp_inlet' /

&RAMP ID='ramp_inlet', T=0.0, F=0.0 /
&RAMP ID='ramp_inlet', T=10.0, F=1.0 /

&DEVC ID='U_5000', QUANTITY='U-VELOCITY', XYZ=50.000 5.000 4.750 /
&DEVC ID='HRR_tot', QUANTITY='HRRPUV',
      XB=0.00 100.00 0.00 10.00 0.00 5.00,
      SPATIAL_STATISTIC='VOLUME INTEGRAL' /
&DEVC ID='Qw_5000', QUANTITY='NET HEAT FLUX',
      XYZ=50.000 5.000 5.000, IOR=-3 /

&BNDF QUANTITY='WALL TEMPERATURE' /
&SLCF PBY=5.00, QUANTITY='VELOCITY', VECTOR=.TRUE. /
&TAIL /
```

该片段只展示关键语法。正式输入仍必须由生成器生成完整且互不重叠的入口、出口、顶棚、侧墙、分块地面和燃烧器几何。
