# 无风隧道顶棚纵向温度研究

> Research code for auditable FDS 6.10.1 simulations, result checks, and temperature-profile analysis in a no-wind tunnel setting.

这是一个面向可复现实验和证据审计的研究型仓库。项目把 FDS 输入生成、版本与日志检查、HRR/温度输出读取、准稳态窗口识别、统计分析和测试放在同一条可追溯链路中。仓库当前公开的是研究代码、协议和阶段性报告；尚未把未完成的外算结果包装成最终科学结论。

## 研究主线

`正向公式重发现 → 直接反演 → 独立锁箱验证`

所有可重复的输入从配置和生成器产生，结果经过 FDS 版本、输入完整性、日志警告、火源 HRR、设备 CSV 和统计窗口检查。项目对外展示的重点是方法和审计链路，而不是用单个示例数字替代完整研究结论。

本仓库已按“正向公式重发现 → 直接反演 → 独立锁箱”的主线重构。项目根目录是唯一研究根，不再使用“阶段一至阶段七”目录组织工作。

当前正式入口：

- 科学方案：[`docs/无风隧道顶棚纵向温度_正向主导双向闭环实施方案_主次定稿版.md`](docs/无风隧道顶棚纵向温度_正向主导双向闭环实施方案_主次定稿版.md)
- 执行方案：[`docs/实施方案.md`](docs/实施方案.md)
- 当前进度：[`docs/当前实施进度.md`](docs/当前实施进度.md)
- 自动执行 Loop：[`docs/项目自动执行Loop提示词.md`](docs/项目自动执行Loop提示词.md)
- 协议：[`config/protocol_v1.json`](config/protocol_v1.json)（当前为 `G2_PILOT_REMAINING_BATCH_EXTERNAL_FDS_RUN_REQUIRED`）
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
- `extras/fast_inversion/`：导师要求时才激活的 G7 快速反演附加实验；当前状态为 `G7_SKIPPED_NOT_REQUESTED`。
- `fds_inputs/`、`runs/`、`derived/`：可再生输入、逐 attempt 原始结果和派生结果，默认不纳入 Git。

运行全部保留测试：

```powershell
uv run --with pytest --with numpy python -m pytest -q
```

G1 与 G2 阶段 1–2 已通过，三个背景长算将检测限确定为 0.5 K，18/36 MW 中网格 heavy 长算的 HRR、准稳态和完整场输出均通过。当前允许把阶段 3–4 的剩余 11 条冻结输入一次性外算；本地仍按阶段 3→4 审计，阶段 4 科学结果在阶段 3 通过前保持隔离。尚未形成网格、测高、有效域或公式结论。
