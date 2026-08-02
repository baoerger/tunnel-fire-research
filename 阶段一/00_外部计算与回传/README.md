# FDS 结果回传与轻量检查

本项目采用精简流程：外部平台计算完成后，把每个工况的整个结果文件夹原样复制回来，不使用批次冻结、SHA256、自动导入或运行清单。

## 1. 文件夹约定

外部平台每个工况使用独立目录：

```text
/project/fds_tunnel/runs/<chid>/
```

计算完成后，将整个 `<chid>` 文件夹复制到：

```text
C:\Users\xiao.cheng\Desktop\科研\阶段一\outputs\runs\<chid>\
```

文件夹名必须与 `.fds` 中的 CHID 相同，不要再增加一层同名目录。

## 2. 最低保留文件

每个正式工况至少保留：

```text
<chid>.fds          # 外部平台实际执行的输入
<chid>.out          # FDS 日志
<chid>_devc.csv     # 温度、速度、HRR_tot、热通量
<chid>_hrr.csv      # HRR、Q_RADI
<chid>.end          # 建议保留；缺少时可用 CSV 最终时间辅助判断
```

网格、长度、外部验证和需要全场解释的代表工况，另保留：

```text
<chid>.smv
<chid>*.sf* 或 <chid>*.s3d*
<chid>*.bf
```

`_cpu.csv`、`_steps.csv` 和版本文本可以一并保留，但不作为轻量检查的硬性要求。

## 3. 轻量检查

把文件夹复制完成后，在 `阶段一` 目录执行：

```bash
python src/check_fds_results.py
```

只检查部分工况：

```bash
python src/check_fds_results.py --chids gsA_c gsA_m gsA_f
```

如果需要只记录版本而不与当前 6.9.1 证据基线比较：

```bash
# 记录版本但不与目标版本比较
python src/check_fds_results.py --expected-version any
```

输出位置：

```text
C:\Users\xiao.cheng\Desktop\科研\阶段一\outputs\analysis\quality\result_check.csv
```

检查内容：

- 最低文件是否齐全，文件夹名与 CHID 是否一致；
- `.out` 中的 FDS 版本、`ERROR`、`WARNING`、`rejected` 和异常终止；
- 是否存在 BURNER VENT 重叠或被拒绝；
- `_devc.csv`、`_hrr.csv` 是否计算到 `T_END`；
- 时间是否倒序、重复、缺行或含 NaN；
- 主体工况是否具有 `T_*`、`U_*`、`HRR_tot`、`Qw_*`、`HRR` 和 `Q_RADI`；外部验证工况是否具有 `measurement_mapping.csv` 指定的全部热电偶通道、`HRR/Q_RADI` 和必需全场文件；
- HRR 是否非零；
- 约 `3×TAU_Q` 后的末段 HRR 是否接近输入燃烧器目标功率；
- 是否存在可供全场分析的 Smokeview、切片和边界文件。

## 4. 状态解释

- `PASS`：没有自动发现问题，可以进入后处理；
- `REVIEW`：结果不自动判废，但需要人工查看，例如存在 WARNING、版本不同、无法证明正常结束或 HRR 功率偏差较大；
- `FAIL`：缺少关键文件、计算未到 T_END、存在 ERROR/rejected、火源为零或关键 CSV 无效，不能直接进入分析。

当前正式证据基线为 FDS 6.9.1。版本不同不会自动判为失败；若以后混用其他
版本，应先用代表工况比较 HRR、温度峰值、峰值位置和衰减参数。缺少 `.end`
时，`.out` 正常结束且 DEVC/HRR 均达到 `T_END` 可作为完整替代证据。

## 5. 22 MESH 专用短试算与并行基准

生成专用短试算输入（60 s，只验证启动链路）：

```bash
python3 src/generate_fds_case.py \
  --csv 00_外部计算与回传/smoke_22mesh_cases.csv \
  --outdir outputs/inputs
```

