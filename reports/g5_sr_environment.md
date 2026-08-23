# G5-SR 运行环境核验

> 核验日期：2026-08-23
> 核验范围：本机 Windows、Python/Julia/PySR/AI Feynman 可用性以及不读取项目数据的合成 smoke test
> 数据隔离：本次没有读取或运行锁箱，也没有读取开发集温度结果。

## 1. 初始环境

| 项目 | 实测状态 |
|---|---|
| `uv` | 可用，版本 `0.11.25` |
| PATH 中的 `python` | 仅为 WindowsApps 占位入口，不能作为项目解释器 |
| `py`、`pip`、`pip3` | 不可用 |
| `conda`、`mamba` | 不可用 |
| `julia`、`juliaup` | 核验开始时不可用 |
| `pysr`、`aifeynman` 命令 | 核验开始时不可用 |
| `uv` 管理的 Python | CPython `3.11.15`、`3.12.13`、`3.14.6` 均存在；三者的基础环境均未安装科学计算依赖 |

G5-SR 采用 CPython 3.11。仓库根目录的 `pyproject.toml` 将 Python 限定为 `>=3.11,<3.12`，直接锁定当前代码和测试需要的 `numpy==2.4.6`、`scipy==1.17.1`、`sympy==1.14.0`、`pytest==9.1.1`，锁定主搜索引擎 `pysr==1.5.10`，并将连续域区间认证实际调用的 `mpmath==1.3.0` 提升为显式依赖；完整传递依赖写入 `uv.lock`。AI Feynman 不属于硬依赖。

建议的重建入口是：

```powershell
uv sync --locked
```

`uv.lock` 只冻结 Python 依赖。环境指纹会逐项记录并校验 `mpmath`、NumPy、SciPy、SymPy 与 PySR 的实际版本；连续认证模块也与搜索、评价、协议守卫和依赖锁一起列入正式执行制品 SHA-256 清单。PySR 首次导入时仍会由 JuliaPkg 下载 Julia 并解析 Julia 包，因此正式搜索还必须把实际 Julia 与 `SymbolicRegression.jl` 版本写入每次运行的环境指纹。

## 2. PySR 临时安装与后端启动

核验使用系统临时目录中的独立 Python 3.11 虚拟环境，并把 `JULIA_DEPOT_PATH`、`JULIAUP_DEPOT_PATH` 和 `PYTHON_JULIAPKG_PROJECT` 全部重定向到同一临时目录，未在仓库中创建 `.venv` 或 Julia depot。

- `pysr==1.5.10` 成功解析和安装，共 22 个 Python 包；安装器明确列出的主要下载量约 58.3 MiB，冷安装约 48 秒。
- 首次导入成功下载 Julia `1.12.7`。
- PySR 的 Julia 项目解析到 `SymbolicRegression.jl==1.11.3`；PySR 自带约束是 `~1.11.0`，所以该补丁版本不由 `uv.lock` 固定。
- Julia 下载、Julia 包、artifact、预编译缓存和 smoke 输出使临时目录达到约 1.62 GiB；正式环境建议至少预留 2 GB，实际 G5-SR 中间结果另计。
- 首次导入（包括下载 267.2 MB Julia 压缩包、安装、解析 Julia 包和预编译）实测 781.08 秒，约 13 分钟；后续同一 depot 的暖启动会复用缓存。

## 3. 合成 smoke test

smoke test 只使用程序内生成的低维数组，不读取 `runs/`、`derived/` 或锁箱。测试目标是确认 Python 能导入 PySR、Julia 后端能启动，并能在极小搜索预算下返回至少一个可计算表达式。

当前状态：`SMOKE_PASS`。通过的确定性最小测试为：

- 数据：41 个等距点，`x in [-2, 2]`，目标 `y=x^2`；
- 搜索：串行、固定随机种子 123，二元算子仅 `+` 和 `*`，`niterations=4`、`populations=1`、`population_size=30`、`ncycles_per_iteration=50`、`maxsize=7`；
- 结果：`x*(x*1.0 - 8.281593e-9)`；
- 41 点最大绝对误差：`1.6563186378e-08`；
- 暖环境拟合耗时：20.01 秒。

随后又用仓库 `run_g5_symbolic_search.py` 的完整受限算子配置做了 1 轮、300 次评价上限的实现级 smoke。首次运行发现自定义 `pow_positive_base` 的无效域分支返回了 `Float64 NaN`，与 PySR 的 `Float32` 输入类型不一致；实现已改为 `oftype(x, NaN)`。修复后同一配置正常完成并返回 5 条 Pareto 候选，证明项目搜索器本身能够驱动当前 Julia 后端。该极小测试只验证软件合同，不作为公式发现结果。

该结果只证明 Windows 上的 Python→Julia→SymbolicRegression 搜索链可运行，不代表正式 G5-SR 的计算预算、收敛性或科学结果。

## 4. AI Feynman 2.0 兼容性

PyPI 的 `aifeynman==2.0.7` 可以解析，但默认安装在当前工具链下失败：其依赖仍写成已弃用的 `sklearn` 占位包。设置 `SKLEARN_ALLOW_DEPRECATED_SKLEARN_PACKAGE_INSTALL=True` 后可以继续解析，但会额外引入 `torch==2.13.0`、`torchvision==0.28.0`、matplotlib/seaborn 等较重依赖。

因此当前可执行结论是：

1. PySR 继续作为 G5-SR 唯一主搜索引擎；
2. AI Feynman 2.0 不进入 `pyproject.toml` 或 `uv.lock`；
3. 若以后执行 AI Feynman 结构审计，应使用单独的隔离环境并记录兼容变量、精确依赖和失败日志；其不可复现不阻塞主搜索，也不能临时替换主引擎。

## 5. 当前真实阻塞

运行环境已具备可重建路径，但 G5-SR 仍不能开始正式搜索。首先，执行补充协议仍为 `DRAFT_REQUIRES_USER_APPROVAL`，正式执行器尚未获得双协议授权；其次，下列已批准的开发集派生表在本地均不存在：

- `derived/development/case_metrics.csv`
- `derived/development/curve_points.csv`

同时 `runs/development/` 目录不存在，本机没有可用于重建上述逐点表或 SR5 同步时间块的 G4 原始 `_devc.csv`。这些都是旧数据恢复问题，不是 FDS 新算例请求，也不是 PySR 依赖问题。现有 G4 验收记录直接沿用，不重复验证 FDS；批准执行补充协议并恢复两张开发表前不得运行正式搜索，SR5 还须恢复原始 `_devc.csv` 或由其生成的同步时间块表。锁箱继续保持封存。

## 6. 环境文件验证

- `uv lock --check`：通过，共解析 28 个 Python 包；
- 使用仓库锁定的 Python 3.11 环境执行全量测试：`239 passed, 148 subtests passed`；
- 唯一警告是既有 5 秒 FDS 输入仅适合启动检查的合同警告，与符号回归环境无关。
