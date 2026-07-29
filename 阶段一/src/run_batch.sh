#!/usr/bin/env bash
# run_batch.sh — 按 CSV 精确生成并运行本批 FDS 工况
# 用法: ./run_batch.sh <cases.csv> [输入目录] [MPI 进程数] [并发数] [运行根目录]
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "用法: $0 <cases.csv> [输入目录] [nprocs] [并发数] [运行根目录]" >&2
  exit 1
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
CSV="$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$1")"
INPUT_DIR="$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "${2:-$HERE/../outputs/inputs}")"
NPROC="${3:-1}"
CONCURRENCY="${4:-1}"
RUN_ROOT="$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "${5:-$HERE/../outputs/runs}")"

if [ ! -f "$CSV" ]; then
  echo "错误: 工况表不存在: $CSV" >&2
  exit 1
fi
if ! [[ "$NPROC" =~ ^[1-9][0-9]*$ && "$CONCURRENCY" =~ ^[1-9][0-9]*$ ]]; then
  echo "错误: nprocs 和并发数必须为正整数。" >&2
  exit 1
fi

mkdir -p "$INPUT_DIR" "$RUN_ROOT"
echo "=== 生成 FDS 输入文件 -> $INPUT_DIR ==="
python3 "$HERE/generate_fds_case.py" --csv "$CSV" --outdir "$INPUT_DIR"

mapfile -t CHIDS < <(python3 -c 'import csv,sys
with open(sys.argv[1], newline="", encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        print(row["chid"].strip())' "$CSV")
CASES=()
for chid in "${CHIDS[@]}"; do
  input="$INPUT_DIR/$chid.fds"
  if [ ! -f "$input" ]; then
    echo "错误: 生成后缺少工况输入: $input" >&2
    exit 1
  fi
  CASES+=("$input")
done

echo "=== 运行 ${#CASES[@]} 个工况（并发=$CONCURRENCY, 进程/工况=$NPROC） ==="
run_one() {
  bash "$HERE/run_case.sh" "$1" "$NPROC" "$RUN_ROOT"
}
export -f run_one
export HERE NPROC RUN_ROOT

if [ "$CONCURRENCY" -eq 1 ]; then
  for input in "${CASES[@]}"; do
    run_one "$input"
  done
else
  printf '%s\0' "${CASES[@]}" | xargs -0 -n 1 -P "$CONCURRENCY" bash -c 'run_one "$1"' _
fi

echo "=== 全部完成。输入在 $INPUT_DIR；运行结果在 $RUN_ROOT/<chid> ==="
