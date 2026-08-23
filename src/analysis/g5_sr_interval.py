"""Deterministic continuous-domain certification for G5-SR candidates.

This module is deliberately separate from the fixed Sobol audit.  A PASS is
issued only when interval branch-and-bound (or an exact symbolic identity)
covers the complete closed rectangular support.  Sampling is used nowhere in
the proof.  If interval dependency or a resource limit prevents a proof, the
result is ``DOMAIN_NOT_CERTIFIED`` rather than a numerical PASS.

The implementation uses :mod:`mpmath` interval arithmetic at fixed precision.
All boxes, split choices and limits are deterministic so that the certificate
can be reproduced from the expression and the frozen machine protocol.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import itertools


CERTIFICATION_METHOD = "MPMATH_IV_BRANCH_AND_BOUND_V1"
EXACT_IDENTITY_METHOD = "SYMPY_EXACT_IDENTITY_V1"
MONOTONE_ENDPOINT_METHOD = "MONOTONICITY_AND_ENDPOINT_INTERVAL_V1"
INTERVAL_DPS = 60
DEFAULT_MAX_BOXES = 4096
DEFAULT_MAX_DEPTH = 24


@dataclass(frozen=True)
class _DomainIssue(Exception):
    """An interval cannot currently establish an operator's real domain."""

    reason: str
    subexpression: str
    argument_interval: tuple[str, str]
    definite: bool = False


def _coerce_domain_issue(exception, expression):
    if isinstance(exception, _DomainIssue):
        return exception
    return _DomainIssue(
        f"INTERVAL_EVALUATION_ERROR:{type(exception).__name__}",
        str(expression), ("", ""), False,
    )


def _sympy():
    try:
        import sympy as sp
    except (ImportError, ModuleNotFoundError) as exc:
        raise RuntimeError("G5-SR 连续域认证需要锁定环境中的 SymPy") from exc
    return sp


def _mpmath():
    try:
        import mpmath as mp
    except (ImportError, ModuleNotFoundError) as exc:
        raise RuntimeError("G5-SR 连续域认证需要 mpmath 区间算术") from exc
    return mp


def _endpoint(mp, interval, upper=False):
    """Return an exact mpmath endpoint from an iv.mpf object."""
    return mp.mpf(interval._mpi_[1 if upper else 0])


def _endpoint_text(mp, value):
    if mp.isinf(value):
        return "+inf" if value > 0 else "-inf"
    if mp.isnan(value):
        return "nan"
    return mp.nstr(value, 25)


def _interval_text(mp, value):
    return (
        _endpoint_text(mp, _endpoint(mp, value)),
        _endpoint_text(mp, _endpoint(mp, value, upper=True)),
    )


def _number_interval(expr, mp):
    sp = _sympy()
    if expr in (sp.oo, -sp.oo, sp.zoo, sp.nan) or not expr.is_real:
        raise _DomainIssue("NONFINITE_OR_NONREAL_CONSTANT", str(expr), ("", ""), True)
    if isinstance(expr, sp.Integer):
        return mp.iv.mpf(int(expr))
    if isinstance(expr, sp.Rational):
        return mp.iv.mpf(int(expr.p)) / mp.iv.mpf(int(expr.q))
    # SymPy Float is the candidate constant itself.  Passing its full decimal
    # rendering to iv.mpf encloses that stored value at the active precision.
    return mp.iv.mpf(str(expr.evalf(INTERVAL_DPS)))


def _contains_zero(mp, value):
    return _endpoint(mp, value) <= 0 <= _endpoint(mp, value, upper=True)


