#!/usr/bin/env bash
# ============================================================================
# run_batch.sh — 按 CSV 工况表批量生成并运行 FDS 工况（Linux/MPI）
#
# 用法:
#   ./run_batch.sh <cases.csv> <输出目录> [MPI 进程数] [并发数]
# 例:
#   ./run_batch.sh ../03_网格敏感性/grid_sensitivity_cases.csv fds_cases 8 1
#
# 流程:
#   1. python generate_fds_case.py --csv <cases.csv> --outdir <输出目录>
#      （逐行生成 .fds）
#   2. 对每个 .fds 调用 run_case.sh 串行/有限并发运行
#
# 依赖: python3, mpiexec, FDS(fds)。前置于 HPC/Linux 平台执行。
# ============================================================================
set -euo pipefail

CSV="$1"
OUTDIR="${2:-fds_cases}"
NPROC="${3:-8}"
CONCURRENCY="${4:-1}"

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

# 1) 生成 .fds
echo "=== 生成 FDS 输入文件 -> $OUTDIR ==="
python3 generate_fds_case.py --csv "$CSV" --outdir "$OUTDIR"

# 2) 运行
echo "=== 运行（并发=$CONCURRENCY, 进程/工况=$NPROC） ==="
# 注意：单节点多工况并发会竞争内存与核心；建议按节点内存/CPU 谨慎设置并发。
run_one() {
  local f="$1"
  bash run_case.sh "$f" "$NPROC" || echo "!! 失败: $f" >&2
}
export -f run_one
export NPROC

if [ "$CONCURRENCY" -le 1 ]; then
  for f in "$OUTDIR"/*.fds; do run_one "$f"; done
else
  # xargs 限并发；进程数仍由各工况的 $NPROC 控制
  ls "$OUTDIR"/*.fds | xargs -I{} -P "$CONCURRENCY" bash -c 'run_one "$@"' _ {}
fi

echo "=== 全部完成。输出在 $OUTDIR ==="
echo "提示: 集群环境建议改用 SLURM array job（见 run_slurm_template.sh）。"
