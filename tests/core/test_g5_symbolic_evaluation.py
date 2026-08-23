import csv
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import sympy as sp

from src.analysis import evaluate_g5_symbolic_search as evaluation
from src.analysis import g5_sr_protocol as protocol_guard


def _write_csv(path, rows, columns=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = columns or list(rows[0])
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _machine(sobol_points=64):
    return {
        "schema_version": "1.0",
        "protocol_id": "g5_symbolic_regression_v1",
        "status": "SR_PROTOCOL_APPROVED",
        "execution_state": "APPROVED_NOT_RUN",
        "approval": {"decision": "SR_PROTOCOL_APPROVED"},
        "tasks": {
            "peak": {
                "target": "F_T", "variables": ["q", "d"],
                "max_complexity": 12, "max_depth": 4,
                "max_free_constants": 3,
            },
            "shape": {
                "target": "Theta", "variables": ["xi", "q", "d"],
                "max_complexity": 16, "max_depth": 5,
                "max_free_constants": 4,
            },
        },
        "operator_contract": {
            "costs": {
                "variable": 1, "constant": 1, "add": 1, "subtract": 1,
                "multiply": 1, "divide": 2, "square": 2, "sqrt": 2,
                "exp": 3, "log": 3, "pow_positive_base": 3,
            },
            "allowed_binary": [
                "add", "subtract", "multiply", "divide", "pow_positive_base",
            ],
            "allowed_unary": ["square", "sqrt", "exp", "log"],
            "pow_positive_base_constant_exponent_range": [-3.0, 3.0],
        },
        "physical_constraints": {
            "continuous_support": {
                "q": [0.66, 1.34], "d": [0.77, 1.12], "xi": [0.0, 5.0]
            },
            "sobol_seed": 12345,
            "sobol_points": sobol_points,
        },
        "equivalence_contract": {
            "prediction_equivalence": {
                "rms_fraction_of_project_temperature_scale_max": 0.01,
                "max_fraction_of_project_temperature_scale_max": 0.03,
            }
        },
        "stability_gate": {
            "equivalence_family_outer_folds_min": 7,
            "core_variable_support_final_seeds_min": 16,
        },
        "output_contract": {
            "search_trials": "reports/g5_sr_search_trials.csv",
            "pareto_front": "reports/g5_sr_pareto_front.csv",
            "candidate_families": "reports/g5_sr_candidate_families.csv",
            "variable_selection_frequency": "reports/g5_sr_variable_selection_frequency.csv",
            "crossfold_stability": "reports/g5_sr_crossfold_stability.csv",
            "physics_rejections": "reports/g5_sr_physics_rejections.csv",
        },
    }


def _approve_execution_supplement(repo, machine, *, peak_count, xi_count):
    machine_path = repo / protocol_guard.MACHINE_PROTOCOL
    machine_hash = protocol_guard.sha256_file(machine_path)
    supplement = {
        "schema_version": "1.0",
        "supplement_id": "g5_sr_execution_supplement_v1",
        "status": protocol_guard.APPROVED_EXECUTION_SUPPLEMENT_STATUS,
        "execution_state": protocol_guard.APPROVED_EXECUTION_SUPPLEMENT_STATE,
        "approval": {
            "decision": protocol_guard.APPROVED_EXECUTION_SUPPLEMENT_STATUS,
            "approved_by": "USER",
            "approved_date": "2026-08-23",
        },
        "parent_protocol": {
            "relative_path": protocol_guard.MACHINE_PROTOCOL.as_posix(),
            "protocol_id": machine["protocol_id"],
            "required_status": "SR_PROTOCOL_APPROVED",
            "sha256": machine_hash,
        },
        "shape_search_sequence": {
            "phase_1": {
                "campaign_id": "shape_xi_only",
                "variables": ["xi"],
                "required_first": True,
                "trial_count": xi_count,
            },
            "expansion_trigger": {
                "logic": "OR",
                "conditions": [
                    {"condition_id": "XI_ONLY_Q_OR_D_SYSTEMATIC_RESIDUAL_FAIL"},
                    {"condition_id": "XI_ONLY_ABSOLUTE_SHAPE_GATE_FAIL"},
                ],
                "decision_artifact_required_before_phase_2": (
                    "reports/g5_sr_shape_expansion_decision.json"
                ),
            },
            "phase_2": {
                "campaign_id": "shape_xi_q_d",
                "variables": ["xi", "q", "d"],
                "conditional": True,
                "trial_count": xi_count,
                "launch_requires_trigger_pass": True,
            },
        },
        "trial_budget": {
            "initial_campaigns": ["peak_q_d", "shape_xi_only"],
            "initial_trial_count": peak_count + xi_count,
            "conditional_campaign": "shape_xi_q_d",
            "maximum_main_search_trial_count": peak_count + 2 * xi_count,
        },
    }
    supplement_path = repo / protocol_guard.EXECUTION_SUPPLEMENT
    supplement_path.write_text(json.dumps(supplement), encoding="utf-8")
    supplement_hash = protocol_guard.sha256_file(supplement_path)
    study = {
        "g5_symbolic_regression": {
            "status": "SR_PROTOCOL_APPROVED",
            "machine_protocol": protocol_guard.MACHINE_PROTOCOL.as_posix(),
            "machine_protocol_sha256": machine_hash,
            "execution_supplement": {
                "status": protocol_guard.APPROVED_EXECUTION_SUPPLEMENT_STATUS,
                "draft_path": protocol_guard.EXECUTION_SUPPLEMENT_DRAFT.as_posix(),
                "approved_path": protocol_guard.EXECUTION_SUPPLEMENT.as_posix(),
                "approved_sha256": supplement_hash,
            },
        }
    }
    (repo / protocol_guard.STUDY_PROTOCOL).write_text(
        json.dumps(study), encoding="utf-8"
    )
    return machine_hash, supplement_hash


class G5SymbolicEvaluationTests(unittest.TestCase):
    def test_whitelist_normalization_complexity_and_constant_audit(self):
        machine = _machine()
        audit = evaluation.audit_expression("q + q", "peak", machine)
        self.assertEqual("PASS", audit["structural_status"])
        self.assertEqual("2*q", audit["canonical_expression"])
        self.assertEqual(["q"], audit["variable_support"])
        self.assertGreaterEqual(audit["normalized_complexity"], 1)
        # The 2 is produced by q+q simplification, not a fitted constant.
        self.assertEqual(0, audit["free_constant_count"])

        for expression in ("square(q)", "sqrt(q)", "q**2", "q**0.5"):
            fixed_operator = evaluation.audit_expression(expression, "peak", machine)
            self.assertEqual("PASS", fixed_operator["structural_status"])
            self.assertEqual(0, fixed_operator["free_constant_count"])
        fitted = evaluation.audit_expression(
            "pow_positive_base(q, 0.8) / sqrt(d)", "peak", machine
        )
        self.assertEqual(1, fitted["free_constant_count"])
        self.assertEqual("q**p0/sqrt(d)", fitted["parameter_template_expression"])
        self.assertEqual(
            [{"name": "p0", "initial_value": 0.8, "role": "fitted_exponent"}],
            fitted["parameter_initial_values"],
        )
        fitted_square = evaluation.audit_expression(
            "pow_positive_base(q, 2)", "peak", machine
        )
        self.assertEqual(1, fitted_square["free_constant_count"])

        with self.assertRaises(ValueError):
            evaluation.parse_whitelisted_expression(
                "__import__('os').system('echo unsafe')", ["q", "d"]
            )
        rejected = evaluation.audit_expression("sin(q)", "peak", machine)
        self.assertEqual("FAIL", rejected["structural_status"])
        self.assertTrue(any(reason.startswith("PARSE_OR_AUDIT_FAILED")
                            for reason in rejected["rejection_reasons"]))
        bad_power = evaluation.audit_expression("q**4", "peak", machine)
        self.assertIn("POWER_EXPONENT_OUT_OF_RANGE", bad_power["rejection_reasons"])

        xi_only_leak = evaluation.audit_expression(
            "1/(1+xi+q)", "shape", machine, allowed_variables=["xi"]
        )
        self.assertEqual("FAIL", xi_only_leak["structural_status"])
        self.assertTrue(any(
            reason.startswith("PARSE_OR_AUDIT_FAILED")
            for reason in xi_only_leak["rejection_reasons"]
        ))

    def test_shape_campaign_frequency_denominators_are_isolated(self):
        machine = _machine()
        trials = [
            {
                "trial_id": "shape_xi_only__final__seed1",
                "campaign_id": "shape_xi_only",
                "search_phase": "INITIAL",
                "variables_json": '["xi"]',
                "task": "shape",
                "stage": "FINAL_SEARCH",
                "status": "SUCCESS",
            },
            {
                "trial_id": "shape_xi_q_d__final__seed1",
                "campaign_id": "shape_xi_q_d",
                "search_phase": "CONDITIONAL",
                "variables_json": '["xi","q","d"]',
                "task": "shape",
                "stage": "FINAL_SEARCH",
                "status": "SUCCESS",
            },
        ]
        audits = [
            {
                "trial_id": "shape_xi_only__final__seed1",
                "variable_support": ["xi"],
            },
            {
                "trial_id": "shape_xi_q_d__final__seed1",
                "variable_support": ["xi", "q"],
            },
        ]
        rows = evaluation.summarize_variable_frequency(trials, audits, machine)
        final_rows = [row for row in rows if row["stage"] == "FINAL_SEARCH"]
        xi_only = [
            row for row in final_rows if row["campaign_id"] == "shape_xi_only"
        ]
        expanded = [
            row for row in final_rows if row["campaign_id"] == "shape_xi_q_d"
        ]
        self.assertEqual(["xi"], [row["variable"] for row in xi_only])
        self.assertEqual(["xi", "q", "d"], [row["variable"] for row in expanded])
        self.assertTrue(all(row["trial_denominator"] == 1 for row in final_rows))
        self.assertEqual(1.0, next(row for row in expanded if row["variable"] == "q")["trial_frequency"])

    def test_valid_forms_pass_numeric_and_strict_continuous_domain_gates(self):
        machine = _machine()
        peak = evaluation.audit_expression(
            "pow_positive_base(q, 0.8) / sqrt(d)", "peak", machine
        )
        peak_numeric = evaluation.numeric_physics_audit(
            peak["expression"], "peak", machine
        )
        self.assertEqual("PASS", peak_numeric["numeric_status"])
        self.assertEqual("DOMAIN_CERTIFIED", peak_numeric["domain_certification_status"])
        self.assertTrue(peak_numeric["freeze_eligible"])

        shape = evaluation.audit_expression("1/(1+xi)", "shape", machine)
        shape_numeric = evaluation.numeric_physics_audit(
            shape["expression"], "shape", machine
        )
        self.assertEqual("PASS", shape_numeric["numeric_status"])
        self.assertEqual("DOMAIN_CERTIFIED", shape_numeric["domain_certification_status"])
        self.assertNotIn("DOMAIN_NOT_CERTIFIED", shape_numeric["rejection_reasons"])
        interval_rows = [
            row for row in shape_numeric["constraints"]
            if row["certification_layer"] == "CONTINUOUS_DOMAIN_INTERVAL"
        ]
        self.assertTrue(interval_rows)
        self.assertTrue(all(row["status"] == "PASS" for row in interval_rows))
        self.assertTrue(all("method" in row["worst_witness"] for row in interval_rows))
        self.assertTrue(all(isinstance(row["boxes_evaluated"], int)
                            for row in interval_rows))

        invalid = evaluation.audit_expression("1 + xi", "shape", machine)
        invalid_numeric = evaluation.numeric_physics_audit(
            invalid["expression"], "shape", machine
        )
        self.assertEqual("FAIL", invalid_numeric["numeric_status"])
        self.assertEqual(
            "DOMAIN_CONSTRAINT_VIOLATION",
            invalid_numeric["domain_certification_status"],
        )
        self.assertFalse(invalid_numeric["freeze_eligible"])
        self.assertIn("THETA_ABOVE_ONE", invalid_numeric["rejection_reasons"])
        self.assertIn("THETA_INCREASES_WITH_XI", invalid_numeric["rejection_reasons"])

    def test_continuous_certifier_distinguishes_violation_and_uncertainty(self):
        machine = _machine()
        failing = evaluation.audit_expression("-q", "peak", machine)
        failed = evaluation.g5_sr_interval.certify_continuous_domain(
            failing["expression"], "peak", machine
        )
        self.assertEqual("DOMAIN_CONSTRAINT_VIOLATION",
                         failed["domain_certification_status"])
        self.assertIn(
            "F_T_NOT_STRICTLY_POSITIVE_CONTINUOUS_DOMAIN",
            failed["rejection_reasons"],
        )

        singular = evaluation.audit_expression("1/(q-1)", "peak", machine)
        singular_result = evaluation.g5_sr_interval.certify_continuous_domain(
            singular["expression"], "peak", machine
        )
        domain_row = next(
            row for row in singular_result["constraints"]
            if row["constraint"] == "REAL_FINITE_NO_SINGULARITY_ON_SUPPORT"
        )
        self.assertEqual("FAIL", domain_row["status"])
        self.assertEqual(
            "REAL_FINITE_OR_SINGULARITY_VIOLATION",
            domain_row["rejection_code"],
        )

        q_boundary = sp.Symbol("q", positive=True)
        zero_at_boundary = evaluation.g5_sr_interval.certify_continuous_domain(
            q_boundary - sp.Rational(33, 50), "peak", machine
        )
        strict_row = next(
            row for row in zero_at_boundary["constraints"]
            if row["constraint"] == "F_T_STRICTLY_POSITIVE"
        )
        self.assertEqual("FAIL", strict_row["status"])

        q_shape = sp.Symbol("q", positive=True)
        xi_shape = sp.Symbol("xi", nonnegative=True)
        nonidentity = evaluation.g5_sr_interval.certify_continuous_domain(
            q_shape / (1 + xi_shape), "shape", machine
        )
        identity_row = next(
            row for row in nonidentity["constraints"]
            if row["constraint"] == "THETA_AT_ZERO_EQUALS_ONE"
        )
        self.assertEqual("FAIL", identity_row["status"])

        # Expanded (q-d)^2 is nonnegative, but a one-box interval budget cannot
        # remove dependency.  The conservative result must be uncertainty,
        # never a sampled PASS.
        q = sp.Symbol("q", positive=True)
        d = sp.Symbol("d", positive=True)
        xi = sp.Symbol("xi", nonnegative=True)
        expanded_square = sp.Add(q**2, -2*q*d, d**2, evaluate=False)
        denominator = sp.Add(
            1, sp.Mul(xi, expanded_square, evaluate=False), evaluate=False
        )
        dependency_limited = sp.Pow(denominator, -1, evaluate=False)
        uncertain = evaluation.g5_sr_interval.certify_continuous_domain(
            dependency_limited, "shape", machine, max_boxes=1, max_depth=1
        )
        self.assertEqual("DOMAIN_NOT_CERTIFIED",
                         uncertain["domain_certification_status"])
        self.assertFalse(uncertain["freeze_eligible"])
        self.assertEqual(["DOMAIN_NOT_CERTIFIED"], uncertain["rejection_reasons"])
        self.assertTrue(any(row["status"] == "NOT_CERTIFIED"
                            for row in uncertain["constraints"]))

    def test_raw_operator_domains_survive_canonical_simplification(self):
        machine = _machine()
        cases = (
            ("exp(-xi)*(xi/xi)", "RAW_DIVISION_DENOMINATOR_ZERO_OR_UNCERTIFIED"),
            ("exp(-xi)+0*log(-xi)", "RAW_LOG_ARGUMENT_NOT_STRICTLY_POSITIVE"),
            ("exp(-xi)+0*sqrt(-xi)", "RAW_SQRT_ARGUMENT_NEGATIVE"),
            ("exp(-xi)+0*((-xi)**0.5)", "RAW_NONINTEGER_POWER_BASE_OUTSIDE_DOMAIN"),
            ("1/(1+pow_positive_base(xi,2))",
             "RAW_POW_POSITIVE_BASE_NOT_STRICTLY_POSITIVE"),
        )
        for raw, rejection in cases:
            with self.subTest(raw=raw):
                audit = evaluation.audit_expression(raw, "shape", machine)
                self.assertEqual("PASS", audit["structural_status"])
                numeric = evaluation.numeric_physics_audit(
                    audit["expression"], "shape", machine,
                    operator_domain_obligations=audit["operator_domain_obligations"],
                )
                self.assertFalse(numeric["freeze_eligible"])
                self.assertEqual("INELIGIBLE_FOR_SELECTION", numeric["eligibility_scope"])
                self.assertIn(rejection, numeric["rejection_reasons"])

        peak = evaluation.audit_expression(
            "pow_positive_base(-q,2)", "peak", machine
        )
        peak_numeric = evaluation.numeric_physics_audit(
            peak["expression"], "peak", machine,
            operator_domain_obligations=peak["operator_domain_obligations"],
        )
        self.assertFalse(peak_numeric["freeze_eligible"])
        self.assertIn(
            "RAW_POW_POSITIVE_BASE_NOT_STRICTLY_POSITIVE",
            peak_numeric["rejection_reasons"],
        )

        audited, _ = evaluation._audit_candidates(
            [{
                "trial_id": "shape-final-unsafe", "stage": "FINAL_SEARCH",
                "task": "shape", "outer_fold_index": "-1",
                "inner_fold_index": "-1", "seed": "1", "status": "SUCCESS",
            }],
            [{
                "trial_id": "shape-final-unsafe", "candidate_rank": "0",
                "raw_expression": "exp(-xi)*(xi/xi)",
            }],
            machine,
            "synthetic-protocol-hash",
        )
        self.assertFalse(audited[0]["freeze_eligible"])
        self.assertIn(
            "RAW_DIVISION_DENOMINATOR_ZERO_OR_UNCERTIFIED",
            audited[0]["rejection_reasons"],
        )

    def test_rational_rendering_of_sqrt_does_not_create_free_constants(self):
        machine = _machine()
        renderings = [
            evaluation.audit_expression(raw, "peak", machine)
            for raw in ("sqrt(q)", "q**0.5", "q**(1/2)")
        ]
        self.assertTrue(all(row["free_constant_count"] == 0 for row in renderings))
        self.assertEqual(1, len({row["structure_signature"] for row in renderings}))
        self.assertEqual(1, len({row["normalized_complexity"] for row in renderings}))

    def test_numeric_exponent_expression_is_one_fitted_constant(self):
        machine = _machine()
        renderings = [
            evaluation.audit_expression(raw, "peak", machine)
            for raw in ("q**(2/3)", "pow_positive_base(q,2/3)")
        ]
        self.assertTrue(all(row["structural_status"] == "PASS" for row in renderings))
        self.assertTrue(all(row["free_constant_count"] == 1 for row in renderings))
        self.assertTrue(all(row["parameter_template_expression"] == "q**p0"
                            for row in renderings))
        self.assertEqual(1, len({row["structure_signature"] for row in renderings}))
        self.assertTrue(all(
            row["parameter_initial_values"][0]["role"] == "fitted_exponent"
            for row in renderings
        ))

    def test_raw_ast_structure_rules_cannot_be_bypassed_by_simplification(self):
        machine = _machine()
        cases = (
            ("q+0*(q**4)", "POWER_EXPONENT_OUT_OF_RANGE"),
            ("q+0*pow_positive_base(q,4)", "POWER_EXPONENT_OUT_OF_RANGE"),
            ("q+0*exp(log(q))", "EXP_LOG_NESTING_FORBIDDEN"),
            ("exp(-xi)+0*log(xi)", "DIRECT_LOG_XI_FORBIDDEN"),
            ("exp(-xi)+0*(xi**-1)", "NEGATIVE_POWER_OF_XI_FORBIDDEN"),
            ("q+0*(q+q+q+q+q+q+q)", "RAW_COMPLEXITY_EXCEEDED"),
            ("square(square(square(square(square(q)))))",
             "RAW_MAX_DEPTH_EXCEEDED"),
        )
        for raw, expected in cases:
            task = "shape" if "xi" in raw else "peak"
            with self.subTest(raw=raw):
                audit = evaluation.audit_expression(raw, task, machine)
                self.assertEqual("FAIL", audit["structural_status"])
                self.assertIn(expected, audit["rejection_reasons"])
                self.assertNotEqual("", audit["raw_ast_complexity"])
                self.assertNotEqual("", audit["raw_ast_depth"])

        for raw in ("square(square(q))", "q*q*q*q"):
            with self.subTest(legal_raw_structure=raw):
                audit = evaluation.audit_expression(raw, "peak", machine)
                self.assertEqual("PASS", audit["structural_status"])

        restricted = json.loads(json.dumps(machine))
        restricted["operator_contract"]["allowed_unary"].remove("exp")
        forbidden = evaluation.audit_expression("q+0*exp(q)", "peak", restricted)
        self.assertIn("OPERATOR_NOT_ALLOWED:exp", forbidden["rejection_reasons"])

    def test_strict_interval_margin_retains_high_precision_and_scope_is_consistent(self):
        machine = _machine()
        audit = evaluation.audit_expression("q*exp(-1000*d)", "peak", machine)
        numeric = evaluation.numeric_physics_audit(
            audit["expression"], "peak", machine,
            operator_domain_obligations=audit["operator_domain_obligations"],
        )
        strict = next(
            row for row in numeric["constraints"]
            if row["certification_layer"] == "CONTINUOUS_DOMAIN_INTERVAL"
            and row["constraint"] == "F_T_STRICTLY_POSITIVE"
        )
        self.assertEqual("PASS", strict["status"])
        self.assertIsInstance(strict["margin"], str)
        self.assertNotEqual("0.0", strict["margin"])
        self.assertGreater(Decimal(strict["margin"]), Decimal(0))
        self.assertEqual("FAIL", numeric["numeric_status"])
        self.assertFalse(numeric["freeze_eligible"])
        self.assertEqual("INELIGIBLE_FOR_SELECTION", numeric["eligibility_scope"])

    def test_algebraic_reference_scale_and_prediction_families_are_separate(self):
        machine = _machine(32)
        expressions = ["q+q", "2*q", "3*q", "2.001*q", "d"]
        audits = []
        for index, raw in enumerate(expressions):
            row = evaluation.audit_expression(raw, "peak", machine)
            row.update({
                "candidate_instance_id": f"c{index}",
                "trial_id": f"t{index}",
                "task": "peak",
            })
            audits.append(row)
        evaluation.assign_equivalence_families(audits, machine)
        self.assertEqual(
            audits[0]["algebraic_family_id"], audits[1]["algebraic_family_id"]
        )
        self.assertNotEqual(
            audits[1]["algebraic_family_id"], audits[2]["algebraic_family_id"]
        )
        self.assertEqual(
            audits[1]["reference_scale_family_id"],
            audits[2]["reference_scale_family_id"],
        )
        self.assertEqual(
            audits[1]["prediction_family_id"],
            audits[3]["prediction_family_id"],
        )
        self.assertNotEqual(
            audits[1]["prediction_family_id"], audits[4]["prediction_family_id"]
        )

    def test_prediction_equivalence_uses_complete_link_not_transitive_closure(self):
        machine = _machine(32)
        audits = []
        for index, raw in enumerate(("q", "q+0.009", "q+0.018")):
            row = evaluation.audit_expression(raw, "peak", machine)
            row.update({
                "candidate_instance_id": f"chain-{index}",
                "trial_id": f"trial-{index}",
                "task": "peak",
            })
            audits.append(row)
        evaluation.assign_equivalence_families(audits, machine)
        self.assertEqual(
            audits[0]["prediction_family_id"], audits[1]["prediction_family_id"]
        )
        self.assertNotEqual(
            audits[0]["prediction_family_id"], audits[2]["prediction_family_id"]
        )
        self.assertNotEqual(
            audits[1]["prediction_family_id"], audits[2]["prediction_family_id"]
        )

    def test_numeric_constants_share_structure_and_emit_refit_template(self):
        machine = _machine(32)
        expressions = [
            "0.8*q + 1.1*d",
            "1.3*d + 0.6*q",
            "pow_positive_base(q, 0.8) / sqrt(d)",
            "pow_positive_base(q, 0.9) / sqrt(d)",
        ]
        audits = []
        for index, raw in enumerate(expressions):
            row = evaluation.audit_expression(raw, "peak", machine)
            row.update({
                "candidate_instance_id": f"structure-{index}",
                "trial_id": f"fold-{index}",
                "task": "peak",
            })
            audits.append(row)
        evaluation.assign_equivalence_families(audits, machine)

        self.assertEqual(
            "d*p0 + p1*q", audits[0]["parameter_template_expression"]
        )
        self.assertEqual(
            audits[0]["parameter_template_expression"],
            audits[1]["parameter_template_expression"],
        )
        self.assertEqual(
            audits[0]["structure_family_id"], audits[1]["structure_family_id"]
        )
        self.assertNotEqual(
            audits[0]["algebraic_family_id"], audits[1]["algebraic_family_id"]
        )
        self.assertEqual(
            audits[2]["structure_family_id"], audits[3]["structure_family_id"]
        )
        self.assertEqual(
            ["p0", "p1"],
            [row["name"] for row in audits[0]["parameter_initial_values"]],
        )

        derived_scale = evaluation.audit_expression("q+q", "peak", machine)
        fitted_scale = evaluation.audit_expression("2*q", "peak", machine)
        self.assertEqual(0, derived_scale["free_constant_count"])
        self.assertEqual(1, fitted_scale["free_constant_count"])
        self.assertEqual(
            derived_scale["structure_signature"],
            fitted_scale["structure_signature"],
        )

    def test_reference_scale_absorbs_only_positive_overall_scale(self):
        machine = _machine()
        positive = [evaluation.audit_expression(value, "peak", machine)
                    for value in ("2*q", "3*q")]
        negative = evaluation.audit_expression("-2*q", "peak", machine)
        self.assertEqual(
            positive[0]["reference_scale_signature"],
            positive[1]["reference_scale_signature"],
        )
        self.assertNotEqual(
            positive[0]["reference_scale_signature"],
            negative["reference_scale_signature"],
        )
        proportional_sums = [
            evaluation.audit_expression(value, "peak", machine)
            for value in ("0.5*q + 1.0*d", "1.5*q + 3.0*d")
        ]
        self.assertEqual(
            proportional_sums[0]["reference_scale_signature"],
            proportional_sums[1]["reference_scale_signature"],
        )

    def test_stability_is_not_inferred_from_raw_pareto_presence(self):
        machine = _machine()
        trials = []
        audits = []
        for outer in range(9):
            for seed in range(5):
                trial_id = f"o{outer}s{seed}"
                selected = outer < 7 and seed == 0
                trials.append({
                    "trial_id": trial_id,
                    "task": "peak",
                    "stage": "OUTER_REFIT_SEARCH",
                    "outer_fold_index": str(outer),
                    "seed": str(seed),
                    "status": "SUCCESS" if selected else "FAILED",
                })
                if selected:
                    audits.append({
                        "trial_id": trial_id, "task": "peak",
                        "prediction_family_id": "PRED_target",
                        "variable_support": ["q", "d"],
                    })
        for seed in range(20):
            trial_id = f"f{seed}"
            selected = seed < 16
            trials.append({
                "trial_id": trial_id,
                "task": "peak",
                "stage": "FINAL_SEARCH",
                "outer_fold_index": "-1",
                "seed": str(seed),
                "status": "SUCCESS" if selected else "TIMEOUT",
            })
            if selected:
                audits.append({
                    "trial_id": trial_id, "task": "peak",
                    "prediction_family_id": "PRED_target",
                    "variable_support": ["q", "d"],
                })
        rows = evaluation.summarize_stability(trials, audits, machine)
        target = next(row for row in rows if row["task"] == "peak")
        self.assertEqual("STABILITY_EVALUATION_STATE", target["record_type"])
        self.assertEqual("NOT_EVALUABLE", target["stability_evaluation_status"])
        self.assertIn("RAW_PARETO_PRESENCE_IS_DIAGNOSTIC_ONLY",
                      target["stability_evaluation_reason"])
        self.assertEqual(("", 9), (
            target["outer_fold_numerator"], target["outer_fold_denominator"]
        ))
        self.assertEqual(("", 45), (
            target["outer_trial_numerator"], target["outer_trial_denominator"]
        ))
        self.assertEqual(("", 20), (
            target["final_seed_numerator"], target["final_seed_denominator"]
        ))
        self.assertEqual(38, target["failed_outer_trials_in_denominator"])
        self.assertEqual(4, target["failed_final_trials_in_denominator"])
        self.assertFalse(target["outer_stability_pass"])
        self.assertFalse(target["final_seed_stability_pass"])

    def test_end_to_end_writes_four_audit_tables_without_freezing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            machine = _machine(32)
            (repo / "config").mkdir(parents=True)
            (repo / "config/symbolic_regression_v1.json").write_text(
                json.dumps(machine), encoding="utf-8"
            )
            machine_hash, supplement_hash = _approve_execution_supplement(
                repo, machine, peak_count=2, xi_count=1
            )
            trials = [
                {
                    "trial_id": "peak_q_d__final__seed1",
                    "campaign_id": "peak_q_d", "search_phase": "INITIAL",
                    "variables_json": "[\"q\",\"d\"]", "stage": "FINAL_SEARCH",
                    "task": "peak", "outer_fold_index": "-1",
                    "outer_holdout_parent_case_id": "",
                    "inner_fold_index": "-1", "seed": "1", "status": "SUCCESS",
                    "machine_protocol_sha256": machine_hash,
                    "execution_supplement_sha256": supplement_hash,
                    "shape_expansion_decision_sha256": "",
                },
                {
                    "trial_id": "shape_xi_only__final__seed1",
                    "campaign_id": "shape_xi_only", "search_phase": "INITIAL",
                    "variables_json": "[\"xi\"]", "stage": "FINAL_SEARCH",
                    "task": "shape", "outer_fold_index": "-1",
                    "outer_holdout_parent_case_id": "",
                    "inner_fold_index": "-1", "seed": "1", "status": "SUCCESS",
                    "machine_protocol_sha256": machine_hash,
                    "execution_supplement_sha256": supplement_hash,
                    "shape_expansion_decision_sha256": "",
                },
                {
                    "trial_id": "peak_q_d__final__seed2",
                    "campaign_id": "peak_q_d", "search_phase": "INITIAL",
                    "variables_json": "[\"q\",\"d\"]", "stage": "FINAL_SEARCH",
                    "task": "peak", "outer_fold_index": "-1",
                    "outer_holdout_parent_case_id": "",
                    "inner_fold_index": "-1", "seed": "2", "status": "TIMEOUT",
                    "machine_protocol_sha256": machine_hash,
                    "execution_supplement_sha256": supplement_hash,
                    "shape_expansion_decision_sha256": "",
                },
            ]
            candidates = [
                {
                    "trial_id": "peak_q_d__final__seed1", "candidate_rank": "0",
                    "campaign_id": "peak_q_d", "search_phase": "INITIAL",
                    "variables_json": "[\"q\",\"d\"]", "stage": "FINAL_SEARCH",
                    "task": "peak", "raw_expression": "pow_positive_base(q,0.8)",
                    "outer_fold_index": "-1", "outer_holdout_parent_case_id": "",
                    "inner_fold_index": "-1", "seed": "1",
                    "loss": "0.1", "score": "1.0",
                    "machine_protocol_sha256": machine_hash,
                    "execution_supplement_sha256": supplement_hash,
                    "shape_expansion_decision_sha256": "",
                },
                {
                    "trial_id": "shape_xi_only__final__seed1", "candidate_rank": "0",
                    "campaign_id": "shape_xi_only", "search_phase": "INITIAL",
                    "variables_json": "[\"xi\"]", "stage": "FINAL_SEARCH",
                    "task": "shape", "raw_expression": "1/(1+xi)",
                    "outer_fold_index": "-1", "outer_holdout_parent_case_id": "",
                    "inner_fold_index": "-1", "seed": "1",
                    "loss": "0.1", "score": "1.0",
                    "machine_protocol_sha256": machine_hash,
                    "execution_supplement_sha256": supplement_hash,
                    "shape_expansion_decision_sha256": "",
                },
            ]
            _write_csv(repo / machine["output_contract"]["search_trials"], trials)
            _write_csv(repo / machine["output_contract"]["pareto_front"], candidates)
            result = evaluation.evaluate(
                repo,
                preflight_fn=lambda _: {
                    "status": "READY_FOR_G5_SR_SEARCH",
                    "machine_protocol_sha256": machine_hash,
                    "execution_supplement": {
                        "approved_sha256": supplement_hash,
                    },
                },
            )
            self.assertEqual(
                "G5_SR_PHYSICS_AUDIT_COMPLETE_NO_FORMULA_SELECTED",
                result["status"],
            )
            self.assertEqual(2, result["numeric_physics_pass_count"])
            self.assertEqual(2, result["domain_certified_candidate_count"])
            self.assertEqual(2, result["freeze_eligible_candidate_count"])
            self.assertFalse(result["A_active_created"])
            for key in (
                "candidate_families", "variable_selection_frequency",
                "crossfold_stability", "physics_rejections",
            ):
                self.assertTrue(Path(result["outputs"][key]).is_file())
            with (repo / machine["output_contract"]["candidate_families"]).open(
                newline="", encoding="utf-8-sig"
            ) as stream:
                families = list(csv.DictReader(stream))
            self.assertEqual(2, len(families))
            self.assertTrue(all(row["domain_certification_status"] == "DOMAIN_CERTIFIED"
                                for row in families))
            self.assertTrue(all(row["freeze_eligible"] == "True" for row in families))
            self.assertTrue(all("NOT_SELECTED_OR_FROZEN" in row["eligibility_scope"]
                                for row in families))
            self.assertTrue(all(row["parameter_template_expression"] for row in families))
            self.assertTrue(all(row["structure_family_id"] for row in families))
            self.assertTrue(all(isinstance(
                json.loads(row["parameter_initial_values_json"]), list
            ) for row in families))
            with (repo / machine["output_contract"]["physics_rejections"]).open(
                newline="", encoding="utf-8-sig"
            ) as stream:
                physics = list(csv.DictReader(stream))
            interval_rows = [
                row for row in physics
                if row["certification_layer"] == "CONTINUOUS_DOMAIN_INTERVAL"
            ]
            self.assertTrue(interval_rows)
            self.assertTrue(all(row["certification_method"] for row in interval_rows))
            self.assertTrue(all(row["boxes_evaluated"] != "" for row in interval_rows))


if __name__ == "__main__":
    unittest.main()
