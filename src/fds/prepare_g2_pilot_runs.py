"""生成、静态审计并登记 G2 的 16 条先导长算输入。"""

from __future__ import annotations

import csv
import json
import math
from collections import Counter
from pathlib import Path

from . import generate_fds_case as generator
from . import run_registry
from .prepare_g1_short_runs import static_audit
from .project_paths import PROJECT_ROOT


DEFAULT_CASES = PROJECT_ROOT / "config" / "pilot_cases.csv"
DEFAULT_INPUT_DIR = PROJECT_ROOT / "fds_inputs" / "pilot" / "g2"
DEFAULT_RUNS_ROOT = PROJECT_ROOT / "runs" / "pilot"
DEFAULT_MANIFEST = PROJECT_ROOT / "reports" / "g2_pilot_run_handoff.csv"
FDS_VERSION = "6.10.1"
FDS_REVISION = "FDS-6.10.1-0-g12efa16-release"


def _stage(row):
    purpose = row["purpose"]
    if purpose == "background":
        return 1
    if purpose in {"grid_low", "grid_high"} and math.isclose(
            float(row["dx"]), 0.25) and int(row["rnd_seed"]) == 104729:
        return 2
    if purpose in {"grid_low", "grid_high", "seed_repeat_low", "seed_repeat_high"}:
        return 3
    if purpose == "opening_translation":
        return 4
    raise ValueError(f"未知 G2 purpose={purpose!r}")


def validate_matrix(rows):
    if len(rows) != 16:
        raise ValueError(f"G2 先导必须恰好 16 条，实际 {len(rows)}")
    for key in ("chid", "job_attempt_id", "run_chid"):
        if key == "run_chid":
            values = [row["chid"] for row in rows]
        else:
            values = [row[key] for row in rows]
        if len(values) != len(set(values)):
            raise ValueError(f"G2 {key} 存在重复")
    expected_counts = {
        "background": 3, "grid_low": 3, "grid_high": 3,
        "opening_translation": 3, "seed_repeat_low": 2,
        "seed_repeat_high": 2,
    }
    counts = Counter(row["purpose"] for row in rows)
    if counts != expected_counts:
        raise ValueError(f"G2 设计块计数错误: {dict(counts)}")
    if Counter(_stage(row) for row in rows) != {1: 3, 2: 2, 3: 8, 4: 3}:
        raise ValueError("G2 阶段顺序计数必须为 3/2/8/3")

    normalized = []
    for line_number, row in enumerate(rows, start=2):
        if row.get("status") != "AUTHORIZED_G2_PILOT":
            raise ValueError(f"CSV 第 {line_number} 行未获 G2 先导授权")
        case = generator.normalize_case(row, row_number=line_number)
        if case["T_end"] != 300.0 or case["U"] != 0.0:
            raise ValueError(f"{case['chid']} 必须为 U=0、T_END=300 s")
        if case["mesh_profile"] != "no_wind_symmetric_v1":
            raise ValueError(f"{case['chid']} 未使用冻结镜像 MESH")
        if case["sensor_profile"] != "no_wind_global_v1":
            raise ValueError(f"{case['chid']} 未使用固定全局测点")
        if case["case_kind"] == "fire":
            requested = float(row["Af_requested_m2"])
            if not math.isclose(case["Af_discrete_m2"], requested, abs_tol=1e-9):
                raise ValueError(f"{case['chid']} 燃烧器未与网格精确对齐")
            if not math.isclose(case["xf_actual_m"], case["x_fire"], abs_tol=1e-9):
                raise ValueError(f"{case['chid']} 火源质心发生吸附偏移")
        normalized.append(case)
    heavy = {case["chid"] for case in normalized if case["output_profile"] == "heavy"}
    expected_heavy = {
        "p_q018_s40_x50_g25_r104729", "p_q036_s40_x50_g25_r104729",
        "p_q027_s45_x40_g25_r104729", "p_q027_s45_x50_g25_r104729",
        "p_q027_s45_x60_g25_r104729",
    }
    if heavy != expected_heavy:
        raise ValueError("G2 heavy 代表工况集合不符合预注册设计")
    return normalized


