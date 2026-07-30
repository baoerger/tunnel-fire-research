"""生成并审计阶段二 12 组先导候选；不生成正式 FDS 输入。"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path


STAGE2_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = STAGE2_ROOT.parent
STAGE1_SRC = PROJECT_ROOT / "阶段一" / "src"
sys.path.insert(0, str(STAGE1_SRC))

import generate_fds_case as fds_generator  # noqa: E402
import tunnel_config as cfg  # noqa: E402


DESIGN_DIR = STAGE2_ROOT / "01_先导工况设计"
DEFAULT_TARGETS = DESIGN_DIR / "pilot_design_targets.csv"
DEFAULT_CASES = DESIGN_DIR / "pilot_cases_candidate.csv"
DEFAULT_UC = DESIGN_DIR / "critical_velocity_estimates.csv"

CASE_FIELDS = (
    "chid", "case_group", "Q", "U", "Df", "dx", "L", "W", "H",
    "x_fire", "T_end", "n_mesh_x", "n_mesh_y", "n_mesh_z", "subset",
    "design_status", "reuse_chid", "Uc_m_s", "U_over_Uc", "flow_layer",
    "critical_velocity_method", "chi_r_assumed", "HRRPUA_kW_m2",
    "selection_reason", "backup_chid", "backup_Q_MW",
    "backup_U_over_Uc", "backup_Df_m", "note",
)
UC_FIELDS = (
    "Q_MW", "Q_convective_MW", "Q_star", "Uc_m_s", "Uc_star", "method",
    "source_chid", "evidence_path", "chi_r_assumed", "H_m",
)


def _finite(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 不是有效数值: {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} 必须为有限数值")
    return number


def li_critical_velocity(Q_MW, chi_r=0.30, H=cfg.H):
    """Li/Ingason 分段关联，仅作分层初估，返回 (Uc, Q*, Uc*)。"""
    Q_MW = _finite(Q_MW, "Q_MW")
    chi_r = _finite(chi_r, "chi_r")
    H = _finite(H, "H")
    if Q_MW <= 0 or H <= 0 or not 0 <= chi_r < 1:
        raise ValueError("Q_MW/H 必须为正且 0<=chi_r<1")
    q_convective_w = Q_MW * 1e6 * (1.0 - chi_r)
    denominator = (
        cfg.RHO_0 * cfg.CP * cfg.T_AMBIENT_K * math.sqrt(cfg.G) * H ** 2.5
    )
    q_star = q_convective_w / denominator
    uc_star = 0.81 * q_star ** (1.0 / 3.0) if q_star <= 0.15 else 0.43
    return uc_star * math.sqrt(cfg.G * H), q_star, uc_star


def wu_bakar_critical_velocity(Q_MW, W=cfg.W, H=cfg.H):
    """Wu--Bakar (2000) 水力高度关联，仅作临界风速敏感性备选。

    返回 ``(Uc, Q_prime, Uc_prime, hydraulic_height)``。原关联中的 ``Q``
    是总 HRR；这里用 W 与 J/(kg K)，数值等价于原式的 kW 与
    kJ/(kg K)。当前先导主表仍使用 Li (2010) 预分层，不静默切换。
    """
    Q_MW = _finite(Q_MW, "Q_MW")
    W = _finite(W, "W")
    H = _finite(H, "H")
    if Q_MW <= 0 or W <= 0 or H <= 0:
        raise ValueError("Q_MW/W/H 必须为正")
    area = W * H
    perimeter = 2.0 * (W + H)
    hydraulic_height = 4.0 * area / perimeter
    q_prime = Q_MW * 1.0e6 / (
        cfg.RHO_0
        * cfg.CP
        * cfg.T_AMBIENT_K
        * math.sqrt(cfg.G)
        * hydraulic_height ** 2.5
    )
    if q_prime <= 0.20:
        uc_prime = 0.4 * q_prime ** (1.0 / 3.0)
    else:
        uc_prime = 0.30
    uc = uc_prime * math.sqrt(cfg.G * hydraulic_height)
    return uc, q_prime, uc_prime, hydraulic_height


def _read_rows(path):
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise ValueError(f"{path}: 缺少 CSV 表头")
        return list(reader)


def _load_overrides(path):
    if not path:
        return {}
    rows = _read_rows(path)
    overrides = {}
    for line_number, row in enumerate(rows, start=2):
        q = _finite(row.get("Q_MW"), f"覆盖表第 {line_number} 行 Q_MW")
        uc = _finite(row.get("Uc_FDS_m_s"), f"覆盖表第 {line_number} 行 Uc_FDS_m_s")
        source = (row.get("source_chid") or "").strip()
        evidence = (row.get("evidence_path") or "").strip()
        if q <= 0 or uc <= 0 or not source or not evidence:
            raise ValueError(f"覆盖表第 {line_number} 行缺少正 Uc、source_chid 或 evidence_path")
        if q in overrides:
            raise ValueError(f"覆盖表 Q={q:g} MW 重复")
        overrides[q] = {
            "uc": uc, "method": "fds_backflow_override",
            "source_chid": source, "evidence_path": evidence,
        }
    return overrides


def _flow_layer(ratio, U):
    if abs(U) <= 1e-12:
        return "no_wind"
    if ratio < 0.85:
        return "weak"
    if ratio <= 1.15:
        return "near_critical"
    return "strong"


def _write(path, fields, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def build(targets_path=DEFAULT_TARGETS, cases_path=DEFAULT_CASES,
          uc_path=DEFAULT_UC, overrides_path=None, chi_r=0.30):
    targets = _read_rows(targets_path)
    if len(targets) != 12:
        raise ValueError(f"先导候选必须恰有 12 组，当前 {len(targets)} 组")
    chids = [(row.get("chid") or "").strip() for row in targets]
    if any(not chid for chid in chids) or len(chids) != len(set(chids)):
        raise ValueError("先导 CHID 为空或重复")
    overrides = _load_overrides(overrides_path)

    case_rows = []
    uc_by_q = {}
    for line_number, row in enumerate(targets, start=2):
        chid = row["chid"].strip()
        Q = _finite(row.get("Q_MW"), f"第 {line_number} 行 Q_MW")
        Df = _finite(row.get("Df_m"), f"第 {line_number} 行 Df_m")
        uc_formula, q_star, uc_star_formula = li_critical_velocity(Q, chi_r, cfg.H)
        override = overrides.get(Q)
        uc = override["uc"] if override else uc_formula
        method = override["method"] if override else "Li_2010_piecewise_preliminary"
        target_ratio_raw = (row.get("target_U_over_Uc") or "").strip()
        fixed_u_raw = (row.get("fixed_U_m_s") or "").strip()
        if bool(target_ratio_raw) == bool(fixed_u_raw):
            raise ValueError(
                f"第 {line_number} 行必须且只能填写 target_U_over_Uc/fixed_U_m_s 之一"
            )
        if fixed_u_raw:
            U = _finite(fixed_u_raw, f"第 {line_number} 行 fixed_U_m_s")
        else:
            U = _finite(target_ratio_raw, f"第 {line_number} 行 target_U_over_Uc") * uc
        if U < 0:
            raise ValueError(f"第 {line_number} 行 U 不得为负")
        ratio = U / uc
        if not 0 <= ratio <= 1.600001:
            raise ValueError(f"{chid}: U/Uc={ratio:.3f} 超出 [0,1.6]")

        normalized = fds_generator.normalize_case({
            "chid": chid, "Q": Q, "U": U, "Df": Df, "dx": 0.25,
            "L": 100, "W": 10, "H": 5, "x_fire": 50, "T_end": 300,
            "n_mesh_x": 11, "n_mesh_y": 1, "n_mesh_z": 2,
        }, row_number=line_number)
        x0, x1, y0, y1 = normalized["burner_bounds"]
        burner_area = (x1 - x0) * (y1 - y0)
        hrrpua = Q * 1000.0 / burner_area
        hrrpua_min, hrrpua_max = cfg.HRRPUA_CANDIDATE_RANGE
        if not hrrpua_min <= hrrpua <= hrrpua_max:
            raise ValueError(
                f"{chid}: 离散 HRRPUA={hrrpua:.1f} kW/m2 超出候选范围 "
                f"[{hrrpua_min:g},{hrrpua_max:g}]"
            )
        selection_reason = (row.get("selection_reason") or "").strip()
        backup_chid = (row.get("backup_chid") or "").strip()
        backup_q = _finite(row.get("backup_Q_MW"), f"{chid}:backup_Q_MW")
        backup_ratio = _finite(row.get("backup_U_over_Uc"), f"{chid}:backup_U_over_Uc")
        backup_df = _finite(row.get("backup_Df_m"), f"{chid}:backup_Df_m")
        if not selection_reason or not backup_chid or backup_q <= 0 or backup_df <= 0:
            raise ValueError(f"{chid}: 选择理由和替补工况必须完整")
        if not 0 <= backup_ratio <= 1.6:
            raise ValueError(f"{chid}: 替补 U/Uc 超出 [0,1.6]")

        reuse = (row.get("reuse_chid") or "").strip()
        source_chid = override["source_chid"] if override else ""
        evidence_path = override["evidence_path"] if override else ""
        case_rows.append({
            "chid": chid, "case_group": "pilot_candidate", "Q": f"{Q:g}",
            "U": f"{U:.6f}", "Df": f"{Df:g}", "dx": "0.25", "L": "100",
            "W": "10", "H": "5", "x_fire": "50", "T_end": "300",
            "n_mesh_x": "11", "n_mesh_y": "1", "n_mesh_z": "2",
            "subset": "development_pilot", "design_status": "WAITING_STAGE1_GATE",
            "reuse_chid": reuse, "Uc_m_s": f"{uc:.6f}",
            "U_over_Uc": f"{ratio:.6f}", "flow_layer": _flow_layer(ratio, U),
            "critical_velocity_method": method, "chi_r_assumed": f"{chi_r:.3f}",
            "HRRPUA_kW_m2": f"{hrrpua:.3f}", "selection_reason": selection_reason,
            "backup_chid": backup_chid, "backup_Q_MW": f"{backup_q:g}",
            "backup_U_over_Uc": f"{backup_ratio:g}", "backup_Df_m": f"{backup_df:g}",
            "note": "候选；阶段一通过后按冻结网格/长度重生成，当前不得提交 FDS",
        })
        uc_by_q[Q] = {
            "Q_MW": f"{Q:g}", "Q_convective_MW": f"{Q * (1-chi_r):.6f}",
            "Q_star": f"{q_star:.8f}", "Uc_m_s": f"{uc:.6f}",
            "Uc_star": f"{uc / math.sqrt(cfg.G * cfg.H):.8f}", "method": method,
            "source_chid": source_chid, "evidence_path": evidence_path,
            "chi_r_assumed": f"{chi_r:.3f}", "H_m": f"{cfg.H:g}",
        }

    _validate_coverage(case_rows)
    _write(cases_path, CASE_FIELDS, case_rows)
    _write(uc_path, UC_FIELDS, [uc_by_q[q] for q in sorted(uc_by_q)])
    return case_rows, [uc_by_q[q] for q in sorted(uc_by_q)]


def _validate_coverage(rows):
    regimes = Counter(row["flow_layer"] for row in rows)
    if any(regimes[name] == 0 for name in ("no_wind", "weak", "near_critical", "strong")):
        raise ValueError(f"先导未覆盖全部风速层: {dict(regimes)}")
    if regimes["near_critical"] < 4:
        raise ValueError("临界区候选少于 4 组")
    powers = [float(row["Q"]) for row in rows]
    bands = Counter("low" if q <= 20 else "mid" if q < 50 else "high" for q in powers)
    if any(bands[name] < 3 for name in ("low", "mid", "high")):
        raise ValueError(f"低/中/高功率覆盖不足: {dict(bands)}")
    if len({float(row["Df"]) for row in rows}) < 5:
        raise ValueError("火源尺寸档位不足")
    if sum(bool(row["reuse_chid"]) for row in rows) != 3:
        raise ValueError("必须恰好复用 3 个中网格代表工况")

    groups = defaultdict(list)
    for row in rows:
        groups[(round(float(row["Q"]), 6), round(float(row["U"]), 4))].append(float(row["Df"]))
    paired_rows = sum(len(values) for values in groups.values() if len(set(values)) >= 2)
    if paired_rows < 6:
        raise ValueError("同 Q 同 U 不同 Df 的尺寸配对少于 6 个候选")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", default=str(DEFAULT_TARGETS))
    parser.add_argument("--out-cases", default=str(DEFAULT_CASES))
    parser.add_argument("--out-uc", default=str(DEFAULT_UC))
    parser.add_argument("--uc-overrides")
    parser.add_argument("--chi-r", type=float, default=0.30,
                        help="仅用于 Uc 初估的暂定辐射份额；不构成最终 chi_r 结论")
    args = parser.parse_args()
    try:
        rows, estimates = build(
            args.targets, args.out_cases, args.out_uc, args.uc_overrides, args.chi_r
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    counts = Counter(row["flow_layer"] for row in rows)
    print(f"[OK] 12 组先导候选 -> {args.out_cases}")
    print(f"[OK] {len(estimates)} 个功率档 Uc 初估 -> {args.out_uc}")
    print("覆盖:", ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))
    print("状态: WAITING_STAGE1_GATE（未生成正式 FDS）")


if __name__ == "__main__":
    main()