- 本机输入：`C:\Users\xiao.cheng\Desktop\科研\阶段一\outputs\inputs\smoke_22mesh.fds`
- 服务器输入/工作目录：`/project/fds_tunnel/runs/smoke_22mesh/`
- 回传目录：`C:\Users\xiao.cheng\Desktop\科研\阶段一\outputs\runs\smoke_22mesh\`
- 至少回传：`smoke_22mesh.fds`、`.out`、`_devc.csv`、`_hrr.csv`；建议同时回传 `.end`、`.smv`、切片、边界和 `_cpu.csv`。

用同一输入依次比较 `22 MPI×2 OpenMP`、`22 MPI×1 OpenMP`、`11 MPI×3 OpenMP`，结果填入 `parallel_benchmark.csv`。MPI 任务数不得超过输入的 22 个 MESH；40 核分配不得使用 22×2。Slurm 模板默认申请 22×2，可用环境变量覆盖运行步：

```bash
sbatch --export=ALL,CASES_CSV="$PWD/00_外部计算与回传/smoke_22mesh_cases.csv",CASES_DIR="$PWD/outputs/inputs",RUNS_DIR="$PWD/outputs/runs",FDS_NTASKS=22,FDS_OMP_THREADS=2 src/run_slurm_template.sh
```

短试算必须检查：本批声明版本（正式证据默认 FDS 6.9.1）、第一条 ERROR、全部 WARNING/rejected、22 MESH 通信与负载、时间推进、`HRR_tot>0`、入口 +x、DEVC 关键列、`_hrr.csv HRR/Q_RADI` 和结束时间。60 s 虽超过约 `3×TAU_Q`，仍不作为准稳态、网格收敛、洞口或外部验证结论。

## 6. 外部验证官方快照提交

本机生成文件：

```text
C:\Users\xiao.cheng\Desktop\科研\阶段一\outputs\external_inputs\Arup_Tunnel\Arup_Tunnel.fds
C:\Users\xiao.cheng\Desktop\科研\阶段一\outputs\external_inputs\CSTB_Tunnel_Test_2\CSTB_Tunnel_Test_2.fds
C:\Users\xiao.cheng\Desktop\科研\阶段一\outputs\external_inputs\IFAB-07\IFAB-07.fds
C:\Users\xiao.cheng\Desktop\科研\阶段一\outputs\external_inputs\IFAB-07\mesh.txt
C:\Users\xiao.cheng\Desktop\科研\阶段一\outputs\external_inputs\IFAB-07\output.txt
```

将每个目录的全部文件上传到同名服务器工作目录。初始 MPI 数不超过静态 MESH 数：Arup 5、CSTB 12、IFAB 12；最终配置仍以目标平台短测为准。

```bash
cd /project/fds_tunnel/runs/Arup_Tunnel && mpiexec -np 5 fds Arup_Tunnel.fds
cd /project/fds_tunnel/runs/CSTB_Tunnel_Test_2 && mpiexec -np 12 fds CSTB_Tunnel_Test_2.fds
cd /project/fds_tunnel/runs/IFAB-07 && mpiexec -np 12 fds IFAB-07.fds
```

关键输出位于 `/project/fds_tunnel/runs/<chid>/`。Arup/CSTB 使用逻辑 CHID
前缀；IFAB 的 CATF 实际执行输入和输出前缀通常为 `IFAB-07_cat`，必须把
逻辑输入、CATF 生成输入和实际前缀的全部输出一起保留。至少包括 `.out`、
`_devc.csv`、`_hrr.csv`、`.smv` 和切片/三维场；只有输入请求 BNDF 时才要求
对应 `.bf`。计算完成后把整个目录回传到：

```text
C:\Users\xiao.cheng\Desktop\科研\阶段一\outputs\runs\<chid>\
```

外部官方输入使用自己的热电偶 ID，不要求主体模型的 `T_*/U_*/HRR_tot/Qw_*`。检查器会从 `05_外部试验复现/measurement_mapping.csv` 读取应有通道，并强制要求 `HRR/Q_RADI`、非零火源、完整时长及 `.smv`、切片/三维场；输入声明 BNDF 时再要求 `.bf`。主体工况原判据保持不变。

## 7. 与外部验证并行的 12 个先导

用户于 2026-07-31 授权按 100 m、0.25 m、22 MESH 生成并提交阶段二先导：

```bash
uv run --python 3.12 python 阶段二/src/design_pilot_cases.py
uv run --python 3.12 python 阶段一/src/generate_fds_case.py \
  --csv 阶段二/01_先导工况设计/pilot_cases_candidate.csv \
  --outdir 阶段一/outputs/pilot_inputs
