# 阶段三：准稳态数据库

本阶段最终目标是形成 68 组真实 FDS 准稳态数据库。当前只完成候选工况和数据隔离软件；阶段一、二决策门尚未通过，`01_工况设计/database_cases_candidate.csv` 的 68 行全部为 `WAITING_STAGE2_GATE`，没有生成阶段三 `.fds`，也不是仿真结果。

## 当前文件

- `src/design_database_cases.py`：复用 12 组先导候选，以固定流态配额和受约束 Latin hypercube 补足 50 组开发候选，并生成 12 组封存测试候选和 6 组偏移候选。
- `01_工况设计/database_cases_candidate.csv`：68 组候选元数据；含连续 `Q/A`、离散 HRRPUA、完整物理工况分组、5 折分组、封存策略和重复种子预留字段。
- `01_工况设计/候选工况设计报告.md`：覆盖、约束、阻塞和首次静态失败证据。
- `src/case_registry.py`：按用途选择子集、独立测试显式解封、派生样本绑定和单一 adopted 结果版本检查。
- `02_数据结构/数据库结构与隔离规范.md`：原始时序、稳态窗口、时间平均、不确定性和元数据表的字段合同。
- `src/batch_postprocess.py`：对选定子集连续执行轻量质量检查、准稳态识别、时间平均、分块 Bootstrap 和噪声分量汇总；逐工况失败不阻断其余工况。
- `03_批量后处理/批量后处理软件验证报告.md`：当前无回传结果的实际预检状态和合成软件通路边界。
- `outputs/analysis/batch/`：当前批量预检产物；50 个开发候选均为 `WAITING_RETURN`，空结果表只有表头。
- `tests/test_database_design.py`：候选覆盖和隔离合同测试。

## 重新生成与测试

从项目根目录执行：

```bash
uv run --python 3.12 python 阶段三/src/design_database_cases.py
uv run --python 3.12 python 阶段三/src/batch_postprocess.py --n-boot 2000
uv run --python 3.12 python -m unittest discover -s 阶段三/tests -v
```

生成器只写候选 CSV，不调用 FDS 生成器写 `.fds`。它会用阶段一几何归一化和 MESH 合同静态检查每个候选燃烧器，任何内部 MESH 边界切穿都会直接失败。

批量后处理默认只处理开发集。质量检查的 `REVIEW/FAIL` 不会自动进入准稳态分析；独立测试必须使用 `--purpose final_evaluation --confirm-final-evaluation` 显式解封。`noise_summary.csv` 只汇总时间波动和重复种子均值离散，重复种子不足时保持 `WAITING_SEED_REPEATS`，不会自动定稿 `Delta T_noise`。

## 数据隔离

- 50 个 `development` 工况用于按完整物理工况分组的交叉验证；同一 CHID 的时窗、噪声、布局和缺测样本保持同一个 `dataset_group_id` 与 `subset`。
- 12 个 `independent_test` 工况标为 `SEALED_UNTIL_FINAL_EVALUATION`。`case_registry.select_cases` 默认只返回开发集；最终评价必须显式确认解封。
- 6 个 `offset_validation` 工况只验证平移、峰值偏移和定位，不进入主体闭合训练。
- 每个 CHID 可保存多个 `result_version`，但 `case_registry.validate_case_linkage` 禁止同时采用两套正式结果。

## 正式化门

阶段一全部进入条件和阶段二五项判伪门通过后，才允许用冻结的长度、生产网格、测点、真实 Uc/流态证据重新生成候选版本并提交正式 FDS。12 个独立测试的结果在闭合公式、反演与网络超参数全部固定前不得用于修改模型。
