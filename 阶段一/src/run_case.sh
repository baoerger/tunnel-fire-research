#!/usr/bin/env bash
# ============================================================================
# run_case.sh — 单个 FDS 工况运行封装（Linux/MPI）
#
# 用法:
#   ./run_case.sh <chid.fds 路径> [MPI 进程数]
# 例:
#   ./run_case.sh fds_cases/gsB_m.fds 8
#   ./run_case.sh fds_cases/gsB_m.fds        # 默认 8 进程
#
# 说明:
# - FDS 单 mesh 由 MPI 按域分解并行；进程数应 <= 网格单元总数且与节点核心匹配。
# - 若 FDS 可执行名不是 `fds`，用环境变量 FDS_BIN 覆盖，例：
#       FDS_BIN=/opt/FDS/fds6.7.9/bin/fds ./run_case.sh ...
# - 运行前请确认 fds 脚本(壳)在 PATH 中，或显式给定 FDS_BIN。
# - 输出与输入同目录（CHID_* 文件）。
# ============================================================================
set -euo pipefail

FDS_BIN="${FDS_BIN:-fds}"

if [ "$#" -lt 1 ]; then
  echo "用法: $0 <chid.fds> [nprocs]" >&2
  exit 1
fi

INPUT="$1"
NPROC="${2:-8}"

if [ ! -f "$INPUT" ]; then
  echo "错误: 输入文件不存在: $INPUT" >&2
  exit 1
fi

DIR="$(dirname "$INPUT")"
CHID="$(basename "$INPUT" .fds)"

echo "[$(date '+%F %T')] 运行 $CHID | 进程=$NPROC | 目录=$DIR"
cd "$DIR"

# 先做语法预检（几秒内可发现 .fds 错误，避免空跑）
if ! "$FDS_BIN" --validate "$INPUT" >/dev/null 2>&1; then
  echo "警告: --validate 未通过或该版本不支持；将直接运行。" >&2
fi

# 正式运行
time mpiexec -n "$NPROC" "$FDS_BIN" "$INPUT"

echo "[$(date '+%F %T')] $CHID 完成。"
echo "  - 设备/CSV: ${CHID}_devc.csv  ${CHID}_hrr.csv(若有)"
echo "  - 切片:     ${CHID}_*.sf?"
echo "  - 边界:     ${CHID}_bf?"
