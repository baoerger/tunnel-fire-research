#!/usr/bin/env bash
#SBATCH --job-name=fds_tunnel
#SBATCH --output=outputs/logs/fds_%A_%a.out
#SBATCH --error=outputs/logs/fds_%A_%a.err
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=1
#SBATCH --time=04:00:00
#SBATCH --mem=32G
#SBATCH --array=0-8

# SLURM array 模板。提交前：
#   1) 生成输入；2) mkdir -p outputs/logs；3) 将 --array 上界设为 CSV 数据行数-1；
#   4) sbatch --export=ALL,CASES_CSV=/abs/cases.csv,CASES_DIR=/abs/inputs run_slurm_template.sh
# 当前生成器输出单 MESH，默认每工况 1 task；多 MESH 输入才应增加 FDS_NTASKS。
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CASES_CSV="${CASES_CSV:-$HERE/../03_网格敏感性/grid_sensitivity_cases.csv}"
CASES_DIR="${CASES_DIR:-$HERE/../outputs/inputs}"
RUNS_DIR="${RUNS_DIR:-$HERE/../outputs/runs}"
FDS_NTASKS="${FDS_NTASKS:-1}"
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

module purge 2>/dev/null || true
if [ -n "${FDS_MODULE:-}" ]; then
  module load "$FDS_MODULE"
fi
export OMP_NUM_THREADS=1
RUN_DIR="$RUNS_DIR/$CHID"
mkdir -p "$RUN_DIR"
cp "$INPUT" "$RUN_DIR/$CHID.fds"
cd "$RUN_DIR"

echo "[$(date '+%F %T')] ${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID} -> $RUN_DIR"
if [ "$FDS_NTASKS" -eq 1 ]; then
  srun --ntasks=1 "$FDS_COMMAND" "$CHID.fds"
else
  srun --ntasks="$FDS_NTASKS" --mpi="${SLURM_MPI_TYPE:-pmix}" "$FDS_COMMAND" "$CHID.fds"
fi
echo "[$(date '+%F %T')] $CHID 完成。"