def _evaluate_interval(expression, environment, mp):
    """Evaluate a whitelisted SymPy expression and return value plus guards.

    ``guards`` are signed margins for every operator-domain requirement.  A
    strictly-positive guard is positive when proved; a nonnegative guard may
    equal zero.  Raising ``_DomainIssue(definite=False)`` means subdivision may
    remove interval dependency.  ``definite=True`` means the whole box is
    outside the real domain.
    """
    sp = _sympy()
    guards = []

    if expression.is_Number:
        return _number_interval(expression, mp), guards
    if expression.is_Symbol:
        name = str(expression)
        if name not in environment:
            raise _DomainIssue("UNBOUNDED_SYMBOL", name, ("", ""), False)
        return environment[name], guards
    if isinstance(expression, sp.Add):
        value = mp.iv.mpf(0)
        for argument in expression.args:
            child, child_guards = _evaluate_interval(argument, environment, mp)
            value += child
            guards.extend(child_guards)
        return value, guards
    if isinstance(expression, sp.Mul):
        value = mp.iv.mpf(1)
        for argument in expression.args:
            child, child_guards = _evaluate_interval(argument, environment, mp)
            value *= child
            guards.extend(child_guards)
        return value, guards
    if isinstance(expression, sp.Pow):
        base_expr, exponent_expr = expression.args
        base, child_guards = _evaluate_interval(base_expr, environment, mp)
        guards.extend(child_guards)
        if not exponent_expr.is_number or not exponent_expr.is_real:
            raise _DomainIssue(
                "POWER_EXPONENT_NOT_REAL_CONSTANT", str(expression),
                _interval_text(mp, base), True,
            )
        lower = _endpoint(mp, base)
        upper = _endpoint(mp, base, upper=True)
        if exponent_expr.is_integer:
            exponent = int(exponent_expr)
            if exponent < 0:
                if lower == 0 and upper == 0:
                    raise _DomainIssue(
                        "ZERO_IN_NEGATIVE_INTEGER_POWER", str(expression),
                        _interval_text(mp, base), True,
                    )
                if _contains_zero(mp, base):
                    raise _DomainIssue(
                        "ZERO_MAY_OCCUR_IN_NEGATIVE_INTEGER_POWER", str(expression),
                        _interval_text(mp, base), False,
                    )
                guards.append(("NEGATIVE_INTEGER_POWER_BASE_ABS", min(abs(lower), abs(upper))))
            return base ** exponent, guards

        exponent = _number_interval(exponent_expr, mp)
        exponent_lower = _endpoint(mp, exponent)
        exponent_upper = _endpoint(mp, exponent, upper=True)
        if lower < 0:
            if upper < 0:
                definite = True
                reason = "NEGATIVE_BASE_IN_NONINTEGER_POWER"
            else:
                definite = False
                reason = "NONNEGATIVE_BASE_NOT_PROVED_FOR_NONINTEGER_POWER"
            raise _DomainIssue(reason, str(expression), _interval_text(mp, base), definite)
        if exponent_lower < 0 and lower <= 0:
            if upper == 0:
                definite = True
                reason = "ZERO_BASE_IN_NEGATIVE_NONINTEGER_POWER"
            else:
                definite = False
                reason = "POSITIVE_BASE_NOT_PROVED_FOR_NEGATIVE_NONINTEGER_POWER"
            raise _DomainIssue(reason, str(expression), _interval_text(mp, base), definite)
        guards.append((
            "NONINTEGER_POWER_BASE",
            lower if exponent_lower >= 0 else min(abs(lower), abs(upper)),
        ))
        return base ** exponent, guards
    if expression.func == sp.exp:
        argument, child_guards = _evaluate_interval(expression.args[0], environment, mp)
        guards.extend(child_guards)
        return mp.iv.exp(argument), guards
    if expression.func == sp.log:
        argument, child_guards = _evaluate_interval(expression.args[0], environment, mp)
        guards.extend(child_guards)
        lower = _endpoint(mp, argument)
        upper = _endpoint(mp, argument, upper=True)
        if lower <= 0:
            raise _DomainIssue(
                "LOG_ARGUMENT_NOT_PROVED_STRICTLY_POSITIVE",
                str(expression), _interval_text(mp, argument), upper <= 0,
            )
        guards.append(("LOG_ARGUMENT", lower))
        return mp.iv.log(argument), guards
    raise _DomainIssue(
        "UNSUPPORTED_INTERVAL_OPERATOR", str(expression), ("", ""), True
    )


def _support_box(machine, fixed=None):
    support = machine["physical_constraints"]["continuous_support"]
    fixed = fixed or {}
    box = {}
    for name, limits in support.items():
        low, high = map(float, limits)
        if name in fixed:
            low = high = float(fixed[name])
        box[name] = (low, high)
    return box


def _box_json(box):
    return {name: [float(limits[0]), float(limits[1])]
            for name, limits in sorted(box.items())}


def _environment(box, mp):
    return {name: mp.iv.mpf([repr(low), repr(high)])
            for name, (low, high) in box.items()}


def _split_box(box, root_box, active_variables):
    choices = []
    for order, (name, (low, high)) in enumerate(box.items()):
        if name not in active_variables:
            continue
        root_low, root_high = root_box[name]
        root_width = root_high - root_low
        normalized = (high - low) / root_width if root_width > 0 else 0.0
        choices.append((normalized, -order, name))
    if not choices:
        return None
    _, _, name = max(choices)
    low, high = box[name]
    midpoint = low + (high - low) / 2.0
    if midpoint == low or midpoint == high:
        return None
    left = dict(box)
    right = dict(box)
    left[name] = (low, midpoint)
    right[name] = (midpoint, high)
    return left, right


def _point_boxes(root_box, active_variables):
    names = list(root_box)
    axes = []
    for name in names:
        low, high = root_box[name]
        midpoint = low + (high - low) / 2.0
        values = (low, midpoint, high) if name in active_variables else (midpoint,)
        axes.append(tuple(dict.fromkeys(values)))
    for values in itertools.product(*axes):
        yield {name: (value, value) for name, value in zip(names, values)}


