"""直接反演的局部 Hessian、目标剖面、观测扰动和参数 Bootstrap。"""
from __future__ import annotations

import argparse
import math
import random
import statistics
import sys
from pathlib import Path


STAGE5_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STAGE5_ROOT / "src"))

import direct_inversion  # noqa: E402
import fisher_layout  # noqa: E402
import tunnel_config as cfg  # noqa: E402


SYNTHETIC_LABEL = fisher_layout.SYNTHETIC_LABEL
NO_COVERAGE_CLAIM = "SOFTWARE_ONLY_NO_COVERAGE_OR_REAL_PRECISION_CLAIM"


def _finite(value, name):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} 必须有限")
    return value


def numerical_hessian(function, eta, steps=(1e-3, 1e-3)):
    """二参数目标函数的对称中心差分 Hessian。"""
    if len(eta) != 2 or len(steps) != 2:
        raise ValueError("Hessian 当前要求两个参数")
    center = [_finite(value, "eta") for value in eta]
    h = [_finite(value, "step") for value in steps]
    if any(value <= 0 for value in h):
        raise ValueError("Hessian 差分步长必须为正")
    f0 = _finite(function(center), "objective")
    hessian = [[0.0, 0.0], [0.0, 0.0]]
    for i in range(2):
        plus, minus = list(center), list(center)
        plus[i] += h[i]
        minus[i] -= h[i]
        hessian[i][i] = (
            _finite(function(plus), "objective") - 2.0 * f0
            + _finite(function(minus), "objective")
        ) / (h[i] * h[i])
    pp = [center[0] + h[0], center[1] + h[1]]
    pm = [center[0] + h[0], center[1] - h[1]]
    mp = [center[0] - h[0], center[1] + h[1]]
    mm = [center[0] - h[0], center[1] - h[1]]
    cross = (
        _finite(function(pp), "objective") - _finite(function(pm), "objective")
        - _finite(function(mp), "objective") + _finite(function(mm), "objective")
    ) / (4.0 * h[0] * h[1])
    hessian[0][1] = hessian[1][0] = cross
    return hessian


def _inverse_spd_2x2(matrix):
    metrics = fisher_layout.fisher_metrics(matrix)
    if metrics["status"] != "PASS":
        raise ValueError("局部 Hessian 非正定，不能构造局部区间")
    a, b, d = float(matrix[0][0]), float(matrix[0][1]), float(matrix[1][1])
    determinant = a * d - b * b
    return [[d / determinant, -b / determinant], [-b / determinant, a / determinant]]


def gaussian_nll_eta(closure_model, scenario, sensor_xs, observed, eta,
                     sigma_C=2.0, correlation_length_m=7.5,
                     nugget_fraction=0.05):
    H = _finite(scenario.get("H", cfg.H), "H")
    Q_MW, x_f = math.exp(_finite(eta[0], "logQ")), _finite(eta[1], "xf/H") * H
    return 0.5 * fisher_layout.correlated_objective(
        closure_model, scenario, sensor_xs, observed, Q_MW, x_f,
        sigma_C, correlation_length_m, nugget_fraction,
    )


def local_hessian_interval(closure_model, scenario, sensor_xs, observed,
                           Q_hat_MW, x_f_hat_m, sigma_C=2.0,
                           correlation_length_m=7.5, nugget_fraction=0.05,
                           z_value=1.959963984540054, steps=(1e-3, 1e-3)):
    H = _finite(scenario.get("H", cfg.H), "H")
    Q_hat = _finite(Q_hat_MW, "Q_hat_MW")
    x_hat = _finite(x_f_hat_m, "x_f_hat_m")
    z_value = _finite(z_value, "z_value")
    if Q_hat <= 0 or H <= 0 or z_value <= 0:
        raise ValueError("局部区间要求 Q/H/z 为正")
    eta_hat = [math.log(Q_hat), x_hat / H]

    def objective(eta):
        return gaussian_nll_eta(
            closure_model, scenario, sensor_xs, observed, eta, sigma_C,
            correlation_length_m, nugget_fraction,
        )

    hessian = numerical_hessian(objective, eta_hat, steps)
    covariance_eta = _inverse_spd_2x2(hessian)
    se_log_q = math.sqrt(covariance_eta[0][0])
    se_x_h = math.sqrt(covariance_eta[1][1])
    q_low = math.exp(eta_hat[0] - z_value * se_log_q)
    q_high = math.exp(eta_hat[0] + z_value * se_log_q)
    x_low = H * (eta_hat[1] - z_value * se_x_h)
    x_high = H * (eta_hat[1] + z_value * se_x_h)
    applicability = fisher_layout.validate_100m_scenario(scenario, sensor_xs)
    return {
        "eta_hat": eta_hat, "hessian": hessian, "covariance_eta": covariance_eta,
        "se_logQ": se_log_q, "se_xf_over_H": se_x_h,
        "Q_low_MW": q_low, "Q_high_MW": q_high, "Q_width_MW": q_high - q_low,
        "x_low_m": x_low, "x_high_m": x_high, "x_width_m": x_high - x_low,
        "method": "LOCAL_HESSIAN_NORMAL_ETA",
        **applicability,
    }


