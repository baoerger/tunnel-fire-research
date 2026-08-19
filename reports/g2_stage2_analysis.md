# G2 阶段 2 中网格长算分析

> 完成日期：2026-08-19
>
> 阶段结论：`G2_STAGE2_PASS`
>
> 当时下一状态：`G2_PILOT_STAGE3_EXTERNAL_FDS_RUN_REQUIRED`
>
> 2026-08-19 调度补充后当前状态：`G2_PILOT_REMAINING_BATCH_EXTERNAL_FDS_RUN_REQUIRED`

## 结论

18 MW 和 36 MW 两个 `dx=0.25 m`、`T_END=300 s` 的 heavy 工况均使用 FDS 6.10.1 精确 Revision `FDS-6.10.1-0-g12efa16-release`、21 MPI × 1 OpenMP 正常完成。联合质量检查为 `PASS=2, REVIEW=0, FAIL=0`，因此 G2 阶段 2 通过。原记录放行阶段 3 的 8 个网格与随机种子工况；2026-08-19 调度补充允许阶段 3–4 的剩余 11 条一次外算，但科学分析仍严格按 3→4 进行。

本结论只说明两个功率端点可以稳定长算，HRR、设备时序和 heavy 输出合同有效；它不代表网格、种子不确定性、测高、有效域或最终公式已经冻结。

## 关键结果

| CHID | HRR 末段均值 | 闭合误差 | 准稳态时均窗 | wall-clock | 原始输出 |
|---|---:|---:|---:|---:|---:|
| `p_q018_s40_x50_g25_r104729` | 17.9968 MW | 0.018% | 167.0–197.0 s | 5,950.949 s | 818,675,928 B |
| `p_q036_s40_x50_g25_r104729` | 35.9624 MW | 0.105% | 133.0–163.0 s | 6,880.171 s | 903,121,601 B |

- 两项输入 SHA-256 均与预注册交接清单完全一致。
- 两项 `.out` 均含 `STOP: FDS completed successfully`，DEVC 与 HRR 时间轴均到 300 s。
- 两项均无 `ERROR`、`WARNING` 或 rejected 记录。
- 每项均保留 357 个切片文件、21 个边界文件、63 个 `.s3d` 和 21 个 `.s3dd`；`.smv` 引用的 378 个切片/边界文件缺失数为 0。
- FDS 未生成 `.end`，但项目合同允许以成功结束的 `.out` 和到达 `T_END` 的 CSV 作为完成证据。
- 焓流积分判据为 `UNCHECKED_NO_FIELD_DATA`：二进制场文件已经完整保留，但尚未派生截面积分表；该项不阻断本阶段运行质量门。

## 数据保留与交接

约 1.60 GiB 原始结果保留在 Git 忽略的 `runs/pilot/<CHID>/attempts/<CHID>_a01/`。Git 仅保存本报告、质量表、准稳态窗口和包含关键输出哈希的轻量指标表，远端拉取后可直接依据 `reports/g2_pilot_run_handoff.csv` 继续阶段 3。
