"""Freeze an accepted G4 database and update tracked execution registries."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from src.fds.project_paths import PROJECT_ROOT


PASS_DECISION = "PASS_WITH_USER_VERSION_OVERRIDE_READY_FOR_G5"


def _read_csv(path):
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        return [dict(row) for row in csv.DictReader(stream)]


def _write_csv(path, rows):
    path = Path(path)
    if not rows:
        raise ValueError(f"{path}: 不得写入空表")
    fields = list(rows[0])
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows({key: row.get(key, "") for key in fields} for row in rows)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _exact_revision(repo, run_chid, attempt_id):
    attempt = repo / "runs" / "development" / run_chid / "attempts" / attempt_id
    paths = [attempt / f"{run_chid}.out", *sorted(attempt.glob("*.err"))]
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        match = re.search(r"FDS-\d+\.\d+\.\d+-\d+-g[0-9a-f]+-release", text, re.I)
        if match:
            return match.group(0)
    raise ValueError(f"{run_chid}: 未找到 FDS 精确 revision")


def _verify_database(repo):
    manifest = _read_csv(repo / "reports" / "g4_development_database_manifest.csv")
    expected_names = {
        "case_master.csv", "steady_windows.csv", "curve_points.csv",
        "case_metrics.csv", "run_exclusions.csv", "seed_metrics.csv",
        "translation_metrics.csv", "d1_flame_regime_evidence.csv",
    }
    actual_names = {Path(row["relative_path"]).name for row in manifest}
    if actual_names != expected_names:
        raise ValueError(f"G4 数据库表不闭合: {sorted(actual_names)}")
    for row in manifest:
        path = repo / row["relative_path"]
        if not path.is_file() or _sha256(path) != row["sha256"]:
            raise ValueError(f"G4 数据库哈希失配: {row['relative_path']}")
    return manifest


def _update_registries(repo, technical):
    technical_by_chid = {row["run_chid"]: row for row in technical}

    cases_path = repo / "config" / "development_cases.csv"
    cases = _read_csv(cases_path)
    if len(cases) != 27:
        raise ValueError("开发清单不是 27 条")
    for row in cases:
        row["status"] = "COMPLETED_ACCEPTED_G4"
    _write_csv(cases_path, cases)

    registry_path = repo / "config" / "case_registry.csv"
    registry = _read_csv(registry_path)
    development_count = 0
    for row in registry:
        if row.get("subset") == "development":
            row["status"] = "COMPLETED_ACCEPTED_G4"
            development_count += 1
    if development_count != 27:
        raise ValueError(f"case_registry 开发条数为 {development_count}")
    _write_csv(registry_path, registry)

    attempts_path = repo / "config" / "job_attempts.csv"
    attempts = _read_csv(attempts_path)
    updated = 0
    for row in attempts:
        if row["run_chid"] not in technical_by_chid:
            continue
        evidence = technical_by_chid[row["run_chid"]]
        row["fds_version_actual"] = _exact_revision(
            repo, row["run_chid"], row["job_attempt_id"]
        )
        row["exit_status"] = "FDS_COMPLETED_SUCCESSFULLY"
        row["allocated_cores"] = evidence["allocated_cores"]
        row["wall_clock_s"] = evidence["wall_clock_s"]
        row["status"] = "COMPLETED_ACCEPTED_G4"
        updated += 1
    if updated != 27:
        raise ValueError(f"job_attempts 只更新了 {updated}/27 条")
    _write_csv(attempts_path, attempts)

    handoff_path = repo / "reports" / "g3_development_run_handoff.csv"
    handoff = _read_csv(handoff_path)
    for row in handoff:
        row["status"] = "COMPLETED_ACCEPTED_G4"
    _write_csv(handoff_path, handoff)


def _update_protocol(repo, decision):
    path = repo / "config" / "protocol_v1.json"
    protocol = json.loads(path.read_text(encoding="utf-8"))
    protocol["updated_at"] = datetime.now(timezone.utc).astimezone().isoformat()
    protocol["status"] = "G4_PASS_G5_FORWARD_MODEL_SELECTION_REQUIRED"
    protocol["formal_run_authorized"] = False
    protocol["g3_design"]["formal_run_authorized"] = False
    protocol["g3_design"]["current_batch"] = "COMPLETE"
    protocol["g3_design"]["current_batch_run_chids"] = []
    protocol["g3_design"]["next_action"] = "G5_FORWARD_MODEL_SELECTION_AND_FREEZE_A"
    protocol["g4_development"] = {
        "decision": decision["decision"],
        "version_policy": decision["version_policy"],
        "actual_fds_versions": decision["actual_fds_versions"],
        "formal_baseline_version": decision["formal_baseline_version"],
        "technical_pass": 27,
        "steady_pass": 27,
        "scientific_run_pass": 27,
        "physical_case_count": 19,
        "batch_gates": decision["batch_gates"],
        "seed_gate": decision["seed_gate"],
        "translation_gate": decision["translation_gate"],
        "flame_regime_gate": decision["flame_regime_gate"],
        "effective_domain_xi_h": decision["effective_domain_xi_h"],
        "detection_limit_K": decision["detection_limit_K"],
        "bootstrap_replicates": decision["bootstrap_replicates"],
        "peak_grid_uncertainty_fraction_retained": 0.13,
        "strict_grid_independence_claim_allowed": False,
        "database_manifest": "reports/g4_development_database_manifest.csv",
        "decision_file": "reports/g4_development_decision.json",
        "analysis": "reports/g4_development_analysis.md",
        "freeze_file": "freezes/g4/g4_development_freeze_v1.json",
        "raw_results_gitignored": True,
        "next_action": "G5_FORWARD_MODEL_SELECTION_AND_FREEZE_A",
    }
    path.write_text(json.dumps(protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _analysis_markdown(metrics, seeds, translations, flames):
    max_metric = lambda field: max(float(row[field]) for row in metrics)
    max_seed_peak = max(float(row["deltaT_peak_cv"]) for row in seeds)
    max_seed_curve = max(float(row["curve_max_pairwise_nrmse"]) for row in seeds)
    max_translation = max(float(row["relative_curve_symmetric_nrmse"]) for row in translations)
    min_flame_temperature = min(float(row["near_ceiling_burner_mean_C"]) for row in flames)
    return f"""# G4 开发集接收与不可变数据库

