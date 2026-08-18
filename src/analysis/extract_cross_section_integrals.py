from __future__ import annotations

import argparse
import csv
import hashlib
import io
import math
import re
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[2]

from .field_integrals import integrate_nodal_cross_section


EXPECTED_STATIONS_M = (15.0, 22.0, 29.0, 39.5, 46.5, 50.0, 53.5, 60.5, 71.0, 78.0, 85.0)
EXPECTED_AREA_M2 = 50.0
CP_MODEL = "constant_cp_1005_J_kgK"
OUTPUT_FIELDS = (
    "chid", "time_s", "x_m", "C_T_J_per_m", "J_T_W", "U_e_field_mps",
    "area_m2", "cp_J_kgK", "T0_K", "cp_model", "status",
)
MANIFEST_FIELDS = (
    "chid", "fds_version", "fdsreader_version", "fds_input_sha256", "smv_sha256",
    "participating_slices_sha256", "source_slice_file_count", "time_count", "station_count",
    "time_start_s", "time_end_s", "cp_J_kgK", "T0_K", "cp_model",
    "case_rows_sha256", "data_csv_sha256",
)


def _load_fdsreader():
    try:
        import fdsreader
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "缺少 fdsreader；请安装并使用已验证版本 1.11.7，例如 pip install fdsreader==1.11.7"
        ) from exc
    return fdsreader


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def combined_file_sha256(root, paths):
    digest = hashlib.sha256()
    unique_paths = sorted({Path(path).resolve() for path in paths}, key=lambda path: path.as_posix())
    for path in unique_paths:
        relative = path.relative_to(Path(root).resolve()).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha256_file(path)))
        digest.update(b"\0")
    return digest.hexdigest(), len(unique_paths)


def parse_fds_environment(path):
    text = Path(path).read_text(encoding="utf-8-sig")
    chid_matches = re.findall(r"&HEAD\b[^/]*?\bCHID\s*=\s*['\"]([^'\"]+)['\"]", text, re.IGNORECASE | re.DOTALL)
    tmpa_matches = re.findall(r"&MISC\b[^/]*?\bTMPA\s*=\s*([-+0-9.eEdD]+)", text, re.IGNORECASE | re.DOTALL)
    if len(chid_matches) != 1 or len(tmpa_matches) != 1:
        raise ValueError(f"{path} 必须包含唯一 HEAD CHID 和 MISC TMPA")
    tmpa_C = float(tmpa_matches[0].replace("D", "E").replace("d", "e"))
    if not math.isfinite(tmpa_C):
        raise ValueError(f"{path} 的 TMPA 不是有限值")
    return chid_matches[0], tmpa_C + 273.15


def _coordinate_indices(global_coordinates, local_coordinates, tolerance=2e-6):
    indices = []
    for coordinate in np.asarray(local_coordinates, dtype=float):
        matches = np.flatnonzero(np.isclose(global_coordinates, coordinate, rtol=0.0, atol=tolerance))
        if matches.size != 1:
            raise ValueError(f"坐标 {coordinate} 无法唯一映射到全局节点网格")
        indices.append(int(matches[0]))
    return indices


