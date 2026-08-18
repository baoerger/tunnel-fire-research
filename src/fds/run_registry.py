"""FDS 逻辑轨迹、逐次启动与锁箱访问的最小不可变注册合同。"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .project_paths import PROJECT_ROOT


CASE_FIELDS = (
    "subset", "purpose", "case_kind", "parent_case_id", "physical_case_id",
    "run_chid", "replicate_id", "Q_requested_MW", "Af_requested_m2",
    "Af_discrete_m2", "Df_requested_m", "Df_discrete_m",
    "burner_side_requested_m", "burner_bounds", "xf_requested_m",
    "xf_actual_m", "yf_actual_m", "dx_m", "mesh_profile",
    "sensor_profile", "output_profile", "rnd_seed", "T_end_s",
    "fds_version_planned", "run_core_sha256", "source_input_path", "status",
)
ATTEMPT_FIELDS = (
    "job_attempt_id", "run_chid", "attempt_kind",
    "restart_parent_job_attempt_id", "attempt_input_sha256",
    "run_core_sha256", "attempt_input_path", "return_dir",
    "checkpoint_manifest_sha256", "T_end_s", "fds_version_planned",
    "fds_version_actual", "exit_status", "allocated_cores", "wall_clock_s",
    "prepared_at_utc", "status",
)
_HEAD_RE = re.compile(r"&HEAD\b[^/]*\bCHID\s*=\s*'([^']+)'", re.I | re.S)
_TIME_RE = re.compile(r"(&TIME\b[^/]*\bT_END\s*=\s*)([0-9.eE+-]+)", re.I | re.S)
_RESTART_RE = re.compile(r"(&MISC\b[^/]*\bRESTART\s*=\s*)\.(?:TRUE|FALSE)\.", re.I | re.S)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _input_text(path):
    return Path(path).read_text(encoding="utf-8-sig")


def input_chid(path):
    match = _HEAD_RE.search(_input_text(path))
    if not match:
        raise ValueError(f"输入没有可识别的 HEAD CHID: {path}")
    return match.group(1)


def input_t_end(path):
    match = _TIME_RE.search(_input_text(path))
    if not match:
        raise ValueError(f"输入没有可识别的 TIME T_END: {path}")
    return float(match.group(2))


def run_core_sha256(path):
    """哈希除运行段白名单外的全部输入字节。

    当前可自动归一化的白名单只有 T_END 与 MISC RESTART；任何其他字节变化
    都会改变哈希。真正启用重启文件路径前必须扩展并测试此归一化器。
    """
    text = _input_text(path).replace("\r\n", "\n")
    text, time_count = _TIME_RE.subn(r"\g<1><T_END>", text)
    text, restart_count = _RESTART_RE.subn(r"\g<1>.<RESTART>.", text)
    if time_count != 1 or restart_count != 1:
        raise ValueError(
            f"输入必须各含一条可识别的 T_END 和 RESTART，实际 {time_count}/{restart_count}: {path}"
        )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_rows(path):
    path = Path(path)
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def _write_rows(path, fields, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def guard_subset(subset, operation, protocol_path=None):
    """锁箱未按协议解锁时拒绝生成、运行、训练和读取。"""
    if str(subset).strip().lower() != "lockbox":
        return
    protocol_path = Path(protocol_path or PROJECT_ROOT / "config" / "protocol_v1.json")
    protocol = json.loads(protocol_path.read_text(encoding="utf-8-sig"))
    lockbox = protocol.get("lockbox", {})
    operation = str(operation).strip().lower()
    key = "run_allowed" if operation in {"generate", "prepare", "run"} else "read_allowed"
    if not lockbox.get(key, False):
        raise PermissionError(
            f"锁箱未解锁：subset=lockbox 禁止 {operation or 'access'}（{key}=false）"
        )


def validate_restart_transition(parent_input, child_input, allowed_t_end_s=(300, 450, 600)):
    if input_chid(parent_input) != input_chid(child_input):
        raise ValueError("RESTART 父子输入 CHID 不一致")
    if run_core_sha256(parent_input) != run_core_sha256(child_input):
        raise ValueError("RESTART 父子输入存在非白名单变化")
    parent_end = input_t_end(parent_input)
    child_end = input_t_end(child_input)
    allowed = [float(value) for value in allowed_t_end_s]
    if parent_end not in allowed or child_end not in allowed:
        raise ValueError("RESTART T_END 不在预注册序列")
    if allowed.index(child_end) != allowed.index(parent_end) + 1:
        raise ValueError("RESTART 只能沿预注册 T_END 序列续接一段")
    if not re.search(r"&MISC\b[^/]*\bRESTART\s*=\s*\.TRUE\.",
                     _input_text(child_input), re.I | re.S):
        raise ValueError("RESTART 子输入必须设置 RESTART=.TRUE.")


def prepare_attempt(*, source_input, return_dir, subset, purpose, case_kind,
                    parent_case_id, physical_case_id, job_attempt_id,
                    attempt_kind="initial", restart_parent_job_attempt_id="",
                    checkpoint_manifest=None, protocol_path=None,
                    case_registry_path=None, attempts_path=None,
                    case_metadata=None, attempt_metadata=None):
    """复制输入到唯一回传目录并登记；完全相同的重复准备为幂等操作。"""
    guard_subset(subset, "prepare", protocol_path)
    source_input = Path(source_input).resolve()
    return_dir = Path(return_dir).resolve()
    case_registry_path = Path(
        case_registry_path or PROJECT_ROOT / "config" / "case_registry.csv")
    attempts_path = Path(
        attempts_path or PROJECT_ROOT / "config" / "job_attempts.csv")
    run_chid = input_chid(source_input)
    if source_input.stem != run_chid:
        raise ValueError(f"输入文件名 {source_input.stem!r} 与 CHID {run_chid!r} 不一致")
    core_hash = run_core_sha256(source_input)
    input_hash = sha256_file(source_input)

    attempts = _read_rows(attempts_path)
    existing = next((row for row in attempts
                     if row.get("job_attempt_id") == job_attempt_id), None)
    expected_return_dir = str(return_dir)
    if existing:
        same = (
            existing.get("run_chid") == run_chid
            and existing.get("attempt_input_sha256") == input_hash
            and existing.get("return_dir") == expected_return_dir
        )
        if not same:
            raise ValueError(f"job_attempt_id={job_attempt_id} 已存在，禁止复用或覆盖")
        return existing

    cases = _read_rows(case_registry_path)
    same_run = [row for row in cases if row.get("run_chid") == run_chid]
    if same_run and any(row.get("run_core_sha256") != core_hash for row in same_run):
        raise ValueError(f"run_chid={run_chid} 已绑定不同 run_core_sha256")
    if not same_run:
        case_row = {
            "subset": subset, "purpose": purpose, "case_kind": case_kind,
            "parent_case_id": parent_case_id,
            "physical_case_id": physical_case_id, "run_chid": run_chid,
            "run_core_sha256": core_hash,
            "source_input_path": str(source_input),
        }
        case_metadata = dict(case_metadata or {})
        unknown = set(case_metadata) - set(CASE_FIELDS)
        reserved = set(case_metadata) & set(case_row)
        if unknown:
            raise ValueError("未知 case_registry 字段: " + ",".join(sorted(unknown)))
        if reserved:
            raise ValueError("case_metadata 不得覆盖身份字段: " + ",".join(sorted(reserved)))
        case_row.update(case_metadata)
        cases.append(case_row)

    if attempt_kind not in {"initial", "retry", "restart"}:
        raise ValueError(f"未知 attempt_kind={attempt_kind!r}")
    checkpoint_hash = ""
    if attempt_kind == "restart":
        if not restart_parent_job_attempt_id:
            raise ValueError("restart attempt 必须指定唯一父 attempt")
        parent = next((row for row in attempts
                       if row.get("job_attempt_id") == restart_parent_job_attempt_id), None)
        if not parent or parent.get("run_chid") != run_chid:
            raise ValueError("RESTART 父 attempt 不存在或不属于同一 run_chid")
        if any(row.get("restart_parent_job_attempt_id") == restart_parent_job_attempt_id
               for row in attempts):
            raise ValueError("RESTART 链发生分叉")
        if not checkpoint_manifest or not Path(checkpoint_manifest).is_file():
            raise ValueError("RESTART 必须提供父检查点清单")
        checkpoint_hash = sha256_file(checkpoint_manifest)
        validate_restart_transition(parent["attempt_input_path"], source_input)
    elif restart_parent_job_attempt_id:
        raise ValueError("非 restart attempt 不得登记 restart_parent_job_attempt_id")

    return_dir.mkdir(parents=True, exist_ok=False)
    attempt_input = return_dir / source_input.name
    shutil.copyfile(source_input, attempt_input)
    if sha256_file(attempt_input) != input_hash:
        raise OSError("回传目录中的输入副本哈希不一致")
    row = {
        "job_attempt_id": job_attempt_id, "run_chid": run_chid,
        "attempt_kind": attempt_kind,
        "restart_parent_job_attempt_id": restart_parent_job_attempt_id,
        "attempt_input_sha256": input_hash, "run_core_sha256": core_hash,
        "attempt_input_path": str(attempt_input), "return_dir": str(return_dir),
        "checkpoint_manifest_sha256": checkpoint_hash,
        "prepared_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "AWAITING_EXTERNAL_RUN",
    }
    attempt_metadata = dict(attempt_metadata or {})
    unknown = set(attempt_metadata) - set(ATTEMPT_FIELDS)
    reserved = set(attempt_metadata) & set(row)
    if unknown:
        raise ValueError("未知 job_attempts 字段: " + ",".join(sorted(unknown)))
    if reserved:
        raise ValueError("attempt_metadata 不得覆盖身份字段: " + ",".join(sorted(reserved)))
    row.update(attempt_metadata)
    attempts.append(row)
    _write_rows(case_registry_path, CASE_FIELDS, cases)
    _write_rows(attempts_path, ATTEMPT_FIELDS, attempts)
    return row
