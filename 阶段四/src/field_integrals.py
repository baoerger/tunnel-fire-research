"""横截面超温焓、纵向超温焓流与全场有效输运指标。"""
from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path


EXPECTED_UNITS = {
    "rho": "kg/m3", "cp": "J/(kg K)", "T": "K", "T0": "K",
    "u_x": "m/s", "dA": "m2",
}
CELL_CSV_FIELDS = ("x_m", "rho_kg_m3", "cp_J_kgK", "T_K", "T0_K", "u_x_mps", "dA_m2")


def validate_units(units):
    if units != EXPECTED_UNITS:
        raise ValueError(f"全场积分单位必须精确为 {EXPECTED_UNITS}")


def integrate_cross_section(cells, units, epsilon_J_per_m=1e-9):
    validate_units(units)
    if not cells or epsilon_J_per_m <= 0:
        raise ValueError("截面单元不能为空且 epsilon 必须为正")
    C_T = 0.0
    J_T = 0.0
    area = 0.0
    for index, cell in enumerate(cells):
        values = {name: float(cell[name]) for name in EXPECTED_UNITS}
        if any(not math.isfinite(value) for value in values.values()):
            raise ValueError(f"截面单元 {index} 含非有限值")
        if values["rho"] <= 0 or values["cp"] <= 0 or values["dA"] <= 0 or values["T0"] <= 0:
            raise ValueError(f"截面单元 {index} 的 rho/cp/dA/T0 必须为正")
        excess_enthalpy_per_length = (
            values["rho"] * values["cp"] * (values["T"] - values["T0"]) * values["dA"]
        )
        C_T += excess_enthalpy_per_length
        J_T += excess_enthalpy_per_length * values["u_x"]
        area += values["dA"]
    denominator = C_T + math.copysign(epsilon_J_per_m, C_T if C_T != 0 else 1.0)
    return {
        "C_T_J_per_m": C_T, "J_T_W": J_T,
        "U_e_field_mps": J_T / denominator,
        "area_m2": area, "n_cells": len(cells),
        "status": "PASS" if abs(C_T) > epsilon_J_per_m else "NEAR_ZERO_C_T_REVIEW",
    }


def integrate_stations(stations, units, epsilon_J_per_m=1e-9):
    if not stations:
        raise ValueError("全场站点为空")
    rows = []
    seen = set()
    for station in stations:
        x = float(station["x_m"])
        if not math.isfinite(x) or x in seen:
            raise ValueError("全场站点 x 非有限或重复")
        seen.add(x)
        rows.append({"x_m": x, **integrate_cross_section(station["cells"], units, epsilon_J_per_m)})
    return sorted(rows, key=lambda row: row["x_m"])


def read_cell_csv(path):
    """读取由 FDS 切片后处理导出的严格 SI 截面单元表。"""
    grouped = {}
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != CELL_CSV_FIELDS:
            raise ValueError(f"截面单元 CSV 字段必须精确为 {CELL_CSV_FIELDS}")
        for line_number, row in enumerate(reader, start=2):
            try:
                values = {field: float(row[field]) for field in CELL_CSV_FIELDS}
            except (TypeError, ValueError) as exc:
                raise ValueError(f"截面单元 CSV 第 {line_number} 行不是有效数值") from exc
            grouped.setdefault(values["x_m"], []).append({
                "rho": values["rho_kg_m3"], "cp": values["cp_J_kgK"],
                "T": values["T_K"], "T0": values["T0_K"],
                "u_x": values["u_x_mps"], "dA": values["dA_m2"],
            })
    if not grouped:
        raise ValueError("截面单元 CSV 没有数据")
    return [{"x_m": x, "cells": cells} for x, cells in sorted(grouped.items())]


def run_synthetic_check(output_path):
    stations = []
    for x, temperature, velocity in ((0.0, 303.15, 2.0), (5.0, 313.15, 3.0)):
        cells = [
            {"rho": 1.2, "cp": 1000.0, "T": temperature, "T0": 293.15,
             "u_x": velocity, "dA": 2.5}
            for _ in range(4)
        ]
        stations.append({"x_m": x, "cells": cells})
    rows = integrate_stations(stations, EXPECTED_UNITS)
    for row in rows:
        row["data_type"] = "SYNTHETIC_SOFTWARE_TEST_NOT_SCIENTIFIC_EVIDENCE"
        row["decision_status"] = "NO_FIELD_PHYSICS_CONCLUSION"
    output_path = Path(output_path); output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parent.parent / "03_正向验证与全场解释" / "synthetic_field_integrals.csv")
    args = parser.parse_args(argv)
    rows = run_synthetic_check(args.output)
    print(f"synthetic field integrals: {len(rows)} stations")
    print("NO_FIELD_PHYSICS_CONCLUSION")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
