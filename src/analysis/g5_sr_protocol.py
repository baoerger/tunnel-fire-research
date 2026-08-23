"""G5-SR machine-protocol and development-data preflight guards.

This module deliberately does not run symbolic regression.  It establishes the
approved, hash-addressed inputs and refuses a formal search unless the G4
development tables are intact and the independent lockbox remains sealed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
from importlib import metadata
import json
import math
import platform
from pathlib import Path
import re

from src.fds.project_paths import PROJECT_ROOT


MACHINE_PROTOCOL = Path("config/symbolic_regression_v1.json")
STUDY_PROTOCOL = Path("config/protocol_v1.json")
EXECUTION_SUPPLEMENT = Path("config/g5_sr_execution_supplement_v1.json")
EXECUTION_SUPPLEMENT_DRAFT = Path(
    "config/g5_sr_execution_supplement_v1.draft.json"
)
A0_MANIFEST = Path("freezes/A0/manifest.json")
ENVIRONMENT_REPORT = Path("reports/g5_sr_environment.json")
SHAPE_EXPANSION_DECISION = Path(
    "reports/g5_sr_shape_expansion_decision.json"
)
APPROVED_PROTOCOL_SHA256 = (
    "453c6a9dbc6ba19d096c9ae6dfb6836b82df3f4ef8501b17a41e60f12dc21a8b"
)
MISSING_G4_DERIVED_INPUTS = "MISSING_G4_DERIVED_INPUTS"
G5_SR_EXECUTION_SUPPLEMENT_NOT_APPROVED = (
    "G5_SR_EXECUTION_SUPPLEMENT_NOT_APPROVED"
)
G5_SR_SHAPE_EXPANSION_NOT_AUTHORIZED = (
    "G5_SR_SHAPE_EXPANSION_NOT_AUTHORIZED"
)
APPROVED_EXECUTION_SUPPLEMENT_STATUS = (
    "G5_SR_EXECUTION_SUPPLEMENT_APPROVED"
)
APPROVED_EXECUTION_SUPPLEMENT_STATE = "AUTHORIZED_FOR_FORMAL_SEARCH"
FORMAL_EXECUTION_ARTIFACTS = (
    "src/analysis/g5_sr_protocol.py",
    "src/analysis/g5_sr_interval.py",
    "src/analysis/build_g5_sr_time_blocks.py",
    "src/analysis/run_g5_symbolic_search.py",
    "src/analysis/evaluate_g5_symbolic_search.py",
    "pyproject.toml",
    "uv.lock",
)


class G5SRProtocolError(ValueError):
    """Raised when an approved G5-SR contract is internally inconsistent."""


class G5SRDataUnavailableError(FileNotFoundError):
    """Raised when an immutable G4 development input is not locally available."""


class G5SRExecutionSupplementNotApprovedError(PermissionError):
    """Raised before formal work when the execution supplement is not approved."""


class G5SRShapeExpansionNotAuthorizedError(PermissionError):
    """Raised before the conditional xi/q/d shape campaign is launched."""


def sha256_file(path):
    """Return the SHA-256 digest of *path* without normalizing its bytes."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path):
    path = Path(path)
    if not path.is_file():
        raise G5SRDataUnavailableError(f"缺少 G5-SR 必需文件: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise G5SRProtocolError(f"JSON 无效: {path}: {exc}") from exc


def _read_csv(path):
    path = Path(path)
    if not path.is_file():
        raise G5SRDataUnavailableError(f"缺少 G5-SR 必需文件: {path}")
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise G5SRProtocolError(f"CSV 缺少表头: {path}")
        return list(reader), list(reader.fieldnames)


def _repo_path(repo, relative_path):
    """Resolve a protocol-relative path and reject repository escape."""
    repo = Path(repo).resolve()
    candidate = (repo / str(relative_path)).resolve()
    try:
        candidate.relative_to(repo)
    except ValueError as exc:
        raise G5SRProtocolError(f"协议路径越出项目根目录: {relative_path}") from exc
    return candidate


def _require_keys(mapping, keys, label):
    missing = [key for key in keys if key not in mapping]
    if missing:
        raise G5SRProtocolError(f"{label} 缺少字段: {', '.join(missing)}")


def _expected_source_map(machine):
    sources = machine["data_contract"].get("required_sources", [])
    result = {}
    for source in sources:
        _require_keys(
            source,
            ("relative_path", "sha256", "bytes", "row_count", "required_columns"),
            "required_sources",
        )
        relative = str(source["relative_path"])
        if relative in result:
            raise G5SRProtocolError(f"required_sources 路径重复: {relative}")
        result[relative] = source
    required = {
        "derived/development/case_metrics.csv",
        "derived/development/curve_points.csv",
    }
    if set(result) != required:
        raise G5SRProtocolError(
            "G5-SR 正式输入必须且只能是开发集 case_metrics.csv 与 curve_points.csv"
        )
    return result


def _validate_approval(machine, study, machine_path):
    if machine.get("schema_version") != "1.0":
        raise G5SRProtocolError("不支持的 G5-SR 机器协议版本")
    if machine.get("status") != "SR_PROTOCOL_APPROVED":
        raise PermissionError("G5-SR 尚未获得 SR_PROTOCOL_APPROVED")
    approval = machine.get("approval", {})
    if approval.get("decision") != "SR_PROTOCOL_APPROVED":
        raise PermissionError("G5-SR 审批记录不完整")
    if machine.get("execution_state") != "APPROVED_NOT_RUN":
        raise G5SRProtocolError("本机器协议必须保持 APPROVED_NOT_RUN，不得回填搜索结果")

    state = study.get("g5_symbolic_regression", {})
    if state.get("status") != "SR_PROTOCOL_APPROVED":
        raise PermissionError("项目协议尚未登记 SR_PROTOCOL_APPROVED")
    expected_path = str(state.get("machine_protocol", "")).replace("\\", "/")
    actual_suffix = MACHINE_PROTOCOL.as_posix()
    if expected_path != actual_suffix:
        raise G5SRProtocolError(
            f"项目协议引用的机器协议路径不一致: {expected_path!r} != {actual_suffix!r}"
        )
    expected_hash = str(state.get("machine_protocol_sha256", "")).lower()
    actual_hash = sha256_file(machine_path)
    if expected_hash != actual_hash:
        raise G5SRProtocolError(
            f"G5-SR 机器协议哈希失配: expected={expected_hash}, actual={actual_hash}"
        )
    return actual_hash


def inspect_execution_supplement(repo, machine, study, parent_protocol_hash):
    """Inspect supplement authorization without opening any research data.

    A draft is a valid repository state for static inspection, but it never
    authorizes a formal search.  An approved supplement is accepted only when
    its exact bytes are addressed by the study protocol and its parent binding
    still matches the approved symbolic-regression protocol.
    """
    repo = Path(repo).resolve()
    parent_protocol_hash = str(parent_protocol_hash).strip().lower()
    state = study.get("g5_symbolic_regression", {}).get(
        "execution_supplement", {}
    )
    issues = []
    if not isinstance(state, dict):
        state = {}
        issues.append("项目协议缺少 execution_supplement 对象")

    status = state.get("status")
    draft_relative = str(state.get("draft_path", "")).replace("\\", "/")
    approved_relative = str(state.get("approved_path", "")).replace("\\", "/")
    declared_approved_hash = state.get("approved_sha256")
    if draft_relative != EXECUTION_SUPPLEMENT_DRAFT.as_posix():
        issues.append("执行补充协议草案路径不一致")
    if approved_relative != EXECUTION_SUPPLEMENT.as_posix():
        issues.append("执行补充协议批准版路径不一致")

    draft_path = _repo_path(repo, EXECUTION_SUPPLEMENT_DRAFT)
    approved_path = _repo_path(repo, EXECUTION_SUPPLEMENT)
    if status != APPROVED_EXECUTION_SUPPLEMENT_STATUS:
        if status != "DRAFT_REQUIRES_USER_APPROVAL":
            issues.append("执行补充协议状态不是待批准草案")
        if declared_approved_hash is not None:
            issues.append("待批准状态的 approved_sha256 必须为 null")
        if approved_path.exists():
            issues.append("待批准状态下不得存在批准版执行补充协议")
        if not draft_path.is_file():
            issues.append("缺少执行补充协议草案")
        else:
            try:
                draft = _read_json(draft_path)
            except (G5SRDataUnavailableError, G5SRProtocolError) as exc:
                issues.append(str(exc))
            else:
                if not isinstance(draft, dict):
                    issues.append("执行补充协议草案必须是 JSON 对象")
                else:
                    if draft.get("status") != "DRAFT_REQUIRES_USER_APPROVAL":
                        issues.append("执行补充协议草案未保持待批准状态")
                    if draft.get("execution_state") != "NOT_AUTHORIZED_FOR_FORMAL_SEARCH":
                        issues.append("执行补充协议草案错误地授权了正式搜索")
                    parent = draft.get("parent_protocol", {})
                    if (
                        str(parent.get("relative_path", "")).replace("\\", "/")
                        != MACHINE_PROTOCOL.as_posix()
                        or parent.get("protocol_id") != machine.get("protocol_id")
                        or str(parent.get("sha256", "")).strip().lower()
                        != parent_protocol_hash
                    ):
                        issues.append("执行补充协议草案与父协议绑定不一致")
        return {
            "status": status or "UNREGISTERED",
            "approved": False,
            "blocker_code": G5_SR_EXECUTION_SUPPLEMENT_NOT_APPROVED,
            "draft_path": EXECUTION_SUPPLEMENT_DRAFT.as_posix(),
            "approved_path": EXECUTION_SUPPLEMENT.as_posix(),
            "approved_sha256": None,
            "validation_issues": issues,
        }

    if not isinstance(declared_approved_hash, str) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", declared_approved_hash
    ):
        issues.append("项目协议缺少有效的执行补充协议 approved_sha256")
    if not approved_path.is_file():
        issues.append("缺少批准版执行补充协议")

    supplement = None
    actual_hash = None
    if approved_path.is_file():
        try:
            supplement = _read_json(approved_path)
        except (G5SRDataUnavailableError, G5SRProtocolError) as exc:
            issues.append(str(exc))
        else:
            if not isinstance(supplement, dict):
                issues.append("执行补充协议批准版必须是 JSON 对象")
                supplement = None
            else:
                actual_hash = sha256_file(approved_path)
                if (
                    isinstance(declared_approved_hash, str)
                    and actual_hash != declared_approved_hash.lower()
                ):
                    issues.append("执行补充协议批准版哈希与项目协议不一致")

    if supplement is not None:
        if supplement.get("supplement_id") != "g5_sr_execution_supplement_v1":
            issues.append("执行补充协议 ID 不一致")
        if supplement.get("status") != APPROVED_EXECUTION_SUPPLEMENT_STATUS:
            issues.append("执行补充协议文件未记录明确批准状态")
        if supplement.get("execution_state") != APPROVED_EXECUTION_SUPPLEMENT_STATE:
            issues.append("执行补充协议文件未授权正式搜索")
        approval = supplement.get("approval", {})
        if (
            not isinstance(approval, dict)
            or approval.get("decision") != APPROVED_EXECUTION_SUPPLEMENT_STATUS
            or approval.get("approved_by") != "USER"
            or not isinstance(approval.get("approved_date"), str)
            or not approval["approved_date"].strip()
        ):
            issues.append("执行补充协议缺少明确用户批准记录")
        parent = supplement.get("parent_protocol", {})
        study_parent = study.get("g5_symbolic_regression", {})
        if (
            str(parent.get("relative_path", "")).replace("\\", "/")
            != MACHINE_PROTOCOL.as_posix()
            or parent.get("protocol_id") != machine.get("protocol_id")
            or parent.get("required_status") != machine.get("status")
            or str(parent.get("sha256", "")).strip().lower()
            != parent_protocol_hash
            or str(study_parent.get("machine_protocol", "")).replace("\\", "/")
            != MACHINE_PROTOCOL.as_posix()
            or str(study_parent.get("machine_protocol_sha256", "")).strip().lower()
            != parent_protocol_hash
        ):
            issues.append("批准版执行补充协议与父协议哈希或身份不一致")

    return {
        "status": status,
        "approved": not issues,
        "blocker_code": (
            None if not issues else G5_SR_EXECUTION_SUPPLEMENT_NOT_APPROVED
        ),
        "draft_path": EXECUTION_SUPPLEMENT_DRAFT.as_posix(),
        "approved_path": EXECUTION_SUPPLEMENT.as_posix(),
        "approved_sha256": actual_hash if not issues else None,
        "validation_issues": issues,
    }


