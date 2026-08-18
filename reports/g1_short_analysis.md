# G1 四个 FDS 短试算分析与远端交接

> 分析日期：2026-08-18  
> 闸门结论：`G1_PASS_G2_READY`  
> 正式后续版本：FDS 6.10.1，Revision `FDS-6.10.1-0-g12efa16-release`

## 结论

四个 G1 输入均保持登记 SHA-256 不变，以 21 MPI × 1 OpenMP 完成实跑。全部 `.out` 明确出现 `STOP: FDS completed successfully`，DEVC/HRR 时间序列到达各自 `T_END`，未检出 FDS `ERROR`、`WARNING`、`rejected`、燃烧器拒绝或异常终止。

因此，G1 的解析、时间推进、背景无火、火源非零、设备/单位、两端开放边界、HRR 输出及 heavy 场输出合同通过，可以进入 G2 先导设计与输入准备。该结论只放行软件和运行合同，**不等于无风公式已经拟合完成，也不把 5 s 结果解释为稳态温度物理结论**。

## 工况结果

| 工况 | 自动检查 | G1 决定 | 关键结果 |
|---|---|---|---|
| 背景 0 MW，5 s | `PASS` | 接受 | HRR 全程为 0，正常结束 |
| 18 MW，5 s | `REVIEW` | 按设计接受 | 火源非零；5 s 小于 `3×TAU_Q`，只验证启动 |
| 36 MW，5 s | `REVIEW` | 按设计接受 | 火源非零；5 s 小于 `3×TAU_Q`，只验证启动 |
| 27 MW，40 s | `PASS` | 接受 | 30–40 s 平均 HRR=26.847 MW，相对目标误差 0.566% |

两个 `REVIEW` 不是计算失败，而是自动检查器对短于 `3×TAU_Q` 的火灾试算进行的范围提醒。40 s 工况提供正式 HRR 闭合证据：末时刻 HRR=26.699 MW，末段平均值与 27 MW 目标相差 0.566%，低于协议 10% 上限。

heavy 工况生成 357 个切片文件和 21 个边界文件；`.smv` 共引用 378 个场文件，缺失数为 0。本机 FDS 未生成 `.end`，未人工补造；按照项目完成性合同，成功 `.out` 与到达 `T_END` 的 DEVC/HRR 共同构成可接受的替代完成证据。

## 复核记录

- 使用 `--expected-version 6.10.1` 和协议 10% HRR 容差重新执行结果检查：`PASS=2, REVIEW=2, FAIL=0`。
- `reports/g1_short_result_check.csv` 与本机重新生成的精确版本检查结果逐字段一致。
- 完整软件回归：`153 passed`；仅有中文字体缺字及 5 s 短试算范围提醒，不影响 G1 判定。

## Git 交接边界

- 原始 `runs/` 约 480 MiB，继续由 `.gitignore` 排除，不上传 Git。
- Git 只保存本报告、`g1_short_result_check.csv` 和 `g1_short_runtime_metrics.csv`；后者包含输入/关键输出哈希，可与保留在本机的原始结果复核。
- 远端拉取后应把 G1 视为完成，从 G2 先导设计和输入准备继续；后续正式 FDS 数据统一使用 6.10.1，不得与 6.9.1 混合拟合。
- G2 长算尚未授权。本次只授权远端继续设计、生成、静态审计和准备新的唯一 attempt 目录；到达 FDS 实算点后仍按项目外算交接规则暂停。

## 轻量证据

- 自动质量表：`reports/g1_short_result_check.csv`
- 运行、HRR、场文件计数及 SHA-256：`reports/g1_short_runtime_metrics.csv`
- 原始结果：本机 `runs/pilot/<run_chid>/attempts/<job_attempt_id>/`，不进入 Git
