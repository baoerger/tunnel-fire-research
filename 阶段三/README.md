# 阶段三：准稳态数据库

本阶段目标是在固定 `100×10×5 m`、`dx=0.25 m`、测量区 `x=15--85 m`
条件域内形成 68 组真实 FDS 准稳态数据库。协议 V1 的 `4/12 FAIL` 保持不变；
用户授权采用条件域协议 V2 后，12 行标为
`REUSE_EXISTING_REAL_FDS_100M_V2`，56 行标为
`AUTHORIZED_100M_V2_WAITING_EXTERNAL_FDS`，并由现有生成器生成输入。

其中 12 组先导已有质量通过的正式 FDS 6.9.1 结果，阶段三直接复用，不重跑；
其余 56 组等待外部计算。按冻结的阶段一准稳态判据重新批处理后，现有 12/12
已形成可显式采用的时间平均结果，另有 38 个开发工况尚未回传；12 个独立测试
和 6 个偏移工况不在默认开发批处理中。

## 当前文件

- `src/design_database_cases.py`：复用 12 组已有真实先导，以固定流态配额和受约束 Latin hypercube 补足 50 组开发工况，并生成 12 组封存测试和 6 组偏移工况。
- `01_工况设计/database_cases_candidate.csv`：68 组候选元数据；含连续 `Q/A`、离散 HRRPUA、完整物理工况分组、5 折分组、封存策略和重复种子预留字段。
- `01_工况设计/external_run_manifest_100m_v2.csv`：12 组复用和 56 组新算的逐 CHID 输入、服务器工作目录与回传映射。
- `01_工况设计/外部运行与回传说明.md`：外部命令、必回传文件和独立测试封存规则。
- `01_工况设计/候选工况设计报告.md`：覆盖、约束、阻塞和首次静态失败证据。
- `src/case_registry.py`：按用途选择子集、独立测试显式解封、派生样本绑定和单一 adopted 结果版本检查。
- `02_数据结构/数据库结构与隔离规范.md`：原始时序、稳态窗口、时间平均、不确定性和元数据表的字段合同。
- `src/batch_postprocess.py`：对选定子集连续执行轻量质量检查、准稳态识别、时间平均、分块 Bootstrap 和噪声分量汇总；逐工况失败不阻断其余工况。
- `03_批量后处理/批量后处理软件验证报告.md`：12 组真实复用结果、38 组等待状态和合成软件通路边界。
- `outputs/analysis/batch/`：真实批处理产物；当前 12 个既有开发工况为
  `READY_FOR_EXPLICIT_ADOPTION`，38 个新开发工况为 `WAITING_RETURN`。
- `tests/test_database_design.py`：候选覆盖和隔离合同测试。

## 重新生成与测试

从项目根目录执行：

```bash
uv run --python 3.12 python 阶段三/src/design_database_cases.py
uv run --python 3.12 python 阶段一/src/generate_fds_case.py \
  --csv 阶段三/01_工况设计/database_cases_candidate.csv \
  --outdir 阶段一/outputs/stage3_100m_v2_inputs
uv run --python 3.12 python 阶段三/src/batch_postprocess.py --n-boot 2000
uv run --python 3.12 python -m unittest discover -s 阶段三/tests -v
```

设计器写注册表和外部运行映射；`.fds` 仍只由阶段一生成器生成。几何归一化
和 MESH 合同会检查每个燃烧器，任何内部 MESH 边界切穿都会直接失败。当前
68/68 输入已通过 CHID、22 MESH、T_END、量名、材料单位、测量区和 TAIL 静检；
本机没有运行新增 56 组正式 FDS。

批量后处理默认只处理开发集。质量检查的 `REVIEW/FAIL` 不会自动进入准稳态分析；独立测试必须使用 `--purpose final_evaluation --confirm-final-evaluation` 显式解封。`noise_summary.csv` 只汇总时间波动和重复种子均值离散，重复种子不足时保持 `WAITING_SEED_REPEATS`，不会自动定稿 `Delta T_noise`。

## 数据隔离

- 50 个 `development` 工况用于按完整物理工况分组的交叉验证；同一 CHID 的时窗、噪声、布局和缺测样本保持同一个 `dataset_group_id` 与 `subset`。
- 12 个 `independent_test` 工况标为 `SEALED_UNTIL_FINAL_EVALUATION`。`case_registry.select_cases` 默认只返回开发集；最终评价必须显式确认解封。
- 6 个 `offset_validation` 工况只验证平移、峰值偏移和定位，不进入主体闭合训练。
- 每个 CHID 可保存多个 `result_version`，但 `case_registry.validate_case_linkage` 禁止同时采用两套正式结果。

## 正式化门

协议 V1 的网格、洞口、外部验证和结构门均不得改写为通过。条件域 V2 允许按
用户固定的 100 m 范围计算剩余 56 组，但科学结论严格限于该几何、网格和
15--85 m 观测域。50 个开发工况齐全并通过质量/准稳态后才能拟合正式闭合；
12 个独立测试的结果在闭合公式、反演与网络超参数全部固定前不得用于修改模型。
