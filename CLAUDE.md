# CLAUDE.md — 隧道火灾 FDS 仿真研究 · 项目级指令

> 本文件每次会话自动加载,是本项目对 Claude 的持久约束。
> **凡涉及 `.fds` 输入文件的撰写、修改、生成,必须遵守下文 §1「FDS 输入文件编写规范」。**
> 目标运行平台:FDS 6.7.x+(校验告警实测来自 FDS 6.8)。
> Git 协作流程见根目录 `协作教程.md`(两台机器同步),此处不重复。

## 0. 项目结构与单一事实源

| 路径 | 角色 |
|---|---|
| `阶段一/src/tunnel_config.py` | 几何/物理/**量名常量**的单一事实源;FDS 量名/统计量名集中在此,按版本修正只改这里 |
| `阶段一/src/generate_fds_case.py` | `.fds` 生成器;**所有工况由它产出,禁止手写多份 `.fds`** |
| `阶段一/02_FDS基准模型/tunnel_benchmark.fds` | 基准 `.fds`(生成器产出,勿手改) |
| `阶段一/02_FDS基准模型/MODEL_SETTINGS.md` | 模型设置说明 + **§6 首次运行必查项** |
| `阶段一/03｜04｜05_*/*.csv` | 工况参数表(网格敏感性 / 长度边界 / 外部试验) |

改 FDS 行为的优先级:**改 `tunnel_config.py` 常量 → 改 `generate_fds_case.py` → 重新生成 `.fds`**;不直接手改已生成的 `.fds`(紧急修正后须同步回生成器,保持两者一致)。

---

## 1. FDS 输入文件编写规范(强制)

### 1.1 气相点测速用分量量名,不用 IOR
- `IOR` 只对**固壁设备**有效;对**气相点**(单点 `XYZ=`、非 `XB=` 区间)的 `QUANTITY='VELOCITY'`,`IOR` 会被忽略并告警 *"IOR is only used with devices attached to a solid surface"*,实际输出的是速度**幅值**,不是分量。
- 取纵向(x)分量必须用 `QUANTITY='U VELOCITY'`(`'V VELOCITY'`/`'W VELOCITY'` 同理),且**不写 IOR**。本项目用 `cfg.QUANTITY_VELOCITY_U`。
- 第三方工具若报 `Unknown quantity: U VELOCITY`,属工具误报(见 §1.7),**勿改回 `'VELOCITY'`**——会变回速度幅值、丢失纵向分量。
- **切片场 `&SLCF` 例外**:矢量场仍用 `QUANTITY='VELOCITY', VECTOR=.TRUE.`(不要改成分量量名)。
- ❌ `&DEVC ID='U_5000', QUANTITY='VELOCITY', XYZ=50 5 4.75, IOR=1, ...`
- ✅ `&DEVC ID='U_5000', QUANTITY='U VELOCITY', XYZ=50 5 4.75, ...`

### 1.2 FDS 6.8+ 燃料物种必须用 &SPEC 定义
- `&REAC FUEL='...'` 必须配一行 `&SPEC ID='...' /`,否则 FDS 6.8 *"用默认参数新建同名物种"*,燃料物性(分子量、燃烧热等)不对。
- 用 `&SPEC ID='n-HEPTANE' /` 可加载 FDS 预定义正庚烷真实物性(大小写不敏感)。生成器 `_fmt_spec()` 已产出此行,置于 `&REAC` 之前。
- 若某版本仍报 *"Creating new Species with default parameters"*,改 `tunnel_config.FUEL_NAME` 为大写 `'N-HEPTANE'`(MODEL_SETTINGS §6.6 兜底)。

### 1.3 环境温度用 &MISC TMPA,不在 &SURF 上写 TAMBIENT
- `TAMBIENT` **不是 FDS 合法参数**,会被忽略并产生"未知属性"告警。
- 环境温度统一用 `&MISC TMPA=<°C>`(默认 20);本项目显式写 `TMPA=20.0`。生成器在 `&MISC` 行已带 `TMPA={cfg.T_AMBIENT_C}`。

### 1.4 &BNDF 必须显式给 QUANTITY
- 空 `&BNDF /` 在部分版本触发 *"Quantity missing, BNDF record ignored"*(边界场不输出)。
- 显式写 `&BNDF QUANTITY='WALL TEMPERATURE' /`(本项目用 `cfg.QUANTITY_BNDF`)。

### 1.5 预设 HRR 燃烧器:面积与 HRRPUA 必须闭环
- 预设 HRR 燃烧器靠 `HRRPUA` 控制总功率;**燃烧器几何面积必须等于计算 HRRPUA 所用的面积**,否则实际火功率≠目标 Q。
- 约定:等效直径 `Df` → 等效面积 `A_f = π·Df²/4`;方形铺设 `side = √A_f`(**不是 `Df`**)。
- 闭环:`HRRPUA = Q·1e6 / A_f`,燃烧器 XB 面积 = `side² = A_f` → 实际 HRR = Q。
- 生成器 `_fmt_surf_burner` + `_fmt_geometry` 已闭环;**改燃烧器尺寸/功率时三者一起改**,并在 `_devc.csv` 核验稳态 `HRR_tot ≈ Q`。
- ❌ HRRPUA 按 A_f=19.63 算,却铺 side=5.0(面积 25) → 实际 ≈50.9 MW 而非 40 MW。

### 1.6 量名/统计量集中在 tunnel_config.py
- 所有 FDS 设备量名(`QUANTITY_*`)与统计量名(`STAT_*`)以常量给出,生成器引用常量而非硬编码字符串。
- 遇 FDS 报"未知量名":**只改 `tunnel_config.py` 常量**,不动生成器主体;分析脚本(`check_convection_ratio.py` 等)对列名做模糊匹配兜底。
- 已确认 FDS 6.8 合法:`'HRR'`、`'CONVECTIVE HRR'`、`'VOLUME INTEGRATION'`、`'TEMPERATURE'`、`'U/V/W VELOCITY'`、`'NET HEAT FLUX'`、`'DENSITY'`、`'WALL TEMPERATURE'`。若第三方工具报其中任一 "unknown quantity",属工具误报(§1.7),**勿改 `tunnel_config.py` 常量**。

### 1.7 第三方导入/校验工具的"未知/不支持"先辨真伪,勿盲改

**判定总则**:合法性以 **FDS 自身实跑(`fds`)**为准;FDS 真问题是其英文 ERROR/WARNING,会中止运行或影响输出。凡出现"导入对象/提取对象/附加字段/additional records section"等措辞的,都是**第三方导入工具**的容忍性提示(只收集不中止),其参数/量名库往往**滞后于 FDS**,会把合法项报成"未知/不支持"——**不是 FDS 错误**,绝大多数情况下**不要为迁就工具改 FDS**。

**已知工具误报清单(逐项处置)**——以下报文均实测由导入工具产生,对应参数/量名在 FDS 6.8 合法,**保留**:

| 工具报文(原文) | 对象 | FDS 6.8 实情 | 处置 |
|---|---|---|---|
| `记录REAC的未知属性SIMPLE_CHEMISTRY` | `&REAC` | 合法:`SIMPLE_CHEMISTRY=.TRUE.` 为本项目简化化学反应模型开关 | **保留**;删了退回需逐物种定义的复杂化学,破坏模型 |
| `记录SURF的未知属性TAU` | `&SURF`(BURNER) | 合法:`TAU` 为 HRRPUA 指数斜坡时间常数 | **保留**;删了火源瞬间满功率启动,产生冲击+数值噪声(等价替代 `RAMP_Q` 工具多半同样不认) |
| `这些字段还不支持:UNITS` | `&DEVC` | 合法:`UNITS` 指定 devc.csv 输出单位并触发换算 | **基准已选 B(删 UNITS),此报文不再出现**;见下“UNITS 两种取舍” |
| `Unknown quantity: U VELOCITY`(每个 U/V/W 测点各一条) | `&DEVC QUANTITY` | 合法:FDS6 UG 收录 `'U VELOCITY'`/`'V VELOCITY'`/`'W VELOCITY'` 为气相速度分量(§1.1/§1.6) | **保留且勿改**;改回 `'VELOCITY'` 会变回幅值、丢失纵向分量,且 `IOR` 对气相点无效救不回来(§1.1) |

**UNITS 的两种取舍(择一,勿反复)** —— **本项目基准已采用 (B)**:生成器 `_fmt_devices` 不写 `UNITS`,FDS 输出默认 SI(温度 K、HRR W、热通量 W/m²、速度 m/s);分析侧经 `fds_io.normalize_units` 按 devc.csv 的 units 列归一到工程单位(°C/kW/kW·m⁻²,对旧 run °C/kW 向后兼容),故下文 (B) 项“须确认单位口径”已落实。选 (B) 后工具仍报 `SIMPLE_CHEMISTRY`/`TAU`/`U VELOCITY` 三类误报,按 §1.7 继续忽略、**勿删**:
- (A,默认)**保留 `UNITS`**:FDS 按 `'C'`/`'m/s'`/`'kW'`/`'kW/m2'` 输出,分析脚本直接可读;代价:工具每次报"不支持:UNITS"(误报,忽略)。
- (B,若要让工具彻底闭嘴)**删除全部 `&DEVC` 的 `UNITS`**:FDS 改输出默认 SI(K、m/s、W、W/m² 等);**模拟不受影响**,仅 devc.csv 数值单位变;此时分析脚本须自行换算(`check_convection_ratio.py` 算 χ_r 用比值、单位自消,不受影响;但温度/HRR 绝对值需转 °C/kW,`quasi_steady_detect`/`analyze_grid_convergence` 须确认单位口径)。
- 无论 A/B,`UNITS` 字符串须为 FDS 规范写法:温度 `'C'`、速度 `'m/s'`、功率 `'kW'`、热通量 `'kW/m2'`(**不带脱字符** `^`——`'kW/m^2'` 易触发工具报错且非规范)。

**真伪判定法(清单外新报文通用)**:
1. 看措辞:中文 / `<html>` / "导入对象/提取对象/附加字段" / "Adding to additional records section" → **工具方容忍性提示**,默认按误报处理,不改 FDS。
2. 看 FDS 自身 stderr:英文 ERROR/WARNING,如 *"As of FDS 6.8 …"*、*"IOR is only used…"*、*"Quantity missing"*、*"Creating new Species with default parameters"* → **真问题**,按对应小节修(§1.1/§1.2/§1.4)。
3. 存疑时:**不改 FDS,先在目标 FDS 版本跑一次短试算**,用 FDS 自身输出判定;勿据工具报文删参数。
4. **切勿"为消除工具告警"删除** `SIMPLE_CHEMISTRY`/`TAU`/`U/V/W VELOCITY`——删了破坏模型或测点意图;`UNITS` 可按上表取舍,但须同步检查分析脚本单位口径。

### 1.8 其他已定约定
- 入口 `VEL` 符号:负值把气流推入域内(+x);出口端 `SURF_ID='OPEN'`。首次运行用中心纵剖面速度切片确认风向(MODEL_SETTINGS §6.1)。
- 壁面靠 `&VENT … SURF_ID='WALL'` 在边界面指派;地面/顶棚/侧墙统一混凝土 `WALL` 表面。
- 测点高度统一顶棚下方 0.10H(z=4.5 m);速度测点 z=4.75 m。
- 输出频率:DEVC 1 Hz、SLCF 5 s、BNDF 10 s(见 `DT_*` 常量)。
- 文件不含 FDS 注释行(不同版本注释口径不一),说明写 `MODEL_SETTINGS.md`。

---

## 2. 首次运行 / 版本切换必查(详见 MODEL_SETTINGS.md §6)
- [ ] `&SPEC ID='n-HEPTANE'` 加载的是预定义物种(无 *"default parameters"* 告警)
- [ ] 入口风向为 +x(短时试算 T_END≈5 s 看速度切片)
- [ ] 壁面表面已应用混凝土 `WALL`
- [ ] `HRR_tot` 稳态均值 ≈ 目标 Q(核验 §1.5 闭环);**选项 B 下 HRR 单位为 W**,故数值 ≈ Q[MW]×1e6(如 40 MW→4.0e7 W),分析脚本经 `fds_io` 归一为 kW
- [ ] z=4.5 m 距顶棚 ≥2 个网格、近壁温度稳定
- [ ] 顶棚壁面热通量 `Qw_*`(IOR=3)结果非 ~0;若异常,试 IOR=-3(顶棚气侧法向朝下)