## 结论

27 条开发运行已按 `D1→D2→D3` 全部通过技术和科学接收，对应
19 个物理工况。全部算至 300 s，27/27 找到合格 60 s 同步平均窗；
D1、D2、D3 批次门均为 `PASS`。因用户明确要求忽略版本差异，项目
保留实跑 FDS 6.9.1 与正式基线 6.10.1 的偏差审计，但不再以此阻断 G5。

## 主要数值

- 稳态窗：27/27 通过，不需要延长到 450/600 s。
- 平均窗 HRR 最大闭合误差：`{max_metric('hrr_closure_rel_error_window'):.3%}`。
- 最不利 `A_global` 点估计/95% 上限：`{max_metric('A_global'):.2%}` / `{max_metric('A_global_ci95_upper'):.2%}`。
- 最不利左右镜像 NRMSE：`{max_metric('left_right_nrmse'):.2%}`。
- 有效域内低信号删失比例：最大 `{max_metric('censored_fraction'):.2%}`。
- 4 组三种子重复的最大峰值 CV/曲线 NRMSE：`{max_seed_peak:.2%}` / `{max_seed_curve:.2%}`。
- 5 个位置父组的最不利平移曲线 NRMSE：`{max_translation:.2%}`。
- D1 重场火源上方近顶棚平均温度最低为 `{min_flame_temperature:.1f} °C`，5/5 与冻结的“撞顶/近撞顶”单一层级一致。

## 数据去向

