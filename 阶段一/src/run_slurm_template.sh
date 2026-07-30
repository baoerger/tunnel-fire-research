#!/usr/bin/env bash
#SBATCH --job-name=fds_tunnel
#SBATCH --output=outputs/logs/fds_%A_%a.out
#SBATCH --error=outputs/logs/fds_%A_%a.err
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=22
#SBATCH --cpus-per-task=2
#SBATCH --time=04:00:00
#SBATCH --mem=32G
#SBATCH --array=0-8

# SLURM array 模板。提交前：
#   1) 生成输入；2) mkdir -p outputs/logs；3) 将 --array 上界设为 CSV 数据行数-1；
#   4) sbatch --export=ALL,CASES_CSV=/abs/cases.csv,CASES_DIR=/abs/inputs run_slurm_template.sh
# 默认资源对应当前网格敏感性输入的 22 MESH：22 MPI × 2 OpenMP。
# 可用 --export=...,FDS_NTASKS=22,FDS_OMP_THREADS=1 或 11/3 做同一短算例对照。
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CASES_CSV="${CASES_CSV:-$HERE/../03_网格敏感性/grid_sensitivity_cases.csv}"
CASES_DIR="${CASES_DIR:-$HERE/../outputs/inputs}"
RUNS_DIR="${RUNS_DIR:-$HERE/../outputs/runs}"
FDS_NTASKS="${FDS_NTASKS:-22}"
FDS_OMP_THREADS="${FDS_OMP_THREADS:-2}"
FDS_COMMAND="${FDS_BIN:-fds}"

if [ ! -f "$CASES_CSV" ]; then
  echo "工况表不存在: $CASES_CSV" >&2
  exit 1
fi
CHID="$(python3 -c 'import csv,sys
index=int(sys.argv[2])
with open(sys.argv[1], newline="", encoding="utf-8-sig") as f:
    rows=list(csv.DictReader(f))
if index >= len(rows):
    raise SystemExit(f"数组下标越界: {index} >= {len(rows)}")
print(rows[index]["chid"].strip())' "$CASES_CSV" "$SLURM_ARRAY_TASK_ID")"
INPUT="$CASES_DIR/$CHID.fds"
if [ ! -f "$INPUT" ]; then
  echo "输入文件不存在: $INPUT" >&2
  exit 1
fi
if ! [[ "$FDS_NTASKS" =~ ^[1-9][0-9]*$ && "$FDS_OMP_THREADS" =~ ^[1-9][0-9]*$ ]]; then
  echo "FDS_NTASKS 和 FDS_OMP_THREADS 必须为正整数。" >&2
  exit 1
fi
MESH_COUNT="$(grep -Ec '^[[:space:]]*&MESH([[:space:]]|$)' "$INPUT" || true)"
if [ "$MESH_COUNT" -lt 1 ]; then
  echo "输入中未找到 MESH: $INPUT" >&2
  exit 1
fi
if [ "$FDS_NTASKS" -gt "$MESH_COUNT" ]; then
  echo "MPI 任务数 $FDS_NTASKS 超过 MESH 数 $MESH_COUNT，拒绝运行。" >&2
  exit 1
fi
if [ -n "${SLURM_NTASKS:-}" ] && [ "$FDS_NTASKS" -gt "$SLURM_NTASKS" ]; then
  echo "请求运行 $FDS_NTASKS 个 MPI 任务，但作业只分配了 $SLURM_NTASKS。" >&2
  exit 1
fi

module purge 2>/dev/null || true
if [ -n "${FDS_MODULE:-}" ]; then
  module load "$FDS_MODULE"
fi
export OMP_NUM_THREADS="$FDS_OMP_THREADS"
RUN_DIR="$RUNS_DIR/$CHID"
mkdir -p "$RUN_DIR"
cp "$INPUT" "$RUN_DIR/$CHID.fds"
cd "$RUN_DIR"

echo "[$(date '+%F %T')] ${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID} -> $RUN_DIR | MESH=$MESH_COUNT MPI=$FDS_NTASKS OpenMP=$OMP_NUM_THREADS"
if [ "$FDS_NTASKS" -eq 1 ]; then
  srun --ntasks=1 --cpus-per-task="$FDS_OMP_THREADS" "$FDS_COMMAND" "$CHID.fds"
else
  srun --ntasks="$FDS_NTASKS" --cpus-per-task="$FDS_OMP_THREADS" \
    --cpu-bind=cores --mpi="${SLURM_MPI_TYPE:-pmix}" "$FDS_COMMAND" "$CHID.fds"
fi
echo "[$(date '+%F %T')] $CHID 完成。"