```

本机输入目录：

```text
C:\Users\xiao.cheng\Desktop\科研\阶段一\outputs\pilot_inputs\
```

服务器每个 CHID 仍使用独立 `/project/fds_tunnel/runs/<chid>/` 工作目录，初始
MPI 数不得超过 22。`gsA_m/gsB_m/gsC_m` 与网格批次同名；当前 6.9.1
回传的 `.out`、DEVC/HRR 和全场文件已经通过质量检查，不因缺 `.end` 重跑。

若服务器保留本项目目录结构，可从项目根目录批量运行（下面并发数为 1，避免
单节点同时启动多个 22-MESH 工况；跨节点并发应由调度器数组控制）：

```bash
cd /project/fds_tunnel/project
bash 阶段一/src/run_batch.sh \
  阶段二/01_先导工况设计/pilot_cases_candidate.csv \
  阶段一/outputs/pilot_inputs 22 1 阶段一/outputs/runs
```

输入对应 `/project/fds_tunnel/project/阶段一/outputs/pilot_inputs/<chid>.fds`，
输出对应 `/project/fds_tunnel/project/阶段一/outputs/runs/<chid>/`。每组至少下载
实际 `.fds`、`.out`、`_devc.csv`、`_hrr.csv`；建议保留 `.end`、`.smv`、温度/
速度/密度切片和 `.bf`，以支持近场、回流和结构残差复核。

本节记录 2026-07-31 的协议 V1 提交纪律：先导与外部验证可以同时计算，但当时
不得同时启动 68 组数据库；只有三组外部验证通过质量/精度检查且 12 个先导
满足 V1 结构判伪门后，才可沿 V1 生成数据库输入。2026-08-02 用户固定 100 m
后启用的条件域 V2 是独立替代路径，见本文件末节，不改写这段历史门状态。

## 8. 2026-08-02 当前外部状态

用户已决定以现有 FDS 6.9.1 作为正式证据基线。3 个外部验证和 12 个先导
均重新检查为 `PASS`，无需版本桥接，也不得仅因缺 `.end` 重跑。外部验证
已在该质量口径下正式提取指标；其数值偏差仍须如实处理，版本放行不等于
验证精度通过。

原 15 组不复算；协议 V1 不生成 68 组数据库。条件域 V2 已另行授权并生成
68 组注册输入，其中 12 组复用、56 组待外部计算。2026-08-02 在历史回传位置
`阶段一/outputs/extrainput/<chid>/` 找到下列两个诊断和两个随机种子重复结果；
每组 715 个文件，均为 FDS 6.9.1、300 s 正常结束、质量 `PASS`。下表只保留
CHID 与服务器路径追溯，不再是待运行清单：

| 目的 | 逻辑 CHID | 服务器工作目录 |
|---|---|---|
| 强风测量域诊断 | `pilot_diag_q100_r160_d70_x41` | `/project/fds_tunnel/runs/pilot_diag_q100_r160_d70_x41/` |
| 强风测量域诊断 | `pilot_diag_q100_r160_d80_x41` | `/project/fds_tunnel/runs/pilot_diag_q100_r160_d80_x41/` |

已生成输入：

```text
C:\Users\xiao.cheng\Desktop\科研\阶段一\outputs\pilot_diagnostic_inputs\pilot_diag_q100_r160_d70_x41.fds
C:\Users\xiao.cheng\Desktop\科研\阶段一\outputs\pilot_diagnostic_inputs\pilot_diag_q100_r160_d80_x41.fds
```

规范的新结果回传位置原定为：

```text
C:\Users\xiao.cheng\Desktop\科研\阶段一\outputs\runs\pilot_diag_q100_r160_d70_x41\
C:\Users\xiao.cheng\Desktop\科研\阶段一\outputs\runs\pilot_diag_q100_r160_d80_x41\
```

每组至少回传实际 `.fds`、`.out`、`_devc.csv`、`_hrr.csv`；为判断出口/测量域
影响，还应回传 `.smv`、温度/速度切片和输入已请求的边界文件。`.end` 建议保留，
但当前 6.9.1 完成证据规则允许以正常 `.out` 加两类 CSV 达到 `T_END` 替代。

本次实际文件位于 `outputs/extrainput/<chid>/`，不移动、不删除；分析命令显式
指定该历史位置。两个诊断正式结果为 1/2 通过，d80 在 x=99 m 前仍未形成
清晰远场衰减。若坚持辨识该工况双侧衰减，需要用户重新决定是否恢复更长
隧道外部计算；该诊断批次本身没有待提交 CHID。后续阶段三批次见下节。

5 °C 工程传感器阈值已依据 IEC 60584-1 的 K 型 2 级允差并经用户批准，证据
记录在 `阶段二/03_删失与峰值/sensor_threshold_evidence_template.csv`。两组
随机种子重复已经完成；代表性噪声包络为 0.1250 °C，最终阈值为
`max(5,0.1250)=5 °C`。

下列两组独立重复已按 FDS 6.9.1 `MISC RND_SEED` 语法完成实跑并通过质量
检查，表中路径保留作证据追溯：

| 逻辑 CHID | 基准父工况 | RND_SEED | 本机输入 | 服务器工作目录 |
|---|---|---:|---|---|
| `pilot_06_q40_r100_d45_seed104729` | `pilot_06_q40_r100_d45` | 104729 | `outputs/pilot_seed_inputs/pilot_06_q40_r100_d45_seed104729.fds` | `/project/fds_tunnel/runs/pilot_06_q40_r100_d45_seed104729/` |
| `pilot_11_q100_r160_d70_seed204729` | `pilot_11_q100_r160_d70` | 204729 | `outputs/pilot_seed_inputs/pilot_11_q100_r160_d70_seed204729.fds` | `/project/fds_tunnel/runs/pilot_11_q100_r160_d70_seed204729/` |

规范回传目录原定为：

```text
C:\Users\xiao.cheng\Desktop\科研\阶段一\outputs\runs\pilot_06_q40_r100_d45_seed104729\
C:\Users\xiao.cheng\Desktop\科研\阶段一\outputs\runs\pilot_11_q100_r160_d70_seed204729\
```

每组至少回传实际 `.fds`、`.out`、`_devc.csv`、`_hrr.csv`；建议保留 `.end`、
`.smv` 和温度/速度切片。两份重复输入与各自父工况除 `HEAD` 和
`MISC RND_SEED` 外逐行一致。

本次实际结果同样位于 `outputs/extrainput/<chid>/`。父/重复各自准稳态窗口、
逐测点差异和 0.1250/0.0774 °C 工况噪声限见
`阶段二/03_删失与峰值/seed_repeat_case_summary.csv`。

## 阶段三 100 m 条件域 V2：56 组新计算

用户已授权固定 100 m 条件域继续。68 组注册表中 12 组先导正式结果直接复用，
不得重跑；其余 56 组需要外部 FDS。输入已由现有生成器写入：

```text
C:\Users\xiao.cheng\Desktop\科研\阶段一\outputs\stage3_100m_v2_inputs\
```

逐 CHID 的完整输入路径、建议服务器工作目录、回传目录、必需文件和封存策略：

```text
C:\Users\xiao.cheng\Desktop\科研\阶段三\01_工况设计\external_run_manifest_100m_v2.csv
C:\Users\xiao.cheng\Desktop\科研\阶段三\01_工况设计\外部运行与回传说明.md
```

新结果统一回传到 `阶段一\outputs\runs\<chid>\`。至少回传实际 `.fds`、`.out`、
`_devc.csv`、`_hrr.csv`，并建议保留 `.end`；代表全场工况另回传 `.smv`、切片与边界
文件。12 个独立测试可以计算，但在模型冻结前不得读取其科学指标。
