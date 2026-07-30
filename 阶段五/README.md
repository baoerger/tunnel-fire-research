# 阶段五：可辨识性与直接反演

当前完成统一 `F_physics`、直接反演、Fisher 信息、候选布局和置信区间软件框架；真实闭合公式尚未选定，因此只使用合成闭合模型验证求解流程，不形成真实精度、覆盖率、可辨识性或推荐布局结论。

- `src/direct_inversion.py`：从 Q、x_f、U、Df 和测点位置计算温升；以 `log Q`、`x_f/H` 执行粗网格、多初值局部细化、边界约束和删失损失。
- `src/fisher_layout.py`：中心差分灵敏度、指数空间相关噪声、Cholesky 求解、Fisher 指标、4/8/12/16 点代表布局、多场景平均/最差候选优化以及 Q—位置目标函数和剖面 SVG。
- `src/confidence_intervals.py`：局部 Hessian、卡方目标剖面、相关观测扰动、相关残差直接反演和参数 Bootstrap。
- `tests/`：合成参数恢复、边界、多初值、删失损失、协方差正定性、相关噪声、布局合同、信息单调性、目标剖面和区间宽度方向测试。
- `01_直接反演/直接反演软件验证报告.md`：当前证据和真实数据依赖。
- `02_Fisher信息与布局/`：明确标记为合成软件测试的灵敏度、布局、Fisher 指标、目标面/剖面数据与软件验证报告。
- `03_置信区间/`：明确标记的局部区间缩放检查、剖面区间、参数 Bootstrap 和软件验证报告。

运行：

```bash
uv run --python 3.12 python -m unittest discover -s 阶段五/tests -v
uv run --python 3.12 python 阶段五/src/fisher_layout.py
uv run --python 3.12 python 阶段五/src/confidence_intervals.py
```

布局优化采用多场景贪心候选序列，不宣称全局组合最优；Fisher 或 Hessian 退化会显式失败，不通过数值正则化掩盖。剖面区间受网格分辨率和范围约束，Bootstrap 当前是给定噪声模型下的参数化观测扰动。必须等阶段四真实闭合公式经开发集交叉验证固定后，才能接入正式 `F_physics`；正式噪声协方差还须由阶段三稳态波动和重复种子估计，12 组独立测试只在全部模型设置固定后用于最终评价。
