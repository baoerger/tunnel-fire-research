"""Capture the G5-SR runtime contract without opening any research data.

The command reads only the approved machine/study protocol JSON files and
software metadata.  It checks declared development inputs with ``is_file``
only; it never imports or opens development/lockbox data.  Julia backend
versions and smoke-test measurements must be supplied explicitly because a
metadata capture must not pretend that it ran an expensive Julia probe.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import math
import platform
from pathlib import Path
import re
import subprocess
import sys

from src.analysis import g5_sr_protocol as protocol_guard


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MACHINE_PROTOCOL = Path("config/symbolic_regression_v1.json")
STUDY_PROTOCOL = Path("config/protocol_v1.json")
DEFAULT_OUTPUT = Path("reports/g5_sr_environment.json")
MISSING_G4_DERIVED_INPUTS = protocol_guard.MISSING_G4_DERIVED_INPUTS
G5_SR_EXECUTION_SUPPLEMENT_NOT_APPROVED = (
    protocol_guard.G5_SR_EXECUTION_SUPPLEMENT_NOT_APPROVED
)
EXPECTED_DEVELOPMENT_INPUTS = {
    "derived/development/case_metrics.csv",
    "derived/development/curve_points.csv",
}
FORMAL_EXECUTION_ARTIFACTS = (
    "src/analysis/g5_sr_protocol.py",
    "src/analysis/g5_sr_interval.py",
    "src/analysis/run_g5_symbolic_search.py",
    "src/analysis/evaluate_g5_symbolic_search.py",
    "pyproject.toml",
    "uv.lock",
)


class G5SREnvironmentError(ValueError):
    """Raised when environment evidence is incomplete or unauthorised."""


def _read_json(path):
    path = Path(path)
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise G5SREnvironmentError(f"缺少协议文件: {path}") from exc
    except json.JSONDecodeError as exc:
        raise G5SREnvironmentError(f"协议 JSON 无效: {path}: {exc}") from exc


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _repo_path(repo, relative):
    repo = Path(repo).resolve()
    candidate = (repo / str(relative)).resolve()
    try:
        candidate.relative_to(repo)
    except ValueError as exc:
        raise G5SREnvironmentError(f"协议路径越出项目根目录: {relative}") from exc
    return candidate


def _approved_protocol(repo):
    machine_path = _repo_path(repo, MACHINE_PROTOCOL)
    study_path = _repo_path(repo, STUDY_PROTOCOL)
    machine = _read_json(machine_path)
    study = _read_json(study_path)

    approval = machine.get("approval", {})
    if (
        machine.get("status") != "SR_PROTOCOL_APPROVED"
        or approval.get("decision") != "SR_PROTOCOL_APPROVED"
    ):
        raise G5SREnvironmentError("G5-SR 机器协议尚未正式批准")

    study_sr = study.get("g5_symbolic_regression", {})
    if study_sr.get("status") != "SR_PROTOCOL_APPROVED":
        raise G5SREnvironmentError("项目协议未登记 SR_PROTOCOL_APPROVED")
    declared_path = str(study_sr.get("machine_protocol", "")).replace("\\", "/")
    if declared_path != MACHINE_PROTOCOL.as_posix():
        raise G5SREnvironmentError("项目协议引用了不同的 G5-SR 机器协议")

    actual_hash = _sha256_file(machine_path)
    declared_hash = str(study_sr.get("machine_protocol_sha256", "")).lower()
    if not declared_hash or declared_hash != actual_hash:
        raise G5SREnvironmentError(
            "批准协议哈希失配: "
            f"declared={declared_hash or '<missing>'}, actual={actual_hash}"
        )

    lockbox = study.get("lockbox", {})
    forbidden = {
        str(value).lower()
        for value in machine.get("data_contract", {}).get("forbidden_subsets", [])
    }
    if (
        lockbox.get("status") != "SEALED_UNREAD_UNRUN"
        or lockbox.get("sealed_at_stage") != "G3"
        or any(
            lockbox.get(key) is not False
            for key in (
                "generation_allowed",
                "preparation_allowed",
                "run_allowed",
                "read_allowed",
            )
        )
        or "lockbox" not in forbidden
    ):
        raise G5SREnvironmentError("锁箱隔离协议不满足 G5-SR 环境捕获要求")

    return machine, study, actual_hash


def _package_versions():
    versions = {}
    for package in ("mpmath", "numpy", "scipy", "sympy", "pysr"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def _uv_version():
    try:
        result = subprocess.run(
            ["uv", "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"available": False, "version": None, "raw": None}
    raw = (result.stdout or result.stderr).strip()
    match = re.match(r"^uv\s+([^\s]+)", raw)
    return {
        "available": result.returncode == 0,
        "version": match.group(1) if result.returncode == 0 and match else None,
        "raw": raw or None,
    }


def _python_platform():
    return {
        "version": platform.python_version(),
        "implementation": platform.python_implementation(),
        "executable_name": Path(sys.executable).name,
        "system": platform.system(),
        "release": platform.release(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "architecture_bits": platform.architecture()[0],
    }


def _development_input_status(repo, machine):
    sources = machine.get("data_contract", {}).get("required_sources", [])
    relative_paths = {
        str(source.get("relative_path", "")).replace("\\", "/")
        for source in sources
    }
    if relative_paths != EXPECTED_DEVELOPMENT_INPUTS:
        raise G5SREnvironmentError(
            "环境合同只允许检查两张已批准的 development 输入表是否存在"
        )

    checked = []
    for relative in sorted(relative_paths):
        path = _repo_path(repo, relative)
        checked.append({"relative_path": relative, "present": path.is_file()})
    missing = [item["relative_path"] for item in checked if not item["present"]]
    return {
        "status": "BLOCKED" if missing else "AVAILABLE_NOT_READ",
        "blocker_code": MISSING_G4_DERIVED_INPUTS if missing else None,
        "required_inputs": checked,
        "missing_inputs": missing,
        "content_read": False,
        "check_method": "PATH_EXISTENCE_ONLY",
        "development_run_directory_present": _repo_path(
            repo, "runs/development"
        ).is_dir(),
    }


def _formal_execution_artifacts(repo):
    """Hash the fixed formal search/evaluation code and dependency locks."""
    files = []
    for relative in FORMAL_EXECUTION_ARTIFACTS:
        path = _repo_path(repo, relative)
        if not path.is_file():
            raise G5SREnvironmentError(f"缺少正式执行关键文件: {relative}")
        files.append(
            {
                "relative_path": relative,
                "sha256": _sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
    return {
        "hash_algorithm": "SHA-256",
        "files": files,
    }


def _positive_finite(value, label, *, allow_zero=False):
    value = float(value)
    valid = math.isfinite(value) and (value >= 0 if allow_zero else value > 0)
    if not valid:
        comparator = "非负" if allow_zero else "正"
        raise G5SREnvironmentError(f"{label} 必须是有限{comparator}数")
    return value


def build_report(
    *,
    repo=PROJECT_ROOT,
    julia_version,
    symbolic_regression_version,
    smoke_status,
    smoke_equation,
    smoke_max_abs_error,
    smoke_fit_seconds,
    cold_start_seconds,
    generated_at=None,
    package_versions=None,
    uv_info=None,
    python_info=None,
):
    """Build a JSON-compatible environment report without opening data tables."""
    repo = Path(repo).resolve()
    machine, study, protocol_hash = _approved_protocol(repo)

    if not str(julia_version).strip() or not str(symbolic_regression_version).strip():
        raise G5SREnvironmentError(
            "Julia 与 SymbolicRegression 版本必须来自已验证 smoke 的显式参数"
        )
    if smoke_status != "SMOKE_PASS":
        raise G5SREnvironmentError("正式环境合同只接受已完成的 SMOKE_PASS 证据")
    if not str(smoke_equation).strip():
        raise G5SREnvironmentError("SMOKE_PASS 必须提供返回表达式")

    smoke_error = _positive_finite(
        smoke_max_abs_error, "smoke_max_abs_error", allow_zero=True
    )
    fit_seconds = _positive_finite(smoke_fit_seconds, "smoke_fit_seconds")
    cold_seconds = _positive_finite(cold_start_seconds, "cold_start_seconds")
    packages = dict(package_versions) if package_versions is not None else _package_versions()
    uv = dict(uv_info) if uv_info is not None else _uv_version()
    py = dict(python_info) if python_info is not None else _python_platform()
    if not isinstance(py.get("version"), str) or not py["version"].strip():
        raise G5SREnvironmentError("Python 版本不能为空")
    missing_packages = [
        name
        for name in ("mpmath", "numpy", "scipy", "sympy", "pysr")
        if not isinstance(packages.get(name), str) or not packages[name].strip()
    ]
    if missing_packages:
        raise G5SREnvironmentError(
            "正式 Python 环境缺少版本: " + ", ".join(missing_packages)
        )
    data_status = _development_input_status(repo, machine)
    execution_supplement = protocol_guard.inspect_execution_supplement(
        repo, machine, study, protocol_hash
    )
    execution_artifacts = _formal_execution_artifacts(repo)
    lockbox = study["lockbox"]

    blocker_codes = []
    if not execution_supplement["approved"]:
        blocker_codes.append(G5_SR_EXECUTION_SUPPLEMENT_NOT_APPROVED)
    if data_status["status"] == "BLOCKED":
        blocker_codes.append(MISSING_G4_DERIVED_INPUTS)

    if generated_at is None:
        generated_at = datetime.now(timezone.utc).isoformat()

    if data_status["status"] == "BLOCKED":
        overall = "ENVIRONMENT_SMOKE_PASS_DATA_BLOCKED"
    elif not execution_supplement["approved"]:
        overall = "ENVIRONMENT_SMOKE_PASS_EXECUTION_SUPPLEMENT_BLOCKED"
    else:
        overall = "ENVIRONMENT_SMOKE_PASS_INPUTS_AVAILABLE_NOT_READ"
    return {
        "schema_version": "1.0",
        "stage": "G5_SR_ENVIRONMENT",
        "generated_at": generated_at,
        "status": overall,
        "formal_search_authorized": not blocker_codes,
        "blocker_code": blocker_codes[0] if blocker_codes else None,
        "blocker_codes": blocker_codes,
        "approved_protocol": {
            "protocol_id": machine.get("protocol_id"),
            "status": machine.get("status"),
            "path": MACHINE_PROTOCOL.as_posix(),
            "approved_sha256": protocol_hash,
            "declared_sha256": study["g5_symbolic_regression"].get(
                "machine_protocol_sha256"
            ),
            "hash_match": True,
        },
        "execution_supplement": execution_supplement,
        "python": py,
        "tooling": {"uv": uv},
        "python_packages": {
            "mpmath": packages.get("mpmath"),
            "numpy": packages.get("numpy"),
            "scipy": packages.get("scipy"),
            "sympy": packages.get("sympy"),
            "pysr": packages.get("pysr"),
        },
        "formal_execution_artifacts": execution_artifacts,
        "julia_backend": {
            "julia_version": str(julia_version),
            "symbolic_regression_version": str(symbolic_regression_version),
            "version_evidence": "EXPLICIT_CLI_FROM_VERIFIED_SMOKE",
            "automatic_probe_performed_by_capture": False,
        },
        "smoke_test": {
            "status": smoke_status,
            "data_source": "SYNTHETIC_IN_MEMORY",
            "project_or_lockbox_data_read": False,
            "samples": 41,
            "x_domain": [-2.0, 2.0],
            "target": "y=x^2",
            "parallelism": "serial",
            "random_seed": 123,
            "binary_operators": ["+", "*"],
            "niterations": 4,
            "populations": 1,
            "population_size": 30,
            "ncycles_per_iteration": 50,
            "maxsize": 7,
            "equation": str(smoke_equation),
            "max_abs_error": smoke_error,
            "fit_seconds": fit_seconds,
            "cold_start_seconds": cold_seconds,
        },
        "ai_feynman": {
            "role": "OPTIONAL_SECONDARY_STRUCTURE_AUDIT",
            "status": "OPTIONAL_NOT_INSTALLED_NON_BLOCKING",
            "hard_dependency": False,
            "blocks_primary_pysr_search": False,
            "default_install_probe": "FAILED_DEPRECATED_SKLEARN_PLACEHOLDER",
            "override_resolution_probe": "RESOLVED_NOT_INSTALLED",
        },
        "lockbox": {
            "protocol_status": lockbox.get("status"),
            "sealed_at_stage": lockbox.get("sealed_at_stage"),
            "run_allowed": lockbox.get("run_allowed"),
            "read_allowed": lockbox.get("read_allowed"),
            "used_by_environment_capture": False,
            "used_by_g5_sr": False,
            "paths_accessed": [],
        },
        "data_preflight": data_status,
    }


def write_report(report, output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(PROJECT_ROOT))
    parser.add_argument("--julia-version", required=True)
    parser.add_argument("--symbolic-regression-version", required=True)
    parser.add_argument("--smoke-status", choices=("SMOKE_PASS",), required=True)
    parser.add_argument("--smoke-equation", required=True)
    parser.add_argument("--smoke-max-abs-error", type=float, required=True)
    parser.add_argument("--smoke-fit-seconds", type=float, required=True)
    parser.add_argument("--cold-start-seconds", type=float, required=True)
    parser.add_argument("--generated-at")
    parser.add_argument(
        "--output",
        help=(
            "write JSON to this path; omitted by default so the command is read-only "
            "and prints to stdout"
        ),
    )
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    report = build_report(
        repo=args.repo,
        julia_version=args.julia_version,
        symbolic_regression_version=args.symbolic_regression_version,
        smoke_status=args.smoke_status,
        smoke_equation=args.smoke_equation,
        smoke_max_abs_error=args.smoke_max_abs_error,
        smoke_fit_seconds=args.smoke_fit_seconds,
        cold_start_seconds=args.cold_start_seconds,
        generated_at=args.generated_at,
    )
    if args.output:
        output = _repo_path(args.repo, args.output)
        write_report(report, output)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    main()