def _sympy_point_value(expression, point_box):
    sp = _sympy()
    substitutions = {}
    for symbol in expression.free_symbols:
        name = str(symbol)
        if name in point_box:
            substitutions[symbol] = sp.Float(repr(point_box[name][0]), INTERVAL_DPS)
    try:
        value = sp.simplify(expression.subs(substitutions))
        return sp.N(value, INTERVAL_DPS)
    except Exception as exc:
        return f"SYMPY_POINT_EVALUATION_ERROR:{type(exc).__name__}"


def _exact_point_domain_failure(expression, point_box):
    """Return a counterexample description only when SymPy proves it invalid."""
    sp = _sympy()
    numeric = _sympy_point_value(expression, point_box)
    if isinstance(numeric, str):
        # An evaluator exception is not a mathematical counterexample.  The
        # enclosing branch-and-bound path must remain unresolved/fail closed.
        return None
    if numeric.has(sp.nan, sp.zoo, sp.oo, -sp.oo):
        return "SYMPY_PROVED_NONFINITE_POINT_VALUE"
    if numeric.is_real is False:
        return "SYMPY_PROVED_NONREAL_POINT_VALUE"
    if numeric.is_finite is False:
        return "SYMPY_PROVED_NONFINITE_POINT_VALUE"
    return None


def _exact_point_relation_failure(expression, point_box, relation):
    """Return exact-point evidence only when the requested sign is violated."""
    numeric = _sympy_point_value(expression, point_box)
    if isinstance(numeric, str):
        return None
    if relation == "GT_ZERO" and numeric.is_nonpositive is True:
        return str(numeric)
    if relation == "GE_ZERO" and numeric.is_negative is True:
        return str(numeric)
    if relation == "LE_ZERO" and numeric.is_positive is True:
        return str(numeric)
    if relation == "NE_ZERO" and numeric.is_zero is True:
        return str(numeric)
    return None


def _bound_margin(mp, interval, relation):
    lower = _endpoint(mp, interval)
    upper = _endpoint(mp, interval, upper=True)
    if relation in ("GE_ZERO", "GT_ZERO"):
        return lower, lower, upper
    if relation == "LE_ZERO":
        return -upper, lower, upper
    if relation == "NE_ZERO":
        if lower > 0:
            return lower, lower, upper
        if upper < 0:
            return -upper, lower, upper
        return mp.mpf(0), lower, upper
    raise ValueError(f"未知区间关系: {relation}")


def _relation_passes(margin, relation):
    return margin > 0 if relation in ("GT_ZERO", "NE_ZERO") else margin >= 0


def _relation_definitely_fails(lower, upper, relation):
    if relation == "GT_ZERO":
        return upper <= 0
    if relation == "GE_ZERO":
        return upper < 0
    if relation == "LE_ZERO":
        return lower > 0
    if relation == "NE_ZERO":
        return lower == 0 and upper == 0
    raise ValueError(f"未知区间关系: {relation}")


def _base_evidence(boxes_evaluated, max_boxes, max_depth):
    return {
        "method": CERTIFICATION_METHOD,
        "mpmath_version": _mpmath().__version__,
        "interval_dps": INTERVAL_DPS,
        "boxes_evaluated": boxes_evaluated,
        "box_limit": max_boxes,
        "depth_limit": max_depth,
    }


