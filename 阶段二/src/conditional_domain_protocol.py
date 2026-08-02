"""建立 100 m 条件域协议 V2，不覆盖协议 V1 的正式失败证据。"""
from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path


STAGE2_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_V1_CASES = STAGE2_ROOT / "outputs" / "pilot_falsification" / "pilot_case_metrics.csv"
DEFAULT_V1_SUMMARY = (
    STAGE2_ROOT / "outputs" / "pilot_falsification" / "pilot_falsification_summary.csv"
)
DEFAULT_OUTDIR = STAGE2_ROOT / "04_100m条件域协议"
PROTOCOL_VERSION = "100M_CONDITIONAL_DOMAIN_V2"
DEVELOPMENT_STATUS = "DEVELOPMENT_GATE_PASS_CONFIRMATION_PENDING"
CONFIRMATION_REQUIREMENT = "SEALED_INDEPENDENT_TEST_AFTER_MODEL_FREEZE"

CLASSIFICATION_MAP = {
    "bilateral_identifiable": "bilateral_identifiable",
    "upstream_censored": "upstream_censored",
    "no_obvious_backflow": "no_obvious_backflow",
    "insufficient_downstream": "downstream_domain_censored",
    "downstream_domain_censored": "downstream_domain_censored",
}

CASE_FIELDS = (
    "chid", "protocol_version", "legacy_v1_formal_gate_status",
    "quality_status", "evidence_status", "data_origin", "domain_length_m",
    "measurement_x_min_m", "measurement_x_max_m",
    "legacy_observation_category", "v2_observation_category",
    "profile_target_available", "peak_target_available",
    "k_u_target_permitted", "k_d_target_permitted",
    "k_u_value_usable_from_v1", "k_d_value_usable_from_v1",
    "k_u_1_m", "k_d_1_m", "kappa_u", "kappa_d",
    "derived_Pe_Da_available", "parameter_target_mask",
    "case_protocol_compatible", "scientific_confirmation_status", "note",
)

SUMMARY_FIELDS = (
    "protocol_version", "legacy_v1_formal_gate_status", "data_origin",
    "n_cases", "n_protocol_compatible", "n_bilateral_identifiable",
    "n_upstream_censored", "n_downstream_domain_censored",
    "n_no_obvious_backflow", "n_both_decay_values_usable_from_v1",
    "n_upstream_only_usable_from_v1", "n_downstream_only_usable_from_v1",
    "n_profile_only_from_v1", "development_gate_status",
    "formal_confirmation_requirement", "scope_statement",
)


def _read_rows(path):
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise ValueError(f"{path}: 缺少 CSV 表头")
        return list(reader)


