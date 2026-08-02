# 阶段一：FDS 模型建立与外部验证

本目录用于建立可复现的隧道火灾 FDS 平台，并完成网格敏感性、洞口边界、外部试验复现、HRR 能量口径和准稳态统计。用户于 2026-08-01 确认以已回传和 firemodels 官方库采用的 **FDS 6.9.1** 作为正式证据基线。FDS 6.10.1 本机短试算仅保留为兼容性记录，不与 6.9.1 正式结果混写。

## 状态与单一事实源

- `src/tunnel_config.py`：几何、材料、燃料、FDS 量名和输出频率。
- `src/generate_fds_case.py`：所有 `.fds` 的生成与工况校验。
- `02_FDS基准模型/tunnel_benchmark.fds`：生成器产物，不独立手改。
- `src/fds_io.py`：读取 FDS CSV 单位行并归一到 °C、kW、kW/m²、m/s。
- `01_文献调研/empirical_formula_catalog.csv`：传统最大温升/临界风速公式的逐分支、单位、DOI、证据等级和实现路径。
- `03_网格敏感性/runs/`：9 组网格工况的实际 `.fds`、DEVC 和 HRR CSV。
- `03_网格敏感性/derived/`：真实切片派生的 `C_T/J_T` 长表与哈希清单。
- `03_网格敏感性/analysis/`：9 组准稳态、Bootstrap 和网格比较结果；当前结论为未收敛。
- `../CLAUDE.md`：FDS 6.9.1 正式证据政策、6.10.1 兼容性实跑记录、错误速查和首次运行门。

本机已确认 FDS 6.10.1 可解析并推进修正后的短工况，且产生非零 HRR、`_devc.csv` 和 `_hrr.csv`。这只证明输入/启动链路有效，不代表网格收敛、洞口无关性、外部验证或正式准稳态计算已经完成。

2026-08-01 扫描发现 3 个外部验证和 12 个先导的完整 FDS 6.9.1 回传。
按现有版本基线重新检查后为 15/15 `PASS`：0 条真正的 FDS `ERROR(...)`、
0 WARNING、0 rejected，DEVC/HRR 均覆盖 `T_END`、火源非零，且 `.out` 均写有
`STOP: FDS completed successfully`。缺 `.end` 已由上述三项完成证据替代。
质量表位于 `outputs/analysis/quality/`；质量通过不等于外部精度或阶段二结构门通过。

2026-08-02 又在历史目录 `outputs/extrainput/<chid>/` 找到两个 x=41 m 强风诊断
和两个非零随机种子重复结果。四组均为 FDS 6.9.1、300 s 正常结束、质量
`PASS`；原始目录保持不动。准稳态、2000 次时间平均 Bootstrap 和质量表位于
`outputs/analysis/extra_four/` 与 `outputs/analysis/quality/extra_four_result_check.csv`。
两组重复形成的代表性弱温升噪声包络为 0.1250 °C；两个诊断的正式结构门为
1/2 通过，d80 到出口前仍未形成可辨识下游远场衰减。

用户随后授权固定 100 m 条件域 V2。阶段三 68 个输入已由本阶段生成器写到
`outputs/stage3_100m_v2_inputs/` 并通过静态合同；其中 12 个先导结果直接复用，
其余 56 个等待外部计算。逐 CHID 路径与回传清单位于
`阶段三/01_工况设计/external_run_manifest_100m_v2.csv`。本机没有运行这 56
个正式工况。

网格回传的 9/9 工况已找到准稳态窗口，但中→细多个核心量超过 5%，且
`gsC_f` 缺少足够的峰值下游温度点拟合 κ_d。用户基于计算资源明确不再加密，
后续统一采用 0.25 m **工作网格**；这是一项资源约束选择，不代表网格收敛。
回传目录还缺 `.out`/`.end`，日志错误和警告审计仍未通过。

## 关键模型决策

1. 基准 L×W×H=100×10×5 m。用户于 2026-07-31 固定 L=100 m 并取消 150/200 m 对照；因此后续只形成 100 m 数值隧道内的条件性结论，不宣称洞口独立。
2. 正庚烷直接使用 `REAC FUEL='N-HEPTANE'`，不额外写 SPEC 或 SIMPLE_CHEMISTRY。
3. 预设 HRR 燃烧器使用 `TAU_Q=10 s`。生成器将燃烧器边界吸附到网格面，并按离散面积及 kW/m² 单位计算 HRRPUA；正式 Q 在约 `3×TAU_Q` 后且进入准稳态时由 CSV 时间平均核验。
4. 粗/中/细均匀网格为 0.5/0.25/0.125 m；当前 9 个网格敏感性输入均划分为 `11×1×2=22 MESH`。中→细结果未收敛，后续按资源约束统一使用 0.25 m 工作网格。
5. 温度/纵向速度测点分别为 0.90H/0.95H；自定义几何按其 H 重算。
6. 火源对流 HRR 从 `_hrr.csv` 推导：`Q_c,source=HRR+Q_RADI`，不使用无效的 `CONVECTIVE HRR` 设备。

## 生成与测试

在 `阶段一` 目录执行：

```bash
python -m unittest discover -s tests -v
python src/generate_fds_case.py --csv 03_网格敏感性/grid_sensitivity_cases.csv
python src/generate_fds_case.py --csv 05_外部试验复现/external_cases_template.csv --outdir outputs/external_inputs
python src/prepare_external_validation.py
```

`lenA_150/lenC_150` 输入曾按原计划生成并保留，但当前不列入运行批次；只有用户
以后重新开启洞口边界验证时，才使用 `--required-only` 重新生成并提交。