def require_execution_supplement_approved(
    repo=PROJECT_ROOT, machine_protocol_path=None, study_protocol_path=None
):
    """Return an approved supplement record or stop before formal execution."""
    repo = Path(repo).resolve()
    machine_path = Path(machine_protocol_path or repo / MACHINE_PROTOCOL).resolve()
    study_path = Path(study_protocol_path or repo / STUDY_PROTOCOL).resolve()
    machine = _read_json(machine_path)
    study = _read_json(study_path)
    parent_hash = sha256_file(machine_path)
    result = inspect_execution_supplement(repo, machine, study, parent_hash)
    if not result["approved"]:
        detail = "; ".join(result["validation_issues"]) or "执行补充协议仍待批准"
        raise G5SRExecutionSupplementNotApprovedError(
            f"{G5_SR_EXECUTION_SUPPLEMENT_NOT_APPROVED}: {detail}"
        )
    return result


def load_approved_execution_supplement(
    repo=PROJECT_ROOT, machine_protocol_path=None, study_protocol_path=None
):
    """Load the exact approved supplement together with both protocol hashes.

    Unlike :func:`inspect_execution_supplement`, this is an authorization
    interface: the parent protocol must itself pass its approval/hash checks,
    and the approved supplement bytes must still match the study protocol.
    The returned ``record`` is therefore safe to use as the source of frozen
    execution details; callers must not substitute the draft file.
    """
    repo = Path(repo).resolve()
    machine_path = Path(machine_protocol_path or repo / MACHINE_PROTOCOL).resolve()
    study_path = Path(study_protocol_path or repo / STUDY_PROTOCOL).resolve()
    machine = _read_json(machine_path)
    study = _read_json(study_path)
    parent_hash = _validate_approval(machine, study, machine_path)
    inspected = inspect_execution_supplement(
        repo, machine, study, parent_hash
    )
    if not inspected["approved"]:
        detail = "; ".join(inspected.get("validation_issues", []))
        detail = detail or "执行补充协议仍待批准"
        raise G5SRExecutionSupplementNotApprovedError(
            f"{G5_SR_EXECUTION_SUPPLEMENT_NOT_APPROVED}: {detail}"
        )

    approved_path = _repo_path(repo, EXECUTION_SUPPLEMENT)
    record = _read_json(approved_path)
    supplement_hash = sha256_file(approved_path)
    if supplement_hash != inspected.get("approved_sha256"):
        raise G5SRExecutionSupplementNotApprovedError(
            f"{G5_SR_EXECUTION_SUPPLEMENT_NOT_APPROVED}: "
            "执行补充协议读取期间发生哈希漂移"
        )
    return {
        "record": record,
        "path": EXECUTION_SUPPLEMENT.as_posix(),
        "sha256": supplement_hash,
        "parent_protocol_sha256": parent_hash,
    }


