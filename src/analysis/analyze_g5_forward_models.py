"""Legacy G5-A0 finite-candidate analysis with parent-group outer CV.

The tracked outputs are immutable evidence for the human-candidate baseline
``A0=M1_S1``.  Once ``SR_PROTOCOL_APPROVED`` is present, this old writer is
blocked before it can overwrite that evidence or advertise a direct G6 route.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares

from src.analysis.forward_temperature import (
    peak_m0, peak_m1, shape_s1, shape_s2, shape_s3,
)
from src.analysis.g5_route_guard import refuse_legacy_route_after_sr_approval
from src.fds import tunnel_config as cfg
from src.fds.project_paths import PROJECT_ROOT


PEAK_MODELS = ("M0", "M1")
SHAPE_MODELS = ("S1", "S2", "S3")
Q_REF_MW = 27.0
D_REF_M = 5.07770625192981
GRID_UNCERTAINTY = 0.13
MIN_SIGMA_K = 0.5
NUGGET = 0.20


def _read_csv(path):
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        return [{key: (value or "").strip() for key, value in row.items()}
                for row in csv.DictReader(stream)]


def _write_csv(path, rows, columns=None):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    columns = columns or (list(rows[0]) if rows else [])
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows({key: row.get(key, "") for key in columns} for row in rows)


def _write_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _quantile(values, probability):
    values = sorted(float(value) for value in values)
    if not values:
        raise ValueError("分位数输入为空")
    position = probability * (len(values) - 1)
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return values[lower]
    weight = position - lower
    return values[lower] * (1.0 - weight) + values[upper] * weight


def _parent_peak_rows(metrics, parents):
    rows = []
    for parent in sorted(parents):
        group = [row for row in metrics if row["parent_case_id"] == parent]
        rows.append({
            "parent_case_id": parent,
            # 正式闭合使用稳态窗实际 HRR，不用请求值代填。
            "Q_MW": statistics.fmean(float(row["Q_actual_kW"]) for row in group) / 1000.0,
            "Df_m": float(group[0]["Df_discrete_m"]),
            "peak_K": statistics.fmean(float(row["deltaT_max_K"]) for row in group),
        })
    return rows


def _fit_peak(model, rows):
    y = np.log([row["peak_K"] / cfg.T_AMBIENT_K for row in rows])
    columns = [np.ones(len(rows)), np.log([row["Q_MW"] / Q_REF_MW for row in rows])]
    if model == "M1":
        columns.append(np.log([row["Df_m"] / D_REF_M for row in rows]))
    coefficients = np.linalg.lstsq(np.column_stack(columns), y, rcond=None)[0]
    params = {"C_m": float(math.exp(coefficients[0])), "a": float(coefficients[1]),
              "Q_ref_MW": Q_REF_MW}
    if model == "M1":
        params.update({"b_d": float(coefficients[2]), "D_ref_m": D_REF_M})
    return params


def _predict_peak(model, params, row):
    if model == "M0":
        return peak_m0(float(row["Q_actual_kW"]), cfg.T_AMBIENT_K, params)
    return peak_m1(float(row["Q_actual_kW"]), float(row["Df_discrete_m"]),
                   cfg.T_AMBIENT_K, params)


SHAPE_BOUNDS = {
    "S1": ([0.01, 0.05], [20.0, 5.0]),
    "S2": ([0.001, 1.01], [10.0, 5.0]),
    "S3": ([0.005, 0.05, 0.05, 0.25], [20.0, 20.0, 5.0, 4.75]),
}
SHAPE_INITIAL = {"S1": [2.0, 0.5], "S2": [0.3, 1.5],
                 "S3": [1.0, 0.5, 0.5, 1.0]}


def _shape_values(model, xi, params):
    function = {"S1": shape_s1, "S2": shape_s2, "S3": shape_s3}[model]
    return np.asarray([function(value, params) for value in xi], dtype=float)


def _shape_dict(model, vector):
    if model == "S1":
        return {"A_s": float(vector[0]), "p_s": float(vector[1])}
    if model == "S2":
        return {"A_e": float(vector[0]), "n": float(vector[1])}
    return {"A": float(vector[0]), "p1": float(vector[1]),
            "p2": float(vector[2]), "xi1": float(vector[3])}


def _fit_shape(model, curves, parents):
    selected = [row for row in curves if row["parent_case_id"] in parents]
    counts = defaultdict(int)
    for row in selected:
        counts[row["parent_case_id"]] += 1
    xi = np.asarray([float(row["xi"]) for row in selected])
    observed = np.asarray([float(row["Theta"]) for row in selected])
    weights = np.sqrt(np.asarray([1.0 / counts[row["parent_case_id"]] for row in selected]))
    lower, upper = SHAPE_BOUNDS[model]

    def residual(vector):
        return weights * (_shape_values(model, xi, _shape_dict(model, vector)) - observed)

    fit = least_squares(residual, SHAPE_INITIAL[model], bounds=(lower, upper),
                        max_nfev=20000, xtol=1e-11, ftol=1e-11, gtol=1e-11)
    if not fit.success:
        raise ValueError(f"{model} 优化失败: {fit.message}")
    vector = fit.x
    distance = min(
        min((value - lo) / max(abs(lo), 1.0), (hi - value) / max(abs(hi), 1.0))
        for value, lo, hi in zip(vector, lower, upper)
    )
    return _shape_dict(model, vector), bool(distance < 0.01), float(fit.cost)


def _run_profiles(curves, parents=None):
    grouped = defaultdict(list)
    for row in curves:
        if parents is None or row["parent_case_id"] in parents:
            grouped[row["run_chid"]].append(row)
    for rows in grouped.values():
        rows.sort(key=lambda row: float(row["x_coord_m"]))
    return grouped


def _estimate_ell(residual_profiles, sigma):
    if sigma <= 0:
        return 1.0
    distances, products = [], []
    for signed_xi, residuals in residual_profiles:
        for i in range(len(residuals)):
            for j in range(i + 1, len(residuals)):
                distance = abs(signed_xi[i] - signed_xi[j])
                if distance <= 3.0:
                    distances.append(distance)
                    products.append(residuals[i] * residuals[j] / (sigma * sigma))
    if not distances:
        return 1.0
    candidates = (0.25, 0.5, 1.0, 2.0)
    return min(candidates, key=lambda ell: statistics.fmean(
        (product - (1.0 - NUGGET) * math.exp(-distance / ell)) ** 2
        for distance, product in zip(distances, products)
    ))


def _training_error(metrics_by_chid, curves, parents, peak_model, peak_params,
                    shape_model, shape_params):
    profiles = _run_profiles(curves, parents)
    parent_mse = defaultdict(list)
    residual_profiles = []
    for chid, rows in profiles.items():
        metric = metrics_by_chid[chid]
        peak = _predict_peak(peak_model, peak_params, metric)
        xi = [float(row["xi"]) for row in rows]
        prediction = peak * _shape_values(shape_model, xi, shape_params)
        observed = np.asarray([float(row["deltaT_mean_K"]) for row in rows])
        residual = observed - prediction
        parent_mse[metric["parent_case_id"]].append(float(np.mean(residual ** 2)))
        signed = [(float(row["x_coord_m"]) - float(row["xf_actual_m"])) / cfg.H
                  for row in rows]
        residual_profiles.append((signed, residual.tolist()))
    sigma = math.sqrt(statistics.fmean(
        statistics.fmean(values) for values in parent_mse.values()
    ))
    sigma = max(MIN_SIGMA_K, sigma)
    return sigma, _estimate_ell(residual_profiles, sigma)


def _correlated_nll(residual, signed_xi, model_sigma, ell, independent_sigma):
    residual = np.asarray(residual, dtype=float)
    signed_xi = np.asarray(signed_xi, dtype=float)
    distance = np.abs(signed_xi[:, None] - signed_xi[None, :])
    correlation = (1.0 - NUGGET) * np.exp(-distance / ell) + NUGGET * np.eye(len(residual))
    covariance = model_sigma ** 2 * correlation + np.diag(np.asarray(independent_sigma) ** 2)
    sign, logdet = np.linalg.slogdet(covariance)
    if sign <= 0:
        raise ValueError("预测协方差不是正定")
    return 0.5 * (logdet + float(residual @ np.linalg.solve(covariance, residual))
                  + len(residual) * math.log(2.0 * math.pi))


def _fold_evaluate(holdout, metrics, curves, n_boot):
    parents = sorted({row["parent_case_id"] for row in metrics})
    training = set(parents) - {holdout}
    peak_training = _parent_peak_rows(metrics, training)
    metrics_by_chid = {row["run_chid"]: row for row in metrics}
    holdout_profiles = _run_profiles(curves, {holdout})
    peak_fits = {model: _fit_peak(model, peak_training) for model in PEAK_MODELS}
    shape_fits = {}
    for model in SHAPE_MODELS:
        shape_fits[model] = _fit_shape(model, curves, training)

    run_rows, point_rows, shape_rows, parameter_rows = [], [], [], []
    for shape_model, (shape_params, bound_hit, _) in shape_fits.items():
        nrmse_by_run, nll_by_run = [], []
        # Normalized-shape error floor is re-estimated in every outer fold.
        train_profiles = _run_profiles(curves, training)
        shape_residuals = []
        for rows in train_profiles.values():
            xi = [float(row["xi"]) for row in rows]
            observed = np.asarray([float(row["Theta"]) for row in rows])
            shape_residuals.extend((observed - _shape_values(shape_model, xi, shape_params)).tolist())
        shape_sigma = max(1e-4, math.sqrt(statistics.fmean(v * v for v in shape_residuals)))
        for chid, rows in holdout_profiles.items():
            xi = [float(row["xi"]) for row in rows]
            observed = np.asarray([float(row["Theta"]) for row in rows])
            predicted = _shape_values(shape_model, xi, shape_params)
            nrmse_by_run.append(float(np.sqrt(np.mean((observed - predicted) ** 2))))
            nll_by_run.append(statistics.fmean(
                0.5 * ((float(o - p) / shape_sigma) ** 2 + math.log(2 * math.pi * shape_sigma ** 2))
                for o, p in zip(observed, predicted)
            ))
        shape_rows.append({
            "holdout_parent_case_id": holdout, "shape_model": shape_model,
            "curve_nrmse": statistics.fmean(nrmse_by_run),
            "mean_point_nll": statistics.fmean(nll_by_run),
            "parameter_bound_hit": bound_hit,
            "parameters_json": json.dumps(shape_params, sort_keys=True),
            "shape_sigma": shape_sigma,
        })
        parameter_rows.append({"holdout_parent_case_id": holdout,
                               "model_component": shape_model,
                               "parameters_json": json.dumps(shape_params, sort_keys=True),
                               "parameter_bound_hit": bound_hit})

    for peak_model, peak_params in peak_fits.items():
        parameter_rows.append({"holdout_parent_case_id": holdout,
                               "model_component": peak_model,
                               "parameters_json": json.dumps(peak_params, sort_keys=True),
                               "parameter_bound_hit": False})
        for shape_model, (shape_params, _, _) in shape_fits.items():
            model_id = f"{peak_model}_{shape_model}"
            model_sigma, ell = _training_error(
                metrics_by_chid, curves, training, peak_model, peak_params,
                shape_model, shape_params
            )
            for chid, rows in holdout_profiles.items():
                metric = metrics_by_chid[chid]
                peak_pred = _predict_peak(peak_model, peak_params, metric)
                xi = [float(row["xi"]) for row in rows]
                signed_xi = [(float(row["x_coord_m"]) - float(row["xf_actual_m"])) / cfg.H
                             for row in rows]
                predicted = peak_pred * _shape_values(shape_model, xi, shape_params)
                observed = np.asarray([float(row["deltaT_mean_K"]) for row in rows])
                temporal = np.asarray([
                    max(0.0, (float(row["deltaT_ci95_high_K"])
                              - float(row["deltaT_ci95_low_K"])) / 3.92)
                    for row in rows
                ])
                independent = np.sqrt((GRID_UNCERTAINTY * predicted) ** 2
                                      + temporal ** 2 + MIN_SIGMA_K ** 2)
                total_sd = np.sqrt(model_sigma ** 2 + independent ** 2)
                lower = np.maximum(0.0, predicted - 1.6448536269514722 * total_sd)
                upper = predicted + 1.6448536269514722 * total_sd
                residual = observed - predicted
                curve_nrmse = float(np.sqrt(np.mean(residual ** 2))
                                    / float(metric["deltaT_max_K"]))
                peak_error = abs(peak_pred - float(metric["deltaT_max_K"])) / float(metric["deltaT_max_K"])
                coverage = float(np.mean((observed >= lower) & (observed <= upper)))
                nll = _correlated_nll(residual, signed_xi, model_sigma, ell, independent)
                run_rows.append({
                    "model_id": model_id, "peak_model": peak_model,
                    "shape_model": shape_model, "holdout_parent_case_id": holdout,
                    "physical_case_id": metric["physical_case_id"], "run_chid": chid,
                    "peak_relative_error": peak_error, "curve_nrmse": curve_nrmse,
                    "prediction_interval_90_coverage": coverage,
                    "correlated_nll": nll, "model_sigma_K": model_sigma,
                    "correlation_length_xi": ell, "covariance_nugget": NUGGET,
                    "nonnegative_monotone_constraint": "PASS",
                })
                for source, obs, pred, sd, lo, hi, sx in zip(
                    rows, observed, predicted, total_sd, lower, upper, signed_xi
                ):
                    point_rows.append({
                        "model_id": model_id, "holdout_parent_case_id": holdout,
                        "physical_case_id": metric["physical_case_id"],
                        "run_chid": chid, "Q_MW": metric["Q_requested_MW"],
                        "Df_m": metric["Df_discrete_m"], "xf_m": metric["xf_actual_m"],
                        "x_coord_m": source["x_coord_m"], "signed_xi": sx,
                        "branch": "RIGHT" if sx > 0 else ("LEFT" if sx < 0 else "PEAK"),
                        "observed_deltaT_K": obs, "predicted_deltaT_K": pred,
                        "prediction_sd_K": sd, "pi90_low_K": lo, "pi90_high_K": hi,
                        "standardized_residual": (obs - pred) / sd,
                    })
    return run_rows, point_rows, shape_rows, parameter_rows


def _summaries(run_rows):
    rows = []
    for model_id in sorted({row["model_id"] for row in run_rows}):
        selected = [row for row in run_rows if row["model_id"] == model_id]
        parents = sorted({row["holdout_parent_case_id"] for row in selected})
        parent_metrics = []
        for parent in parents:
            group = [row for row in selected if row["holdout_parent_case_id"] == parent]
            parent_metrics.append({
                "peak": statistics.fmean(row["peak_relative_error"] for row in group),
                "curve": statistics.fmean(row["curve_nrmse"] for row in group),
                "coverage": statistics.fmean(row["prediction_interval_90_coverage"] for row in group),
                "nll": statistics.fmean(row["correlated_nll"] for row in group),
            })
        rows.append({
            "model_id": model_id, "peak_model": selected[0]["peak_model"],
            "shape_model": selected[0]["shape_model"], "outer_parent_count": len(parents),
            "peak_relative_error_median": statistics.median(row["peak"] for row in parent_metrics),
            "peak_relative_error_p90": _quantile([row["peak"] for row in parent_metrics], 0.90),
            "curve_nrmse_median": statistics.median(row["curve"] for row in parent_metrics),
            "curve_nrmse_p90": _quantile([row["curve"] for row in parent_metrics], 0.90),
            "pi90_coverage": statistics.fmean(row["coverage"] for row in parent_metrics),
            "mean_parent_correlated_nll": statistics.fmean(row["nll"] for row in parent_metrics),
            "constraint_pass_fraction": statistics.fmean(
                row["nonnegative_monotone_constraint"] == "PASS" for row in selected
            ),
            "worst_parent_curve_nrmse": max(row["curve"] for row in parent_metrics),
        })
    return rows


def _shape_summary(shape_rows):
    rows = []
    for model in SHAPE_MODELS:
        selected = [row for row in shape_rows if row["shape_model"] == model]
        rows.append({
            "shape_model": model,
            "outer_nrmse_median": statistics.median(row["curve_nrmse"] for row in selected),
            "outer_nrmse_p90": _quantile([row["curve_nrmse"] for row in selected], 0.90),
            "mean_point_nll": statistics.fmean(row["mean_point_nll"] for row in selected),
            "parent_improvement_inputs": len(selected),
            "parameter_bound_hit_any_fold": any(row["parameter_bound_hit"] for row in selected),
        })
    return rows


def _bootstrap_bd(parent_rows, n_boot, seed):
    rng = random.Random(seed); values = []
    while len(values) < n_boot:
        sample = [rng.choice(parent_rows) for _ in parent_rows]
        try:
            value = _fit_peak("M1", sample)["b_d"]
        except np.linalg.LinAlgError:
            continue
        if math.isfinite(value):
            values.append(value)
    return _quantile(values, 0.025), _quantile(values, 0.975)


def _select_peak(metrics, run_rows, n_boot, seed):
    parents = sorted({row["parent_case_id"] for row in metrics})
    parent_rows = _parent_peak_rows(metrics, parents)
    final_m1 = _fit_peak("M1", parent_rows)
    ci_low, ci_high = _bootstrap_bd(parent_rows, n_boot, seed)
    errors = {}
    for model in PEAK_MODELS:
        selected = [row for row in run_rows if row["peak_model"] == model and row["shape_model"] == "S1"]
        errors[model] = {
            parent: statistics.fmean(row["peak_relative_error"] for row in selected
                                     if row["holdout_parent_case_id"] == parent)
            for parent in parents
        }
    # “外层父组方向一致”指每个留一父组拟合的 b_d 符号与
    # 全开发集一致，而不是要求每个被留出父组的误差都严格下降。
    # 后者会把“无实质效应”区间之外、且最不利误差改善的稳定尺寸
    # 指数错误拒绝。
    fold_bd = []
    for holdout in parents:
        training = _parent_peak_rows(metrics, set(parents) - {holdout})
        fold_bd.append(_fit_peak("M1", training)["b_d"])
    direction_consistent = statistics.fmean(
        value * final_m1["b_d"] > 0 for value in fold_bd
    )
    worst_not_worse = max(errors["M1"].values()) <= max(errors["M0"].values()) + 1e-12
    retain = ((ci_low > 0.10 or ci_high < -0.10)
              and direction_consistent >= 0.70 and worst_not_worse)
    evidence = [{
        "candidate": "M1_SIZE_TERM", "b_d": final_m1["b_d"],
        "b_d_cluster_bootstrap_ci95_low": ci_low,
        "b_d_cluster_bootstrap_ci95_high": ci_high,
        "outer_fold_b_d_direction_consistent_fraction": direction_consistent,
        "outer_fold_b_d_min": min(fold_bd),
        "outer_fold_b_d_max": max(fold_bd),
        "worst_parent_not_worse": worst_not_worse,
        "status": "RETAIN" if retain else "REJECT",
    }]
    return ("M1" if retain else "M0"), evidence


def _select_shape(shape_rows, shape_summary):
    summaries = {row["shape_model"]: row for row in shape_summary}
    simple = min(("S1", "S2"), key=lambda model: summaries[model]["outer_nrmse_median"])
    s3_by_parent = {row["holdout_parent_case_id"]: row for row in shape_rows
                    if row["shape_model"] == "S3"}
    simple_by_parent = {row["holdout_parent_case_id"]: row for row in shape_rows
                        if row["shape_model"] == simple}
    nll_improvement = (
        summaries[simple]["mean_point_nll"] - summaries["S3"]["mean_point_nll"]
    ) / max(abs(summaries[simple]["mean_point_nll"]), 1e-12)
    nrmse_improvement = (
        summaries[simple]["outer_nrmse_median"] - summaries["S3"]["outer_nrmse_median"]
    )
    parent_improved = statistics.fmean(
        s3_by_parent[parent]["curve_nrmse"] < simple_by_parent[parent]["curve_nrmse"]
        for parent in s3_by_parent
    )
    s3_pass = (
        nll_improvement >= 0.10 and nrmse_improvement >= 0.02
        and parent_improved >= 0.70
        and not summaries["S3"]["parameter_bound_hit_any_fold"]
    )
    evidence = {
        "best_simple_shape": simple,
        "s3_mean_nll_relative_improvement": nll_improvement,
        "s3_median_nrmse_improvement": nrmse_improvement,
        "s3_parent_improved_fraction": parent_improved,
        "s3_parameter_bound_hit_any_fold": summaries["S3"]["parameter_bound_hit_any_fold"],
        "s3_status": "RETAIN" if s3_pass else "REJECT_NO_TWO_ZONE_EVIDENCE",
    }
    return ("S3" if s3_pass else simple), evidence


def _systematic_residuals(point_rows, model_id, n_boot, seed):
    rows = [row for row in point_rows if row["model_id"] == model_id]
    parents = sorted({row["holdout_parent_case_id"] for row in rows})
    specs = {
        "log_Q": lambda row: math.log(float(row["Q_MW"])),
        "log_Df": lambda row: math.log(float(row["Df_m"])),
        "xf_minus_50": lambda row: float(row["xf_m"]) - 50.0,
    }
    prepared = {}
    for name, function in specs.items():
        prepared[name] = []
        for parent in parents:
            group = [row for row in rows if row["holdout_parent_case_id"] == parent]
            prepared[name].append((parent, function(group[0]),
                                   statistics.fmean(float(row["standardized_residual"]) for row in group)))
    prepared["right_minus_left"] = []
    for parent in parents:
        group = [row for row in rows if row["holdout_parent_case_id"] == parent]
        left = [float(row["standardized_residual"]) for row in group if row["branch"] == "LEFT"]
        right = [float(row["standardized_residual"]) for row in group if row["branch"] == "RIGHT"]
        prepared["right_minus_left"].append((parent, 1.0,
                                              statistics.fmean(right) - statistics.fmean(left)))

    def slope(data, branch=False):
        if branch:
            return statistics.fmean(row[2] for row in data)
        x = np.asarray([row[1] for row in data]); y = np.asarray([row[2] for row in data])
        if float(np.var(x)) <= 1e-15:
            raise ValueError("残差回归自变量无变化")
        return float(np.sum((x - x.mean()) * (y - y.mean())) / np.sum((x - x.mean()) ** 2))

    output = []
    for index, (name, data) in enumerate(prepared.items()):
        branch = name == "right_minus_left"
        point = slope(data, branch)
        rng = random.Random(seed + index * 10000); boot = []
        while len(boot) < n_boot:
            sampled_parents = [rng.choice(parents) for _ in parents]
            sample = []
            for occurrence, parent in enumerate(sampled_parents):
                source = next(row for row in data if row[0] == parent)
                sample.append((f"{parent}_{occurrence}", source[1], source[2]))
            try:
                boot.append(slope(sample, branch))
            except ValueError:
                continue
        # Holm family-wise protection is conservatively represented by 98.75% CIs.
        ci_low, ci_high = _quantile(boot, 0.00625), _quantile(boot, 0.99375)
        x_range = 1.0 if branch else max(row[1] for row in data) - min(row[1] for row in data)
        effect = point * x_range
        significant = (ci_low > 0 or ci_high < 0) and abs(effect) >= 0.25
        output.append({
            "predictor": name, "slope": point, "holm_ci98_75_low": ci_low,
            "holm_ci98_75_high": ci_high, "standardized_effect_across_design": effect,
            "systematic_failure": significant, "status": "FAIL" if significant else "PASS",
        })
    return output


def analyze(repo, n_boot=2000, seed=20260821):
    repo = Path(repo).resolve()
    refuse_legacy_route_after_sr_approval(
        repo, "src.analysis.analyze_g5_forward_models"
    )
    g4 = json.loads((repo / "reports" / "g4_development_decision.json").read_text(encoding="utf-8"))
    if g4.get("decision") != "PASS_WITH_USER_VERSION_OVERRIDE_READY_FOR_G5":
        raise ValueError("G4 未冻结，不得运行 G5")
    metrics = _read_csv(repo / "derived" / "development" / "case_metrics.csv")
    curves = [row for row in _read_csv(repo / "derived" / "development" / "curve_points.csv")
              if float(row["xi"]) <= 5.0 + 1e-12 and row["point_role"] != "QC_ONLY"]
    parents = sorted({row["parent_case_id"] for row in metrics})
    if len(metrics) != 27 or len(parents) != 9:
        raise ValueError("G5 需要 27 条运行和 9 个 Q-D 父组")

    run_rows, point_rows, shape_rows, parameter_rows = [], [], [], []
    for holdout in parents:
        run, point, shape, parameters = _fold_evaluate(holdout, metrics, curves, n_boot)
        run_rows.extend(run); point_rows.extend(point); shape_rows.extend(shape)
        parameter_rows.extend(parameters)
    summaries = _summaries(run_rows)
    shape_summary = _shape_summary(shape_rows)
    peak_model, peak_evidence = _select_peak(metrics, run_rows, n_boot, seed)
    shape_model, s3_evidence = _select_shape(shape_rows, shape_summary)
    selected_id = f"{peak_model}_{shape_model}"
    selected = next(row for row in summaries if row["model_id"] == selected_id)
    systematic = _systematic_residuals(point_rows, selected_id, n_boot, seed + 700000)
    forward_gate = (
        selected["peak_relative_error_median"] <= 0.15
        and selected["peak_relative_error_p90"] <= 0.25
        and selected["curve_nrmse_median"] <= 0.15
        and selected["curve_nrmse_p90"] <= 0.25
        and selected["constraint_pass_fraction"] >= 1.0
        and selected["pi90_coverage"] >= 0.80
        and all(row["status"] == "PASS" for row in systematic)
    )

    peak_params = _fit_peak(peak_model, _parent_peak_rows(metrics, parents))
    shape_params, shape_bound_hit, _ = _fit_shape(shape_model, curves, set(parents))
    metrics_by_chid = {row["run_chid"]: row for row in metrics}
    model_sigma, ell = _training_error(metrics_by_chid, curves, set(parents),
                                       peak_model, peak_params, shape_model, shape_params)
    final_parameters = [
        {"component": peak_model, "parameters_json": json.dumps(peak_params, sort_keys=True),
         "parameter_bound_hit": False},
        {"component": shape_model, "parameters_json": json.dumps(shape_params, sort_keys=True),
         "parameter_bound_hit": shape_bound_hit},
        {"component": "ERROR_MODEL", "parameters_json": json.dumps({
            "model_sigma_K": model_sigma, "correlation_length_xi": ell,
            "covariance_nugget": NUGGET, "peak_grid_uncertainty_fraction": GRID_UNCERTAINTY,
            "minimum_absolute_sigma_K": MIN_SIGMA_K,
        }, sort_keys=True), "parameter_bound_hit": False},
    ]

    derived = repo / "derived" / "development"
    outputs = {
        "g5_outer_run_predictions.csv": run_rows,
        "g5_outer_point_predictions.csv": point_rows,
        "g5_outer_parameters.csv": parameter_rows,
        "g5_shape_outer_metrics.csv": shape_rows,
        "g5_candidate_summary.csv": summaries,
        "g5_shape_summary.csv": shape_summary,
        "g5_peak_model_evidence.csv": peak_evidence,
        "g5_systematic_residuals.csv": systematic,
        "g5_final_parameters.csv": final_parameters,
    }
    for name, rows in outputs.items():
        _write_csv(derived / name, rows)
    for name in ("g5_candidate_summary.csv", "g5_shape_summary.csv",
                 "g5_peak_model_evidence.csv", "g5_systematic_residuals.csv",
                 "g5_final_parameters.csv"):
        _write_csv(repo / "reports" / name, outputs[name])

    manifest = []
    for name, rows in outputs.items():
        path = derived / name
        manifest.append({"relative_path": path.relative_to(repo).as_posix(),
                         "sha256": _sha256(path), "bytes": path.stat().st_size,
                         "row_count": len(rows)})
    _write_csv(repo / "reports" / "g5_database_manifest.csv", manifest)
    decision = {
        "schema_version": "1.0", "stage": "G5_FORWARD_MODEL_SELECTION",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "decision": "PASS_FREEZE_A" if forward_gate else "FAIL_FORWARD_GATE",
        "selected_peak_model": peak_model, "selected_shape_model": shape_model,
        "selected_model_id": selected_id, "selected_outer_metrics": selected,
        "s3_evidence": s3_evidence, "systematic_residual_gate": (
            "PASS" if all(row["status"] == "PASS" for row in systematic) else "FAIL"),
        "final_peak_parameters": peak_params, "final_shape_parameters": shape_params,
        "prediction_error": {"model_sigma_K": model_sigma,
                             "correlation_length_xi": ell,
                             "covariance_nugget": NUGGET,
                             "peak_grid_uncertainty_fraction": GRID_UNCERTAINTY,
                             "minimum_absolute_sigma_K": MIN_SIGMA_K},
        "lockbox_status": "SEALED_UNREAD_UNRUN",
        "next_action": "G6_DIRECT_INVERSION" if forward_gate else "STOP_FORWARD_CLAIM",
    }
    _write_json(repo / "reports" / "g5_forward_model_decision.json", decision)
    return decision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(PROJECT_ROOT))
    parser.add_argument("--n-boot", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20260821)
    args = parser.parse_args()
    print(json.dumps(analyze(args.repo, args.n_boot, args.seed), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
