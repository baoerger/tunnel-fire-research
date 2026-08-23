# G5-SR 符号回归公式发现过程

> 报告状态：`INITIAL_SEARCH_AUDITED_SELECTION_NOT_EVALUABLE`
>
> 协议状态：`SR_PROTOCOL_APPROVED`（2026-08-23）
>
> 执行补充协议：`G5_SR_EXECUTION_SUPPLEMENT_APPROVED`；父协议 SHA-256 为 `453c6a9dbc6ba19d096c9ae6dfb6836b82df3f4ef8501b17a41e60f12dc21a8b`，补充协议 SHA-256 为 `c3fe3963d6eaedd1cc3fbb65c73a0bcf57cb260fd2f6fa9c40d3371f40640a55`。
>
> 重要说明：本文是论文独立结果章节的持续填充稿。初始搜索与候选物理审计结果已经写入；所有仍标记为 `TBD_AFTER_FORMAL_RUN` 的内容必须等待协议阻断解除后的唯一选择、验证与联合重拟合，不得人工猜测或从锁箱补全。
>
> 当前模型身份：`M1_S1/A0` 是不可变人工候选基准。初始 490 个正式 trial 已全部成功，候选结构与连续域物理审计已完成；但 `shape_xi_only` 外层折 7 无合格候选，补充协议要求的九折唯一选择不可评价。最终正向公式冻结 `A_active` 尚未生成；锁箱保持 `SEALED_UNREAD_UNRUN`，G6 暂停，快速反演保持 `G7_SKIPPED_NOT_REQUESTED`。

## 1. 协议、数据与运行审计

正式填充时记录：

| 项目 | 值 |
|---|---|
| SR 机器协议及 SHA-256 | `config/symbolic_regression_v1.json`；`453c6a9dbc6ba19d096c9ae6dfb6836b82df3f4ef8501b17a41e60f12dc21a8b` |
| 开发数据清单及 SHA-256 | `TBD_AFTER_FORMAL_RUN` |
| 外层 `parent_case_id` 折定义 | `TBD_AFTER_FORMAL_RUN` |
| PySR/SymbolicRegression.jl 版本与环境 | PySR 1.5.10；Julia 1.12.7；SymbolicRegression.jl 1.11.3 |
| AI Feynman 2.0 审计版本与环境 | `TBD_AFTER_FORMAL_RUN` |
| 峰值/形状正式随机种子 | `TBD_AFTER_FORMAL_RUN` |
| 搜索预算、停止条件与实际消耗 | 初始预算 490 trial；实际完成峰值 245 + `shape_xi_only` 245 |
| 搜索失败、超时、无可行式计数 | 490 `SUCCESS`；0 `FAILED/TIMEOUT/FAILED_NO_CANDIDATES` |
| 锁箱状态 | `SEALED_UNREAD_UNRUN` |

当前已知的比较基准仅为人工候选 `A0=M1_S1`。其既有折外峰值相对误差中位数/90% 分位数为 4.34%/11.21%，曲线 NRMSE 中位数/90% 分位数为 3.27%/4.48%。这些数值不是符号回归结果，只用于统一评价口径下的基准比较。

## 2. 误差—复杂度 Pareto 前沿

峰值任务 `F_T(q,d)` 与形状任务 `Theta(xi,q,d)` 必须分别报告 Pareto 前沿。图和表均使用规范化后的表达式复杂度以及统一常数复估后的父组相关损失；不得使用各搜索引擎不可比的内部得分替代。

### 2.1 峰值任务

初始搜索得到 1,626 个峰值 Pareto 候选；结构、数值与连续域审计后 1,148 个可进入后续选择/重拟合。最终选择与折外指标尚未形成。

必须给出：全部可行 Pareto 点、复杂度、自由常数数、训练折损失、外层折外指标、物理门状态、生成折和随机种子，并在图中标出人工候选基准 `A0`。

### 2.2 形状任务

