# 无风隧道顶棚纵向温度研究

本仓库已按“正向公式重发现 → 直接反演 → 独立锁箱”的主线重构。项目根目录是唯一研究根，不再使用“阶段一至阶段七”目录组织工作。

当前正式入口：

- 科学方案：[`docs/无风隧道顶棚纵向温度_正向主导双向闭环实施方案_主次定稿版.md`](docs/无风隧道顶棚纵向温度_正向主导双向闭环实施方案_主次定稿版.md)
- 执行方案：[`docs/实施方案.md`](docs/实施方案.md)
- 当前进度：[`docs/当前实施进度.md`](docs/当前实施进度.md)
- 自动执行 Loop：[`docs/项目自动执行Loop提示词.md`](docs/项目自动执行Loop提示词.md)
- 协议草案：[`config/protocol_v1.json`](config/protocol_v1.json)（G0 外部审计完成前 `formal_run_authorized=false`）
- FDS 技术规范：[`CLAUDE.md`](CLAUDE.md)

目录职责：

- `src/fds/`：FDS 配置、输入生成、CSV 读取和质量审计的单一事实源。
- `src/analysis/`：网格、准稳态、时间平均、Bootstrap 和温度曲线分析。
- `config/no_wind/`：尚未授权正式运行的无风设计草案。
- `extras/legacy_modeling/`：可参考的旧正向/反演脚手架，不是当前冻结模型。
- `extras/fast_inversion/`：导师要求时才激活的 G7 快速反演附加实验；当前状态为 `G7_SKIPPED_NOT_REQUESTED`。
- `fds_inputs/`、`runs/`、`derived/`：可再生输入、逐 attempt 原始结果和派生结果，默认不纳入 Git。

运行全部保留测试：

```powershell
uv run --python 3.12 --with numpy python -m unittest discover -s tests -t . -q
```

当前生成器尚未完成执行方案 G1 要求的背景模式、固定全局测点、镜像 MESH 和 attempt 身份链，因此不得直接据现有草案启动正式批算。
