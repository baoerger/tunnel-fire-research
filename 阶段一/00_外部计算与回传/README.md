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

如果外部平台使用的不是 6.10.1：

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
- `REVIEW`：结果不自动判废，但需要人工查看，例如存在 WARNING、版本不同、缺少 `.end` 或 HRR 功率偏差较大；
- `FAIL`：缺少关键文件、计算未到 T_END、存在 ERROR/rejected、火源为零或关键 CSV 无效，不能直接进入分析。

版本不同不会自动判为失败。如果正式数据库需要混用版本，应先用一个代表工况比较 HRR、温度峰值、峰值位置和衰减参数，确认版本差异可以接受并记录所用版本。

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

短试算必须检查：FDS 6.10.1、第一条 ERROR、全部 WARNING/rejected、22 MESH 通信与负载、时间推进、`HRR_tot>0`、入口 +x、DEVC 关键列、`_hrr.csv HRR/Q_RADI` 和结束时间。60 s 虽超过约 `3×TAU_Q`，仍不作为准稳态、网格收敛、洞口或外部验证结论。

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

关键输出位于 `/project/fds_tunnel/runs/<chid>/<chid>.out`、`<chid>_devc.csv`、`<chid>_hrr.csv`、`<chid>.smv`，以及同目录切片/三维场和 `.bf`。计算完成后把整个目录回传到：

```text
C:\Users\xiao.cheng\Desktop\科研\阶段一\outputs\runs\<chid>\
```

外部官方输入使用自己的热电偶 ID，不要求主体模型的 `T_*/U_*/HRR_tot/Qw_*`。检查器会从 `05_外部试验复现/measurement_mapping.csv` 读取应有通道，并强制要求 `HRR/Q_RADI`、非零火源、完整时长及 `.smv`、切片/三维场和 `.bf`；主体工况原判据保持不变。
