# 无风隧道顶棚纵向温度研究

本仓库已按“人工候选基准 A0 → 物理辅助符号回归 → 最终正向冻结 A_active → 直接反演 → 独立锁箱”的主线重构。项目根目录是唯一研究根，不再使用“阶段一至阶段七”目录组织工作。

当前正式入口：

- 科学方案：[`docs/无风隧道顶棚纵向温度_正向主导双向闭环实施方案_主次定稿版.md`](docs/无风隧道顶棚纵向温度_正向主导双向闭环实施方案_主次定稿版.md)
- 执行方案：[`docs/实施方案.md`](docs/实施方案.md)
- 当前进度：[`docs/当前实施进度.md`](docs/当前实施进度.md)
- G5-SR 批准执行合同：[`docs/无风隧道顶棚温度_物理辅助符号回归公式发现方案_审阅稿.md`](docs/无风隧道顶棚温度_物理辅助符号回归公式发现方案_审阅稿.md)
- G5-SR 执行补充协议：[`docs/technical/G5-SR执行补充协议_待批准.md`](docs/technical/G5-SR执行补充协议_待批准.md)（当前待批准）
- G5-SR 公式发现过程报告：[`reports/g5_sr_formula_discovery.md`](reports/g5_sr_formula_discovery.md)（待实际搜索填充）
- 自动执行 Loop：[`docs/项目自动执行Loop提示词.md`](docs/项目自动执行Loop提示词.md)
- 协议：[`config/protocol_v1.json`](config/protocol_v1.json)（当前为 `G5_SR_EXECUTION_SUPPLEMENT_APPROVAL_AND_INPUT_RESTORE_REQUIRED`）
- G1 实跑结论：[`reports/g1_short_analysis.md`](reports/g1_short_analysis.md)
- G2 先导交接：[`reports/g2_pilot_preparation.md`](reports/g2_pilot_preparation.md)
- G2 阶段 1 结果：[`reports/g2_stage1_background_analysis.md`](reports/g2_stage1_background_analysis.md)
- G2 阶段 2 结果：[`reports/g2_stage2_analysis.md`](reports/g2_stage2_analysis.md)
- FDS 技术规范：[`CLAUDE.md`](CLAUDE.md)

目录职责：

- `src/fds/`：FDS 配置、输入生成、CSV 读取和质量审计的单一事实源。
- `src/analysis/`：网格、准稳态、时间平均、Bootstrap 和温度曲线分析。
- `config/no_wind/`：无风设计草案，以及仅授权 G1 短试算的 `g1_short_cases.csv`。
- `extras/legacy_modeling/`：可参考的旧正向/反演脚手架，不是当前冻结模型。
- `extras/fast_inversion/`：仅在 `A_active` 与冻结 B 均通过、锁箱仍为 `SEALED_UNREAD_UNRUN` 且导师明确要求时激活的 G7 快速反演附加实验；当前状态为 `G7_SKIPPED_NOT_REQUESTED`。
- `fds_inputs/`、`runs/`、`derived/`：可再生输入、逐 attempt 原始结果和派生结果，默认不纳入 Git。

运行全部保留测试：

```powershell
uv sync --locked
uv run --locked python -m pytest -q
```

G1–G4、有限人工候选比较和 G5-SR 方法审阅已经完成。用户于 2026-08-23 正式批准父科学协议 `SR_PROTOCOL_APPROVED`：`M1_S1/A0` 仅作为不可变人工候选基准，G6 暂停，必须先实际运行 G5-SR 并生成唯一最终正向公式冻结 `A_active`。机器协议、PySR/Julia 冒烟验证、490-trial 原始搜索编排和候选审计层已经完成；正式搜索仍须先批准执行补充协议并恢复被 Git 忽略的两张开发集逐点派生表，SR5 另需远端原始 `_devc.csv` 或由其生成的同步时间块表。现有 G4 记录已足以沿用 FDS 验收，不会重跑或重复验证。锁箱状态为 `SEALED_UNREAD_UNRUN`（封存于 G3），快速反演继续保持 `G7_SKIPPED_NOT_REQUESTED`。
