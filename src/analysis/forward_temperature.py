"""Frozen-family forward callable used by G5 and later direct inversion."""

from __future__ import annotations

import math


def peak_m0(q_actual_kw, t0_k, params):
    q_mw = float(q_actual_kw) / 1000.0
    return float(t0_k) * float(params["C_m"]) * (
        q_mw / float(params["Q_ref_MW"])
    ) ** float(params["a"])


def peak_m1(q_actual_kw, df_discrete_m, t0_k, params):
    return peak_m0(q_actual_kw, t0_k, params) * (
        float(df_discrete_m) / float(params["D_ref_m"])
    ) ** float(params["b_d"])


def shape_s1(xi, params):
    xi = float(xi)
    a_s = float(params["A_s"])
    p_s = float(params["p_s"])
    return (1.0 + (a_s * xi) ** 2) ** (-p_s / 2.0)


def shape_s2(xi, params):
    return math.exp(-float(params["A_e"]) * float(xi) ** float(params["n"]))


def shape_s3(xi, params):
    xi = float(xi)
    a = float(params["A"])
    p1 = float(params["p1"])
    p2 = float(params["p2"])
    xi1 = float(params["xi1"])
    if xi < xi1:
        return (1.0 + (a * xi) ** 2) ** (-p1 / 2.0)
    b = p1 * a * a * xi1 / (p2 * (1.0 + a * a * xi1 * xi1))
    theta1 = (1.0 + (a * xi1) ** 2) ** (-p1 / 2.0)
    return theta1 * (1.0 + b * (xi - xi1)) ** (-p2)


def forward_temperature(q_actual_kw, df_discrete_m, xf_actual_m, sensor_x,
                        t0_k, protocol):
    """Return mean ceiling-gas temperature rise and a frozen status code.

    ``protocol`` is the compact ``forward_callable`` object stored in freeze A.
    The callable refuses unsupported Q, D, x_f or sensor positions rather than
    extrapolating or silently returning zero.
    """
    q = float(q_actual_kw)
    diameter = float(df_discrete_m)
    fire_x = float(xf_actual_m)
    x = float(sensor_x)
    t0 = float(t0_k)
    support = protocol["support"]
    if not (
        float(support["Q_actual_kW"][0]) <= q <= float(support["Q_actual_kW"][1])
        and float(support["Df_discrete_m"][0]) <= diameter <= float(support["Df_discrete_m"][1])
        and float(support["xf_actual_m"][0]) <= fire_x <= float(support["xf_actual_m"][1])
    ):
        return {"mean_deltaT_K": None, "prediction_interval_K": None,
                "status": "OUT_OF_SUPPORT"}
    xi = abs(x - fire_x) / float(support["H_eff_m"])
    if xi > float(support["xi_h"]) + 1e-12:
        return {"mean_deltaT_K": None, "prediction_interval_K": None,
                "status": "OUT_OF_EFFECTIVE_DOMAIN"}
    if protocol["flame_regime_layer"] != "CEILING_IMPINGING_OR_NEAR_IMPINGING":
        return {"mean_deltaT_K": None, "prediction_interval_K": None,
                "status": "FLAME_REGIME_AMBIGUOUS"}

    peak_family = protocol["peak_model"]
    peak_params = protocol["peak_parameters"]
    if peak_family == "M0":
        peak = peak_m0(q, t0, peak_params)
    elif peak_family == "M1":
        peak = peak_m1(q, diameter, t0, peak_params)
    else:
        raise ValueError(f"未知峰值模型 {peak_family}")
    shape_family = protocol["shape_model"]
    shape_params = protocol["shape_parameters"]
    if shape_family == "S1":
        theta = shape_s1(xi, shape_params)
    elif shape_family == "S2":
        theta = shape_s2(xi, shape_params)
    elif shape_family == "S3":
        theta = shape_s3(xi, shape_params)
    else:
        raise ValueError(f"未知形状模型 {shape_family}")
    mean = peak * theta
    error = protocol["prediction_error"]
    sigma = math.sqrt(
        float(error["model_sigma_K"]) ** 2
        + (float(error["peak_grid_uncertainty_fraction"]) * mean) ** 2
        + float(error["minimum_absolute_sigma_K"]) ** 2
    )
    z90 = 1.6448536269514722
    interval = [max(0.0, mean - z90 * sigma), mean + z90 * sigma]
    return {
        "mean_deltaT_K": mean,
        "prediction_interval_K": interval,
        "status": "PASS",
    }
