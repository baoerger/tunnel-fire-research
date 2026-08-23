"""Audit raw G5-SR candidates without selecting or freezing a formula.

The fixed boundary/Sobol audit remains a diagnostic layer.  Eligibility for
subsequent out-of-fold selection additionally requires a deterministic strict
continuous-domain certificate; sampling never substitutes for that proof.
"""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import itertools
import json
import math
import re
from collections import defaultdict
from fractions import Fraction
from pathlib import Path
import unicodedata

from src.analysis import g5_sr_protocol as protocol_guard
from src.analysis import g5_sr_interval
from src.fds.project_paths import PROJECT_ROOT


FAMILY_COLUMNS = (
    "candidate_instance_id", "trial_id", "candidate_rank", "campaign_id",
    "search_phase", "variables_json", "stage", "task",
    "outer_fold_index", "inner_fold_index", "seed", "raw_expression",
    "canonical_expression", "parameter_template_expression",
    "parameter_initial_values_json", "structure_signature",
    "reference_scale_signature", "algebraic_family_id", "structure_family_id",
    "reference_scale_family_id", "prediction_family_id",
    "normalized_complexity", "raw_ast_complexity", "raw_ast_depth",
    "free_constant_count", "variable_support_json",
    "structural_status", "numeric_physics_status", "domain_certification_status",
    "freeze_eligible", "eligibility_scope", "rejection_reasons_json", "loss", "score",
    "machine_protocol_sha256", "execution_supplement_sha256",
    "shape_expansion_decision_sha256",
)
VARIABLE_COLUMNS = (
    "campaign_id", "search_phase", "task", "variables_json", "stage",
    "variable", "trial_denominator",
    "trials_with_variable_in_any_candidate", "trial_frequency",
    "candidate_instance_denominator", "candidate_instances_with_variable",
    "candidate_instance_frequency", "failed_trials_in_denominator",
    "machine_protocol_sha256", "execution_supplement_sha256",
    "shape_expansion_decision_sha256",
)
STABILITY_COLUMNS = (
    "campaign_id", "search_phase", "task", "variables_json", "record_type",
    "family_or_support", "outer_fold_numerator",
    "outer_fold_denominator", "outer_trial_numerator", "outer_trial_denominator",
    "final_seed_numerator", "final_seed_denominator",
    "failed_outer_trials_in_denominator", "failed_final_trials_in_denominator",
    "outer_stability_threshold", "final_seed_stability_threshold",
    "outer_stability_pass", "final_seed_stability_pass",
    "stability_evaluation_status", "stability_evaluation_reason",
    "machine_protocol_sha256", "execution_supplement_sha256",
    "shape_expansion_decision_sha256",
)
PHYSICS_COLUMNS = (
    "candidate_instance_id", "trial_id", "campaign_id", "search_phase",
    "variables_json", "task", "constraint",
    "certification_layer", "status", "margin", "worst_witness_json",
    "certification_method", "boxes_evaluated", "rejection_code",
    "machine_protocol_sha256", "execution_supplement_sha256",
    "shape_expansion_decision_sha256",
)

_CONSTANT_NAME = re.compile(r"^(?:c|C|k|K)[0-9]+$")
_CALLS = {"square", "sqrt", "exp", "log", "pow_positive_base"}
_PARAMETER_PREFIX = "__sr_parameter_"


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _read_csv(path):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"缺少 G5-SR 评价输入: {path}")
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise ValueError(f"CSV 缺少表头: {path}")
        return list(reader), list(reader.fieldnames)


def _write_csv_atomic(path, rows, columns):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows({key: row.get(key, "") for key in columns} for row in rows)
    temporary.replace(path)


def _compact_json_list(values):
    return json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))


def _campaign_contract(machine, supplement):
    """Return the only campaign identities accepted by formal evaluation."""
    sequence = supplement.get("shape_search_sequence", {})
    phase_1 = sequence.get("phase_1", {})
    phase_2 = sequence.get("phase_2", {})
    budget = supplement.get("trial_budget", {})
    contract = {
        "peak_q_d": ("INITIAL", "peak", ["q", "d"]),
        str(phase_1.get("campaign_id", "")): (
            "INITIAL", "shape", list(phase_1.get("variables", []))
        ),
        str(phase_2.get("campaign_id", "")): (
            "CONDITIONAL", "shape", list(phase_2.get("variables", []))
        ),
    }
    expected = {
        "peak_q_d": ("INITIAL", "peak", ["q", "d"]),
        "shape_xi_only": ("INITIAL", "shape", ["xi"]),
        "shape_xi_q_d": ("CONDITIONAL", "shape", ["xi", "q", "d"]),
    }
    if contract != expected:
        raise protocol_guard.G5SRProtocolError(
            "批准版执行补充协议的 campaign/phase/task/variables 合同不一致"
        )
    if budget.get("initial_campaigns") != ["peak_q_d", "shape_xi_only"]:
        raise protocol_guard.G5SRProtocolError(
            "批准版执行补充协议的 initial_campaigns 不一致"
        )
    if budget.get("conditional_campaign") != "shape_xi_q_d":
        raise protocol_guard.G5SRProtocolError(
            "批准版执行补充协议的 conditional_campaign 不一致"
        )
    return expected


def _legacy_campaign_identity(row, machine):
    """Provide compatibility only for pure summary helpers, never evaluate()."""
    task = row["task"]
    campaign_id = row.get("campaign_id") or (
        "peak_q_d" if task == "peak" else "shape_xi_only"
    )
    search_phase = row.get("search_phase") or "INITIAL"
    variables_json = row.get("variables_json") or _compact_json_list(
        machine["tasks"][task]["variables"]
    )
    return campaign_id, search_phase, task, variables_json


def _stable_id(prefix, value, length=16):
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}_{digest}"


def _sympy():
    try:
        import sympy as sp
    except (ImportError, ModuleNotFoundError) as exc:
        raise RuntimeError("G5-SR 候选审计需要锁定环境中的 SymPy") from exc
    return sp


def _make_symbol(sp, name):
    assumptions = {"real": True}
    if name in {"q", "d"}:
        assumptions["positive"] = True
    elif name == "xi":
        assumptions["nonnegative"] = True
    return sp.Symbol(name, **assumptions)


def _literal_value(node):
    """Return a signed Python numeric literal, or ``None`` for an expression.

    A leading sign is part of one fitted literal.  Keeping it together prevents
    ``-0.8`` from becoming a fixed minus operator plus a positive parameter.
    """
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            return None
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _literal_value(node.operand)
        if value is None:
            return None
        return value if isinstance(node.op, ast.UAdd) else -value
    return None


def _constant_numeric_value(node):
    """Evaluate a side-effect-free numeric AST exactly when practical.

    This is intentionally narrower than Python evaluation.  Its primary use is
    recognizing renderings such as ``x**(1/2)`` as the registered ``sqrt``
    operator, rather than mistaking ``1`` and ``2`` for two fitted constants.
    """
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            return None
        if not math.isfinite(float(node.value)):
            return None
        return Fraction(node.value) if isinstance(node.value, int) else Fraction(str(node.value))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _constant_numeric_value(node.operand)
        if value is None:
            return None
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp):
        left = _constant_numeric_value(node.left)
        right = _constant_numeric_value(node.right)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div) and right != 0:
            return left / right
    return None


def _constant_expression_initial_value(node):
    value = _constant_numeric_value(node)
    if value is None:
        return None
    return int(value) if value.denominator == 1 else float(value)


def _sympy_number(sp, value):
    if not math.isfinite(float(value)):
        raise ValueError("表达式含非有限常量")
    return sp.Integer(value) if isinstance(value, int) else sp.Float(str(value))


def _is_fixed_literal(value, role):
    """Identify rendered operator syntax that is not a fitted parameter."""
    numeric = float(value)
    # The second child of the explicit fitted-power operator is a searchable
    # constant even when optimization happens to land on 1/2, 1 or 2.
    if role == "fitted_exponent":
        return False
    # PySR renders the fixed square/sqrt operators as powers in some versions.
    # Those exponents are operator syntax, not two extra fitted constants.
    return role == "rendered_power_exponent" and numeric in (
        -1.0, 0.0, 0.5, 1.0, 2.0
    )


def _fixed_sympy_literal(sp, value, role):
    """Use exact SymPy values for operator syntax emitted as decimal text."""
    numeric = float(value)
    if numeric in (-1.0, 0.0, 1.0, 2.0):
        return sp.Integer(int(numeric))
    if role == "rendered_power_exponent" and numeric == 0.5:
        return sp.Rational(1, 2)
    return _sympy_number(sp, value)


