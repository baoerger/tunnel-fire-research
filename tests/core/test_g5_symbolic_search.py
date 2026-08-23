import csv
import json
import tempfile
import unittest
from collections import Counter, defaultdict
from pathlib import Path

from src.analysis import g5_sr_protocol as protocol_guard
from src.analysis import run_g5_symbolic_search as search


def _write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _ready(repo):
    machine_path = repo / protocol_guard.MACHINE_PROTOCOL
    supplement = protocol_guard.require_execution_supplement_approved(repo)
    return {
        "status": "READY_FOR_G5_SR_SEARCH",
        "machine_protocol_sha256": protocol_guard.sha256_file(machine_path),
        "execution_supplement": supplement,
        "environment": {
            "julia_version": "1.12.7",
            "symbolic_regression_version": "1.11.3",
        },
    }


def _backend_probe():
    return {
        "julia_version": "1.12.7",
        "symbolic_regression_version": "1.11.3",
    }


def _machine(parent_count=3, inner_count=2):
    return {
        "schema_version": "1.0",
        "protocol_id": "g5_symbolic_regression_v1",
        "status": "SR_PROTOCOL_APPROVED",
        "execution_state": "APPROVED_NOT_RUN",
        "approval": {"decision": "SR_PROTOCOL_APPROVED"},
        "data_contract": {
            "development_registry": "config/development_cases.csv",
            "required_sources": [
                {"relative_path": "derived/development/case_metrics.csv"},
                {"relative_path": "derived/development/curve_points.csv"},
            ],
            "effective_domain": {
                "xi_max": 5.0,
                "include_point_roles": ["ACTIVE", "CENSORED_LOW_SIGNAL"],
                "censored_role": "CENSORED_LOW_SIGNAL",
            },
        },
        "normalization": {
            "Q_ref_kW": 27000.0,
            "D_ref_m": 5.0,
            "H_eff_m": 5.0,
            "T0_K": 293.15,
        },
        "tasks": {
            "peak": {
                "target": "F_T", "variables": ["q", "d"],
                "max_complexity": 12, "max_depth": 4,
            },
            "shape": {
                "target": "Theta", "variables": ["xi", "q", "d"],
                "max_complexity": 16, "max_depth": 5,
            },
        },
        "search_budget": {
            "outer_folds": {"count": parent_count},
            "inner_folds": {"count": inner_count},
            "outer_search_seeds": [11],
            "final_search_seeds": [21],
            "per_seed": {
                "iterations": 1,
                "populations": 1,
                "population_size": 8,
                "candidate_evaluations_max": 10,
                "wall_time_s_max": 1,
            },
        },
        "final_refit": {"retain_peak_grid_uncertainty_fraction": 0.13},
        "output_contract": {
            "search_trials": "reports/g5_sr_search_trials.csv",
            "pareto_front": "reports/g5_sr_pareto_front.csv",
        },
    }


