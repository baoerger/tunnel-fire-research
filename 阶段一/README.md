# 阶段一：FDS 模型建立与外部验证

本目录用于建立可复现的隧道火灾 FDS 平台，并完成网格敏感性、洞口边界、外部试验复现、HRR 能量口径和准稳态统计。当前输入语法与最小启动已在本机 **FDS 6.10.1** 验证；正式 300 s 工况和论文统计结论仍须运行后填写。

## 状态与单一事实源

- `src/tunnel_config.py`：几何、材料、燃料、FDS 量名和输出频率。
- `src/generate_fds_case.py`：所有 `.fds` 的生成与工况校验。
- `02_FDS基准模型/tunnel_benchmark.fds`：生成器产物，不独立手改。
- `src/fds_io.py`：读取 FDS CSV 单位行并归一到 °C、kW、kW/m²、m/s。
- `../CLAUDE.md`：FDS 6.10.1 已验证语法、错误速查和首次运行门。

本机已确认 FDS 6.10.1 可解析并推进修正后的短工况，且产生非零 HRR、`_devc.csv` 和 `_hrr.csv`。这只证明输入/启动链路有效，不代表网格收敛、洞口无关性、外部验证或正式准稳态计算已经完成。

## 关键模型决策

1. 基准 L×W×H=100×10×5 m；L=100 m 的洞口影响必须用 150/200 m 对照检查。
2. 正庚烷直接使用 `REAC FUEL='N-HEPTANE'`，不额外写 SPEC 或 SIMPLE_CHEMISTRY。
3. 预设 HRR 燃烧器使用 `TAU_Q=10 s`。生成器将燃烧器边界吸附到网格面，并按离散面积及 kW/m² 单位计算 HRRPUA；正式 Q 在约 `3×TAU_Q` 后且进入准稳态时由 CSV 时间平均核验。
4. 粗/中/细均匀单 mesh 为 0.5/0.25/0.125 m；生产网格由核心目标量收敛决定。
5. 温度/纵向速度测点分别为 0.90H/0.95H；自定义几何按其 H 重算。
6. 火源对流 HRR 从 `_hrr.csv` 推导：`Q_c,source=HRR+Q_RADI`，不使用无效的 `CONVECTIVE HRR` 设备。

## 生成与测试

在 `阶段一` 目录执行：

```bash
python -m unittest discover -s tests -v
python src/generate_fds_case.py --csv 03_网格敏感性/grid_sensitivity_cases.csv
python src/generate_fds_case.py --csv 04_隧道长度与洞口边界/length_boundary_cases.csv
```

外部模板含 `TBD` 时生成器会快速失败并列出未填写字段；必须按文献补齐后再生成，不会猜值或静默跳过。

## 运行

单 mesh 默认单进程：

```bash
bash src/run_case.sh outputs/inputs/gsB_m.fds
bash src/run_batch.sh 03_网格敏感性/grid_sensitivity_cases.csv
```

只有输入明确划分多个 MESH 时才增加 MPI 进程。批处理严格按 CSV 的 CHID 运行，不会误跑输出目录中的旧 `.fds`。自定义目录时，`run_batch.sh` 的位置参数依次为输入目录、MPI 进程数、并发数、运行根目录。

所有脚本的默认产物统一放在：

```text
outputs/
├── inputs/                 # 生成的 .fds 输入
├── runs/<chid>/            # 各工况原始 FDS 输出，互不混放
├── analysis/               # 后处理表格和图片
│   ├── steady/
│   ├── time_average/
│   ├── grid_convergence/
│   ├── boundary_effect/
│   ├── convection_ratio/
│   └── validation/
├── sensors/                # 测点表和布置图
└── logs/                   # Slurm 日志
```

默认路径锚定到本 `阶段一` 目录，从其他目录启动脚本也不会把文件散落到当前工作目录；各脚本的 `--outdir`、`--rundir` 仍可覆盖默认值。

Slurm 提交前先创建日志目录并设置 CSV/工况目录：

```bash
mkdir -p outputs/logs
sbatch --array=0-8 \
  --export=ALL,CASES_CSV="$PWD/03_网格敏感性/grid_sensitivity_cases.csv",CASES_DIR="$PWD/outputs/inputs",RUNS_DIR="$PWD/outputs/runs" \
  src/run_slurm_template.sh
```

Slurm 模板已做 shell 语法检查，但模块名、MPI 类型和调度行为须在目标集群最终验证。

## 后处理顺序

```bash
python src/quasi_steady_detect.py --chids gsA_m gsB_m gsC_m
python src/time_average_bootstrap.py --chid gsB_m --t0 <t0> --t1 <t1>
python src/analyze_grid_convergence.py --csv 03_网格敏感性/grid_sensitivity_cases.csv --t0 <t0> --t1 <t1>
python src/analyze_boundary_effect.py --length 04_隧道长度与洞口边界/length_boundary_cases.csv --baseline 03_网格敏感性/grid_sensitivity_cases.csv --t0 <t0> --t1 <t1>
python src/check_convection_ratio.py --chids gsA_m gsB_m gsC_m --t0 <t0> --t1 <t1>
```

每次首次运行必须检查目标 FDS 版本、第一条 ERROR、全部 WARNING、无 VENT rejected、`HRR_tot>0`、入口方向为 +x，以及 `_hrr.csv` 含 `HRR/Q_RADI`。

## 进入下一阶段的前提

- [ ] 核心目标量网格收敛
- [ ] 洞口边界对主体测量区影响可接受
- [ ] 外部试验复现达到预定指标
- [ ] 辐射/对流比例口径确定
- [ ] 测点、输出、准稳态窗口和温度定义固定
