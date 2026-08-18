"""统一 F_physics 与 Q/x_f 直接反演软件基线。"""
from __future__ import annotations

import math
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

from src.analysis import censoring_peak
from src.fds import tunnel_config as cfg
from . import closure_models, dimensionless_model


PROTOCOL_VERSION = "100M_CONDITIONAL_DOMAIN_V2"
DOMAIN_LENGTH_M = 100.0
MEASUREMENT_BOUNDS_M = (15.0, 85.0)
DOMAIN_CENSOR_STATES = {
    "none", "bilateral_identifiable", "upstream_censored",
    "downstream_domain_censored", "no_obvious_backflow",
}


def validate_100m_observation(observation, x_bounds=MEASUREMENT_BOUNDS_M,
                              require_in_domain=False):
    """评估适用域；默认允许带范围标志的探索性外推。"""
    missing_reasons, ood_reasons = [], []
    expected = {"L": DOMAIN_LENGTH_M, "W": cfg.W, "H": cfg.H, "dx": cfg.WORKING_GRID_DX}
    for name, target in expected.items():
        raw = observation.get(name)
        if raw is None or str(raw).strip() == "":
            missing_reasons.append(f"{name}_MISSING")
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            missing_reasons.append(f"{name}_INVALID")
            continue
        if not math.isfinite(value):
            missing_reasons.append(f"{name}_INVALID")
        elif not math.isclose(value, target, abs_tol=1e-9):
            ood_reasons.append(f"{name}_OUT_OF_CORE_DOMAIN")
    protocol = str(observation.get("protocol_version") or "").strip()
    if not protocol:
        missing_reasons.append("PROTOCOL_VERSION_MISSING")
    elif protocol != PROTOCOL_VERSION:
        ood_reasons.append("PROTOCOL_VERSION_DIFFERENT")
    lo, hi = MEASUREMENT_BOUNDS_M
    if x_bounds[0] < lo or x_bounds[1] > hi:
        ood_reasons.append("INVERSION_BOUNDS_OUTSIDE_CORE_15_85")
    xs = [float(value) for value in observation.get("x", ())]
    if not xs or any(not math.isfinite(value) for value in xs):
        raise ValueError("反演测点 x 为空或非有限")
    if any(value < lo or value > hi for value in xs):
        ood_reasons.append("SENSOR_X_OUTSIDE_CORE_15_85")
    source_x = observation.get("x_f")
    if source_x is not None and str(source_x).strip() != "":
        source_x = float(source_x)
        if not math.isfinite(source_x):
            raise ValueError("x_f 必须有限")
        if source_x < lo or source_x > hi:
            ood_reasons.append("SOURCE_X_OUTSIDE_CORE_15_85")
    state = str(observation.get("domain_censor_state") or "").strip()
    if not state:
        missing_reasons.append("DOMAIN_CENSOR_STATE_MISSING")
        state = "not_reported"
    elif state not in DOMAIN_CENSOR_STATES:
        missing_reasons.append("DOMAIN_CENSOR_STATE_UNRECOGNIZED")
    if missing_reasons:
        status = "APPLICABILITY_UNDETERMINED"
        evidence_scope = "EXPLORATORY_METADATA_INCOMPLETE"
    elif ood_reasons:
        status = "OOD_EXPLORATORY"
        evidence_scope = "EXPLORATORY_EXTENSION_NO_CORE_DOMAIN_VALIDATION_CLAIM"
    else:
        status = "IN_DOMAIN_100M_CONDITIONAL"
        evidence_scope = "CORE_DOMAIN_RESULT_PENDING_REMAINING_SCIENTIFIC_GATES"
    reasons = missing_reasons + ood_reasons
    if require_in_domain and status != "IN_DOMAIN_100M_CONDITIONAL":
        raise ValueError("正式条件域筛选未通过: " + ";".join(reasons))
    return {
        "protocol_version": protocol,
        "applicability_status": status,
        "applicability_reasons": ";".join(reasons),
        "evidence_scope": evidence_scope,
        "domain_censor_state": state,
        "decay_parameter_warning": (
            "K_D_NOT_IDENTIFIABLE_FROM_THIS_DOMAIN"
            if state == "downstream_domain_censored" else
            "K_U_NOT_IDENTIFIABLE_FROM_THIS_OBSERVATION"
            if state in {"upstream_censored", "no_obvious_backflow"} else "NONE"
        ),
    }


def physics_forward(closure_model, Q_MW, x_f, U, Df, sensor_xs,
                    H=cfg.H, T0_K=cfg.T_AMBIENT_K, chi_r=cfg.CHI_R_PRESET):
    values = [float(Q_MW), float(x_f), float(U), float(Df), float(H), float(T0_K)]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("F_physics 输入必须有限")
    if Q_MW <= 0 or U < 0 or Df <= 0 or H <= 0 or T0_K <= 0:
        raise ValueError("Q/Df/H/T0 必须为正且 U>=0")
    row = {
        "Q_star": dimensionless_model.q_convective_star(Q_MW, chi_r, H),
        "Fr": U / math.sqrt(cfg.G * H), "Df_over_H": Df / H,
    }
    closure = closure_models.predict_closure(closure_model, row)
    xp = x_f + closure["delta_over_H"] * H
    return dimensionless_model.temperature_rise_profile(
        sensor_xs, xp, H, T0_K, closure["Pi_S"], closure["Pe_e"], closure["Da_e"],
    )