def _domain_obligation(domain_obligations, *, operator, relation, argument, node):
    if domain_obligations is None:
        return
    domain_obligations.append({
        "operator": operator,
        "relation": relation,
        "argument_expression": argument,
        "argument_text": sp_sstr(argument),
        "raw_subexpression": ast.unparse(node),
    })


def sp_sstr(expression):
    """Stable SymPy text without importing SymPy at module import time."""
    return _sympy().sstr(expression, order="lex")


def _convert_ast(
    node, symbols, sp, parameters=None, role="value", domain_obligations=None,
):
    literal = _literal_value(node)
    if literal is not None:
        if parameters is not None and not _is_fixed_literal(literal, role):
            name = f"{_PARAMETER_PREFIX}{len(parameters)}"
            symbol = sp.Symbol(name, real=True)
            parameters.append({
                "internal_name": name,
                "initial_value": literal,
                "role": (
                    "fitted_exponent"
                    if role in {"fitted_exponent", "rendered_power_exponent"}
                    else role
                ),
            })
            return symbol
        return _fixed_sympy_literal(sp, literal, role)
    folded = _constant_numeric_value(node)
    if role in {"rendered_power_exponent", "fitted_exponent"} and folded is not None:
        initial_value = _constant_expression_initial_value(node)
        if role == "rendered_power_exponent" and _is_fixed_literal(float(folded), role):
            return _fixed_sympy_literal(sp, float(folded), role)
        if parameters is not None:
            name = f"{_PARAMETER_PREFIX}{len(parameters)}"
            symbol = sp.Symbol(name, real=True)
            parameters.append({
                "internal_name": name,
                "initial_value": initial_value,
                "role": "fitted_exponent",
            })
            return symbol
        if folded.denominator == 1:
            return sp.Integer(folded.numerator)
        return sp.Rational(folded.numerator, folded.denominator)
    if isinstance(node, ast.Expression):
        return _convert_ast(
            node.body, symbols, sp, parameters, role, domain_obligations
        )
    if isinstance(node, ast.Constant):
        raise ValueError("表达式常量必须是有限实数")
    if isinstance(node, ast.Name):
        if node.id not in symbols:
            raise ValueError(f"变量或常数名不在白名单: {node.id}")
        return symbols[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _convert_ast(
            node.operand, symbols, sp, parameters, role, domain_obligations
        )
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp):
        left = _convert_ast(
            node.left, symbols, sp, parameters, "value", domain_obligations
        )
        right_role = (
            "rendered_power_exponent" if isinstance(node.op, ast.Pow) else "value"
        )
        right = _convert_ast(
            node.right, symbols, sp, parameters, right_role, domain_obligations
        )
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            _domain_obligation(
                domain_obligations, operator="DIVISION", relation="NE_ZERO",
                argument=right, node=node,
            )
            return left / right
        if isinstance(node.op, ast.Pow):
            if right.is_integer is True:
                if right.is_negative is True:
                    _domain_obligation(
                        domain_obligations, operator="NEGATIVE_INTEGER_POWER",
                        relation="NE_ZERO", argument=left, node=node,
                    )
            elif right.is_number and right.is_real:
                relation = "GT_ZERO" if right.is_negative is True else "GE_ZERO"
                _domain_obligation(
                    domain_obligations, operator="NONINTEGER_POWER",
                    relation=relation, argument=left, node=node,
                )
            return left ** right
        raise ValueError(f"二元算子不在白名单: {type(node.op).__name__}")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _CALLS:
            raise ValueError("函数不在白名单")
        if node.keywords:
            raise ValueError("白名单函数不接受关键字参数")
        name = node.func.id
        args = [
            _convert_ast(
                value, symbols, sp, parameters,
                "fitted_exponent"
                if name == "pow_positive_base" and index == 1 else "value",
                domain_obligations,
            )
            for index, value in enumerate(node.args)
        ]
        if name == "square" and len(args) == 1:
            return args[0] ** 2
        if name == "sqrt" and len(args) == 1:
            _domain_obligation(
                domain_obligations, operator="SQRT", relation="GE_ZERO",
                argument=args[0], node=node,
            )
            return sp.sqrt(args[0])
        if name == "exp" and len(args) == 1:
            return sp.exp(args[0])
        if name == "log" and len(args) == 1:
            _domain_obligation(
                domain_obligations, operator="LOG", relation="GT_ZERO",
                argument=args[0], node=node,
            )
            return sp.log(args[0])
        if name == "pow_positive_base" and len(args) == 2:
            _domain_obligation(
                domain_obligations, operator="POW_POSITIVE_BASE",
                relation="GT_ZERO", argument=args[0], node=node,
            )
            return args[0] ** args[1]
        raise ValueError(f"函数 {name} 参数个数不合法")
    raise ValueError(f"表达式语法节点不在白名单: {type(node).__name__}")


def parse_whitelisted_expression(raw_expression, allowed_variables):
    """Parse a restricted arithmetic expression into a SymPy expression."""
    sp = _sympy()
    text = str(raw_expression).strip().replace("^", "**")
    if not text or len(text) > 4096:
        raise ValueError("表达式为空或过长")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"表达式语法无效: {exc.msg}") from exc
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    variable_names = set(allowed_variables)
    unknown = names - variable_names - _CALLS
    if any(not _CONSTANT_NAME.fullmatch(name) for name in unknown):
        raise ValueError("表达式含白名单之外的符号: " + ", ".join(sorted(unknown)))
    symbols = {name: _make_symbol(sp, name) for name in variable_names | unknown}
    return _convert_ast(tree, symbols, sp), symbols


def _parse_expression_with_domain_obligations(raw_expression, allowed_variables):
    """Parse a candidate and retain every pre-simplification domain guard."""
    sp = _sympy()
    text = str(raw_expression).strip().replace("^", "**")
    if not text or len(text) > 4096:
        raise ValueError("表达式为空或过长")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"表达式语法无效: {exc.msg}") from exc
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    variable_names = set(allowed_variables)
    unknown = names - variable_names - _CALLS
    if any(not _CONSTANT_NAME.fullmatch(name) for name in unknown):
        raise ValueError("表达式含白名单之外的符号: " + ", ".join(sorted(unknown)))
    symbols = {name: _make_symbol(sp, name) for name in variable_names | unknown}
    obligations = []
    expression = _convert_ast(
        tree, symbols, sp, domain_obligations=obligations
    )
    return expression, symbols, obligations, tree


def _parse_parameterized_expression(raw_expression, allowed_variables):
    """Parse while replacing fitted numeric leaves by auditable parameters."""
    sp = _sympy()
    text = str(raw_expression).strip().replace("^", "**")
    if not text or len(text) > 4096:
        raise ValueError("表达式为空或过长")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"表达式语法无效: {exc.msg}") from exc
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    variable_names = set(allowed_variables)
    unknown = names - variable_names - _CALLS
    if any(not _CONSTANT_NAME.fullmatch(name) for name in unknown):
        raise ValueError("表达式含白名单之外的符号: " + ", ".join(sorted(unknown)))
    symbols = {name: _make_symbol(sp, name) for name in variable_names | unknown}
    parameters = []
    expression = _convert_ast(tree, symbols, sp, parameters)
    return expression, parameters


def canonicalize_expression(expression):
    sp = _sympy()
    value = sp.powsimp(sp.cancel(sp.together(expression)), force=False)
    value = sp.factor(value)
    value = sp.simplify(value)
    return value, sp.sstr(value, order="lex")


