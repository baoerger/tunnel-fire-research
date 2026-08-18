# G2 先导输入准备记录

> 状态：`G2_PILOT_STAGE1_EXTERNAL_FDS_RUN_REQUIRED`
> FDS：6.10.1，Revision `FDS-6.10.1-0-g12efa16-release`

## 本地冻结结果

- `config/pilot_cases.csv` 恰含 16 条先导：3 背景、6 网格、3 洞口/平移、4 种子重复。
- 全部输入 `T_END=300 s`、无机械风、两端 OPEN、固定全局三层测点和 21 MESH 镜像拓扑。
- 5 个代表火灾工况使用 heavy，其余 11 个使用 light；燃烧器均与各自网格精确对齐，实际质心无偏移。
- 16 个输入与 attempt 副本 SHA-256 一致，回传目录均已创建。
- 完整测试：`157 passed`，另有 42 个 subtests；唯一 warning 为既有 G1 5 s 合同测试的预期提示。
- 完整路径、哈希、阶段和回传文件清单见 `reports/g2_pilot_run_handoff.csv`。

## 阶段顺序

| 阶段 | 数量 | 内容 | 放行状态 |
|---|---:|---|---|
| 1 | 3 | 三个背景种子 | 当前放行 |
| 2 | 2 | 18/36 MW 中网格 heavy | 等阶段 1 通过 |
| 3 | 8 | 粗/细网格和种子重复 | 等阶段 2 通过 |
| 4 | 3 | 27 MW、x=40/50/60 m 平移/洞口 | 等阶段 3 通过 |

## 当前需外算的三个输入

| CHID | SHA-256 | 本机输入 | 本机回传目录 |
|---|---|---|---|
| `p_bg_g25_r104729` | `73e46d1c8e6dd5345d4b5f572c8b1959507d2f7e03d59172514e820dcf527532` | `fds_inputs/pilot/g2/p_bg_g25_r104729.fds` | `runs/pilot/p_bg_g25_r104729/attempts/p_bg_g25_r104729_a01` |
| `p_bg_g25_r204729` | `9c7ee73e763719f5b7311ac3ee4c6b6d71418861af2ee7d2f859cc80bac6a281` | `fds_inputs/pilot/g2/p_bg_g25_r204729.fds` | `runs/pilot/p_bg_g25_r204729/attempts/p_bg_g25_r204729_a01` |
| `p_bg_g25_r304729` | `71a778ce07cf48db6311f9b0ee63f8d4a2103cab058f3a194ad2a60540aafcf3` | `fds_inputs/pilot/g2/p_bg_g25_r304729.fds` | `runs/pilot/p_bg_g25_r304729/attempts/p_bg_g25_r304729_a01` |

背景回传至少包含同 CHID 的 `.fds`、`.out`、`_devc.csv`、`.smv` 和可用的 `.end`；若 FDS 生成 `_hrr.csv` 也一并保留。阶段 1 只建立背景偏流、数值温度地板和输出完整性证据，不形成稳态火灾或公式结论。
