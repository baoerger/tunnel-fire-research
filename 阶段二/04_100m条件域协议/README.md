# 100 m 条件域协议 V2

本协议只适用于当前固定计算条件：`L×W×H=100×10×5 m`、工作网格
`dx=0.25 m`、主体测量区 `x=15--85 m`、FDS 6.9.1。它不声称网格收敛、
洞口独立，也不外推为长隧道通用结论。

协议 V1 的正式结论保持为 `4/12, FAIL`，文件和指标不修改。V2 改变的是研究
输出合同：所有质量与准稳态通过工况都保留 100 m 内温度剖面、连续峰值、平台
宽度和删失类别；只有某一侧同时具备足够测点且拟合稳定时，才输出该侧衰减
参数。上游删失时 `k_u` 留空，下游域删失时 `k_d` 留空；缺一侧时不能计算
需要双侧参数的 `Pe_e/Da_e`。缺失值必须为空并配参数掩码，不能以 0、硬编码
或强制拟合代替。

V2 的分类为：

- `bilateral_identifiable`：两侧都可进入稳定性审查；
- `upstream_censored`：上游低于检测阈值，下游仍可作为条件目标；
- `downstream_domain_censored`：100 m 域没有提供可辨识下游远场；
- `no_obvious_backflow`：不从上游数值波动反推衰减参数。

现有 12 组用于制定协议，因此只能得到
`DEVELOPMENT_GATE_PASS_CONFIRMATION_PENDING`。正式科学确认必须在闭合结构和
超参数冻结后使用 12 组封存独立测试；开发集、封存集和偏移验证集仍按完整
物理 CHID 隔离。V1 失败不能被引用为 V2 的独立验证。

运行：

```bash
uv run --python 3.12 python 阶段二/src/conditional_domain_protocol.py
```

输出 `protocol_v2_case_targets.csv` 和 `protocol_v2_summary.csv` 均来自真实 V1
FDS 指标，但它们只是协议迁移证据，不是闭合公式、反演精度或网络性能结论。