def _complexity(expression, costs, parameter_names=()):
    sp = _sympy()
    if expression.is_Atom:
        is_parameter = str(expression) in set(parameter_names)
        return int(costs["constant"] if expression.is_number or is_parameter
                   else costs["variable"])
    if isinstance(expression, sp.Add):
        return sum(_complexity(arg, costs, parameter_names) for arg in expression.args) + (
            len(expression.args) - 1
        ) * int(costs["add"])
    if isinstance(expression, sp.Mul):
        numerators, denominators = [], []
        for arg in expression.args:
            if isinstance(arg, sp.Pow) and arg.exp.is_number and float(arg.exp) < 0:
                denominators.append(arg.base ** (-arg.exp))
            else:
                numerators.append(arg)
        if denominators:
            child = sum(_complexity(arg, costs, parameter_names)
                        for arg in numerators + denominators)
            multiply_count = max(0, len(numerators) - 1) + max(0, len(denominators) - 1)
            return child + multiply_count * int(costs["multiply"]) + len(denominators) * int(costs["divide"])
        return sum(_complexity(arg, costs, parameter_names) for arg in expression.args) + (
            len(expression.args) - 1
        ) * int(costs["multiply"])
    if isinstance(expression, sp.Pow):
        if expression.exp == -1:
            return (
                int(costs["constant"])
                + _complexity(expression.base, costs, parameter_names)
                + int(costs["divide"])
            )
        if expression.exp == 2:
            return _complexity(expression.base, costs, parameter_names) + int(costs["square"])
        if expression.exp == sp.Rational(1, 2):
            return _complexity(expression.base, costs, parameter_names) + int(costs["sqrt"])
        return (
            _complexity(expression.base, costs, parameter_names)
            + _complexity(expression.exp, costs, parameter_names)
            + int(costs["pow_positive_base"])
        )
    if expression.func == sp.exp:
        return _complexity(expression.args[0], costs, parameter_names) + int(costs["exp"])
    if expression.func == sp.log:
        return _complexity(expression.args[0], costs, parameter_names) + int(costs["log"])
    raise ValueError(f"规范化表达式含未登记算子: {expression.func}")


def _canonical_parameter_template(expression, parameter_records):
    """Return a fold-invariant template and aligned parameter initial values.

    Search engines emit optimized numbers, so exact canonical expressions from
    two folds cannot identify a shared structure.  Numeric leaves are first
    represented by unique symbols, then parameter names are canonicalized over
    all permutations (at most four for an admissible candidate).  The emitted
    template is directly usable by the later common constant refitter.
    """
    sp = _sympy()
    canonical, _ = canonicalize_expression(expression)
    record_by_internal = {row["internal_name"]: row for row in parameter_records}
    surviving = sorted(
        (symbol for symbol in canonical.free_symbols
         if str(symbol).startswith(_PARAMETER_PREFIX)),
        key=lambda symbol: int(str(symbol).removeprefix(_PARAMETER_PREFIX)),
    )
    public = [sp.Symbol(f"p{index}", real=True) for index in range(len(surviving))]
    permutations = itertools.permutations(public)
    # Candidates above the frozen free-constant limit are rejected anyway.  Do
    # not permit a hostile expression to trigger factorial work during audit.
    if len(surviving) > 6:
        permutations = [tuple(public)]
    best = None
    for replacement in permutations:
        mapping = dict(zip(surviving, replacement))
        renamed, text = canonicalize_expression(canonical.xreplace(mapping))
        item = (text, tuple(str(value) for value in replacement), renamed, mapping)
        if best is None or item[:2] < best[:2]:
            best = item
    if best is None:
        return canonical, sp.sstr(canonical, order="lex"), []
    text, _, renamed, mapping = best
    public_to_record = {
        str(public_symbol): record_by_internal[str(internal_symbol)]
        for internal_symbol, public_symbol in mapping.items()
    }
    aligned = []
    for public_symbol in public:
        source = public_to_record[str(public_symbol)]
        aligned.append({
            "name": str(public_symbol),
            "initial_value": source["initial_value"],
            "role": source["role"],
        })
    return renamed, text, aligned


def _scale_free_signature(expression, variable_names, *, symbolic_scale):
    """Normalize only a single overall multiplicative scale.

    This deliberately does not remove offsets or exponents.  Consequently an
    amplitude absorbed into the temperature/reference scale is equivalent,
    while a different curve shape is not mislabeled reference-scale equivalent.
    """
    sp = _sympy()
    canonical, _ = canonicalize_expression(expression)
    variable_symbols = {
        symbol for symbol in canonical.free_symbols if str(symbol) in set(variable_names)
    }
    independent, dependent = canonical.as_independent(
        *sorted(variable_symbols, key=str), as_Add=False
    )
    if dependent != 1 and independent != 1:
        if symbolic_scale:
            canonical = dependent
        elif independent.is_number and independent.is_real:
            canonical = sp.sign(independent) * dependent
    elif not symbolic_scale and isinstance(canonical, sp.Add):
        # SymPy does not always factor a common floating-point coefficient.
        # Normalize by a deterministic term coefficient so positively
        # proportional sums receive the same signature without erasing sign.
        anchor = canonical.as_ordered_terms()[0]
        coefficient, _ = anchor.as_coeff_Mul()
        if (coefficient.is_number and coefficient.is_real
                and coefficient.is_nonzero):
            canonical = canonical / abs(coefficient)
    canonical, text = canonicalize_expression(canonical)
    return canonical, text


def _raw_ast_structure_audit(tree, allowed_variables, task_config, machine):
    """Audit the unsimplified syntax tree and return complexity/depth evidence.

    This prevents algebraic cancellation (for example multiplication by zero)
    from hiding an operator that the frozen grammar forbids.
    """
    costs = machine["operator_contract"]["costs"]
    allowed_binary = set(machine["operator_contract"].get(
        "allowed_binary", ("add", "subtract", "multiply", "divide", "pow_positive_base")
    ))
    allowed_unary = set(machine["operator_contract"].get(
        "allowed_unary", ("square", "sqrt", "exp", "log")
    ))
    exponent_low, exponent_high = map(
        float,
        machine["operator_contract"].get(
            "pow_positive_base_constant_exponent_range", [-3.0, 3.0]
        ),
    )
    allowed_names = set(allowed_variables)
    rejections = []

    def reject(code):
        rejections.append(code)

    def numeric_exponent(node):
        value = _constant_numeric_value(node)
        if value is None:
            reject("POWER_EXPONENT_NOT_CONSTANT")
            return None
        numeric = float(value)
        if not math.isfinite(numeric):
            reject("POWER_EXPONENT_NOT_REAL")
            return None
        if not exponent_low <= numeric <= exponent_high:
            reject("POWER_EXPONENT_OUT_OF_RANGE")
        return numeric

    def visit(node):
        signed_literal = _literal_value(node)
        if signed_literal is not None:
            if not math.isfinite(float(signed_literal)):
                reject("NONFINITE_NUMERIC_LITERAL")
            return int(costs["constant"]), 0
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant):
            reject("NON_NUMERIC_LITERAL")
            return int(costs["constant"]), 0
        if isinstance(node, ast.Name):
            if node.id in allowed_names:
                return int(costs["variable"]), 0
            if _CONSTANT_NAME.fullmatch(node.id):
                return int(costs["constant"]), 0
            reject("SYMBOL_OUTSIDE_WHITELIST")
            return int(costs["variable"]), 0
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            child_complexity, child_depth = visit(node.operand)
            if isinstance(node.op, ast.USub):
                child_complexity += int(costs["constant"]) + int(costs["multiply"])
            return child_complexity, child_depth + 1
        if isinstance(node, ast.BinOp):
            left_complexity, left_depth = visit(node.left)
            if isinstance(node.op, ast.Pow):
                exponent = numeric_exponent(node.right)
                if exponent is None:
                    right_complexity, right_depth = visit(node.right)
                    operator_cost = int(costs["pow_positive_base"])
                else:
                    right_depth = 0
                    if exponent == 2.0:
                        operator_name = "square"
                        operator_cost = int(costs["square"])
                        right_complexity = 0
                    elif exponent == 0.5:
                        operator_name = "sqrt"
                        operator_cost = int(costs["sqrt"])
                        right_complexity = 0
                    elif exponent in (0.0, 1.0):
                        operator_name = None
                        operator_cost = 0
                        right_complexity = 0
                    elif exponent == -1.0:
                        operator_name = "divide"
                        operator_cost = int(costs["divide"])
                        right_complexity = int(costs["constant"])
                    else:
                        operator_name = "pow_positive_base"
                        operator_cost = int(costs["pow_positive_base"])
                        right_complexity = int(costs["constant"])
                    if operator_name in {"square", "sqrt"} and operator_name not in allowed_unary:
                        reject(f"OPERATOR_NOT_ALLOWED:{operator_name}")
                    if operator_name in {"divide", "pow_positive_base"} and operator_name not in allowed_binary:
                        reject(f"OPERATOR_NOT_ALLOWED:{operator_name}")
                    if (isinstance(node.left, ast.Name) and node.left.id == "xi"
                            and exponent < 0):
                        reject("NEGATIVE_POWER_OF_XI_FORBIDDEN")
                return (
                    left_complexity + right_complexity + operator_cost,
                    max(left_depth, right_depth) + 1,
                )

            right_complexity, right_depth = visit(node.right)
            operator_by_type = {
                ast.Add: "add", ast.Sub: "subtract", ast.Mult: "multiply",
                ast.Div: "divide",
            }
            operator_name = operator_by_type.get(type(node.op))
            if operator_name is None:
                reject(f"BINARY_OPERATOR_NOT_ALLOWED:{type(node.op).__name__}")
                operator_cost = 0
            else:
                if operator_name not in allowed_binary:
                    reject(f"OPERATOR_NOT_ALLOWED:{operator_name}")
                operator_cost = int(costs[operator_name])
            return (
                left_complexity + right_complexity + operator_cost,
                max(left_depth, right_depth) + 1,
            )
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _CALLS:
                reject("FUNCTION_OUTSIDE_WHITELIST")
                return 0, 0
            name = node.func.id
            if node.keywords:
                reject("KEYWORD_ARGUMENT_FORBIDDEN")
            expected_arity = 2 if name == "pow_positive_base" else 1
            if len(node.args) != expected_arity:
                reject("OPERATOR_ARITY_INVALID")
            if name == "pow_positive_base":
                if name not in allowed_binary:
                    reject(f"OPERATOR_NOT_ALLOWED:{name}")
                base_complexity, base_depth = visit(node.args[0]) if node.args else (0, 0)
                exponent = numeric_exponent(node.args[1]) if len(node.args) > 1 else None
                if (exponent is not None and isinstance(node.args[0], ast.Name)
                        and node.args[0].id == "xi" and exponent < 0):
                    reject("NEGATIVE_POWER_OF_XI_FORBIDDEN")
                exponent_complexity = int(costs["constant"]) if exponent is not None else 0
                exponent_depth = 0
                if exponent is None and len(node.args) > 1:
                    exponent_complexity, exponent_depth = visit(node.args[1])
                return (
                    base_complexity + exponent_complexity
                    + int(costs["pow_positive_base"]),
                    max(base_depth, exponent_depth) + 1,
                )

            if name not in allowed_unary:
                reject(f"OPERATOR_NOT_ALLOWED:{name}")
            child_complexity, child_depth = visit(node.args[0]) if node.args else (0, 0)
            if name == "log" and node.args and isinstance(node.args[0], ast.Name):
                if node.args[0].id == "xi":
                    reject("DIRECT_LOG_XI_FORBIDDEN")
            if name in {"exp", "log"} and node.args:
                nested = any(
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Name)
                    and child.func.id in {"exp", "log"}
                    for child in ast.walk(node.args[0])
                )
                if nested:
                    reject("EXP_LOG_NESTING_FORBIDDEN")
            return child_complexity + int(costs[name]), child_depth + 1

        reject(f"AST_NODE_OUTSIDE_WHITELIST:{type(node).__name__}")
        return 0, 0

    complexity, depth = visit(tree)
    if complexity > int(task_config["max_complexity"]):
        reject("RAW_COMPLEXITY_EXCEEDED")
    max_depth = task_config.get("max_depth")
    if max_depth is not None and depth > int(max_depth):
        reject("RAW_MAX_DEPTH_EXCEEDED")
    return {
        "raw_ast_complexity": complexity,
        "raw_ast_depth": depth,
        "rejection_reasons": sorted(set(rejections)),
    }


