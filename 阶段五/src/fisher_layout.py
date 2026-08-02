"""Fisher 信息、空间相关噪声、稳健布局与目标函数剖面工具。

本模块可以用明确标记的合成闭合验证软件流程；在真实闭合、正式数据库和
独立测试可用前，任何输出都不能解释为推荐传感器布局或可辨识性结论。
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path


STAGE5_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = STAGE5_ROOT.parent
STAGE1_ROOT = PROJECT_ROOT / "阶段一"
sys.path.insert(0, str(STAGE5_ROOT / "src"))

import direct_inversion  # noqa: E402
import tunnel_config as cfg  # noqa: E402


SYNTHETIC_LABEL = "SYNTHETIC_SOFTWARE_TEST_NOT_SCIENTIFIC_EVIDENCE"
NO_RECOMMENDATION = "SOFTWARE_ONLY_NO_LAYOUT_RECOMMENDATION"
DEFAULT_SENSOR_CSV = STAGE1_ROOT / "07_温度测点与输出规范" / "sensor_layout.csv"
DEFAULT_SUBSET_CSV = STAGE1_ROOT / "07_温度测点与输出规范" / "sparse_subsets.csv"


def _finite(value, name):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} 必须有限")
    return value


def validate_100m_scenario(scenario, sensor_xs):
    """评估 Fisher 场景范围，不阻断带标志的探索性外推。"""
    payload = dict(scenario)
    payload["x"] = list(sensor_xs)
    return direct_inversion.validate_100m_observation(payload)


def load_sensor_catalog(path=DEFAULT_SENSOR_CSV):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    sensors = []
    for row in rows:
        sensor_id = str(row.get("id") or "").strip()
        if not sensor_id:
            raise ValueError("测点目录存在空 ID")
        sensors.append({"id": sensor_id, "x": _finite(row.get("x"), f"{sensor_id}.x")})
    if not sensors or len({row["id"] for row in sensors}) != len(sensors):
        raise ValueError("测点目录为空或 ID 重复")
    if len({row["x"] for row in sensors}) != len(sensors):
        raise ValueError("测点目录存在重复 x 坐标")
    return sorted(sensors, key=lambda row: row["x"])


def load_uniform_layouts(path=DEFAULT_SUBSET_CSV, catalog=None):
    catalog = catalog or load_sensor_catalog()
    by_id = {row["id"]: row for row in catalog}
    layouts = {}
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("layout_type") != "uniform_default":
                continue
            n_sensors = int(row["n_sensors"])
            ids = [value.strip() for value in row["sensor_ids"].split(";") if value.strip()]
            if len(ids) != n_sensors or len(set(ids)) != n_sensors:
                raise ValueError(f"{n_sensors} 点均匀布局数量错误或重复")
            unknown = sorted(set(ids) - set(by_id))
            if unknown:
                raise ValueError(f"均匀布局引用未知测点: {unknown}")
            layouts[n_sensors] = [by_id[sensor_id] for sensor_id in ids]
    return layouts


def sensitivity_matrix(closure_model, scenario, sensor_xs, relative_step=1e-4):
    """对 eta=[log(Q), x_f/H] 作中心差分，返回每个测点的一行灵敏度。"""
    step = _finite(relative_step, "relative_step")
    if step <= 0 or not sensor_xs:
        raise ValueError("差分步长必须为正且测点不能为空")
    validate_100m_scenario(scenario, sensor_xs)
    Q = _finite(scenario["Q_MW"], "Q_MW")
    x_f = _finite(scenario["x_f"], "x_f")
    U = _finite(scenario["U"], "U")
    Df = _finite(scenario["Df"], "Df")
    H = _finite(scenario.get("H", cfg.H), "H")
    if Q <= 0 or U < 0 or Df <= 0 or H <= 0:
        raise ValueError("灵敏度场景要求 Q/Df/H>0 且 U>=0")

    common = (closure_model, U, Df, sensor_xs, H,
              scenario.get("T0_K", cfg.T_AMBIENT_K),
              scenario.get("chi_r", cfg.CHI_R_PRESET))
    q_plus = direct_inversion.physics_forward(common[0], Q * math.exp(step), x_f, *common[1:])
    q_minus = direct_inversion.physics_forward(common[0], Q * math.exp(-step), x_f, *common[1:])
    x_step_m = step * H
    x_plus = direct_inversion.physics_forward(common[0], Q, x_f + x_step_m, *common[1:])
    x_minus = direct_inversion.physics_forward(common[0], Q, x_f - x_step_m, *common[1:])
    jacobian = [
        [(qp - qm) / (2.0 * step), (xp - xm) / (2.0 * step)]
        for qp, qm, xp, xm in zip(q_plus, q_minus, x_plus, x_minus)
    ]
    if any(not math.isfinite(value) for row in jacobian for value in row):
        raise ValueError("灵敏度含非有限值")
    return jacobian


def _cholesky(matrix):
    n = len(matrix)
    if n == 0 or any(len(row) != n for row in matrix):
        raise ValueError("协方差必须为非空方阵")
    scale = max(abs(float(matrix[i][i])) for i in range(n))
    if scale <= 0:
        raise ValueError("协方差对角线必须为正")
    factor = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            if abs(float(matrix[i][j]) - float(matrix[j][i])) > 1e-10 * scale:
                raise ValueError("协方差必须对称")
            value = float(matrix[i][j]) - sum(factor[i][k] * factor[j][k] for k in range(j))
            if i == j:
                if value <= 1e-12 * scale:
                    raise ValueError("协方差不是数值正定矩阵")
                factor[i][j] = math.sqrt(value)
            else:
                factor[i][j] = value / factor[j][j]
    return factor


def solve_spd(matrix, vector):
    """用 Cholesky 分解稳定求解对称正定小矩阵。"""
    factor = _cholesky(matrix)
    n = len(factor)
    if len(vector) != n:
        raise ValueError("线性方程右端长度不一致")
    y = [0.0] * n
    for i in range(n):
        y[i] = (float(vector[i]) - sum(factor[i][j] * y[j] for j in range(i))) / factor[i][i]
    result = [0.0] * n
    for i in range(n - 1, -1, -1):
        result[i] = (
            y[i] - sum(factor[j][i] * result[j] for j in range(i + 1, n))
        ) / factor[i][i]
    return result


def cholesky_factor(matrix):
    """返回对称正定矩阵的下三角 Cholesky 因子。"""
    return _cholesky(matrix)


def exponential_covariance(sensor_xs, sigma_C=1.0, correlation_length_m=5.0,
                           nugget_fraction=0.05):
    """指数空间相关协方差，sigma_C 表示每点总标准差。"""
    xs = [_finite(value, "sensor_x") for value in sensor_xs]
    sigma = _finite(sigma_C, "sigma_C")
    length = _finite(correlation_length_m, "correlation_length_m")
    nugget = _finite(nugget_fraction, "nugget_fraction")
    if not xs or len(set(xs)) != len(xs):
        raise ValueError("协方差测点不能为空或重复")
    if sigma <= 0 or length <= 0 or not 0 <= nugget < 1:
        raise ValueError("sigma/相关长度必须为正且 nugget 位于 [0,1)")
    variance = sigma * sigma
    covariance = []
    for i, xi in enumerate(xs):
        row = []
        for j, xj in enumerate(xs):
            correlated = (1.0 - nugget) * math.exp(-abs(xi - xj) / length)
            row.append(variance * (correlated + (nugget if i == j else 0.0)))
        covariance.append(row)
    _cholesky(covariance)
    return covariance


def fisher_information(jacobian, covariance):
    if not jacobian or any(len(row) != 2 for row in jacobian):
        raise ValueError("灵敏度矩阵必须为 n×2")
    if len(covariance) != len(jacobian):
        raise ValueError("灵敏度与协方差维数不一致")
    solved_columns = [
        solve_spd(covariance, [row[column] for row in jacobian])
        for column in range(2)
    ]
    information = [[0.0, 0.0], [0.0, 0.0]]
    for i in range(2):
        for j in range(2):
            information[i][j] = sum(
                row[i] * solved_columns[j][index]
                for index, row in enumerate(jacobian)
            )
    off_diagonal = 0.5 * (information[0][1] + information[1][0])
    information[0][1] = information[1][0] = off_diagonal
    return information


def fisher_metrics(information):
    if len(information) != 2 or any(len(row) != 2 for row in information):
        raise ValueError("Fisher 矩阵必须为 2×2")
    a, b, d = (float(information[0][0]), float(information[0][1]),
               float(information[1][1]))
    if not all(math.isfinite(value) for value in (a, b, d)):
        raise ValueError("Fisher 矩阵含非有限值")
    discriminant = math.sqrt(max(0.0, (a - d) ** 2 + 4.0 * b * b))
    eig_max = 0.5 * (a + d + discriminant)
    eig_min = 0.5 * (a + d - discriminant)
    determinant = a * d - b * b
    determinant_scale = max(abs(a * d), b * b, 1.0)
    if determinant < 0 and abs(determinant) <= 1e-12 * determinant_scale:
        determinant = 0.0
    if eig_min < 0 and abs(eig_min) <= 1e-12 * max(abs(eig_max), 1.0):
        eig_min = 0.0
    if eig_min <= 0 or determinant <= 0:
        return {
            "det": determinant, "logdet": -math.inf, "cond": math.inf,
            "trace_inverse": math.inf, "eigen_min": eig_min,
            "eigen_max": eig_max, "status": "SINGULAR",
        }
    return {
        "det": determinant, "logdet": math.log(determinant),
        "cond": eig_max / eig_min, "trace_inverse": (a + d) / determinant,
        "eigen_min": eig_min, "eigen_max": eig_max, "status": "PASS",
    }


def _scenario_id(scenario, index):
    return str(scenario.get("scenario_id") or f"scenario_{index + 1:03d}")


def precompute_sensitivities(closure_model, scenarios, catalog, relative_step=1e-4):
    xs = [row["x"] for row in catalog]
    cache = {}
    for index, scenario in enumerate(scenarios):
        scenario_id = _scenario_id(scenario, index)
        if scenario_id in cache:
            raise ValueError("Fisher 场景 ID 重复")
        matrix = sensitivity_matrix(closure_model, scenario, xs, relative_step)
        cache[scenario_id] = {
            sensor["id"]: matrix[row_index]
            for row_index, sensor in enumerate(catalog)
        }
    return cache


def aggregate_layout_metrics(closure_model, scenarios, layout, sigma_C=2.0,
                             correlation_length_m=7.5, nugget_fraction=0.05,
                             sensitivity_cache=None):
    if not scenarios or len(layout) < 2:
        raise ValueError("Fisher 汇总至少需要一个场景和两个测点")
    if len({row["id"] for row in layout}) != len(layout):
        raise ValueError("布局存在重复测点")
    scenario_rows = []
    for index, scenario in enumerate(scenarios):
        scenario_id = _scenario_id(scenario, index)
        if sensitivity_cache is None:
            jacobian = sensitivity_matrix(
                closure_model, scenario, [row["x"] for row in layout]
            )
        else:
            jacobian = [sensitivity_cache[scenario_id][row["id"]] for row in layout]
        covariance = exponential_covariance(
            [row["x"] for row in layout], sigma_C,
            correlation_length_m, nugget_fraction,
        )
        information = fisher_information(jacobian, covariance)
        metrics = fisher_metrics(information)
        scenario_rows.append({"scenario_id": scenario_id, "information": information, **metrics})
    finite = [row for row in scenario_rows if row["status"] == "PASS"]
    if len(finite) != len(scenario_rows):
        average_logdet = worst_logdet = -math.inf
    else:
        average_logdet = sum(row["logdet"] for row in finite) / len(finite)
        worst_logdet = min(row["logdet"] for row in finite)
    return {
        "n_scenarios": len(scenario_rows), "scenario_ids": [row["scenario_id"] for row in scenario_rows],
        "n_singular_scenarios": len(scenario_rows) - len(finite),
        "average_det": sum(row["det"] for row in scenario_rows) / len(scenario_rows),
        "worst_det": min(row["det"] for row in scenario_rows),
        "average_logdet": average_logdet, "worst_logdet": worst_logdet,
        "worst_cond": max(row["cond"] for row in scenario_rows),
        "average_trace_inverse": sum(row["trace_inverse"] for row in scenario_rows) / len(scenario_rows),
        "scenario_metrics": scenario_rows,
    }


def _evenly_spaced(records, count):
    if not 1 <= count <= len(records):
        raise ValueError("均匀抽取数量无效")
    if count == 1:
        return [records[len(records) // 2]]
    indices = [round(index * (len(records) - 1) / (count - 1)) for index in range(count)]
    return [records[index] for index in indices]


def representative_layouts(catalog, n_sensors, uniform_layout=None, reference_x=50.0):
    """构造均匀、两侧、单侧不利和强风下游偏置代表布局。"""
    if n_sensors not in (4, 8, 12, 16) or n_sensors > len(catalog):
        raise ValueError("代表布局仅支持 4/8/12/16 个测点")
    ordered = sorted(catalog, key=lambda row: row["x"])
    reference_x = _finite(reference_x, "reference_x")
    if uniform_layout is None:
        uniform_layout = _evenly_spaced(ordered, n_sensors)
    left = [row for row in ordered if row["x"] < reference_x]
    right = [row for row in ordered if row["x"] > reference_x]
    half = n_sensors // 2
    bilateral = sorted(left[-half:] + right[:half], key=lambda row: row["x"])

    downstream_pool = [row for row in ordered if row["x"] >= reference_x]
    upstream_pool = [row for row in ordered if row["x"] < reference_x]
    n_down = min(len(downstream_pool), math.ceil(0.75 * n_sensors))
    n_up = n_sensors - n_down
    downstream = sorted(
        _evenly_spaced(upstream_pool, n_up) + _evenly_spaced(downstream_pool, n_down),
        key=lambda row: row["x"],
    ) if n_up else _evenly_spaced(downstream_pool, n_down)
    layouts = {
        "uniform_default": list(uniform_layout),
        "bilateral_near_source": bilateral,
        "upstream_one_sided_adverse": ordered[:n_sensors],
        "downstream_bias": downstream,
    }
    for name, layout in layouts.items():
        if len(layout) != n_sensors or len({row["id"] for row in layout}) != n_sensors:
            raise ValueError(f"{name} 布局数量错误或存在重复")
    return layouts


def _layout_score(metrics, objective):
    if objective == "average_logdet":
        return metrics["average_logdet"]
    if objective == "worst_logdet":
        return metrics["worst_logdet"]
    if objective == "balanced_logdet":
        return 0.5 * (metrics["average_logdet"] + metrics["worst_logdet"])
    raise ValueError(f"未知布局目标 {objective!r}")


def greedy_layout_sequence(closure_model, scenarios, catalog, sizes=(4, 8, 12, 16),
                           objective="balanced_logdet", sigma_C=2.0,
                           correlation_length_m=7.5, nugget_fraction=0.05):
    """以同一条贪心序列生成嵌套布局，避免不同数量间不可比。"""
    sizes = tuple(sorted(set(int(value) for value in sizes)))
    if not sizes or sizes[0] < 2 or sizes[-1] > len(catalog):
        raise ValueError("优化布局数量范围无效")
    cache = precompute_sensitivities(closure_model, scenarios, catalog)

    def evaluate(layout):
        metrics = aggregate_layout_metrics(
            closure_model, scenarios, layout, sigma_C, correlation_length_m,
            nugget_fraction, cache,
        )
        return _layout_score(metrics, objective), metrics

    best_pair = None
    best_key = None
    for first in range(len(catalog) - 1):
        for second in range(first + 1, len(catalog)):
            pair = [catalog[first], catalog[second]]
            score, _ = evaluate(pair)
            key = (score, tuple(sorted(row["id"] for row in pair)))
            if math.isfinite(score) and (best_key is None or key > best_key):
                best_pair, best_key = pair, key
    if best_pair is None:
        raise ValueError("所有候选测点对的 Fisher 信息均奇异")

    selected = list(best_pair)
    snapshots = {}
    while len(selected) < sizes[-1]:
        best_candidate = None
        best_key = None
        for candidate in catalog:
            if candidate["id"] in {row["id"] for row in selected}:
                continue
            trial = selected + [candidate]
            score, _ = evaluate(trial)
            key = (score, candidate["id"])
            if math.isfinite(score) and (best_key is None or key > best_key):
                best_candidate, best_key = candidate, key
        if best_candidate is None:
            raise ValueError("布局贪心扩展失败")
        selected.append(best_candidate)
        if len(selected) in sizes:
            snapshots[len(selected)] = sorted(selected, key=lambda row: row["x"])
    if len(best_pair) in sizes:
        snapshots[len(best_pair)] = sorted(best_pair, key=lambda row: row["x"])
    return {size: snapshots[size] for size in sizes}


def correlated_objective(closure_model, scenario, sensor_xs, observed, Q_MW, x_f,
                         sigma_C=2.0, correlation_length_m=7.5,
                         nugget_fraction=0.05):
    validate_100m_scenario(scenario, sensor_xs)
    candidate_x = _finite(x_f, "x_f")
    predicted = direct_inversion.physics_forward(
        closure_model, Q_MW, candidate_x, scenario["U"], scenario["Df"], sensor_xs,
        scenario.get("H", cfg.H), scenario.get("T0_K", cfg.T_AMBIENT_K),
        scenario.get("chi_r", cfg.CHI_R_PRESET),
    )
    if len(observed) != len(predicted):
        raise ValueError("目标函数观测长度不一致")
    covariance = exponential_covariance(
        sensor_xs, sigma_C, correlation_length_m, nugget_fraction
    )
    residual = [float(y) - float(yhat) for y, yhat in zip(observed, predicted)]
    solved = solve_spd(covariance, residual)
    return sum(value * solved[index] for index, value in enumerate(residual))


def objective_surface(closure_model, scenario, sensor_xs, observed, q_values, x_values,
                      sigma_C=2.0, correlation_length_m=7.5,
                      nugget_fraction=0.05):
    q_values = [_finite(value, "Q_grid") for value in q_values]
    x_values = [_finite(value, "x_grid") for value in x_values]
    if not q_values or not x_values or any(value <= 0 for value in q_values):
        raise ValueError("目标函数网格无效")
    rows = []
    for Q_MW in q_values:
        for x_f in x_values:
            rows.append({
                "Q_MW": Q_MW, "x_f_m": x_f,
                "objective": correlated_objective(
                    closure_model, scenario, sensor_xs, observed, Q_MW, x_f,
                    sigma_C, correlation_length_m, nugget_fraction,
                ),
            })
    return rows


def profile_objective(surface_rows):
    if not surface_rows:
        raise ValueError("目标函数面为空")
    by_q, by_x = {}, {}
    for row in surface_rows:
        by_q.setdefault(float(row["Q_MW"]), []).append(row)
        by_x.setdefault(float(row["x_f_m"]), []).append(row)
    q_profile = [
        {"Q_MW": Q, "profile_objective": min(float(row["objective"]) for row in rows)}
        for Q, rows in sorted(by_q.items())
    ]
    x_profile = [
        {"x_f_m": x, "profile_objective": min(float(row["objective"]) for row in rows)}
        for x, rows in sorted(by_x.items())
    ]
    return q_profile, x_profile


def _write_csv(path, fieldnames, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _polyline(points, x_key, y_key, x0, y0, width, height):
    xs = [float(row[x_key]) for row in points]
    ys = [float(row[y_key]) for row in points]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    x_span, y_span = max(x_max - x_min, 1e-12), max(y_max - y_min, 1e-12)
    return " ".join(
        f"{x0 + width * (x - x_min) / x_span:.2f},{y0 + height * (1 - (y - y_min) / y_span):.2f}"
        for x, y in zip(xs, ys)
    )


def write_objective_svg(surface_rows, q_profile, x_profile, path):
    """无额外绘图库输出二维目标热图及 Q/x 剖面。"""
    q_values = sorted({float(row["Q_MW"]) for row in surface_rows})
    x_values = sorted({float(row["x_f_m"]) for row in surface_rows})
    losses = sorted(float(row["objective"]) for row in surface_rows)
    cap = losses[min(len(losses) - 1, int(0.95 * (len(losses) - 1)))]
    cap = max(cap, 1e-12)
    lookup = {(float(row["Q_MW"]), float(row["x_f_m"])): float(row["objective"])
              for row in surface_rows}
    heat_x, heat_y, heat_w, heat_h = 65, 55, 390, 255
    cell_w, cell_h = heat_w / len(q_values), heat_h / len(x_values)
    rectangles = []
    for qi, Q in enumerate(q_values):
        for xi, x_f in enumerate(x_values):
            normalized = min(1.0, lookup[(Q, x_f)] / cap)
            red = int(255 * normalized)
            blue = int(255 * (1.0 - normalized))
            rectangles.append(
                f'<rect x="{heat_x + qi * cell_w:.2f}" y="{heat_y + (len(x_values)-1-xi) * cell_h:.2f}" '
                f'width="{cell_w + 0.2:.2f}" height="{cell_h + 0.2:.2f}" fill="rgb({red},70,{blue})"/>'
            )
    q_line = _polyline(q_profile, "Q_MW", "profile_objective", 525, 65, 315, 100)
    x_line = _polyline(x_profile, "x_f_m", "profile_objective", 525, 215, 315, 100)
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="880" height="360" viewBox="0 0 880 360">
<rect width="880" height="360" fill="white"/>
<text x="440" y="24" text-anchor="middle" font-family="sans-serif" font-size="16">Synthetic objective surface — not scientific evidence</text>
{''.join(rectangles)}
<rect x="{heat_x}" y="{heat_y}" width="{heat_w}" height="{heat_h}" fill="none" stroke="black"/>
<text x="260" y="340" text-anchor="middle" font-family="sans-serif" font-size="13">Q (MW)</text>
<text x="18" y="185" text-anchor="middle" transform="rotate(-90 18 185)" font-family="sans-serif" font-size="13">x_f (m)</text>
<rect x="525" y="65" width="315" height="100" fill="none" stroke="#777"/><polyline points="{q_line}" fill="none" stroke="#1f77b4" stroke-width="2"/>
<text x="682" y="55" text-anchor="middle" font-family="sans-serif" font-size="13">Profile over x_f</text>
<rect x="525" y="215" width="315" height="100" fill="none" stroke="#777"/><polyline points="{x_line}" fill="none" stroke="#d62728" stroke-width="2"/>
<text x="682" y="205" text-anchor="middle" font-family="sans-serif" font-size="13">Profile over Q</text>
</svg>'''
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8")


def _synthetic_model():
    return {
        "family": "power_law", "epsilon": 1e-6,
        "coefficients": {
            output: [0.0, 0.0, 0.0, 0.0]
            for output in ("Pe_e", "Da_e", "Pi_S", "delta_over_H")
        },
    }


def run_synthetic_software_check(output_dir):
    output_dir = Path(output_dir)
    catalog = load_sensor_catalog()
    uniform = load_uniform_layouts(catalog=catalog)
    model = _synthetic_model()
    domain = {
        "L": 100.0, "W": cfg.W, "H": cfg.H, "dx": cfg.WORKING_GRID_DX,
        "protocol_version": direct_inversion.PROTOCOL_VERSION,
        "domain_censor_state": "none",
    }
    scenarios = [
        {**domain, "scenario_id": "syn_low_left_weak", "Q_MW": 12.0, "x_f": 38.0, "U": 0.5, "Df": 3.0},
        {**domain, "scenario_id": "syn_mid_center", "Q_MW": 35.0, "x_f": 50.0, "U": 2.0, "Df": 5.0},
        {**domain, "scenario_id": "syn_high_right_strong", "Q_MW": 85.0, "x_f": 62.0, "U": 4.5, "Df": 7.0},
        {**domain, "scenario_id": "syn_high_left_strong", "Q_MW": 70.0, "x_f": 40.0, "U": 3.8, "Df": 5.5},
    ]
    optimized_average = greedy_layout_sequence(
        model, scenarios, catalog, objective="average_logdet"
    )
    optimized_worst = greedy_layout_sequence(
        model, scenarios, catalog, objective="worst_logdet"
    )
    layout_rows, metric_rows = [], []
    for n_sensors in (4, 8, 12, 16):
        layouts = representative_layouts(catalog, n_sensors, uniform[n_sensors])
        layouts["optimized_average_multiscenario"] = optimized_average[n_sensors]
        layouts["optimized_worst_multiscenario"] = optimized_worst[n_sensors]
        for layout_type, layout in layouts.items():
            metrics = aggregate_layout_metrics(model, scenarios, layout)
            layout_rows.append({
                "n_sensors": n_sensors, "layout_type": layout_type,
                "sensor_ids": ";".join(row["id"] for row in layout),
                "scenario_ids": ";".join(metrics["scenario_ids"]),
                "evidence_label": SYNTHETIC_LABEL, "decision_status": NO_RECOMMENDATION,
            })
            metric_rows.append({
                "n_sensors": n_sensors, "layout_type": layout_type,
                "average_det": f'{metrics["average_det"]:.10g}',
                "worst_det": f'{metrics["worst_det"]:.10g}',
                "average_logdet": f'{metrics["average_logdet"]:.10g}',
                "worst_logdet": f'{metrics["worst_logdet"]:.10g}',
                "worst_cond": f'{metrics["worst_cond"]:.10g}',
                "average_trace_inverse": f'{metrics["average_trace_inverse"]:.10g}',
                "n_scenarios": metrics["n_scenarios"],
                "n_singular_scenarios": metrics["n_singular_scenarios"],
                "evidence_label": SYNTHETIC_LABEL, "decision_status": NO_RECOMMENDATION,
            })
    _write_csv(
        output_dir / "synthetic_layouts.csv",
        ["n_sensors", "layout_type", "sensor_ids", "scenario_ids", "evidence_label", "decision_status"],
        layout_rows,
    )
    _write_csv(
        output_dir / "synthetic_fisher_metrics.csv",
        ["n_sensors", "layout_type", "average_det", "worst_det", "average_logdet",
         "worst_logdet", "worst_cond", "average_trace_inverse", "n_scenarios",
         "n_singular_scenarios", "evidence_label", "decision_status"], metric_rows,
    )

    reference = scenarios[1]
    reference_jacobian = sensitivity_matrix(model, reference, [row["x"] for row in catalog])
    _write_csv(
        output_dir / "synthetic_sensitivity.csv",
        ["scenario_id", "sensor_id", "x_m", "dT_dlogQ", "dT_dxf_over_H", "evidence_label"],
        [{
            "scenario_id": reference["scenario_id"], "sensor_id": sensor["id"],
            "x_m": sensor["x"], "dT_dlogQ": f"{jacobian[0]:.10g}",
            "dT_dxf_over_H": f"{jacobian[1]:.10g}", "evidence_label": SYNTHETIC_LABEL,
        } for sensor, jacobian in zip(catalog, reference_jacobian)],
    )

    surface_layout = uniform[8]
    sensor_xs = [row["x"] for row in surface_layout]
    observed = direct_inversion.physics_forward(
        model, reference["Q_MW"], reference["x_f"], reference["U"],
        reference["Df"], sensor_xs,
    )
    q_values = [20.0 + index for index in range(31)]
    x_values = [40.0 + 0.5 * index for index in range(41)]
    surface = objective_surface(model, reference, sensor_xs, observed, q_values, x_values)
    q_profile, x_profile = profile_objective(surface)
    for row in surface:
        row["evidence_label"] = SYNTHETIC_LABEL
    for row in q_profile + x_profile:
        row["evidence_label"] = SYNTHETIC_LABEL
    _write_csv(output_dir / "synthetic_objective_surface.csv",
               ["Q_MW", "x_f_m", "objective", "evidence_label"], surface)
    _write_csv(output_dir / "synthetic_profile_Q.csv",
               ["Q_MW", "profile_objective", "evidence_label"], q_profile)
    _write_csv(output_dir / "synthetic_profile_x.csv",
               ["x_f_m", "profile_objective", "evidence_label"], x_profile)
    write_objective_svg(surface, q_profile, x_profile, output_dir / "synthetic_objective_profiles.svg")
    return {"layouts": layout_rows, "metrics": metric_rows, "surface": surface}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path,
        default=STAGE5_ROOT / "02_Fisher信息与布局",
    )
    args = parser.parse_args(argv)
    result = run_synthetic_software_check(args.output_dir)
    print(
        f"wrote {len(result['layouts'])} synthetic layout checks and "
        f"{len(result['surface'])} objective-grid rows to {args.output_dir}"
    )
    print(NO_RECOMMENDATION)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
