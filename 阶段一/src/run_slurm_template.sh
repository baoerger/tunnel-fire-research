#!/usr/bin/env bash
#SBATCH --job-name=fds_tunnel
#SBATCH --output=logs/fds_%A_%a.out
#SBATCH --error=logs/fds_%A_%a.err
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=8          # MPI 进程数 = 单工况并行核数；按节点调整
#SBATCH --cpus-per-task=1
#SBATCH --time=04:00:00              # 单工况壁钟上限；细网格/高功率需上调
#SBATCH --mem=32G                    # 单工况内存；细网格需上调
#SBATCH --array=0-8                  # 工况数组下标；按 CSV 行数调整(0-based，跳过表头)

# ============================================================================
# SLURM array job 模板：每个数组任务运行一个 FDS 工况。
# 用法:
#   1) 先生成 .fds:  python3 generate_fds_case.py --csv <cases.csv> --outdir fds_cases
#   2) 收集 .fds 列表并据此设置 --array 上界（行数-1，跳过表头）
#   3) sbatch run_slurm_template.sh
# 例（9 工况网格敏感性）: --array=0-8
# ============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

CASES=(fds_cases/*.fds)
N=${#CASES[@]}
if [ "$SLURM_ARRAY_TASK_ID" -ge "$N" ]; then
  echo "数组下标越界: $SLURM_ARRAY_TASK_ID >= $N" >&2
  exit 1
fi
INPUT="${CASES[$SLURM_ARRAY_TASK_ID]}"

echo "[$(date '+%F %T')] 任务 ${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID} -> $(basename "$INPUT")"
module purge 2>/dev/null || true
module load fds 2>/dev/null || export FDS_BIN="${FDS_BIN:-fds}"

export OMP_NUM_THREADS=1
srun --mpi=pmix "${FDS_BIN:-fds}" "$INPUT"
echo "[$(date '+%F %T')] 完成。"
