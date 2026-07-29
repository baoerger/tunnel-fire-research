"""
generate_sensor_layout.py — 顶棚中心线测点布置生成器（§1.7）

产出
----
1. sensor_layout.csv        全部密集测点（20~30 个）：ID、x、y、z_temp、z_vel、分区
2. sparse_subsets.csv       稀疏传感器子集（4/8/12/16）的默认采样，供阶段五 §5.4
                            “均匀布局”初值；Fisher 优化布局由 §5.4 单独确定。
3. sensor_layout.png        隧道纵向剖面 + 测点 + 火源 示意图（需 matplotlib）

设计（§1.7）
- 沿顶棚中心线布 20~30 个虚拟测点，全部落在均匀测量区内；
- 火源可能区附近间距 0.25H~0.5H（≈1.25~2.5 m）；
- 远离火源后放宽至 H~2H（≈5~10 m）；
- 测点高度统一设于顶棚下方 0.05H~0.10H（此处 0.10H = 0.5 m，z=4.5 m）；
- 保证上下游衰减段各至少 3~4 个有效测点用于 κ_u、κ_d 拟合。

注：本脚本仅输出布置；FDS 工况中测点已由 generate_fds_case.py 内嵌同一
sensor_layout() 生成，二者口径一致。
"""
import os
import csv
import argparse

import tunnel_config as cfg


def _id(x):
    return f"T_{int(round(x * 100)):04d}"


def _region_of(x, fire_x, height=cfg.H):
    """分区：near（火源±4H）、far_up、far_down。"""
    if abs(x - fire_x) <= 4.0 * height:
        return "near"
    return "far_up" if x < fire_x else "far_down"


def build_layout(fire_x=None, region=None, length=cfg.L, width=cfg.W, height=cfg.H):
    if fire_x is None:
        fire_x = length / 2.0
    if region is None:
        region = cfg.measurement_region(length, height)
    z_temp, z_vel = cfg.sensor_heights(height)
    xs = cfg.sensor_layout(height=height, fire_x=fire_x, region=region)
    rows = []
    for x in xs:
        rows.append({
            "id": _id(x),
            "x": round(x, 3),
            "y": round(width / 2.0, 3),
            "z_temp": round(z_temp, 3),
            "z_vel": round(z_vel, 3),
            "region": _region_of(x, fire_x, height),
        })
    return rows


def default_sparse_subset(rows, n):
    """
    从密集测点中均匀抽取 n 个（§5.4 “均匀布局”初值）。
    采用等间距下标采样，保证覆盖测量区两端。
    返回 id 列表。
    """
    m = len(rows)
    if n >= m:
        return [r["id"] for r in rows]
    idxs = [int(round(i * (m - 1) / (n - 1))) for i in range(n)] if n > 1 else [m // 2]
    idxs = sorted(set(idxs))
    return [rows[i]["id"] for i in idxs]


def plot_layout(rows, fire_x, outpath, length=cfg.L, height=cfg.H):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("[WARN] matplotlib 不可用，跳过绘图。")
        return False

    xs = [r["x"] for r in rows]
    z = rows[0]["z_temp"] if rows else 0.90 * height
    fig, ax = plt.subplots(figsize=(14, 3.2))
    # 隧道剖面
    ax.add_patch(plt.Rectangle((cfg.X0, cfg.Z0), length, height,
                               fill=False, ec="k", lw=1.5))
    # 测点
    for r in rows:
        c = {"near": "C3", "far_up": "C0", "far_down": "C2"}.get(r["region"], "k")
        ax.plot(r["x"], r["z_temp"], "o", color=c, ms=4)
    # 火源
    ax.plot([fire_x], [cfg.Z0], marker="X", color="darkred", ms=12)
    # 测量区
    lo, hi = cfg.measurement_region(length, height)
    ax.axvline(lo, ls="--", c="gray", lw=0.8)
    ax.axvline(hi, ls="--", c="gray", lw=0.8)
    ax.set_xlim(cfg.X0 - 0.02 * length, length + 0.02 * length)
    ax.set_ylim(-0.2 * height, 1.3 * height)
    ax.set_xlabel("x [m] (纵向)")
    ax.set_ylabel("z [m]")
    ax.set_title(f"顶棚中心线测点布置（{len(rows)} 个）· z_temp={z:.2f} m (顶棚下方 {height - z:.2f} m)")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(outpath, dpi=150)
    plt.close(fig)
    return True


def main():
    ap = argparse.ArgumentParser(description="生成顶棚中心线测点布置")
    ap.add_argument("--fire_x", type=float, default=None, help="火源 x 位置，默认隧道中部")
    ap.add_argument("--L", type=float, default=cfg.L, help="隧道长度 [m]")
    ap.add_argument("--W", type=float, default=cfg.W, help="隧道宽度 [m]")
    ap.add_argument("--H", type=float, default=cfg.H, help="隧道高度 [m]")
    ap.add_argument("--outdir", default=".", help="输出目录")
    args = ap.parse_args()

    fire_x = args.fire_x if args.fire_x is not None else args.L / 2.0
    region = cfg.measurement_region(args.L, args.H)
    rows = build_layout(fire_x=fire_x, region=region,
                        length=args.L, width=args.W, height=args.H)

    os.makedirs(args.outdir, exist_ok=True)
    csv_path = os.path.join(args.outdir, "sensor_layout.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["id", "x", "y", "z_temp", "z_vel", "region"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"[OK] 测点 {len(rows)} 个 -> {csv_path}")

    # 稀疏子集（均匀初值）
    sub_path = os.path.join(args.outdir, "sparse_subsets.csv")
    with open(sub_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["n_sensors", "layout_type", "sensor_ids"])
        for n in [4, 8, 12, 16]:
            ids = default_sparse_subset(rows, n)
            w.writerow([n, "uniform_default", ";".join(ids)])
    print(f"[OK] 稀疏子集(4/8/12/16) -> {sub_path}")

    png = os.path.join(args.outdir, "sensor_layout.png")
    if plot_layout(rows, fire_x, png, length=args.L, height=args.H):
        print(f"[OK] 示意图 -> {png}")
    print(f"测点 x: {[r['x'] for r in rows]}")


if __name__ == "__main__":
    main()
