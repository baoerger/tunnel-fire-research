# 阶段二：先导结构判伪

阶段目标是用 12 个开发集先导工况判定分段指数远场结构是否值得扩展到 68 组数据库。当前已完成候选设计、临界风速分层、解析结构、删失分类和连续峰值的软件工具；生产网格、隧道长度和阶段一外部验证尚未通过，因此所有先导均为 `WAITING_STAGE1_GATE`，不得生成或提交正式 FDS 输入。

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
uv run --python 3.12 python 阶段二/src/transport_model.py
uv run --python 3.12 python 阶段二/src/censoring_peak.py
uv run --python 3.12 python -m unittest discover -s 阶段二/tests -v
```

真实阶段一/先导回流结果可通过 `--uc-overrides <csv>` 覆盖同一 Q 的经验 U_c。覆盖表必须包含 `Q_MW,Uc_FDS_m_s,source_chid,evidence_path,note`；脚本会把方法和证据路径写入输出，原始估计表不被静默改写。

## 正式化门

只有阶段一网格、长度、外部验证、资源和温度定义全部通过后，才允许：

1. 用冻结的生产网格/长度更新候选表；
2. 由阶段一生成器生成 12 个正式 `.fds`；
3. 提交外部计算并运行结构判伪。

当前没有阶段二科学结论，也没有宣称 U_c、流态、近场范围、解析结构、删失阈值或峰值稳定性已经由真实 FDS 证实。判伪汇总接口对 CSV 布尔文本作严格解析，缺字段和未知值均不会默认通过。
