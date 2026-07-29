#!/usr/bin/env bash
# run_case.sh — 运行一个 FDS 工况（默认单 mesh、单进程）
# 用法: ./run_case.sh <case.fds> [MPI 进程数] [运行根目录]
# FDS_BIN 可为 PATH 中的命令或绝对路径；多进程仅适用于输入已划分多个 MESH 的情况。
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "用法: $0 <case.fds> [nprocs] [运行根目录]" >&2
  exit 1
fi

INPUT="$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$1")"
NPROC="${2:-1}"
HERE="$(cd "$(dirname "$0")" && pwd)"
RUN_ROOT="$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "${3:-$HERE/../outputs/runs}")"
FDS_COMMAND="${FDS_BIN:-fds}"

if [ ! -f "$INPUT" ]; then
  echo "错误: 输入文件不存在: $INPUT" >&2
  exit 1
fi
if ! [[ "$NPROC" =~ ^[1-9][0-9]*$ ]]; then
  echo "错误: MPI 进程数必须为正整数: $NPROC" >&2
  exit 1
fi
if [[ "$FDS_COMMAND" == */* ]]; then
  FDS_COMMAND="$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$FDS_COMMAND")"
  if [ ! -x "$FDS_COMMAND" ]; then
    echo "错误: FDS_BIN 不可执行: $FDS_COMMAND" >&2
    exit 1
  fi
elif ! FDS_COMMAND="$(command -v "$FDS_COMMAND")"; then
  echo "错误: 找不到 FDS 可执行文件；请设置 FDS_BIN。" >&2
  exit 1
fi

FILE="$(basename "$INPUT")"
CHID="${FILE%.fds}"
DIR="$RUN_ROOT/$CHID"
mkdir -p "$DIR"
RUN_INPUT="$DIR/$FILE"
if [ "$INPUT" != "$RUN_INPUT" ]; then
  cp "$INPUT" "$RUN_INPUT"
fi

echo "[$(date '+%F %T')] 运行 $CHID | 进程=$NPROC | 目录=$DIR"
cd "$DIR"
if [ "$NPROC" -eq 1 ]; then
  time "$FDS_COMMAND" "$FILE"
else
  time mpiexec -n "$NPROC" "$FDS_COMMAND" "$FILE"
fi

echo "[$(date '+%F %T')] $CHID 完成。"
echo "  - 设备/能量: ${CHID}_devc.csv  ${CHID}_hrr.csv"
echo "  - 切片/边界: ${CHID}_*.sf*  ${CHID}_*.bf"
