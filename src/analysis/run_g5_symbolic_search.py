"""Plan and orchestrate the approved G5-SR raw symbolic searches.

Formal execution always passes :mod:`src.analysis.g5_sr_protocol` first.  This
module only generates raw Pareto candidates and complete trial provenance; it
does not select a formula, open the lockbox, or create A_SR/A_active.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from src.analysis import g5_sr_protocol as protocol_guard
from src.fds.project_paths import PROJECT_ROOT


TRIAL_COLUMNS = (
    "trial_id", "campaign_id", "search_phase", "variables_json",
    "stage", "task", "outer_fold_index",
    "outer_holdout_parent_case_id", "inner_fold_index",
    "train_parent_case_ids_json", "validation_parent_case_ids_json",
    "test_parent_case_ids_json", "seed", "status", "error_type",
    "error_message", "started_at_utc", "elapsed_s", "n_train_rows",
    "n_train_parent_groups", "candidate_count", "runner_metadata_json",
    "machine_protocol_sha256", "execution_supplement_sha256",
    "shape_expansion_decision_sha256",
)
CANDIDATE_COLUMNS = (
    "trial_id", "candidate_rank", "campaign_id", "search_phase",
    "variables_json", "stage", "task", "outer_fold_index",
    "outer_holdout_parent_case_id", "inner_fold_index", "seed",
    "raw_expression", "complexity", "loss", "score", "raw_candidate_json",
    "machine_protocol_sha256", "execution_supplement_sha256",
    "shape_expansion_decision_sha256",
)
TERMINAL_TRIAL_STATUSES = frozenset({
    "SUCCESS",
    "FAILED",
    "TIMEOUT",
    "FAILED_NO_CANDIDATES",
    "FAILED_INTERRUPTED",
})
TRIAL_IDENTITY_COLUMNS = (
    "trial_id",
    "campaign_id",
    "search_phase",
    "variables_json",
    "stage",
    "task",
    "outer_fold_index",
    "outer_holdout_parent_case_id",
    "inner_fold_index",
    "train_parent_case_ids_json",
    "validation_parent_case_ids_json",
    "test_parent_case_ids_json",
    "seed",
    "n_train_parent_groups",
    "machine_protocol_sha256",
    "execution_supplement_sha256",
    "shape_expansion_decision_sha256",
)

INITIAL_SEARCH_PHASE = "INITIAL"
CONDITIONAL_SEARCH_PHASE = "CONDITIONAL"
INITIAL_CAMPAIGNS = ("peak_q_d", "shape_xi_only")
CONDITIONAL_CAMPAIGN = "shape_xi_q_d"
KNOWN_CAMPAIGNS = frozenset((*INITIAL_CAMPAIGNS, CONDITIONAL_CAMPAIGN))


class G5SRResumeError(ValueError):
    """Raised when existing formal-search outputs are unsafe to resume."""


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _read_csv(path):
    rows, _ = _read_csv_with_columns(path)
    return rows


def _read_csv_with_columns(path):
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        columns = tuple(reader.fieldnames or ())
        return [dict(row) for row in reader], columns


def _write_csv_atomic(path, rows, columns):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows({key: row.get(key, "") for key in columns} for row in rows)
    temporary.replace(path)


def _json_list(values):
    return json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))


def _rotate(values, offset):
    values = list(values)
    if not values:
        return values
    offset %= len(values)
    return values[offset:] + values[:offset]


def build_fold_structure(parent_case_ids, machine):
    """Return deterministic outer/inner grouped folds from the machine contract."""
    parents = sorted({str(value) for value in parent_case_ids})
    budget = machine["search_budget"]
    outer_count = int(budget["outer_folds"]["count"])
    inner_count = int(budget["inner_folds"]["count"])
    if len(parents) != outer_count:
        raise ValueError(
            f"父组数与外层折数不一致: parents={len(parents)}, outer={outer_count}"
        )
    if (outer_count - 1) % inner_count != 0:
        raise ValueError("当前冻结分组要求每个内层验证折含相同数量的父组")

    folds = []
    for outer_index, holdout in enumerate(parents):
        outer_train = [parent for parent in parents if parent != holdout]
        rotated = _rotate(outer_train, outer_index)
        group_size = len(rotated) // inner_count
        inner = []
        for inner_index in range(inner_count):
            validation = sorted(
                rotated[inner_index * group_size:(inner_index + 1) * group_size]
            )
            training = sorted(set(outer_train) - set(validation))
            if set(training) & set(validation) or holdout in training or holdout in validation:
                raise AssertionError("父组在嵌套折之间泄漏")
            if set(training) | set(validation) != set(outer_train):
                raise AssertionError("内层训练/验证父组未完整覆盖外层训练集")
            inner.append({
                "inner_fold_index": inner_index,
                "train_parent_case_ids": training,
                "validation_parent_case_ids": validation,
            })
        validation_union = [
            parent for fold in inner for parent in fold["validation_parent_case_ids"]
        ]
        if sorted(validation_union) != sorted(outer_train):
            raise AssertionError("一个外层折内的内层验证父组不是恰好覆盖一次")
        folds.append({
            "outer_fold_index": outer_index,
            "outer_holdout_parent_case_id": holdout,
            "outer_train_parent_case_ids": sorted(outer_train),
            "inner_folds": inner,
        })
    return folds


def _campaign_specs(machine, supplement=None):
    """Return the frozen campaign/task/variable mapping.

    Formal execution always supplies the approved supplement.  The fallback is
    intentionally limited to pure planning/tests and mirrors the same frozen
    sequence; it never authorizes execution.
    """
    peak_variables = list(machine["tasks"]["peak"]["variables"])
    shape_variables = list(machine["tasks"]["shape"]["variables"])
    phase_1 = {}
    phase_2 = {}
    initial_ids = list(INITIAL_CAMPAIGNS)
    conditional_id = CONDITIONAL_CAMPAIGN
    if supplement is not None:
        sequence = supplement.get("shape_search_sequence", {})
        phase_1 = sequence.get("phase_1", {})
        phase_2 = sequence.get("phase_2", {})
        budget = supplement.get("trial_budget", {})
        initial_ids = list(budget.get("initial_campaigns", []))
        conditional_id = budget.get("conditional_campaign")
        if initial_ids != list(INITIAL_CAMPAIGNS):
            raise protocol_guard.G5SRProtocolError(
                "执行补充协议 initial_campaigns 必须依次为 peak_q_d、shape_xi_only"
            )
        if conditional_id != CONDITIONAL_CAMPAIGN:
            raise protocol_guard.G5SRProtocolError(
                "执行补充协议 conditional_campaign 必须为 shape_xi_q_d"
            )

    xi_only_id = phase_1.get("campaign_id", "shape_xi_only")
    xi_only_variables = list(phase_1.get("variables", ["xi"]))
    expanded_id = phase_2.get("campaign_id", "shape_xi_q_d")
    expanded_variables = list(phase_2.get("variables", shape_variables))
    if peak_variables != ["q", "d"]:
        raise protocol_guard.G5SRProtocolError(
            "peak_q_d 的冻结变量必须严格为 ['q', 'd']"
        )
    if xi_only_id != "shape_xi_only" or xi_only_variables != ["xi"]:
        raise protocol_guard.G5SRProtocolError(
            "shape phase_1 必须严格为 shape_xi_only 且仅使用 xi"
        )
    if expanded_id != "shape_xi_q_d" or expanded_variables != ["xi", "q", "d"]:
        raise protocol_guard.G5SRProtocolError(
            "shape phase_2 必须严格为 shape_xi_q_d 且变量为 xi,q,d"
        )
    return {
        "peak_q_d": {
            "campaign_id": "peak_q_d", "task": "peak",
            "search_phase": INITIAL_SEARCH_PHASE, "variables": peak_variables,
        },
        "shape_xi_only": {
            "campaign_id": "shape_xi_only", "task": "shape",
            "search_phase": INITIAL_SEARCH_PHASE, "variables": xi_only_variables,
        },
        "shape_xi_q_d": {
            "campaign_id": "shape_xi_q_d", "task": "shape",
            "search_phase": CONDITIONAL_SEARCH_PHASE,
            "variables": expanded_variables,
        },
    }


def _trials_per_campaign(machine):
    budget = machine["search_budget"]
    outer_count = int(budget["outer_folds"]["count"])
    inner_count = int(budget["inner_folds"]["count"])
    outer_seeds = len(budget["outer_search_seeds"])
    final_seeds = len(budget["final_search_seeds"])
    return outer_count * (inner_count + 1) * outer_seeds + final_seeds


def _validate_campaign_budget(machine, supplement, phase, count):
    if supplement is None:
        return
    budget = supplement.get("trial_budget", {})
    per_campaign = _trials_per_campaign(machine)
    if phase == INITIAL_SEARCH_PHASE:
        expected = int(budget.get("initial_trial_count", -1))
        if count != expected or expected != 2 * per_campaign:
            raise protocol_guard.G5SRProtocolError(
                "执行补充协议初始 campaign 预算与机器搜索预算不一致"
            )
    elif phase == CONDITIONAL_SEARCH_PHASE:
        expected = int(budget.get("conditional_additional_trial_count", -1))
        maximum = int(budget.get("maximum_main_search_trial_count", -1))
        if count != expected or expected != per_campaign:
            raise protocol_guard.G5SRProtocolError(
                "执行补充协议条件 campaign 预算与机器搜索预算不一致"
            )
        if 2 * per_campaign + count > maximum or maximum != 3 * per_campaign:
            raise protocol_guard.G5SRProtocolError(
                "执行补充协议主搜索总上限与 735-trial 状态机不一致"
            )


def build_search_plan(
    parent_case_ids,
    machine,
    supplement=None,
    *,
    search_phase=INITIAL_SEARCH_PHASE,
    shape_expansion_decision_sha256="",
):
    """Expand the requested campaign phase into a stable formal trial plan."""
    parents = sorted({str(value) for value in parent_case_ids})
    folds = build_fold_structure(parents, machine)
    campaign_specs = _campaign_specs(machine, supplement)
    if search_phase == INITIAL_SEARCH_PHASE:
        campaign_ids = INITIAL_CAMPAIGNS
        if shape_expansion_decision_sha256:
            raise ValueError("初始 campaign 不得绑定 shape 扩展决策哈希")
    elif search_phase == CONDITIONAL_SEARCH_PHASE:
        campaign_ids = (CONDITIONAL_CAMPAIGN,)
        if not shape_expansion_decision_sha256:
            raise ValueError("条件 campaign 必须绑定 shape 扩展决策哈希")
    else:
        raise ValueError(f"未知 search_phase: {search_phase!r}")
    outer_seeds = [int(seed) for seed in machine["search_budget"]["outer_search_seeds"]]
    final_seeds = [int(seed) for seed in machine["search_budget"]["final_search_seeds"]]
    plan = []
    for campaign_id in campaign_ids:
        spec = campaign_specs[campaign_id]
        task = spec["task"]
        variables = list(spec["variables"])
        for outer in folds:
            outer_index = outer["outer_fold_index"]
            holdout = outer["outer_holdout_parent_case_id"]
            for inner in outer["inner_folds"]:
                inner_index = inner["inner_fold_index"]
                for seed in outer_seeds:
                    plan.append({
                        "trial_id": f"{campaign_id}__outer{outer_index:02d}__inner{inner_index:02d}__seed{seed}",
                        "campaign_id": campaign_id,
                        "search_phase": search_phase,
                        "variables": variables,
                        "shape_expansion_decision_sha256": shape_expansion_decision_sha256,
                        "stage": "INNER_SEARCH",
                        "task": task,
                        "outer_fold_index": outer_index,
                        "outer_holdout_parent_case_id": holdout,
                        "inner_fold_index": inner_index,
                        "train_parent_case_ids": inner["train_parent_case_ids"],
                        "validation_parent_case_ids": inner["validation_parent_case_ids"],
                        "test_parent_case_ids": [holdout],
                        "seed": seed,
                    })
            for seed in outer_seeds:
                plan.append({
                    "trial_id": f"{campaign_id}__outer{outer_index:02d}__refit__seed{seed}",
                    "campaign_id": campaign_id,
                    "search_phase": search_phase,
                    "variables": variables,
                    "shape_expansion_decision_sha256": shape_expansion_decision_sha256,
                    "stage": "OUTER_REFIT_SEARCH",
                    "task": task,
                    "outer_fold_index": outer_index,
                    "outer_holdout_parent_case_id": holdout,
                    "inner_fold_index": -1,
                    "train_parent_case_ids": outer["outer_train_parent_case_ids"],
                    "validation_parent_case_ids": [],
                    "test_parent_case_ids": [holdout],
                    "seed": seed,
                })
        for seed in final_seeds:
            plan.append({
                "trial_id": f"{campaign_id}__final__seed{seed}",
                "campaign_id": campaign_id,
                "search_phase": search_phase,
                "variables": variables,
                "shape_expansion_decision_sha256": shape_expansion_decision_sha256,
                "stage": "FINAL_SEARCH",
                "task": task,
                "outer_fold_index": -1,
                "outer_holdout_parent_case_id": "",
                "inner_fold_index": -1,
                "train_parent_case_ids": parents,
                "validation_parent_case_ids": [],
                "test_parent_case_ids": [],
                "seed": seed,
            })
    if len({row["trial_id"] for row in plan}) != len(plan):
        raise AssertionError("搜索 trial_id 不唯一")
    _validate_campaign_budget(machine, supplement, search_phase, len(plan))
    return plan


def build_peak_dataset(metrics, parent_case_ids, machine):
    """Aggregate run-level peaks to one uncertainty-aware row per parent group."""
    allowed = set(parent_case_ids)
    grouped = defaultdict(list)
    for row in metrics:
        parent = row["parent_case_id"]
        if parent in allowed:
            grouped[parent].append(row)
    if set(grouped) != allowed:
        raise ValueError("峰值数据未覆盖请求的全部父组")

    normal = machine["normalization"]
    q_ref = float(normal["Q_ref_kW"])
    d_ref = float(normal["D_ref_m"])
    t0 = float(normal["T0_K"])
    grid_fraction = float(machine["final_refit"]["retain_peak_grid_uncertainty_fraction"])
    output = []
    for parent in sorted(grouped):
        group = grouped[parent]
        diameters = [float(row["Df_discrete_m"]) for row in group]
        if max(diameters) - min(diameters) > 1e-9:
            raise ValueError(f"{parent}: 同一父组的 Df_discrete_m 不一致")
        peaks = [float(row["deltaT_max_K"]) for row in group]
        if not all(math.isfinite(value) and value > 0 for value in peaks):
            raise ValueError(f"{parent}: 峰值必须为有限正数")
        q_actual = statistics.fmean(float(row["Q_actual_kW"]) for row in group)
        peak = statistics.fmean(peaks)
        within_sigma = [
            max(0.0, float(row["deltaT_peak_ci95_high_K"])
                - float(row["deltaT_peak_ci95_low_K"])) / 3.92
            for row in group
        ]
        within_mean_variance = sum(value * value for value in within_sigma) / len(group) ** 2
        between_mean_variance = statistics.variance(peaks) / len(group) if len(group) > 1 else 0.0
        sigma_peak = math.sqrt(
            within_mean_variance + between_mean_variance + (grid_fraction * peak) ** 2
        )
        sigma_target = max(sigma_peak / t0, 1e-12)
        output.append({
            "parent_case_id": parent,
            "q": q_actual / q_ref,
            "d": diameters[0] / d_ref,
            "F_T": peak / t0,
            "sigma_F_T": sigma_target,
            "weight": 1.0 / (sigma_target * sigma_target),
            "nested_run_count": len(group),
        })
    mean_weight = statistics.fmean(row["weight"] for row in output)
    for row in output:
        row["weight"] /= mean_weight
    return output


def build_shape_dataset(metrics, curves, parent_case_ids, machine):
    """Build point rows whose weights sum to one within every parent group."""
    allowed = set(parent_case_ids)
    metric_by_run = {row["run_chid"]: row for row in metrics}
    normal = machine["normalization"]
    q_ref = float(normal["Q_ref_kW"])
    d_ref = float(normal["D_ref_m"])
    domain = machine["data_contract"]["effective_domain"]
    xi_max = float(domain["xi_max"])
    roles = set(domain["include_point_roles"])
    selected = []
    for row in curves:
        parent = row["parent_case_id"]
        if parent not in allowed:
            continue
        xi = float(row["xi"])
        role = row["point_role"]
        if xi > xi_max + 1e-12 or role not in roles:
            continue
        metric = metric_by_run.get(row["run_chid"])
        if metric is None or metric["parent_case_id"] != parent:
            raise ValueError(f"形状点 {row['run_chid']} 缺少一致的运行级指标")
        peak = float(metric["deltaT_max_K"])
        point_sigma = max(
            0.0,
            float(row["deltaT_ci95_high_K"]) - float(row["deltaT_ci95_low_K"]),
        ) / 3.92 / peak
        censor_limit = float(row["detection_limit_K"]) / peak
        selected.append({
            "parent_case_id": parent,
            "physical_case_id": row["physical_case_id"],
            "run_chid": row["run_chid"],
            "xi": xi,
            "q": float(metric["Q_actual_kW"]) / q_ref,
            "d": float(metric["Df_discrete_m"]) / d_ref,
            "Theta": float(row["Theta"]),
            "sigma_Theta": max(point_sigma, 1e-6),
            "is_censored": role == domain["censored_role"],
            "censor_limit_Theta": censor_limit,
            "raw_precision": 1.0 / max(point_sigma * point_sigma, 1e-12),
        })
    grouped = defaultdict(list)
    for row in selected:
        grouped[row["parent_case_id"]].append(row)
    if set(grouped) != allowed:
        raise ValueError("形状数据未覆盖请求的全部父组")
    for rows in grouped.values():
        total = sum(row["raw_precision"] for row in rows)
        for row in rows:
            row["weight"] = row["raw_precision"] / total
            del row["raw_precision"]
    return selected


def _trial_dataset(dataset, trial):
    allowed = set(trial["train_parent_case_ids"])
    selected = [row for row in dataset if row["parent_case_id"] in allowed]
    present = {row["parent_case_id"] for row in selected}
    if present != allowed:
        raise ValueError(f"{trial['trial_id']}: 训练数据父组不完整")
    forbidden = set(trial["validation_parent_case_ids"]) | set(trial["test_parent_case_ids"])
    if present & forbidden:
        raise AssertionError(f"{trial['trial_id']}: 父组泄漏到训练数据")
    return selected


def _task_config_for_trial(machine, trial):
    """Return a campaign-scoped task config without mutating the protocol."""
    task_config = dict(machine["tasks"][trial["task"]])
    variables = list(trial["variables"])
    allowed = set(task_config["variables"])
    if not variables or len(variables) != len(set(variables)):
        raise protocol_guard.G5SRProtocolError(
            f"{trial['trial_id']}: campaign 变量为空或重复"
        )
    if not set(variables) <= allowed:
        raise protocol_guard.G5SRProtocolError(
            f"{trial['trial_id']}: campaign 变量超出父协议任务白名单"
        )
    if trial["campaign_id"] == "shape_xi_only" and variables != ["xi"]:
        raise protocol_guard.G5SRProtocolError(
            f"{trial['trial_id']}: xi-only campaign 必须仅向引擎传入 xi"
        )
    task_config["variables"] = variables
    return task_config


def run_pysr_trial(*, trial, dataset, task_config, machine, repo):
    """Default runner; PySR and NumPy are imported only after formal preflight."""
    try:
        import numpy as np
        from pysr import PySRRegressor
    except (ImportError, ModuleNotFoundError) as exc:
        raise RuntimeError("正式 G5-SR 需要锁定环境中的 PySR 与 NumPy") from exc

    variables = list(task_config["variables"])
    target = task_config["target"]
    x = np.asarray([[float(row[name]) for name in variables] for row in dataset])
    y = np.asarray([float(row[target]) for row in dataset])
    weights = np.asarray([float(row["weight"]) for row in dataset])
    operator = machine["operator_contract"]
    binary_map = {
        "add": "+", "subtract": "-", "multiply": "*", "divide": "/",
        "pow_positive_base": (
            "pow_positive_base(x, y) = "
            "(x > 0 && y >= -3.0 && y <= 3.0) ? x^y : oftype(x, NaN)"
        ),
    }
    unary_map = {
        "square": "square", "sqrt": "sqrt", "exp": "exp", "log": "log",
    }
    binary = [binary_map[name] for name in operator["allowed_binary"]]
    unary = [unary_map[name] for name in operator["allowed_unary"]]
    costs = operator["costs"]
    complexity = {
        "+": costs["add"], "-": costs["subtract"], "*": costs["multiply"],
        "/": costs["divide"], "pow_positive_base": costs["pow_positive_base"],
        "square": costs["square"], "sqrt": costs["sqrt"],
        "exp": costs["exp"], "log": costs["log"],
    }
    budget = machine["search_budget"]["per_seed"]
    engine_root = Path(repo) / "derived" / "development" / "g5_sr_engine"
    model = PySRRegressor(
        niterations=int(budget["iterations"]),
        populations=int(budget["populations"]),
        population_size=int(budget["population_size"]),
        max_evals=int(budget["candidate_evaluations_max"]),
        timeout_in_seconds=float(budget["wall_time_s_max"]),
        maxsize=int(task_config["max_complexity"]),
        maxdepth=int(task_config["max_depth"]),
        binary_operators=binary,
        unary_operators=unary,
        constraints={"pow_positive_base": (-1, 1)},
        nested_constraints={
            "exp": {"exp": 0, "log": 0},
            "log": {"exp": 0, "log": 0},
        },
        complexity_of_operators=complexity,
        complexity_of_constants=costs["constant"],
        complexity_of_variables=costs["variable"],
        random_state=int(trial["seed"]),
        deterministic=True,
        parallelism="serial",
        progress=False,
        verbosity=0,
        output_directory=str(engine_root),
        run_id=trial["trial_id"],
        extra_sympy_mappings={"pow_positive_base": lambda left, right: left ** right},
    )
    model.fit(x, y, weights=weights, variable_names=variables)
    candidates = []
    for _, row in model.equations_.iterrows():
        candidates.append({
            "raw_expression": str(row.get("equation", "")),
            "complexity": row.get("complexity", ""),
            "loss": row.get("loss", ""),
            "score": row.get("score", ""),
        })
    return {
        "candidates": candidates,
        "metadata": {
            "engine": "PySR",
            "engine_output_directory": str(engine_root),
            "engine_run_id": trial["trial_id"],
        },
    }


def _normalize_runner_result(result):
    if isinstance(result, list):
        return result, {}
    if not isinstance(result, dict):
        raise TypeError("runner 必须返回候选列表或含 candidates 的字典")
    candidates = result.get("candidates", [])
    if not isinstance(candidates, list) or not all(isinstance(row, dict) for row in candidates):
        raise TypeError("runner candidates 必须是字典列表")
    metadata = result.get("metadata", {})
    if not isinstance(metadata, dict):
        raise TypeError("runner metadata 必须是字典")
    return candidates, metadata


def _initial_trial_row(trial, protocol_hash, supplement_hash):
    return {
        "trial_id": trial["trial_id"],
        "campaign_id": trial["campaign_id"],
        "search_phase": trial["search_phase"],
        "variables_json": _json_list(trial["variables"]),
        "stage": trial["stage"],
        "task": trial["task"],
        "outer_fold_index": trial["outer_fold_index"],
        "outer_holdout_parent_case_id": trial["outer_holdout_parent_case_id"],
        "inner_fold_index": trial["inner_fold_index"],
        "train_parent_case_ids_json": _json_list(trial["train_parent_case_ids"]),
        "validation_parent_case_ids_json": _json_list(trial["validation_parent_case_ids"]),
        "test_parent_case_ids_json": _json_list(trial["test_parent_case_ids"]),
        "seed": trial["seed"],
        "status": "PENDING",
        "error_type": "",
        "error_message": "",
        "started_at_utc": "",
        "elapsed_s": "",
        "n_train_rows": "",
        "n_train_parent_groups": len(trial["train_parent_case_ids"]),
        "candidate_count": 0,
        "runner_metadata_json": "{}",
        "machine_protocol_sha256": protocol_hash,
        "execution_supplement_sha256": supplement_hash,
        "shape_expansion_decision_sha256": trial.get(
            "shape_expansion_decision_sha256", ""
        ),
    }


def _nonnegative_int(value, label):
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise G5SRResumeError(f"{label} 不是非负整数") from exc
    if parsed < 0:
        raise G5SRResumeError(f"{label} 不是非负整数")
    return parsed


def _validate_json_object(value, label):
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise G5SRResumeError(f"{label} 不是有效 JSON") from exc
    if not isinstance(parsed, dict):
        raise G5SRResumeError(f"{label} 必须是 JSON 对象")


def _load_resume_state(
    trial_path,
    candidate_path,
    plan,
    protocol_hash,
    supplement_hash,
    expected_train_rows,
):
    """Load and fully validate both formal outputs before allowing a write."""
    trial_rows, trial_columns = _read_csv_with_columns(trial_path)
    candidate_rows, candidate_columns = _read_csv_with_columns(candidate_path)
    if trial_columns != TRIAL_COLUMNS:
        raise G5SRResumeError("续跑 trial CSV 表头与冻结合同不一致")
    if candidate_columns != CANDIDATE_COLUMNS:
        raise G5SRResumeError("续跑 Pareto CSV 表头与冻结合同不一致")
    if len(trial_rows) != len(plan):
        raise G5SRResumeError(
            f"续跑 trial 计划不完整: expected={len(plan)}, actual={len(trial_rows)}"
        )

    expected_rows = [
        _initial_trial_row(trial, protocol_hash, supplement_hash)
        for trial in plan
    ]
    expected_ids = [row["trial_id"] for row in expected_rows]
    actual_ids = [row.get("trial_id", "") for row in trial_rows]
    if actual_ids != expected_ids or len(set(actual_ids)) != len(actual_ids):
        raise G5SRResumeError("续跑 trial_id 的顺序、完整性或唯一性与冻结计划不一致")

    seen_pending = False
    started_pending = 0
    by_id = {}
    for index, (row, expected) in enumerate(zip(trial_rows, expected_rows)):
        trial_id = expected["trial_id"]
        by_id[trial_id] = row
        for column in TRIAL_IDENTITY_COLUMNS:
            if str(row.get(column, "")) != str(expected[column]):
                raise G5SRResumeError(
                    f"续跑 trial 身份失配: {trial_id}: {column}"
                )
        status = row.get("status", "")
        if status not in TERMINAL_TRIAL_STATUSES | {"PENDING"}:
            raise G5SRResumeError(f"续跑 trial 状态无效: {trial_id}: {status!r}")
        _validate_json_object(
            row.get("runner_metadata_json", ""),
            f"{trial_id}.runner_metadata_json",
        )
        count = _nonnegative_int(row.get("candidate_count", ""), f"{trial_id}.candidate_count")
        started = bool(str(row.get("started_at_utc", "")).strip())
        if status == "PENDING":
            seen_pending = True
            if count != 0:
                raise G5SRResumeError(f"PENDING trial 不得已有候选: {trial_id}")
            if started:
                started_pending += 1
                if started_pending > 1:
                    raise G5SRResumeError("续跑记录包含多个已启动但未完成的 trial")
                if _nonnegative_int(
                    row.get("n_train_rows", ""), f"{trial_id}.n_train_rows"
                ) != expected_train_rows[trial_id]:
                    raise G5SRResumeError(f"续跑 trial 训练行数失配: {trial_id}")
            else:
                for column, initial in (
                    ("error_type", ""),
                    ("error_message", ""),
                    ("elapsed_s", ""),
                    ("n_train_rows", ""),
                    ("runner_metadata_json", "{}"),
                ):
                    if row.get(column, "") != initial:
                        raise G5SRResumeError(
                            f"未启动 PENDING trial 含非初始字段: {trial_id}: {column}"
                        )
        else:
            if seen_pending:
                raise G5SRResumeError("续跑状态不是单一终态前缀加 PENDING 后缀")
            if not started:
                raise G5SRResumeError(f"终态 trial 缺少启动时间: {trial_id}")
            if _nonnegative_int(
                row.get("n_train_rows", ""), f"{trial_id}.n_train_rows"
            ) != expected_train_rows[trial_id]:
                raise G5SRResumeError(f"终态 trial 训练行数失配: {trial_id}")
            elapsed = str(row.get("elapsed_s", "")).strip()
            if status != "FAILED_INTERRUPTED" or elapsed:
                try:
                    elapsed_value = float(elapsed)
                except ValueError as exc:
                    raise G5SRResumeError(f"{trial_id}.elapsed_s 无效") from exc
                if not math.isfinite(elapsed_value) or elapsed_value < 0:
                    raise G5SRResumeError(f"{trial_id}.elapsed_s 无效")

    candidates_by_trial = defaultdict(list)
    seen_candidate_keys = set()
    plan_index = {trial_id: index for index, trial_id in enumerate(expected_ids)}
    ordering = []
    for row in candidate_rows:
        trial_id = row.get("trial_id", "")
        if trial_id not in by_id:
            raise G5SRResumeError(f"Pareto 候选引用未知 trial: {trial_id}")
        rank = _nonnegative_int(
            row.get("candidate_rank", ""), f"{trial_id}.candidate_rank"
        )
        key = (trial_id, rank)
        if key in seen_candidate_keys:
            raise G5SRResumeError(f"Pareto 候选键重复: {trial_id}, rank={rank}")
        seen_candidate_keys.add(key)
        ordering.append((plan_index[trial_id], rank))
        trial_row = by_id[trial_id]
        for column in (
            "campaign_id",
            "search_phase",
            "variables_json",
            "stage",
            "task",
            "outer_fold_index",
            "outer_holdout_parent_case_id",
            "inner_fold_index",
            "seed",
            "machine_protocol_sha256",
            "execution_supplement_sha256",
            "shape_expansion_decision_sha256",
        ):
            if str(row.get(column, "")) != str(trial_row.get(column, "")):
                raise G5SRResumeError(
                    f"Pareto 候选身份失配: {trial_id}, rank={rank}: {column}"
                )
        _validate_json_object(
            row.get("raw_candidate_json", ""),
            f"{trial_id}[{rank}].raw_candidate_json",
        )
        candidates_by_trial[trial_id].append(rank)
    if ordering != sorted(ordering):
        raise G5SRResumeError("Pareto 候选顺序与冻结 trial/rank 顺序不一致")

    for trial_id, row in by_id.items():
        ranks = candidates_by_trial.get(trial_id, [])
        if ranks != list(range(len(ranks))):
            raise G5SRResumeError(f"Pareto candidate_rank 不连续: {trial_id}")
        declared = _nonnegative_int(
            row.get("candidate_count", ""), f"{trial_id}.candidate_count"
        )
        if declared != len(ranks):
            raise G5SRResumeError(f"trial 与 Pareto 候选计数不一致: {trial_id}")
        if row["status"] == "SUCCESS" and declared == 0:
            raise G5SRResumeError(f"SUCCESS trial 没有 Pareto 候选: {trial_id}")
        if row["status"] != "SUCCESS" and declared != 0:
            raise G5SRResumeError(f"非 SUCCESS trial 含 Pareto 候选: {trial_id}")
    return trial_rows, candidate_rows


def probe_julia_backend_versions():
    """Query the Julia backend loaded by PySR immediately before formal writes."""
    try:
        from pysr import jl
    except (ImportError, ModuleNotFoundError) as exc:
        raise RuntimeError("无法加载锁定环境中的 PySR/Julia 后端") from exc

    try:
        julia_version = str(jl.seval("string(VERSION)"))
        symbolic_regression_version = str(
            jl.seval("string(Base.pkgversion(SymbolicRegression))")
        )
    except Exception as exc:
        raise RuntimeError("无法查询 Julia 与 SymbolicRegression.jl 实际版本") from exc
    return {
        "julia_version": julia_version,
        "symbolic_regression_version": symbolic_regression_version,
    }


def _verify_julia_backend(preflight_result, version_probe):
    expected = preflight_result.get("environment", {})
    expected_versions = {
        "julia_version": expected.get("julia_version"),
        "symbolic_regression_version": expected.get(
            "symbolic_regression_version"
        ),
    }
    if any(
        not isinstance(value, str) or not value.strip()
        for value in expected_versions.values()
    ):
        raise protocol_guard.G5SRProtocolError(
            "完整预检未返回已冻结的 Julia 后端版本"
        )

    actual = version_probe()
    if not isinstance(actual, dict):
        raise TypeError("Julia 后端版本 probe 必须返回字典")
    verified = {}
    for key, expected_value in expected_versions.items():
        actual_value = actual.get(key)
        if not isinstance(actual_value, str) or not actual_value.strip():
            raise RuntimeError(f"Julia 后端版本 probe 缺少 {key}")
        if actual_value != expected_value:
            raise protocol_guard.G5SRProtocolError(
                f"正式 Julia 后端环境漂移: {key}: "
                f"recorded={expected_value!r}, current={actual_value!r}"
            )
        verified[key] = actual_value
    return verified


def execute_search(
    repo=PROJECT_ROOT,
    runner=None,
    preflight_fn=None,
    resume=False,
    backend_version_probe=None,
    phase="initial",
):
    """Execute one authorized campaign phase after the complete preflight.

    ``initial`` runs only ``peak_q_d`` and ``shape_xi_only``.  The conditional
    ``shape-expansion`` phase is a separate append-only transition and cannot
    be entered without a valid, hash-bound trigger artifact.
    """
    repo = Path(repo).resolve()
    if phase not in {"initial", "shape-expansion"}:
        raise ValueError(f"未知 G5-SR 执行阶段: {phase!r}")
    supplement_context = protocol_guard.load_approved_execution_supplement(repo)
    supplement = supplement_context["record"]
    supplement_hash = supplement_context["sha256"]
    preflight_fn = preflight_fn or protocol_guard.preflight
    preflight_result = preflight_fn(repo)
    if preflight_result.get("status") != "READY_FOR_G5_SR_SEARCH":
        raise PermissionError("G5-SR 完整预检未返回 READY_FOR_G5_SR_SEARCH")
    machine_path = repo / protocol_guard.MACHINE_PROTOCOL
    machine = _read_json(machine_path)
    protocol_hash = protocol_guard.sha256_file(machine_path)
    preflight_hash = str(preflight_result.get("machine_protocol_sha256", "")).lower()
    if preflight_hash != protocol_hash:
        raise PermissionError("G5-SR 预检协议哈希与当前机器协议不一致")
    preflight_supplement_hash = str(
        preflight_result.get("execution_supplement", {}).get(
            "approved_sha256", ""
        )
    ).lower()
    if preflight_supplement_hash != supplement_hash:
        raise PermissionError("G5-SR 预检补充协议哈希与当前批准版不一致")
    source_paths = {
        item["relative_path"]: repo / item["relative_path"]
        for item in machine["data_contract"]["required_sources"]
    }
    if any("lockbox" in path.as_posix().lower().split("/") for path in source_paths.values()):
        raise PermissionError("搜索编排拒绝任何 lockbox 路径")
    metrics = _read_csv(source_paths["derived/development/case_metrics.csv"])
    curves = _read_csv(source_paths["derived/development/curve_points.csv"])
    parents = sorted({row["parent_case_id"] for row in metrics})
    datasets = {
        "peak": build_peak_dataset(metrics, parents, machine),
        "shape": build_shape_dataset(metrics, curves, parents, machine),
    }
    initial_plan = build_search_plan(
        parents, machine, supplement, search_phase=INITIAL_SEARCH_PHASE
    )
    decision_context = None
    conditional_plan = []
    if phase == "shape-expansion":
        decision_context = protocol_guard.require_shape_expansion_authorized(
            repo,
            machine_protocol_sha256=protocol_hash,
            execution_supplement_sha256=supplement_hash,
            supplement=supplement,
        )
        conditional_plan = build_search_plan(
            parents,
            machine,
            supplement,
            search_phase=CONDITIONAL_SEARCH_PHASE,
            shape_expansion_decision_sha256=decision_context["decision_sha256"],
        )
    plan = initial_plan + conditional_plan
    if len(plan) > int(
        supplement["trial_budget"]["maximum_main_search_trial_count"]
    ):
        raise protocol_guard.G5SRProtocolError("G5-SR 主搜索计划超过冻结总上限")
    outputs = machine["output_contract"]
    trial_path = repo / outputs["search_trials"]
    candidate_path = repo / outputs["pareto_front"]
    expected_train_rows = {
        trial["trial_id"]: len(_trial_dataset(datasets[trial["task"]], trial))
        for trial in plan
    }
    exists = (trial_path.is_file(), candidate_path.is_file())
    if phase == "initial":
        if not resume:
            if any(exists):
                raise FileExistsError("正式搜索输出已存在；禁止覆盖或静默重跑")
            trial_rows = [
                _initial_trial_row(trial, protocol_hash, supplement_hash)
                for trial in initial_plan
            ]
            candidate_rows = []
        else:
            if exists != (True, True):
                raise FileNotFoundError("--resume 要求两份正式搜索输出同时存在")
            trial_rows, candidate_rows = _load_resume_state(
                trial_path,
                candidate_path,
                initial_plan,
                protocol_hash,
                supplement_hash,
                expected_train_rows,
            )
        active_plan = initial_plan
    else:
        if exists != (True, True):
            raise FileNotFoundError(
                "条件 shape campaign 要求完整保留初始 490-trial 输出"
            )
        if resume:
            trial_rows, candidate_rows = _load_resume_state(
                trial_path,
                candidate_path,
                plan,
                protocol_hash,
                supplement_hash,
                expected_train_rows,
            )
        else:
            trial_rows, candidate_rows = _load_resume_state(
                trial_path,
                candidate_path,
                initial_plan,
                protocol_hash,
                supplement_hash,
                expected_train_rows,
            )
            if any(row["status"] not in TERMINAL_TRIAL_STATUSES for row in trial_rows):
                raise PermissionError("初始 490-trial campaign 尚未全部形成终态")
            trial_rows.extend(
                _initial_trial_row(trial, protocol_hash, supplement_hash)
                for trial in conditional_plan
            )
        active_plan = conditional_plan

    initial_ids = {trial["trial_id"] for trial in initial_plan}
    if phase == "shape-expansion" and any(
        row["trial_id"] in initial_ids
        and row["status"] not in TERMINAL_TRIAL_STATUSES
        for row in trial_rows
    ):
        raise PermissionError("初始 campaign 非完整终态，拒绝条件扩展")

    backend_versions = _verify_julia_backend(
        preflight_result,
        backend_version_probe or probe_julia_backend_versions,
    )
    if not resume or phase == "shape-expansion":
        _write_csv_atomic(trial_path, trial_rows, TRIAL_COLUMNS)
        _write_csv_atomic(candidate_path, candidate_rows, CANDIDATE_COLUMNS)

    runner = runner or run_pysr_trial
    by_id = {row["trial_id"]: row for row in trial_rows}
    interrupted_trials = []
    if resume:
        active_ids = {trial["trial_id"] for trial in active_plan}
        for record in trial_rows:
            if record["trial_id"] not in active_ids:
                continue
            if record["status"] == "PENDING" and record["started_at_utc"].strip():
                record["status"] = "FAILED_INTERRUPTED"
                record["error_type"] = "InterruptedExecution"
                record["error_message"] = (
                    "上次进程在 trial 启动后、终态提交前中断；按协议计入失败且不重跑"
                )
                interrupted_trials.append(record["trial_id"])
        if interrupted_trials:
            _write_csv_atomic(trial_path, trial_rows, TRIAL_COLUMNS)

    for trial in active_plan:
        record = by_id[trial["trial_id"]]
        if record["status"] != "PENDING":
            continue
        data = _trial_dataset(datasets[trial["task"]], trial)
        record["started_at_utc"] = datetime.now(timezone.utc).isoformat()
        record["n_train_rows"] = len(data)
        # Persist the launch marker before entering PySR.  A killed process can
        # then distinguish an interrupted formal seed from an unstarted one.
        _write_csv_atomic(trial_path, trial_rows, TRIAL_COLUMNS)
        start = time.perf_counter()
        try:
            result = runner(
                trial=trial,
                dataset=data,
                task_config=_task_config_for_trial(machine, trial),
                machine=machine,
                repo=repo,
            )
            candidates, metadata = _normalize_runner_result(result)
            record["candidate_count"] = len(candidates)
            record["runner_metadata_json"] = json.dumps(
                metadata, ensure_ascii=False, sort_keys=True, default=str
            )
            record["status"] = "SUCCESS" if candidates else "FAILED_NO_CANDIDATES"
            for rank, candidate in enumerate(candidates):
                candidate_rows.append({
                    "trial_id": trial["trial_id"],
                    "candidate_rank": rank,
                    "campaign_id": trial["campaign_id"],
                    "search_phase": trial["search_phase"],
                    "variables_json": _json_list(trial["variables"]),
                    "stage": trial["stage"],
                    "task": trial["task"],
                    "outer_fold_index": trial["outer_fold_index"],
                    "outer_holdout_parent_case_id": trial["outer_holdout_parent_case_id"],
                    "inner_fold_index": trial["inner_fold_index"],
                    "seed": trial["seed"],
                    "raw_expression": candidate.get(
                        "raw_expression", candidate.get("equation", "")
                    ),
                    "complexity": candidate.get("complexity", ""),
                    "loss": candidate.get("loss", ""),
                    "score": candidate.get("score", ""),
                    "raw_candidate_json": json.dumps(
                        candidate, ensure_ascii=False, sort_keys=True, default=str
                    ),
                    "machine_protocol_sha256": protocol_hash,
                    "execution_supplement_sha256": supplement_hash,
                    "shape_expansion_decision_sha256": trial.get(
                        "shape_expansion_decision_sha256", ""
                    ),
                })
        except Exception as exc:  # Every failed formal seed remains in the denominator.
            record["status"] = "TIMEOUT" if isinstance(exc, TimeoutError) else "FAILED"
            record["error_type"] = type(exc).__name__
            record["error_message"] = str(exc)
        finally:
            record["elapsed_s"] = time.perf_counter() - start
            _write_csv_atomic(trial_path, trial_rows, TRIAL_COLUMNS)
            _write_csv_atomic(candidate_path, candidate_rows, CANDIDATE_COLUMNS)

    status_counts = defaultdict(int)
    for row in trial_rows:
        status_counts[row["status"]] += 1
    return {
        "status": "RAW_G5_SR_SEARCH_COMPLETE",
        "campaign_state": (
            "INITIAL_COMPLETE_AWAITING_EVALUATION"
            if phase == "initial" else "SEARCH_COMPLETE_735"
        ),
        "executed_phase": (
            INITIAL_SEARCH_PHASE
            if phase == "initial" else CONDITIONAL_SEARCH_PHASE
        ),
        "machine_protocol_sha256": protocol_hash,
        "execution_supplement_sha256": supplement_hash,
        "shape_expansion_decision_sha256": (
            decision_context["decision_sha256"] if decision_context else ""
        ),
        "planned_trials": len(trial_rows),
        "phase_trial_count": len(active_plan),
        "trial_status_counts": dict(sorted(status_counts.items())),
        "raw_pareto_candidates": len(candidate_rows),
        "search_trials": str(trial_path),
        "pareto_front": str(candidate_path),
        "lockbox_used": False,
        "formula_selected": False,
        "A_active_created": False,
        "resumed": bool(resume),
        "interrupted_trials_marked": interrupted_trials,
        "verified_julia_backend": backend_versions,
    }


def dry_run(repo=PROJECT_ROOT, preflight_fn=None, static_validator=None):
    """Return the complete trial plan without importing PySR or writing outputs."""
    repo = Path(repo).resolve()
    preflight_fn = preflight_fn or protocol_guard.preflight
    static_validator = static_validator or protocol_guard.validate_static
    caught_blocker = ""
    try:
        checked = preflight_fn(repo)
    except (
        protocol_guard.G5SRDataUnavailableError,
        protocol_guard.G5SRExecutionSupplementNotApprovedError,
    ) as exc:
        checked = static_validator(repo)
        if protocol_guard.G5_SR_EXECUTION_SUPPLEMENT_NOT_APPROVED in str(exc):
            caught_blocker = (
                protocol_guard.G5_SR_EXECUTION_SUPPLEMENT_NOT_APPROVED
            )
        elif protocol_guard.MISSING_G4_DERIVED_INPUTS in str(exc):
            caught_blocker = protocol_guard.MISSING_G4_DERIVED_INPUTS
        else:
            caught_blocker = type(exc).__name__
    blocker_codes = list(checked.get("blocker_codes", []))
    if caught_blocker and caught_blocker not in blocker_codes:
        blocker_codes.append(caught_blocker)
    data_ready = protocol_guard.MISSING_G4_DERIVED_INPUTS not in blocker_codes
    supplement_approved = (
        protocol_guard.G5_SR_EXECUTION_SUPPLEMENT_NOT_APPROVED
        not in blocker_codes
    )
    machine_path = repo / protocol_guard.MACHINE_PROTOCOL
    machine = _read_json(machine_path)
    supplement_record = None
    supplement_info = checked.get("execution_supplement", {})
    supplement_relative = (
        supplement_info.get("approved_path")
        if supplement_info.get("approved")
        else supplement_info.get("draft_path")
    )
    if supplement_relative:
        supplement_path = repo / supplement_relative
        if supplement_path.is_file():
            supplement_record = _read_json(supplement_path)
    registry = _read_csv(repo / machine["data_contract"]["development_registry"])
    if any(row.get("subset", "").strip().lower() != "development" for row in registry):
        raise PermissionError("dry-run 计划源含非 development 工况")
    parents = sorted({row["parent_case_id"] for row in registry})
    plan = build_search_plan(parents, machine, supplement_record)
    conditional_plan = build_search_plan(
        parents,
        machine,
        supplement_record,
        search_phase=CONDITIONAL_SEARCH_PHASE,
        shape_expansion_decision_sha256="0" * 64,
    )
    return {
        "status": "G5_SR_DRY_RUN_PLAN",
        "data_ready": data_ready,
        "execution_supplement_approved": supplement_approved,
        "formal_search_authorized": not blocker_codes,
        "blocker_code": blocker_codes[0] if blocker_codes else "",
        "blocker_codes": blocker_codes,
        "static_or_full_preflight": checked.get("status"),
        "machine_protocol_sha256": checked.get(
            "machine_protocol_sha256", protocol_guard.sha256_file(machine_path)
        ),
        "planned_trial_count": len(plan),
        "initial_trial_count": len(plan),
        "conditional_trial_count": len(conditional_plan),
        "maximum_main_search_trial_count": len(plan) + len(conditional_plan),
        "conditional_search_authorized": False,
        "lockbox_used": False,
        "plan": plan,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(PROJECT_ROOT))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--phase", choices=("initial", "shape-expansion"), default="initial"
    )
    args = parser.parse_args()
    result = (
        dry_run(args.repo)
        if args.dry_run
        else execute_search(args.repo, resume=args.resume, phase=args.phase)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