def _shape_expansion_denied(issues):
    detail = "; ".join(str(issue) for issue in issues if str(issue).strip())
    detail = detail or "条件形状扩展未获得授权"
    raise G5SRShapeExpansionNotAuthorizedError(
        f"{G5_SR_SHAPE_EXPANSION_NOT_AUTHORIZED}: {detail}"
    )


def require_shape_expansion_authorized(
    repo=PROJECT_ROOT,
    *,
    machine_protocol_sha256=None,
    execution_supplement_sha256=None,
    supplement=None,
    machine_protocol_path=None,
    study_protocol_path=None,
):
    """Require a strict, current authorization for ``shape_xi_q_d``.

    The decision is deliberately fail-closed.  A missing or malformed report,
    a stale parent/supplement hash, incomplete fold selection, leakage flag, or
    an unevaluated/false trigger family cannot authorize the conditional
    campaign.  The report is only an inter-phase gate; it never authorizes a
    lockbox read or any FDS work.
    """
    repo = Path(repo).resolve()
    approved = load_approved_execution_supplement(
        repo,
        machine_protocol_path=machine_protocol_path,
        study_protocol_path=study_protocol_path,
    )
    machine_path = Path(machine_protocol_path or repo / MACHINE_PROTOCOL).resolve()
    machine = _read_json(machine_path)
    approved_record = approved["record"]
    issues = []

    if supplement is not None:
        if not isinstance(supplement, dict) or supplement != approved_record:
            issues.append("调用方提供的执行补充协议不是当前批准版正文")

    expected_parent_hash = approved["parent_protocol_sha256"]
    expected_supplement_hash = approved["sha256"]
    if machine_protocol_sha256 is not None:
        supplied = str(machine_protocol_sha256).strip().lower()
        if supplied != expected_parent_hash:
            issues.append("调用方提供的父协议 SHA-256 与当前批准版不一致")
    if execution_supplement_sha256 is not None:
        supplied = str(execution_supplement_sha256).strip().lower()
        if supplied != expected_supplement_hash:
            issues.append("调用方提供的执行补充协议 SHA-256 与当前批准版不一致")

    sequence = approved_record.get("shape_search_sequence")
    if not isinstance(sequence, dict):
        _shape_expansion_denied(issues + ["批准版补充协议缺少 shape_search_sequence"])
    phase_1 = sequence.get("phase_1")
    phase_2 = sequence.get("phase_2")
    expansion = sequence.get("expansion_trigger")
    if not isinstance(phase_1, dict):
        issues.append("批准版补充协议缺少 shape phase_1")
        phase_1 = {}
    if not isinstance(phase_2, dict):
        issues.append("批准版补充协议缺少 shape phase_2")
        phase_2 = {}
    if not isinstance(expansion, dict):
        issues.append("批准版补充协议缺少 expansion_trigger")
        expansion = {}

    source_campaign = phase_1.get("campaign_id")
    target_campaign = phase_2.get("campaign_id")
    if source_campaign != "shape_xi_only":
        issues.append("批准版补充协议的源 campaign 不是 shape_xi_only")
    if target_campaign != "shape_xi_q_d":
        issues.append("批准版补充协议的目标 campaign 不是 shape_xi_q_d")
    if phase_1.get("required_first") is not True:
        issues.append("批准版补充协议未要求 shape_xi_only 先运行")
    if phase_2.get("conditional") is not True:
        issues.append("批准版补充协议未把 shape_xi_q_d 标为条件 campaign")
    if phase_2.get("launch_requires_trigger_pass") is not True:
        issues.append("批准版补充协议未要求扩展触发通过")
    if expansion.get("logic") != "OR":
        issues.append("批准版补充协议的扩展触发逻辑不是 OR")
    declared_decision_path = str(
        expansion.get("decision_artifact_required_before_phase_2", "")
    ).replace("\\", "/")
    if declared_decision_path != SHAPE_EXPANSION_DECISION.as_posix():
        issues.append("批准版补充协议的形状扩展决策路径不一致")

    approved_conditions = expansion.get("conditions")
    approved_condition_ids = []
    if not isinstance(approved_conditions, list) or not approved_conditions:
        issues.append("批准版补充协议没有形状扩展触发条件")
    else:
        for item in approved_conditions:
            condition_id = item.get("condition_id") if isinstance(item, dict) else None
            if not isinstance(condition_id, str) or not condition_id.strip():
                issues.append("批准版补充协议含无效 condition_id")
                continue
            approved_condition_ids.append(condition_id)
        if len(approved_condition_ids) != len(set(approved_condition_ids)):
            issues.append("批准版补充协议的 condition_id 重复")

    decision_path = _repo_path(repo, SHAPE_EXPANSION_DECISION)
    if not decision_path.is_file():
        _shape_expansion_denied(issues + ["缺少形状扩展决策文件"])
    try:
        decision = _read_json(decision_path)
    except (G5SRDataUnavailableError, G5SRProtocolError) as exc:
        _shape_expansion_denied(issues + [str(exc)])
    if not isinstance(decision, dict):
        _shape_expansion_denied(issues + ["形状扩展决策必须是 JSON 对象"])

    for key, expected in (
        ("schema_version", "1.0"),
        ("stage", "G5_SR_SHAPE_EXPANSION_DECISION"),
        ("status", "SHAPE_EXPANSION_TRIGGERED"),
        ("decision", "RUN_SHAPE_XI_Q_D"),
        ("source_campaign", "shape_xi_only"),
        ("target_campaign", "shape_xi_q_d"),
        ("trigger_logic", "OR"),
    ):
        if decision.get(key) != expected:
            issues.append(f"形状扩展决策字段 {key} 必须为 {expected}")
    if decision.get("source_campaign") != source_campaign:
        issues.append("形状扩展决策的 source_campaign 与补充协议不一致")
    if decision.get("target_campaign") != target_campaign:
        issues.append("形状扩展决策的 target_campaign 与补充协议不一致")
    if decision.get("launch_authorized") is not True:
        issues.append("形状扩展决策未显式设置 launch_authorized=true")
    if decision.get("lockbox_used") is not False:
        issues.append("形状扩展决策必须显式记录 lockbox_used=false")
    if decision.get("outer_holdout_used_for_selection") is not False:
        issues.append(
            "形状扩展决策必须显式记录 outer_holdout_used_for_selection=false"
        )

    report_parent_hash = str(
        decision.get("machine_protocol_sha256", "")
    ).strip().lower()
    report_supplement_hash = str(
        decision.get("execution_supplement_sha256", "")
    ).strip().lower()
    if report_parent_hash != expected_parent_hash:
        issues.append("形状扩展决策的父协议 SHA-256 不一致")
    if report_supplement_hash != expected_supplement_hash:
        issues.append("形状扩展决策的执行补充协议 SHA-256 不一致")

    try:
        outer_fold_count = machine["search_budget"]["outer_folds"]["count"]
    except (KeyError, TypeError):
        outer_fold_count = None
        issues.append("父协议缺少外层折数")
    if type(outer_fold_count) is not int or outer_fold_count != 9:
        issues.append("父协议的形状扩展外层折数必须严格为 9")
    selected_count = decision.get("outer_fold_selected_count")
    if type(selected_count) is not int or selected_count != outer_fold_count:
        issues.append("形状扩展决策没有九折各一个的唯一选择记录")

    trigger_tests = decision.get("trigger_tests")
    triggered_condition_ids = []
    seen_condition_ids = []
    if not isinstance(trigger_tests, list) or not trigger_tests:
        issues.append("形状扩展决策缺少 trigger_tests")
    else:
        for test in trigger_tests:
            if not isinstance(test, dict):
                issues.append("trigger_tests 项必须是对象")
                continue
            condition_id = test.get("condition_id")
            if not isinstance(condition_id, str) or not condition_id.strip():
                issues.append("trigger_tests 项缺少 condition_id")
                continue
            seen_condition_ids.append(condition_id)
            if test.get("evaluated") is not True:
                issues.append(f"触发条件 {condition_id} 未明确 evaluated=true")
            if type(test.get("triggered")) is not bool:
                issues.append(f"触发条件 {condition_id} 的 triggered 不是布尔值")
            elif test["triggered"]:
                triggered_condition_ids.append(condition_id)
        if len(seen_condition_ids) != len(set(seen_condition_ids)):
            issues.append("trigger_tests 的 condition_id 重复")
        if set(seen_condition_ids) != set(approved_condition_ids):
            issues.append("trigger_tests 未完整且唯一覆盖补充协议的全部触发条件")
        if not triggered_condition_ids:
            issues.append("没有任何批准的形状扩展条件被触发")

    if issues:
        _shape_expansion_denied(issues)
    decision_hash = sha256_file(decision_path)
    return {
        "authorized": True,
        "decision_path": SHAPE_EXPANSION_DECISION.as_posix(),
        "decision_sha256": decision_hash,
        "machine_protocol_sha256": expected_parent_hash,
        "execution_supplement_sha256": expected_supplement_hash,
        "source_campaign": source_campaign,
        "target_campaign": target_campaign,
        "triggered_condition_ids": triggered_condition_ids,
        "record": decision,
    }