def inverse_loss(observed, predicted, sigma_C=1.0, censor_threshold_C=None,
                 censor_mask=None, weights=None):
    if len(observed) != len(predicted) or not observed:
        raise ValueError("反演观测与预测长度不一致或为空")
    if weights is None:
        weights = [1.0] * len(observed)
    if len(weights) != len(observed) or any(float(value) <= 0 for value in weights):
        raise ValueError("反演权重长度不一致或非正")
    if censor_threshold_C is not None:
        result = censoring_peak.censored_gaussian_nll(
            observed, predicted, sigma_C, censor_threshold_C, censor_mask,
            reduction="sum",
        )
        return result["loss"] / sum(float(value) for value in weights)
    sigma_C = float(sigma_C)
    if sigma_C <= 0:
        raise ValueError("sigma_C 必须为正")
    return sum(
        float(weight) * ((float(truth) - float(estimate)) / sigma_C) ** 2
        for truth, estimate, weight in zip(observed, predicted, weights)
    ) / sum(float(value) for value in weights)


def _evaluate(closure_model, eta_log_q, eta_x_h, observation, bounds):
    Q = math.exp(eta_log_q)
    H = observation.get("H", cfg.H)
    x_f = eta_x_h * H
    if not (bounds[0][0] <= Q <= bounds[0][1] and bounds[1][0] <= x_f <= bounds[1][1]):
        return math.inf, None
    predicted = physics_forward(
        closure_model, Q, x_f, observation["U"], observation["Df"],
        observation["x"], H, observation.get("T0_K", cfg.T_AMBIENT_K),
        observation.get("chi_r", cfg.CHI_R_PRESET),
    )
    loss = inverse_loss(
        observation["dT"], predicted, observation.get("sigma_C", 1.0),
        observation.get("censor_threshold_C"), observation.get("censor_mask"),
        observation.get("weights"),
    )
    return loss, predicted


def direct_invert(closure_model, observation, Q_bounds=(5.0, 100.0),
                  x_bounds=(15.0, 85.0), q_grid=25, x_grid=36,
                  n_starts=5, local_iterations=30):
    if q_grid < 3 or x_grid < 3 or n_starts < 1 or local_iterations < 1:
        raise ValueError("反演网格、多初值和迭代数无效")
    if not (0 < Q_bounds[0] < Q_bounds[1] and x_bounds[0] < x_bounds[1]):
        raise ValueError("反演边界无效")
    applicability = validate_100m_observation(observation, x_bounds)
    H = float(observation.get("H", cfg.H))
    bounds = (Q_bounds, x_bounds)
    log_lo, log_hi = math.log(Q_bounds[0]), math.log(Q_bounds[1])
    coarse = []
    for qi in range(q_grid):
        eta_q = log_lo + (log_hi - log_lo) * qi / (q_grid - 1)
        for xi in range(x_grid):
            x_f = x_bounds[0] + (x_bounds[1] - x_bounds[0]) * xi / (x_grid - 1)
            loss, _ = _evaluate(closure_model, eta_q, x_f / H, observation, bounds)
            coarse.append((loss, eta_q, x_f / H))
    coarse.sort(key=lambda row: row[0])
    solutions = []
    for _, eta_q, eta_x in coarse[:n_starts]:
        step_q = (log_hi - log_lo) / (q_grid - 1)
        step_x = (x_bounds[1] - x_bounds[0]) / H / (x_grid - 1)
        best_loss, _ = _evaluate(closure_model, eta_q, eta_x, observation, bounds)
        for _ in range(local_iterations):
            candidates = [
                (eta_q + dq, eta_x + dx)
                for dq, dx in ((step_q, 0), (-step_q, 0), (0, step_x), (0, -step_x))
            ]
            improved = False
            for candidate_q, candidate_x in candidates:
                loss, _ = _evaluate(
                    closure_model, candidate_q, candidate_x, observation, bounds
                )
                if loss < best_loss:
                    best_loss, eta_q, eta_x, improved = loss, candidate_q, candidate_x, True
            if not improved:
                step_q *= 0.5
                step_x *= 0.5
        final_loss, prediction = _evaluate(closure_model, eta_q, eta_x, observation, bounds)
        solutions.append({
            "Q_hat_MW": math.exp(eta_q), "x_f_hat_m": eta_x * H,
            "loss": final_loss, "prediction": prediction,
        })
    solutions.sort(key=lambda row: row["loss"])
    best = dict(solutions[0])
    residuals = [float(y) - float(yhat) for y, yhat in zip(observation["dT"], best["prediction"])]
    best.update({
        "rmse_C": math.sqrt(sum(value * value for value in residuals) / len(residuals)),
        "n_local_solutions": len(solutions), "local_solutions": solutions,
        **applicability,
    })
    return best