def _approve_execution_supplement(repo, machine):
    machine_path = repo / protocol_guard.MACHINE_PROTOCOL
    parent_hash = protocol_guard.sha256_file(machine_path)
    per_campaign = (
        int(machine["search_budget"]["outer_folds"]["count"])
        * (int(machine["search_budget"]["inner_folds"]["count"]) + 1)
        * len(machine["search_budget"]["outer_search_seeds"])
        + len(machine["search_budget"]["final_search_seeds"])
    )
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
            "sha256": parent_hash,
        },
        "shape_search_sequence": {
            "phase_1": {
                "campaign_id": "shape_xi_only",
                "variables": ["xi"],
                "required_first": True,
                "trial_count": per_campaign,
            },
            "expansion_trigger": {
                "logic": "OR",
                "conditions": [
                    {
                        "condition_id": "XI_ONLY_Q_OR_D_SYSTEMATIC_RESIDUAL_FAIL",
                    },
                    {
                        "condition_id": "XI_ONLY_ABSOLUTE_SHAPE_GATE_FAIL",
                    },
                ],
                "decision_artifact_required_before_phase_2": (
                    "reports/g5_sr_shape_expansion_decision.json"
                ),
            },
            "phase_2": {
                "campaign_id": "shape_xi_q_d",
                "variables": ["xi", "q", "d"],
                "conditional": True,
                "trial_count": per_campaign,
                "launch_requires_trigger_pass": True,
            },
        },
        "trial_budget": {
            "initial_campaigns": ["peak_q_d", "shape_xi_only"],
            "initial_trial_count": 2 * per_campaign,
            "conditional_campaign": "shape_xi_q_d",
            "conditional_additional_trial_count": per_campaign,
            "maximum_main_search_trial_count": 3 * per_campaign,
        },
    }
    supplement_path = repo / protocol_guard.EXECUTION_SUPPLEMENT
    supplement_path.write_text(json.dumps(supplement), encoding="utf-8")
    study = {
        "g5_symbolic_regression": {
            "status": "SR_PROTOCOL_APPROVED",
            "machine_protocol": protocol_guard.MACHINE_PROTOCOL.as_posix(),
            "machine_protocol_sha256": parent_hash,
            "execution_supplement": {
                "status": protocol_guard.APPROVED_EXECUTION_SUPPLEMENT_STATUS,
                "draft_path": protocol_guard.EXECUTION_SUPPLEMENT_DRAFT.as_posix(),
                "approved_path": protocol_guard.EXECUTION_SUPPLEMENT.as_posix(),
                "approved_sha256": protocol_guard.sha256_file(supplement_path),
            },
        }
    }
    (repo / protocol_guard.STUDY_PROTOCOL).write_text(
        json.dumps(study), encoding="utf-8"
    )
    return supplement


def _register_draft_execution_supplement(repo, machine):
    machine_path = repo / protocol_guard.MACHINE_PROTOCOL
    parent_hash = protocol_guard.sha256_file(machine_path)
    draft = {
        "schema_version": "1.0-draft",
        "supplement_id": "g5_sr_execution_supplement_v1",
        "status": "DRAFT_REQUIRES_USER_APPROVAL",
        "execution_state": "NOT_AUTHORIZED_FOR_FORMAL_SEARCH",
        "parent_protocol": {
            "relative_path": protocol_guard.MACHINE_PROTOCOL.as_posix(),
            "protocol_id": machine["protocol_id"],
            "required_status": "SR_PROTOCOL_APPROVED",
            "sha256": parent_hash,
        },
    }
    (repo / protocol_guard.EXECUTION_SUPPLEMENT_DRAFT).write_text(
        json.dumps(draft), encoding="utf-8"
    )
    study = {
        "g5_symbolic_regression": {
            "status": "SR_PROTOCOL_APPROVED",
            "machine_protocol": protocol_guard.MACHINE_PROTOCOL.as_posix(),
            "machine_protocol_sha256": parent_hash,
            "execution_supplement": {
                "status": "DRAFT_REQUIRES_USER_APPROVAL",
                "draft_path": protocol_guard.EXECUTION_SUPPLEMENT_DRAFT.as_posix(),
                "approved_path": protocol_guard.EXECUTION_SUPPLEMENT.as_posix(),
                "approved_sha256": None,
            },
        }
    }
    (repo / protocol_guard.STUDY_PROTOCOL).write_text(
        json.dumps(study), encoding="utf-8"
    )


