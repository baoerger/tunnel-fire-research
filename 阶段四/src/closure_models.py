"""带物理约束的闭合候选、分组交叉验证和两阶段拟合框架。"""
from __future__ import annotations

import ast
import math
import random
import statistics
from copy import deepcopy

import dimensionless_model


FAMILIES = ("power_law", "constrained_nonlinear", "response_surface", "additive_gam")
OUTPUTS = ("Pe_e", "Da_e", "Pi_S", "delta_over_H")


def softplus(value):
    value = float(value)
    if value > 30:
        return value
    if value < -30:
        return math.exp(value)
    return math.log1p(math.exp(value))


def inverse_softplus(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("inverse_softplus 输入必须为有限正数")
    if value > 30:
        return value
    return math.log(math.expm1(value))


def _inputs(row):
    q = float(row["Q_star"])
    fr = float(row["Fr"])
    d = float(row["Df_over_H"])
    if not all(math.isfinite(value) for value in (q, fr, d)) or q <= 0 or fr < 0 or d <= 0:
        raise ValueError("Q_star/Df_over_H 必须为正且 Fr>=0")
    return q, fr, d


def basis(row, family):
    q, fr, d = _inputs(row)
    if family == "power_law":
        return [1.0, math.log(q), math.log1p(fr), math.log(d)]
    if family == "constrained_nonlinear":
        return [1.0, math.log(q), math.log1p(fr), math.log(d), math.sqrt(q), fr / (1 + fr), d]
    if family == "response_surface":
        return [1.0, q, fr, d, q * q, fr * fr, d * d, q * fr, q * d, fr * d]
    if family == "additive_gam":
        return [1.0, math.log(q), math.sqrt(q), q, fr, fr * fr,
                math.log(d), d, d * d]
    raise ValueError(f"未知闭合候选 family={family!r}")


def _solve(matrix, vector):
    n = len(vector)
    augmented = [list(matrix[index]) + [vector[index]] for index in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(augmented[row][col]))
        if abs(augmented[pivot][col]) < 1e-14:
            raise ValueError("闭合拟合矩阵奇异")
        augmented[col], augmented[pivot] = augmented[pivot], augmented[col]
        scale = augmented[col][col]
        augmented[col] = [value / scale for value in augmented[col]]
        for row in range(n):
            if row == col:
                continue
            factor = augmented[row][col]
            augmented[row] = [
                value - factor * reference
                for value, reference in zip(augmented[row], augmented[col])
            ]
    return [augmented[index][-1] for index in range(n)]


def _ridge_fit(features, targets, ridge):
    if not features or len(features) != len(targets):
        raise ValueError("闭合拟合样本为空或长度不一致")
    width = len(features[0])
    if any(len(row) != width for row in features):
        raise ValueError("闭合基函数宽度不一致")
    matrix = [[0.0] * width for _ in range(width)]
    vector = [0.0] * width
    for x, y in zip(features, targets):
        for i in range(width):
            vector[i] += x[i] * y
            for j in range(width):
                matrix[i][j] += x[i] * x[j]
    for index in range(width):
        matrix[index][index] += ridge
    return _solve(matrix, vector)


def _latent_target(row, output, epsilon):
    q, fr, _ = _inputs(row)
    value = float(row[output])
    if not math.isfinite(value):
        raise ValueError(f"{output} 目标非有限")
    if output == "Pe_e":
        if fr == 0:
            if abs(value) > 1e-8:
                raise ValueError("Fr=0 时 Pe_e 必须为 0")
            return None
        return inverse_softplus(value / fr)
    if output == "delta_over_H":
        if fr == 0:
            if abs(value) > 1e-8:
                raise ValueError("Fr=0 时 delta_over_H 必须为 0")
            return None
        return inverse_softplus(value / fr)
    if output == "Da_e":
        return inverse_softplus(value - epsilon)
    return inverse_softplus(value / q)


def fit_closure(rows, family="power_law", ridge=1e-8, epsilon=1e-6):
    if family not in FAMILIES or not rows or ridge <= 0 or epsilon <= 0:
        raise ValueError("闭合 family/rows/ridge/epsilon 无效")
    case_ids = [str(row.get("case_id") or "").strip() for row in rows]
    if any(not case_id for case_id in case_ids):
        raise ValueError("每个闭合样本必须有 case_id")
    coefficients = {}
    for output in OUTPUTS:
        features, targets = [], []
        for row in rows:
            latent = _latent_target(row, output, epsilon)
            if latent is None:
                continue
            features.append(basis(row, family))
            targets.append(latent)
        coefficients[output] = _ridge_fit(features, targets, ridge)
    return {"family": family, "epsilon": epsilon, "coefficients": coefficients}


def predict_closure(model, row):
    q, fr, _ = _inputs(row)
    features = basis(row, model["family"])
    latent = {
        output: sum(coefficient * value for coefficient, value in zip(
            model["coefficients"][output], features
        )) for output in OUTPUTS
    }
    return {
        "Pe_e": fr * softplus(latent["Pe_e"]),
        "Da_e": softplus(latent["Da_e"]) + model["epsilon"],
        "Pi_S": q * softplus(latent["Pi_S"]),
        "delta_over_H": fr * softplus(latent["delta_over_H"]),
    }


def grouped_cross_validation(rows, family="power_law", n_folds=5):
    groups = sorted({str(row["case_id"]) for row in rows})
    if not 2 <= n_folds <= len(groups):
        raise ValueError("n_folds 必须位于 [2,完整工况数]")
    fold_of = {group: index % n_folds for index, group in enumerate(groups)}
    fold_rows = []
    for fold in range(n_folds):
        train = [row for row in rows if fold_of[str(row["case_id"])] != fold]
        validation = [row for row in rows if fold_of[str(row["case_id"])] == fold]
        model = fit_closure(train, family)
        errors = {output: [] for output in OUTPUTS}
        for row in validation:
            prediction = predict_closure(model, row)
            for output in OUTPUTS:
                scale = max(abs(float(row[output])), 1e-8)
                errors[output].append(abs(prediction[output] - float(row[output])) / scale)
        fold_rows.append({
            "fold": fold,
            "train_groups": sorted({str(row["case_id"]) for row in train}),
            "validation_groups": sorted({str(row["case_id"]) for row in validation}),
            **{f"{output}_median_relative_error": statistics.median(values)
               for output, values in errors.items()},
        })
    return {
        "family": family, "folds": fold_rows,
        "median_relative_error": statistics.median(
            statistics.fmean(row[f"{output}_median_relative_error"] for output in OUTPUTS)
            for row in fold_rows
        ),
    }


def compare_candidates(rows, n_folds=5):
    results = [grouped_cross_validation(rows, family, n_folds) for family in FAMILIES]
    return sorted(results, key=lambda row: row["median_relative_error"])


def temperature_curve_loss(model, profiles):
    if not profiles:
        raise ValueError("全曲线拟合 profiles 为空")
    losses = []
    for profile in profiles:
        closure = predict_closure(model, profile)
        H = float(profile.get("H", 5.0))
        xp = float(profile["x_f"]) + closure["delta_over_H"] * H
        prediction = dimensionless_model.temperature_rise_profile(
            profile["x"], xp, H, float(profile.get("T0_K", 293.15)),
            closure["Pi_S"], closure["Pe_e"], closure["Da_e"],
        )
        observed = [float(value) for value in profile["dT"]]
        if len(observed) != len(prediction) or not observed:
            raise ValueError("全曲线观测与预测长度不一致")
        scale = max(max(abs(value) for value in observed), 1e-8)
        losses.extend(((estimate - truth) / scale) ** 2
                      for truth, estimate in zip(observed, prediction))
    return statistics.fmean(losses)


def two_stage_fit(parameter_rows, profiles, family="power_law",
                  passes=2, initial_step=0.05):
    model = fit_closure(parameter_rows, family)
    initial_loss = temperature_curve_loss(model, profiles)
    refined = deepcopy(model)
    best_loss = initial_loss
    step = initial_step
    for _ in range(passes):
        for output in OUTPUTS:
            for index in range(len(refined["coefficients"][output])):
                original = refined["coefficients"][output][index]
                best_value = original
                for candidate in (original - step, original + step):
                    refined["coefficients"][output][index] = candidate
                    loss = temperature_curve_loss(refined, profiles)
                    if loss < best_loss:
                        best_loss, best_value = loss, candidate
                refined["coefficients"][output][index] = best_value
        step *= 0.5
    return {
        "stage1_model": model, "final_model": refined,
        "stage1_temperature_loss": initial_loss, "final_temperature_loss": best_loss,
    }


def bootstrap_closure(rows, evaluation_rows, family="power_law", n_boot=30,
                      seed=20260729):
    if n_boot < 20 or not evaluation_rows:
        raise ValueError("n_boot 至少 20 且 evaluation_rows 非空")
    groups = sorted({str(row["case_id"]) for row in rows})
    by_group = {group: [row for row in rows if str(row["case_id"]) == group] for group in groups}
    rng = random.Random(seed)
    predictions = []
    for _ in range(n_boot):
        sampled = [rng.choice(groups) for _ in groups]
        training = [row for group in sampled for row in by_group[group]]
        model = fit_closure(training, family)
        predictions.append([predict_closure(model, row) for row in evaluation_rows])
    summaries = []
    for eval_index, row in enumerate(evaluation_rows):
        summary = {"evaluation_id": row.get("case_id", f"eval_{eval_index}")}
        for output in OUTPUTS:
            values = sorted(sample[eval_index][output] for sample in predictions)
            summary[f"{output}_median"] = statistics.median(values)
            summary[f"{output}_ci_low"] = values[int(0.025 * (len(values) - 1))]
            summary[f"{output}_ci_high"] = values[math.ceil(0.975 * (len(values) - 1))]
        summaries.append(summary)
    return {"n_boot": n_boot, "seed": seed, "summaries": summaries}


_ALLOWED_CALLS = {"log1p": math.log1p, "sqrt": math.sqrt, "softplus": softplus}
_ALLOWED_NAMES = {"Q_star", "Fr", "Df_over_H"}
_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Name, ast.Constant, ast.Call,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd, ast.Load,
)