def audit_expression(raw_expression, task, machine, *, allowed_variables=None):
    """Return structural audit metadata plus an internal SymPy expression."""
    task_config = dict(machine["tasks"][task])
    variables = list(
        task_config["variables"]
        if allowed_variables is None else allowed_variables
    )
    if not variables or len(variables) != len(set(variables)):
        raise ValueError("候选审计变量白名单为空或重复")
    if not set(variables) <= set(machine["tasks"][task]["variables"]):
        raise ValueError("候选审计变量超出父协议任务白名单")
    task_config["variables"] = variables
    result = {
        "raw_expression": str(raw_expression),
        "canonical_expression": "",
        "parameter_template_expression": "",
        "parameter_initial_values": [],
        "structure_signature": "",
        "reference_scale_signature": "",
        "normalized_complexity": "",
        "raw_ast_complexity": "",
        "raw_ast_depth": "",
        "free_constant_count": "",
        "variable_support": [],
        "unresolved_constants": [],
        "operator_domain_obligations": [],
        "structural_status": "FAIL",
        "rejection_reasons": [],
        "expression": None,
    }
    try:
        expression, _, domain_obligations, raw_tree = (
            _parse_expression_with_domain_obligations(
            raw_expression, variables
            )
        )
        raw_structure = _raw_ast_structure_audit(
            raw_tree, variables, task_config, machine
        )
        canonical, canonical_text = canonicalize_expression(expression)
        parameterized, parameter_records = _parse_parameterized_expression(
            raw_expression, variables
        )
        template, template_text, parameter_initial_values = (
            _canonical_parameter_template(parameterized, parameter_records)
        )
        parameter_names = {row["name"] for row in parameter_initial_values}
        complexity = _complexity(
            template, machine["operator_contract"]["costs"], parameter_names
        )
        unresolved = sorted(
            str(symbol) for symbol in canonical.free_symbols
            if str(symbol) not in set(variables)
        )
        constant_count = len(parameter_initial_values) + len(unresolved)
        support = sorted(str(symbol) for symbol in canonical.free_symbols if str(symbol) in variables)
        _, reference_text = _scale_free_signature(
            canonical, variables, symbolic_scale=False
        )
        _, structure_text = _scale_free_signature(
            template, variables, symbolic_scale=True
        )
        result.update({
            "canonical_expression": canonical_text,
            "parameter_template_expression": template_text,
            "parameter_initial_values": parameter_initial_values,
            "structure_signature": structure_text,
            "reference_scale_signature": reference_text,
            "normalized_complexity": complexity,
            "raw_ast_complexity": raw_structure["raw_ast_complexity"],
            "raw_ast_depth": raw_structure["raw_ast_depth"],
            "free_constant_count": constant_count,
            "variable_support": support,
            "unresolved_constants": unresolved,
            "operator_domain_obligations": domain_obligations,
            "expression": canonical,
        })
        if complexity > int(task_config["max_complexity"]):
            result["rejection_reasons"].append("COMPLEXITY_EXCEEDED")
        if constant_count > int(task_config["max_free_constants"]):
            result["rejection_reasons"].append("FREE_CONSTANTS_EXCEEDED")
        if unresolved:
            result["rejection_reasons"].append("UNRESOLVED_FREE_CONSTANTS")
        if not support:
            result["rejection_reasons"].append("NO_ALLOWED_VARIABLE_SUPPORT")
        result["rejection_reasons"].extend(
            raw_structure["rejection_reasons"]
        )
        result["structural_status"] = (
            "PASS" if not result["rejection_reasons"] else "FAIL"
        )
    except Exception as exc:
        result["rejection_reasons"].append(f"PARSE_OR_AUDIT_FAILED:{type(exc).__name__}")
        result["parse_error"] = str(exc)
    return result


def _domain_points(task, machine, *, allowed_variables=None):
    import numpy as np
    from scipy.stats import qmc

    variables = list(
        machine["tasks"][task]["variables"]
        if allowed_variables is None else allowed_variables
    )
    support = machine["physical_constraints"]["continuous_support"]
    boundary_axes = []
    for variable in variables:
        low, high = map(float, support[variable])
        boundary_axes.append((low, (low + high) / 2.0, high))
    boundary = np.asarray(list(itertools.product(*boundary_axes)), dtype=float)
    count = int(machine["physical_constraints"]["sobol_points"])
    sampler = qmc.Sobol(
        d=len(variables), scramble=True,
        seed=int(machine["physical_constraints"]["sobol_seed"]),
    )
    if count > 0 and count & (count - 1) == 0:
        sobol = sampler.random_base2(int(math.log2(count)))
    else:
        sobol = sampler.random(count)
    lows = np.asarray([float(support[name][0]) for name in variables])
    highs = np.asarray([float(support[name][1]) for name in variables])
    sobol = qmc.scale(sobol, lows, highs)
    return variables, np.vstack([boundary, sobol]), len(boundary)


