# G4 开发集接收与不可变数据库

## 结论

27 条开发运行已按 `D1→D2→D3` 全部通过技术和科学接收，对应
19 个物理工况。全部算至 300 s，27/27 找到合格 60 s 同步平均窗；
D1、D2、D3 批次门均为 `PASS`。因用户明确要求忽略版本差异，项目
保留实跑 FDS 6.9.1 与正式基线 6.10.1 的偏差审计，但不再以此阻断 G5。

## 主要数值

- 稳态窗：27/27 通过，不需要延长到 450/600 s。
- 平均窗 HRR 最大闭合误差：`0.177%`。
- 最不利 `A_global` 点估计/95% 上限：`7.80%` / `12.09%`。
- 最不利左右镜像 NRMSE：`6.81%`。
- 有效域内低信号删失比例：最大 `0.00%`。
- 4 组三种子重复的最大峰值 CV/曲线 NRMSE：`3.83%` / `4.48%`。
- 5 个位置父组的最不利平移曲线 NRMSE：`4.65%`。
- D1 重场火源上方近顶棚平均温度最低为 `682.7 °C`，5/5 与冻结的“撞顶/近撞顶”单一层级一致。

## 数据去向

原始结果保留在 Git 忽略的 `runs/development/`；派生数据库位于
`derived/development/`，包含 `case_master/steady_windows/curve_points/case_metrics`
等表。轻量哈希清单见 `reports/g4_development_database_manifest.csv`，可用
`python -m src.analysis.analyze_g4_development` 从原始结果重建。

锁箱仍为 `SEALED_UNREAD_UNRUN`。下一步是 G5 正向候选比较与冻结 A。