def _interval_bound_proof(
    expression,
    machine,
    relation,
    *,
    fixed=None,
    max_boxes=DEFAULT_MAX_BOXES,
    max_depth=DEFAULT_MAX_DEPTH,
):
    """Prove a signed bound over the complete support using interval boxes."""
    mp = _mpmath()
    root_box = _support_box(machine, fixed=fixed)
    active_variables = {str(symbol) for symbol in expression.free_symbols}
    old_iv_dps = mp.iv.dps
    old_mp_dps = mp.mp.dps
    mp.iv.dps = INTERVAL_DPS
    mp.mp.dps = max(old_mp_dps, INTERVAL_DPS)
    boxes_evaluated = 0
    worst_margin = mp.inf
    worst_box = root_box
    worst_interval = ("", "")
    try:
        # Exact, deterministic points only find counterexamples.  They never
        # contribute to a PASS certificate.
        for point_box in _point_boxes(root_box, active_variables):
            try:
                value, _ = _evaluate_interval(expression, _environment(point_box, mp), mp)
            except Exception as caught:
                issue = _coerce_domain_issue(caught, expression)
                exact_reason = _exact_point_domain_failure(expression, point_box)
                if exact_reason is not None:
                    evidence = _base_evidence(boxes_evaluated, max_boxes, max_depth)
                    evidence.update({
                        "reason": exact_reason,
                        "interval_guard_reason": issue.reason,
                        "subexpression": issue.subexpression,
                        "argument_interval": list(issue.argument_interval),
                        "counterexample_box": _box_json(point_box),
                    })
                    return {
                        "status": "FAIL", "margin": "", "evidence": evidence,
                        "boxes_evaluated": boxes_evaluated,
                    }
                continue
            lower = _endpoint(mp, value)
            upper = _endpoint(mp, value, upper=True)
            if not mp.isfinite(lower) or not mp.isfinite(upper):
                exact_reason = _exact_point_domain_failure(expression, point_box)
                if exact_reason is not None:
                    evidence = _base_evidence(boxes_evaluated, max_boxes, max_depth)
                    evidence.update({
                        "reason": exact_reason,
                        "counterexample_box": _box_json(point_box),
                        "value_interval": list(_interval_text(mp, value)),
                    })
                    return {
                        "status": "FAIL", "margin": "", "evidence": evidence,
                        "boxes_evaluated": boxes_evaluated,
                    }
                continue
            margin, lower, upper = _bound_margin(mp, value, relation)
            if _relation_definitely_fails(lower, upper, relation):
                evidence = _base_evidence(boxes_evaluated, max_boxes, max_depth)
                evidence.update({
                    "reason": "EXACT_POINT_COUNTEREXAMPLE",
                    "counterexample_box": _box_json(point_box),
                    "value_interval": list(_interval_text(mp, value)),
                })
                return {
                    "status": "FAIL", "margin": _endpoint_text(mp, margin),
                    "evidence": evidence, "boxes_evaluated": boxes_evaluated,
                }
            if not _relation_passes(margin, relation):
                exact_value = _exact_point_relation_failure(
                    expression, point_box, relation
                )
                if exact_value is not None:
                    evidence = _base_evidence(boxes_evaluated, max_boxes, max_depth)
                    evidence.update({
                        "reason": "SYMPY_EXACT_POINT_COUNTEREXAMPLE",
                        "counterexample_box": _box_json(point_box),
                        "exact_point_value": exact_value,
                        "value_interval": list(_interval_text(mp, value)),
                    })
                    return {
                        "status": "FAIL", "margin": _endpoint_text(mp, margin),
                        "evidence": evidence, "boxes_evaluated": boxes_evaluated,
                    }

        pending = deque([(root_box, 0)])
        while pending:
            if boxes_evaluated >= max_boxes:
                box, depth = pending[0]
                evidence = _base_evidence(boxes_evaluated, max_boxes, max_depth)
                evidence.update({
                    "reason": "BOX_LIMIT_REACHED",
                    "unresolved_box": _box_json(box), "unresolved_depth": depth,
                    "worst_certified_box": _box_json(worst_box),
                    "worst_certified_interval": list(worst_interval),
                })
                return {
                    "status": "NOT_CERTIFIED", "margin": "",
                    "evidence": evidence, "boxes_evaluated": boxes_evaluated,
                }
            box, depth = pending.popleft()
            boxes_evaluated += 1
            try:
                value, _ = _evaluate_interval(expression, _environment(box, mp), mp)
                lower = _endpoint(mp, value)
                upper = _endpoint(mp, value, upper=True)
                if not mp.isfinite(lower) or not mp.isfinite(upper):
                    raise _DomainIssue(
                        "NONFINITE_INTERVAL_VALUE", str(expression),
                        _interval_text(mp, value), False,
                    )
                margin, lower, upper = _bound_margin(mp, value, relation)
                if _relation_passes(margin, relation):
                    if margin < worst_margin:
                        worst_margin = margin
                        worst_box = box
                        worst_interval = _interval_text(mp, value)
                    continue
                if _relation_definitely_fails(lower, upper, relation):
                    evidence = _base_evidence(boxes_evaluated, max_boxes, max_depth)
                    evidence.update({
                        "reason": "INTERVAL_COUNTERREGION",
                        "counterexample_box": _box_json(box),
                        "value_interval": list(_interval_text(mp, value)),
                    })
                    return {
                        "status": "FAIL", "margin": _endpoint_text(mp, margin),
                        "evidence": evidence, "boxes_evaluated": boxes_evaluated,
                    }
                issue = None
            except Exception as caught:
                issue = _coerce_domain_issue(caught, expression)
                if issue.definite:
                    evidence = _base_evidence(boxes_evaluated, max_boxes, max_depth)
                    evidence.update({
                        "reason": issue.reason,
                        "subexpression": issue.subexpression,
                        "argument_interval": list(issue.argument_interval),
                        "counterexample_box": _box_json(box),
                    })
                    return {
                        "status": "FAIL", "margin": "",
                        "evidence": evidence, "boxes_evaluated": boxes_evaluated,
                    }

            if depth >= max_depth:
                evidence = _base_evidence(boxes_evaluated, max_boxes, max_depth)
                evidence.update({
                    "reason": "DEPTH_LIMIT_REACHED",
                    "unresolved_box": _box_json(box),
                })
                if issue is not None:
                    evidence.update({
                        "operator_guard_reason": issue.reason,
                        "subexpression": issue.subexpression,
                        "argument_interval": list(issue.argument_interval),
                    })
                return {
                    "status": "NOT_CERTIFIED", "margin": "",
                    "evidence": evidence, "boxes_evaluated": boxes_evaluated,
                }
            children = _split_box(box, root_box, active_variables)
            if children is None:
                evidence = _base_evidence(boxes_evaluated, max_boxes, max_depth)
                evidence.update({
                    "reason": "BOX_CANNOT_BE_SPLIT_FURTHER",
                    "unresolved_box": _box_json(box),
                })
                return {
                    "status": "NOT_CERTIFIED", "margin": "",
                    "evidence": evidence, "boxes_evaluated": boxes_evaluated,
                }
            pending.extend((child, depth + 1) for child in children)

        evidence = _base_evidence(boxes_evaluated, max_boxes, max_depth)
        evidence.update({
            "covered_support": _box_json(root_box),
            "worst_box": _box_json(worst_box),
            "worst_interval": list(worst_interval),
            "bound_relation": relation,
        })
        margin = _endpoint_text(mp, worst_margin) if worst_margin != mp.inf else ""
        return {
            "status": "PASS", "margin": margin, "evidence": evidence,
            "boxes_evaluated": boxes_evaluated,
        }
    finally:
        mp.iv.dps = old_iv_dps
        mp.mp.dps = old_mp_dps