def _data(parents=("p1", "p2", "p3")):
    metrics = []
    curves = []
    for parent_index, parent in enumerate(parents, start=1):
        run_count = parent_index
        for run_index in range(run_count):
            run = f"{parent}_r{run_index}"
            physical = f"{parent}_x{run_index}"
            peak = 90.0 + 5.0 * parent_index + run_index
            metrics.append({
                "parent_case_id": parent,
                "physical_case_id": physical,
                "run_chid": run,
                "Q_actual_kW": str(17000 + 5000 * parent_index),
                "Df_discrete_m": str(3.5 + 0.5 * parent_index),
                "deltaT_max_K": str(peak),
                "deltaT_peak_ci95_low_K": str(peak - 4.0),
                "deltaT_peak_ci95_high_K": str(peak + 4.0),
            })
            for xi, role in ((0.0, "ACTIVE"), (1.0, "CENSORED_LOW_SIGNAL")):
                curves.append({
                    "parent_case_id": parent,
                    "physical_case_id": physical,
                    "run_chid": run,
                    "xi": str(xi),
                    "Theta": str(1.0 / (1.0 + xi)),
                    "deltaT_ci95_low_K": str(max(0.0, peak / (1.0 + xi) - 2.0)),
                    "deltaT_ci95_high_K": str(peak / (1.0 + xi) + 2.0),
                    "detection_limit_K": "0.5",
                    "point_role": role,
                })
    return metrics, curves


def _write_shape_expansion_decision(repo, machine, supplement, mutate=None):
    machine_hash = protocol_guard.sha256_file(
        repo / protocol_guard.MACHINE_PROTOCOL
    )
    supplement_hash = protocol_guard.sha256_file(
        repo / protocol_guard.EXECUTION_SUPPLEMENT
    )
    decision = {
        "schema_version": "1.0",
        "stage": "G5_SR_SHAPE_EXPANSION_DECISION",
        "status": "SHAPE_EXPANSION_TRIGGERED",
        "decision": "RUN_SHAPE_XI_Q_D",
        "launch_authorized": True,
        "source_campaign": "shape_xi_only",
        "target_campaign": "shape_xi_q_d",
        "trigger_logic": "OR",
        "machine_protocol_sha256": machine_hash,
        "execution_supplement_sha256": supplement_hash,
        "outer_fold_selected_count": int(
            machine["search_budget"]["outer_folds"]["count"]
        ),
        "outer_holdout_used_for_selection": False,
        "lockbox_used": False,
        "trigger_tests": [
            {
                "condition_id": item["condition_id"],
                "evaluated": True,
                "triggered": index == 0,
            }
            for index, item in enumerate(
                supplement["shape_search_sequence"]["expansion_trigger"]["conditions"]
            )
        ],
    }
    if mutate:
        mutate(decision)
    path = repo / protocol_guard.SHAPE_EXPANSION_DECISION
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(decision), encoding="utf-8")
    return decision, path


