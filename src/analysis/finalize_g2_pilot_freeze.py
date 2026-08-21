"""汇总 G2 全部先导证据并生成进入 G3 前的唯一冻结文件。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from src.fds import fds_io
from src.fds.project_paths import PROJECT_ROOT


QUALITY_REPORTS = (
    "reports/g2_stage1_background_result_check.csv",
    "reports/g2_stage3_result_check.csv",
    "reports/g2_stage4_result_check.csv",
)
SCRIPT_EVIDENCE = (
    "src/fds/check_fds_results.py",
    "src/fds/fds_io.py",
    "src/analysis/quasi_steady_detect.py",
    "src/analysis/time_average_bootstrap.py",
    "src/analysis/analyze_g2_stage3.py",
    "src/analysis/analyze_g2_stage4.py",
    "src/analysis/finalize_g2_pilot_freeze.py",
)


def _read_csv(path):
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        return [{key: (value or "").strip() for key, value in row.items()}
                for row in csv.DictReader(stream)]


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def heskestad_flame_height_m(q_mw, equivalent_diameter_m):
    if q_mw <= 0 or equivalent_diameter_m <= 0:
        raise ValueError("火焰高度筛查要求正 Q 和正等效直径")
    return -1.02 * equivalent_diameter_m + 0.235 * (q_mw * 1000) ** 0.4


def _window_map(*paths):
    windows = {}
    for path in paths:
        for row in _read_csv(path):
            if row.get("status") == "PASS":
                windows[row["chid"]] = (
                    float(row["avg_t0"]), float(row["avg_t1"]))
    return windows


def _window_mean(times, values, window):
    selected = [value for time, value in zip(times, values)
                if window[0] <= time <= window[1] and math.isfinite(value)]
    if not selected:
        raise ValueError("平均窗内没有有限值")
    return sum(selected) / len(selected)


def _flame_screening(repo, protocol, pilot_cases, windows):
    geometry_h = float(protocol["study"]["geometry_m"]["H"])
    screening = []
    for q_mw in protocol["design"]["Q_MW"]:
        for side_m in protocol["design"]["development_burner_side_m"]:
            diameter = math.sqrt(4 * side_m ** 2 / math.pi)
            height = heskestad_flame_height_m(float(q_mw), diameter)
            screening.append({
                "Q_MW": float(q_mw),
                "burner_side_m": float(side_m),
                "equivalent_diameter_m": diameter,
                "heskestad_Lf_m": height,
                "Lf_minus_H_m": height - geometry_h,
            })

    near_ceiling = []
    for case in pilot_cases:
        if case["case_kind"] != "fire" or case["output_profile"] != "heavy":
            continue
        chid = case["chid"]
        if chid not in windows:
            raise ValueError(f"{chid}: heavy 工况缺少 PASS 平均窗")
        times, series, units = fds_io.read_devc(
            str(repo / "runs" / "pilot"), chid)
        if times is None:
            raise ValueError(f"{chid}: 缺少 DEVC")
        normalized = fds_io.normalize_units(series, units)
        sensor = f"T95_{int(round(float(case['x_fire']) * 100)):04d}"
        if sensor not in normalized:
            raise ValueError(f"{chid}: 缺少火源正上方 {sensor}")
        near_ceiling.append({
            "chid": chid,
            "Q_MW": float(case["Q"]),
            "x_fire_m": float(case["x_fire"]),
            "sensor": sensor,
            "mean_temperature_C": _window_mean(
                times, normalized[sensor], windows[chid]),
        })
    return screening, near_ceiling


def build_freeze(repo):
    repo = Path(repo).resolve()
    with (repo / "config" / "protocol_v1.json").open(encoding="utf-8") as stream:
        protocol = json.load(stream)
    with (repo / "reports" / "g2_stage4_decision.json").open(
            encoding="utf-8") as stream:
        stage4 = json.load(stream)
    if stage4.get("decision") != "PASS_WITH_USER_ACCEPTED_GRID_UNCERTAINTY":
        raise ValueError("阶段 4 未按用户工作网格决定通过")

    pilot_cases = _read_csv(repo / "config" / "pilot_cases.csv")
    handoff = _read_csv(repo / "reports" / "g2_pilot_run_handoff.csv")
    if len(pilot_cases) != 16 or len(handoff) != 16:
        raise ValueError("G2 先导和交接清单必须各有 16 条")
    handoff_by_chid = {row["run_chid"]: row for row in handoff}

    quality = {}
    for relative in QUALITY_REPORTS:
        for row in _read_csv(repo / relative):
            quality[row["chid"]] = row
    expected = {row["chid"] for row in pilot_cases}
    if set(quality) != expected:
        missing = sorted(expected - set(quality))
        extra = sorted(set(quality) - expected)
        raise ValueError(f"质量表工况不闭合: missing={missing}, extra={extra}")
    failed = [chid for chid, row in quality.items() if row["status"] != "PASS"]
    if failed:
        raise ValueError(f"存在未通过的先导结果: {failed}")

    identities = []
    for case in pilot_cases:
        chid = case["chid"]
        registered = handoff_by_chid[chid]
        source = repo / "fds_inputs" / "pilot" / "g2" / f"{chid}.fds"
        attempt = (repo / "runs" / "pilot" / chid / "attempts"
                   / case["job_attempt_id"] / f"{chid}.fds")
        for label, path in (("source", source), ("attempt", attempt)):
            if not path.is_file():
                raise ValueError(f"{chid}: 缺少 {label} 输入 {path}")
        source_hash = _sha256(source)
        attempt_hash = _sha256(attempt)
        registered_hash = registered["attempt_input_sha256"].lower()
        if source_hash != registered_hash or attempt_hash != registered_hash:
            raise ValueError(f"{chid}: 输入哈希与交接清单不一致")
        identities.append({
            "chid": chid,
            "job_attempt_id": case["job_attempt_id"],
            "purpose": case["purpose"],
            "attempt_input_sha256": attempt_hash,
            "run_core_sha256": registered["run_core_sha256"].lower(),
            "fds_version_actual": quality[chid]["fds_version"],
            "quality_status": quality[chid]["status"],
        })

    windows = _window_map(
        repo / "reports" / "g2_stage3_steady_windows.csv",
        repo / "reports" / "g2_stage4_steady_windows.csv")
    if len(windows) != 13:
        raise ValueError(f"13 个火灾工况应有 PASS 平均窗，实际 {len(windows)}")
    screening, near_ceiling = _flame_screening(
        repo, protocol, pilot_cases, windows)

    seed_rows = [row for row in _read_csv(
        repo / "reports" / "g2_stage3_seed_metrics.csv")
                 if row["xi_h"] == "5"]
    max_seed_peak_cv = max(float(row["deltaT_peak_cv"]) for row in seed_rows)
    max_seed_curve_nrmse = max(
        float(row["curve_max_pairwise_nrmse"]) for row in seed_rows)
    script_hashes = {relative: _sha256(repo / relative)
                     for relative in SCRIPT_EVIDENCE}

    return {
        "schema_version": "1.0",
        "freeze_id": "g2_pilot_freeze_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "decision": "READY_FOR_G3_WITH_USER_ACCEPTED_GRID_UNCERTAINTY",
        "protocol_id": protocol["protocol_id"],
        "version_policy": "USER_DIRECTED_IGNORE_VERSION_DIFFERENCE",
        "frozen_values": {
            "working_dx_m": 0.25,
            "strict_grid_independence_claim_allowed": False,
            "peak_grid_uncertainty_fraction": 0.13,
            "temperature_height_z_over_H": 0.90,
            "temperature_sensor_prefix": "T90",
            "mean_window_s": 60,
            "minimum_sufficient_T_END_s": 300,
            "detection_limit_C": 0.5,
            "effective_domain_xi_h": 5,
            "effective_domain_m": 25.0,
            "opening_buffer_h": 3.0,
            "allowed_x_fire_m": [40.0, 60.0],
            "flame_regime_layer": "CEILING_IMPINGING_OR_NEAR_IMPINGING",
            "development_extension_pool_enabled": False,
        },
        "uncertainty_floor_components": {
            "absolute_detection_limit_C": 0.5,
            "maximum_pilot_seed_peak_cv": max_seed_peak_cv,
            "maximum_pilot_seed_curve_nrmse": max_seed_curve_nrmse,
            "peak_grid_uncertainty_fraction": 0.13,
            "combination_rule": (
                "时间块不确定性按工况估计；种子、网格和后续模型残差作为独立"
                "分量保留，不在 G2 中伪合并为单一已知标准差。"),
        },
        "flame_regime_evidence": {
            "classification": "CONSERVATIVE_SINGLE_CEILING_INTERACTING_LAYER",
            "heskestad_is_screening_not_fds_truth": True,
            "full_design_screening": screening,
            "heavy_pilot_near_ceiling_temperature": near_ceiling,
            "development_recheck_required": True,
            "stop_if_mixed_regime_observed": True,
        },
        "unselected_or_failed_candidates": {
            "strict_grid_gate": {
                "status": "FAIL_RETAINED_AS_EVIDENCE",
                "reason": "18/36 MW 中-细峰值差 12.79%/8.34% 超过 7.5%",
                "evidence": "reports/g2_stage3_grid_metrics.csv",
            },
            "effective_domain_xi_h": {
                "selected": 5,
                "unselected_but_passing": [4, 3],
            },
            "temperature_height_priority": {
                "selected_first_passing": 0.90,
                "not_selected": [0.85, 0.95],
            },
            "development_extension_pool": {
                "status": "DISABLED",
                "reason": "主体结果不可见时没有充分证据证明必须为 S3 增加 4 个工况",
            },
        },
        "evidence_files": {
            "stage1": "reports/g2_stage1_background_analysis.md",
            "stage2": "reports/g2_stage2_analysis.md",
            "stage3": "reports/g2_stage3_analysis.md",
            "stage3_user_mesh_decision": "reports/g2_stage3_working_mesh_decision.md",
            "stage4": "reports/g2_stage4_analysis.md",
        },
        "script_sha256": script_hashes,
        "pilot_runs": identities,
        "pilot_run_count": len(identities),
        "raw_results_gitignored": True,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=str(PROJECT_ROOT))
    parser.add_argument("--out", default=str(
        PROJECT_ROOT / "freezes" / "pilot" / "pilot_freeze_v1.json"))
    args = parser.parse_args()
    freeze = build_freeze(args.repo)
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(freeze, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    print(json.dumps({
        "decision": freeze["decision"],
        "pilot_run_count": freeze["pilot_run_count"],
        "effective_domain_xi_h": freeze["frozen_values"]["effective_domain_xi_h"],
        "output": str(output),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