def validate_symbolic_expression(expression, domain_rows, max_complexity=30):
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError("符号表达式语法错误") from exc
    nodes = list(ast.walk(tree))
    if len(nodes) > max_complexity or any(not isinstance(node, _ALLOWED_NODES) for node in nodes):
        raise ValueError("符号表达式超过复杂度或含禁用运算")
    for node in nodes:
        if isinstance(node, ast.Name) and node.id not in _ALLOWED_NAMES | set(_ALLOWED_CALLS):
            raise ValueError(f"符号表达式含禁用名称 {node.id}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_CALLS:
                raise ValueError("符号表达式含禁用函数")
        if isinstance(node, ast.Pow):
            if not isinstance(node.right, ast.Constant) or abs(float(node.right.value)) > 3:
                raise ValueError("幂指数必须是绝对值不超过 3 的常数")
    code = compile(tree, "<symbolic_closure>", "eval")
    if not domain_rows:
        raise ValueError("奇点检查域为空")
    for row in domain_rows:
        q, fr, d = _inputs(row)
        try:
            value = eval(code, {"__builtins__": {}}, {
                **_ALLOWED_CALLS, "Q_star": q, "Fr": fr, "Df_over_H": d,
            })
        except (ArithmeticError, ValueError) as exc:
            raise ValueError("符号表达式在研究域存在奇点或定义域错误") from exc
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("符号表达式在研究域产生非有限值")
    return {"expression": expression, "complexity": len(nodes), "status": "PASS"}

