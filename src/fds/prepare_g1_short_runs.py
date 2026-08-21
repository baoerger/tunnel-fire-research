"""生成、静态审计并登记 G1 外部短试算输入。"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from . import generate_fds_case as generator
from . import run_registry
from .project_paths import PROJECT_ROOT


DEFAULT_CASES = PROJECT_ROOT / "config" / "no_wind" / "g1_short_cases.csv"
DEFAULT_INPUT_DIR = PROJECT_ROOT / "fds_inputs" / "pilot" / "g1_short"
DEFAULT_MANIFEST = PROJECT_ROOT / "reports" / "g1_short_run_handoff.csv"


def static_audit(path, case):
    text = Path(path).read_text(encoding="utf-8")
    issues = []
    if not text.rstrip().endswith("&TAIL /"):
        issues.append("TAIL 不是最后记录")
    expected_meshes = 18 if abs(float(case["dx"]) - 0.5) < 1e-10 else 21
    if text.count("&MESH ID=") != expected_meshes:
        issues.append(f"MESH 数不是 {expected_meshes}")
    if text.count("SURF_ID='OPEN'") != 2:
        issues.append("两端 OPEN 数量错误")
    for forbidden in ("SURF ID='INLET'", "RAMP_V", "U VELOCITY",
                      "CONVECTIVE HRR", "SIMPLE_CHEMISTRY"):
        if forbidden in text:
            issues.append(f"存在禁止项 {forbidden}")
    device_ids = re.findall(r"&DEVC\b[^/]*\bID='([^']+)'", text, re.I | re.S)
    duplicates = sorted({value for value in device_ids if device_ids.count(value) > 1})
    if duplicates:
        issues.append("DEVC ID 重复: " + ",".join(duplicates))
    if case["sensor_profile"] == "no_wind_global_t90_v1":
        required_prefixes = ("T90_", "U95_")
        for forbidden_prefix in ("T85_", "T95_"):
            if any(value.startswith(forbidden_prefix) for value in device_ids):
                issues.append(f"正式单层配置不应含 {forbidden_prefix} 设备")
    else:
        required_prefixes = ("T85_", "T90_", "T95_", "U95_")
    for prefix in required_prefixes:
        if not any(value.startswith(prefix) for value in device_ids):
            issues.append(f"缺少 {prefix} 设备")
    if case["case_kind"] == "background":
        for forbidden in ("&REAC", "BURNER", "HRR_tot"):
            if forbidden in text:
                issues.append(f"背景工况存在 {forbidden}")
    else:
        for required in ("&REAC", "SURF ID='BURNER'", "SURF_ID='BURNER'",
                         "HRR_tot", "DT_HRR="):
            if required not in text:
                issues.append(f"火灾工况缺少 {required}")
    if case["output_profile"] == "heavy" and "&BNDF" not in text:
        issues.append("heavy 工况缺少 BNDF")
    if issues:
        raise ValueError(f"{path} 静态审计失败: {'; '.join(issues)}")


def prepare(cases_path=DEFAULT_CASES, input_dir=DEFAULT_INPUT_DIR,
            manifest_path=DEFAULT_MANIFEST):
    cases_path = Path(cases_path)
    input_dir = Path(input_dir)
    with cases_path.open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError("G1 短试算表为空")

    generator._run_csv(str(cases_path), str(input_dir))
    manifest = []
    for line_number, row in enumerate(rows, start=2):
        case = generator.normalize_case(row, row_number=line_number)
        source = (input_dir / f"{case['chid']}.fds").resolve()
        static_audit(source, case)
        return_dir = (
            PROJECT_ROOT / "runs" / row["subset"] / case["chid"]
            / "attempts" / row["job_attempt_id"]
        ).resolve()
        attempt = run_registry.prepare_attempt(
            source_input=source, return_dir=return_dir,
            subset=row["subset"], purpose=row["purpose"],
            case_kind=case["case_kind"], parent_case_id=row["parent_case_id"],
            physical_case_id=row["physical_case_id"],
            job_attempt_id=row["job_attempt_id"],
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
        manifest.append({
            "run_chid": case["chid"],
            "job_attempt_id": row["job_attempt_id"],
            "case_kind": case["case_kind"],
            "Q_MW": f"{case['Q']:g}", "T_end_s": f"{case['T_end']:g}",
            "output_profile": case["output_profile"],
            "source_fds_path": str(source),
            "attempt_fds_path": attempt["attempt_input_path"],
            "attempt_input_sha256": attempt["attempt_input_sha256"],
            "run_core_sha256": attempt["run_core_sha256"],
            "return_dir": attempt["return_dir"],
            "required_return_files": ";".join(required),
            "static_audit": "PASS",
        })

    manifest_path = Path(manifest_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=manifest[0].keys())
        writer.writeheader()
        writer.writerows(manifest)
    return manifest


def main():
    rows = prepare()
    for row in rows:
        print(f"[PASS] {row['run_chid']} {row['attempt_input_sha256']}")
        print(f"       FDS: {row['source_fds_path']}")
        print(f"       RETURN: {row['return_dir']}")
    print(f"[OK] manifest: {DEFAULT_MANIFEST}")


if __name__ == "__main__":
    main()