def assemble_slice(slice_object, vector_component=None):
    if slice_object.orientation != 1 or slice_object.cell_centered:
        raise ValueError("仅支持 PBX=常数且位于节点上的横截面切片")
    coordinates = slice_object.get_coordinates(ignore_cell_centered=False)
    y = np.asarray(coordinates["y"], dtype=float)
    z = np.asarray(coordinates["z"], dtype=float)
    times = np.asarray(slice_object.times, dtype=float)
    if y.size < 2 or z.size < 2 or times.size < 1:
        raise ValueError("横截面坐标或时间轴为空")
    output = np.full((times.size, y.size, z.size), np.nan, dtype=float)
    for subslice in slice_object.subslices:
        local_coordinates = subslice.get_coordinates(ignore_cell_centered=False)
        y_indices = _coordinate_indices(y, local_coordinates["y"])
        z_indices = _coordinate_indices(z, local_coordinates["z"])
        if vector_component is None:
            data = np.asarray(subslice.data, dtype=float)
        else:
            if vector_component not in subslice.vector_filenames:
                raise ValueError(f"切片缺少速度分量 {vector_component}")
            data = np.asarray(subslice.vector_data[vector_component], dtype=float)
        expected_shape = (times.size, len(y_indices), len(z_indices))
        if data.shape != expected_shape:
            raise ValueError(f"子切片数据形状 {data.shape} 与坐标 {expected_shape} 不一致")
        for local_y, global_y in enumerate(y_indices):
            output[:, global_y, z_indices] = data[:, local_y, :]
    if not np.all(np.isfinite(output)):
        raise ValueError("多 MESH 横截面组装后存在空洞或非有限值")
    return times, y, z, output


def _assert_same_axis(name, reference, candidate, tolerance=2e-6):
    if reference.shape != candidate.shape or not np.allclose(reference, candidate, rtol=0.0, atol=tolerance):
        raise ValueError(f"配对切片的 {name} 不一致")


def integrate_paired_slices(temperature_slice, density_slice, velocity_slice, cp_J_kgK, T0_K,
                            epsilon_J_per_m=1e-9):
    for name, slice_object in (("温度", temperature_slice), ("密度", density_slice), ("速度", velocity_slice)):
        if slice_object.orientation != 1 or slice_object.cell_centered:
            raise ValueError(f"{name}切片不是节点型 PBX 横截面")
    times = np.asarray(temperature_slice.times, dtype=float)
    _assert_same_axis("温度/密度时间轴", times, np.asarray(density_slice.times, dtype=float))
    _assert_same_axis("温度/速度时间轴", times, np.asarray(velocity_slice.times, dtype=float))

    def by_mesh(slice_object):
        mapping = {subslice.mesh.id: subslice for subslice in slice_object.subslices}
        if len(mapping) != len(slice_object.subslices):
            raise ValueError("同一切片包含重复 MESH")
        return mapping

    temperature_meshes = by_mesh(temperature_slice)
    density_meshes = by_mesh(density_slice)
    velocity_meshes = by_mesh(velocity_slice)
    if temperature_meshes.keys() != density_meshes.keys() or temperature_meshes.keys() != velocity_meshes.keys():
        raise ValueError("配对切片覆盖的 MESH 集合不一致")

    totals = [{"C_T_J_per_m": 0.0, "J_T_W": 0.0, "area_m2": 0.0} for _ in times]
    for mesh_id in sorted(temperature_meshes):
        temperature_subslice = temperature_meshes[mesh_id]
        density_subslice = density_meshes[mesh_id]
        velocity_subslice = velocity_meshes[mesh_id]
        coordinates = temperature_subslice.get_coordinates(ignore_cell_centered=False)
        density_coordinates = density_subslice.get_coordinates(ignore_cell_centered=False)
        velocity_coordinates = velocity_subslice.get_coordinates(ignore_cell_centered=False)
        y = np.asarray(coordinates["y"], dtype=float)
        z = np.asarray(coordinates["z"], dtype=float)
        for name, reference, candidate in (
            (f"{mesh_id} 温度/密度 y 坐标", y, np.asarray(density_coordinates["y"], dtype=float)),
            (f"{mesh_id} 温度/速度 y 坐标", y, np.asarray(velocity_coordinates["y"], dtype=float)),
            (f"{mesh_id} 温度/密度 z 坐标", z, np.asarray(density_coordinates["z"], dtype=float)),
            (f"{mesh_id} 温度/速度 z 坐标", z, np.asarray(velocity_coordinates["z"], dtype=float)),
        ):
            _assert_same_axis(name, reference, candidate)
        if "u" not in velocity_subslice.vector_filenames:
            raise ValueError(f"{mesh_id} 缺少 u 速度分量")
        temperature_C = np.asarray(temperature_subslice.data, dtype=float)
        density = np.asarray(density_subslice.data, dtype=float)
        velocity = np.asarray(velocity_subslice.vector_data["u"], dtype=float)
        expected_shape = (times.size, y.size, z.size)
        if any(array.shape != expected_shape for array in (temperature_C, density, velocity)):
            raise ValueError(f"{mesh_id} 的配对场形状必须精确为 {expected_shape}")
        for index in range(times.size):
            result = integrate_nodal_cross_section(
                y, z, density[index], cp_J_kgK, temperature_C[index] + 273.15,
                T0_K, velocity[index], epsilon_J_per_m,
            )
            totals[index]["C_T_J_per_m"] += result["C_T_J_per_m"]
            totals[index]["J_T_W"] += result["J_T_W"]
            totals[index]["area_m2"] += result["area_m2"]

    for result in totals:
        C_T = result["C_T_J_per_m"]
        denominator = C_T + math.copysign(epsilon_J_per_m, C_T if C_T != 0 else 1.0)
        result["U_e_field_mps"] = result["J_T_W"] / denominator
        result["status"] = "PASS" if abs(C_T) > epsilon_J_per_m else "NEAR_ZERO_C_T_REVIEW"
    return times, totals