def prepare(cases_path=DEFAULT_CASES, input_dir=DEFAULT_INPUT_DIR,
            runs_root=DEFAULT_RUNS_ROOT, manifest_path=DEFAULT_MANIFEST,
            case_registry_path=None, attempts_path=None):
    cases_path = Path(cases_path)
    input_dir = Path(input_dir)
    runs_root = Path(runs_root)
    with cases_path.open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    normalized = validate_matrix(rows)
    generator._run_csv(str(cases_path), str(input_dir), required_only=True)

    prepared = []
    for row, case in zip(rows, normalized):
        source = (input_dir / f"{case['chid']}.fds").resolve()
        static_audit(source, case)
        return_dir = (
            runs_root / case["chid"] / "attempts" / row["job_attempt_id"]
        ).resolve()
        bounds = (json.dumps(case["burner_bounds"], ensure_ascii=False)
                  if case["burner_bounds"] else "")
        attempt = run_registry.prepare_attempt(
            source_input=source, return_dir=return_dir,
            subset=row["subset"], purpose=row["purpose"],
            case_kind=case["case_kind"], parent_case_id=row["parent_case_id"],
            physical_case_id=row["physical_case_id"],
            job_attempt_id=row["job_attempt_id"],
            case_registry_path=case_registry_path, attempts_path=attempts_path,
            case_metadata={
                "replicate_id": row["replicate_id"],
                "Q_requested_MW": f"{case['Q']:g}",
                "Af_requested_m2": row["Af_requested_m2"],
                "Af_discrete_m2": f"{case['Af_discrete_m2']:.8g}",
                "Df_requested_m": f"{case['Df']:.15g}",
                "Df_discrete_m": f"{case['Df_discrete_m']:.15g}",
                "burner_side_requested_m": row["burner_side_requested_m"],
                "burner_bounds": bounds,
                "xf_requested_m": f"{case['x_fire']:g}",
                "xf_actual_m": f"{case['xf_actual_m']:g}",
                "yf_actual_m": f"{case['yf_actual_m']:g}",
                "dx_m": f"{case['dx']:g}",
                "mesh_profile": case["mesh_profile"],
                "sensor_profile": case["sensor_profile"],
                "output_profile": case["output_profile"],
                "rnd_seed": str(case["rnd_seed"]),
                "T_end_s": f"{case['T_end']:g}",
                "fds_version_planned": f"{FDS_VERSION} ({FDS_REVISION})",
                "status": "PREPARED_G2_PILOT",
            },
            attempt_metadata={
                "T_end_s": f"{case['T_end']:g}",
                "fds_version_planned": f"{FDS_VERSION} ({FDS_REVISION})",
            },
        )
        required = [
            f"{case['chid']}.fds", f"{case['chid']}.out",
            f"{case['chid']}_devc.csv", f"{case['chid']}.smv",
            f"{case['chid']}.end (如有)",
        ]
        if case["case_kind"] == "fire":
            required.insert(3, f"{case['chid']}_hrr.csv")
        if case["output_profile"] == "heavy":
            required.extend([f"{case['chid']}*.sf*", f"{case['chid']}*.bf"])
        prepared.append({
            "pilot_stage": _stage(row), "run_chid": case["chid"],
            "job_attempt_id": row["job_attempt_id"],
            "purpose": row["purpose"], "case_kind": case["case_kind"],
            "Q_MW": f"{case['Q']:g}", "dx_m": f"{case['dx']:g}",
            "xf_m": f"{case['x_fire']:g}", "rnd_seed": case["rnd_seed"],
            "T_end_s": f"{case['T_end']:g}",
            "output_profile": case["output_profile"],
            "source_fds_path": str(source),
            "attempt_input_sha256": attempt["attempt_input_sha256"],
            "run_core_sha256": attempt["run_core_sha256"],
            "return_dir": attempt["return_dir"],
            "required_return_files": ";".join(required),
            "static_audit": "PASS",
        })

    prepared.sort(key=lambda item: (int(item["pilot_stage"]), item["run_chid"]))
    manifest_path = Path(manifest_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=prepared[0].keys())
        writer.writeheader()
        writer.writerows(prepared)
    return prepared


def main():
    rows = prepare()
    counts = Counter(int(row["pilot_stage"]) for row in rows)
    for stage in sorted(counts):
        print(f"[PASS] G2 stage {stage}: {counts[stage]} inputs")
    print(f"[OK] manifest: {DEFAULT_MANIFEST}")


if __name__ == "__main__":
    main()