def _domain_proof(
    expression,
    machine,
    *,
    max_boxes=DEFAULT_MAX_BOXES,
    max_depth=DEFAULT_MAX_DEPTH,
):
    """Prove that every operator and final value is real and finite."""
    mp = _mpmath()
    root_box = _support_box(machine)
    active_variables = {str(symbol) for symbol in expression.free_symbols}
    old_iv_dps = mp.iv.dps
    old_mp_dps = mp.mp.dps
    mp.iv.dps = INTERVAL_DPS
    mp.mp.dps = max(old_mp_dps, INTERVAL_DPS)
    boxes_evaluated = 0
    worst_guard = mp.inf
    worst_box = root_box
    try:
        for point_box in _point_boxes(root_box, active_variables):
            try:
                value, _ = _evaluate_interval(expression, _environment(point_box, mp), mp)
                lower = _endpoint(mp, value)
                upper = _endpoint(mp, value, upper=True)
                if not mp.isfinite(lower) or not mp.isfinite(upper):
                    raise _DomainIssue(
                        "NONFINITE_POINT_VALUE", str(expression),
                        _interval_text(mp, value), True,
                    )
            except Exception as caught:
                issue = _coerce_domain_issue(caught, expression)
                exact_reason = _exact_point_domain_failure(expression, point_box)
                if exact_reason is not None:
                    evidence = _base_evidence(boxes_evaluated, max_boxes, max_depth)
                    evidence.update({
                        "reason": exact_reason,
                        "interval_guard_reason": issue.reason,
                        "subexpression": issue.subexpression,
                        "argument_interval": list(issue.argument_interval),
                        "counterexample_box": _box_json(point_box),
                    })
                    return {
                        "status": "FAIL", "margin": "", "evidence": evidence,
                        "boxes_evaluated": boxes_evaluated,
                    }
                continue

        pending = deque([(root_box, 0)])
        while pending:
            if boxes_evaluated >= max_boxes:
                box, depth = pending[0]
                evidence = _base_evidence(boxes_evaluated, max_boxes, max_depth)
                evidence.update({
                    "reason": "BOX_LIMIT_REACHED",
                    "unresolved_box": _box_json(box), "unresolved_depth": depth,
                })
                return {
                    "status": "NOT_CERTIFIED", "margin": "",
                    "evidence": evidence, "boxes_evaluated": boxes_evaluated,
                }
            box, depth = pending.popleft()
            boxes_evaluated += 1
            try:
                value, guards = _evaluate_interval(expression, _environment(box, mp), mp)
                lower = _endpoint(mp, value)
                upper = _endpoint(mp, value, upper=True)
                if not mp.isfinite(lower) or not mp.isfinite(upper):
                    raise _DomainIssue(
                        "NONFINITE_INTERVAL_VALUE", str(expression),
                        _interval_text(mp, value), False,
                    )
                if guards:
                    local_guard = min(margin for _, margin in guards)
                    if local_guard < worst_guard:
                        worst_guard = local_guard
                        worst_box = box
                continue
            except Exception as caught:
                issue = _coerce_domain_issue(caught, expression)
                if issue.definite:
                    evidence = _base_evidence(boxes_evaluated, max_boxes, max_depth)
                    evidence.update({
                        "reason": issue.reason,
                        "subexpression": issue.subexpression,
                        "argument_interval": list(issue.argument_interval),
                        "counterexample_box": _box_json(box),
                    })
                    return {
                        "status": "FAIL", "margin": "",
                        "evidence": evidence, "boxes_evaluated": boxes_evaluated,
                    }
            if depth >= max_depth:
                evidence = _base_evidence(boxes_evaluated, max_boxes, max_depth)
                evidence.update({
                    "reason": "DEPTH_LIMIT_REACHED",
                    "unresolved_box": _box_json(box),
                    "operator_guard_reason": issue.reason,
                    "subexpression": issue.subexpression,
                    "argument_interval": list(issue.argument_interval),
                })
                return {
                    "status": "NOT_CERTIFIED", "margin": "",
                    "evidence": evidence, "boxes_evaluated": boxes_evaluated,
                }
            children = _split_box(box, root_box, active_variables)
            if children is None:
                evidence = _base_evidence(boxes_evaluated, max_boxes, max_depth)
                evidence.update({
                    "reason": "BOX_CANNOT_BE_SPLIT_FURTHER",
                    "unresolved_box": _box_json(box),
                })
                return {
                    "status": "NOT_CERTIFIED", "margin": "",
                    "evidence": evidence, "boxes_evaluated": boxes_evaluated,
                }
            pending.extend((child, depth + 1) for child in children)

        evidence = _base_evidence(boxes_evaluated, max_boxes, max_depth)
        evidence.update({
            "covered_support": _box_json(root_box),
            "worst_guard_box": _box_json(worst_box),
            "worst_operator_guard": (
                _endpoint_text(mp, worst_guard) if worst_guard != mp.inf else "not_applicable"
            ),
        })
        margin = _endpoint_text(mp, worst_guard) if worst_guard != mp.inf else ""
        return {
            "status": "PASS", "margin": margin, "evidence": evidence,
            "boxes_evaluated": boxes_evaluated,
        }
    finally:
        mp.iv.dps = old_iv_dps
        mp.mp.dps = old_mp_dps


