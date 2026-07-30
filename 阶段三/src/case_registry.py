"""阶段三工况注册表读取与完整物理工况级数据隔离合同。"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from design_database_cases import CASE_FIELDS, DEFAULT_OUT, validate_design


PURPOSE_TO_SUBSET = {
    "model_development": "development",
    "offset_validation": "offset_validation",
    "final_evaluation": "independent_test",
}
DERIVED_REQUIRED_FIELDS = (
    "source_chid", "dataset_group_id", "subset", "result_version", "is_adopted",
)


def _strict_bool(value, label):
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    raise ValueError(f"{label} 必须为明确布尔值")


def read_registry(path=DEFAULT_OUT):
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise ValueError(f"{path}: 缺少 CSV 表头")
        missing = [field for field in CASE_FIELDS if field not in reader.fieldnames]
        if missing:
            raise ValueError(f"{path}: 缺少字段 {', '.join(missing)}")
        rows = list(reader)
    validate_design(rows)
    return rows


def select_cases(rows, purpose="model_development", confirm_final_evaluation=False):
    """按用途返回一个子集；独立测试必须显式确认最终评价用途。"""
    if purpose not in PURPOSE_TO_SUBSET:
        raise ValueError(f"未知 purpose={purpose!r}")
    if purpose == "final_evaluation" and not confirm_final_evaluation:
        raise PermissionError("独立测试集已封存；仅最终评价可显式解封")
    subset = PURPOSE_TO_SUBSET[purpose]
    selected = [dict(row) for row in rows if row["subset"] == subset]
    if not selected:
        raise ValueError(f"purpose={purpose} 没有工况")
    return selected


def validate_case_linkage(derived_rows, registry_rows):
    """验证时窗、增强、布局和缺测样本仍绑定同一原始物理工况。"""
    registry = {row["chid"]: row for row in registry_rows}
    if len(registry) != len(registry_rows):
        raise ValueError("注册表 CHID 重复")
    adopted_by_case = defaultdict(set)
    for line_number, row in enumerate(derived_rows, start=2):
        missing = [field for field in DERIVED_REQUIRED_FIELDS if field not in row]
        if missing:
            raise ValueError(f"派生表第 {line_number} 行缺少字段: {', '.join(missing)}")
        chid = str(row["source_chid"]).strip()
        if chid not in registry:
            raise ValueError(f"派生表第 {line_number} 行 source_chid={chid!r} 不在注册表")
        source = registry[chid]
        if row["dataset_group_id"] != source["dataset_group_id"] or row["dataset_group_id"] != chid:
            raise ValueError(f"{chid}: dataset_group_id 必须保持为原始 CHID")
        if row["subset"] != source["subset"]:
            raise ValueError(f"{chid}: 派生样本不得跨子集")
        version = str(row["result_version"]).strip()
        if not version:
            raise ValueError(f"{chid}: result_version 为空")
        if _strict_bool(row["is_adopted"], f"{chid}:is_adopted"):
            adopted_by_case[chid].add(version)
    conflicts = {chid: versions for chid, versions in adopted_by_case.items() if len(versions) > 1}
    if conflicts:
        details = "; ".join(f"{chid}={sorted(versions)}" for chid, versions in conflicts.items())
        raise ValueError(f"同一 CHID 存在多套 adopted 正式结果: {details}")
    return {
        "n_rows": len(derived_rows),
        "n_source_cases": len({row["source_chid"] for row in derived_rows}),
        "n_adopted_cases": len(adopted_by_case),
    }