def _evaluate_expression(expression, variables, points):
    import numpy as np
    sp = _sympy()
    by_name = {str(symbol): symbol for symbol in expression.free_symbols}
    symbols = [by_name.get(name, _make_symbol(sp, name)) for name in variables]
    function = sp.lambdify(symbols, expression, modules="numpy")
    with np.errstate(all="ignore"):
        values = np.asarray(function(*[points[:, index] for index in range(len(variables))]), dtype=float)
    if values.ndim == 0:
        values = np.full(len(points), float(values))
    return np.broadcast_to(values, (len(points),)).astype(float, copy=False), symbols


def numeric_physics_audit(
    expression, task, machine, *, operator_domain_obligations=None,
    allowed_variables=None,
):
    """Run the fixed numerical audit and an independent continuous proof.

    ``freeze_eligible`` here means eligible to enter later selection/refit
    gates.  This function never selects or freezes A_SR/A_active.
    """
    import numpy as np
    sp = _sympy()
    variables, points, boundary_count = _domain_points(
        task, machine, allowed_variables=allowed_variables
    )
    constraints = []
    reasons = []

    def add(name, passed, margin, witness_index, code):
        witness = {}
        if witness_index is not None and len(points):
            witness = {variable: float(points[witness_index, i])
                       for i, variable in enumerate(variables)}
        constraints.append({
            "constraint": name,
            "certification_layer": "BOUNDARY_PLUS_SOBOL_NUMERIC",
            "status": "PASS" if passed else "FAIL",
            "margin": margin,
            "worst_witness": witness,
            "rejection_code": "" if passed else code,
            "certification_method": "FIXED_BOUNDARY_PLUS_SOBOL_NUMERIC_AUDIT",
            "boxes_evaluated": 0,
        })
        if not passed:
            reasons.append(code)

    try:
        values, symbols = _evaluate_expression(expression, variables, points)
    except Exception:
        values = np.full(len(points), np.nan)
        symbols = []
    finite = np.isfinite(values)
    add("REAL_FINITE_ON_AUDIT_POINTS", bool(np.all(finite)),
        float(np.mean(finite) - 1.0), int(np.where(~finite)[0][0]) if not np.all(finite) else 0,
        "NONFINITE_OR_UNDEFINED_ON_AUDIT_POINTS")
    safe_values = values[finite]
    if not len(safe_values):
        reasons.append("NUMERIC_AUDIT_UNAVAILABLE")
    elif task == "peak":
        minimum_index = int(np.nanargmin(values))
        add("F_T_STRICTLY_POSITIVE", bool(np.min(safe_values) > 0.0),
            float(np.min(safe_values)), minimum_index, "F_T_NOT_STRICTLY_POSITIVE")
        try:
            q_symbol = next(symbol for symbol in symbols if str(symbol) == "q")
            derivative = sp.diff(expression, q_symbol)
            derivative_values, _ = _evaluate_expression(derivative, variables, points)
            derivative_finite = np.isfinite(derivative_values)
            minimum_derivative = float(np.min(derivative_values[derivative_finite])) if np.any(derivative_finite) else -math.inf
            derivative_index = int(np.nanargmin(derivative_values)) if np.any(derivative_finite) else 0
            add("PEAK_NONDECREASING_WITH_Q",
                bool(np.all(derivative_finite) and minimum_derivative >= -1e-10),
                minimum_derivative, derivative_index, "PEAK_DECREASES_WITH_Q")
        except Exception:
            add("PEAK_NONDECREASING_WITH_Q", False, -math.inf, 0,
                "PEAK_Q_DERIVATIVE_UNAVAILABLE")
    elif task == "shape":
        low_index = int(np.nanargmin(values))
        high_index = int(np.nanargmax(values))
        add("THETA_NONNEGATIVE", bool(np.min(safe_values) >= -1e-10),
            float(np.min(safe_values)), low_index, "THETA_BELOW_ZERO")
        upper_margin = float(1.0 - np.max(safe_values))
        add("THETA_NOT_ABOVE_ONE", upper_margin >= -1e-10,
            upper_margin, high_index, "THETA_ABOVE_ONE")
        xi_index = variables.index("xi")
        xi_zero = np.isclose(points[:, xi_index], 0.0, rtol=0.0, atol=1e-14)
        normalization_error = float(np.max(np.abs(values[xi_zero] - 1.0)))
        normalization_margin = 1e-8 - normalization_error
        normalization_worst = int(np.where(xi_zero)[0][np.argmax(np.abs(values[xi_zero] - 1.0))])
        add("THETA_AT_ZERO_EQUALS_ONE", normalization_margin >= 0.0,
            normalization_margin, normalization_worst, "THETA_ZERO_NOT_NORMALIZED")
        try:
            xi_symbol = next(symbol for symbol in symbols if str(symbol) == "xi")
            derivative = sp.diff(expression, xi_symbol)
            derivative_values, _ = _evaluate_expression(derivative, variables, points)
            derivative_finite = np.isfinite(derivative_values)
            maximum_derivative = float(np.max(derivative_values[derivative_finite])) if np.any(derivative_finite) else math.inf
            derivative_index = int(np.nanargmax(derivative_values)) if np.any(derivative_finite) else 0
            add("THETA_NONINCREASING_WITH_XI",
                bool(np.all(derivative_finite) and maximum_derivative <= 1e-10),
                -maximum_derivative, derivative_index, "THETA_INCREASES_WITH_XI")
        except Exception:
            add("THETA_NONINCREASING_WITH_XI", False, -math.inf, 0,
                "THETA_XI_DERIVATIVE_UNAVAILABLE")
    else:
        raise ValueError(f"未知符号回归任务: {task}")

    continuous = g5_sr_interval.certify_continuous_domain(
        expression, task, machine,
        operator_domain_obligations=operator_domain_obligations,
    )
    constraints.extend(continuous["constraints"])
    reasons.extend(continuous["rejection_reasons"])
    numeric_failures = [row for row in constraints
                        if row["certification_layer"] == "BOUNDARY_PLUS_SOBOL_NUMERIC"
                        and row["status"] == "FAIL"]
    numeric_margins = [float(row["margin"]) for row in constraints
                       if row["certification_layer"] == "BOUNDARY_PLUS_SOBOL_NUMERIC"
                       and row["margin"] != "" and math.isfinite(float(row["margin"]))]
    return {
        "numeric_status": "PASS" if not numeric_failures else "FAIL",
        "domain_certification_status": continuous["domain_certification_status"],
        "freeze_eligible": (
            not numeric_failures and continuous["freeze_eligible"]
        ),
        "eligibility_scope": (
            continuous["eligibility_scope"]
            if not numeric_failures else "INELIGIBLE_FOR_SELECTION"
        ),
        "rejection_reasons": sorted(set(reasons)),
        "worst_numeric_margin": min(numeric_margins) if numeric_margins else -math.inf,
        "boundary_point_count": boundary_count,
        "sobol_point_count": len(points) - boundary_count,
        "constraints": constraints,
        "points": points,
        "variables": variables,
        "values": values,
    }


def _nfc_utf8(value):
    return unicodedata.normalize("NFC", str(value)).encode("utf-8")


def _prediction_member_order(row):
    """Frozen complete-link leader order from the execution supplement."""
    return (
        int(row["normalized_complexity"]),
        int(row["free_constant_count"]),
        _nfc_utf8(row["canonical_expression"]),
        _nfc_utf8(row.get("candidate_instance_id", "")),
    )


def _complete_link_prediction_clusters(ordered_keys, equivalent_pairs):
    """Cluster without taking the invalid transitive closure of similarity."""
    clusters = []
    for key in ordered_keys:
        destination = None
        for cluster in clusters:
            if all(
                member == key or frozenset((member, key)) in equivalent_pairs
                for member in cluster
            ):
                destination = cluster
                break
        if destination is None:
            clusters.append([key])
        else:
            destination.append(key)
    return clusters


