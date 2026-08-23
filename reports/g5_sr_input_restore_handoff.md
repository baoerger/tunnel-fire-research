# G5-SR 开发数据恢复完成记录

> 状态：`COMPLETE_READY_FOR_G5_SR_SEARCH`
>
> 日期：2026-08-23

两张由 G4 生成的冻结开发表已经恢复并通过完整预检：

| 本机相对路径 | 字节数 | 数据行数 | SHA-256 |
|---|---:|---:|---|
| `derived/development/case_metrics.csv` | 15,799 | 27 | `d97f50bc253b9e7cb45e58990f59f741888a7fb26e2746508ae1f664f5acbde1` |
| `derived/development/curve_points.csv` | 301,127 | 1,323 | `e5222d35143945bea27b808a1e95f28fa728815d2c95f21496dfd14acda4ab73` |

执行补充协议 v1 也已批准。27 条已接受开发运行的原始 `_devc.csv` 已生成符合批准模式的 `derived/development/g5_sr_synchronized_time_block_profiles.csv` 及来源清单：27 个运行、1,633 个完整循环同步时间块、80,017 行。该表不是 CI 摘要或抽稀数据；所有移动块均以步长 1 保留，同一时间块对全部空间点使用相同源索引。

完整预检已返回 `READY_FOR_G5_SR_SEARCH`。原始 `runs/development/` 继续被 Git 忽略；本次只上传两张冻结开发表、同步时间块表和轻量来源清单。现有 G4 的 27/27 FDS 接收结论直接沿用，没有重跑或重复验证 FDS。锁箱未生成、未运行、未读取。
