"""项目共享目录约定；所有默认路径均锚定到仓库根目录。"""
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
# 兼容旧调用名；其含义现在是项目根而非“阶段一”。
STAGE_ROOT = PROJECT_ROOT
OUTPUT_ROOT = PROJECT_ROOT / "derived"

FDS_INPUTS_DIR = PROJECT_ROOT / "fds_inputs"
FDS_RUNS_DIR = PROJECT_ROOT / "runs"
ANALYSIS_DIR = PROJECT_ROOT / "derived"
SENSORS_DIR = PROJECT_ROOT / "config"
LOGS_DIR = PROJECT_ROOT / "reports" / "logs"


def output_path(*parts):
    """返回 ``derived/`` 下的绝对路径字符串，供 argparse 使用。"""
    return str(OUTPUT_ROOT.joinpath(*parts))