def _write_rows(path, fields, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _strict_bool(value, label):
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no", ""}:
        return False
    raise ValueError(f"{label} 必须为明确布尔值")


def _finite_or_none(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _availability(category):
    if category == "bilateral_identifiable":
        return True, True
    if category in {"upstream_censored", "no_obvious_backflow"}:
        return False, True
    if category == "downstream_domain_censored":
        return True, False
    raise ValueError(f"未知 V2 分类: {category}")


def convert_case(row, legacy_gate_status):
    chid = str(row.get("chid") or "").strip()
    if not chid:
        raise ValueError("V1 工况缺少 CHID")
    legacy_category = str(row.get("observation_category") or "").strip()
    if legacy_category not in CLASSIFICATION_MAP:
        raise ValueError(f"{chid}: 未知 V1 observation_category={legacy_category!r}")
    category = CLASSIFICATION_MAP[legacy_category]
    quality_pass = str(row.get("quality_status") or "").strip().upper() == "PASS"
    formal_evidence = str(row.get("evidence_status") or "").strip() == "FORMAL_QUALITY_PASS"
    peak_available = _finite_or_none(row.get("xp_peak_m")) is not None
    profile_available = quality_pass and str(row.get("data_origin") or "").strip() == "REAL_FDS"
    interval_stable = _strict_bool(row.get("interval_stable"), f"{chid}/interval_stable")
    upstream_permitted, downstream_permitted = _availability(category)
    k_u = _finite_or_none(row.get("k_u_1_m"))
    k_d = _finite_or_none(row.get("k_d_1_m"))
    kappa_u = _finite_or_none(row.get("kappa_u"))
    kappa_d = _finite_or_none(row.get("kappa_d"))
    upstream_usable = bool(
        upstream_permitted and interval_stable and k_u is not None and k_u > 0
        and kappa_u is not None and kappa_u > 0
    )
    downstream_usable = bool(
        downstream_permitted and interval_stable and k_d is not None and k_d > 0
        and kappa_d is not None and kappa_d > 0
    )
    compatible = quality_pass and formal_evidence and profile_available and peak_available
    masks = (
        f"profile=1;peak=1;k_u={int(upstream_usable)};k_d={int(downstream_usable)};"
        f"Pe_Da={int(upstream_usable and downstream_usable)}"
    )
    note_parts = []
    if category == "downstream_domain_censored":
        note_parts.append("100 m 测量域未提供可辨识下游远场；k_d 必须留空")
    if category in {"upstream_censored", "no_obvious_backflow"}:
        note_parts.append("上游物理删失；k_u 必须留空")
    if (upstream_permitted or downstream_permitted) and not interval_stable:
        note_parts.append("V1 拟合区间不稳定；不继承衰减参数")
    if not note_parts:
        note_parts.append("V1 稳定参数仅作 V2 开发输入审计，不能替代封存测试确认")
    return {
        "chid": chid, "protocol_version": PROTOCOL_VERSION,
        "legacy_v1_formal_gate_status": legacy_gate_status,
        "quality_status": row.get("quality_status", ""),
        "evidence_status": row.get("evidence_status", ""),
        "data_origin": row.get("data_origin", ""), "domain_length_m": "100.000000",
        "measurement_x_min_m": "15.000000", "measurement_x_max_m": "85.000000",
        "legacy_observation_category": legacy_category,
        "v2_observation_category": category,
        "profile_target_available": str(profile_available).lower(),
        "peak_target_available": str(peak_available).lower(),
        "k_u_target_permitted": str(upstream_permitted).lower(),
        "k_d_target_permitted": str(downstream_permitted).lower(),
        "k_u_value_usable_from_v1": str(upstream_usable).lower(),
        "k_d_value_usable_from_v1": str(downstream_usable).lower(),
        "k_u_1_m": f"{k_u:.12g}" if upstream_usable else "",
        "k_d_1_m": f"{k_d:.12g}" if downstream_usable else "",
        "kappa_u": f"{kappa_u:.12g}" if upstream_usable else "",
        "kappa_d": f"{kappa_d:.12g}" if downstream_usable else "",
        "derived_Pe_Da_available": str(upstream_usable and downstream_usable).lower(),
        "parameter_target_mask": masks,
        "case_protocol_compatible": str(compatible).lower(),
        "scientific_confirmation_status": DEVELOPMENT_STATUS,
        "note": "；".join(note_parts),
    }


def build(v1_cases=DEFAULT_V1_CASES, v1_summary=DEFAULT_V1_SUMMARY,
          outdir=DEFAULT_OUTDIR):
    summaries = _read_rows(v1_summary)
    if len(summaries) != 1:
        raise ValueError("协议 V1 汇总必须恰有一行")
    legacy = summaries[0]
    legacy_gate = str(legacy.get("formal_gate_status") or "").strip()
    if legacy_gate != "FAIL":
        raise ValueError("当前 V2 迁移证据要求保留协议 V1 的正式 FAIL")
    if str(legacy.get("data_origin") or "").strip() != "REAL_FDS":
        raise ValueError("协议 V1 不是 REAL_FDS，不能迁移为正式开发证据")
    source_rows = _read_rows(v1_cases)
    if len(source_rows) != 12:
        raise ValueError(f"协议 V2 先导迁移必须恰有 12 组，当前 {len(source_rows)}")
    rows = [convert_case(row, legacy_gate) for row in source_rows]
    if len({row["chid"] for row in rows}) != len(rows):
        raise ValueError("协议 V2 CHID 重复")
    counts = {name: sum(row["v2_observation_category"] == name for row in rows)
              for name in set(CLASSIFICATION_MAP.values())}
    both = sum(row["k_u_value_usable_from_v1"] == "true" and
               row["k_d_value_usable_from_v1"] == "true" for row in rows)
    upstream_only = sum(row["k_u_value_usable_from_v1"] == "true" and
                        row["k_d_value_usable_from_v1"] == "false" for row in rows)
    downstream_only = sum(row["k_u_value_usable_from_v1"] == "false" and
                          row["k_d_value_usable_from_v1"] == "true" for row in rows)
    compatible = sum(row["case_protocol_compatible"] == "true" for row in rows)
    summary = {
        "protocol_version": PROTOCOL_VERSION,
        "legacy_v1_formal_gate_status": legacy_gate, "data_origin": "REAL_FDS",
        "n_cases": len(rows), "n_protocol_compatible": compatible,
        "n_bilateral_identifiable": counts.get("bilateral_identifiable", 0),
        "n_upstream_censored": counts.get("upstream_censored", 0),
        "n_downstream_domain_censored": counts.get("downstream_domain_censored", 0),
        "n_no_obvious_backflow": counts.get("no_obvious_backflow", 0),
        "n_both_decay_values_usable_from_v1": both,
        "n_upstream_only_usable_from_v1": upstream_only,
        "n_downstream_only_usable_from_v1": downstream_only,
        "n_profile_only_from_v1": len(rows) - both - upstream_only - downstream_only,
        "development_gate_status": DEVELOPMENT_STATUS if compatible == len(rows) else "FAIL",
        "formal_confirmation_requirement": CONFIRMATION_REQUIREMENT,
        "scope_statement": (
            "仅适用于 L=100 m、W=10 m、H=5 m、dx=0.25 m、x=15--85 m 的条件域；"
            "不声明网格收敛、洞口独立或长隧道普适性"
        ),
    }
    outdir = Path(outdir)
    _write_rows(outdir / "protocol_v2_case_targets.csv", CASE_FIELDS, rows)
    _write_rows(outdir / "protocol_v2_summary.csv", SUMMARY_FIELDS, [summary])
    return rows, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v1-cases", default=str(DEFAULT_V1_CASES))
    parser.add_argument("--v1-summary", default=str(DEFAULT_V1_SUMMARY))
    parser.add_argument("--outdir", default=str(DEFAULT_OUTDIR))
    args = parser.parse_args()
    try:
        rows, summary = build(args.v1_cases, args.v1_summary, args.outdir)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(
        f"[{summary['development_gate_status']}] {summary['n_protocol_compatible']}/"
        f"{summary['n_cases']} 组兼容 100 m 条件域协议 V2"
    )
    print("协议 V1 正式门保持:", summary["legacy_v1_formal_gate_status"])
    print("正式确认:", summary["formal_confirmation_requirement"])
    print("输出:", Path(args.outdir).resolve())


if __name__ == "__main__":
    main()
