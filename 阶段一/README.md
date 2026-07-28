# 阶段一：模型建立与外部验证 · 工作目录

> 本目录是《实施.md》阶段一的执行产物。阶段目标：建立可信、可复现的 FDS 隧道火灾基准平台；
> 完成网格敏感性、洞口边界、外部试验验证与计算资源预算；固定测点/输出/准稳态判定/温度与 HRR 定义。

## ⚠️ 平台说明（请先读）

本机的 Python 仅为 Windows Store 占位（无实际运行环境）、FDS/MPI/conda 均未安装。
因此**本阶段交付的是"可在目标平台直接运行的整套准备物"**，包括：
- FDS 输入文件生成器与一份手写基准 `.fds`；
- 工况表、运行脚本（bash/Slurm）；
- 分析脚本（Python，读 FDS 设备 CSV）；
- 设计/协议/报告文档（含待填结果模板）。

**实际 FDS 计算（§1.3/§1.4/§1.5/§1.8/§1.9 的"运行后填入"部分）须在 Linux/HPC + MPI 平台执行。**
详见 `阶段一完成报告.md` 的"是否需要换平台"一节与 `src/run_slurm_template.sh`。

---

## 目录结构

```
阶段一/
├─ 01_文献调研/
│  ├─ 文献调研笔记.md                     §1.1 经验关联式/临界风速/FDS 验证 + 局限
│  └─ 正向对照基准公式清单.md              §1.1 供阶段四 §23 对照的基准公式表
├─ 02_FDS基准模型/
│  ├─ MODEL_SETTINGS.md                   §1.2 完整模型设置（可复现记录 + 首次必查项）
│  └─ tunnel_benchmark.fds                §1.2 手写基准 .fds（中功率临界风·中网格；供直接检视）
├─ 03_网格敏感性/
│  ├─ grid_sensitivity_cases.csv          §1.3 9 工况表（3 工况×3 网格）
│  └─ 网格敏感性分析报告.md                §1.3 协议 + 结果模板
├─ 04_隧道长度与洞口边界/
│  ├─ length_boundary_cases.csv           §1.4 延长工况表（150/200 m）
│  └─ 洞口边界影响报告.md                  §1.4 协议 + 结果模板
├─ 05_外部试验复现/
│  ├─ external_cases_template.csv        §1.5 外部工况模板（TBD 待按文献补全）
│  └─ 外部试验候选清单.md                  §1.5 Memorial/Runehamar/EUREKA/缩尺候选 + 选用准则
├─ 06_计算资源预算/
│  └─ 计算资源预算表.md                    §1.6 81~84 次计算的核心小时与存储预算
├─ 07_温度测点与输出规范/
│  ├─ generate_sensor_layout.py           §1.7 测点布置生成器 → CSV + 图 + 稀疏子集
│  └─ 输出变量与频率规范.md                 §1.7 测点/输出/频率规范
├─ 08_HRR与对流比例/
│  └─ 对流比例稳定性检查报告.md            §1.8 协议 + 结果模板
├─ 09_准稳态与时间平均/
│  └─ 准稳态判定与时间平均规范.md            §1.9 协议 + 结果模板
├─ src/
│  ├─ tunnel_config.py                   共享几何/物理常量 + 测点布局
│  ├─ generate_fds_case.py                ★ FDS 输入文件生成器（核心）
│  ├─ fds_io.py                           FDS 设备 CSV 读取 + 温度曲线特征提取
│  ├─ analyze_grid_convergence.py         §1.3 网格收敛分析
│  ├─ analyze_boundary_effect.py          §1.4 洞口边界分析
│  ├─ check_convection_ratio.py           §1.8 对流比例检查
│  ├─ quasi_steady_detect.py              §1.9 准稳态检测
│  ├─ time_average_bootstrap.py          §1.9 时间平均 + 分块 Bootstrap
│  ├─ validation_metrics.py               §1.5 NRMSE/偏差/峰值位置误差
│  ├─ run_case.sh / run_batch.sh           Linux/MPI 单/批量运行
│  └─ run_slurm_template.sh               HPC Slurm array 模板
├─ README.md                              本文件
└─ 阶段一完成报告.md                       完成状态 + 是否换平台结论
```