def assign_equivalence_families(audits, machine):
    """Assign exact, fitted-structure, reference-scale and prediction families."""
    import numpy as np
    from scipy.spatial import cKDTree

    for task in machine["tasks"]:
        selected = [row for row in audits if row["task"] == task and row["expression"] is not None]
        if not selected:
            continue
        by_canonical = defaultdict(list)
        by_structure = defaultdict(list)
        by_reference = defaultdict(list)
        for row in selected:
            by_canonical[row["canonical_expression"]].append(row)
            by_structure[row["structure_signature"]].append(row)
            by_reference[row["reference_scale_signature"]].append(row)
        for canonical, rows in by_canonical.items():
            family = _stable_id("ALG", f"{task}|{canonical}")
            for row in rows:
                row["algebraic_family_id"] = family
        for signature, rows in by_structure.items():
            family = _stable_id("STRUCT", f"{task}|{signature}")
            for row in rows:
                row["structure_family_id"] = family
        for signature, rows in by_reference.items():
            family = _stable_id("REF", f"{task}|{signature}")
            for row in rows:
                row["reference_scale_family_id"] = family

        unique = {}
        for row in selected:
            if row["unresolved_constants"]:
                continue
            key = row["canonical_expression"]
            if (key not in unique
                    or _prediction_member_order(row) < _prediction_member_order(unique[key])):
                unique[key] = row
        keys = sorted(unique, key=lambda key: _prediction_member_order(unique[key]))
        predictions = []
        valid_keys = []
        variables, points, _ = _domain_points(task, machine)
        for key in keys:
            try:
                values, _ = _evaluate_expression(unique[key]["expression"], variables, points)
            except Exception:
                continue
            if np.all(np.isfinite(values)):
                predictions.append(values)
                valid_keys.append(key)
        if valid_keys:
            matrix = np.asarray(predictions)
            anchor_count = min(16, matrix.shape[1])
            anchor_indices = np.linspace(0, matrix.shape[1] - 1, anchor_count, dtype=int)
            anchors = matrix[:, anchor_indices]
            thresholds = machine["equivalence_contract"]["prediction_equivalence"]
            max_threshold = float(thresholds["max_fraction_of_project_temperature_scale_max"])
            rms_threshold = float(thresholds["rms_fraction_of_project_temperature_scale_max"])
            equivalent_pairs = set()
            for left, right in sorted(
                cKDTree(anchors).query_pairs(max_threshold, p=math.inf)
            ):
                difference = matrix[left] - matrix[right]
                rms = float(np.sqrt(np.mean(difference * difference)))
                maximum = float(np.max(np.abs(difference)))
                if rms <= rms_threshold and maximum <= max_threshold:
                    equivalent_pairs.add(frozenset((valid_keys[left], valid_keys[right])))
        else:
            equivalent_pairs = set()
        valid = set(valid_keys)
        clusters = _complete_link_prediction_clusters(
            [key for key in keys if key in valid], equivalent_pairs
        )
        clusters.extend([key] for key in keys if key not in valid)
        family_by_key = {}
        for component in clusters:
            identity = _stable_id(
                "PRED", f"{task}|" + "|".join(sorted(component, key=_nfc_utf8))
            )
            for key in component:
                family_by_key[key] = identity
        for row in selected:
            row["prediction_family_id"] = family_by_key.get(
                row["canonical_expression"], ""
            )
    for row in audits:
        row.setdefault("algebraic_family_id", "")
        row.setdefault("structure_family_id", "")
        row.setdefault("reference_scale_family_id", "")
        row.setdefault("prediction_family_id", "")
    return audits


def summarize_variable_frequency(trials, audits, machine):
    rows = []
    campaign_groups = sorted({
        _legacy_campaign_identity(row, machine) for row in trials
    })
    for campaign_id, search_phase, task, variables_json in campaign_groups:
        variables = json.loads(variables_json)
        campaign_trials = [
            row for row in trials
            if _legacy_campaign_identity(row, machine)
            == (campaign_id, search_phase, task, variables_json)
        ]
        for stage in ("INNER_SEARCH", "OUTER_REFIT_SEARCH", "FINAL_SEARCH"):
            stage_trials = [row for row in campaign_trials if row["stage"] == stage]
            trial_ids = {row["trial_id"] for row in stage_trials}
            candidates = [row for row in audits if row["trial_id"] in trial_ids]
            for variable in variables:
                trial_with = {
                    row["trial_id"] for row in candidates
                    if variable in row["variable_support"]
                }
                candidate_with = sum(variable in row["variable_support"] for row in candidates)
                rows.append({
                    "campaign_id": campaign_id,
                    "search_phase": search_phase,
                    "task": task,
                    "variables_json": variables_json,
                    "stage": stage,
                    "variable": variable,
                    "trial_denominator": len(stage_trials),
                    "trials_with_variable_in_any_candidate": len(trial_with),
                    "trial_frequency": len(trial_with) / len(stage_trials) if stage_trials else 0.0,
                    "candidate_instance_denominator": len(candidates),
                    "candidate_instances_with_variable": candidate_with,
                    "candidate_instance_frequency": candidate_with / len(candidates) if candidates else 0.0,
                    "failed_trials_in_denominator": sum(
                        row.get("status") != "SUCCESS" for row in stage_trials
                    ),
                    "machine_protocol_sha256": (
                        stage_trials[0].get("machine_protocol_sha256", "")
                        if stage_trials else ""
                    ),
                    "execution_supplement_sha256": (
                        stage_trials[0].get("execution_supplement_sha256", "")
                        if stage_trials else ""
                    ),
                    "shape_expansion_decision_sha256": (
                        stage_trials[0].get("shape_expansion_decision_sha256", "")
                        if stage_trials else ""
                    ),
                })
    return rows


def summarize_stability(trials, audits, machine):
    """Emit a fail-closed state until unique selection records exist.

    Raw Pareto presence is diagnostic discovery frequency, not stability.  The
    current output contract contains no leakage-free, one-per-outer-fold and
    one-per-final-seed selection artifact, so no stability numerator can be
    computed honestly at this stage.
    """
    rows = []
    outer_threshold = int(machine["stability_gate"]["equivalence_family_outer_folds_min"])
    final_threshold = int(machine["stability_gate"]["core_variable_support_final_seeds_min"])
    campaign_groups = sorted({
        _legacy_campaign_identity(row, machine) for row in trials
    })
    for campaign_id, search_phase, task, variables_json in campaign_groups:
        campaign_trials = [
            row for row in trials
            if _legacy_campaign_identity(row, machine)
            == (campaign_id, search_phase, task, variables_json)
        ]
        outer_trials = [row for row in campaign_trials
                        if row["stage"] == "OUTER_REFIT_SEARCH"]
        final_trials = [row for row in campaign_trials
                        if row["stage"] == "FINAL_SEARCH"]
        provenance = (outer_trials or final_trials or campaign_trials)[0]
        outer_folds = {row["outer_fold_index"] for row in outer_trials}
        final_seeds = {row["seed"] for row in final_trials}
        rows.append({
            "campaign_id": campaign_id,
            "search_phase": search_phase,
            "task": task,
            "variables_json": variables_json,
            "record_type": "STABILITY_EVALUATION_STATE",
            "family_or_support": "",
            "outer_fold_numerator": "",
            "outer_fold_denominator": len(outer_folds),
            "outer_trial_numerator": "",
            "outer_trial_denominator": len(outer_trials),
            "final_seed_numerator": "",
            "final_seed_denominator": len(final_seeds),
            "failed_outer_trials_in_denominator": sum(
                row.get("status") != "SUCCESS" for row in outer_trials
            ),
            "failed_final_trials_in_denominator": sum(
                row.get("status") != "SUCCESS" for row in final_trials
            ),
            "outer_stability_threshold": outer_threshold,
            "final_seed_stability_threshold": final_threshold,
            "outer_stability_pass": False,
            "final_seed_stability_pass": False,
            "stability_evaluation_status": "NOT_EVALUABLE",
            "stability_evaluation_reason": (
                "MISSING_FORMAL_UNIQUE_OUTER_FOLD_AND_FINAL_SEED_SELECTION_RECORDS;"
                "RAW_PARETO_PRESENCE_IS_DIAGNOSTIC_ONLY"
            ),
            "machine_protocol_sha256": provenance.get(
                "machine_protocol_sha256", ""
            ),
            "execution_supplement_sha256": provenance.get(
                "execution_supplement_sha256", ""
            ),
            "shape_expansion_decision_sha256": provenance.get(
                "shape_expansion_decision_sha256", ""
            ),
        })
    return rows


