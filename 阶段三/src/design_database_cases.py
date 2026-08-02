"""生成并审计 100 m 条件域协议 V2 的阶段三 68 组外部 FDS 工况。"""
from __future__ import annotations

import argparse
import csv
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path


STAGE3_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = STAGE3_ROOT.parent
STAGE1_SRC = PROJECT_ROOT / "阶段一" / "src"
STAGE2_SRC = PROJECT_ROOT / "阶段二" / "src"
for source_dir in (STAGE1_SRC, STAGE2_SRC):
    sys.path.insert(0, str(source_dir))

import design_pilot_cases as pilot_design  # noqa: E402
import generate_fds_case as fds_generator  # noqa: E402
import tunnel_config as cfg  # noqa: E402


DESIGN_DIR = STAGE3_ROOT / "01_工况设计"
DEFAULT_PILOTS = PROJECT_ROOT / "阶段二" / "01_先导工况设计" / "pilot_cases_candidate.csv"
DEFAULT_OUT = DESIGN_DIR / "database_cases_candidate.csv"
DEFAULT_RUN_MANIFEST = DESIGN_DIR / "external_run_manifest_100m_v2.csv"
DESIGN_SEED = 20260729
DESIGN_STATUS = "AUTHORIZED_100M_V2_WAITING_EXTERNAL_FDS"
REUSE_STATUS = "REUSE_EXISTING_REAL_FDS_100M_V2"
DATA_VERSION = "100m_conditional_domain_v2_input_v1_not_fds_data"
PROTOCOL_VERSION = "100M_CONDITIONAL_DOMAIN_V2"
SCIENTIFIC_CONFIRMATION_STATUS = "DEVELOPMENT_GATE_PASS_CONFIRMATION_PENDING"

CASE_FIELDS = (
    "chid", "subset", "role", "test_role", "case_group", "Q", "U", "Df",
    "dx", "L", "W", "H", "x_fire", "T_end", "n_mesh_x", "n_mesh_y",
    "n_mesh_z", "Uc_m_s", "U_over_Uc", "flow_layer", "chi_r_assumed",
    "critical_velocity_method", "continuous_qdash_kW_m2", "HRRPUA_kW_m2",
    "dataset_group_id", "cv_fold", "access_policy", "design_status",
    "data_version", "protocol_version", "domain_scope",
    "measurement_x_min_m", "measurement_x_max_m", "censoring_allowed",
    "profile_target_policy", "peak_target_policy", "k_u_target_policy",
    "k_d_target_policy", "parameter_mask_required",
    "scientific_confirmation_status", "external_run_required",
    "existing_result_path", "result_source", "reuse_chid", "source_design_id", "parent_chid",
    "les_random_seed", "random_seed_repeat_planned", "design_reason", "note",
)
RUN_MANIFEST_FIELDS = (
    "chid", "subset", "protocol_version", "local_input_path",
    "server_input_path", "server_workdir", "local_return_dir",
    "required_return_files", "completion_evidence_policy",
    "field_return_files_if_needed", "access_policy",
    "scientific_confirmation_status", "external_run_required", "existing_result_path",
)

FLOW_TARGETS = {
    "no_wind": 6,
    "weak": 15,
    "near_critical": 17,
    "strong": 12,
}

TEST_SPECS = (
    ("test_01_unseen_q_low", "unseen_power", 12.5, 0.45, 3.0),
    ("test_02_unseen_q_mid", "unseen_power", 37.5, 1.00, 5.0),
    ("test_03_unseen_q_high", "unseen_power", 72.5, 1.35, 7.0),
    ("test_04_unseen_u_weak", "unseen_wind", 25.0, 0.25, 4.0),
    ("test_05_unseen_u_mid", "unseen_wind", 55.0, 0.75, 6.0),
    ("test_06_unseen_u_strong", "unseen_wind", 90.0, 1.25, 8.0),
    ("test_07_critical_low", "near_critical_difficult", 32.0, 0.97, 4.5),
    ("test_08_critical_high", "near_critical_difficult", 68.0, 1.03, 7.0),
    ("test_09_size_small", "size_boundary", 7.0, 0.60, 1.75),
    ("test_10_size_large", "size_boundary", 100.0, 0.90, 9.0),
    ("test_11_high_strong", "high_power_strong", 85.0, 1.45, 8.0),
    ("test_12_upper_strong", "high_power_strong", 100.0, 1.58, 8.5),
)