def _slice_files(slice_object, vector_component=None):
    files = []
    for subslice in slice_object.subslices:
        filename = subslice.filename if vector_component is None else subslice.vector_filenames.get(vector_component)
        if not filename:
            raise ValueError(f"切片缺少参与积分的 {vector_component or 'scalar'} 文件")
        files.append(Path(slice_object._root_path) / filename)
    return files


def _find_station_slices(simulation, station_x):
    candidates = [slice_object for slice_object in simulation.slices
                  if slice_object.orientation == 1
                  and math.isclose(float(slice_object.extent.x_start), station_x, rel_tol=0.0, abs_tol=2e-6)]
    by_quantity = {}
    for slice_object in candidates:
        by_quantity.setdefault(slice_object.quantity.name.upper(), []).append(slice_object)
    selected = {}
    for quantity in ("TEMPERATURE", "DENSITY", "VELOCITY"):
        matches = by_quantity.get(quantity, [])
        if len(matches) != 1:
            raise ValueError(f"x={station_x:g} m 的 {quantity} 切片数量为 {len(matches)}，应为 1")
        selected[quantity] = matches[0]
    return selected


def _canonical_rows_sha256(rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=OUTPUT_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return hashlib.sha256(stream.getvalue().encode("utf-8")).hexdigest()


def extract_case(case_dir, cp_J_kgK=1005.0, fdsreader_module=None):
    case_dir = Path(case_dir).resolve()
    chid = case_dir.name
    fds_path = case_dir / f"{chid}.fds"
    smv_path = case_dir / f"{chid}.smv"
    if not fds_path.is_file() or not smv_path.is_file():
        raise FileNotFoundError(f"{case_dir} 缺少 {chid}.fds 或 {chid}.smv")
    input_chid, T0_K = parse_fds_environment(fds_path)
    if input_chid != chid:
        raise ValueError(f"目录 CHID {chid} 与输入文件 CHID {input_chid} 不一致")
    cp = float(cp_J_kgK)
    if not math.isfinite(cp) or cp <= 0:
        raise ValueError("cp 必须为有限正值")

    fdsreader_module = fdsreader_module or _load_fdsreader()
    simulation = fdsreader_module.Simulation(str(smv_path))
    rows = []
    participating_files = []
    reference_times = None
    for station_x in EXPECTED_STATIONS_M:
        slices = _find_station_slices(simulation, station_x)
        temperature_slice = slices["TEMPERATURE"]
        density_slice = slices["DENSITY"]
        velocity_slice = slices["VELOCITY"]
        if temperature_slice.quantity.unit != "C" or density_slice.quantity.unit != "kg/m3" or velocity_slice.quantity.unit != "m/s":
            raise ValueError(f"x={station_x:g} m 的切片单位不符合 C、kg/m3、m/s 合同")

        times, results = integrate_paired_slices(
            temperature_slice, density_slice, velocity_slice, cp, T0_K
        )
        if reference_times is None:
            reference_times = times
        else:
            _assert_same_axis("各站点时间轴", reference_times, times)

        for time_s, result in zip(times, results):
            if not math.isclose(result["area_m2"], EXPECTED_AREA_M2, rel_tol=0.0, abs_tol=1e-6):
                raise ValueError(f"x={station_x:g} m 的截面面积为 {result['area_m2']} m2，应为 50 m2")
            rows.append({
                "chid": chid, "time_s": float(time_s), "x_m": station_x,
                "C_T_J_per_m": result["C_T_J_per_m"], "J_T_W": result["J_T_W"],
                "U_e_field_mps": result["U_e_field_mps"], "area_m2": result["area_m2"],
                "cp_J_kgK": cp, "T0_K": T0_K,
                "cp_model": f"constant_cp_{cp:g}_J_kgK", "status": result["status"],
            })
        participating_files.extend(_slice_files(temperature_slice))
        participating_files.extend(_slice_files(density_slice))
        participating_files.extend(_slice_files(velocity_slice, "u"))
        temperature_slice.clear_cache()
        density_slice.clear_cache()
        velocity_slice.clear_cache()

    participating_hash, source_count = combined_file_sha256(case_dir, participating_files)
    manifest = {
        "chid": chid,
        "fds_version": str(getattr(simulation, "fds_version", "")),
        "fdsreader_version": str(getattr(fdsreader_module, "__version__", "")),
        "fds_input_sha256": sha256_file(fds_path),
        "smv_sha256": sha256_file(smv_path),
        "participating_slices_sha256": participating_hash,
        "source_slice_file_count": source_count,
        "time_count": len(reference_times),
        "station_count": len(EXPECTED_STATIONS_M),
        "time_start_s": float(reference_times[0]),
        "time_end_s": float(reference_times[-1]),
        "cp_J_kgK": cp,
        "T0_K": T0_K,
        "cp_model": f"constant_cp_{cp:g}_J_kgK",
        "case_rows_sha256": _canonical_rows_sha256(rows),
        "data_csv_sha256": "",
    }
    return rows, manifest


def write_outputs(rows, manifests, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    data_path = output_dir / "cross_section_integrals.csv"
    manifest_path = output_dir / "cross_section_integrals_manifest.csv"
    with data_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    data_hash = sha256_file(data_path)
    for manifest in manifests:
        manifest["data_csv_sha256"] = data_hash
    with manifest_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(manifests)
    return data_path, manifest_path


def main(argv=None):
    default_runs = PROJECT_ROOT / "runs"
    default_output = PROJECT_ROOT / "derived" / "field_integrals"
    parser = argparse.ArgumentParser(description="从 FDS PBX 切片提取 constant-cp 横截面超温焓指标")
    parser.add_argument("--runs-dir", type=Path, default=default_runs)
    parser.add_argument("--output-dir", type=Path, default=default_output)
    parser.add_argument("--chids", nargs="*")
    parser.add_argument("--cp", type=float, default=1005.0)
    args = parser.parse_args(argv)
    chids = args.chids or sorted(path.name for path in args.runs_dir.iterdir() if path.is_dir())
    if not chids:
        raise ValueError("没有找到待提取的 CHID")
    all_rows = []
    manifests = []
    for chid in chids:
        rows, manifest = extract_case(args.runs_dir / chid, args.cp)
        all_rows.extend(rows)
        manifests.append(manifest)
        print(f"{chid}: {manifest['station_count']} stations x {manifest['time_count']} times")
    data_path, manifest_path = write_outputs(all_rows, manifests, args.output_dir)
    print(f"wrote {len(all_rows)} rows: {data_path}")
    print(f"manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