def _audit_candidates(
    trials, candidates, machine, protocol_hash, supplement_hash=""
):
    trial_by_id = {row["trial_id"]: row for row in trials}
    if len(trial_by_id) != len(trials):
        raise ValueError("raw trial CSV 的 trial_id 不唯一")
    audits = []
    physics_rows = []
    for index, candidate in enumerate(candidates):
        trial_id = candidate["trial_id"]
        if trial_id not in trial_by_id:
            raise ValueError(f"候选引用未知 trial_id: {trial_id}")
        trial = trial_by_id[trial_id]
        if trial.get("status") != "SUCCESS":
            raise ValueError(
                f"{trial_id}: 非 SUCCESS trial 不得贡献 Pareto 候选或稳定性证据"
            )
        if candidate.get("task") and candidate["task"] != trial["task"]:
            raise ValueError(f"{trial_id}: 候选 task 与 trial 不一致")
        campaign_id, search_phase, _, variables_json = (
            _legacy_campaign_identity(trial, machine)
        )
        variables = json.loads(variables_json)
        raw = candidate.get("raw_expression", "")
        instance = _stable_id(
            "CAND", f"{trial_id}|{candidate.get('candidate_rank', index)}|{raw}", 20
        )
        structural = audit_expression(
            raw, trial["task"], machine, allowed_variables=variables
        )
        audit = {
            **structural,
            "candidate_instance_id": instance,
            "trial_id": trial_id,
            "candidate_rank": candidate.get("candidate_rank", index),
            "campaign_id": campaign_id,
            "search_phase": search_phase,
            "variables_json": variables_json,
            "stage": trial["stage"],
            "task": trial["task"],
            "outer_fold_index": trial.get("outer_fold_index", ""),
            "inner_fold_index": trial.get("inner_fold_index", ""),
            "seed": trial.get("seed", ""),
            "execution_supplement_sha256": (
                trial.get("execution_supplement_sha256") or supplement_hash
            ),
            "shape_expansion_decision_sha256": trial.get(
                "shape_expansion_decision_sha256", ""
            ),
            "loss": candidate.get("loss", ""),
            "score": candidate.get("score", ""),
            "numeric_physics_status": "NOT_RUN",
            "domain_certification_status": "NOT_RUN_STRUCTURAL_REJECT",
            "freeze_eligible": False,
            "eligibility_scope": "INELIGIBLE_FOR_SELECTION",
        }
        if structural["structural_status"] == "PASS":
            numeric = numeric_physics_audit(
                structural["expression"], trial["task"], machine,
                operator_domain_obligations=structural["operator_domain_obligations"],
                allowed_variables=variables,
            )
            audit["numeric_physics_status"] = numeric["numeric_status"]
            audit["domain_certification_status"] = numeric["domain_certification_status"]
            audit["freeze_eligible"] = numeric["freeze_eligible"]
            audit["eligibility_scope"] = numeric["eligibility_scope"]
            audit["rejection_reasons"].extend(numeric["rejection_reasons"])
            for constraint in numeric["constraints"]:
                physics_rows.append({
                    "candidate_instance_id": instance,
                    "trial_id": trial_id,
                    "campaign_id": campaign_id,
                    "search_phase": search_phase,
                    "variables_json": variables_json,
                    "task": trial["task"],
                    "constraint": constraint["constraint"],
                    "certification_layer": constraint["certification_layer"],
                    "status": constraint["status"],
                    "margin": constraint["margin"],
                    "worst_witness_json": json.dumps(
                        constraint["worst_witness"], ensure_ascii=False,
                        sort_keys=True, separators=(",", ":"),
                    ),
                    "certification_method": constraint.get("certification_method", ""),
                    "boxes_evaluated": constraint.get("boxes_evaluated", ""),
                    "rejection_code": constraint["rejection_code"],
                    "machine_protocol_sha256": protocol_hash,
                    "execution_supplement_sha256": (
                        trial.get("execution_supplement_sha256")
                        or supplement_hash
                    ),
                    "shape_expansion_decision_sha256": trial.get(
                        "shape_expansion_decision_sha256", ""
                    ),
                })
        else:
            for reason in audit["rejection_reasons"]:
                physics_rows.append({
                    "candidate_instance_id": instance,
                    "trial_id": trial_id,
                    "campaign_id": campaign_id,
                    "search_phase": search_phase,
                    "variables_json": variables_json,
                    "task": trial["task"],
                    "constraint": "STRUCTURAL_EXPRESSION_AUDIT",
                    "certification_layer": "SYMBOLIC",
                    "status": "FAIL",
                    "margin": "",
                    "worst_witness_json": "{}",
                    "certification_method": "SYMBOLIC_STRUCTURE_AUDIT",
                    "boxes_evaluated": 0,
                    "rejection_code": reason,
                    "machine_protocol_sha256": protocol_hash,
                    "execution_supplement_sha256": (
                        trial.get("execution_supplement_sha256")
                        or supplement_hash
                    ),
                    "shape_expansion_decision_sha256": trial.get(
                        "shape_expansion_decision_sha256", ""
                    ),
                })
        audit["rejection_reasons"] = sorted(set(audit["rejection_reasons"]))
        audits.append(audit)
    assign_equivalence_families(audits, machine)
    return audits, physics_rows


def _family_rows(audits, protocol_hash, supplement_hash=""):
    rows = []
    for audit in audits:
        rows.append({
            "candidate_instance_id": audit["candidate_instance_id"],
            "trial_id": audit["trial_id"],
            "candidate_rank": audit["candidate_rank"],
            "campaign_id": audit["campaign_id"],
            "search_phase": audit["search_phase"],
            "variables_json": audit["variables_json"],
            "stage": audit["stage"],
            "task": audit["task"],
            "outer_fold_index": audit["outer_fold_index"],
            "inner_fold_index": audit["inner_fold_index"],
            "seed": audit["seed"],
            "raw_expression": audit["raw_expression"],
            "canonical_expression": audit["canonical_expression"],
            "parameter_template_expression": audit["parameter_template_expression"],
            "parameter_initial_values_json": json.dumps(
                audit["parameter_initial_values"], ensure_ascii=False,
                sort_keys=True, separators=(",", ":"),
            ),
            "structure_signature": audit["structure_signature"],
            "reference_scale_signature": audit["reference_scale_signature"],
            "algebraic_family_id": audit["algebraic_family_id"],
            "structure_family_id": audit["structure_family_id"],
            "reference_scale_family_id": audit["reference_scale_family_id"],
            "prediction_family_id": audit["prediction_family_id"],
            "normalized_complexity": audit["normalized_complexity"],
            "raw_ast_complexity": audit["raw_ast_complexity"],
            "raw_ast_depth": audit["raw_ast_depth"],
            "free_constant_count": audit["free_constant_count"],
            "variable_support_json": json.dumps(
                audit["variable_support"], ensure_ascii=False, separators=(",", ":")
            ),
            "structural_status": audit["structural_status"],
            "numeric_physics_status": audit["numeric_physics_status"],
            "domain_certification_status": audit["domain_certification_status"],
            "freeze_eligible": audit["freeze_eligible"],
            "eligibility_scope": audit["eligibility_scope"],
            "rejection_reasons_json": json.dumps(
                audit["rejection_reasons"], ensure_ascii=False, separators=(",", ":")
            ),
            "loss": audit["loss"],
            "score": audit["score"],
            "machine_protocol_sha256": protocol_hash,
            "execution_supplement_sha256": (
                audit.get("execution_supplement_sha256") or supplement_hash
            ),
            "shape_expansion_decision_sha256": audit.get(
                "shape_expansion_decision_sha256", ""
            ),
        })
    return rows


