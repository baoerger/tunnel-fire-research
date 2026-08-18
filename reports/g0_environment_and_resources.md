# G0 环境与资源审计

> 状态：`G0_LOCAL_FROZEN`  
> 日期：2026-08-18

> G1 后更新：用户已选择 FDS 6.10.1，Revision `FDS-6.10.1-0-g12efa16-release`，并完成四个 G1 实跑。下表中的 6.9.1 与旧工作树信息是 G0 初始快照；当前事实源以 `config/protocol_v1.json` 和 `reports/g1_short_analysis.md` 为准。

## 已确认

| 项目 | 结果 |
|---|---|
| 项目根目录 | `C:\Users\xiao.cheng\Desktop\科研` |
| Git HEAD | `50cad6d09b202eed9bf0cf2e486a89fd1396dc47` |
| 工作树 | 用户授权的仓库重构尚未提交；本轮未清理或覆盖这些改动 |
| Python | 3.12.13 |
| uv | 0.11.25 |
| G0 软件测试 | core 34/34、analysis 11/11，通过 |
| 本机 FDS/MPI | 未发现；遵循外部服务器专用执行约定 |
| G0 初始拟选版本 | FDS 6.9.1；G1 前由用户改选 6.10.1，现已实跑核验 |
| G7 | `G7_SKIPPED_NOT_REQUESTED` |

完整软件测试记录见 `reports/g0_test_baseline.txt`。这些结果不构成 FDS 物理证据。

## 资源合同

| 项目 | 冻结上限/公式 | 当前值 |
|---|---|---|
| 最终接受科学轨迹 | 最多 61 条 `run_chid` | 已写入协议草案 |
| FDS 进程启动 | 最多 79 个 `job_attempt_id` | 已写入协议草案 |
| 核时硬上限 | G3 科学预算的 1.35 倍 | 上限已冻结；若回传 60 s 基准再换算 |
| 估算余量 | `Σ(wall-clock×核数×T_END/60)×1.25` | 等用户可用时回传基准 |
| 存储估算 | `单工况输出×计划次数×1.25` | 等用户可用时回传基准 |
| 每节点并发 | 不超过核心、内存和磁盘带宽约束 | 用户自行管理，不是本地协议输入 |

## 后续外部证据

代理不再索取服务器配置。G1 四个工况已在本机完成，实际版本、wall-clock 和输出量已汇总至 `reports/g1_short_runtime_metrics.csv`。后续外算仍只交付 `.fds`、哈希和唯一回传目录；缺少服务器运维信息不阻止输入准备。

当前 `formal_run_authorized=false` 表示 G2 正式科学长算尚未放行；`g2_pilot_preparation_authorized=true`，允许远端继续先导设计、输入生成、静态审计和交接准备。
