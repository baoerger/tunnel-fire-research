# G2 先导输入准备记录

> 状态：`G2_PILOT_REMAINING_BATCH_EXTERNAL_FDS_RUN_REQUIRED`
> FDS：6.10.1，Revision `FDS-6.10.1-0-g12efa16-release`

## 本地冻结结果

- `config/pilot_cases.csv` 恰含 16 条先导：3 背景、6 网格、3 洞口/平移、4 种子重复。
- 全部输入 `T_END=300 s`、无机械风、两端 OPEN 和固定全局三层测点；粗网格使用 18 MESH，中/细网格使用 21 MESH。
- 5 个代表火灾工况使用 heavy，其余 11 个使用 light；燃烧器均与各自网格精确对齐，实际质心无偏移。
- 16 个输入与 attempt 副本 SHA-256 一致，回传目录均已创建。
- 修复后完整测试：`158 passed`；warning 仅为测试作图字体和既有 G1 5 s 合同测试的预期提示。
- 完整路径、哈希、阶段和回传文件清单见 `reports/g2_pilot_run_handoff.csv`。

## 阶段顺序

| 阶段 | 数量 | 内容 | 放行状态 |
|---|---:|---|---|
| 1 | 3 | 三个背景种子 | 已完成并通过 |
| 2 | 2 | 18/36 MW 中网格 heavy | 已完成并通过 |
| 3 | 8 | 粗/细网格和种子重复 | 外算合批放行；首先分析 |
| 4 | 3 | 27 MW、x=40/50/60 m 平移/洞口 | 外算合批放行；阶段 3 通过后分析 |

## 当前可合批外算的 11 个输入

| CHID | SHA-256 | 用途 |
|---|---|---|
| `p_q018_s40_x50_g20_r104729` | `e979458086d946eb6b3b3f2899451de8c760d8d0eaca2332918bf3dc3beab947` | 18 MW，细网格 |
| `p_q018_s40_x50_g25_r204729` | `13b38494e32fbcb345de9428b8ba17fc1259132cbca8f16b9d02993354e9f272` | 18 MW，种子 2 |
| `p_q018_s40_x50_g25_r304729` | `591ee982681a11eca168c3b8641aa3e31a74f0daf2aedf0554122859e7c63c2e` | 18 MW，种子 3 |
| `p_q018_s40_x50_g50m18_r104729` | `f0bf26eea0d1d88a1367493652d088de75fde76d8b01afcce930760c507a1751` | 18 MW，粗网格，18 MESH 修复版 |
| `p_q036_s40_x50_g20_r104729` | `f5f8699c19df915c6b1d93d159e49acb7baae161514f5dc6442395c91cb6208b` | 36 MW，细网格 |
| `p_q036_s40_x50_g25_r204729` | `032d519e960960fa6dc063b5e89875515cc9e2e31cc82540aaf4289e911a65d8` | 36 MW，种子 2 |
| `p_q036_s40_x50_g25_r304729` | `30e771987ccd0644356ed7cbd30da9de63fc87da2cd9c9cef84ae5bcbac702f4` | 36 MW，种子 3 |
| `p_q036_s40_x50_g50m18_r104729` | `7c376424cb51a5c8d913e675a49eb35780969e6dc654dd9aaeeb6e9b115a7a45` | 36 MW，粗网格，18 MESH 修复版 |
| `p_q027_s45_x40_g25_r104729` | `da615b84905887875b894b66697e53f173894d5a0533b51424c21f82f37e1343` | 27 MW，x=40 m，heavy |
| `p_q027_s45_x50_g25_r104729` | `993997b2b177cbe972292ce1a73bdd903478d1ea5577c849eebd83599da2cd42` | 27 MW，x=50 m，heavy |
| `p_q027_s45_x60_g25_r104729` | `c49c96ae3d23a40e48d87667eba856e10d5c9c9f47b0957fd6ba3de27ea69631` | 27 MW，x=60 m，heavy |

> 原 `g50` 的 21 MESH 输入已作废：中间区每个 z 分区只有 2 层单元，FDS 6.10.1 报
> `ERROR(426) ... Poisson initialization error` 。修复版保持 `dx=0.5 m` 和 40,000 个总单元不变，
> 仅改为 18 个并行分区，已在 FDS 6.10.1 上用 18 MPI 完成 0.5 s 启动试算。

全部回传至少包含同 CHID 的 `.fds`、`.out`、`_devc.csv`、`_hrr.csv`、`.smv` 和可用的 `.end`；阶段 4 三个 heavy 工况还必须保留 `.smv` 引用的 `.sf*` 与 `.bf`。完整输入路径、attempt 目录和运行合同见 `reports/g2_pilot_run_handoff.csv`。

## 阶段 1 完成记录

三个背景工况均已运行至 300 s 并通过质量门，背景 HRR 为零，检测限为 0.5 K。轻量证据见 `reports/g2_stage1_background_analysis.md`、`reports/g2_stage1_background_result_check.csv` 和 `reports/g2_stage1_background_metrics.csv`；原始结果继续保留在 Git 忽略的 `runs/`。

## 阶段 2 完成记录

18/36 MW 中网格 heavy 工况均已运行至 300 s，联合质量门和准稳态判定通过，HRR 闭合误差分别为 0.018% 和 0.105%。轻量证据见 `reports/g2_stage2_analysis.md`、`reports/g2_stage2_result_check.csv`、`reports/g2_stage2_steady_windows.csv` 和 `reports/g2_stage2_metrics.csv`；约 1.60 GiB 原始结果继续保留在 Git 忽略的 `runs/`。

## 当前合批外算范围

2026-08-19 起，阶段 3–4 的剩余 11 条冻结输入获一次性外算授权。完整绝对路径、SHA-256、输出档位和唯一回传目录见 `reports/g2_pilot_run_handoff.csv` 中 `pilot_stage>=3` 的记录。科学分析仍按阶段 3→4 进行，阶段 4 目标科学指标在阶段 3 通过前不得生成或查看。

合批只改变外部提交时机，不改变输入、哈希、门限或停止规则。阶段 3 失败时，已计算的阶段 4 结果标记为 `QUARANTINED_BY_UPSTREAM_GATE`。