`shape_xi_only` 初始搜索得到 1,120 个 Pareto 候选；审计后仅 33 个可进入后续选择/重拟合。它们覆盖 8/9 个外层折和 3/20 个全量种子，外层折 7 无合格候选，因此尚不能形成协议要求的九折唯一选择。

必须给出：全部可行 Pareto 点、复杂度、自由常数数、训练折损失、外层折外指标、物理门状态、生成折和随机种子，并区分只含 `xi` 与额外含 `q/d` 的结构。

## 3. 候选结构族与表达式等价

当前审计结果：2,746 个候选中 1,181 个通过结构、固定数值审计和连续域证明。明细见 `reports/g5_sr_candidate_families.csv` 与 `reports/g5_sr_physics_rejections.csv`。由于外层唯一选择不可评价，本节不得把原始 Pareto 出现次数冒充跨折稳定性，也不得提前填入最终结构族。

| 结构族 ID | 规范化代表式 | 任务 | 核心变量 | 复杂度 | 自由常数 | 等价类型 | 来源折/种子 | 状态 |
|---|---|---|---|---:|---:|---|---|---|
| `TBD` | `TBD_AFTER_FORMAL_RUN` | `peak/shape` | `TBD` | `TBD` | `TBD` | `algebraic/reference-scale/predictive` | `TBD` | `TBD` |

必须分别报告代数等价、参考尺度等价和当前域内预测等价；数值接近不能写成代数重发现。复杂度在规范化后重算，原始表达式与化简表达式均保留在机器结果中。

## 4. 变量选择频率

当前只完成原始 Pareto 候选中的诊断性变量频率统计；尚未形成九个外层折和 20 个全量种子的正式唯一选式，因此不得把“曾在候选池出现”冒充稳定性命中。峰值任务 20/20 个全量种子均存在合格候选，`shape_xi_only` 仅为 3/20。

变量频率按峰值和形状任务分开，并至少给出两种分母：

1. 9 个外层折最终结构中的出现次数；
2. 20 个全开发集正式搜索种子中的出现次数。

| 任务 | 变量/变量组 | 外层折频率（/9） | 全量种子频率（/20） | 方向稳定性 | 备注 |
|---|---|---:|---:|---|---|
| `TBD` | `q/d/xi/interaction` | `TBD` | `TBD` | `TBD` | `TBD` |

搜索失败、超时和无可行式必须计入分母；不得只统计成功搜索。还应报告核心变量联合支持，例如 `{q,d}` 或 `{xi,q}`，不能只给单变量边际频率。

## 5. 跨折稳定性

当前状态：`SHAPE_EXPANSION_DECISION_NOT_EVALUABLE`。`shape_xi_only` 外层折 7 没有任何 `freeze_eligible=True` 候选；机器证据见 `reports/g5_sr_selection_readiness.json`。20 个全量种子中只有 3 个存在合格形状候选，因此现有结果也不能达到 16/20 核心变量稳定门。新版执行授权前不得生成扩展决策、运行条件 campaign 或冻结公式。

| 外层折 | 留出父组 | 峰值结构族 | 形状结构族 | 常数复估状态 | 峰值误差 | 曲线 NRMSE | 最不利约束裕量 | 结论 |
|---:|---|---|---|---|---:|---:|---:|---|
| `TBD` | `TBD_AFTER_FORMAL_RUN` | `TBD` | `TBD` | `TBD` | `TBD` | `TBD` | `TBD` | `TBD` |

本节必须汇总同一等价结构族是否达到 7/9 外层折门、核心变量是否达到 16/20 全量种子门、自由指数或尺寸效应是否因删除父组而反号，以及最不利父组是否恶化。结构频率不足但预测稳定时，只能报告“多公式预测等价”。

## 6. 物理约束淘汰

结果：`TBD_AFTER_FORMAL_RUN`