def _validate_raw_campaign_identity(
    trials,
    candidates,
    machine,
    supplement,
    protocol_hash,
    supplement_hash,
    decision_hash="",
):
    """Validate every raw row against the campaign and hash identity."""
    contract = _campaign_contract(machine, supplement)
    trial_by_id = {}
    campaign_counts = defaultdict(int)
    terminal = {"SUCCESS", "FAILED", "TIMEOUT", "FAILED_NO_CANDIDATES", "FAILED_INTERRUPTED"}
    for row in trials:
        trial_id = row.get("trial_id", "")
        if not trial_id or trial_id in trial_by_id:
            raise ValueError("raw trial CSV 的 trial_id 为空或重复")
        campaign_id = row.get("campaign_id", "")
        if campaign_id not in contract:
            raise ValueError(f"{trial_id}: 未知 campaign_id {campaign_id!r}")
        expected_phase, expected_task, expected_variables = contract[campaign_id]
        expected_variables_json = _compact_json_list(expected_variables)
        for column, expected in (
            ("search_phase", expected_phase),
            ("task", expected_task),
            ("variables_json", expected_variables_json),
            ("machine_protocol_sha256", protocol_hash),
            ("execution_supplement_sha256", supplement_hash),
        ):
            actual = str(row.get(column, ""))
            if column.endswith("sha256"):
                actual = actual.lower()
                expected = str(expected).lower()
            if actual != str(expected):
                raise ValueError(f"{trial_id}: raw trial 身份失配: {column}")
        if not trial_id.startswith(f"{campaign_id}__"):
            raise ValueError(f"{trial_id}: trial_id 未绑定 campaign_id")
        row_decision_hash = str(
            row.get("shape_expansion_decision_sha256", "")
        ).lower()
        if expected_phase == "INITIAL" and row_decision_hash:
            raise ValueError(f"{trial_id}: 初始 campaign 不得绑定条件决策哈希")
        if expected_phase == "CONDITIONAL" and row_decision_hash != decision_hash.lower():
            raise ValueError(f"{trial_id}: 条件 campaign 决策哈希失配")
        if row.get("status") not in terminal:
            raise ValueError(f"{trial_id}: raw trial 尚未形成终态")
        trial_by_id[trial_id] = row
        campaign_counts[campaign_id] += 1

    sequence = supplement["shape_search_sequence"]
    budget = supplement["trial_budget"]
    xi_count = int(sequence["phase_1"]["trial_count"])
    expanded_count = int(sequence["phase_2"]["trial_count"])
    initial_count = int(budget["initial_trial_count"])
    expected_counts = {
        "peak_q_d": initial_count - xi_count,
        "shape_xi_only": xi_count,
    }
    for campaign_id, expected_count in expected_counts.items():
        if campaign_counts[campaign_id] != expected_count:
            raise ValueError(
                f"{campaign_id}: raw trial 数量不完整: "
                f"expected={expected_count}, actual={campaign_counts[campaign_id]}"
            )
    conditional_count = campaign_counts["shape_xi_q_d"]
    if conditional_count not in {0, expanded_count}:
        raise ValueError("shape_xi_q_d 条件 campaign 必须为零条或完整预算")
    if conditional_count and not decision_hash:
        raise ValueError("存在条件 campaign，但没有有效的 shape 扩展决策哈希")
    maximum = int(budget["maximum_main_search_trial_count"])
    if len(trials) > maximum:
        raise ValueError("raw trial 数量超过执行补充协议总上限")

    identity_columns = (
        "campaign_id", "search_phase", "variables_json", "stage", "task",
        "outer_fold_index", "outer_holdout_parent_case_id", "inner_fold_index",
        "seed", "machine_protocol_sha256", "execution_supplement_sha256",
        "shape_expansion_decision_sha256",
    )
    seen_candidates = set()
    for row in candidates:
        trial_id = row.get("trial_id", "")
        if trial_id not in trial_by_id:
            raise ValueError(f"候选引用未知 trial_id: {trial_id}")
        key = (trial_id, row.get("candidate_rank", ""))
        if key in seen_candidates:
            raise ValueError(f"Pareto 候选键重复: {trial_id}, rank={key[1]}")
        seen_candidates.add(key)
        trial = trial_by_id[trial_id]
        for column in identity_columns:
            actual = str(row.get(column, ""))
            expected = str(trial.get(column, ""))
            if column.endswith("sha256"):
                actual = actual.lower()
                expected = expected.lower()
            if actual != expected:
                raise ValueError(
                    f"{trial_id}: raw candidate 身份失配: {column}"
                )
    return {
        "conditional_campaign_present": bool(conditional_count),
        "campaign_counts": dict(campaign_counts),
    }


def evaluate(repo=PROJECT_ROOT, preflight_fn=None):
    """Evaluate raw candidates after full preflight and write four audit tables."""
    repo = Path(repo).resolve()
    supplement_context = protocol_guard.load_approved_execution_supplement(repo)
    supplement = supplement_context["record"]
    supplement_hash = supplement_context["sha256"]
    checked = (preflight_fn or protocol_guard.preflight)(repo)
    if checked.get("status") != "READY_FOR_G5_SR_SEARCH":
        raise PermissionError("G5-SR 完整预检未通过，禁止候选评价")
    machine_path = repo / protocol_guard.MACHINE_PROTOCOL
    machine = _read_json(machine_path)
    protocol_hash = protocol_guard.sha256_file(machine_path)
    if str(checked.get("machine_protocol_sha256", "")).lower() != protocol_hash:
        raise PermissionError("G5-SR 评价预检父协议哈希与当前文件不一致")
    if str(
        checked.get("execution_supplement", {}).get("approved_sha256", "")
    ).lower() != supplement_hash:
        raise PermissionError("G5-SR 评价预检补充协议哈希与当前批准版不一致")
    outputs = machine["output_contract"]
    trials, trial_columns = _read_csv(repo / outputs["search_trials"])
    candidates, candidate_columns = _read_csv(repo / outputs["pareto_front"])
    required_trial = {
        "trial_id", "campaign_id", "search_phase", "variables_json", "stage",
        "task", "outer_fold_index", "outer_holdout_parent_case_id",
        "inner_fold_index", "seed", "status", "machine_protocol_sha256",
        "execution_supplement_sha256", "shape_expansion_decision_sha256",
    }
    required_candidate = {
        "trial_id", "candidate_rank", "campaign_id", "search_phase",
        "variables_json", "stage", "task", "outer_fold_index",
        "outer_holdout_parent_case_id", "inner_fold_index", "seed",
        "raw_expression", "machine_protocol_sha256",
        "execution_supplement_sha256", "shape_expansion_decision_sha256",
    }
    if not required_trial <= set(trial_columns):
        raise ValueError("raw trial CSV 缺少评价字段")
    if not required_candidate <= set(candidate_columns):
        raise ValueError("raw Pareto CSV 缺少评价字段")
    if any("lockbox" in str(path).lower() for path in (
        outputs["search_trials"], outputs["pareto_front"]
    )):
        raise PermissionError("评价器拒绝读取 lockbox 路径")
    decision_context = None
    if any(row.get("campaign_id") == "shape_xi_q_d" for row in trials):
        decision_context = protocol_guard.require_shape_expansion_authorized(
            repo,
            machine_protocol_sha256=protocol_hash,
            execution_supplement_sha256=supplement_hash,
            supplement=supplement,
        )
    decision_hash = (
        decision_context["decision_sha256"] if decision_context else ""
    )
    campaign_state = _validate_raw_campaign_identity(
        trials,
        candidates,
        machine,
        supplement,
        protocol_hash,
        supplement_hash,
        decision_hash,
    )

    audits, physics = _audit_candidates(
        trials, candidates, machine, protocol_hash, supplement_hash
    )
    family_rows = _family_rows(audits, protocol_hash, supplement_hash)
    variable_rows = summarize_variable_frequency(trials, audits, machine)
    stability_rows = summarize_stability(trials, audits, machine)
    destinations = {
        "candidate_families": (family_rows, FAMILY_COLUMNS),
        "variable_selection_frequency": (variable_rows, VARIABLE_COLUMNS),
        "crossfold_stability": (stability_rows, STABILITY_COLUMNS),
        "physics_rejections": (physics, PHYSICS_COLUMNS),
    }
    for key in destinations:
        if (repo / outputs[key]).exists():
            raise FileExistsError(f"G5-SR 评价输出已存在，禁止覆盖: {outputs[key]}")
    for key, (rows, columns) in destinations.items():
        _write_csv_atomic(repo / outputs[key], rows, columns)
    numeric_pass = sum(row["numeric_physics_status"] == "PASS" for row in audits)
    domain_certified = sum(
        row["domain_certification_status"] == "DOMAIN_CERTIFIED" for row in audits
    )
    freeze_eligible = sum(bool(row["freeze_eligible"]) for row in audits)
    return {
        "status": "G5_SR_PHYSICS_AUDIT_COMPLETE_NO_FORMULA_SELECTED",
        "machine_protocol_sha256": protocol_hash,
        "execution_supplement_sha256": supplement_hash,
        "shape_expansion_decision_sha256": decision_hash,
        "campaign_counts": campaign_state["campaign_counts"],
        "trial_count_including_failures": len(trials),
        "raw_candidate_count": len(candidates),
        "numeric_physics_pass_count": numeric_pass,
        "domain_certified_candidate_count": domain_certified,
        "freeze_eligible_candidate_count": freeze_eligible,
        "freeze_eligibility_scope": (
            "STRUCTURE_NUMERIC_AND_CONTINUOUS_DOMAIN_PASS_ONLY;"
            "NOT_A_SR_OR_A_ACTIVE_SELECTION_OR_FREEZE"
        ),
        "A_active_created": False,
        "lockbox_used": False,
        "outputs": {key: str(repo / outputs[key]) for key in destinations},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(PROJECT_ROOT))
    args = parser.parse_args()
    print(json.dumps(evaluate(args.repo), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
