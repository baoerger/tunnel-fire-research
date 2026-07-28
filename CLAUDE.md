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
- 已确认 FDS 6.8 合法:`'HRR'`、`'CONVECTIVE HRR'`、`'VOLUME INTEGRATION'`、`'TEMPERATURE'`、`'U/V/W VELOCITY'`、`'NET HEAT FLUX'`、`'DENSITY'`、`'WALL TEMPERATURE'`。

### 1.7 校验工具的"未知/不支持"先辨真伪,勿盲改
- `SIMPLE_CHEMISTRY`(REAC)、`TAU`(SURF)、`UNITS`(DEVC)在 FDS 6.8 均为**合法参数**。若第三方导入工具报"未知/不支持",是**工具过时**,不是 FDS 错误——**不要为迁就工具删这些参数**(删 `UNITS` 会丢单位换算)。
- 区分:工具的中文/HTML 提示多为误报;FDS 自身的英文告警(如 *"As of FDS 6.8 …"*、*"IOR is only used with devices attached to a solid surface"*、*"Quantity missing"*)才是真问题。

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
- [ ] `HRR_tot` 稳态均值 ≈ 目标 Q(核验 §1.5 闭环)
- [ ] z=4.5 m 距顶棚 ≥2 个网格、近壁温度稳定
- [ ] 顶棚壁面热通量 `Qw_*`(IOR=3)结果非 ~0;若异常,试 IOR=-3(顶棚气侧法向朝下)
