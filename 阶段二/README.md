# 阶段二：先导结构判伪

阶段目标是用 12 个开发集先导工况判定分段指数远场结构是否值得扩展到 68 组数据库。当前已完成候选设计、临界风速分层、解析结构、删失分类和连续峰值的软件工具。用户于 2026-07-31 固定核心模型为 100 m、0.25 m，并授权 12 个先导与 3 个外部试验复现并行；先导状态为 `CONDITIONAL_PARALLEL_PENDING_EXTERNAL_VALIDATION`。这只授权生成和提交，不代表阶段一验证或阶段二判伪通过。

## 当前文件

- `01_先导工况设计/pilot_design_targets.csv`：12 组设计意图的单一事实源。
- `src/design_pilot_cases.py`：按 Li (2010) 临界风速初估或真实回流覆盖值生成候选表并执行覆盖/功率密度合同；另提供不改变主表的 Wu–Bakar (2000) 水力高度敏感性备选。
- `01_先导工况设计/pilot_cases_candidate.csv`：可定位的当前候选产物。
- `01_先导工况设计/critical_velocity_estimates.csv`：当前 U_c 初估表。
- `01_先导工况设计/临界风速候选公式.md`：公式、适用边界和后续更新纪律。
- `01_先导工况设计/先导设计报告.md`：覆盖检查和阻塞状态。
- `src/transport_model.py`：点源/有限源核、约束拟合、近场分组评价和区间扰动。
- `02_解析结构与参数提取/synthetic_software_check.csv`：明确标记的合成软件验证，不是科研数据。
- `02_解析结构与参数提取/解析结构软件验证报告.md`：当前能力与真实结果依赖。
- `src/censoring_peak.py`：三类上游观测分类、删失高斯损失、连续峰值、平台宽度、时间分块/测点扰动 Bootstrap 和判伪汇总。
- `03_删失与峰值/synthetic_censor_peak_check.csv`：明确标记的删失/峰值合成软件验证，不是科研数据。
- `03_删失与峰值/强风删失与峰值软件验证报告.md`：候选方法、输入校验和真实结果依赖。

## 重新生成与测试

从项目根目录执行：

```bash
uv run --python 3.12 python 阶段二/src/design_pilot_cases.py
uv run --python 3.12 python 阶段一/src/generate_fds_case.py \
  --csv 阶段二/01_先导工况设计/pilot_cases_candidate.csv \
  --outdir 阶段一/outputs/pilot_inputs
uv run --python 3.12 python 阶段二/src/transport_model.py
uv run --python 3.12 python 阶段二/src/censoring_peak.py
uv run --python 3.12 python -m unittest discover -s 阶段二/tests -v
```

真实阶段一/先导回流结果可通过 `--uc-overrides <csv>` 覆盖同一 Q 的经验 U_c。覆盖表必须包含 `Q_MW,Uc_FDS_m_s,source_chid,evidence_path,note`；脚本会把方法和证据路径写入输出，原始估计表不被静默改写。

## 条件性并行与正式化门

当前允许：

1. 按固定的 100 m、0.25 m 生成 12 个先导输入；
2. 与 3 个外部验证官方快照并行提交；
3. `gsA_m/gsB_m/gsC_m` 优先恢复已有 `.out/.end`，若无法恢复则用本批输入重跑，避免把缺日志的 FDS 6.9.1 回传直接当作已通过质量门。

仍然禁止：

1. 在外部验证和先导判伪结果回来前宣称模型有效；
2. 在阶段二五项决策门通过前生成或提交 68 组数据库；
3. 把 100 m、0.25 m 写成洞口无关或网格收敛。

当前没有阶段二科学结论，也没有宣称 U_c、流态、近场范围、解析结构、删失阈值或峰值稳定性已经由真实 FDS 证实。判伪汇总接口对 CSV 布尔文本作严格解析，缺字段和未知值均不会默认通过。