| 淘汰阶段 | 检查项 | 被检候选数 | 淘汰数 | 首要淘汰原因 | 最小通过裕量 | 证据文件 |
|---|---|---:|---:|---|---:|---|
| 符号检查 | 量纲、显式分母、对数、非整数幂 | `TBD` | `TBD` | `TBD` | `TBD` | `TBD` |
| 连续域检查 | 有限性、正值、峰值归一、单调、功率趋势 | `TBD` | `TBD` | `TBD` | `TBD` | `TBD` |
| 固定数值审计 | Sobol 点、边界、角点、`xi=0` | `TBD` | `TBD` | `TBD` | `TBD` | `TBD` |

应同时保存“第一淘汰原因”和全部失败标签，避免同一候选多重失败造成统计口径不清。不能用有限采样未见异常替代连续域证明；无法证明者标记 `DOMAIN_NOT_CERTIFIED` 并淘汰。

## 7. 相对 A0 的选择与最终联合重拟合

结果：`TBD_AFTER_FORMAL_RUN`

| 项目 | 人工候选基准 A0 | 最佳 SR 候选 | 差值/门限 | 判定 |
|---|---:|---:|---:|---|
| 规范化复杂度 | `TBD` | `TBD` | `TBD` | `TBD` |
| 平均父组相关 NLL | `TBD` | `TBD` | 改善至少 10% 或按非劣门 | `TBD` |
| 峰值误差中位数/90% 分位 | `TBD` | `TBD` | 见批准合同 | `TBD` |
| 曲线 NRMSE 中位数/90% 分位 | `TBD` | `TBD` | 改善至少 2 个百分点或按非劣门 | `TBD` |
| 改善父组数 | `TBD` | `TBD` | 至少 7/9 | `TBD` |
| 最不利父组 | `TBD` | `TBD` | 不明显恶化 | `TBD` |
| 稳定性与物理门 | `TBD` | `TBD` | 全部通过 | `TBD` |

选定数学结构后，必须回到全部开发工况的原始温升和真实全局坐标完成联合重拟合。本节随后填入最终显式公式、全精度常数、分层 Bootstrap 区间、空间协方差与误差地板、删失处理、支持域、预测区间和系统残差结果。全开发集联合重拟合只确定最终系数，不能冒充折外性能。

## 8. A_active 冻结结论

当前状态：`NOT_CREATED_BEFORE_G5_SR_COMPLETION`

正式运行后只能填写以下一种分支：

- `SR_SELECTED` 或 `SR_REDISCOVERED`：封存 `A_SR`，并生成 `A_active(source=A_SR)`；
- `SR_NO_REPLACEMENT`：保留人工候选基准 `A0`，完成规定的联合重拟合/一致性审计，并生成 `A_active(source=A0)`；
- `FORWARD_CLAIM_REJECTED`：不生成可供 G6 使用的 `A_active`，停止反演主张。

最终判定、`A_active` 路径、SHA-256、公式、参数、适用域和下一步：`TBD_AFTER_FORMAL_RUN`。

## 9. 论文图表映射

| 论文内容 | 预期证据 | 状态 |
|---|---|---|
| 图：峰值/形状 Pareto 前沿 | 完整候选前沿与 A0 标记 | `TBD_AFTER_FORMAL_RUN` |
| 表：候选结构族 | 规范化表达式、复杂度和等价类型 | `TBD_AFTER_FORMAL_RUN` |
| 图/表：变量选择频率 | 9 折与 20 种子频率，失败计入分母 | `TBD_AFTER_FORMAL_RUN` |
| 图：跨折稳定性 | 结构族×外层折矩阵及最不利父组 | `TBD_AFTER_FORMAL_RUN` |
| 表：物理约束淘汰 | 分阶段数量、原因和最小裕量 | `TBD_AFTER_FORMAL_RUN` |
| 表：最终联合重拟合 | 公式、常数、区间、误差模型和 A_active 来源 | `TBD_AFTER_FORMAL_RUN` |

本章必须作为独立论文结果章节保留，即使最终结论为 `SR_NO_REPLACEMENT`；“没有稳定替代人工候选基准”本身也是受控搜索结果。
