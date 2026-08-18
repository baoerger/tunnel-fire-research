"""筛查全局伸缩指数远场是否能挽救阶段二结构门。

候选形式为 ``log(theta)=a-ku*(xp-x)^pu-kd*(x-xp)^pd``。``pu/pd`` 对全部
工况共享，每个工况仍只拟合共同截距、源中心和双侧尺度。本工具是结构负结果
筛查，不把该经验形式写成一维常系数输运方程的解析解，也不覆盖正式判伪表。
"""
from __future__ import annotations

import argparse
import csv
import math
import statistics
from pathlib import Path

import numpy as np

from . import censoring_peak as cp


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILES = (
    PROJECT_ROOT / "derived" / "time_average"
    / "time_average_database.csv"
)
DEFAULT_OUTDIR = PROJECT_ROOT / "derived" / "method_checks" / "stretched_exponential"
EXPONENTS = (0.5, 0.75, 1.0, 1.25, 1.5)
WINDOWS_H = ((0.3, 5.0), (0.4, 6.0), (0.5, 7.0))
CANDIDATE_FIELDS = (
    "p_upstream", "p_downstream", "n_cases", "n_valid", "n_fail",
    "median_far_nrmse", "n_far_pass", "n_interval_pass", "n_joint_pass",
    "joint_pass_fraction", "failure_chids", "selected_by_full_data_nrmse",
    "screen_status",
)
LOOCV_FIELDS = (
    "held_out_chid", "selected_p_upstream", "selected_p_downstream",
    "held_out_valid", "held_out_far_nrmse", "held_out_log_r2",
    "held_out_max_k_rel_change", "far_pass", "interval_pass", "joint_pass",
    "selection_method",
)
DECISION_FIELDS = (
    "n_cases", "minimum_fraction", "full_data_selected_p_upstream",
    "full_data_selected_p_downstream", "full_data_joint_pass",
    "full_data_joint_pass_fraction", "loocv_joint_pass",
    "loocv_joint_pass_fraction", "decision", "evidence_boundary",
)