def _validate_lockbox_isolation(repo, machine, study):
    lockbox = study.get("lockbox", {})
    if lockbox.get("status") != "SEALED_UNREAD_UNRUN":
        raise PermissionError("锁箱状态不是 SEALED_UNREAD_UNRUN，G5-SR 不得启动")
    if lockbox.get("sealed_at_stage") != "G3":
        raise PermissionError("锁箱 sealed_at_stage 必须为 G3")
    for key in ("generation_allowed", "preparation_allowed", "run_allowed", "read_allowed"):
        if lockbox.get(key) is not False:
            raise PermissionError(f"锁箱权限 {key} 必须显式为 false")

    contract = machine.get("data_contract", {})
    if str(contract.get("allowed_subset", "")).lower() != "development":
        raise G5SRProtocolError("G5-SR 唯一允许的数据子集必须是 development")
    forbidden = {str(value).lower() for value in contract.get("forbidden_subsets", [])}
    if "lockbox" not in forbidden:
        raise G5SRProtocolError("G5-SR 机器协议必须显式禁止 lockbox")

    allowed_root = (Path(repo).resolve() / "derived" / "development").resolve()
    for relative in _expected_source_map(machine):
        source = _repo_path(repo, relative)
        try:
            source.relative_to(allowed_root)
        except ValueError as exc:
            raise PermissionError(f"G5-SR 输入不在开发派生目录内: {relative}") from exc


