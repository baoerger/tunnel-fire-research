"""
check_convection_ratio.py — 总/对流 HRR 与对流比例检查（§9 / §1.8）

输入
----
- FDS 运行结果目录中的 <chid>_hrr.csv（读取 HRR 与 Q_RADI）
- 或多个 chid 批量检查

逻辑
----
1. 从 _hrr.csv 读取 HRR（总）与 Q_RADI（负值表示辐射损失）时序；
2. 计算火源对流部分 Q_c = HRR + Q_RADI；
3. 计算 χ_r_realized = 1 - Q_c/Q；
4. 与预设 χ_r_preset 比较；统计 χ_r_realized 随 Q/风速/尺寸的变化。

决策门（§1.8）
- 若 Q_c/Q 随 Q/风速/尺寸明显系统性变化 → 建立对流比例修正关系；
- 不得在公式训练时使用仅 FDS 内部可得的对流 HRR，而在工程反演时默认已知。

用法:
  python check_convection_ratio.py --rundir outputs/runs \
      --chids gsA_m gsB_m gsC_m gsC_f \
      --outdir outputs/analysis/convection_ratio
"""
import os
import csv
import argparse

import tunnel_config as cfg
import fds_io
from project_paths import FDS_RUNS_DIR, output_path


def _avg_in_window(arr, idx):
    good = [arr[i] for i in idx if i < len(arr) and arr[i] == arr[i]]
    return sum(good) / len(good) if good else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rundir", default=str(FDS_RUNS_DIR),
                    help="FDS 运行根目录（默认: outputs/runs）")
    ap.add_argument("--chids", nargs="+", required=True)
    ap.add_argument("--outdir", default=output_path("analysis", "convection_ratio"))
    ap.add_argument("--t0", type=float, default=None, help="准稳态窗口起点")
    ap.add_argument("--t1", type=float, default=None, help="准稳态窗口终点")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    rows = []
    for chid in args.chids:
        times, series, units = fds_io.read_hrr(args.rundir, chid)
        series = fds_io.normalize_units(series, units)
        if times is None:
            print(f"[WARN] 无 {chid}_hrr.csv")
            continue
        tot_id = _find_key(series, ["HRR"])
        radi_id = _find_key(series, ["Q_RADI"])
        if not tot_id or not radi_id:
            print(f"[WARN] {chid}: _hrr.csv 缺少 HRR 或 Q_RADI 列")
            continue
        idx = list(range(len(times)))
        if args.t0 is not None and args.t1 is not None:
            idx = [i for i, t in enumerate(times) if args.t0 <= t <= args.t1]
        Q_tot = _avg_in_window(series[tot_id], idx)
        Q_radi = _avg_in_window(series[radi_id], idx)
        Q_conv = Q_tot + Q_radi
        chi_r = -Q_radi / Q_tot if Q_tot else float("nan")
        rows.append(dict(chid=chid, Q_tot_kW=Q_tot, Q_conv_kW=Q_conv,
                         chi_r_realized=chi_r, chi_r_preset=cfg.CHI_R_PRESET))

    path = os.path.join(args.outdir, "convection_ratio.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["chid", "Q_tot_kW", "Q_conv_kW",
                                          "chi_r_realized", "chi_r_preset"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"[OK] -> {path}")

    print("\n=== 决策门（§1.8）===")
    chis = [r["chi_r_realized"] for r in rows
            if r["chi_r_realized"] == r["chi_r_realized"]]
    if chis:
        print(f"  χ_r_realized: min={min(chis):.3f}  max={max(chis):.3f}")
        print(f"  χ_r_preset = {cfg.CHI_R_PRESET:.3f}")
        if len(chis) < 2:
            print("  => 样本不足，只报告 χ_r，不判定跨工况稳定性")
        else:
            spread = max(chis) - min(chis)
            print(f"  极差={spread:.3f}")
            if spread > 0.05:
                print("  => χ_r 随工况系统性变化 → 建立对流比例修正关系（见 §1.8）")
            else:
                print("  => χ_r 基本稳定 → 固定 χ_r 合理，但仍以 FDS 实测值为准")


def _find_key(series, candidates):
    """大小写/空白无关的列名匹配。"""
    norm = {k.strip().upper().replace(" ", "_"): k for k in series}
    for c in candidates:
        key = c.strip().upper().replace(" ", "_")
        if key in norm:
            return norm[key]
    return None


if __name__ == "__main__":
    main()