def _read_profiles(path):
    grouped = {}
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            chid = str(row.get("chid") or "").strip()
            if not chid:
                raise ValueError("时间平均表存在空 CHID")
            try:
                x = float(row["x"])
                value = float(row["T_bar"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"{chid}: x/T_bar 无效") from exc
            if not math.isfinite(x) or not math.isfinite(value):
                raise ValueError(f"{chid}: x/T_bar 必须有限")
            grouped.setdefault(chid, []).append((x, value))
    for chid, rows in grouped.items():
        rows.sort()
        if len(rows) < 7 or len({row[0] for row in rows}) != len(rows):
            raise ValueError(f"{chid}: 剖面至少 7 点且 x 不重复")
    if not grouped:
        raise ValueError("时间平均表为空")
    return grouped


def _features(xs, xp, p_upstream, p_downstream):
    upstream = np.zeros_like(xs, dtype=float)
    downstream = np.zeros_like(xs, dtype=float)
    left = xs < xp
    upstream[left] = (xp - xs[left]) ** p_upstream
    downstream[~left] = (xs[~left] - xp) ** p_downstream
    return upstream, downstream


def _r2(observed, predicted):
    mean = float(np.mean(observed))
    denominator = float(np.sum((observed - mean) ** 2))
    if denominator <= 1e-15:
        return 1.0 if np.allclose(observed, predicted) else -math.inf
    return 1.0 - float(np.sum((observed - predicted) ** 2)) / denominator


def fit_fixed_exponents(
    xs, values, p_upstream, p_downstream, H=5.0,
    near_exclusion_H=0.3, max_distance_H=None,
    detection_threshold=5.0, min_side_points=3,
):
    xs = np.asarray(xs, dtype=float)
    values = np.asarray(values, dtype=float)
    if xs.ndim != 1 or values.shape != xs.shape or xs.size < 7:
        raise ValueError("剖面维度或点数无效")
    if np.any(~np.isfinite(xs)) or np.any(~np.isfinite(values)) or np.any(np.diff(xs) <= 0):
        raise ValueError("剖面必须有限且 x 严格递增")
    if min(p_upstream, p_downstream, H) <= 0 or near_exclusion_H < 0:
        raise ValueError("指数和 H 必须为正，近场排除不得为负")
    spacing = float(np.min(np.diff(xs)))
    peak_x = float(xs[int(np.argmax(values))])
    span = max(near_exclusion_H * H, 2.0 * spacing)
    lo = max(float(xs[0]), peak_x - span)
    hi = min(float(xs[-1]), peak_x + span)
    if not lo < hi:
        raise ValueError("源中心搜索区间无效")
    step = spacing / 10.0
    n_step = int(math.floor((hi - lo) / step))
    candidates = [lo + index * step for index in range(n_step + 1)]
    if not math.isclose(candidates[-1], hi, rel_tol=0.0, abs_tol=1e-12):
        candidates.append(hi)
    best = None
    for xp in candidates:
        selected = (np.abs(xs - xp) >= near_exclusion_H * H) & (values >= detection_threshold)
        if max_distance_H is not None:
            selected &= np.abs(xs - xp) <= max_distance_H * H
        if np.count_nonzero(selected & (xs < xp)) < min_side_points:
            continue
        if np.count_nonzero(selected & (xs >= xp)) < min_side_points:
            continue
        upstream, downstream = _features(xs, xp, p_upstream, p_downstream)
        matrix = np.column_stack((
            np.ones(np.count_nonzero(selected)),
            -upstream[selected], -downstream[selected],
        ))
        coefficients = np.linalg.lstsq(matrix, np.log(values[selected]), rcond=None)[0]
        if coefficients[1] <= 0 or coefficients[2] <= 0:
            continue
        predicted_log = matrix @ coefficients
        log_rmse = float(np.sqrt(np.mean((predicted_log - np.log(values[selected])) ** 2)))
        if best is not None and log_rmse >= best["log_rmse"]:
            continue
        full_prediction = np.exp(
            coefficients[0] - coefficients[1] * upstream - coefficients[2] * downstream
        )
        far_nrmse = float(
            np.sqrt(np.mean((full_prediction[selected] - values[selected]) ** 2))
            / np.max(values[selected])
        )
        best = {
            "xp": float(xp), "k_u": float(coefficients[1]),
            "k_d": float(coefficients[2]), "log_rmse": log_rmse,
            "log_r2": _r2(np.log(values[selected]), predicted_log),
            "far_nrmse": far_nrmse, "prediction": full_prediction.tolist(),
        }
    if best is None:
        raise ValueError("伸缩指数拟合因点数不足、矩阵退化或非正衰减失败")
    return best


def _case_metrics(rows, p_upstream, p_downstream, threshold=5.0):
    xs = [row[0] for row in rows]
    values = [row[1] for row in rows]
    peak = cp.estimate_peak(xs, values)
    classification = cp.classify_upstream_observation(
        xs, values, peak["xp"], threshold, H=5.0, near_exclusion_H=0.3,
    )
    if classification["category"] != "bilateral_identifiable":
        raise ValueError(classification["category"])
    base = fit_fixed_exponents(xs, values, p_upstream, p_downstream)
    fits = [fit_fixed_exponents(
        xs, values, p_upstream, p_downstream,
        near_exclusion_H=near, max_distance_H=maximum,
    ) for near, maximum in WINDOWS_H]
    baseline = fits[0]
    max_change = max(
        max(
            abs(fit["k_u"] - baseline["k_u"]) / baseline["k_u"],
            abs(fit["k_d"] - baseline["k_d"]) / baseline["k_d"],
        )
        for fit in fits
    )
    far_pass = base["log_r2"] >= 0.85 and base["far_nrmse"] <= 0.20
    interval_pass = max_change <= 0.20
    return {
        **base, "max_k_rel_change": max_change,
        "far_pass": far_pass, "interval_pass": interval_pass,
        "joint_pass": far_pass and interval_pass,
    }


def _write_rows(path, fields, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def evaluate(profiles_path=DEFAULT_PROFILES, outdir=DEFAULT_OUTDIR,
             exponents=EXPONENTS, minimum_fraction=0.75):
    profiles = _read_profiles(profiles_path)
    if not 0 < minimum_fraction <= 1:
        raise ValueError("minimum_fraction 必须在 (0,1] 内")
    exponent_values = tuple(float(value) for value in exponents)
    if not exponent_values or any(not math.isfinite(value) or value <= 0 for value in exponent_values):
        raise ValueError("候选指数必须为有限正数")
    metrics = {}
    candidate_rows = []
    for p_upstream in exponent_values:
        for p_downstream in exponent_values:
            key = (p_upstream, p_downstream)
            case_metrics = {}
            failures = []
            for chid, rows in profiles.items():
                try:
                    case_metrics[chid] = _case_metrics(rows, p_upstream, p_downstream)
                except ValueError:
                    failures.append(chid)
            metrics[key] = case_metrics
            scores = [row["far_nrmse"] for row in case_metrics.values()]
            n_far = sum(row["far_pass"] for row in case_metrics.values())
            n_interval = sum(row["interval_pass"] for row in case_metrics.values())
            n_joint = sum(row["joint_pass"] for row in case_metrics.values())
            fraction = n_joint / len(profiles)
            candidate_rows.append({
                "p_upstream": f"{p_upstream:.6f}",
                "p_downstream": f"{p_downstream:.6f}",
                "n_cases": len(profiles), "n_valid": len(case_metrics),
                "n_fail": len(failures),
                "median_far_nrmse": "" if not scores else f"{statistics.median(scores):.9f}",
                "n_far_pass": n_far, "n_interval_pass": n_interval,
                "n_joint_pass": n_joint, "joint_pass_fraction": f"{fraction:.9f}",
                "failure_chids": ";".join(failures),
                "selected_by_full_data_nrmse": "false",
                "screen_status": "PASS_SCREEN" if fraction >= minimum_fraction else "FAIL_SCREEN",
            })
    selected_row = min(
        (row for row in candidate_rows if row["median_far_nrmse"]),
        key=lambda row: (
            float(row["median_far_nrmse"]), -int(row["n_joint_pass"]),
            float(row["p_upstream"]), float(row["p_downstream"]),
        ),
    )
    selected_row["selected_by_full_data_nrmse"] = "true"

    loocv_rows = []
    for held_out in profiles:
        training = [chid for chid in profiles if chid != held_out]
        selection = min(
            metrics,
            key=lambda key: (
                statistics.median([
                    metrics[key][chid]["far_nrmse"]
                    for chid in training if chid in metrics[key]
                ]),
                key,
            ),
        )
        held_metrics = metrics[selection].get(held_out)
        loocv_rows.append({
            "held_out_chid": held_out,
            "selected_p_upstream": f"{selection[0]:.6f}",
            "selected_p_downstream": f"{selection[1]:.6f}",
            "held_out_valid": "true" if held_metrics else "false",
            "held_out_far_nrmse": "" if not held_metrics else f"{held_metrics['far_nrmse']:.9f}",
            "held_out_log_r2": "" if not held_metrics else f"{held_metrics['log_r2']:.9f}",
            "held_out_max_k_rel_change": "" if not held_metrics else f"{held_metrics['max_k_rel_change']:.9f}",
            "far_pass": "true" if held_metrics and held_metrics["far_pass"] else "false",
            "interval_pass": "true" if held_metrics and held_metrics["interval_pass"] else "false",
            "joint_pass": "true" if held_metrics and held_metrics["joint_pass"] else "false",
            "selection_method": "minimum training-case median far NRMSE",
        })
    loocv_joint = sum(row["joint_pass"] == "true" for row in loocv_rows)
    full_joint = int(selected_row["n_joint_pass"])
    decision_rows = [{
        "n_cases": len(profiles), "minimum_fraction": f"{minimum_fraction:.6f}",
        "full_data_selected_p_upstream": selected_row["p_upstream"],
        "full_data_selected_p_downstream": selected_row["p_downstream"],
        "full_data_joint_pass": full_joint,
        "full_data_joint_pass_fraction": f"{full_joint / len(profiles):.9f}",
        "loocv_joint_pass": loocv_joint,
        "loocv_joint_pass_fraction": f"{loocv_joint / len(profiles):.9f}",
        "decision": (
            "RETAIN_FOR_FULL_GATE_REVIEW"
            if min(full_joint, loocv_joint) / len(profiles) >= minimum_fraction
            else "REJECT_AS_STAGE2_RESCUE_CANDIDATE"
        ),
        "evidence_boundary": (
            "screen uses only far-shape and interval-stability gates; it cannot pass the "
            "full stage-2 decision when this necessary screen fails"
        ),
    }]
    outdir = Path(outdir)
    _write_rows(outdir / "stretched_exponent_candidates.csv", CANDIDATE_FIELDS, candidate_rows)
    _write_rows(outdir / "stretched_exponent_loocv.csv", LOOCV_FIELDS, loocv_rows)
    _write_rows(outdir / "stretched_exponent_decision.csv", DECISION_FIELDS, decision_rows)
    return {"candidates": candidate_rows, "loocv": loocv_rows, "decision": decision_rows[0]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", default=str(DEFAULT_PROFILES))
    parser.add_argument("--outdir", default=str(DEFAULT_OUTDIR))
    parser.add_argument("--minimum-fraction", type=float, default=0.75)
    args = parser.parse_args()
    try:
        result = evaluate(args.profiles, args.outdir, minimum_fraction=args.minimum_fraction)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    row = result["decision"]
    print(
        f"full-data joint={row['full_data_joint_pass']}/{row['n_cases']}, "
        f"LOOCV joint={row['loocv_joint_pass']}/{row['n_cases']}; {row['decision']}"
    )


if __name__ == "__main__":
    main()