class G5SymbolicSearchTests(unittest.TestCase):
    def test_nine_outer_four_inner_folds_never_leak_parent_groups(self):
        repo = Path(__file__).resolve().parents[2]
        machine = json.loads(
            (repo / "config/symbolic_regression_v1.json").read_text(encoding="utf-8")
        )
        parents = [f"p{i}" for i in range(9)]
        folds = search.build_fold_structure(parents, machine)
        self.assertEqual(9, len(folds))
        for outer in folds:
            holdout = outer["outer_holdout_parent_case_id"]
            seen = []
            self.assertEqual(4, len(outer["inner_folds"]))
            for inner in outer["inner_folds"]:
                train = set(inner["train_parent_case_ids"])
                validation = set(inner["validation_parent_case_ids"])
                self.assertFalse(train & validation)
                self.assertNotIn(holdout, train | validation)
                self.assertEqual(6, len(train))
                self.assertEqual(2, len(validation))
                seen.extend(validation)
            self.assertEqual(sorted(outer["outer_train_parent_case_ids"]), sorted(seen))

        plan = search.build_search_plan(parents, machine)
        # 2 tasks * (9 outer * (4 inner + 1 outer-refit) * 5 seeds + 20 final seeds)
        self.assertEqual(490, len(plan))
        self.assertEqual(len(plan), len({row["trial_id"] for row in plan}))
        self.assertEqual(
            {"peak_q_d": 245, "shape_xi_only": 245},
            dict(Counter(row["campaign_id"] for row in plan)),
        )
        self.assertTrue(all(
            row["variables"] == ["xi"]
            for row in plan if row["campaign_id"] == "shape_xi_only"
        ))
        conditional = search.build_search_plan(
            parents,
            machine,
            search_phase=search.CONDITIONAL_SEARCH_PHASE,
            shape_expansion_decision_sha256="a" * 64,
        )
        self.assertEqual(245, len(conditional))
        self.assertEqual(735, len(plan) + len(conditional))
        self.assertFalse(
            {row["trial_id"] for row in plan}
            & {row["trial_id"] for row in conditional}
        )

    def test_peak_aggregation_and_shape_weights_respect_parent_hierarchy(self):
        machine = _machine()
        metrics, curves = _data()
        parents = ["p1", "p2", "p3"]
        peak = search.build_peak_dataset(metrics, parents, machine)
        self.assertEqual(3, len(peak))
        self.assertEqual(parents, [row["parent_case_id"] for row in peak])
        self.assertAlmostEqual(1.0, sum(row["weight"] for row in peak) / 3.0)
        self.assertEqual([1, 2, 3], [row["nested_run_count"] for row in peak])

        shape = search.build_shape_dataset(metrics, curves, parents, machine)
        sums = defaultdict(float)
        censored = 0
        for row in shape:
            sums[row["parent_case_id"]] += row["weight"]
            censored += int(row["is_censored"])
        self.assertEqual(set(parents), set(sums))
        for total in sums.values():
            self.assertAlmostEqual(1.0, total)
        self.assertGreater(censored, 0)

    def test_execution_records_failures_and_raw_candidates_without_leakage(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            machine = _machine()
            (repo / "config").mkdir(parents=True)
            (repo / "config/symbolic_regression_v1.json").write_text(
                json.dumps(machine), encoding="utf-8"
            )
            _approve_execution_supplement(repo, machine)
            metrics, curves = _data()
            _write_csv(repo / "derived/development/case_metrics.csv", metrics)
            _write_csv(repo / "derived/development/curve_points.csv", curves)

            calls = []

            def runner(**kwargs):
                trial = kwargs["trial"]
                expected_variables = (
                    ["q", "d"]
                    if trial["campaign_id"] == "peak_q_d" else ["xi"]
                )
                self.assertEqual(expected_variables, kwargs["task_config"]["variables"])
                parent_ids = {row["parent_case_id"] for row in kwargs["dataset"]}
                self.assertEqual(set(trial["train_parent_case_ids"]), parent_ids)
                self.assertFalse(
                    parent_ids
                    & (set(trial["validation_parent_case_ids"])
                       | set(trial["test_parent_case_ids"]))
                )
                calls.append(trial["trial_id"])
                if trial["stage"] == "INNER_SEARCH" and trial["inner_fold_index"] == 1:
                    raise RuntimeError("synthetic failed seed")
                return {
                    "candidates": [{
                        "raw_expression": (
                            "q*d" if trial["campaign_id"] == "peak_q_d" else "xi"
                        ),
                        "complexity": 3,
                        "loss": 0.01,
                        "score": 1.0,
                    }],
                    "metadata": {"mock": True},
                }

            result = search.execute_search(
                repo,
                runner=runner,
                preflight_fn=_ready,
                backend_version_probe=_backend_probe,
            )
            self.assertEqual(20, result["planned_trials"])
            self.assertEqual(20, len(calls))
            self.assertEqual(6, result["trial_status_counts"]["FAILED"])
            self.assertEqual(14, result["trial_status_counts"]["SUCCESS"])
            self.assertEqual(14, result["raw_pareto_candidates"])
            self.assertEqual(_backend_probe(), result["verified_julia_backend"])
            self.assertFalse(result["lockbox_used"])
            self.assertFalse(result["formula_selected"])

            trials = list(csv.DictReader(
                (repo / "reports/g5_sr_search_trials.csv").open(
                    newline="", encoding="utf-8-sig"
                )
            ))
            candidates = list(csv.DictReader(
                (repo / "reports/g5_sr_pareto_front.csv").open(
                    newline="", encoding="utf-8-sig"
                )
            ))
            self.assertEqual(20, len(trials))
            self.assertEqual(set(search.TRIAL_COLUMNS), set(trials[0]))
            self.assertEqual(14, len(candidates))
            self.assertEqual(set(search.CANDIDATE_COLUMNS), set(candidates[0]))
            failed = [row for row in trials if row["status"] == "FAILED"]
            self.assertTrue(all(row["error_type"] == "RuntimeError" for row in failed))
            self.assertTrue(all(row["candidate_count"] == "0" for row in failed))

    def test_backend_version_drift_is_rejected_before_any_formal_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            machine = _machine()
            (repo / "config").mkdir(parents=True)
            (repo / "config/symbolic_regression_v1.json").write_text(
                json.dumps(machine), encoding="utf-8"
            )
            _approve_execution_supplement(repo, machine)
            metrics, curves = _data()
            _write_csv(repo / "derived/development/case_metrics.csv", metrics)
            _write_csv(repo / "derived/development/curve_points.csv", curves)
            runner_calls = []

            with self.assertRaisesRegex(
                protocol_guard.G5SRProtocolError, "Julia 后端环境漂移"
            ):
                search.execute_search(
                    repo,
                    runner=lambda **kwargs: runner_calls.append(kwargs),
                    preflight_fn=_ready,
                    backend_version_probe=lambda: {
                        "julia_version": "1.12.8",
                        "symbolic_regression_version": "1.11.3",
                    },
                )

            self.assertEqual([], runner_calls)
            self.assertFalse(
                (repo / machine["output_contract"]["search_trials"]).exists()
            )
            self.assertFalse(
                (repo / machine["output_contract"]["pareto_front"]).exists()
            )

    def test_resume_marks_interrupted_seed_and_never_reruns_terminal_trials(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            machine = _machine()
            (repo / "config").mkdir(parents=True)
            (repo / "config/symbolic_regression_v1.json").write_text(
                json.dumps(machine), encoding="utf-8"
            )
            _approve_execution_supplement(repo, machine)
            metrics, curves = _data()
            _write_csv(repo / "derived/development/case_metrics.csv", metrics)
            _write_csv(repo / "derived/development/curve_points.csv", curves)

            first_calls = []

            def interrupted_runner(**kwargs):
                trial_id = kwargs["trial"]["trial_id"]
                first_calls.append(trial_id)
                call_number = len(first_calls)
                if call_number == 2:
                    raise RuntimeError("formal failed seed")
                if call_number == 3:
                    raise TimeoutError("formal timed out seed")
                if call_number == 4:
                    return {"candidates": [], "metadata": {}}
                if call_number == 5:
                    raise KeyboardInterrupt("synthetic process interruption")
                return {
                    "candidates": [{"raw_expression": "q", "complexity": 1}],
                    "metadata": {"phase": "before-interruption"},
                }

            with self.assertRaises(KeyboardInterrupt):
                search.execute_search(
                    repo,
                    runner=interrupted_runner,
                    preflight_fn=_ready,
                    backend_version_probe=_backend_probe,
                )

            trial_path = repo / machine["output_contract"]["search_trials"]
            candidate_path = repo / machine["output_contract"]["pareto_front"]
            interrupted_rows = list(csv.DictReader(
                trial_path.open(newline="", encoding="utf-8-sig")
            ))
            fifth = interrupted_rows[4]
            self.assertEqual("PENDING", fifth["status"])
            self.assertTrue(fifth["started_at_utc"])
            self.assertTrue(fifth["n_train_rows"])
            candidates_before = list(csv.DictReader(
                candidate_path.open(newline="", encoding="utf-8-sig")
            ))

            with self.assertRaises(FileExistsError):
                search.execute_search(
                    repo,
                    runner=lambda **_: None,
                    preflight_fn=_ready,
                    backend_version_probe=_backend_probe,
                )

            resumed_calls = []

            def resumed_runner(**kwargs):
                resumed_calls.append(kwargs["trial"]["trial_id"])
                return {
                    "candidates": [{"raw_expression": "q*d", "complexity": 3}],
                    "metadata": {"phase": "resume"},
                }

            result = search.execute_search(
                repo,
                runner=resumed_runner,
                preflight_fn=_ready,
                resume=True,
                backend_version_probe=_backend_probe,
            )
            self.assertTrue(result["resumed"])
            self.assertEqual([first_calls[4]], result["interrupted_trials_marked"])
            self.assertEqual(15, len(resumed_calls))
            self.assertFalse(set(first_calls) & set(resumed_calls))
            self.assertEqual(16, result["trial_status_counts"]["SUCCESS"])
            self.assertEqual(1, result["trial_status_counts"]["FAILED"])
            self.assertEqual(1, result["trial_status_counts"]["TIMEOUT"])
            self.assertEqual(1, result["trial_status_counts"]["FAILED_NO_CANDIDATES"])
            self.assertEqual(1, result["trial_status_counts"]["FAILED_INTERRUPTED"])
            self.assertEqual(16, result["raw_pareto_candidates"])

            completed_rows = list(csv.DictReader(
                trial_path.open(newline="", encoding="utf-8-sig")
            ))
            self.assertEqual("FAILED_INTERRUPTED", completed_rows[4]["status"])
            candidates_after = list(csv.DictReader(
                candidate_path.open(newline="", encoding="utf-8-sig")
            ))
            self.assertEqual(candidates_before, candidates_after[:len(candidates_before)])

    def test_resume_rejects_incomplete_or_tampered_outputs_before_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            machine = _machine()
            (repo / "config").mkdir(parents=True)
            (repo / "config/symbolic_regression_v1.json").write_text(
                json.dumps(machine), encoding="utf-8"
            )
            _approve_execution_supplement(repo, machine)
            metrics, curves = _data()
            _write_csv(repo / "derived/development/case_metrics.csv", metrics)
            _write_csv(repo / "derived/development/curve_points.csv", curves)
            trial_path = repo / machine["output_contract"]["search_trials"]
            candidate_path = repo / machine["output_contract"]["pareto_front"]
            trial_path.parent.mkdir(parents=True, exist_ok=True)
            trial_path.write_text("trial_id\n", encoding="utf-8")
            with self.assertRaises(FileNotFoundError):
                search.execute_search(
                    repo,
                    preflight_fn=_ready,
                    resume=True,
                    backend_version_probe=_backend_probe,
                )

            trial_path.unlink()
            search.execute_search(
                repo,
                runner=lambda **_: {
                    "candidates": [{"raw_expression": "q", "complexity": 1}],
                    "metadata": {},
                },
                preflight_fn=_ready,
                backend_version_probe=_backend_probe,
            )
            rows = list(csv.DictReader(
                trial_path.open(newline="", encoding="utf-8-sig")
            ))
            rows[0]["seed"] = "999999"
            _write_csv(trial_path, rows)
            calls = []
            with self.assertRaisesRegex(search.G5SRResumeError, "trial 身份失配"):
                search.execute_search(
                    repo,
                    runner=lambda **kwargs: calls.append(kwargs["trial"]["trial_id"]),
                    preflight_fn=_ready,
                    resume=True,
                    backend_version_probe=_backend_probe,
                )
            self.assertEqual([], calls)

    def test_execute_rejects_draft_supplement_before_preflight_or_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            machine = _machine()
            (repo / "config").mkdir(parents=True)
            (repo / "config/symbolic_regression_v1.json").write_text(
                json.dumps(machine), encoding="utf-8"
            )
            _register_draft_execution_supplement(repo, machine)
            calls = {"preflight": 0, "runner": 0, "backend": 0}

            def preflight(_):
                calls["preflight"] += 1
                return _ready(repo)

            def runner(**_):
                calls["runner"] += 1
                return {"candidates": []}

            def backend():
                calls["backend"] += 1
                return _backend_probe()

            with self.assertRaisesRegex(
                protocol_guard.G5SRExecutionSupplementNotApprovedError,
                protocol_guard.G5_SR_EXECUTION_SUPPLEMENT_NOT_APPROVED,
            ):
                search.execute_search(
                    repo,
                    runner=runner,
                    preflight_fn=preflight,
                    backend_version_probe=backend,
                )

            self.assertEqual(
                {"preflight": 0, "runner": 0, "backend": 0}, calls
            )
            self.assertFalse(
                (repo / machine["output_contract"]["search_trials"]).exists()
            )
            self.assertFalse(
                (repo / machine["output_contract"]["pareto_front"]).exists()
            )

    def test_conditional_campaign_fail_closed_states_make_zero_runner_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            machine = _machine(parent_count=9, inner_count=4)
            (repo / "config").mkdir(parents=True)
            (repo / protocol_guard.MACHINE_PROTOCOL).write_text(
                json.dumps(machine), encoding="utf-8"
            )
            supplement = _approve_execution_supplement(repo, machine)
            parents = tuple(f"p{index}" for index in range(1, 10))
            metrics, curves = _data(parents)
            _write_csv(repo / "derived/development/case_metrics.csv", metrics)
            _write_csv(repo / "derived/development/curve_points.csv", curves)
            runner_calls = []
            backend_calls = []

            cases = (
                ("missing", None),
                (
                    "not-triggered",
                    lambda row: row.update(
                        status="SHAPE_EXPANSION_NOT_TRIGGERED",
                        decision="DO_NOT_RUN_SHAPE_XI_Q_D",
                        launch_authorized=False,
                    ),
                ),
                (
                    "not-evaluable",
                    lambda row: row.update(
                        status="SHAPE_EXPANSION_DECISION_NOT_EVALUABLE",
                        decision="STOP",
                        launch_authorized=False,
                    ),
                ),
                (
                    "hash-mismatch",
                    lambda row: row.update(machine_protocol_sha256="0" * 64),
                ),
            )
            for label, mutation in cases:
                with self.subTest(label=label):
                    decision_path = repo / protocol_guard.SHAPE_EXPANSION_DECISION
                    decision_path.unlink(missing_ok=True)
                    if mutation is not None:
                        _write_shape_expansion_decision(
                            repo, machine, supplement, mutation
                        )
                    with self.assertRaises(
                        protocol_guard.G5SRShapeExpansionNotAuthorizedError
                    ):
                        search.execute_search(
                            repo,
                            phase="shape-expansion",
                            runner=lambda **kwargs: runner_calls.append(kwargs),
                            preflight_fn=_ready,
                            backend_version_probe=lambda: (
                                backend_calls.append(True) or _backend_probe()
                            ),
                        )
            self.assertEqual([], runner_calls)
            self.assertEqual([], backend_calls)

    def test_triggered_conditional_campaign_appends_only_its_trials(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            machine = _machine(parent_count=9, inner_count=4)
            (repo / "config").mkdir(parents=True)
            (repo / protocol_guard.MACHINE_PROTOCOL).write_text(
                json.dumps(machine), encoding="utf-8"
            )
            supplement = _approve_execution_supplement(repo, machine)
            parents = tuple(f"p{index}" for index in range(1, 10))
            metrics, curves = _data(parents)
            _write_csv(repo / "derived/development/case_metrics.csv", metrics)
            _write_csv(repo / "derived/development/curve_points.csv", curves)

            def initial_runner(**kwargs):
                expression = (
                    "q" if kwargs["trial"]["campaign_id"] == "peak_q_d"
                    else "1/(1+xi)"
                )
                return {"candidates": [{"raw_expression": expression}]}

            initial = search.execute_search(
                repo,
                runner=initial_runner,
                preflight_fn=_ready,
                backend_version_probe=_backend_probe,
            )
            self.assertEqual(92, initial["planned_trials"])
            _, decision_path = _write_shape_expansion_decision(
                repo, machine, supplement
            )
            decision_hash = protocol_guard.sha256_file(decision_path)
            calls = []

            def conditional_runner(**kwargs):
                calls.append(kwargs["trial"]["trial_id"])
                self.assertEqual("shape_xi_q_d", kwargs["trial"]["campaign_id"])
                self.assertEqual(["xi", "q", "d"], kwargs["task_config"]["variables"])
                return {"candidates": [{"raw_expression": "1/(1+xi*q*d)"}]}

            expanded = search.execute_search(
                repo,
                phase="shape-expansion",
                runner=conditional_runner,
                preflight_fn=_ready,
                backend_version_probe=_backend_probe,
            )
            self.assertEqual(46, len(calls))
            self.assertEqual(138, expanded["planned_trials"])
            self.assertEqual(46, expanded["phase_trial_count"])
            self.assertEqual(decision_hash, expanded["shape_expansion_decision_sha256"])
            trial_rows = list(csv.DictReader(
                (repo / machine["output_contract"]["search_trials"]).open(
                    newline="", encoding="utf-8-sig"
                )
            ))
            conditional_rows = [
                row for row in trial_rows
                if row["campaign_id"] == "shape_xi_q_d"
            ]
            self.assertEqual(46, len(conditional_rows))
            self.assertTrue(all(
                row["shape_expansion_decision_sha256"] == decision_hash
                for row in conditional_rows
            ))

    def test_dry_run_reports_supplement_and_data_blockers_without_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            machine = _machine()
            (repo / "config").mkdir(parents=True)
            (repo / "config/symbolic_regression_v1.json").write_text(
                json.dumps(machine), encoding="utf-8"
            )
            _write_csv(
                repo / "config/development_cases.csv",
                [{"subset": "development", "parent_case_id": parent}
                 for parent in ("p1", "p2", "p3")],
            )
            calls = {"full": 0, "static": 0}

            def blocked(_):
                calls["full"] += 1
                raise protocol_guard.G5SRExecutionSupplementNotApprovedError(
                    protocol_guard.G5_SR_EXECUTION_SUPPLEMENT_NOT_APPROVED
                )

            def static(_):
                calls["static"] += 1
                return {
                    "status": "STATIC_G5_SR_PROTOCOL_PASS",
                    "machine_protocol_sha256": "abc123",
                    "blocker_codes": [
                        protocol_guard.G5_SR_EXECUTION_SUPPLEMENT_NOT_APPROVED,
                        protocol_guard.MISSING_G4_DERIVED_INPUTS,
                    ],
                }

            result = search.dry_run(repo, preflight_fn=blocked, static_validator=static)
            self.assertEqual({"full": 1, "static": 1}, calls)
            self.assertFalse(result["data_ready"])
            self.assertFalse(result["execution_supplement_approved"])
            self.assertFalse(result["formal_search_authorized"])
            self.assertEqual(
                [
                    protocol_guard.G5_SR_EXECUTION_SUPPLEMENT_NOT_APPROVED,
                    protocol_guard.MISSING_G4_DERIVED_INPUTS,
                ],
                result["blocker_codes"],
            )
            self.assertEqual(20, result["planned_trial_count"])
            self.assertFalse(result["lockbox_used"])


if __name__ == "__main__":
    unittest.main()