def _validate_a0_identity(repo, machine):
    identity = machine.get("freeze_identity", {})
    a0 = identity.get("A0", {})
    if a0.get("role") != "IMMUTABLE_HUMAN_CANDIDATE_BASELINE":
        raise G5SRProtocolError("M1_S1 必须登记为不可变人工候选基准 A0")
    if identity.get("A_SR", {}).get("state") != "NOT_CREATED":
        raise G5SRProtocolError("正式搜索前 A_SR 必须为 NOT_CREATED")
    if identity.get("A_active", {}).get("state") != "PENDING_G5_SR_DECISION":
        raise G5SRProtocolError("正式搜索前 A_active 必须为 PENDING_G5_SR_DECISION")

    expected = str(a0.get("sha256", "")).lower()
    canonical = _repo_path(repo, a0.get("canonical_file", ""))
    legacy = _repo_path(repo, a0.get("legacy_compatibility_file", ""))
    for label, path in (("A0", canonical), ("A0 历史兼容副本", legacy)):
        if not path.is_file():
            raise G5SRDataUnavailableError(f"{label} 不存在: {path}")
        actual = sha256_file(path)
        if actual != expected:
            raise G5SRProtocolError(
                f"{label} 哈希失配: expected={expected}, actual={actual}"
            )
    if canonical.read_bytes() != legacy.read_bytes():
        raise G5SRProtocolError("A0 与历史兼容文件不是逐字节相同副本")

    manifest = _read_json(_repo_path(repo, A0_MANIFEST))
    if (
        manifest.get("identity") != "A0"
        or manifest.get("role") != "IMMUTABLE_HUMAN_CANDIDATE_BASELINE"
        or str(manifest.get("canonical_sha256", "")).lower() != expected
        or str(manifest.get("legacy_sha256", "")).lower() != expected
    ):
        raise G5SRProtocolError("A0 兼容清单与机器协议不一致")
    return expected


def _validate_g4_metadata(repo, machine):
    contract = machine["data_contract"]
    decision = _read_json(_repo_path(repo, contract["g4_decision"]))
    if decision.get("decision") != contract["g4_required_decision"]:
        raise G5SRProtocolError("G4 决策未达到 G5-SR 前置状态")
    if decision.get("lockbox_status") != "SEALED_UNREAD_UNRUN":
        raise PermissionError("G4 决策表明锁箱曾被触及，拒绝 G5-SR")

    rows, columns = _read_csv(_repo_path(repo, contract["g4_manifest"]))
    _require_keys(
        {column: True for column in columns},
        ("relative_path", "sha256", "bytes", "row_count"),
        "G4 数据清单",
    )
    indexed = {row["relative_path"].replace("\\", "/"): row for row in rows}
    for relative, expected in _expected_source_map(machine).items():
        row = indexed.get(relative)
        if row is None:
            raise G5SRProtocolError(f"G4 数据清单缺少: {relative}")
        for field in ("sha256", "bytes", "row_count"):
            if str(row[field]).lower() != str(expected[field]).lower():
                raise G5SRProtocolError(
                    f"G4 数据清单 {relative} 的 {field} 与批准协议不一致"
                )


