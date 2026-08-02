"""由正式随机种子重复结果生成先导温升噪声阈值证据。

父工况和重复工况分别使用各自的准稳态时间平均窗口。对每个同名温度测点，
两次独立均值的样本标准差为 ``abs(mu_1 - mu_2) / sqrt(2)``。为避免低估，
该分量与两次时间块 Bootstrap 标准差取最大值，再乘置信乘子。删失阈值只
关注弱温升区，因此每对工况在两次平均温升不超过工程弱信号上限的测点中
取最大值；多组代表工况再取保守包络并显式赋给所有先导 CHID。
"""
from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path


STAGE2_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = STAGE2_ROOT.parent
DEFAULT_DESIGN = STAGE2_ROOT / "01_先导工况设计" / "pilot_cases_candidate.csv"
DEFAULT_SEED_DESIGN = STAGE2_ROOT / "01_先导工况设计" / "pilot_seed_repeat_cases.csv"
DEFAULT_PARENT_PROFILES = (
    STAGE2_ROOT / "outputs" / "preliminary_fds_6.9.1" / "time_average"
    / "time_average_database.csv"
)
DEFAULT_REPEAT_PROFILES = (
    PROJECT_ROOT / "阶段一" / "outputs" / "analysis" / "extra_four"
    / "time_average" / "time_average_database.csv"
)
DEFAULT_OUTDIR = STAGE2_ROOT / "03_删失与峰值"

POINT_FIELDS = (
    "parent_chid", "repeat_chid", "sensor", "x_m",
    "parent_t0_s", "parent_t1_s", "repeat_t0_s", "repeat_t1_s",
    "parent_deltaT_bar_C", "repeat_deltaT_bar_C", "pair_mean_deltaT_C",
    "absolute_mean_difference_C", "repeat_seed_mean_sd_C",
    "parent_bootstrap_sd_C", "repeat_bootstrap_sd_C",
    "single_run_mean_noise_sd_C", "noise_limit_C", "weak_signal_eligible",
)
SUMMARY_FIELDS = (
    "parent_chid", "repeat_chid", "n_sensors", "n_weak_signal_sensors",
    "weak_signal_limit_C", "confidence_multiplier", "DeltaT_noise_C",
    "status", "method", "parent_profile_source", "repeat_profile_source",
)
THRESHOLD_FIELDS = (
    "chid", "DeltaT_noise_C", "assignment_scope", "n_representative_pairs",
    "source_parent_chids", "source_repeat_chids", "pair_DeltaT_noise_C",
    "weak_signal_limit_C", "confidence_multiplier", "method",
    "evidence_path", "data_origin", "status",
)


