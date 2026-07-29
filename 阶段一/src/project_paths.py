"""阶段一脚本共享的目录约定。

所有默认路径都锚定到“阶段一”目录，避免从不同工作目录启动脚本时把产物
散落到仓库各处。命令行参数仍可覆盖这些默认值。
"""
from pathlib import Path


STAGE_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_ROOT = STAGE_ROOT / "outputs"

FDS_INPUTS_DIR = OUTPUT_ROOT / "inputs"
FDS_RUNS_DIR = OUTPUT_ROOT / "runs"
ANALYSIS_DIR = OUTPUT_ROOT / "analysis"
SENSORS_DIR = OUTPUT_ROOT / "sensors"
LOGS_DIR = OUTPUT_ROOT / "logs"


def output_path(*parts):
    """返回 outputs 下的绝对路径字符串，供 argparse 默认值使用。"""
    return str(OUTPUT_ROOT.joinpath(*parts))