外部验证已固定为 Arup FSB2009 Tests 1–5、CSTB Tunnel Test 2 和 IFAB-07。它们使用 firemodels 官方输入快照，生成器只静态校验并逐字复制到独立目录，不把复杂截面重新生成为矩形模型；IFAB 的 `mesh.txt/output.txt` 会一并复制。真实热电偶观测由 `prepare_external_validation.py` 整理到 `05_外部试验复现/observations/`，不得作为主体气体温度或合成训练数据。

现有回传由 `src/analyze_external_validation.py` 正式提取。当前 6.9.1 指标
NRMSE 为 Arup 0.2403、CSTB 0.2659、IFAB 0.1928，详见
`outputs/analysis/validation/external_validation_metrics.csv`。三组质量证据均为
`FORMAL_QUALITY_PASS`；偏差数值本身并未因版本规则调整而改善。

`src/diagnose_external_validation.py` 已进一步用真实试验/FDS 时序核查偏差来源。
三组全部 DEVC 坐标及 `THERMOCOUPLE` 量名匹配；在原窗口前后平移半个窗口长度
时，最佳 NRMSE 仅改善 3.86%~4.82%，且 Arup/IFAB 始终低估、CSTB 始终高估。
因此回传、映射和时间窗均不是主要问题，外部精度门仍未通过。诊断表位于
`outputs/analysis/validation/diagnostics/`，完整解释见外部试验验证报告。

## 运行

`run_case.sh` 默认单进程，仅适用于单 MESH 输入。当前 9 个网格敏感性输入为 22 MESH，正式运行时必须显式设置合适的 MPI/OpenMP 配置并先做同一短算例性能比较：

```bash
bash src/run_case.sh outputs/inputs/gsB_m.fds 22
bash src/run_batch.sh 03_网格敏感性/grid_sensitivity_cases.csv outputs/inputs 22 1 outputs/runs
```

MPI 进程数不得超过 MESH 数。批处理严格按 CSV 的 CHID 运行，不会误跑输出目录中的旧 `.fds`。自定义目录时，`run_batch.sh` 的位置参数依次为输入目录、MPI 进程数、并发数、运行根目录。

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
├── external_inputs/<chid>/ # 官方外部验证输入快照与伴随文件
├── sensors/                # 测点表和布置图
└── logs/                   # Slurm 日志
```

默认路径锚定到本 `阶段一` 目录，从其他目录启动脚本也不会把文件散落到当前工作目录；各脚本的 `--outdir`、`--rundir` 仍可覆盖默认值。

## 外部平台计算与回传

外部计算采用精简流程：每个 CHID 在外部平台使用独立文件夹，计算结束后把整个文件夹复制到本地：

```text
C:\Users\xiao.cheng\Desktop\科研\阶段一\outputs\runs\<chid>\
```

文件夹至少保留实际执行的 `<chid>.fds`、`<chid>.out`、`<chid>_devc.csv` 和 `<chid>_hrr.csv`，建议同时保留 `<chid>.end`。详细说明见 `00_外部计算与回传/README.md`。

复制完成后运行轻量检查：

```bash
python src/check_fds_results.py
```

检查结果写入 `outputs/analysis/quality/result_check.csv`。`PASS` 可进入后处理；`REVIEW` 不自动判废，但需查看版本、WARNING、完成证据或 HRR 偏差；`FAIL` 不得直接分析。

当前外部验证回传采用历史平铺位置 `outputs/<chid>/`。正式提取命令为：

```bash
uv run --python 3.12 --with numpy --with matplotlib python \
  阶段一/src/analyze_external_validation.py
uv run --python 3.12 --with numpy python \
  阶段一/src/diagnose_external_validation.py
```

后续新回传仍统一放入 `outputs/runs/<chid>/`，按 6.9.1 证据基线重新检查并使用
`--rundir outputs/runs` 分析，不得覆盖已有原始结果。

Slurm 提交前先创建日志目录并设置 CSV/工况目录：

```bash
mkdir -p outputs/logs
sbatch --array=0-8 \
  --export=ALL,CASES_CSV="$PWD/03_网格敏感性/grid_sensitivity_cases.csv",CASES_DIR="$PWD/outputs/inputs",RUNS_DIR="$PWD/outputs/runs",FDS_NTASKS=22,FDS_OMP_THREADS=2 \
  src/run_slurm_template.sh
```

Slurm 模板已做 shell 语法检查，但模块名、MPI 类型和调度行为须在目标集群最终验证。

## 后处理顺序

```bash
python src/quasi_steady_detect.py --chids gsA_m gsB_m gsC_m
python src/time_average_bootstrap.py --steady-windows outputs/analysis/steady/steady_windows.csv
python src/analyze_grid_convergence.py --csv 03_网格敏感性/grid_sensitivity_cases.csv --steady-windows outputs/analysis/steady/steady_windows.csv
python src/analyze_boundary_effect.py --length 04_隧道长度与洞口边界/length_boundary_cases.csv --baseline 03_网格敏感性/grid_sensitivity_cases.csv --steady-windows outputs/analysis/steady/steady_windows.csv
python src/check_convection_ratio.py --chids gsA_m gsB_m gsC_m \
  --cases-csv 03_网格敏感性/grid_sensitivity_cases.csv \
  --steady-windows outputs/analysis/steady/steady_windows.csv
```

每次首次运行必须检查目标 FDS 版本、第一条 ERROR、全部 WARNING、无 VENT rejected、`HRR_tot>0`、入口方向为 +x，以及 `_hrr.csv` 含 `HRR/Q_RADI`。

## 进入下一阶段的前提

- [ ] 核心目标量网格收敛
- [ ] 洞口边界对主体测量区影响可接受
- [ ] 外部试验复现达到预定指标
- [ ] 辐射/对流比例口径确定
- [ ] 测点、输出、准稳态窗口和温度定义固定
