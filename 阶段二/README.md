# 阶段二：先导结构判伪

> **历史冻结（2026-08-14）：**本阶段属于已作废的纵向通风反演路线。
> 代码仅可按 [`../无风公式重发现/复用与作废边界.md`](../无风公式重发现/复用与作废边界.md)
> 作方法参考，不得继续生成当前工况或形成新科研结论。

阶段目标是用 12 个开发集先导工况判定分段指数远场结构是否值得扩展到 68 组数据库。当前已完成候选设计、临界风速分层、解析结构、删失分类、连续峰值和真实结果集成分析工具。用户于 2026-07-31 固定核心模型为 100 m、0.25 m，并授权 12 个先导与 3 个外部试验复现并行；该授权不代表阶段一验证或阶段二判伪通过。

12 组主体、2 组 x=41 m 诊断和 2 组非零随机种子重复的 FDS 6.9.1 结果均已
找到并通过质量检查。工程传感器阈值依据 IEC 60584-1 的 K 型 2 级允差及
用户批准固定为 5 °C；两组父/重复工况给出的弱温升噪声限为
0.1250/0.0774 °C，代表性保守包络为 0.1250 °C，因此正式检测阈值仍为 5 °C。

补齐阈值证据后，协议 V1 的原 12 组正式分析仍只有 4/12 通过全部门，正式门为 `FAIL`；
x=41 m 诊断只有 d70 通过，d80 仍下游不可辨识。全场 PBY 温度切片进一步
显示 d80 在 x=85/99 m 的准稳态温升约为 291.5/275.7 °C，85--99 m 的对数
衰减 R² 仅 0.165，直到出口前仍是高温平台。当前 100 m 域不能支持该工况的
下游远场衰减结论。

用户于 2026-08-02 决定后续研究固定使用 100 m，并授权据此调整其他合同。
因此新增独立的条件域协议 V2，保留 V1 的失败证据：所有质量/准稳态通过工况
保留 15--85 m 温度剖面、峰值、平台宽度和删失类别；`k_u/k_d` 只有在对应
侧可辨识且稳定时才输出。现有 12 组对 V2 只能形成
`DEVELOPMENT_GATE_PASS_CONFIRMATION_PENDING`，正式科学确认等待开发数据库和
模型冻结后的封存独立测试。

## 当前文件

- `01_先导工况设计/pilot_design_targets.csv`：12 组设计意图的单一事实源。
- `src/design_pilot_cases.py`：按 Li (2010) 临界风速初估或真实回流覆盖值生成候选表并执行覆盖/功率密度合同；另提供不改变主表的 Wu–Bakar (2000) 水力高度敏感性备选。
- `01_先导工况设计/pilot_cases_candidate.csv`：可定位的当前候选产物。
- `01_先导工况设计/critical_velocity_estimates.csv`：当前 U_c 初估表。
- `01_先导工况设计/临界风速候选公式.md`：公式、适用边界和后续更新纪律。
- `01_先导工况设计/先导设计报告.md`：覆盖检查和阻塞状态。
- `01_先导工况设计/pilot_diagnostic_cases.csv`：不属于正式 12 组的 2 个强风测量域诊断工况；火源移至 41 m，用于检验峰值靠近出口导致的下游不可辨识。
- `01_先导工况设计/强风测量域诊断说明.md`：诊断动机、边界和回传要求。
- `01_先导工况设计/pilot_seed_repeat_cases.csv`：近临界与高功率强风各 1 个非零 `RND_SEED` 重复；与现有默认种子父工况配对，不属于 68 组数据库。
- `01_先导工况设计/随机种子重复说明.md`：FDS 6.9.1 官方语法依据、生成命令、证据边界和回传要求。
- `src/transport_model.py`：点源/有限源核、约束拟合、近场分组评价和区间扰动。
- `02_解析结构与参数提取/synthetic_software_check.csv`：明确标记的合成软件验证，不是科研数据。
- `02_解析结构与参数提取/解析结构软件验证报告.md`：当前能力与真实结果依赖。
- `src/censoring_peak.py`：三类上游观测分类、删失高斯损失、连续峰值、平台宽度、时间分块/测点扰动 Bootstrap 和判伪汇总。
- `src/analyze_pilot_falsification.py`：质量硬门、准稳态/时间平均读取、近场候选、区间敏感性、点源/有限源留一工况比较、峰值 Bootstrap 和五项判伪集成。
- `src/derive_seed_noise_thresholds.py`：比较父/重复各自准稳态均值，生成逐点噪声分量、两组代表阈值及保守赋值表。
- `src/diagnose_longitudinal_slice_domain.py`：组装多 MESH PBY 温度切片，以完整 0--100 m 剖面诊断出口前是否出现远场衰减；只作全场诊断，不替代 DEVC 判伪。
- `src/evaluate_stretched_exponential.py`：筛查全局共享的上下游伸缩指数；只增加两个全局结构量，不按工况增加自由度，并执行留一选择。
- `03_删失与峰值/synthetic_censor_peak_check.csv`：明确标记的删失/峰值合成软件验证，不是科研数据。
- `03_删失与峰值/强风删失与峰值软件验证报告.md`：候选方法、输入校验和真实结果依赖。
- `03_删失与峰值/sensor_threshold_evidence_template.csv`：已批准的 5 °C 工程传感器阈值证据；记录 IEC 60584-1 依据、温升差值的保守误差界和用户批准日期。
- `03_删失与峰值/seed_repeat_pointwise_noise.csv`：两组父/重复工况的 46 条逐测点差异、Bootstrap 分量和弱信号标记。
- `03_删失与峰值/seed_repeat_case_summary.csv`：代表工况 `DeltaT_noise=0.1250/0.0774 °C` 及输入路径。
- `03_删失与峰值/noise_thresholds_formal.csv`：将 0.1250 °C 保守代表包络显式赋给 12 个主体和 2 个诊断 CHID；不声称每个 CHID 都有独立重复。
- `03_删失与峰值/stretched_exponent_*.csv`：25 个全局指数候选、逐折选择及否决结论；最佳候选必要两门仍只有 6/12。
- `src/conditional_domain_protocol.py`：把 V1 指标迁移到独立的 100 m 条件域 V2
  输出合同，不覆盖 V1 文件；为两侧衰减参数分别生成可用性掩码。