def profile_confidence_intervals(surface_rows, delta_chi2=3.841458820694124):
    """由 chi-square 目标面计算单参数剖面区间；默认 95%/1 自由度阈值。"""
    delta = _finite(delta_chi2, "delta_chi2")
    if delta <= 0:
        raise ValueError("剖面阈值必须为正")
    q_profile, x_profile = fisher_layout.profile_objective(surface_rows)
    minimum = min(float(row["objective"]) for row in surface_rows)
    threshold = minimum + delta

    def interval(rows, key):
        accepted = [row for row in rows if float(row["profile_objective"]) <= threshold]
        if not accepted:
            raise ValueError("目标剖面没有落在阈值内的网格点")
        all_values = [float(row[key]) for row in rows]
        accepted_values = [float(row[key]) for row in accepted]
        low, high = min(accepted_values), max(accepted_values)
        return {
            "low": low, "high": high, "width": high - low,
            "lower_grid_truncated": low == min(all_values),
            "upper_grid_truncated": high == max(all_values),
        }

    return {
        "minimum_chi2": minimum, "threshold_chi2": threshold,
        "delta_chi2": delta, "Q": interval(q_profile, "Q_MW"),
        "x_f": interval(x_profile, "x_f_m"),
        "Q_profile": q_profile, "x_profile": x_profile,
        "method": "PROFILE_CHI_SQUARE_GRID",
    }


def perturb_observations(observed, covariance, n_replicates, seed=20260729):
    if int(n_replicates) != n_replicates or n_replicates < 1:
        raise ValueError("观测扰动次数必须为正整数")
    observed = [_finite(value, "observed") for value in observed]
    factor = fisher_layout.cholesky_factor(covariance)
    if len(factor) != len(observed):
        raise ValueError("观测与协方差维数不一致")
    rng = random.Random(seed)
    replicates = []
    for _ in range(int(n_replicates)):
        standard = [rng.gauss(0.0, 1.0) for _ in observed]
        noise = [
            sum(factor[i][j] * standard[j] for j in range(i + 1))
            for i in range(len(observed))
        ]
        replicates.append([value + noise[index] for index, value in enumerate(observed)])
    return replicates