def _finite(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 不是有效数值: {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} 必须为有限数值")
    return number


def _read_rows(path):
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise ValueError(f"{path}: 缺少 CSV 表头")
        return list(reader)


def _write_rows(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=CASE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_external_run_manifest(rows, path=DEFAULT_RUN_MANIFEST):
    input_root = PROJECT_ROOT / "阶段一" / "outputs" / "stage3_100m_v2_inputs"
    return_root = PROJECT_ROOT / "阶段一" / "outputs" / "runs"
    manifest = []
    for row in rows:
        chid = row["chid"]
        server_workdir = f"/project/fds_tunnel/stage3_100m_v2/{chid}"
        needs_run = row["external_run_required"] == "yes"
        return_dir = (return_root / chid).resolve() if needs_run else Path(row["existing_result_path"])
        manifest.append({
            "chid": chid, "subset": row["subset"],
            "protocol_version": row["protocol_version"],
            "local_input_path": str((input_root / f"{chid}.fds").resolve()),
            "server_input_path": f"{server_workdir}/{chid}.fds" if needs_run else "",
            "server_workdir": server_workdir if needs_run else "",
            "local_return_dir": str(return_dir),
            "required_return_files": f"{chid}.fds;{chid}.out;{chid}_devc.csv;{chid}_hrr.csv",
            "completion_evidence_policy": "END_OR_SUCCESSFUL_OUT_AND_BOTH_CSV_AT_T_END",
            "field_return_files_if_needed": f"{chid}.smv;{chid}_*.sf*;{chid}_*.bf",
            "access_policy": row["access_policy"],
            "scientific_confirmation_status": row["scientific_confirmation_status"],
            "external_run_required": row["external_run_required"],
            "existing_result_path": row["existing_result_path"],
        })
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=RUN_MANIFEST_FIELDS)
        writer.writeheader(); writer.writerows(manifest)
    return manifest


def _latin_values(count, lower, upper, rng):
    """标准库实现的一维 Latin hypercube 分层样本。"""
    values = [
        lower + (upper - lower) * (index + rng.random()) / count
        for index in range(count)
    ]
    rng.shuffle(values)
    return values


def _flow_layer(ratio, velocity):
    if abs(velocity) <= 1e-12:
        return "no_wind"
    if ratio < 0.85:
        return "weak"
    if ratio <= 1.15:
        return "near_critical"
    return "strong"


def _base_case(chid, Q, ratio, Df, x_fire=50.0):
    Q = _finite(Q, f"{chid}:Q")
    ratio = _finite(ratio, f"{chid}:U_over_Uc")
    Df = _finite(Df, f"{chid}:Df")
    uc, _, _ = pilot_design.li_critical_velocity(Q, cfg.CHI_R_PRESET, cfg.H)
    velocity = ratio * uc
    return {
        "chid": chid, "Q": Q, "U": velocity, "Df": Df, "dx": 0.25,
        "L": 100.0, "W": 10.0, "H": 5.0, "x_fire": x_fire,
        "T_end": 300.0, "n_mesh_x": 11, "n_mesh_y": 1, "n_mesh_z": 2,
        "Uc_m_s": uc, "U_over_Uc": ratio,
        "critical_velocity_method": "Li_2010_piecewise_preliminary",
    }


def _normalize_metrics(case, row_number):
    normalized = fds_generator.normalize_case(case, row_number=row_number)
    x0, x1, y0, y1 = normalized["burner_bounds"]
    discrete_area = (x1 - x0) * (y1 - y0)
    continuous_area = math.pi * normalized["Df"] ** 2 / 4.0
    qdash = normalized["Q"] * 1000.0 / continuous_area
    hrrpua = normalized["Q"] * 1000.0 / discrete_area
    qmin, qmax = cfg.QDASH_CANDIDATE_RANGE
    hmin, hmax = cfg.HRRPUA_CANDIDATE_RANGE
    if not qmin <= qdash <= qmax:
        raise ValueError(
            f"{case['chid']}: 连续 Q/A={qdash:.1f} kW/m2 超出候选范围 [{qmin:g},{qmax:g}]"
        )
    if not hmin <= hrrpua <= hmax:
        raise ValueError(
            f"{case['chid']}: 离散 HRRPUA={hrrpua:.1f} kW/m2 超出候选范围 [{hmin:g},{hmax:g}]"
        )
    return normalized, qdash, hrrpua


def _choose_diameter(Q, target_qdash, x_fire, row_number):
    raw = math.sqrt(4.0 * Q * 1000.0 / (math.pi * target_qdash))
    center = min(9.5, max(1.5, round(raw * 4.0) / 4.0))
    offsets = [0.0]
    for step in range(1, 33):
        offsets.extend((0.25 * step, -0.25 * step))
    for offset in offsets:
        diameter = center + offset
        if not 1.5 <= diameter <= 9.5:
            continue
        candidate = _base_case("diameter_probe", Q, 0.5, diameter, x_fire)
        try:
            _normalize_metrics(candidate, row_number)
        except ValueError:
            continue
        return diameter
    raise ValueError(f"Q={Q:g} MW 找不到满足几何和 HRRPUA 约束的候选 Df")


def _make_row(case, subset, role, reason, row_number, test_role="",
              reuse_chid="", source_design_id="stage3_latin_hypercube"):
    normalized, qdash, hrrpua = _normalize_metrics(case, row_number)
    ratio = _finite(case["U_over_Uc"], f"{case['chid']}:U_over_Uc")
    uc = _finite(case["Uc_m_s"], f"{case['chid']}:Uc_m_s")
    layer = _flow_layer(ratio, normalized["U"])
    if subset == "development":
        access = "MODEL_DEVELOPMENT_AFTER_GATES"
    elif subset == "independent_test":
        access = "SEALED_UNTIL_FINAL_EVALUATION"
    else:
        access = "VALIDATION_ONLY_NOT_CLOSURE_TRAINING"
    repeat_planned = "yes" if case["chid"] in {
        "pilot_06_q40_r100_d45", "gsC_m", "dev_34_near_critical"
    } else "no"
    existing_result_path = ""
    external_run_required = "yes"
    result_source = "WAITING_EXTERNAL_FDS"
    if role == "pilot":
        existing_result_path = str(
            (PROJECT_ROOT / "阶段一" / "outputs" / "pilot_input" / case["chid"]).resolve()
        )
        external_run_required = "no"
        result_source = "REUSE_EXISTING_FORMAL_PILOT_FDS_6.9.1"
    row_design_status = REUSE_STATUS if role == "pilot" else DESIGN_STATUS
    return {
        "chid": case["chid"], "subset": subset, "role": role,
        "test_role": test_role, "case_group": "database_candidate",
        "Q": f"{normalized['Q']:.6f}", "U": f"{normalized['U']:.6f}",
        "Df": f"{normalized['Df']:.6f}", "dx": f"{normalized['dx']:.6f}",
        "L": f"{normalized['L']:.6f}", "W": f"{normalized['W']:.6f}",
        "H": f"{normalized['H']:.6f}", "x_fire": f"{normalized['x_fire']:.6f}",
        "T_end": f"{normalized['T_end']:.6f}",
        "n_mesh_x": str(normalized["n_mesh_x"]),
        "n_mesh_y": str(normalized["n_mesh_y"]),
        "n_mesh_z": str(normalized["n_mesh_z"]), "Uc_m_s": f"{uc:.6f}",
        "U_over_Uc": f"{ratio:.6f}", "flow_layer": layer,
        "chi_r_assumed": f"{cfg.CHI_R_PRESET:.3f}",
        "critical_velocity_method": case["critical_velocity_method"],
        "continuous_qdash_kW_m2": f"{qdash:.3f}",
        "HRRPUA_kW_m2": f"{hrrpua:.3f}",
        "dataset_group_id": case["chid"], "cv_fold": "",
        "access_policy": access, "design_status": row_design_status,
        "data_version": DATA_VERSION, "protocol_version": PROTOCOL_VERSION,
        "domain_scope": "L100_W10_H5_dx0.25_measurement_x15_85",
        "measurement_x_min_m": "15.000000", "measurement_x_max_m": "85.000000",
        "censoring_allowed": "yes",
        "profile_target_policy": "REQUIRED_IF_QUALITY_AND_STEADY_PASS",
        "peak_target_policy": "REQUIRED_WITH_BOOTSTRAP_INTERVAL",
        "k_u_target_policy": "ONLY_IF_UPSTREAM_IDENTIFIABLE_AND_STABLE",
        "k_d_target_policy": "ONLY_IF_DOWNSTREAM_IDENTIFIABLE_AND_STABLE",
        "parameter_mask_required": "yes",
        "scientific_confirmation_status": SCIENTIFIC_CONFIRMATION_STATUS,
        "external_run_required": external_run_required,
        "existing_result_path": existing_result_path, "result_source": result_source,
        "reuse_chid": reuse_chid,
        "source_design_id": source_design_id, "parent_chid": "",
        "les_random_seed": "", "random_seed_repeat_planned": repeat_planned,
        "design_reason": reason,
        "note": (
            "100 m 条件域协议 V2 外部计算输入；协议 V1 的 4/12 FAIL 保持不变；"
            "闭合与反演结论等待开发结果和冻结后的封存测试"
        ),
    }


def _pilot_rows(path):
    rows = _read_rows(path)
    if len(rows) != 12:
        raise ValueError(f"阶段二先导表必须恰有 12 行，当前 {len(rows)}")
    output = []
    for index, row in enumerate(rows, start=2):
        case = {
            key: row[key]
            for key in ("chid", "Q", "U", "Df", "dx", "L", "W", "H",
                        "x_fire", "T_end", "n_mesh_x", "n_mesh_y", "n_mesh_z",
                        "Uc_m_s", "U_over_Uc", "critical_velocity_method")
        }
        output.append(_make_row(
            case, "development", "pilot", row["selection_reason"], index,
            reuse_chid=row["reuse_chid"], source_design_id="stage2_pilot_candidate",
        ))
    return output


def _additional_development(existing_rows, rng):
    counts = Counter(row["flow_layer"] for row in existing_rows)
    required = {layer: FLOW_TARGETS[layer] - counts[layer] for layer in FLOW_TARGETS}
    if any(value < 0 for value in required.values()) or sum(required.values()) != 38:
        raise ValueError(f"先导流态计数与 50 组开发目标不兼容: {dict(counts)}")
    slots = [layer for layer, count in required.items() for _ in range(count)]
    rng.shuffle(slots)
    q_values = _latin_values(len(slots), cfg.Q_RANGE[0], cfg.Q_RANGE[1], rng)
    qdash_targets = _latin_values(len(slots), 750.0, 2600.0, rng)
    ratio_values = {
        "no_wind": [0.0] * required["no_wind"],
        "weak": _latin_values(required["weak"], 0.15, 0.82, rng),
        "near_critical": _latin_values(required["near_critical"], 0.86, 1.14, rng),
        "strong": _latin_values(required["strong"], 1.18, 1.58, rng),
    }
    output = []
    for index, (layer, Q, target_qdash) in enumerate(
        zip(slots, q_values, qdash_targets), start=13
    ):
        ratio = ratio_values[layer].pop()
        diameter = _choose_diameter(Q, target_qdash, cfg.X_FIRE_DEFAULT, index + 1)
        chid = f"dev_{index:02d}_{layer}"
        case = _base_case(chid, Q, ratio, diameter)
        output.append(_make_row(
            case, "development", "lhs_sample",
            f"固定流态配额内的受约束 Latin hypercube 候选（seed={DESIGN_SEED}）",
            index + 1,
        ))
    return output


def _independent_tests(start_row):
    output = []
    for offset, (chid, role, Q, ratio, Df) in enumerate(TEST_SPECS):
        case = _base_case(chid, Q, ratio, Df)
        output.append(_make_row(
            case, "independent_test", "sealed_test", "预设独立测试职责；不得用于调参",
            start_row + offset, test_role=role, source_design_id="stage3_test_spec",
        ))
    return output


def _offset_cases(start_row):
    output = []
    index = 0
    for x_fire, location in ((32.5, "upstream_offset"), (67.5, "downstream_offset")):
        for ratio, layer in ((0.50, "weak"), (1.00, "near_critical"), (1.40, "strong")):
            index += 1
            chid = f"offset_{index:02d}_{location}_{layer}"
            case = _base_case(chid, 40.0, ratio, 5.0, x_fire)
            output.append(_make_row(
                case, "offset_validation", "offset_translation",
                "固定布局下验证坐标平移、峰值偏移和定位，不参与主体闭合",
                start_row + index - 1, source_design_id="stage3_offset_spec",
            ))
    return output


def _assign_cv_folds(rows):
    development = [row for row in rows if row["subset"] == "development"]
    for index, row in enumerate(development):
        row["cv_fold"] = str(index % 5)
    for row in rows:
        if row["subset"] == "independent_test":
            row["cv_fold"] = "SEALED"
        elif row["subset"] == "offset_validation":
            row["cv_fold"] = "VALIDATION_ONLY"


def validate_design(rows):
    if len(rows) != 68:
        raise ValueError(f"数据库候选必须恰有 68 行，当前 {len(rows)}")
    chids = [row["chid"] for row in rows]
    if any(not chid for chid in chids) or len(chids) != len(set(chids)):
        raise ValueError("CHID 为空或重复")
    subset_counts = Counter(row["subset"] for row in rows)
    expected_subsets = {"development": 50, "independent_test": 12, "offset_validation": 6}
    if dict(subset_counts) != expected_subsets:
        raise ValueError(f"子集计数错误: {dict(subset_counts)}")
    development = [row for row in rows if row["subset"] == "development"]
    flow_counts = Counter(row["flow_layer"] for row in development)
    if dict(flow_counts) != FLOW_TARGETS:
        raise ValueError(f"开发集流态计数错误: {dict(flow_counts)}")
    if sum(row["role"] == "pilot" for row in development) != 12:
        raise ValueError("开发集必须包含恰好 12 个先导候选")
    if sum(bool(row["reuse_chid"]) for row in development) != 3:
        raise ValueError("必须恰好复用 3 个中网格工况")
    test_counts = Counter(row["test_role"] for row in rows if row["subset"] == "independent_test")
    if test_counts != Counter({
        "unseen_power": 3, "unseen_wind": 3, "near_critical_difficult": 2,
        "size_boundary": 2, "high_power_strong": 2,
    }):
        raise ValueError(f"独立测试职责计数错误: {dict(test_counts)}")
    offsets = [row for row in rows if row["subset"] == "offset_validation"]
    if {float(row["x_fire"]) for row in offsets} != {32.5, 67.5}:
        raise ValueError("偏移工况位置错误")
    region_lo, region_hi = cfg.measurement_region()
    if any(not region_lo <= float(row["x_fire"]) <= region_hi for row in offsets):
        raise ValueError("偏移火源越出均匀测量区")
    if any(min(float(row["x_fire"]), float(row["L"]) - float(row["x_fire"])) < 5 * float(row["H"])
           for row in offsets):
        raise ValueError("偏移火源距最近洞口不足 5H")
    allowed_statuses = {DESIGN_STATUS, REUSE_STATUS}
    if any(row["design_status"] not in allowed_statuses for row in rows):
        raise ValueError("工况状态不是 V2 复用或等待外部 FDS")
    if any(row["protocol_version"] != PROTOCOL_VERSION for row in rows):
        raise ValueError("存在非 100 m 条件域协议 V2 工况")
    if any(row["scientific_confirmation_status"] != SCIENTIFIC_CONFIRMATION_STATUS
           for row in rows):
        raise ValueError("不得提前宣称 100 m 条件域协议已获独立确认")
    if any(float(row["L"]) != 100.0 or float(row["dx"]) != 0.25 for row in rows):
        raise ValueError("协议 V2 只允许 L=100 m、dx=0.25 m")
    if any(float(row["measurement_x_min_m"]) != 15.0 or
           float(row["measurement_x_max_m"]) != 85.0 for row in rows):
        raise ValueError("协议 V2 测量区必须为 x=15--85 m")
    if any(row["parameter_mask_required"] != "yes" for row in rows):
        raise ValueError("协议 V2 必须启用衰减参数可用性掩码")
    pilots = [row for row in rows if row["role"] == "pilot"]
    if len(pilots) != 12 or any(row["external_run_required"] != "no" or
                                not row["existing_result_path"] or
                                row["design_status"] != REUSE_STATUS for row in pilots):
        raise ValueError("12 组既有先导必须登记为真实结果复用，不能要求重跑")
    new_runs = [row for row in rows if row["role"] != "pilot"]
    if len(new_runs) != 56 or any(row["external_run_required"] != "yes" or
                                  row["existing_result_path"] or
                                  row["design_status"] != DESIGN_STATUS for row in new_runs):
        raise ValueError("阶段三必须恰有 56 组新外部计算")
    tests = [row for row in rows if row["subset"] == "independent_test"]
    if any(row["access_policy"] != "SEALED_UNTIL_FINAL_EVALUATION" for row in tests):
        raise ValueError("独立测试集未封存")
    if any(row["dataset_group_id"] != row["chid"] for row in rows):
        raise ValueError("完整物理工况必须是最小数据分组")
    qmin, qmax = cfg.QDASH_CANDIDATE_RANGE
    hmin, hmax = cfg.HRRPUA_CANDIDATE_RANGE
    if any(not qmin <= float(row["continuous_qdash_kW_m2"]) <= qmax for row in rows):
        raise ValueError("存在连续 Q/A 超出候选范围")
    if any(not hmin <= float(row["HRRPUA_kW_m2"]) <= hmax for row in rows):
        raise ValueError("存在离散 HRRPUA 超出候选范围")
    power_bands = Counter(
        "low" if float(row["Q"]) <= 20 else "mid" if float(row["Q"]) < 50 else "high"
        for row in development
    )
    if any(power_bands[band] == 0 for band in ("low", "mid", "high")):
        raise ValueError(f"开发集未覆盖低/中/高功率: {dict(power_bands)}")
    by_q = defaultdict(set)
    by_df = defaultdict(set)
    for row in development:
        by_q[round(float(row["Q"]), 6)].add(round(float(row["Df"]), 6))
        by_df[round(float(row["Df"]), 6)].add(round(float(row["Q"]), 6))
    matched_chids = {
        row["chid"] for row in development
        if len(by_q[round(float(row["Q"]), 6)]) >= 2
        or len(by_df[round(float(row["Df"]), 6)]) >= 2
    }
    if len(matched_chids) < 8:
        raise ValueError(f"功率/尺寸匹配工况少于 8 个: {len(matched_chids)}")
    folds = Counter(row["cv_fold"] for row in development)
    if folds != Counter({str(index): 10 for index in range(5)}):
        raise ValueError(f"5 折完整工况分组不平衡: {dict(folds)}")
    return {
        "subset_counts": subset_counts,
        "development_flow_counts": flow_counts,
        "power_band_counts": power_bands,
        "matched_case_count": len(matched_chids),
        "hrrpua_min": min(float(row["HRRPUA_kW_m2"]) for row in rows),
        "hrrpua_max": max(float(row["HRRPUA_kW_m2"]) for row in rows),
    }


def build(pilots_path=DEFAULT_PILOTS, out_path=DEFAULT_OUT, seed=DESIGN_SEED):
    rng = random.Random(seed)
    rows = _pilot_rows(pilots_path)
    rows.extend(_additional_development(rows, rng))
    rows.extend(_independent_tests(len(rows) + 2))
    rows.extend(_offset_cases(len(rows) + 2))
    _assign_cv_folds(rows)
    summary = validate_design(rows)
    _write_rows(out_path, rows)
    return rows, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilots", default=str(DEFAULT_PILOTS))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--seed", type=int, default=DESIGN_SEED)
    parser.add_argument("--run-manifest", default=str(DEFAULT_RUN_MANIFEST))
    args = parser.parse_args()
    try:
        rows, summary = build(args.pilots, args.out, args.seed)
        manifest = write_external_run_manifest(rows, args.run_manifest)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"[OK] {len(rows)} 组阶段三候选 -> {args.out}")
    print("子集:", dict(summary["subset_counts"]))
    print("开发流态:", dict(summary["development_flow_counts"]))
    print(
        f"匹配工况={summary['matched_case_count']}，离散 HRRPUA="
        f"{summary['hrrpua_min']:.1f}..{summary['hrrpua_max']:.1f} kW/m2"
    )
    print(f"状态: 12×{REUSE_STATUS}；56×{DESIGN_STATUS}")
    print(f"科学确认: {SCIENTIFIC_CONFIRMATION_STATUS}")
    print(f"外部运行映射: {len(manifest)} 行 -> {args.run_manifest}")


if __name__ == "__main__":
    main()