---

## 关键设计决策（与两份文档的关系）

1. **隧道长度 L=100 m**：采用《实施.md》（更新）的 100 m 基准，而非理论研究方案.docx 的 250~300 m。
   代价：洞口边界影响增大 → §1.4 列为**生产前必查项**。若 §1.4 不通过，延长至 150~200 m。
2. **火源建模**：预设 `HRRPUA` 燃烧器，精确控制总功率 Q（FDS 按需注入燃料）；正庚烷 SIMPLE_CHEMISTRY，
   碳烟适中，便于实现合理 χ_r（§1.8 以 FDS 实测 Q_c/Q 检验，不假设）。
3. **网格**：均匀单 mesh，粗 0.5 / 中 0.25 / 细 0.125 m；中网格为候选生产网格，3 代表工况计入 68 组 DB。
4. **测点高度**：顶棚下方 0.10H（z=4.5 m），§1.3 核查近壁稳定性后可在 0.05H~0.10H 内调整。
5. **纵向通风**：入口 x=0 施加速度（`VEL` 负值推入域内），出口 x=L OPEN；U=0 两端 OPEN。

---

## 推荐执行顺序（Linux/HPC 平台）

```bash
cd 阶段一

# 0) 一次性依赖：python3 + numpy + matplotlib（分析/绘图）、mpiexec + FDS(fds)
#    （如集群无 matplotlib，绘图自动跳过，CSV 仍产出）

# 1) 测点布置（可选，FDS 工况已内嵌同一布局）
python3 07_温度测点与输出规范/generate_sensor_layout.py --outdir 07_温度测点与输出规范

# 2) 生成全部 §1.3/§1.4 FDS 输入
python3 src/generate_fds_case.py --csv 03_网格敏感性/grid_sensitivity_cases.csv --outdir fds_cases
python3 src/generate_fds_case.py --csv 04_隧道长度与洞口边界/length_boundary_cases.csv --outdir fds_cases

# 3) 运行（二选一）
bash src/run_batch.sh 03_网格敏感性/grid_sensitivity_cases.csv fds_cases 8 1
# 或 Slurm array（按 .fds 数设 --array 上界）：
#   sbatch --array=0-8 src/run_slurm_template.sh

# 4) 首次必查（见 MODEL_SETTINGS.md §6）：短试算确认入口风向 / HRR 量名 / 壁面 / HRRPUA / 测点高度

# 5) 准稳态检测 → 时间平均 → 网格/边界/对流比例分析
python3 src/quasi_steady_detect.py --rundir fds_cases --chids gsA_m gsB_m gsC_m --outdir results/steady
python3 src/time_average_bootstrap.py --rundir fds_cases --chid gsB_m --t0 <t0> --t1 <t1> --outdir results/avg
python3 src/analyze_grid_convergence.py --csv 03_网格敏感性/grid_sensitivity_cases.csv --rundir fds_cases --t0 <t0> --t1 <t1> --outdir results/grid
python3 src/analyze_boundary_effect.py --length 04_隧道长度与洞口边界/length_boundary_cases.csv --baseline 03_网格敏感性/grid_sensitivity_cases.csv --rundir fds_cases --t0 <t0> --t1 <t1> --outdir results/boundary
python3 src/check_convection_ratio.py --rundir fds_cases --chids gsA_m gsB_m gsC_m --t0 <t0> --t1 <t1> --outdir results/hrr
```

## 进入下一阶段的前提（阶段一末勾选）
- [ ] 网格核心目标量收敛
- [ ] 洞口边界对主体测量区影响可忽略
- [ ] 外部试验复现达可接受指标
- [ ] 计算资源预算可承载 81~84 次计算
- [ ] 测点高度/输出/准稳态判定/温度定义已固定并统一