def _identity_at_zero_proof(
    expression,
    machine,
    *,
    max_boxes=DEFAULT_MAX_BOXES,
    max_depth=DEFAULT_MAX_DEPTH,
):
    sp = _sympy()
    xi_symbol = next((symbol for symbol in expression.free_symbols
                      if str(symbol) == "xi"), sp.Symbol("xi", nonnegative=True))
    difference = sp.factor(sp.cancel(sp.together(expression.subs(xi_symbol, 0) - 1)))
    if difference == 0 or difference.is_zero is True:
        return {
            "status": "PASS", "margin": "0", "boxes_evaluated": 0,
            "evidence": {
                "method": EXACT_IDENTITY_METHOD,
                "identity": "Theta(xi=0,q,d)-1 == 0",
                "simplified_difference": "0",
            },
        }

    # A single exact nonzero point refutes an identity.  These points never
    # establish PASS; only symbolic reduction to exact zero does that.
    root_box = _support_box(machine, fixed={"xi": 0.0})
    active_variables = {str(symbol) for symbol in difference.free_symbols}
    points_checked = 0
    for point_box in _point_boxes(root_box, active_variables):
        points_checked += 1
        numeric = _sympy_point_value(difference, point_box)
        if isinstance(numeric, str):
            continue
        if (numeric.is_real is True and numeric.is_finite is True
                and numeric.is_zero is False):
            return {
                "status": "FAIL", "margin": str(-abs(numeric)),
                "boxes_evaluated": 0,
                "evidence": {
                    "method": EXACT_IDENTITY_METHOD,
                    "reason": "EXACT_POINT_REFUTES_IDENTITY",
                    "simplified_difference": sp.sstr(difference, order="lex"),
                    "counterexample_box": _box_json(point_box),
                    "exact_point_value": str(numeric),
                    "exact_points_checked": points_checked,
                },
            }
    evidence = {
        "method": EXACT_IDENTITY_METHOD,
        "reason": "SYMBOLIC_IDENTITY_NOT_PROVED",
        "simplified_difference": sp.sstr(difference, order="lex"),
        "exact_points_checked": points_checked,
        "box_limit_not_used_for_identity": max_boxes,
        "depth_limit_not_used_for_identity": max_depth,
    }
    return {
        "status": "NOT_CERTIFIED", "margin": "",
        "boxes_evaluated": 0,
        "evidence": evidence,
    }


def _constraint_row(
    name, proof, violation_code, *, certification_layer="CONTINUOUS_DOMAIN_INTERVAL",
):
    status = proof["status"]
    return {
        "constraint": name,
        "certification_layer": certification_layer,
        "status": status,
        "margin": proof["margin"],
        "worst_witness": proof["evidence"],
        "rejection_code": (
            "" if status == "PASS"
            else violation_code if status == "FAIL"
            else "DOMAIN_NOT_CERTIFIED"
        ),
        "certification_method": proof["evidence"].get("method", CERTIFICATION_METHOD),
        "boxes_evaluated": proof["boxes_evaluated"],
    }