- `04_100m条件域协议/`：V2 说明、12 组迁移目标与汇总；状态为开发通过、
  独立确认待完成，不是闭合或反演性能结论。

## 重新生成与测试

从项目根目录执行：

```bash
uv run --python 3.12 python 阶段二/src/design_pilot_cases.py
uv run --python 3.12 python 阶段一/src/generate_fds_case.py \
  --csv 阶段二/01_先导工况设计/pilot_cases_candidate.csv \
  --outdir 阶段一/outputs/pilot_inputs
uv run --python 3.12 python 阶段二/src/transport_model.py
uv run --python 3.12 python 阶段二/src/censoring_peak.py
uv run --python 3.12 python 阶段二/src/derive_seed_noise_thresholds.py \
  --additional-design 阶段二/01_先导工况设计/pilot_diagnostic_cases.csv
uv run --python 3.12 --with numpy python \
  阶段二/src/evaluate_stretched_exponential.py
PYTHONUTF8=1 uv run --python 3.12 --with numpy --with fdsreader==1.11.7 python \
  阶段二/src/diagnose_longitudinal_slice_domain.py
uv run --python 3.12 --with numpy --with matplotlib python \
  -m unittest discover -s 阶段二/tests -v
uv run --python 3.12 python 阶段二/src/conditional_domain_protocol.py
```

当前正式阈值结果的可重复分析命令：

```bash
uv run --python 3.12 --with numpy --with matplotlib python \
  阶段二/src/analyze_pilot_falsification.py \
  --noise-thresholds 阶段二/03_删失与峰值/noise_thresholds_formal.csv \
  --threshold-evidence 阶段二/03_删失与峰值/sensor_threshold_evidence_template.csv \
  --sensor-threshold-C 5 --n-boot 500
```

预分析选择 0.3H 近场排除；点源的工况中位全剖面 NRMSE 为 0.0988。
在对有限源的源中心、上下游衰减率和源强作受限拟合后，其留一工况中位
NRMSE 约 0.0513，相对改善约 48%，因此预分析选择有限源类；但留一折的
宽度系数选择分裂为 0.1（5 折）和 0.2（6 折），全数据最优 0.3，不能把
`c_s` 提前固化。
11/12 工况的时间块与测点扰动合并峰值区间宽于 0.5H，因此后续峰值偏移
只应采用低阶修正。`pilot_12_q100_r160_d80` 的峰值约在 78.3 m，当前有效
测量区止于 85 m，近场外下游只有 1 点，无法辨识下游衰减。当前质量通过、
阈值候选状态的明细位于 `outputs/pilot_falsification/`；旧目录只保留历史追溯。

用 500 次 Bootstrap 重新执行后结论不变：4/12 通过；单项门分别为远场
对数线性 8/12、区间稳定 6/12、近场残差 10/12、临界结构 9/12、删失稳定
11/12、源模型精度 11/12。当前失败不是代码回归或 FDS 版本造成。尤其强风
剖面在 85 m 附近仍保持高温平台，不能靠双尺度项或放宽阈值代替下游信息。
补齐正式阈值后各项通过数仍为：远场对数线性 8/12、区间稳定 6/12、近场
残差 10/12、临界结构 9/12、删失稳定 11/12、源模型精度 11/12。失败不是
版本、阈值或代码回归造成。d80 即使将火源移至 41 m，峰值仍在约 74--79 m，
全场温度在出口前保持平台；继续增加双尺度自由度没有可辨识数据支撑。
全局伸缩指数也不能挽救该门：全数据与留一筛查的远场形状+区间稳定联合通过
均为 6/12，仍低于 75%，因此不进入更复杂的逐工况双尺度模型。

真实阶段一/先导回流结果可通过 `--uc-overrides <csv>` 覆盖同一 Q 的经验 U_c。覆盖表必须包含 `Q_MW,Uc_FDS_m_s,source_chid,evidence_path,note`；脚本会把方法和证据路径写入输出，原始估计表不被静默改写。

## 条件性并行与正式化门

当前允许：

1. 按固定的 100 m、0.25 m 生成 12 个先导输入；
2. 与 3 个外部验证官方快照并行提交；
3. 已回传 12 组的质量证据按 6.9.1 基线保留，不因缺 `.end` 重跑。

仍然禁止：

1. 在外部验证和先导判伪结果回来前宣称模型有效；
2. 把协议 V1 的 `FAIL` 改写为通过，或用同一 12 组宣称 V2 已独立验证；
3. 把 100 m、0.25 m 写成洞口无关或网格收敛。

当前已有正式阶段二 V1 结论：拟议的统一分段指数/有限源结构在当前设计下被判为
`FAIL`。近场候选仍为 0.3H；峰值复杂修正因 11/12 区间
宽于 0.5H 而降级；d80 在 100 m 域内只保留域删失/平台信息，不输出下游
衰减参数。V2 不再要求每个工况都有双侧衰减参数，故可以按用户授权生成
100 m 阶段三输入，但必须把 d80 类工况标为 `downstream_domain_censored`，
`k_d` 留空，并由封存测试确认条件域模型。该路径不能支持长隧道双侧衰减结论。