def _current_runtime_versions():
    """Return the Python-side versions used by this preflight process."""
    versions = {"python": platform.python_version()}
    for package in ("mpmath", "numpy", "scipy", "sympy", "pysr"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def _validate_formal_execution_artifacts(repo, report):
    evidence = report.get("formal_execution_artifacts", {})
    if evidence.get("hash_algorithm") != "SHA-256":
        raise G5SRProtocolError("环境证据未使用 SHA-256 记录正式执行文件")
    items = evidence.get("files")
    if not isinstance(items, list):
        raise G5SRProtocolError("环境证据缺少正式执行文件哈希列表")

    indexed = {}
    for item in items:
        if not isinstance(item, dict):
            raise G5SRProtocolError("正式执行文件哈希记录无效")
        relative = str(item.get("relative_path", "")).replace("\\", "/")
        if not relative or relative in indexed:
            raise G5SRProtocolError("正式执行文件路径缺失或重复")
        indexed[relative] = item
    if set(indexed) != set(FORMAL_EXECUTION_ARTIFACTS):
        raise G5SRProtocolError("环境证据中的正式执行文件集合与固定合同不一致")

    verified = {}
    for relative in FORMAL_EXECUTION_ARTIFACTS:
        path = _repo_path(repo, relative)
        if not path.is_file():
            raise G5SRProtocolError(f"正式执行关键文件缺失: {relative}")
        expected = str(indexed[relative].get("sha256", "")).strip().lower()
        actual = sha256_file(path)
        if expected != actual:
            raise G5SRProtocolError(
                f"正式执行文件环境漂移: {relative}: expected={expected}, actual={actual}"
            )
        try:
            recorded_bytes = int(indexed[relative].get("bytes"))
        except (TypeError, ValueError) as exc:
            raise G5SRProtocolError(
                f"正式执行文件字节数记录无效: {relative}"
            ) from exc
        if recorded_bytes != path.stat().st_size:
            raise G5SRProtocolError(f"正式执行文件字节数漂移: {relative}")
        verified[relative] = actual
    return verified


def _validate_environment_evidence(repo, machine, protocol_hash):
    """Validate the immutable runtime evidence required for a formal search.

    This guard is deliberately called only after the G4 derived inputs pass
    their complete contract.  Consequently, a missing development-data export
    remains the first actionable blocker; once those inputs are restored, an
    environment report captured while they were absent is rejected as stale.
    """
    declared_path = str(
        machine.get("engine_contract", {}).get(
            "exact_environment_record", ENVIRONMENT_REPORT.as_posix()
        )
    ).replace("\\", "/")
    if declared_path != ENVIRONMENT_REPORT.as_posix():
        raise G5SRProtocolError(
            "G5-SR 环境证据路径必须为 reports/g5_sr_environment.json"
        )
    path = _repo_path(repo, declared_path)
    if not path.is_file():
        raise G5SRProtocolError(f"缺少 G5-SR 环境证据: {path}")
    report = _read_json(path)

    normalized_hash = str(protocol_hash).strip().lower()
    if normalized_hash != APPROVED_PROTOCOL_SHA256:
        raise G5SRProtocolError(
            "当前 G5-SR 协议不是正式批准的固定 SHA-256: "
            f"expected={APPROVED_PROTOCOL_SHA256}, actual={normalized_hash}"
        )
    approved = report.get("approved_protocol", {})
    for field in ("approved_sha256", "declared_sha256"):
        value = str(approved.get(field, "")).strip().lower()
        if value != APPROVED_PROTOCOL_SHA256 or value != normalized_hash:
            raise G5SRProtocolError(f"环境证据中的协议 {field} 与批准 SHA-256 不一致")
    if approved.get("hash_match") is not True:
        raise G5SRProtocolError("环境证据未确认协议哈希一致")
    if str(approved.get("path", "")).replace("\\", "/") != MACHINE_PROTOCOL.as_posix():
        raise G5SRProtocolError("环境证据引用了不同的 G5-SR 机器协议")

    if (
        report.get("schema_version") != "1.0"
        or report.get("stage") != "G5_SR_ENVIRONMENT"
    ):
        raise G5SRProtocolError("G5-SR 环境证据版本或阶段无效")
    if report.get("status") != "ENVIRONMENT_SMOKE_PASS_INPUTS_AVAILABLE_NOT_READ":
        raise G5SRProtocolError(
            "G5-SR 环境证据不是数据恢复后的有效记录；请重新捕获环境 JSON"
        )

    smoke = report.get("smoke_test", {})
    if smoke.get("status") != "SMOKE_PASS":
        raise G5SRProtocolError("G5-SR 环境 smoke test 未通过")
    if smoke.get("project_or_lockbox_data_read") is not False:
        raise PermissionError("环境 smoke test 必须使用合成数据且不得读取项目或锁箱数据")

    versions = {
        "Python": report.get("python", {}).get("version"),
        "mpmath": report.get("python_packages", {}).get("mpmath"),
        "NumPy": report.get("python_packages", {}).get("numpy"),
        "SciPy": report.get("python_packages", {}).get("scipy"),
        "SymPy": report.get("python_packages", {}).get("sympy"),
        "PySR": report.get("python_packages", {}).get("pysr"),
        "Julia": report.get("julia_backend", {}).get("julia_version"),
        "SymbolicRegression.jl": report.get("julia_backend", {}).get(
            "symbolic_regression_version"
        ),
    }
    missing_versions = [
        label
        for label, value in versions.items()
        if not isinstance(value, str) or not value.strip()
    ]
    if missing_versions:
        raise G5SRProtocolError(
            "G5-SR 环境证据缺少版本: " + ", ".join(missing_versions)
        )

    current_versions = _current_runtime_versions()
    recorded_python_versions = {
        "python": versions["Python"],
        "mpmath": versions["mpmath"],
        "numpy": versions["NumPy"],
        "scipy": versions["SciPy"],
        "sympy": versions["SymPy"],
        "pysr": versions["PySR"],
    }
    for name, recorded in recorded_python_versions.items():
        current = current_versions.get(name)
        if recorded != current:
            raise G5SRProtocolError(
                f"正式 Python 环境漂移: {name}: "
                f"recorded={recorded!r}, current={current!r}"
            )

    artifact_hashes = _validate_formal_execution_artifacts(repo, report)

    lockbox = report.get("lockbox", {})
    if lockbox.get("protocol_status") != "SEALED_UNREAD_UNRUN":
        raise PermissionError("环境证据中的锁箱状态不是 SEALED_UNREAD_UNRUN")
    if lockbox.get("sealed_at_stage") != "G3":
        raise PermissionError("环境证据中的锁箱 sealed_at_stage 不是 G3")
    if lockbox.get("used_by_environment_capture") is not False:
        raise PermissionError("环境捕获记录表明曾使用锁箱")
    if lockbox.get("used_by_g5_sr") is not False:
        raise PermissionError("G5-SR 环境记录表明曾使用锁箱")
    if lockbox.get("paths_accessed") != []:
        raise PermissionError("G5-SR 环境证据中的锁箱访问路径必须为空")

    data = report.get("data_preflight", {})
    if data.get("status") != "AVAILABLE_NOT_READ":
        raise G5SRProtocolError(
            "环境证据仍是数据缺失状态；恢复 G4 输入后必须重新捕获"
        )
    if data.get("content_read") is not False:
        raise G5SRProtocolError("环境捕获阶段不得读取开发数据内容")
    if data.get("missing_inputs") != []:
        raise G5SRProtocolError("环境证据仍记录缺失的 G4 开发输入")
    expected_sources = set(_expected_source_map(machine))
    recorded_sources = data.get("required_inputs")
    if not isinstance(recorded_sources, list):
        raise G5SRProtocolError("环境证据缺少 required_inputs 列表")
    recorded = {}
    for item in recorded_sources:
        if not isinstance(item, dict):
            raise G5SRProtocolError("环境证据 required_inputs 项无效")
        relative = str(item.get("relative_path", "")).replace("\\", "/")
        if not relative or relative in recorded:
            raise G5SRProtocolError("环境证据 required_inputs 路径缺失或重复")
        recorded[relative] = item.get("present")
    if set(recorded) != expected_sources or any(value is not True for value in recorded.values()):
        raise G5SRProtocolError("环境证据未确认全部批准的 G4 开发输入均已存在")

    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "status": report["status"],
        "python_version": versions["Python"],
        "mpmath_version": versions["mpmath"],
        "numpy_version": versions["NumPy"],
        "scipy_version": versions["SciPy"],
        "sympy_version": versions["SymPy"],
        "pysr_version": versions["PySR"],
        "julia_version": versions["Julia"],
        "symbolic_regression_version": versions["SymbolicRegression.jl"],
        "formal_execution_artifact_sha256": artifact_hashes,
    }