原始结果保留在 Git 忽略的 `runs/development/`；派生数据库位于
`derived/development/`，包含 `case_master/steady_windows/curve_points/case_metrics`
等表。轻量哈希清单见 `reports/g4_development_database_manifest.csv`，可用
`python -m src.analysis.analyze_g4_development` 从原始结果重建。

锁箱仍为 `SEALED_UNREAD_UNRUN`。下一步是 G5 正向候选比较与冻结 A。
"""


def finalize(repo):
    repo = Path(repo).resolve()
    decision_path = repo / "reports" / "g4_development_decision.json"
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    if decision.get("decision") != PASS_DECISION:
        raise ValueError("G4 决策未通过，不得冻结")
    if decision.get("lockbox_status") != "SEALED_UNREAD_UNRUN":
        raise ValueError("锁箱状态不正常")
    manifest = _verify_database(repo)
    technical = _read_csv(repo / "reports" / "g4_development_technical_acceptance.csv")
    if len(technical) != 27 or any(row["status"] != "PASS" for row in technical):
        raise ValueError("27 条技术接收未全部 PASS")
    _update_registries(repo, technical)
    _update_protocol(repo, decision)

    metrics = _read_csv(repo / "derived" / "development" / "case_metrics.csv")
    seeds = _read_csv(repo / "derived" / "development" / "seed_metrics.csv")
    translations = _read_csv(repo / "derived" / "development" / "translation_metrics.csv")
    flames = _read_csv(repo / "derived" / "development" / "d1_flame_regime_evidence.csv")
    report_path = repo / "reports" / "g4_development_analysis.md"
    report_path.write_text(
        _analysis_markdown(metrics, seeds, translations, flames), encoding="utf-8"
    )

    evidence_paths = [
        "config/protocol_v1.json", "config/development_cases.csv",
        "config/case_registry.csv", "config/job_attempts.csv",
        "reports/g3_development_run_handoff.csv",
        "reports/g4_development_decision.json",
        "reports/g4_development_technical_acceptance.csv",
        "reports/g4_development_batch_gates.csv",
        "reports/g4_development_seed_metrics.csv",
        "reports/g4_development_translation_metrics.csv",
        "reports/g4_d1_flame_regime_evidence.csv",
        "reports/g4_accepted_chain_hashes.csv",
        "reports/g4_development_database_manifest.csv",
        "reports/g4_development_analysis.md",
        "src/analysis/analyze_g4_development.py",
        "src/analysis/finalize_g4_development.py",
    ]
    freeze = {
        "schema_version": "1.0",
        "freeze_id": "g4_development_freeze_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "decision": PASS_DECISION,
        "version_policy": decision["version_policy"],
        "actual_fds_versions": decision["actual_fds_versions"],
        "formal_baseline_version": decision["formal_baseline_version"],
        "accepted_runs": 27,
        "physical_cases": 19,
        "batch_gates": decision["batch_gates"],
        "seed_gate": decision["seed_gate"],
        "translation_gate": decision["translation_gate"],
        "flame_regime_gate": decision["flame_regime_gate"],
        "peak_grid_uncertainty_fraction_retained": 0.13,
        "strict_grid_independence_claim_allowed": False,
        "lockbox_status": "SEALED_UNREAD_UNRUN",
        "database": {row["relative_path"]: row["sha256"] for row in manifest},
        "evidence_sha256": {path: _sha256(repo / path) for path in evidence_paths},
        "next_action": "G5_FORWARD_MODEL_SELECTION_AND_FREEZE_A",
    }
    freeze_path = repo / "freezes" / "g4" / "g4_development_freeze_v1.json"
    freeze_path.parent.mkdir(parents=True, exist_ok=True)
    freeze_path.write_text(
        json.dumps(freeze, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return freeze


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(PROJECT_ROOT))
    args = parser.parse_args()
    freeze = finalize(args.repo)
    print(json.dumps(freeze, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
