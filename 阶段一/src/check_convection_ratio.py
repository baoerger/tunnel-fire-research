"""
check_convection_ratio.py — 总/对流 HRR 与对流比例检查（§9 / §1.8）

输入
----
- FDS 运行结果目录中的 <chid>_devc.csv（须含 HRR_tot、HRR_conv 设备列）
- 或多个 chid 批量检查

逻辑
----
1. 读取 HRR_tot(总) 与 HRR_conv(对流) 时序；
2. 在准稳态窗口内取时间平均；
3. 计算 χ_r_realized = 1 - Q_c/Q；
4. 与预设 χ_r_preset 比较；统计 χ_r_realized 随 Q/风速/尺寸的变化。

决策门（§1.8）
- 若 Q_c/Q 随 Q/风速/尺寸明显系统性变化 → 建立对流比例修正关系；
- 不得在公式训练时使用仅 FDS 内部可得的对流 HRR，而在工程反演时默认已知。

用法:
  python check_convection_ratio.py --rundir fds_cases \
      --chids gsA_m gsB_m gsC_m gsC_f \
      --outdir results/hrr
"""
import os
import csv
import argparse

import tunnel_config as cfg
import fds_io


def _avg_in_window(arr, idx):
    good = [arr[i] for i in idx if i < len(arr) and arr[i] == arr[i]]
    return sum(good) / len(good) if good else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rundir", required=True)
    ap.add_argument("--chids", nargs="+", required=True)
    ap.add_argument("--outdir", default="results/hrr")
    ap.add_argument("--t0", type=float, default=None, help="准稳态窗口起点")
    ap.add_argument("--t1", type=float, default=None, help="准稳态窗口终点")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    rows = []
    for chid in args.chids:
        times, series, units = fds_io.read_devc(args.rundir, chid)
        series = fds_io.normalize_units(series, units)   # §1.7 选项 B：归一到 °C/kW/kW·m⁻²
        if times is None:
            print(f"[WARN] 无 {chid}_devc.csv")
            continue
        # 模糊匹配 HRR 列（版本兼容）
        tot_id = _find_key(series, ["HRR_tot", "HRR TOTAL", "TOTAL HRR"])
        conv_id = _find_key(series, ["HRR_conv", "CONVECTIVE HRR", "CONVECTIVE", "HRR CONV"])
        if not tot_id:
            print(f"[WARN] {chid}: 未找到总 HRR 设备列")
            continue
        idx = list(range(len(times)))
        if args.t0 is not None and args.t1 is not None:
            idx = [i for i, t in enumerate(times) if args.t0 <= t <= args.t1]
        Q_tot = _avg_in_window(series[tot_id], idx)
        if conv_id:
            Q_conv = _avg_in_window(series[conv_id], idx)
            chi_r = 1.0 - Q_conv / Q_tot if Q_tot else float("nan")
        else:
            Q_conv = float("nan")
            chi_r = float("nan")
            print(f"[WARN] {chid}: 未找到对流 HRR 列（量名见 tunnel_config.QUANTITY_HRR_CONV；"
                  f"可由 HRR-辐射损失 推算，见 MODEL_SETTINGS.md §1.8）")
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
    chis = [r["chi_r_realized"] for r in rows if r["chi_r_realized"] == r["chi_r_realized"]]
    if chis:
        spread = max(chis) - min(chis)
        print(f"  χ_r_realized: min={min(chis):.3f}  max={max(chis):.3f}  极差={spread:.3f}")
        print(f"  χ_r_preset = {cfg.CHI_R_PRESET:.3f}")
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
    # 退化：任一含 'CONV' 的列
    for k in series:
        if "CONV" in k.upper():
            return k
    return None


if __name__ == "__main__":
    main()
