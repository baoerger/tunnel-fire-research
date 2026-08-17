# 阶段四：解析模型与闭合公式

> **历史冻结（2026-08-14）：**本目录的有风非对称输运核不再是当前主体模型。
> 部分约束回归和评价工具可按新复用合同迁移，但旧闭合对象、结果和排名不得沿用。

当前完成无量纲点源核、参数提取、闭合候选、正向统一评价和全场截面积分软件框架。正式参数表、源项选择、闭合变量、公式结构、系数与模型排序仍依赖阶段一至三真实结果，尚未拟合或定稿。

## 当前文件

- `src/dimensionless_model.py`：`xi/phi/Pe_e/Da_e/Pi_S` 统一接口、无量纲上下游核、与有量纲温升转换、双侧/单侧参数批量提取、逐侧拟合窗口稳定性、独立参数掩码和多扰动 Bootstrap。
- `01_无量纲核与参数提取/synthetic_parameter_check.csv`：明确标记为非科研证据的合成恢复检查。
- `01_无量纲核与参数提取/无量纲核与参数提取软件验证报告.md`：数学合同、软件证据和真实数据依赖。
- `tests/test_dimensionless_model.py`：极限、约束、参数恢复、删失与不确定性测试。
- `src/closure_models.py`：softplus 物理约束闭合、四类低复杂度候选、完整工况分组交叉验证、全曲线二阶段微调、工况 Bootstrap 和受限符号表达式检查。
- `02_闭合模型/闭合拟合框架软件验证报告.md`：候选能力、合同测试与不得提前定稿事项。
- `tests/test_closure_models.py`：约束、交叉验证隔离、两阶段损失、Bootstrap 和符号安全测试。
- `src/forward_validation.py`：传统经验式、独立指数、普通回归、直接符号回归和输运核闭合五类模型的固定评价接口、强风删失/无风/远场/留区间指标和独立测试封存。
- `src/empirical_baselines.py`：Kurioka (2003) 与 Li–Lei–Ingason (2011)
  最大顶棚气体温升公式；显式处理原式 kW/kJ 单位、分支和适用域，只输出峰值。
- `src/field_integrals.py`：严格 SI 截面单元 CSV 读取、`C_T`、`J_T`、`U_e^field` 积分和多站点处理。
- `03_正向验证与全场解释/`：明确标记的合成评价/积分表和软件验证报告。
- `tests/test_forward_validation.py`：指标、封存、单位、解析积分和读取合同测试。

## 运行

从项目根目录执行：

```bash
uv run --python 3.12 --with numpy --with matplotlib python 阶段四/src/dimensionless_model.py
uv run --python 3.12 --with numpy --with matplotlib python 阶段四/src/forward_validation.py
uv run --python 3.12 --with numpy --with matplotlib python 阶段四/src/field_integrals.py
uv run --python 3.12 --with numpy --with matplotlib python -m unittest discover -s 阶段四/tests -v
```

100 m 条件域 V2 将删失分为 `upstream_censored`、
`downstream_domain_censored` 和 `no_obvious_backflow`。上游删失只允许稳定的
下游衰减参数，下游域删失只允许稳定的上游衰减参数；缺一侧时不生成
`Pe_e/Da_e/Pi_S`。闭合参数阶段按掩码跳过不可用目标，同时所有质量通过剖面
仍可进入全曲线损失。`insufficient_downstream` 只作为 V1 迁移别名保留。

正式闭合筛选应传入预先固定的 `interval_windows`。每一侧的 `kappa` 分别按
20% 最大相对变化门标记稳定性；窗口点数不足或变化超限时，参数估计值仍保留
用于探索和诊断，但正式闭合训练掩码关闭。任一侧不稳定时双侧组合参数同样不
进入正式闭合目标，峰值、平台、参数估计和全剖面都不删除。

## 等待项

必须在 50 个真实开发工况完成后，才能生成正式逐工况参数表、比较闭合公式、
填入五类真实预测并评价稳定性。当前只有 12/50 个开发结果可用，不能定稿。
全场积分还需正式代表工况切片导出的严格 SI 单元表。独立测试结果在公式和
超参数固定前保持封存。所有结论限于 100 m 条件域。

传统峰值式的书目信息、逐分支公式和证据等级见
`阶段一/01_文献调研/empirical_formula_catalog.csv`。它们不得被扩展成虚构的纵向
温度剖面，也不得与外部试验热电偶响应温度直接比较。