_RAW_OPERATOR_CODES = {
    "DIVISION": (
        "RAW_DIVISION_DENOMINATOR_NONZERO",
        "RAW_DIVISION_DENOMINATOR_ZERO_OR_UNCERTIFIED",
    ),
    "NEGATIVE_INTEGER_POWER": (
        "RAW_NEGATIVE_INTEGER_POWER_BASE_NONZERO",
        "RAW_NEGATIVE_INTEGER_POWER_BASE_ZERO_OR_UNCERTIFIED",
    ),
    "LOG": (
        "RAW_LOG_ARGUMENT_STRICTLY_POSITIVE",
        "RAW_LOG_ARGUMENT_NOT_STRICTLY_POSITIVE",
    ),
    "SQRT": (
        "RAW_SQRT_ARGUMENT_NONNEGATIVE",
        "RAW_SQRT_ARGUMENT_NEGATIVE",
    ),
    "NONINTEGER_POWER": (
        "RAW_NONINTEGER_POWER_BASE_DOMAIN",
        "RAW_NONINTEGER_POWER_BASE_OUTSIDE_DOMAIN",
    ),
    "POW_POSITIVE_BASE": (
        "RAW_POW_POSITIVE_BASE_STRICTLY_POSITIVE",
        "RAW_POW_POSITIVE_BASE_NOT_STRICTLY_POSITIVE",
    ),
}


def _raw_operator_domain_rows(
    obligations, machine, *, max_boxes=DEFAULT_MAX_BOXES, max_depth=DEFAULT_MAX_DEPTH,
):
    """Certify guards retained from the raw operational AST.

    Canonical algebra may remove a division or another undefined child.  These
    obligations therefore must be proved independently of the simplified
    expression used for family identity and physical-shape bounds.
    """
    rows = []
    for index, obligation in enumerate(obligations or (), start=1):
        operator = obligation["operator"]
        relation = obligation["relation"]
        expression = obligation["argument_expression"]
        proof = _interval_bound_proof(
            expression, machine, relation,
            max_boxes=max_boxes, max_depth=max_depth,
        )
        proof["evidence"].update({
            "raw_operator": operator,
            "raw_subexpression": obligation.get("raw_subexpression", ""),
            "guard_expression": obligation.get("argument_text", str(expression)),
            "guard_relation": relation,
        })
        name, violation = _RAW_OPERATOR_CODES[operator]
        rows.append(_constraint_row(
            f"{name}_{index:04d}", proof, violation,
            certification_layer="RAW_OPERATOR_DOMAIN_INTERVAL",
        ))
    return rows