def _read_rows(path):
    path = Path(path)
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def _write_rows(path, fields, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _finite(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 不是有效数值") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} 必须为有限数值")
    return number


def _unique_values(rows, key, label):
    values = []
    seen = set()
    for row in rows:
        value = str(row.get(key) or "").strip()
        if not value or value in seen:
            raise ValueError(f"{label}: {key} 为空或重复")
        seen.add(value)
        values.append(value)
    if not values:
        raise ValueError(f"{label} 为空")
    return values


def _profile_index(path):
    grouped = {}
    for row in _read_rows(path):
        chid = str(row.get("chid") or "").strip()
        sensor = str(row.get("sensor") or "").strip()
        if not chid or not sensor:
            raise ValueError(f"{path}: 时间平均表存在空 chid/sensor")
        case = grouped.setdefault(chid, {})
        if sensor in case:
            raise ValueError(f"{path}: {chid}/{sensor} 重复")
        for field in ("x", "t0", "t1", "T_bar", "sd"):
            _finite(row.get(field), f"{chid}/{sensor}/{field}")
        if _finite(row["t1"], f"{chid}/{sensor}/t1") <= _finite(
            row["t0"], f"{chid}/{sensor}/t0"
        ):
            raise ValueError(f"{chid}/{sensor}: 时间平均窗口无效")
        if _finite(row["sd"], f"{chid}/{sensor}/sd") < 0:
            raise ValueError(f"{chid}/{sensor}: Bootstrap 标准差不能为负")
        case[sensor] = row
    return grouped


def _display_path(path):
    path = Path(path).resolve()
    try:
        return path.relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def derive_thresholds(
    design_path=DEFAULT_DESIGN,
    seed_design_path=DEFAULT_SEED_DESIGN,
    parent_profiles_path=DEFAULT_PARENT_PROFILES,
    repeat_profiles_path=DEFAULT_REPEAT_PROFILES,
    outdir=DEFAULT_OUTDIR,
    weak_signal_limit_C=5.0,
    confidence_multiplier=1.96,
    additional_design_paths=(),
):
    weak_signal_limit_C = _finite(weak_signal_limit_C, "weak_signal_limit_C")
    confidence_multiplier = _finite(confidence_multiplier, "confidence_multiplier")
    if weak_signal_limit_C <= 0 or confidence_multiplier <= 0:
        raise ValueError("弱信号上限和置信乘子必须为正")

    design_chids = _unique_values(_read_rows(design_path), "chid", "先导设计表")
    for extra_path in additional_design_paths or ():
        extra_chids = _unique_values(_read_rows(extra_path), "chid", f"附加设计表 {extra_path}")
        overlap = sorted(set(design_chids) & set(extra_chids))
        if overlap:
            raise ValueError(f"附加设计表 CHID 与已有目标重复: {overlap}")
        design_chids.extend(extra_chids)
    seed_rows = _read_rows(seed_design_path)
    repeat_chids = _unique_values(seed_rows, "chid", "随机种子设计表")
    if len(seed_rows) < 2:
        raise ValueError("正式代表性噪声包络至少需要两组父/重复工况")
    parent_profiles = _profile_index(parent_profiles_path)
    repeat_profiles = _profile_index(repeat_profiles_path)

    point_rows = []
    summary_rows = []
    method = (
        "case-specific quasi-steady means; per-sensor seed SD=abs(mean1-mean2)/sqrt(2); "
        "noise SD=max(seed SD,parent block-bootstrap SD,repeat block-bootstrap SD); "
        "multiply by confidence multiplier and take maximum where pair-mean DeltaT is "
        "within weak-signal limit; final value is the maximum representative-pair envelope"
    )
    for seed_row, repeat_chid in zip(seed_rows, repeat_chids):
        parent_chid = str(seed_row.get("parent_chid") or "").strip()
        if not parent_chid:
            raise ValueError(f"{repeat_chid}: 缺少 parent_chid")
        if parent_chid not in design_chids:
            raise ValueError(f"{repeat_chid}: 父工况 {parent_chid} 不在先导设计表")
        if parent_chid not in parent_profiles:
            raise ValueError(f"{parent_chid}: 缺少父工况时间平均剖面")
        if repeat_chid not in repeat_profiles:
            raise ValueError(f"{repeat_chid}: 缺少重复工况时间平均剖面")

        parent_case = parent_profiles[parent_chid]
        repeat_case = repeat_profiles[repeat_chid]
        parent_sensors = set(parent_case)
        repeat_sensors = set(repeat_case)
        if parent_sensors != repeat_sensors:
            missing_repeat = sorted(parent_sensors - repeat_sensors)
            missing_parent = sorted(repeat_sensors - parent_sensors)
            raise ValueError(
                f"{parent_chid}/{repeat_chid}: 测点集合不一致；"
                f"重复缺少={missing_repeat}，父工况缺少={missing_parent}"
            )
        if len(parent_sensors) < 7:
            raise ValueError(f"{parent_chid}/{repeat_chid}: 同名温度测点少于 7 个")

        pair_rows = []
        for sensor in sorted(parent_sensors, key=lambda item: _finite(parent_case[item]["x"], item)):
            parent = parent_case[sensor]
            repeat = repeat_case[sensor]
            x_parent = _finite(parent["x"], f"{parent_chid}/{sensor}/x")
            x_repeat = _finite(repeat["x"], f"{repeat_chid}/{sensor}/x")
            if not math.isclose(x_parent, x_repeat, rel_tol=0.0, abs_tol=1e-9):
                raise ValueError(f"{parent_chid}/{repeat_chid}/{sensor}: x 坐标不一致")
            parent_mean = _finite(parent["T_bar"], f"{parent_chid}/{sensor}/T_bar")
            repeat_mean = _finite(repeat["T_bar"], f"{repeat_chid}/{sensor}/T_bar")
            parent_sd = _finite(parent["sd"], f"{parent_chid}/{sensor}/sd")
            repeat_sd = _finite(repeat["sd"], f"{repeat_chid}/{sensor}/sd")
            absolute_difference = abs(parent_mean - repeat_mean)
            seed_sd = absolute_difference / math.sqrt(2.0)
            noise_sd = max(seed_sd, parent_sd, repeat_sd)
            pair_mean = (parent_mean + repeat_mean) / 2.0
            eligible = pair_mean <= weak_signal_limit_C
            row = {
                "parent_chid": parent_chid,
                "repeat_chid": repeat_chid,
                "sensor": sensor,
                "x_m": f"{x_parent:.6f}",
                "parent_t0_s": f"{_finite(parent['t0'], 'parent/t0'):.6f}",
                "parent_t1_s": f"{_finite(parent['t1'], 'parent/t1'):.6f}",
                "repeat_t0_s": f"{_finite(repeat['t0'], 'repeat/t0'):.6f}",
                "repeat_t1_s": f"{_finite(repeat['t1'], 'repeat/t1'):.6f}",
                "parent_deltaT_bar_C": f"{parent_mean:.9f}",
                "repeat_deltaT_bar_C": f"{repeat_mean:.9f}",
                "pair_mean_deltaT_C": f"{pair_mean:.9f}",
                "absolute_mean_difference_C": f"{absolute_difference:.9f}",
                "repeat_seed_mean_sd_C": f"{seed_sd:.9f}",
                "parent_bootstrap_sd_C": f"{parent_sd:.9f}",
                "repeat_bootstrap_sd_C": f"{repeat_sd:.9f}",
                "single_run_mean_noise_sd_C": f"{noise_sd:.9f}",
                "noise_limit_C": f"{confidence_multiplier * noise_sd:.9f}",
                "weak_signal_eligible": "true" if eligible else "false",
            }
            pair_rows.append(row)
            point_rows.append(row)

        weak_rows = [row for row in pair_rows if row["weak_signal_eligible"] == "true"]
        if len(weak_rows) < 3:
            raise ValueError(f"{parent_chid}/{repeat_chid}: 弱温升区有效测点少于 3 个")
        pair_threshold = max(float(row["noise_limit_C"]) for row in weak_rows)
        summary_rows.append({
            "parent_chid": parent_chid,
            "repeat_chid": repeat_chid,
            "n_sensors": len(pair_rows),
            "n_weak_signal_sensors": len(weak_rows),
            "weak_signal_limit_C": f"{weak_signal_limit_C:.6f}",
            "confidence_multiplier": f"{confidence_multiplier:.6f}",
            "DeltaT_noise_C": f"{pair_threshold:.9f}",
            "status": "FORMAL_FDS_6_9_1_SEED_PAIR_READY",
            "method": method,
            "parent_profile_source": _display_path(parent_profiles_path),
            "repeat_profile_source": _display_path(repeat_profiles_path),
        })

    global_threshold = max(float(row["DeltaT_noise_C"]) for row in summary_rows)
    parent_list = ";".join(row["parent_chid"] for row in summary_rows)
    repeat_list = ";".join(row["repeat_chid"] for row in summary_rows)
    pair_values = ";".join(row["DeltaT_noise_C"] for row in summary_rows)
    evidence_path = _display_path(Path(outdir) / "seed_repeat_case_summary.csv")
    threshold_rows = [{
        "chid": chid,
        "DeltaT_noise_C": f"{global_threshold:.9f}",
        "assignment_scope": "conservative_representative_pair_envelope_not_individual_repeat",
        "n_representative_pairs": len(summary_rows),
        "source_parent_chids": parent_list,
        "source_repeat_chids": repeat_list,
        "pair_DeltaT_noise_C": pair_values,
        "weak_signal_limit_C": f"{weak_signal_limit_C:.6f}",
        "confidence_multiplier": f"{confidence_multiplier:.6f}",
        "method": method,
        "evidence_path": evidence_path,
        "data_origin": "FORMAL_FDS_6_9_1",
        "status": "TRACEABLE_REPRESENTATIVE_SEED_ENVELOPE",
    } for chid in design_chids]

    outdir = Path(outdir)
    _write_rows(outdir / "seed_repeat_pointwise_noise.csv", POINT_FIELDS, point_rows)
    _write_rows(outdir / "seed_repeat_case_summary.csv", SUMMARY_FIELDS, summary_rows)
    _write_rows(outdir / "noise_thresholds_formal.csv", THRESHOLD_FIELDS, threshold_rows)
    return {
        "pointwise": point_rows,
        "summaries": summary_rows,
        "thresholds": threshold_rows,
        "DeltaT_noise_C": global_threshold,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", default=str(DEFAULT_DESIGN))
    parser.add_argument("--seed-design", default=str(DEFAULT_SEED_DESIGN))
    parser.add_argument("--parent-profiles", default=str(DEFAULT_PARENT_PROFILES))
    parser.add_argument("--repeat-profiles", default=str(DEFAULT_REPEAT_PROFILES))
    parser.add_argument("--outdir", default=str(DEFAULT_OUTDIR))
    parser.add_argument(
        "--additional-design", action="append", default=[],
        help="把同一代表性噪声包络赋给附加诊断设计表；可重复指定",
    )
    parser.add_argument("--weak-signal-limit-C", type=float, default=5.0)
    parser.add_argument("--confidence-multiplier", type=float, default=1.96)
    args = parser.parse_args()
    try:
        result = derive_thresholds(
            design_path=args.design,
            seed_design_path=args.seed_design,
            parent_profiles_path=args.parent_profiles,
            repeat_profiles_path=args.repeat_profiles,
            outdir=args.outdir,
            weak_signal_limit_C=args.weak_signal_limit_C,
            confidence_multiplier=args.confidence_multiplier,
            additional_design_paths=args.additional_design,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(
        f"{len(result['summaries'])} 组代表性随机种子对完成；"
        f"保守 DeltaT_noise={result['DeltaT_noise_C']:.6f} °C；"
        f"已写入 {Path(args.outdir).resolve()}"
    )


if __name__ == "__main__":
    main()
