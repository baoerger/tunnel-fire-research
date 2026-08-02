"""用正式 PBY 温度切片诊断强风工况的下游测量域与出口前衰减。

该工具只形成全场诊断证据，不替代 DEVC 正式判伪结果。多 MESH 重合节点取
算术平均；每个工况仍使用阶段一已判定的自身准稳态窗口。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import math
import re
from pathlib import Path

import numpy as np


STAGE2_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = STAGE2_ROOT.parent
DEFAULT_RUN_ROOT = PROJECT_ROOT / "阶段一" / "outputs" / "extrainput"
DEFAULT_WINDOWS = (
    PROJECT_ROOT / "阶段一" / "outputs" / "analysis" / "extra_four"
    / "steady" / "steady_windows.csv"
)
DEFAULT_DEVICE_PROFILES = (
    PROJECT_ROOT / "阶段一" / "outputs" / "analysis" / "extra_four"
    / "time_average" / "time_average_database.csv"
)
DEFAULT_OUTDIR = STAGE2_ROOT / "outputs" / "longitudinal_domain_diagnostics"
DEFAULT_CHIDS = (
    "pilot_diag_q100_r160_d70_x41",
    "pilot_diag_q100_r160_d80_x41",
)
PROFILE_FIELDS = (
    "chid", "x_m", "z_m", "t0_s", "t1_s", "n_slice_frames",
    "T_mean_C", "DeltaT_bar_C", "temporal_sd_C", "data_origin",
)
SUMMARY_FIELDS = (
    "chid", "fds_version", "t0_s", "t1_s", "n_slice_frames", "z_m",
    "x_peak_15_85_m", "DeltaT_peak_15_85_C", "DeltaT_85_C",
    "DeltaT_90_C", "DeltaT_95_C", "DeltaT_99_C",
    "tail_85_99_log_decay_k_1_m", "tail_85_99_log_r2",
    "tail_85_99_drop_fraction", "device_slice_nrmse",
    "mesh_overlap_node_count", "status", "interpretation_boundary",
)
MANIFEST_FIELDS = (
    "chid", "fds_input_sha256", "smv_sha256", "slice_files_sha256",
    "slice_file_count", "fdsreader_version", "profile_rows_sha256",
)


def _load_fdsreader():
    try:
        import fdsreader
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "缺少 fdsreader；请使用 fdsreader==1.11.7 运行本诊断"
        ) from exc
    return fdsreader


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _combined_hash(root, paths):
    root = Path(root).resolve()
    digest = hashlib.sha256()
    unique = sorted({Path(path).resolve() for path in paths}, key=lambda item: item.as_posix())
    for path in unique:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(_sha256_file(path)))
        digest.update(b"\0")
    return digest.hexdigest(), len(unique)


def _ambient_temperature_C(fds_path):
    text = Path(fds_path).read_text(encoding="utf-8-sig")
    matches = re.findall(
        r"&MISC\b[^/]*?\bTMPA\s*=\s*([-+0-9.eEdD]+)",
        text, re.IGNORECASE | re.DOTALL,
    )
    if len(matches) != 1:
        raise ValueError(f"{fds_path}: 必须有唯一 MISC TMPA")
    value = float(matches[0].replace("D", "E").replace("d", "e"))
    if not math.isfinite(value):
        raise ValueError(f"{fds_path}: TMPA 不是有限值")
    return value


def _coordinate_indices(global_coordinates, local_coordinates, tolerance=2e-6):
    indices = []
    for coordinate in np.asarray(local_coordinates, dtype=float):
        matches = np.flatnonzero(np.isclose(
            global_coordinates, coordinate, rtol=0.0, atol=tolerance
        ))
        if matches.size != 1:
            raise ValueError(f"坐标 {coordinate} 无法唯一映射到纵向切片全局网格")
        indices.append(int(matches[0]))
    return indices


def assemble_longitudinal_slice(slice_object):
    """组装节点型 PBY 标量切片，并平均多 MESH 重合节点。"""
    if slice_object.orientation != 2 or slice_object.cell_centered:
        raise ValueError("仅支持节点型 PBY 纵向切片")
    coordinates = slice_object.get_coordinates(ignore_cell_centered=False)
    xs = np.asarray(coordinates["x"], dtype=float)
    zs = np.asarray(coordinates["z"], dtype=float)
    times = np.asarray(slice_object.times, dtype=float)
    if xs.size < 2 or zs.size < 2 or times.size < 2:
        raise ValueError("纵向切片坐标或时间轴不足")
    sums = np.zeros((times.size, xs.size, zs.size), dtype=float)
    counts = np.zeros((xs.size, zs.size), dtype=int)
    source_files = []
    for subslice in slice_object.subslices:
        local = subslice.get_coordinates(ignore_cell_centered=False)
        x_indices = _coordinate_indices(xs, local["x"])
        z_indices = _coordinate_indices(zs, local["z"])
        data = np.asarray(subslice.data, dtype=float)
        expected = (times.size, len(x_indices), len(z_indices))
        if data.shape != expected:
            raise ValueError(f"子切片数据形状 {data.shape} 与坐标 {expected} 不一致")
        for local_x, global_x in enumerate(x_indices):
            for local_z, global_z in enumerate(z_indices):
                sums[:, global_x, global_z] += data[:, local_x, local_z]
                counts[global_x, global_z] += 1
        source_files.append(Path(slice_object._root_path) / subslice.filename)
    if np.any(counts == 0):
        raise ValueError("多 MESH 纵向切片组装后存在空洞")
    values = sums / counts[np.newaxis, :, :]
    if not np.all(np.isfinite(values)):
        raise ValueError("纵向切片包含非有限值")
    overlap_count = int(np.count_nonzero(counts > 1))
    return times, xs, zs, values, overlap_count, source_files


def _read_index(path, key):
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    result = {}
    for row in rows:
        value = str(row.get(key) or "").strip()
        if not value:
            raise ValueError(f"{path}: {key} 为空")
        result.setdefault(value, []).append(row)
    return result


def _nearest_value(xs, values, x):
    index = int(np.argmin(np.abs(xs - x)))
    if abs(float(xs[index]) - x) > 0.126:
        raise ValueError(f"纵向切片没有接近 x={x:g} m 的节点")
    return float(values[index])


def _linear_log_metrics(xs, values, lo=85.0, hi=99.0):
    selected = [(float(x), math.log(float(value))) for x, value in zip(xs, values)
                if lo <= x <= hi and value > 0]
    if len(selected) < 3:
        return math.nan, math.nan
    mean_x = sum(row[0] for row in selected) / len(selected)
    mean_y = sum(row[1] for row in selected) / len(selected)
    denominator = sum((row[0] - mean_x) ** 2 for row in selected)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in selected) / denominator
    intercept = mean_y - slope * mean_x
    observed = [row[1] for row in selected]
    predicted = [intercept + slope * row[0] for row in selected]
    total = sum((value - mean_y) ** 2 for value in observed)
    r2 = 1.0 - sum((a - b) ** 2 for a, b in zip(observed, predicted)) / total if total else 1.0
    return -slope, r2


def _device_slice_nrmse(chid, xs, profile, device_rows):
    rows = device_rows.get(chid, [])
    if not rows:
        raise ValueError(f"{chid}: 缺少 DEVC 时间平均剖面用于切片交叉检查")
    pairs = []
    for row in rows:
        x = float(row["x"])
        observed = float(row["T_bar"])
        predicted = float(np.interp(x, xs, profile))
        pairs.append((observed, predicted))
    scale = max(abs(row[0]) for row in pairs)
    if scale <= 0:
        raise ValueError(f"{chid}: DEVC 温升剖面退化")
    return math.sqrt(sum((a - b) ** 2 for a, b in pairs) / len(pairs)) / scale


def _canonical_profile_hash(rows):
    from io import StringIO
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=PROFILE_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return hashlib.sha256(stream.getvalue().encode("utf-8")).hexdigest()


def analyze_case(case_dir, window_row, device_rows, z_m=4.5, fdsreader_module=None):
    case_dir = Path(case_dir).resolve()
    chid = case_dir.name
    fds_path = case_dir / f"{chid}.fds"
    smv_path = case_dir / f"{chid}.smv"
    if not fds_path.is_file() or not smv_path.is_file():
        raise FileNotFoundError(f"{case_dir}: 缺少 .fds 或 .smv")
    t0 = float(window_row["avg_t0"])
    t1 = float(window_row["avg_t1"])
    if window_row.get("status", "").upper() != "PASS" or not t1 > t0:
        raise ValueError(f"{chid}: 准稳态窗口未通过")
    ambient = _ambient_temperature_C(fds_path)
    module = fdsreader_module or _load_fdsreader()
    simulation = module.Simulation(str(smv_path))
    candidates = [item for item in simulation.slices
                  if item.orientation == 2 and not item.cell_centered
                  and item.quantity.name.upper() == "TEMPERATURE"]
    if len(candidates) != 1:
        raise ValueError(f"{chid}: 节点型 PBY TEMPERATURE 切片数量为 {len(candidates)}")
    slice_object = candidates[0]
    if slice_object.quantity.unit != "C":
        raise ValueError(f"{chid}: 温度切片单位不是 C")
    times, xs, zs, values, overlap_count, source_files = assemble_longitudinal_slice(slice_object)
    z_matches = np.flatnonzero(np.isclose(zs, z_m, rtol=0.0, atol=2e-6))
    if z_matches.size != 1:
        raise ValueError(f"{chid}: z={z_m:g} m 无法唯一定位")
    time_indices = np.flatnonzero((times >= t0) & (times <= t1))
    if time_indices.size < 4:
        raise ValueError(f"{chid}: 准稳态窗口内温度切片少于 4 帧")
    samples = values[time_indices, :, int(z_matches[0])]
    mean_C = np.mean(samples, axis=0)
    delta_T = mean_C - ambient
    temporal_sd = np.std(samples, axis=0, ddof=1)
    rows = [{
        "chid": chid, "x_m": f"{x:.6f}", "z_m": f"{z_m:.6f}",
        "t0_s": f"{t0:.6f}", "t1_s": f"{t1:.6f}",
        "n_slice_frames": int(time_indices.size), "T_mean_C": f"{temperature:.9f}",
        "DeltaT_bar_C": f"{rise:.9f}", "temporal_sd_C": f"{sd:.9f}",
        "data_origin": "FORMAL_FDS_6_9_1_FULL_FIELD_DIAGNOSTIC",
    } for x, temperature, rise, sd in zip(xs, mean_C, delta_T, temporal_sd)]
    core = np.flatnonzero((xs >= 15.0) & (xs <= 85.0))
    peak_index = int(core[np.argmax(delta_T[core])])
    d85 = _nearest_value(xs, delta_T, 85.0)
    d99 = _nearest_value(xs, delta_T, 99.0)
    tail_k, tail_r2 = _linear_log_metrics(xs, delta_T)
    summary = {
        "chid": chid, "fds_version": str(getattr(simulation, "fds_version", "")),
        "t0_s": f"{t0:.6f}", "t1_s": f"{t1:.6f}",
        "n_slice_frames": int(time_indices.size), "z_m": f"{z_m:.6f}",
        "x_peak_15_85_m": f"{float(xs[peak_index]):.6f}",
        "DeltaT_peak_15_85_C": f"{float(delta_T[peak_index]):.9f}",
        "DeltaT_85_C": f"{d85:.9f}",
        "DeltaT_90_C": f"{_nearest_value(xs, delta_T, 90.0):.9f}",
        "DeltaT_95_C": f"{_nearest_value(xs, delta_T, 95.0):.9f}",
        "DeltaT_99_C": f"{d99:.9f}",
        "tail_85_99_log_decay_k_1_m": f"{tail_k:.9f}",
        "tail_85_99_log_r2": f"{tail_r2:.9f}",
        "tail_85_99_drop_fraction": f"{((d85 - d99) / d85):.9f}",
        "device_slice_nrmse": f"{_device_slice_nrmse(chid, xs, delta_T, device_rows):.9f}",
        "mesh_overlap_node_count": overlap_count,
        "status": "PASS_DIAGNOSTIC_ONLY",
        "interpretation_boundary": (
            "full-field slice is diagnostic; values inside the 15 m outlet buffer cannot "
            "establish boundary independence or replace a longer-domain FDS comparison"
        ),
    }
    slice_hash, slice_count = _combined_hash(case_dir, source_files)
    manifest = {
        "chid": chid, "fds_input_sha256": _sha256_file(fds_path),
        "smv_sha256": _sha256_file(smv_path), "slice_files_sha256": slice_hash,
        "slice_file_count": slice_count,
        "fdsreader_version": str(getattr(module, "__version__", "")),
        "profile_rows_sha256": _canonical_profile_hash(rows),
    }
    return rows, summary, manifest


def _write_rows(path, fields, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def run_diagnostics(run_root=DEFAULT_RUN_ROOT, windows_path=DEFAULT_WINDOWS,
                    device_profiles_path=DEFAULT_DEVICE_PROFILES, outdir=DEFAULT_OUTDIR,
                    chids=DEFAULT_CHIDS, z_m=4.5, fdsreader_module=None):
    windows = _read_index(windows_path, "chid")
    devices = _read_index(device_profiles_path, "chid")
    all_profiles, summaries, manifests = [], [], []
    for chid in chids:
        if chid not in windows or len(windows[chid]) != 1:
            raise ValueError(f"{chid}: 准稳态窗口缺失或重复")
        rows, summary, manifest = analyze_case(
            Path(run_root) / chid, windows[chid][0], devices, z_m, fdsreader_module,
        )
        all_profiles.extend(rows)
        summaries.append(summary)
        manifests.append(manifest)
    outdir = Path(outdir)
    _write_rows(outdir / "longitudinal_temperature_profiles.csv", PROFILE_FIELDS, all_profiles)
    _write_rows(outdir / "longitudinal_domain_summary.csv", SUMMARY_FIELDS, summaries)
    _write_rows(outdir / "longitudinal_domain_manifest.csv", MANIFEST_FIELDS, manifests)
    return {"profiles": all_profiles, "summaries": summaries, "manifests": manifests}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rundir", default=str(DEFAULT_RUN_ROOT))
    parser.add_argument("--steady-windows", default=str(DEFAULT_WINDOWS))
    parser.add_argument("--device-profiles", default=str(DEFAULT_DEVICE_PROFILES))
    parser.add_argument("--outdir", default=str(DEFAULT_OUTDIR))
    parser.add_argument("--chids", nargs="+", default=list(DEFAULT_CHIDS))
    parser.add_argument("--z-m", type=float, default=4.5)
    args = parser.parse_args()
    try:
        result = run_diagnostics(
            args.rundir, args.steady_windows, args.device_profiles,
            args.outdir, args.chids, args.z_m,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    for row in result["summaries"]:
        print(
            f"{row['chid']}: peak x={row['x_peak_15_85_m']} m, "
            f"DeltaT(85/99 m)={row['DeltaT_85_C']}/{row['DeltaT_99_C']} °C, "
            f"tail R2={row['tail_85_99_log_r2']}"
        )
    print(f"结果目录: {Path(args.outdir).resolve()}")


if __name__ == "__main__":
    main()