def certify_continuous_domain(
    expression,
    task,
    machine,
    *,
    operator_domain_obligations=None,
    max_boxes=DEFAULT_MAX_BOXES,
    max_depth=DEFAULT_MAX_DEPTH,
):
    """Certify all frozen continuous-domain constraints for one candidate.

    ``freeze_eligible`` means only that the candidate may enter subsequent
    out-of-fold selection/refit gates.  It does not select or freeze A_SR or
    A_active.
    """
    sp = _sympy()
    constraints = []
    constraints.extend(_raw_operator_domain_rows(
        operator_domain_obligations, machine,
        max_boxes=max_boxes, max_depth=max_depth,
    ))
    domain = _domain_proof(
        expression, machine, max_boxes=max_boxes, max_depth=max_depth
    )
    constraints.append(_constraint_row(
        "REAL_FINITE_NO_SINGULARITY_ON_SUPPORT", domain,
        "REAL_FINITE_OR_SINGULARITY_VIOLATION",
    ))

    if task == "peak":
        positivity = _interval_bound_proof(
            expression, machine, "GT_ZERO",
            max_boxes=max_boxes, max_depth=max_depth,
        )
        constraints.append(_constraint_row(
            "F_T_STRICTLY_POSITIVE", positivity,
            "F_T_NOT_STRICTLY_POSITIVE_CONTINUOUS_DOMAIN",
        ))
        q_symbol = next((symbol for symbol in expression.free_symbols
                         if str(symbol) == "q"), sp.Symbol("q", positive=True))
        derivative = sp.factor(sp.cancel(sp.together(sp.diff(expression, q_symbol))))
        monotonic = _interval_bound_proof(
            derivative, machine, "GE_ZERO",
            max_boxes=max_boxes, max_depth=max_depth,
        )
        monotonic["evidence"]["certified_expression"] = sp.sstr(derivative, order="lex")
        constraints.append(_constraint_row(
            "PEAK_NONDECREASING_WITH_Q", monotonic,
            "PEAK_DECREASES_WITH_Q_ON_CONTINUOUS_DOMAIN",
        ))
    elif task == "shape":
        normalization = _identity_at_zero_proof(
            expression, machine, max_boxes=max_boxes, max_depth=max_depth,
        )
        xi_symbol = next((symbol for symbol in expression.free_symbols
                          if str(symbol) == "xi"), sp.Symbol("xi", nonnegative=True))
        negative_derivative = sp.factor(sp.cancel(sp.together(
            -sp.diff(expression, xi_symbol)
        )))
        monotonic = _interval_bound_proof(
            negative_derivative, machine, "GE_ZERO",
            max_boxes=max_boxes, max_depth=max_depth,
        )
        monotonic["evidence"]["certified_expression"] = sp.sstr(
            negative_derivative, order="lex"
        )

        if monotonic["status"] == "PASS":
            xi_high = float(
                machine["physical_constraints"]["continuous_support"]["xi"][1]
            )
            endpoint_expression = sp.factor(sp.cancel(sp.together(
                expression.subs(xi_symbol, sp.Float(repr(xi_high), INTERVAL_DPS))
            )))
            endpoint = _interval_bound_proof(
                endpoint_expression, machine, "GE_ZERO", fixed={"xi": xi_high},
                max_boxes=max_boxes, max_depth=max_depth,
            )
            if endpoint["status"] == "PASS":
                nonnegative = {
                    "status": "PASS", "margin": endpoint["margin"],
                    "boxes_evaluated": (
                        monotonic["boxes_evaluated"] + endpoint["boxes_evaluated"]
                    ),
                    "evidence": {
                        "method": MONOTONE_ENDPOINT_METHOD,
                        "theorem": (
                            "dTheta/dxi<=0 and Theta(xi_max,q,d)>=0 imply "
                            "Theta>=0 on the full support"
                        ),
                        "xi_max": xi_high,
                        "endpoint_expression": sp.sstr(
                            endpoint_expression, order="lex"
                        ),
                        "monotonicity_proof": monotonic["evidence"],
                        "endpoint_proof": endpoint["evidence"],
                    },
                }
            else:
                nonnegative = endpoint
        else:
            nonnegative = _interval_bound_proof(
                expression, machine, "GE_ZERO",
                max_boxes=max_boxes, max_depth=max_depth,
            )

        if normalization["status"] == "PASS" and monotonic["status"] == "PASS":
            upper = {
                "status": "PASS", "margin": "0",
                "boxes_evaluated": monotonic["boxes_evaluated"],
                "evidence": {
                    "method": MONOTONE_ENDPOINT_METHOD,
                    "theorem": (
                        "Theta(0,q,d)=1 and dTheta/dxi<=0 imply Theta<=1 "
                        "on the full support"
                    ),
                    "normalization_proof": normalization["evidence"],
                    "monotonicity_proof": monotonic["evidence"],
                },
            }
        else:
            upper_expression = sp.factor(sp.cancel(sp.together(1 - expression)))
            upper = _interval_bound_proof(
                upper_expression, machine, "GE_ZERO",
                max_boxes=max_boxes, max_depth=max_depth,
            )
            upper["evidence"]["certified_expression"] = sp.sstr(
                upper_expression, order="lex"
            )

        constraints.append(_constraint_row(
            "THETA_AT_ZERO_EQUALS_ONE", normalization,
            "THETA_ZERO_IDENTITY_VIOLATION",
        ))
        constraints.append(_constraint_row(
            "THETA_NONNEGATIVE", nonnegative,
            "THETA_BELOW_ZERO_ON_CONTINUOUS_DOMAIN",
        ))
        constraints.append(_constraint_row(
            "THETA_NOT_ABOVE_ONE", upper,
            "THETA_ABOVE_ONE_ON_CONTINUOUS_DOMAIN",
        ))
        constraints.append(_constraint_row(
            "THETA_NONINCREASING_WITH_XI", monotonic,
            "THETA_INCREASES_WITH_XI_ON_CONTINUOUS_DOMAIN",
        ))
    else:
        raise ValueError(f"未知符号回归任务: {task}")

    statuses = {row["status"] for row in constraints}
    if "FAIL" in statuses:
        certification_status = "DOMAIN_CONSTRAINT_VIOLATION"
    elif "NOT_CERTIFIED" in statuses:
        certification_status = "DOMAIN_NOT_CERTIFIED"
    else:
        certification_status = "DOMAIN_CERTIFIED"
    rejection_reasons = sorted({
        row["rejection_code"] for row in constraints if row["rejection_code"]
    })
    return {
        "domain_certification_status": certification_status,
        "freeze_eligible": certification_status == "DOMAIN_CERTIFIED",
        "eligibility_scope": (
            "ELIGIBLE_FOR_OUTER_SELECTION_AND_REFIT_ONLY_NOT_SELECTED_OR_FROZEN"
            if certification_status == "DOMAIN_CERTIFIED"
            else "INELIGIBLE_FOR_SELECTION"
        ),
        "rejection_reasons": rejection_reasons,
        "constraints": constraints,
        "total_boxes_evaluated": sum(row["boxes_evaluated"] for row in constraints),
    }
