# G5-SR 开发数据恢复交接

> 状态：`EXTERNAL_INFORMATION_REQUIRED_AND_EXECUTION_SUPPLEMENT_PENDING`
>
> 日期：2026-08-23

G5-SR 已获 `SR_PROTOCOL_APPROVED`，但当前工作区缺少两张由 G4 生成、此前被 Git 忽略的开发派生表。它们不是新的 FDS 计算结果；本阶段不需要新增或重跑 FDS。

现有 G4 记录已足以沿用 27/27 条开发运行的 FDS 技术与科学验收，后续不再重复版本、日志、HRR 闭合、准稳态窗或批次门验证。这 27 条保持为已批准的 FDS 6.9.1 开发证据集，不与新增 6.10.1 结果混合拟合。若从远端恢复原始 `_devc.csv`，只核对运行身份、冻结窗口和文件完整性，并用于生成 G5-SR5 的同步时间块统计输入；这不是重新验收 FDS，也不需要为了复验恢复 `.out`、`_hrr.csv`、`.smv` 或场文件。

请把原文件直接放回以下位置，不要改名、另存或用汇总报告重建：

| 本机相对路径 | 字节数 | 数据行数 | SHA-256 |
|---|---:|---:|---|
| `derived/development/case_metrics.csv` | 15,799 | 27 | `d97f50bc253b9e7cb45e58990f59f741888a7fb26e2746508ae1f664f5acbde1` |
| `derived/development/curve_points.csv` | 301,127 | 1,323 | `e5222d35143945bea27b808a1e95f28fa728815d2c95f21496dfd14acda4ab73` |

也可以恢复原 `derived/development/` 整目录。若两张表已丢失，只需从远端 27 个已接受 attempt 取回各自的 `_devc.csv` 来重建，不要求为了复验回传整个运行目录或其他 FDS 输出；重建结果必须与上表冻结哈希逐字节一致。该动作只恢复数据，不重新裁决 G4/FDS。不要提供、复制或读取 `runs/lockbox/` 或任何锁箱结果。

若要完成 G5-SR5 的同步时间块分层 Bootstrap，还需恢复 27 条已接受开发运行的原始 `_devc.csv`，或在远端从这些原文件生成符合补充协议的 `g5_sr_synchronized_time_block_profiles.csv` 及清单。现有汇总记录能证明 G4 已通过，但不能反推出测点间同步的源时间块。

文件恢复后仍不会立即启动正式 PySR。必须先把 `config/g5_sr_execution_supplement_v1.draft.json` 审批并冻结为正式补充协议，随后让执行器同时核验父协议与补充协议哈希；两表的哈希、字段、运行身份、父组数量和锁箱守卫也全部通过后，才可启动正式搜索。同步时间块输入可晚于搜索表恢复，但必须在 G5-SR5 前通过完整性检查。