def correlated_direct_invert(closure_model, scenario, sensor_xs, observed,
                             Q_bounds=(5.0, 100.0), x_bounds=(15.0, 85.0),
                             sigma_C=2.0, correlation_length_m=7.5,
                             nugget_fraction=0.05, q_grid=18, x_grid=29,
                             n_starts=4, local_iterations=24):
    if q_grid < 3 or x_grid < 3 or n_starts < 1 or local_iterations < 1:
        raise ValueError("相关反演网格、多初值或迭代数无效")
    if not (0 < Q_bounds[0] < Q_bounds[1] and x_bounds[0] < x_bounds[1]):
        raise ValueError("相关反演边界无效")
    applicability = fisher_layout.validate_100m_scenario(scenario, sensor_xs)
    H = _finite(scenario.get("H", cfg.H), "H")
    log_lo, log_hi = math.log(Q_bounds[0]), math.log(Q_bounds[1])

    def evaluate(eta_q, eta_x):
        Q, x_f = math.exp(eta_q), eta_x * H
        if not (Q_bounds[0] <= Q <= Q_bounds[1] and x_bounds[0] <= x_f <= x_bounds[1]):
            return math.inf
        return gaussian_nll_eta(
            closure_model, scenario, sensor_xs, observed, [eta_q, eta_x],
            sigma_C, correlation_length_m, nugget_fraction,
        )

    coarse = []
    for qi in range(q_grid):
        eta_q = log_lo + (log_hi - log_lo) * qi / (q_grid - 1)
        for xi in range(x_grid):
            x_f = x_bounds[0] + (x_bounds[1] - x_bounds[0]) * xi / (x_grid - 1)
            coarse.append((evaluate(eta_q, x_f / H), eta_q, x_f / H))
    coarse.sort(key=lambda row: row[0])
    solutions = []
    for _, eta_q, eta_x in coarse[:n_starts]:
        step_q = (log_hi - log_lo) / (q_grid - 1)
        step_x = (x_bounds[1] - x_bounds[0]) / H / (x_grid - 1)
        best = evaluate(eta_q, eta_x)
        for _ in range(local_iterations):
            candidates = [
                (eta_q + dq, eta_x + dx)
                for dq, dx in ((step_q, 0.0), (-step_q, 0.0), (0.0, step_x), (0.0, -step_x))
            ]
            candidate_values = [(evaluate(q, x), q, x) for q, x in candidates]
            candidate_values.sort(key=lambda row: row[0])
            if candidate_values[0][0] < best:
                best, eta_q, eta_x = candidate_values[0]
            else:
                step_q *= 0.5
                step_x *= 0.5
        solutions.append({"nll": best, "Q_hat_MW": math.exp(eta_q), "x_f_hat_m": eta_x * H})
    solutions.sort(key=lambda row: row["nll"])
    return {**solutions[0], "local_solutions": solutions, **applicability}


def _quantile(values, probability):
    ordered = sorted(_finite(value, "bootstrap_value") for value in values)
    if not ordered or not 0 <= probability <= 1:
        raise ValueError("分位数输入无效")
    position = probability * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def parameter_bootstrap(closure_model, scenario, sensor_xs, observed,
                        n_boot=100, seed=20260729, alpha=0.05,
                        sigma_C=2.0, correlation_length_m=7.5,
                        nugget_fraction=0.05, inversion_options=None):
    if int(n_boot) != n_boot or n_boot < 2 or not 0 < alpha < 1:
        raise ValueError("Bootstrap 次数或 alpha 无效")
    inversion_options = dict(inversion_options or {})
    base = correlated_direct_invert(
        closure_model, scenario, sensor_xs, observed, sigma_C=sigma_C,
        correlation_length_m=correlation_length_m,
        nugget_fraction=nugget_fraction, **inversion_options,
    )
    fitted = direct_inversion.physics_forward(
        closure_model, base["Q_hat_MW"], base["x_f_hat_m"], scenario["U"],
        scenario["Df"], sensor_xs, scenario.get("H", cfg.H),
        scenario.get("T0_K", cfg.T_AMBIENT_K),
        scenario.get("chi_r", cfg.CHI_R_PRESET),
    )
    covariance = fisher_layout.exponential_covariance(
        sensor_xs, sigma_C, correlation_length_m, nugget_fraction
    )
    synthetic_observations = perturb_observations(fitted, covariance, int(n_boot), seed)
    estimates = []
    for index, replicate in enumerate(synthetic_observations):
        result = correlated_direct_invert(
            closure_model, scenario, sensor_xs, replicate, sigma_C=sigma_C,
            correlation_length_m=correlation_length_m,
            nugget_fraction=nugget_fraction, **inversion_options,
        )
        estimates.append({
            "replicate": index, "Q_hat_MW": result["Q_hat_MW"],
            "x_f_hat_m": result["x_f_hat_m"], "nll": result["nll"],
        })
    q_values = [row["Q_hat_MW"] for row in estimates]
    x_values = [row["x_f_hat_m"] for row in estimates]
    return {
        "base_fit": base, "estimates": estimates, "n_boot": len(estimates),
        "Q_low_MW": _quantile(q_values, alpha / 2),
        "Q_high_MW": _quantile(q_values, 1 - alpha / 2),
        "x_low_m": _quantile(x_values, alpha / 2),
        "x_high_m": _quantile(x_values, 1 - alpha / 2),
        "method": "PARAMETRIC_OBSERVATION_NOISE_BOOTSTRAP",
    }


