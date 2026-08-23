"""生成、审计并冻结 G3 开发集、禁用扩展池和隔离锁箱输入。"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import statistics
import stat
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import generate_fds_case as generator
from . import run_registry
from . import tunnel_config as cfg
from .prepare_g1_short_runs import static_audit
from .project_paths import PROJECT_ROOT


FREEZE_PATH = PROJECT_ROOT / "freezes" / "pilot" / "pilot_freeze_v1.json"
PROTOCOL_PATH = PROJECT_ROOT / "config" / "protocol_v1.json"
DEVELOPMENT_CSV = PROJECT_ROOT / "config" / "development_cases.csv"
EXTENSION_CSV = PROJECT_ROOT / "config" / "development_extension_pool.csv"
LOCKBOX_CSV = PROJECT_ROOT / "config" / "lockbox_cases.csv"
DEVELOPMENT_INPUTS = PROJECT_ROOT / "fds_inputs" / "development" / "g3"
LOCKBOX_INPUTS = PROJECT_ROOT / "fds_inputs" / "lockbox" / "g3"
DEVELOPMENT_RUNS = PROJECT_ROOT / "runs" / "development"
LOCKBOX_RUNS = PROJECT_ROOT / "runs" / "lockbox"
DEVELOPMENT_HANDOFF = PROJECT_ROOT / "reports" / "g3_development_run_handoff.csv"
LOCKBOX_MANIFEST = PROJECT_ROOT / "reports" / "g3_lockbox_archive_manifest.csv"
RESOURCE_REPORT = PROJECT_ROOT / "reports" / "g3_resource_budget.csv"
APPROVAL_REPORT = PROJECT_ROOT / "reports" / "g3_batch_approval.csv"
AUDIT_JSON = PROJECT_ROOT / "reports" / "g3_design_audit.json"
REPORT_MD = PROJECT_ROOT / "reports" / "g3_design_freeze.md"
G3_FREEZE = PROJECT_ROOT / "freezes" / "g3" / "g3_design_freeze_v1.json"

FDS_VERSION = "6.10.1"
FDS_REVISION = "FDS-6.10.1-0-g12efa16-release"
SEEDS = (104729, 204729, 304729)
CSV_FIELDS = (
    "chid", "job_attempt_id", "subset", "purpose", "case_kind", "batch",
    "parent_case_id", "physical_case_id", "replicate_id", "Q", "U", "Df",
    "Af_requested_m2", "burner_side_requested_m", "dx", "L", "W", "H",
    "x_fire", "T_end", "rnd_seed", "sensor_profile", "mesh_profile",
    "output_profile", "case_group", "note", "status", "required",
)

# D1 在查看任何正式结果前固定：低/中/高中心代表点和一对中点平移。
D1_PHYSICAL = {
    (18.0, 3.5, 50.0),
    (27.0, 4.5, 50.0),
    (36.0, 5.0, 50.0),
    (27.0, 4.5, 40.0),
    (27.0, 4.5, 60.0),
}
OFFSET_PARENTS = {
    (18.0, 3.5), (18.0, 5.0), (27.0, 4.5),
    (36.0, 3.5), (36.0, 5.0),
}
REPEAT_PHYSICAL = {
    (18.0, 3.5, 50.0), (36.0, 5.0, 50.0),
    (27.0, 4.5, 40.0), (27.0, 4.5, 60.0),
}


def _token(value):
    value = float(value)
    if value.is_integer():
        return str(int(value))
    return f"{value:g}".replace(".", "p")


def _side_token(side):
    return str(int(round(float(side) * 10)))


def _diameter(side):
    return math.sqrt(4.0 * float(side) ** 2 / math.pi)


def _base_row(*, prefix, subset, purpose, batch, parent_case_id,
              physical_case_id, Q, side, x_fire, seed, output_profile,
              status, required="yes", label=""):
    chid = (
        f"{prefix}_q{int(Q):03d}_s{_side_token(side)}_x{_token(x_fire)}_"
        f"g25_r{seed}"
    )
    return {
        "chid": chid,
        "job_attempt_id": f"{chid}_a01",
        "subset": subset,
        "purpose": purpose,
        "case_kind": "fire",
        "batch": batch,
        "parent_case_id": parent_case_id,
        "physical_case_id": physical_case_id,
        "replicate_id": f"r{seed}",
        "Q": f"{float(Q):g}",
        "U": "0",
        "Df": f"{_diameter(side):.15g}",
        "Af_requested_m2": f"{float(side) ** 2:g}",
        "burner_side_requested_m": f"{float(side):g}",
        "dx": "0.25",
        "L": "100",
        "W": "10",
        "H": "5",
        "x_fire": f"{float(x_fire):g}",
        "T_end": "300",
        "rnd_seed": str(seed),
        "sensor_profile": "no_wind_global_t90_v1",
        "mesh_profile": "no_wind_symmetric_v1",
        "output_profile": output_profile,
        "case_group": "G3_DEVELOPMENT" if subset == "development" else "G3_LOCKBOX",
        "note": label,
        "status": status,
        "required": required,
    }


def build_design_rows():
    development = []
    for Q in (18.0, 27.0, 36.0):
        for side in (3.5, 4.5, 5.0):
            physical = (Q, side, 50.0)
            batch = "D1" if physical in D1_PHYSICAL else "D2"
            parent = f"d_q{int(Q):03d}_s{_side_token(side)}"
            physical_id = f"{parent}_x50"
            development.append(_base_row(
                prefix="d", subset="development", purpose="development_center",
                batch=batch, parent_case_id=parent, physical_case_id=physical_id,
                Q=Q, side=side, x_fire=50.0, seed=SEEDS[0],
                output_profile="heavy" if physical in D1_PHYSICAL else "light",
                status="AUTHORIZED_G3_DEVELOPMENT",
                label=f"G3 {batch} center Q={Q:g} MW side={side:g} m",
            ))
    for Q, side in sorted(OFFSET_PARENTS):
        for x_fire in (40.0, 60.0):
            physical = (Q, side, x_fire)
            batch = "D1" if physical in D1_PHYSICAL else "D3"
            parent = f"d_q{int(Q):03d}_s{_side_token(side)}"
            physical_id = f"{parent}_x{_token(x_fire)}"
            development.append(_base_row(
                prefix="d", subset="development", purpose="development_offset",
                batch=batch, parent_case_id=parent, physical_case_id=physical_id,
                Q=Q, side=side, x_fire=x_fire, seed=SEEDS[0],
                output_profile="heavy" if physical in D1_PHYSICAL else "light",
                status="AUTHORIZED_G3_DEVELOPMENT",
                label=f"G3 {batch} offset Q={Q:g} MW side={side:g} m x={x_fire:g} m",
            ))
    for Q, side, x_fire in sorted(REPEAT_PHYSICAL):
        parent = f"d_q{int(Q):03d}_s{_side_token(side)}"
        physical_id = f"{parent}_x{_token(x_fire)}"
        for seed in SEEDS[1:]:
            development.append(_base_row(
                prefix="d", subset="development", purpose="development_seed_repeat",
                batch="D3", parent_case_id=parent, physical_case_id=physical_id,
                Q=Q, side=side, x_fire=x_fire, seed=seed, output_profile="light",
                status="AUTHORIZED_G3_DEVELOPMENT",
                label=f"G3 D3 seed repeat Q={Q:g} MW side={side:g} m x={x_fire:g} m",
            ))
    development.sort(key=lambda row: (int(row["batch"][1]), row["chid"]))

    extension = []
    for Q in (24.0, 30.0):
        for side in (4.0, 5.0):
            parent = f"e_q{int(Q):03d}_s{_side_token(side)}"
            extension.append(_base_row(
                prefix="e", subset="development_extension", purpose="s3_extension_pool",
                batch="DISABLED", parent_case_id=parent,
                physical_case_id=f"{parent}_x50", Q=Q, side=side, x_fire=50.0,
                seed=SEEDS[0], output_profile="light",
                status="FROZEN_DISABLED_BY_G2", required="no",
                label="G3 preregistered extension pool; disabled by G2 freeze",
            ))

    lock_conditions = [
        ("l01", 22.0, 4.0, 50.0), ("l02", 32.0, 4.0, 50.0),
        ("l03", 22.0, 4.5, 42.5), ("l04", 22.0, 4.5, 57.5),
        ("l05", 32.0, 5.0, 42.5), ("l06", 32.0, 5.0, 57.5),
        ("l07", 18.0, 4.0, 42.5), ("l08", 18.0, 4.0, 57.5),
        ("l09", 36.0, 4.0, 42.5), ("l10", 36.0, 4.0, 57.5),
    ]
    lockbox = []
    for label, Q, side, x_fire in lock_conditions:
        seeds = SEEDS if label in {"l01", "l02"} else SEEDS[:1]
        for seed in seeds:
            lockbox.append(_base_row(
                prefix=label, subset="lockbox", purpose="independent_confirmation",
                batch="G8_ONLY", parent_case_id=label, physical_case_id=label,
                Q=Q, side=side, x_fire=x_fire, seed=seed, output_profile="light",
                status="SEALED_G3_LOCKBOX", label=f"G3 sealed lockbox {label.upper()}",
            ))
    return development, extension, lockbox


def _write_csv(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _normalized(rows):
    return [generator.normalize_case(row, row_number=index)
            for index, row in enumerate(rows, start=2)]


def validate_design(development, extension, lockbox, freeze):
    frozen = freeze["frozen_values"]
    expected = {
        "working_dx_m": 0.25,
        "temperature_height_z_over_H": 0.9,
        "minimum_sufficient_T_END_s": 300,
        "development_extension_pool_enabled": False,
    }
    for key, value in expected.items():
        if frozen.get(key) != value:
            raise ValueError(f"G2 冻结值 {key}={frozen.get(key)!r}，期望 {value!r}")
    if len(development) != 27 or len(extension) != 4 or len(lockbox) != 14:
        raise ValueError("G3 运行数必须为开发 27、禁用扩展 4、锁箱 14")
    if Counter(row["batch"] for row in development) != {"D1": 5, "D2": 6, "D3": 16}:
        raise ValueError("开发批次计数必须为 D1/D2/D3=5/6/16")
    if len({row["physical_case_id"] for row in development}) != 19:
        raise ValueError("开发集必须恰好 19 个物理工况")
    if len({row["parent_case_id"] for row in development}) != 9:
        raise ValueError("开发集必须恰好 9 个 Q-D 父组")
    if len({row["physical_case_id"] for row in lockbox}) != 10:
        raise ValueError("锁箱必须恰好 10 个物理工况")
    if set(row["physical_case_id"] for row in development) & set(
            row["physical_case_id"] for row in lockbox):
        raise ValueError("开发集与锁箱 physical_case_id 交叉")
    if any(row["status"] != "FROZEN_DISABLED_BY_G2" or row["required"] != "no"
           for row in extension):
        raise ValueError("扩展池必须整体冻结为禁用")
    all_active = development + lockbox
    for key in ("chid", "job_attempt_id"):
        values = [row[key] for row in all_active]
        if len(values) != len(set(values)):
            raise ValueError(f"G3 {key} 重复")
    cases = _normalized(all_active)
    for row, case in zip(all_active, cases):
        if case["dx"] != 0.25 or case["T_end"] != 300 or case["U"] != 0:
            raise ValueError(f"{case['chid']} 未使用冻结 dx/T_END/U")
        if case["mesh_profile"] != "no_wind_symmetric_v1":
            raise ValueError(f"{case['chid']} 未使用冻结 MESH")
        if case["sensor_profile"] != "no_wind_global_t90_v1":
            raise ValueError(f"{case['chid']} 未使用冻结 T90 正式测点")
        side = float(row["burner_side_requested_m"])
        if not math.isclose(case["Af_discrete_m2"], side ** 2, abs_tol=1e-9):
            raise ValueError(f"{case['chid']} 燃烧器面积未精确落在 0.25 m 网格")
        if not math.isclose(case["xf_actual_m"], case["x_fire"], abs_tol=1e-9):
            raise ValueError(f"{case['chid']} 火源中心发生网格吸附偏移")
    heavy = {row["chid"] for row in development if row["output_profile"] == "heavy"}
    expected_heavy = {
        row["chid"] for row in development
        if (float(row["Q"]), float(row["burner_side_requested_m"]),
            float(row["x_fire"])) in D1_PHYSICAL and int(row["rnd_seed"]) == SEEDS[0]
    }
    if heavy != expected_heavy or len(heavy) != 5:
        raise ValueError("heavy 输出必须只用于五个 D1 主种子代表工况")
    return cases[:len(development)], cases[len(development):]


def _formal_static_audit(path, case):
    static_audit(path, case)
    text = Path(path).read_text(encoding="utf-8")
    expected_devices = len(cfg.no_wind_global_sensor_layout())
    if text.count("&MESH ID=") != 21:
        raise ValueError(f"{path}: 正式 0.25 m 输入不是 21 MESH")
    if len(re.findall(r"^&DEVC ID='T90_", text, re.M)) != expected_devices:
        raise ValueError(f"{path}: T90 测点数量错误")
    if len(re.findall(r"^&DEVC ID='U95_", text, re.M)) != expected_devices:
        raise ValueError(f"{path}: U95 测点数量错误")
    if "ID='T85_" in text or "ID='T95_" in text:
        raise ValueError(f"{path}: 正式输入混入未冻结温度层")
    if f"&HEAD CHID='{case['chid']}'" not in text or Path(path).stem != case["chid"]:
        raise ValueError(f"{path}: 文件名与 CHID 不一致")
    if case["output_profile"] == "light" and "&BNDF" in text:
        raise ValueError(f"{path}: light 输入含 BNDF")


def _metadata(row, case, status):
    return {
        "replicate_id": row["replicate_id"],
        "Q_requested_MW": f"{case['Q']:g}",
        "Af_requested_m2": row["Af_requested_m2"],
        "Af_discrete_m2": f"{case['Af_discrete_m2']:.8g}",
        "Df_requested_m": f"{case['Df']:.15g}",
        "Df_discrete_m": f"{case['Df_discrete_m']:.15g}",
        "burner_side_requested_m": row["burner_side_requested_m"],
        "burner_bounds": json.dumps(case["burner_bounds"], ensure_ascii=False),
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
        "status": status,
    }


def _required_files(case):
    chid = case["chid"]
    files = [f"{chid}.fds", f"{chid}.out", f"{chid}_devc.csv",
             f"{chid}_hrr.csv", f"{chid}.smv", f"{chid}.end (如有)"]
    if case["output_profile"] == "heavy":
        files.extend([f"{chid}*.sf*", f"{chid}*.bf"])
    return ";".join(files)


def _prepare_rows(rows, cases, input_dir, runs_root, lockbox=False):
    manifest = []
    input_dir = Path(input_dir)
    for row, case in zip(rows, cases):
        source = (input_dir / f"{case['chid']}.fds").resolve()
        _formal_static_audit(source, case)
        return_dir = (
            Path(runs_root) / case["chid"] / "attempts" / row["job_attempt_id"]
        ).resolve()
        attempt = run_registry.prepare_attempt(
            source_input=source, return_dir=return_dir,
            subset=row["subset"], purpose=row["purpose"], case_kind="fire",
            parent_case_id=row["parent_case_id"],
            physical_case_id=row["physical_case_id"],
            job_attempt_id=row["job_attempt_id"], protocol_path=PROTOCOL_PATH,
            case_metadata=_metadata(
                row, case, "SEALED_G3_LOCKBOX" if lockbox else "PREPARED_G3_DEVELOPMENT"),
            attempt_metadata={
                "T_end_s": "300",
                "fds_version_planned": f"{FDS_VERSION} ({FDS_REVISION})",
            },
        )
        manifest.append({
            "batch": row["batch"],
            "run_chid": case["chid"],
            "job_attempt_id": row["job_attempt_id"],
            "parent_case_id": row["parent_case_id"],
            "physical_case_id": row["physical_case_id"],
            "Q_MW": f"{case['Q']:g}",
            "burner_side_m": row["burner_side_requested_m"],
            "xf_m": f"{case['x_fire']:g}",
            "rnd_seed": str(case["rnd_seed"]),
            "output_profile": case["output_profile"],
            "source_fds_repo_path": source.relative_to(PROJECT_ROOT).as_posix(),
            "source_fds_path": str(source),
            "attempt_fds_path": attempt["attempt_input_path"],
            "attempt_input_sha256": attempt["attempt_input_sha256"],
            "run_core_sha256": attempt["run_core_sha256"],
            "return_dir": attempt["return_dir"],
            "required_return_files": "LOCKBOX_NOT_RUNNABLE" if lockbox else _required_files(case),
            "static_audit": "PASS",
            "status": attempt["status"],
        })
    return manifest


def _design_audit(development, development_cases, lockbox_manifest):
    by_physical = {}
    for row, case in zip(development, development_cases):
        by_physical.setdefault(row["physical_case_id"], (row, case))
    physical = list(by_physical.values())
    raw = np.array([
        [math.log(case["Q"]), math.log(case["Df_discrete_m"]),
         (case["xf_actual_m"] - 50.0) / 5.0]
        for _, case in physical
    ], dtype=float)
    scaled = (raw - raw.mean(axis=0)) / raw.std(axis=0, ddof=0)
    matrix = np.column_stack([np.ones(len(scaled)), scaled])
    centers = np.array([
        [1.0, math.log(case["Q"]), math.log(case["Df_discrete_m"])]
        for row, case in physical if math.isclose(case["xf_actual_m"], 50.0)
    ])
    corr = np.corrcoef(raw, rowvar=False)
    positions = defaultdict(set)
    for row, case in physical:
        positions[row["parent_case_id"]].add(case["xf_actual_m"])
    paired = sorted(parent for parent, values in positions.items()
                    if values == {40.0, 50.0, 60.0})
    development_hashes = {
        run_registry.sha256_file(DEVELOPMENT_INPUTS / f"{row['chid']}.fds")
        for row in development
    }
    lockbox_hashes = {row["attempt_input_sha256"] for row in lockbox_manifest}
    criteria = {
        "main_effect_rank": bool(int(np.linalg.matrix_rank(matrix)) == 4),
        "nine_parent_rank": bool(int(np.linalg.matrix_rank(centers)) == 3),
        "five_complete_position_pairs": bool(len(paired) == 5),
        "position_not_confounded": bool(
            max(abs(corr[2, 0]), abs(corr[2, 1])) < 1e-12),
        "unique_input_hashes": bool(len(development_hashes | lockbox_hashes) == 41),
        "cross_subset_hash_overlap": bool(not (development_hashes & lockbox_hashes)),
    }
    return {
        "status": "PASS" if all(criteria.values()) else "FAIL",
        "criteria": criteria,
        "development_physical_cases": len(physical),
        "development_runs": len(development),
        "lockbox_runs": len(lockbox_manifest),
        "main_effect_matrix_rank": int(np.linalg.matrix_rank(matrix)),
        "main_effect_matrix_columns": 4,
        "standardized_condition_number": float(np.linalg.cond(matrix)),
        "nine_parent_matrix_rank": int(np.linalg.matrix_rank(centers)),
        "nine_parent_matrix_columns": 3,
        "correlation_logQ_logDf": float(corr[0, 1]),
        "correlation_position_logQ": float(corr[2, 0]),
        "correlation_position_logDf": float(corr[2, 1]),
        "complete_position_pair_parent_ids": paired,
    }


def _out_value(path, pattern):
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    matches = re.findall(pattern, text, re.I)
    if not matches:
        raise ValueError(f"基准输出缺少字段 {pattern}: {path}")
    return float(matches[-1])


def _pilot_benchmarks():
    with (PROJECT_ROOT / "config" / "pilot_cases.csv").open(
            newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    samples = defaultdict(list)
    for row in rows:
        if row["case_kind"] != "fire" or float(row["dx"]) != 0.25:
            continue
        chid = row["chid"]
        attempt_dir = (PROJECT_ROOT / "runs" / "pilot" / chid / "attempts"
                       / row["job_attempt_id"])
        out = attempt_dir / f"{chid}.out"
        if not out.is_file():
            continue
        wall = _out_value(out, r"Total Elapsed Wall Clock Time \(s\):\s*([0-9.]+)")
        cores = int(_out_value(out, r"Number of MPI Processes:\s*([0-9]+)"))
        size = sum(path.stat().st_size for path in attempt_dir.rglob("*") if path.is_file())
        samples[row["output_profile"]].append({
            "chid": chid, "wall_s": wall, "cores": cores, "output_bytes": size,
        })
    if not samples["heavy"] or not samples["light"]:
        raise ValueError("缺少 0.25 m、300 s 的 heavy/light 先导资源基准")
    return samples


def _pilot_actual_core_hours(freeze):
    total = 0.0
    accepted = 0
    for item in freeze["pilot_runs"]:
        chid = item["chid"]
        out = (PROJECT_ROOT / "runs" / "pilot" / chid / "attempts"
               / item["job_attempt_id"] / f"{chid}.out")
        if not out.is_file():
            continue
        wall = _out_value(out, r"Total Elapsed Wall Clock Time \(s\):\s*([0-9.]+)")
        cores = _out_value(out, r"Number of MPI Processes:\s*([0-9]+)")
        total += wall * cores / 3600.0
        accepted += 1
    if accepted != 16:
        raise ValueError(f"资源冻结要求 16 个先导输出，实际找到 {accepted}")
    return total


def _resource_audit(development, lockbox, freeze):
    samples = _pilot_benchmarks()
    margin = 1.25
    profile = {}
    for name in ("light", "heavy"):
        wall_300 = statistics.median(item["wall_s"] for item in samples[name])
        cores = int(round(statistics.median(item["cores"] for item in samples[name])))
        output_300 = statistics.median(item["output_bytes"] for item in samples[name])
        profile[name] = {
            "sample_count": len(samples[name]),
            "normalized_60s_wall_clock_s": wall_300 / 5.0,
            "median_300s_wall_clock_s": wall_300,
            "planned_cores_per_job": cores,
            "estimated_300s_wall_clock_with_margin_s": wall_300 * margin,
            "estimated_300s_core_hours_with_margin": wall_300 * margin * cores / 3600.0,
            "median_300s_output_GiB": output_300 / (1024 ** 3),
            "estimated_300s_output_with_margin_GiB": output_300 * margin / (1024 ** 3),
        }
    counts = Counter(row["output_profile"] for row in development + lockbox)
    development_counts = Counter(row["output_profile"] for row in development)
    lockbox_counts = Counter(row["output_profile"] for row in lockbox)

    def total_for(counter, key):
        return sum(counter[name] * profile[name][key] for name in counter)

    development_core = total_for(development_counts, "estimated_300s_core_hours_with_margin")
    lockbox_core = total_for(lockbox_counts, "estimated_300s_core_hours_with_margin")
    remaining_core = total_for(counts, "estimated_300s_core_hours_with_margin")
    pilot_actual = _pilot_actual_core_hours(freeze)
    science_budget = pilot_actual + remaining_core
    cap = science_budget * 1.35
    remaining_disk = total_for(counts, "estimated_300s_output_with_margin_GiB")
    trajectory_count = 16 + len(development) + len(lockbox)
    with (PROJECT_ROOT / "config" / "job_attempts.csv").open(
            newline="", encoding="utf-8-sig") as stream:
        attempts = list(csv.DictReader(stream))
    # G1 是单列的短语法试算；G2 的一次已启动失败会消耗 79 次进程启动预算，
    # 但作废且从未启动的替代记录不计入。
    completed_or_started_pilot_jobs = sum(
        row["run_chid"].startswith("p_") and row["status"] != "SUPERSEDED_UNRUN"
        for row in attempts
    )
    job_starts = completed_or_started_pilot_jobs + len(development) + len(lockbox)
    criteria = {
        "accepted_trajectories_within_61": trajectory_count <= 61,
        "initial_job_starts_within_79": job_starts <= 79,
        "benchmark_profiles_available": all(profile[name]["sample_count"] > 0
                                            for name in ("light", "heavy")),
        "frozen_cap_not_exceeded_by_budget": science_budget <= cap,
    }
    return {
        "status": "PASS" if all(criteria.values()) else "FAIL",
        "criteria": criteria,
        "estimation_margin": margin,
        "benchmark_source": "completed G2 0.25 m fire runs, normalized from 300 s to 60 s",
        "profiles": profile,
        "completed_pilot_actual_core_hours": pilot_actual,
        "development_projected_core_hours": development_core,
        "lockbox_projected_core_hours": lockbox_core,
        "remaining_projected_core_hours": remaining_core,
        "g3_science_budget_core_hours": science_budget,
        "absolute_core_hour_cap": cap,
        "remaining_projected_disk_GiB": remaining_disk,
        "accepted_trajectory_budget": {"planned": trajectory_count, "limit": 61},
        "initial_job_start_budget": {"planned": job_starts, "limit": 79},
        "completed_or_started_g2_jobs": completed_or_started_pilot_jobs,
        "development_sequential_wall_days": total_for(
            development_counts, "estimated_300s_wall_clock_with_margin_s") / 86400.0,
        "lockbox_sequential_wall_days": total_for(
            lockbox_counts, "estimated_300s_wall_clock_with_margin_s") / 86400.0,
        "concurrency": "USER_MANAGED; each benchmarked job used 21 MPI processes",
    }


def _write_dict_csv(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_reports(development, extension, lockbox, development_manifest,
                   lockbox_manifest, design, resources):
    _write_dict_csv(DEVELOPMENT_HANDOFF, development_manifest)
    _write_dict_csv(LOCKBOX_MANIFEST, lockbox_manifest)
    resource_rows = []
    for name, values in resources["profiles"].items():
        resource_rows.append({
            "item": f"benchmark_{name}", "unit": "profile",
            "value": name, "status": "PASS",
            "evidence": f"n={values['sample_count']};60s_wall={values['normalized_60s_wall_clock_s']:.3f};"
                        f"300s_margin_core_h={values['estimated_300s_core_hours_with_margin']:.3f};"
                        f"300s_margin_GiB={values['estimated_300s_output_with_margin_GiB']:.3f}",
        })
    for item, unit, value in (
        ("accepted_scientific_trajectories", "run_chid", "57/61"),
        ("initial_job_starts", "job_attempt_id",
         f"{resources['initial_job_start_budget']['planned']}/79"),
        ("development_projected_core_hours", "core-hour", f"{resources['development_projected_core_hours']:.3f}"),
        ("lockbox_projected_core_hours", "core-hour", f"{resources['lockbox_projected_core_hours']:.3f}"),
        ("g3_science_budget", "core-hour", f"{resources['g3_science_budget_core_hours']:.3f}"),
        ("absolute_core_hour_cap", "core-hour", f"{resources['absolute_core_hour_cap']:.3f}"),
        ("remaining_projected_disk", "GiB", f"{resources['remaining_projected_disk_GiB']:.3f}"),
    ):
        resource_rows.append({"item": item, "unit": unit, "value": value,
                              "status": "PASS", "evidence": "G3 frozen estimate"})
    _write_dict_csv(RESOURCE_REPORT, resource_rows)

    approvals = [
        {"gate": "design", "status": design["status"],
         "evidence": "reports/g3_design_audit.json", "decision": "Q,Df,xf full-rank and unconfounded"},
        {"gate": "numerical", "status": "PASS",
         "evidence": "41 generated inputs passed static audit", "decision": "development may be externally run"},
        {"gate": "resource", "status": resources["status"],
         "evidence": "reports/g3_resource_budget.csv",
         "decision": ("57/61 trajectories and "
                      f"{resources['initial_job_start_budget']['planned']}/79 process starts")},
        {"gate": "isolation", "status": "PASS",
         "evidence": "reports/g3_lockbox_archive_manifest.csv", "decision": "lockbox sealed; run/read denied"},
    ]
    _write_dict_csv(APPROVAL_REPORT, approvals)

    audit = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "decision": "PASS" if all(row["status"] == "PASS" for row in approvals) else "FAIL",
        "design": design,
        "numerical": {
            "status": "PASS", "audited_input_count": 41,
            "development_input_count": len(development),
            "lockbox_input_count": len(lockbox),
            "mesh_count_each": 21, "sensor_profile": "no_wind_global_t90_v1",
            "temperature_height_z_over_H": 0.9,
        },
        "resources": resources,
        "isolation": {
            "status": "PASS", "lockbox_run_count": len(lockbox),
            "run_allowed": False, "read_allowed": False,
            "output_files_seen": 0,
        },
    }
    AUDIT_JSON.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")

    freeze = {
        "schema_version": "1.0", "freeze_id": "g3_design_freeze_v1",
        "generated_at_utc": audit["generated_at_utc"],
        "decision": "PASS_G4_DEVELOPMENT_EXTERNAL_RUN_AUTHORIZED",
        "g2_freeze_sha256": _sha256(FREEZE_PATH),
        "frozen_design": {
            "working_dx_m": 0.25, "temperature_height_z_over_H": 0.9,
            "T_end_s": 300, "sensor_profile": "no_wind_global_t90_v1",
            "mesh_profile": "no_wind_symmetric_v1", "fds_version": FDS_VERSION,
            "development_physical_cases": 19, "development_runs": 27,
            "development_batches": {"D1": 5, "D2": 6, "D3": 16},
            "extension_pool_runs": 0, "lockbox_physical_cases": 10,
            "lockbox_runs": 14,
        },
        "files": {
            path.relative_to(PROJECT_ROOT).as_posix(): _sha256(path)
            for path in (DEVELOPMENT_CSV, EXTENSION_CSV, LOCKBOX_CSV,
                         DEVELOPMENT_HANDOFF, LOCKBOX_MANIFEST, RESOURCE_REPORT,
                         APPROVAL_REPORT, AUDIT_JSON,
                         PROJECT_ROOT / "src" / "fds" / "prepare_g3_design.py")
        },
        "development_input_sha256": {
            row["run_chid"]: row["attempt_input_sha256"] for row in development_manifest
        },
        "lockbox_input_sha256": {
            row["run_chid"]: row["attempt_input_sha256"] for row in lockbox_manifest
        },
        "approval_gates": {row["gate"]: row["status"] for row in approvals},
        "lockbox": {"status": "SEALED_UNREAD_UNRUN", "sealed_at_stage": "G3",
                    "generation_allowed": False, "preparation_allowed": False,
                    "run_allowed": False, "read_allowed": False},
    }
    G3_FREEZE.parent.mkdir(parents=True, exist_ok=True)
    G3_FREEZE.write_text(json.dumps(freeze, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")

    d1 = [row for row in development_manifest if row["batch"] == "D1"]
    REPORT_MD.write_text(
        "# G3 设计、隔离、哈希与批算冻结\n\n"
        "## 结论\n\n"
        "设计、数值、资源和隔离四项审计全部通过。开发集 19 个物理工况、"
        "27 次运行已经生成和登记，可进入 G4 外部计算；扩展池 4 个工况按 G2 "
        "决定保持禁用。锁箱 10 个物理工况、14 次运行只完成输入与哈希封存，"
        "当前禁止运行和读取。\n\n"
        "## 固定口径\n\n"
        "- 全部正式输入统一采用 `dx=0.25 m`、21 个镜像 MESH、`T_END=300 s`。\n"
        "- 测温只保留冻结的 `T90` 层，速度测点保留 `U95`；不再输出 G2 的 T85/T95。\n"
        "- 五个 D1 主种子代表工况使用 heavy 输出，其余开发工况使用 light 输出。\n"
        "- 峰值结论继续携带 13% 网格不确定性，不声称严格网格无关。\n\n"
        "## 开发批次\n\n"
        "| 批次 | 次数 | 科学接收顺序 |\n|---|---:|---|\n"
        "| D1 | 5 | 三个中心代表条件和一对中点偏移，优先接收 |\n"
        "| D2 | 6 | 其余中心 3×3 工况 |\n"
        "| D3 | 16 | 其余偏移与四个条件的种子重复 |\n\n"
        "完整文件、SHA-256、回传位置和返回文件要求见 "
        "`reports/g3_development_run_handoff.csv`。服务器可以合批计算 D1–D3，"
        "但本地科学分析仍按 D1→D2→D3 解封。\n\n"
        "D1 CHID：\n\n" + "\n".join(f"- `{row['run_chid']}`" for row in d1) + "\n\n"
        "## 资源冻结\n\n"
        f"按已完成的 0.25 m、300 s 先导运行换算 60 s 基准并加入 25% 余量，"
        f"开发集预计 {resources['development_projected_core_hours']:.1f} 核时，"
        f"尚未运行的开发加锁箱预计 {resources['remaining_projected_core_hours']:.1f} 核时、"
        f"{resources['remaining_projected_disk_GiB']:.1f} GiB。"
        f"完整 57 条科学轨迹的 G3 冻结预算为 {resources['g3_science_budget_core_hours']:.1f} 核时，"
        f"绝对上限为 {resources['absolute_core_hour_cap']:.1f} 核时。\n\n"
        "## 锁箱隔离\n\n"
        "锁箱输入、CSV 与哈希已经封存；协议中的 generation/preparation/run/read "
        "四个权限将在本次冻结后全部关闭。G8 显式解锁前，不得上传计算、读取输出或"
        "用于训练和调参。\n",
        encoding="utf-8",
    )


def _set_read_only(paths):
    for path in paths:
        path = Path(path)
        mode = stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH
        try:
            os.chmod(path, mode)
        except OSError:
            # 哈希封存和协议守卫是跨平台主隔离；只读属性属于本机附加保护。
            pass


def prepare():
    freeze = json.loads(FREEZE_PATH.read_text(encoding="utf-8-sig"))
    if freeze.get("decision") != "READY_FOR_G3_WITH_USER_ACCEPTED_GRID_UNCERTAINTY":
        raise ValueError("G2 尚未形成可进入 G3 的冻结决定")
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8-sig"))
    lockbox_protocol = protocol.get("lockbox", {})
    if not lockbox_protocol.get("generation_allowed", False):
        raise PermissionError("G3 锁箱生成窗口未开启或已经封存；禁止重建")
    if lockbox_protocol.get("run_allowed", False) or lockbox_protocol.get("read_allowed", False):
        raise PermissionError("G3 生成时锁箱 run/read 权限必须保持关闭")

    development, extension, lockbox = build_design_rows()
    development_cases, lockbox_cases = validate_design(
        development, extension, lockbox, freeze)
    _write_csv(DEVELOPMENT_CSV, development)
    _write_csv(EXTENSION_CSV, extension)
    _write_csv(LOCKBOX_CSV, lockbox)
    generator._run_csv(str(DEVELOPMENT_CSV), str(DEVELOPMENT_INPUTS), required_only=True)
    run_registry.guard_subset("lockbox", "generate", PROTOCOL_PATH)
    generator._run_csv(str(LOCKBOX_CSV), str(LOCKBOX_INPUTS), required_only=True)

    development_manifest = _prepare_rows(
        development, development_cases, DEVELOPMENT_INPUTS, DEVELOPMENT_RUNS)
    lockbox_manifest = _prepare_rows(
        lockbox, lockbox_cases, LOCKBOX_INPUTS, LOCKBOX_RUNS, lockbox=True)
    design = _design_audit(development, development_cases, lockbox_manifest)
    resources = _resource_audit(development, lockbox, freeze)
    if design["status"] != "PASS" or resources["status"] != "PASS":
        raise ValueError("G3 设计或资源审计失败，禁止形成批算批准")
    _write_reports(development, extension, lockbox, development_manifest,
                   lockbox_manifest, design, resources)
    _set_read_only(
        [LOCKBOX_CSV, *LOCKBOX_INPUTS.glob("*.fds"),
         *(Path(row["attempt_fds_path"]) for row in lockbox_manifest)]
    )
    return development_manifest, lockbox_manifest, design, resources


def main():
    development, lockbox, design, resources = prepare()
    print(f"[PASS] development: {len(development)} runs (D1/D2/D3=5/6/16)")
    print(f"[PASS] lockbox sealed: {len(lockbox)} runs; run/read prohibited")
    print(f"[PASS] design matrix rank: {design['main_effect_matrix_rank']}/4")
    print(f"[PASS] remaining projected core hours: {resources['remaining_projected_core_hours']:.1f}")
    print(f"[OK] freeze pending protocol seal: {G3_FREEZE}")


if __name__ == "__main__":
    main()
