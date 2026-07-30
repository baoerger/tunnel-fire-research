# Fisher 信息与布局软件验证报告

状态：**A/B 类软件框架完成；真实可辨识性、推荐布局和局部最优结论等待正式闭合、噪声与独立测试。**

## 方法合同

- 灵敏度参数固定为 `η=[log Q, x_f/H]`，使用中心差分计算 `J`。
- 观测协方差使用指数空间相关模型，并将可配置 nugget 作为观测噪声模型的一部分；通过 Cholesky 分解检查正定性和求解 `Σ⁻¹J`。
- 计算 `I=JᵀΣ⁻¹J`、`det(I)`、`cond(I)` 和 `tr(I⁻¹)`。退化矩阵显式标记为 `SINGULAR`，其条件数和逆矩阵迹为无穷，不用正则项掩盖不可辨识。
- 比较现有均匀布局、标称火源两侧布局、最大上游偏置的单侧不利布局、强风下游偏置布局，以及多 Q/位置/风速场景的平均和最差 log-det 贪心候选布局。
- 贪心结果只是组合搜索候选，不宣称全局最优；4/8/12/16 点来自同一嵌套序列，便于检查增加观测后信息不下降。
- Q—位置二维目标采用同一相关协方差的二次型，同时导出二维网格、Q 剖面、位置剖面和 SVG 图。

## 可定位的软件证据

原始测点与既有均匀子集：

- `阶段一/07_温度测点与输出规范/sensor_layout.csv`
- `阶段一/07_温度测点与输出规范/sparse_subsets.csv`

分析脚本与测试：

- `阶段五/src/fisher_layout.py`
- `阶段五/tests/test_fisher_layout.py`

合成输出：

- `synthetic_sensitivity.csv`
- `synthetic_layouts.csv`
- `synthetic_fisher_metrics.csv`
- `synthetic_objective_surface.csv`
- `synthetic_profile_Q.csv`
- `synthetic_profile_x.csv`
- `synthetic_objective_profiles.svg`

全部 CSV 均带 `SYNTHETIC_SOFTWARE_TEST_NOT_SCIENTIFIC_EVIDENCE`；布局表及指标表另带 `SOFTWARE_ONLY_NO_LAYOUT_RECOMMENDATION`。合成网格的目标函数最小值回到生成真值，只证明目标面、相关残差和剖面导出实现一致。部分单侧或局部布局在某些场景下被正确检出为 Fisher 退化，这是软件判别测试，不是对真实隧道布局的结论。

## 正式分析仍缺的输入

正式 Fisher 分析必须等待：阶段一决策门通过；阶段二结构与删失规则固定；阶段三开发数据库和稳态/重复种子噪声统计完成；阶段四闭合公式经交叉验证固定。之后才可使用开发工况选择候选布局，并在所有设置冻结后用 12 组独立测试评价，当前合成排序不得转写为推荐布局。