def _synthetic_model():
    return {
        "family": "power_law", "epsilon": 1e-6,
        "coefficients": {
            output: [0.0, 0.0, 0.0, 0.0]
            for output in ("Pe_e", "Da_e", "Pi_S", "delta_over_H")
        },
    }


def _write_profile_ci_svg(profile, path):
    q_rows, x_rows = profile["Q_profile"], profile["x_profile"]

    def line(rows, key, x0, y0, width, height):
        xs = [float(row[key]) for row in rows]
        ys = [float(row["profile_objective"]) for row in rows]
        x_span = max(max(xs) - min(xs), 1e-12)
        y_cap = max(max(ys), profile["threshold_chi2"], 1e-12)
        return " ".join(
            f"{x0 + width * (x - min(xs)) / x_span:.2f},{y0 + height * (1 - min(y, y_cap) / y_cap):.2f}"
            for x, y in zip(xs, ys)
        ), y0 + height * (1 - profile["threshold_chi2"] / y_cap)

    q_line, q_threshold = line(q_rows, "Q_MW", 70, 65, 330, 190)
    x_line, x_threshold = line(x_rows, "x_f_m", 470, 65, 330, 190)
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="850" height="310" viewBox="0 0 850 310">
<rect width="850" height="310" fill="white"/>
<text x="425" y="25" text-anchor="middle" font-family="sans-serif" font-size="16">Synthetic profile confidence intervals — no coverage claim</text>
<rect x="70" y="65" width="330" height="190" fill="none" stroke="#777"/>
<polyline points="{q_line}" fill="none" stroke="#1f77b4" stroke-width="2"/><line x1="70" x2="400" y1="{q_threshold:.2f}" y2="{q_threshold:.2f}" stroke="#d62728" stroke-dasharray="5,4"/>
<text x="235" y="282" text-anchor="middle" font-family="sans-serif" font-size="13">Q (MW)</text>
<rect x="470" y="65" width="330" height="190" fill="none" stroke="#777"/>
<polyline points="{x_line}" fill="none" stroke="#1f77b4" stroke-width="2"/><line x1="470" x2="800" y1="{x_threshold:.2f}" y2="{x_threshold:.2f}" stroke="#d62728" stroke-dasharray="5,4"/>
<text x="635" y="282" text-anchor="middle" font-family="sans-serif" font-size="13">x_f (m)</text>
</svg>'''
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8")


def run_synthetic_software_check(output_dir):
    output_dir = Path(output_dir)
    model = _synthetic_model()
    catalog = fisher_layout.load_sensor_catalog()
    domain = {
        "L": 100.0, "W": cfg.W, "H": cfg.H, "dx": cfg.WORKING_GRID_DX,
        "protocol_version": direct_inversion.PROTOCOL_VERSION,
        "domain_censor_state": "none",
    }
    scenario_set = [
        {**domain, "scenario_id": "syn_left", "Q_MW": 15.0, "x_f": 38.0, "U": 0.8, "Df": 3.0},
        {**domain, "scenario_id": "syn_center", "Q_MW": 35.0, "x_f": 50.0, "U": 2.0, "Df": 5.0},
        {**domain, "scenario_id": "syn_right", "Q_MW": 80.0, "x_f": 62.0, "U": 4.0, "Df": 7.0},
    ]
    layouts = fisher_layout.greedy_layout_sequence(
        model, scenario_set, catalog, sizes=(4, 8, 12, 16), objective="balanced_logdet"
    )
    scenario = scenario_set[1]
    scaling_rows = []
    for n_sensors in (4, 8, 12, 16):
        sensor_xs = [row["x"] for row in layouts[n_sensors]]
        observed = direct_inversion.physics_forward(
            model, scenario["Q_MW"], scenario["x_f"], scenario["U"], scenario["Df"], sensor_xs
        )
        for sigma in (1.0, 2.0, 4.0):
            interval = local_hessian_interval(
                model, scenario, sensor_xs, observed, scenario["Q_MW"], scenario["x_f"],
                sigma_C=sigma,
            )
            scaling_rows.append({
                "n_sensors": n_sensors, "sigma_C": sigma,
                "Q_width_MW": interval["Q_width_MW"], "x_width_m": interval["x_width_m"],
                "Q_low_MW": interval["Q_low_MW"], "Q_high_MW": interval["Q_high_MW"],
                "x_low_m": interval["x_low_m"], "x_high_m": interval["x_high_m"],
                "evidence_label": SYNTHETIC_LABEL, "decision_status": NO_COVERAGE_CLAIM,
            })
    fisher_layout._write_csv(
        output_dir / "synthetic_local_ci_scaling.csv",
        ["n_sensors", "sigma_C", "Q_width_MW", "x_width_m", "Q_low_MW",
         "Q_high_MW", "x_low_m", "x_high_m", "evidence_label", "decision_status"],
        scaling_rows,
    )

    layout = layouts[8]
    sensor_xs = [row["x"] for row in layout]
    observed = direct_inversion.physics_forward(
        model, scenario["Q_MW"], scenario["x_f"], scenario["U"], scenario["Df"], sensor_xs
    )
    surface = fisher_layout.objective_surface(
        model, scenario, sensor_xs, observed,
        [25.0 + index for index in range(21)], [45.0 + 0.25 * index for index in range(41)],
        sigma_C=2.0,
    )
    profile = profile_confidence_intervals(surface)
    profile_summary = [{
        "method": profile["method"], "minimum_chi2": profile["minimum_chi2"],
        "threshold_chi2": profile["threshold_chi2"],
        "Q_low_MW": profile["Q"]["low"], "Q_high_MW": profile["Q"]["high"],
        "Q_grid_truncated": profile["Q"]["lower_grid_truncated"] or profile["Q"]["upper_grid_truncated"],
        "x_low_m": profile["x_f"]["low"], "x_high_m": profile["x_f"]["high"],
        "x_grid_truncated": profile["x_f"]["lower_grid_truncated"] or profile["x_f"]["upper_grid_truncated"],
        "evidence_label": SYNTHETIC_LABEL, "decision_status": NO_COVERAGE_CLAIM,
    }]
    fisher_layout._write_csv(
        output_dir / "synthetic_profile_ci.csv", list(profile_summary[0]), profile_summary
    )
    _write_profile_ci_svg(profile, output_dir / "synthetic_profile_ci.svg")

    bootstrap = parameter_bootstrap(
        model, scenario, sensor_xs, observed, n_boot=40, seed=20260729,
        sigma_C=2.0,
        inversion_options={"q_grid": 13, "x_grid": 21, "n_starts": 3, "local_iterations": 16},
    )
    bootstrap_rows = [
        {**row, "evidence_label": SYNTHETIC_LABEL, "decision_status": NO_COVERAGE_CLAIM}
        for row in bootstrap["estimates"]
    ]
    fisher_layout._write_csv(
        output_dir / "synthetic_parameter_bootstrap.csv",
        ["replicate", "Q_hat_MW", "x_f_hat_m", "nll", "evidence_label", "decision_status"],
        bootstrap_rows,
    )
    bootstrap_summary = [{
        "n_boot": bootstrap["n_boot"], "base_Q_hat_MW": bootstrap["base_fit"]["Q_hat_MW"],
        "base_x_f_hat_m": bootstrap["base_fit"]["x_f_hat_m"],
        "Q_low_MW": bootstrap["Q_low_MW"], "Q_high_MW": bootstrap["Q_high_MW"],
        "x_low_m": bootstrap["x_low_m"], "x_high_m": bootstrap["x_high_m"],
        "evidence_label": SYNTHETIC_LABEL, "decision_status": NO_COVERAGE_CLAIM,
    }]
    fisher_layout._write_csv(
        output_dir / "synthetic_bootstrap_summary.csv", list(bootstrap_summary[0]), bootstrap_summary
    )
    return {"scaling": scaling_rows, "profile": profile_summary[0], "bootstrap": bootstrap}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=STAGE5_ROOT / "03_置信区间")
    args = parser.parse_args(argv)
    result = run_synthetic_software_check(args.output_dir)
    print(
        f"wrote {len(result['scaling'])} local-CI scaling rows and "
        f"{result['bootstrap']['n_boot']} synthetic bootstrap estimates to {args.output_dir}"
    )
    print(NO_COVERAGE_CLAIM)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
