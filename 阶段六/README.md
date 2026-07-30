# 阶段六：可微物理解码网络

当前完成 6.1 集合编码器与数据接口、6.2 合成扰动/可微物理解码/联合损失，以及 6.3 训练协议与四模型统一评价框架。项目没有固定的 Torch/JAX/NumPy 依赖，因此采用仓库内最小标量反向自动微分后端，实现真实可求梯度的共享 MLP 和物理解码链，而不是仅保留接口。只有明确标记的合成软件权重，不形成真实网络精度或优势结论。

- `src/set_encoder.py`：单传感器 `{dT, x, m}` 与已知 U、Df、H、W、T0 输入；共享编码器；掩码 mean/max/有效数量聚合；`Q_ref exp(z_Q)` 和有界 sigmoid 位置输出；状态保存/加载。
- `src/physics_training.py`：合成参数、相关温度噪声、位置误差、随机缺测和模型残差生成；与阶段五合成前向一致的可微解码器；参数监督、温度重建和删失联合损失。
- `src/training_evaluation.py`：Adam、训练版本日志、完整工况分组折叠、独立测试封存、普通全连接基线和四模型统一评价。
- `tests/`：自动微分、集合合同、解码梯度、合成确定性、删失、优化器、分组无泄漏、封存保护和四模型注册测试。
- `01_集合编码器/`：明确标记的合成接口检查、未训练初始化和软件验证报告。
- `02_合成数据与物理解码/`：30 个明确标记的合成样本、联合损失/梯度检查和软件验证报告。
- `03_训练与四模型对照/`：合成训练日志、三套合成软件权重、四模型指标、协议快照和软件验证报告。

运行：

```bash
uv run --python 3.12 python -m unittest discover -s 阶段六/tests -v
uv run --python 3.12 python 阶段六/src/set_encoder.py
uv run --python 3.12 python 阶段六/src/physics_training.py
uv run --python 3.12 python 阶段六/src/training_evaluation.py
```

当前 `ConstantClosurePhysicsDecoder` 只复现阶段五合成常数潜变量软件模型；三套权重状态均为 `SYNTHETIC_PRETRAINED_SOFTWARE_ONLY`。正式预训练必须等阶段四闭合固定，FDS 微调与独立测试必须遵守阶段三数据集隔离；独立测试默认拒绝访问，仅在所有设置冻结后的最终评价显式释放。