def _verify_source_file(repo, source):
    relative = source["relative_path"]
    path = _repo_path(repo, relative)
    if not path.is_file():
        raise G5SRDataUnavailableError(
            f"{MISSING_G4_DERIVED_INPUTS}: G5-SR 数据尚未回传: {relative}; "
            "请恢复 G4 原始派生文件，不得用报告汇总值重建"
        )
    actual_hash = sha256_file(path)
    if actual_hash != str(source["sha256"]).lower():
        raise G5SRProtocolError(
            f"G5-SR 输入哈希失配: {relative}: expected={source['sha256']}, actual={actual_hash}"
        )
    if path.stat().st_size != int(source["bytes"]):
        raise G5SRProtocolError(f"G5-SR 输入字节数失配: {relative}")
    rows, columns = _read_csv(path)
    missing = sorted(set(source["required_columns"]) - set(columns))
    if missing:
        raise G5SRProtocolError(f"{relative} 缺少列: {', '.join(missing)}")
    if len(rows) != int(source["row_count"]):
        raise G5SRProtocolError(
            f"{relative} 行数失配: expected={source['row_count']}, actual={len(rows)}"
        )
    return rows, columns, actual_hash


def _finite(row, key, label, *, positive=False, nonnegative=False):
    try:
        value = float(row[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise G5SRProtocolError(f"{label} 的 {key} 不是有限数") from exc
    if not math.isfinite(value):
        raise G5SRProtocolError(f"{label} 的 {key} 不是有限数")
    if positive and value <= 0:
        raise G5SRProtocolError(f"{label} 的 {key} 必须大于 0")
    if nonnegative and value < 0:
        raise G5SRProtocolError(f"{label} 的 {key} 必须非负")
    return value


def _validate_development_rows(repo, machine, metrics, curves):
    contract = machine["data_contract"]
    registry, columns = _read_csv(_repo_path(repo, contract["development_registry"]))
    _require_keys(
        {column: True for column in columns},
        ("chid", "job_attempt_id", "subset", "parent_case_id", "physical_case_id"),
        "development_cases.csv",
    )
    if any(str(row["subset"]).strip().lower() != "development" for row in registry):
        raise PermissionError("development_cases.csv 含非 development 行")
    expected_counts = contract["expected_counts"]
    if len(registry) != int(expected_counts["runs"]):
        raise G5SRProtocolError("开发注册表运行数与批准协议不一致")

    registry_by_run = {}
    for row in registry:
        run_chid = row["chid"].strip()
        if run_chid in registry_by_run:
            raise G5SRProtocolError(f"开发注册表 run_chid 重复: {run_chid}")
        registry_by_run[run_chid] = row
    allowed_runs = set(registry_by_run)

    if len(metrics) != int(expected_counts["runs"]):
        raise G5SRProtocolError("case_metrics.csv 运行数与批准协议不一致")
    metric_runs = [row["run_chid"].strip() for row in metrics]
    if len(metric_runs) != len(set(metric_runs)):
        raise G5SRProtocolError("case_metrics.csv 的 run_chid 不唯一")
    if set(metric_runs) != allowed_runs:
        raise PermissionError("case_metrics.csv 含未授权运行或缺少开发运行")

    for row in metrics:
        run_chid = row["run_chid"].strip()
        registered = registry_by_run[run_chid]
        for data_key, registry_key in (
            ("job_attempt_id", "job_attempt_id"),
            ("parent_case_id", "parent_case_id"),
            ("physical_case_id", "physical_case_id"),
        ):
            if row[data_key].strip() != registered[registry_key].strip():
                raise G5SRProtocolError(
                    f"{run_chid}: {data_key} 与冻结开发注册表不一致"
                )
        if row["quality_status"].strip() != "PASS" or row["exclusion_reason"].strip():
            raise G5SRProtocolError(f"{run_chid}: 未通过 G4 科学质量门")
        for key in ("Q_actual_kW", "Q_star", "Df_discrete_m", "deltaT_max_K"):
            _finite(row, key, run_chid, positive=True)
        for key in (
            "xf_actual_m",
            "deltaT_peak_ci95_low_K",
            "deltaT_peak_ci95_high_K",
            "censored_fraction",
            "bootstrap_n",
            "bootstrap_success_rate",
            "bootstrap_block_len_samples",
        ):
            _finite(row, key, run_chid, nonnegative=True)

    parents = {row["parent_case_id"].strip() for row in metrics}
    physical = {row["physical_case_id"].strip() for row in metrics}
    if len(parents) != int(expected_counts["parent_groups"]):
        raise G5SRProtocolError("case_metrics.csv 父组数与批准协议不一致")
    if len(physical) != int(expected_counts["physical_cases"]):
        raise G5SRProtocolError("case_metrics.csv 物理工况数与批准协议不一致")

    if len(curves) != int(expected_counts["curve_rows"]):
        raise G5SRProtocolError("curve_points.csv 行数与批准协议不一致")
    curve_runs = {row["run_chid"].strip() for row in curves}
    if curve_runs != allowed_runs:
        raise PermissionError("curve_points.csv 含未授权运行或缺少开发运行")
    allowed_roles = {"ACTIVE", "CENSORED_LOW_SIGNAL", "QC_ONLY"}
    included_roles = set(contract["effective_domain"]["include_point_roles"])
    xi_max = float(contract["effective_domain"]["xi_max"])
    height = float(machine["normalization"]["H_eff_m"])
    formal_rows = 0
    formal_parents = set()
    for index, row in enumerate(curves, start=2):
        run_chid = row["run_chid"].strip()
        registered = registry_by_run[run_chid]
        for data_key, registry_key in (
            ("job_attempt_id", "job_attempt_id"),
            ("parent_case_id", "parent_case_id"),
            ("physical_case_id", "physical_case_id"),
        ):
            if row[data_key].strip() != registered[registry_key].strip():
                raise G5SRProtocolError(
                    f"curve_points.csv:{index} {data_key} 与开发注册表不一致"
                )
        role = row["point_role"].strip()
        if role not in allowed_roles:
            raise G5SRProtocolError(f"curve_points.csv:{index} 未知 point_role={role!r}")
        x_coord = _finite(row, "x_coord_m", f"curve_points.csv:{index}")
        fire_x = _finite(row, "xf_actual_m", f"curve_points.csv:{index}")
        xi = _finite(row, "xi", f"curve_points.csv:{index}", nonnegative=True)
        expected_xi = abs(x_coord - fire_x) / height
        if not math.isclose(xi, expected_xi, rel_tol=0.0, abs_tol=1e-9):
            raise G5SRProtocolError(f"curve_points.csv:{index} xi 与坐标合同不一致")
        for key in (
            "deltaT_mean_K",
            "deltaT_ci95_low_K",
            "deltaT_ci95_high_K",
            "Theta",
            "detection_limit_K",
            "bootstrap_n",
            "bootstrap_block_len_samples",
        ):
            _finite(row, key, f"curve_points.csv:{index}")
        if xi <= xi_max + 1e-12 and role in included_roles:
            formal_rows += 1
            formal_parents.add(row["parent_case_id"].strip())
    if formal_parents != parents:
        raise G5SRProtocolError("正式形状数据未覆盖全部父组")
    if formal_rows <= 0:
        raise G5SRProtocolError("正式形状数据为空")
    return {
        "development_runs": len(allowed_runs),
        "parent_groups": len(parents),
        "physical_cases": len(physical),
        "curve_rows_total": len(curves),
        "curve_rows_in_formal_domain": formal_rows,
    }


def validate_static(repo=PROJECT_ROOT, machine_protocol_path=None, study_protocol_path=None):
    """Validate approval, A0 identity, lockbox isolation and G4 metadata only.

    This does not open any G4 derived table and is useful before the user has
    restored the gitignored development data.
    """
    repo = Path(repo).resolve()
    machine_path = Path(machine_protocol_path or repo / MACHINE_PROTOCOL).resolve()
    study_path = Path(study_protocol_path or repo / STUDY_PROTOCOL).resolve()
    machine = _read_json(machine_path)
    study = _read_json(study_path)
    protocol_hash = _validate_approval(machine, study, machine_path)
    _validate_lockbox_isolation(repo, machine, study)
    a0_hash = _validate_a0_identity(repo, machine)
    _validate_g4_metadata(repo, machine)
    supplement = inspect_execution_supplement(
        repo, machine, study, protocol_hash
    )
    required_inputs = [
        {
            "relative_path": relative,
            "present": _repo_path(repo, relative).is_file(),
        }
        for relative in sorted(_expected_source_map(machine))
    ]
    missing_inputs = [
        item["relative_path"] for item in required_inputs if not item["present"]
    ]
    blocker_codes = []
    if not supplement["approved"]:
        blocker_codes.append(G5_SR_EXECUTION_SUPPLEMENT_NOT_APPROVED)
    if missing_inputs:
        blocker_codes.append(MISSING_G4_DERIVED_INPUTS)
    return {
        "status": "STATIC_G5_SR_PROTOCOL_PASS",
        "machine_protocol": str(machine_path),
        "machine_protocol_sha256": protocol_hash,
        "a0_sha256": a0_hash,
        "lockbox_status": "SEALED_UNREAD_UNRUN",
        "execution_supplement": supplement,
        "data_availability": {
            "check_method": "PATH_EXISTENCE_ONLY",
            "content_read": False,
            "required_inputs": required_inputs,
            "missing_inputs": missing_inputs,
        },
        "blocker_codes": blocker_codes,
        "formal_search_authorized": not blocker_codes,
        "search_results_created": False,
        "A_active_created": False,
    }


def preflight(repo=PROJECT_ROOT, machine_protocol_path=None, study_protocol_path=None):
    """Run the complete formal G5-SR input preflight without searching."""
    repo = Path(repo).resolve()
    machine_path = Path(machine_protocol_path or repo / MACHINE_PROTOCOL).resolve()
    static = validate_static(repo, machine_path, study_protocol_path)
    if not static["execution_supplement"]["approved"]:
        detail = "; ".join(
            static["execution_supplement"].get("validation_issues", [])
        ) or "执行补充协议仍待批准"
        raise G5SRExecutionSupplementNotApprovedError(
            f"{G5_SR_EXECUTION_SUPPLEMENT_NOT_APPROVED}: {detail}"
        )
    machine = _read_json(machine_path)
    verified = {}
    loaded = {}
    for relative, source in _expected_source_map(machine).items():
        rows, columns, digest = _verify_source_file(repo, source)
        loaded[relative] = rows
        verified[relative] = {
            "sha256": digest,
            "row_count": len(rows),
            "column_count": len(columns),
        }
    counts = _validate_development_rows(
        repo,
        machine,
        loaded["derived/development/case_metrics.csv"],
        loaded["derived/development/curve_points.csv"],
    )
    environment = _validate_environment_evidence(
        repo, machine, static["machine_protocol_sha256"]
    )
    return {
        **static,
        "status": "READY_FOR_G5_SR_SEARCH",
        "formal_search_authorized": True,
        "verified_sources": verified,
        "counts": counts,
        "environment": environment,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(PROJECT_ROOT))
    parser.add_argument(
        "--static-only",
        action="store_true",
        help="validate approval/A0/lockbox/manifests without opening missing derived tables",
    )
    args = parser.parse_args()
    result = validate_static(args.repo) if args.static_only else preflight(args.repo)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
